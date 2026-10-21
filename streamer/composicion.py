"""Media Syntaxis Streamer - composición (fase 2).

Una composición toma la entrada de un flujo, reencuadra el vídeo dentro de un lienzo 1920x1080
y le superpone hasta dos capas HTML5 (Chromium sin pantalla, con canal alfa). Recodifica con
NVENC y publica el resultado en MediaMTX, en el path "comp_<flujo>", del que leen las salidas
con fuente "compuesta". La señal limpia sigue disponible a la vez para las demás salidas.

La superposición va por CPU: el ffmpeg del Z8 no trae overlay_cuda/scale_cuda.

Capas como "última imagen" (0.4.0, el contrato del diseño): un proceso aparte por composición
(compositor/capas.mjs, tarea "capas-<flujo>" del supervisor) deja la última imagen de cada capa en
<capas_dir>/<flujo>/capaN.yuv (yuva420p 1920x1080, alfa recto, escrita entera y renombrada). El
compositor relee ese archivo en cada fotograma (-f image2 -loop 1): el navegador nunca marca el ritmo
del vídeo. Mostrar, ocultar, recargar o cambiar la dirección de una capa no relanza el compositor:
lo aplica capas.mjs leyendo capas.json. Relanzan el compositor el encuadre, el fondo, la calidad,
añadir o quitar una capa y pasarla de encima a debajo (eso irá en vivo con zmq, paso B).
"""

import json
import os
import pathlib
import re

import mtx

LIENZO_W, LIENZO_H = 1920, 1080
TAM_CAPA = LIENZO_W * LIENZO_H * 5 // 2      # un fotograma yuva420p
MAX_CAPAS = 2
FONDO_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# preajustes de encuadre que ofrece el panel (x, y, ancho); el alto sale del ancho (16:9)
PREAJUSTES = {
    "completa": (0, 0, 1920),
    "l_derecha": (422, 0, 1498),       # L-bar: 78 % arriba a la derecha (el del PoC)
    "l_izquierda": (0, 0, 1498),
    "centrada": (192, 108, 1536),      # 80 % centrada
}


def maximo(cfg):
    """Tope de composiciones a la vez (config.json → composicion_max; 3 por defecto, como el diseño)."""
    try:
        return max(0, int(cfg.get("composicion_max", 3)))
    except (TypeError, ValueError):
        return 3


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


def id_capas(flujo_id):
    """Clave del proceso de las capas (navegador) en estado_salidas."""
    return "capas-" + flujo_id


def capas_de(comp):
    """Capas con dirección, visibles u ocultas: cada una es una entrada del compositor (ocultarla
    solo cambia su imagen a transparente, no el comando)."""
    try:
        capas = json.loads(comp["capas"] or "[]")
    except ValueError:
        return []
    return [c for c in capas if isinstance(c, dict) and c.get("url")][:MAX_CAPAS]


