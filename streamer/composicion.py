"""Media Syntaxis Streamer - composición (fase 2).

Una composición toma la entrada de un flujo, reencuadra el vídeo dentro de un lienzo 1920x1080
y le superpone hasta dos capas HTML5 (Chromium sin pantalla, con canal alfa). Recodifica con
NVENC y publica el resultado en MediaMTX, en el path "comp_<flujo>", del que leen las salidas
con fuente "compuesta". La señal limpia sigue disponible a la vez para las demás salidas.

La superposición va por CPU: el ffmpeg del Z8 no trae overlay_cuda/scale_cuda. Es el mismo
esquema que el PoC (19 h estable, ~4,4 núcleos, 29,95 fps).
"""

import json
import re

import mtx

LIENZO_W, LIENZO_H = 1920, 1080
MAX_CAPAS = 2
FONDO_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# preajustes de encuadre que ofrece el panel (x, y, ancho); el alto sale del ancho (16:9)
PREAJUSTES = {
    "completa": (0, 0, 1920),
    "l_derecha": (422, 0, 1498),       # L-bar: 78 % arriba a la derecha (el del PoC)
    "l_izquierda": (0, 0, 1498),
    "centrada": (192, 108, 1536),      # 80 % centrada
}


def path_comp(flujo_id):
    return "comp_" + flujo_id


def id_tarea(flujo_id):
    """Clave de la composición en estado_salidas (comparte la vigilancia con las salidas)."""
    return "comp-" + flujo_id


def normalizar(d):
    """Ajusta el encuadre para que el vídeo quepa entero en el lienzo, con medidas pares (NVENC
    y yuv420p lo exigen). Devuelve (x, y, ancho, alto)."""
    ancho = max(320, min(LIENZO_W, int(d["ancho"]))) // 2 * 2
    alto = round(ancho * 9 / 16) // 2 * 2
    x = max(0, min(LIENZO_W - ancho, int(d["x"]))) // 2 * 2
    y = max(0, min(LIENZO_H - alto, int(d["y"]))) // 2 * 2
    return x, y, ancho, alto


def capas_de(comp):
    try:
        capas = json.loads(comp["capas"] or "[]")
    except ValueError:
        return []
    return [c for c in capas if c.get("activa") and c.get("url")][:MAX_CAPAS]


def leer(c, flujo_id):
    r = c.execute("SELECT * FROM composiciones WHERE flujo=?", (flujo_id,)).fetchone()
    if r:
        return dict(r)
    return {"flujo": flujo_id, "activa": 0, "x": 0, "y": 0, "ancho": 1920, "fondo": "#000000",
            "capas": "[]", "kbps": 8000}


def filtro(comp):
    """filter_complex: vídeo reencuadrado sobre el fondo, capas "debajo" entre el fondo y el
    vídeo, capas "encima" sobre todo. Las capas son las entradas 1, 2… (la 0 es la señal)."""
    x, y, w, h = normalizar(comp)
    capas = capas_de(comp)
    debajo = [i + 1 for i, c in enumerate(capas) if not c.get("encima", True)]
    encima = [i + 1 for i, c in enumerate(capas) if c.get("encima", True)]
    fondo = "0x" + comp["fondo"].lstrip("#")
    partes = []
    if debajo:
        # el vídeo se usa dos veces: para el lienzo (misma base de tiempos) y encima de las capas
        partes.append(f"[0:v]setsar=1,scale={w}:{h},split=2[va][vb]")
        partes.append(f"[va]pad={LIENZO_W}:{LIENZO_H}:{x}:{y}:color={fondo}[b0]")
    else:
        partes.append(f"[0:v]setsar=1,scale={w}:{h},pad={LIENZO_W}:{LIENZO_H}:{x}:{y}:color={fondo}[b0]")
    n = 0
    for i in debajo:
        partes.append(f"[b{n}][{i}:v]overlay=0:0:eof_action=repeat[b{n + 1}]")
        n += 1
    if debajo:
        partes.append(f"[b{n}][vb]overlay={x}:{y}[b{n + 1}]")
        n += 1
    for i in encima:
        partes.append(f"[b{n}][{i}:v]overlay=0:0:eof_action=repeat[b{n + 1}]")
        n += 1
    partes.append(f"[b{n}]format=yuv420p[out]")
    return ";".join(partes)


def comando(comp, path_entrada, ffmpeg, run, componer):
    """Comando completo: componer.sh arranca las capturas y se convierte en este ffmpeg."""
    fid = comp["flujo"]
    prog = run / f"{id_tarea(fid)}.prog"
    capas = capas_de(comp)
    previo, entradas_capas = [str(componer)], []
    for i, c in enumerate(capas, start=1):
        fifo = run / f"{id_tarea(fid)}-capa{i}.fifo"
        previo += [str(fifo), c["url"]]
        entradas_capas += ["-thread_queue_size", "4", "-f", "image2pipe", "-c:v", "png",
                           "-framerate", "30000/1001", "-i", str(fifo)]
    kbps = max(1000, min(40000, int(comp["kbps"])))
    ff = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
          "-progress", str(prog), "-stats_period", "2",
          "-thread_queue_size", "512", "-i", mtx.url_lectura(path_entrada) + "&latency=200000",
          *entradas_capas,
          "-filter_complex", filtro(comp), "-map", "[out]", "-map", "0:a?",
          "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr",
          "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k", "-g", "60", "-bf", "0",
          "-c:a", "copy",
          "-f", "mpegts", mtx.url_publicacion(path_comp(fid)) + "&pkt_size=1316"]
    return previo + ["--"] + ff


def validar_url_capa(url):
    return bool(re.match(r"^https?://\S+$", url or ""))


def validar_fondo(fondo):
    return bool(FONDO_RE.match(fondo or ""))


def activas(c):
    """Composiciones activas de flujos activos: {flujo_id: fila}."""
    return {r["flujo"]: dict(r) for r in c.execute(
        """SELECT k.* FROM composiciones k JOIN flujos f ON f.id = k.flujo
           WHERE k.activa = 1 AND f.activo = 1""")}


def fps_de(prog):
    try:
        txt = prog.read_text()[-600:]
    except OSError:
        return None
    for linea in reversed(txt.splitlines()):
        if linea.startswith("fps="):
            try:
                return float(linea.split("=", 1)[1])
            except ValueError:
                return None
    return None