def carpeta_capas(cfg, flujo_id):
    """Carpeta de las imágenes de las capas de un flujo. Va en memoria (tmpfs): /run/msxs, que crea
    systemd-tmpfiles (scripts/desplegar.sh); si no existe o no se puede escribir, /dev/shm/msxs."""
    for base in (cfg.get("capas_dir") or "/run/msxs", "/dev/shm/msxs"):
        d = pathlib.Path(base) / flujo_id
        try:
            d.mkdir(parents=True, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            continue
    raise OSError("no hay carpeta en memoria para las capas (/run/msxs ni /dev/shm/msxs)")


def transparente():
    y = LIENZO_W * LIENZO_H
    return bytes([16]) * y + bytes([128]) * (y // 2) + bytes(y)


def escribir(archivo, datos):
    tmp = archivo.with_name(archivo.name + ".tmp")
    tmp.write_bytes(datos)
    os.replace(tmp, archivo)


def preparar_capas(comp, carpeta):
    """Antes de lanzar el compositor: cada capa tiene ya su archivo (transparente si es nuevo o está
    mal), para que el compositor nunca arranque sin él. Nunca se borra mientras corre."""
    for i in range(1, len(capas_de(comp)) + 1):
        f = carpeta / f"capa{i}.yuv"
        try:
            if f.stat().st_size == TAM_CAPA:
                continue
        except OSError:
            pass
        escribir(f, transparente())


def estado_capas(comp):
    """Lo que lee capas.mjs (capas.json): dirección, visible y contador de recarga de cada capa."""
    return json.dumps({"capas": [{"url": c["url"], "visible": bool(c.get("activa", True)),
                                  "recarga": int(c.get("recarga") or 0)} for c in capas_de(comp)]},
                      sort_keys=True)


def comando_capas(carpeta, run, lanzar):
    """Proceso de las capas de un flujo. No depende de las direcciones: no se relanza al editarlas."""
    return [str(lanzar), str(carpeta), str(carpeta / "capas.json"),
            str(run / f"{id_capas(carpeta.name)}.prog")]


def leer(c, flujo_id):
    r = c.execute("SELECT * FROM composiciones WHERE flujo=?", (flujo_id,)).fetchone()
    if r:
        return dict(r)
    return {"flujo": flujo_id, "activa": 0, "x": 0, "y": 0, "ancho": 1920, "fondo": "#000000",
            "capas": "[]", "kbps": 8000}


def filtro(comp):
    """filter_complex: vídeo reencuadrado sobre el fondo, capas "debajo" entre el fondo y el
    vídeo, capas "encima" sobre todo. Las capas son las entradas 1, 2… (la 0 es la señal).

    shortest=1 en todas las capas (OBLIGATORIO, lo comprueba tests/test_composicion.py): las capas
    son archivos que se releen sin fin, así que sin él, si se corta la señal, la salida se desboca a
    más de 3 veces el tiempo real. Con él, ffmpeg termina y el supervisor relanza la composición
    cuando vuelve la entrada. Si falta el archivo de una capa, también termina y se relanza."""
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
        partes.append(f"[b{n}][{i}:v]overlay=0:0:format=yuv420:shortest=1[b{n + 1}]")
        n += 1
    if debajo:
        partes.append(f"[b{n}][vb]overlay={x}:{y}:shortest=1[b{n + 1}]")
        n += 1
    for i in encima:
        partes.append(f"[b{n}][{i}:v]overlay=0:0:format=yuv420:shortest=1[b{n + 1}]")
        n += 1
    partes.append(f"[b{n}]format=yuv420p[out]")
    return ";".join(partes)


def comando(comp, path_entrada, ffmpeg, run, componer, carpeta):
    """Comando completo: componer.sh baja la prioridad y se convierte en este ffmpeg. Las capas son
    la última imagen de cada una (carpeta/capaN.yuv), releída en cada fotograma; la cola corta
    (thread_queue_size 4) hace que el gráfico no llegue con retraso respecto a la página."""
    fid = comp["flujo"]
    prog = run / f"{id_tarea(fid)}.prog"
    capas = capas_de(comp)
    previo, entradas_capas = [str(componer), "--"], []
    for i in range(1, len(capas) + 1):
        entradas_capas += ["-thread_queue_size", "4", "-f", "image2", "-loop", "1",
                           "-framerate", "30000/1001", "-c:v", "rawvideo", "-pixel_format", "yuva420p",
                           "-video_size", f"{LIENZO_W}x{LIENZO_H}", "-i", str(carpeta / f"capa{i}.yuv")]
    kbps = max(1000, min(40000, int(comp["kbps"])))
    ff = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
          "-progress", str(prog), "-stats_period", "2",
          "-thread_queue_size", "512", "-i", mtx.url_lectura(path_entrada) + "&latency=200000",
          *entradas_capas,
          "-filter_complex", filtro(comp), "-map", "[out]", "-map", "0:a?",
          "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr",
          "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k", "-g", "60", "-bf", "0",
          "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
          "-c:a", "copy",
          "-f", "mpegts", mtx.url_publicacion(path_comp(fid)) + "&pkt_size=1316"]
    return previo + ff


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
        with open(prog, "rb") as f:          # solo el final: el archivo crece mientras corre
            f.seek(max(0, os.fstat(f.fileno()).st_size - 600))
            txt = f.read().decode(errors="replace")
    except OSError:
        return None
    for linea in reversed(txt.splitlines()):
        if linea.startswith("fps="):
            try:
                return float(linea.split("=", 1)[1])
            except ValueError:
                return None
    return None

