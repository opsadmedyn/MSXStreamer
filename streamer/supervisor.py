#!/usr/bin/env python3
"""Media Syntaxis Streamer - supervisor.

Cada 2 s compara lo que pide la base de datos con lo que está corriendo y lo corrige:
  - declara en MediaMTX los paths de los flujos activos (y borra los que sobran);
  - mantiene un ffmpeg por cada salida SRT/RTMP activa, en copia, y lo relanza si cae,
    con espera creciente (2, 4, 8… hasta 30 s);
  - las salidas HLS no necesitan proceso: las sirve MediaMTX;
  - fase 2: mantiene un proceso de composición por flujo con la composición activa (capturas de
    Chromium + ffmpeg con NVENC) que publica en "comp_<flujo>"; las salidas con fuente
    "compuesta" leen de ahí. Se vigila igual que una salida.

Mientras el Recorder del equipo está en reposo («Apagar servicio», su reposo.json existe) no corre
nada: se paran las salidas y las composiciones y se quitan los paths de MediaMTX, sin tocar qué
flujos y salidas están activos. Al encender el Recorder vuelve todo lo activo.

Hub de señales (matriz.py): además mantiene lo que piden las rutas guardadas, con las mismas reglas:
  - los paths de las fuentes, de las señales derivadas y de los destinos que el hub entrega, y los
    usuarios de MediaMTX con los que publica cada origen y lee cada receptor;
  - un ffmpeg por fuente que el hub va a buscar, uno por conversión (compartida entre las rutas que
    piden lo mismo) y uno por destino al que el hub se conecta, cada uno con la política de
    reconexión de su destino. Si un destino falla, solo se relanza el suyo;
  - mira qué trae cada fuente al llegar su señal (inspeccion.py), que es lo que decide qué se copia.

Los ffmpeg corren en su propia sesión: si el supervisor se reinicia (por ejemplo al desplegar),
las salidas siguen al aire y el supervisor las vuelve a adoptar por su PID. Tras reiniciar el equipo
no queda ninguno y se levanta uno por cada cosa habilitada: nunca dos para lo mismo (antes de lanzar
se comprueba que no corra ya, y solo puede haber un supervisor a la vez).

Ningún ffmpeg se lanza a pelo: va dentro del lanzador (correr.py), que guarda en el registro lo que
escribe sin las claves.
"""

import concurrent.futures
import fcntl
import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.parse

import composicion
import db
import inspeccion
import matriz
import mtx
import secretos
from comandos import entrada_directa, url_salida      # noqa: F401  (url_salida: la usan otros módulos)

FFMPEG = db.config().get("ffmpeg", "ffmpeg")
RUN = db.BASE / "run"
LOGS = db.BASE / "logs"
LOG_MAX = 5 * 1024 * 1024
PROG_MAX = 1024 * 1024                      # espacio en disco de un .prog antes de recortarlo
VUELTA = 2.0
ESPERA_MAX = 30.0
COMPONER = pathlib.Path(__file__).parent / "componer.sh"
CORRER = pathlib.Path(__file__).parent / "correr.py"
COMP_MAX = composicion.maximo(db.config())
VIDEO_PARADO = 15.0                         # composición viva con el vídeo quieto tanto tiempo: se relanza
CAMBIO_ENTRADA = 20.0                       # entrada de hub sin señal tanto tiempo: pasa a la dirección siguiente
ESTABLE = 60.0                              # enviando tanto tiempo seguido: los intentos vuelven a cero
MISMO_AVISO = 600.0                         # la misma caída no se anota en los eventos más de una vez cada tanto
REINSPECCION = 30.0                         # inspección incompleta: se repite cada tanto


path_de_flujo = mtx.path_de_flujo


def comando(s, path, directa=None):
    """ffmpeg de un destino: lee el path de MediaMTX o, en un flujo 1080i directo, la entrada misma."""
    prog = RUN / f"{s['id']}.prog"
    if directa:
        opciones, url = entrada_directa(directa)
        entrada = [*opciones, "-i", url]
    else:
        entrada = ["-i", mtx.url_lectura(path)]
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning",
           "-progress", str(prog), "-stats_period", "2", *entrada]
    if s["tipo"] == "rtmp":                 # FLV admite un vídeo y un audio
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "flv"]
    else:
        cmd += ["-map", "0", "-c", "copy", "-f", "mpegts"]
    url, opciones = url_salida(s)
    return cmd + opciones + [url]


def firma(cmd):
    return hashlib.sha1("\0".join(cmd).encode()).hexdigest()[:16]


def vivo(pid, prog):
    """El PID sigue siendo nuestro ffmpeg (y no otro proceso que heredó el número)."""
    if not pid:
        return False
    try:
        linea = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        estado = pathlib.Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except OSError:
        return False
    return estado != "Z" and str(prog).encode() in linea


def _terminado(pid):
    """El proceso ya no existe. Si es hijo nuestro se recoge aquí mismo: mientras nadie lo recoge
    sigue figurando (zombi) y habría que esperar el plazo entero."""
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return True
    except ChildProcessError:               # adoptado tras un reinicio del supervisor: no es hijo
        pass
    except OSError:
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    try:                                    # hijo de otro (adoptado) que ya terminó y espera a que lo recojan
        return pathlib.Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] == "Z"
    except OSError:
        return True


def matar(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:                                # no encabeza un grupo (un proceso suelto): solo a él
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    for _ in range(40):
        time.sleep(0.05)
        if _terminado(pid):
            return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def buscar(prog):
    """PID del proceso que ya corre con ese .prog, o None. Es la última comprobación antes de
    lanzar: si por lo que sea hay uno vivo que la base no conoce (el supervisor murió justo después
    de lanzarlo, o no pudo guardar su PID), se adopta en vez de lanzar otro igual, que publicaría
    lo mismo dos veces. De los varios procesos de una tarea, el que encabeza su grupo."""
    marca, encontrado = str(prog).encode(), None
    for d in pathlib.Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if marca not in (d / "cmdline").read_bytes().split(b"\0"):
                continue
            campos = (d / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if campos[0] == "Z":
            continue
        pid = int(d.name)
        if int(campos[2]) == pid:           # encabeza su grupo: el lanzador (o un ffmpeg de antes de la 0.4)
            return pid
        encontrado = encontrado or pid
    return encontrado


def procesos_de_run():
    """{tarea: pid} de todo lo que corre con un .prog de RUN, lo conozca o no la base."""
    prefijo, vistos = str(RUN).encode() + b"/", {}
    for d in pathlib.Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            args = (d / "cmdline").read_bytes().split(b"\0")
            campos = (d / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if campos[0] == "Z":
            continue
        for a in args:
            if a.startswith(prefijo) and a.endswith(b".prog"):
                tarea, pid = a[len(prefijo):-5].decode(errors="replace"), int(d.name)
                if int(campos[2]) == pid or tarea not in vistos:
                    vistos[tarea] = pid
                break
    return vistos


def codificador_h264(ffmpeg):
    """Con qué se codifica el vídeo de una conversión: NVENC si la tarjeta lo da (se prueba de
    verdad, codificando unos fotogramas) y, si no, libx264. config.json → "codificador" lo fija."""
    pedido = db.config().get("codificador", "auto")
    if pedido in ("libx264", "h264_nvenc"):
        return pedido
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=black:s=640x360:r=30", "-frames:v", "5", "-c:v", "h264_nvenc", "-f", "null", "-"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        return "h264_nvenc" if r.returncode == 0 else "libx264"
    except (OSError, subprocess.TimeoutExpired):
        return "libx264"


def barrer(pid, prog):
    """Tras morir una composición, mata lo que quede de su grupo con su comando: las capturas que
    seguían esperando a que ffmpeg abriera su FIFO (si no llegó a abrirlo, se quedan bloqueadas
    para siempre). No toca procesos de otro grupo ni con otro comando (un PID reutilizado)."""
    for d in pathlib.Path("/proc").iterdir():
        try:
            if (d.name.isdigit() and int((d / "stat").read_text().rsplit(")", 1)[1].split()[2]) == pid
                    and str(prog).encode() in (d / "cmdline").read_bytes().split(b"\0")):
                os.kill(int(d.name), signal.SIGKILL)
        except (OSError, ValueError, IndexError):
            pass


def recoger_hijos():
    while True:                             # evita zombis de los ffmpeg que lanzamos nosotros
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def final_de(prog):
    """Últimos 600 bytes del .prog: crece mientras el proceso corre y basta con el final."""
    with open(prog, "rb") as f:
        f.seek(max(0, os.fstat(f.fileno()).st_size - 600))
        return f.read().decode(errors="replace")


def leer_kbps(prog):
    try:
        txt = final_de(prog)
        if time.time() - prog.stat().st_mtime > 6:
            return None
    except OSError:
        return None
    for linea in reversed(txt.splitlines()):
        if linea.startswith("bitrate="):
            v = linea.split("=", 1)[1].replace("kbits/s", "").strip()
            try:
                return float(v)
            except ValueError:
                return None
    return None


def fotogramas(prog):
    """Fotogramas de vídeo que lleva escritos el proceso (frame= del .prog), o None."""
    try:
        txt = final_de(prog)
    except OSError:
        return None
    for linea in reversed(txt.splitlines()):
        if linea.startswith("frame="):
            try:
                return int(linea.split("=", 1)[1])
            except ValueError:
                return None
    return None


_sin_huecos = set()                         # .prog que no se pueden recortar (sistema de archivos sin huecos)


def recortar(prog, log):
    """Acota en disco los archivos de un proceso que sigue corriendo (antes solo se hacía al lanzarlo).
    .prog: ffmpeg escribe en su propia posición (sin O_APPEND), así que se vacía y se reescribe el
    final en el mismo sitio: queda un archivo disperso de pocos KB en disco. .log: el supervisor lo
    abrió en modo añadir, así que basta con dejar su final."""
    try:
        if prog not in _sin_huecos and os.stat(prog).st_blocks * 512 > PROG_MAX:
            with open(prog, "r+b") as f:
                fin = f.seek(0, 2)
                f.seek(max(0, fin - 4096))
                cola = f.read()
                f.truncate(0)
                f.seek(fin - len(cola))
                f.write(cola)
            if os.stat(prog).st_blocks * 512 > PROG_MAX:    # no se ganó espacio: no insistir
                _sin_huecos.add(prog)
        if os.path.getsize(log) > LOG_MAX:
            with open(log, "r+b") as f:
                f.seek(-(LOG_MAX // 5), 2)
                cola = f.read()
                f.seek(0)
                f.truncate()
                f.write(cola)
    except OSError:
        pass


def ultimo_error(log):
    try:
        with open(log, "rb") as f:
            f.seek(max(0, os.path.getsize(log) - 2000))
            lineas = [l for l in f.read().decode(errors="replace").splitlines() if l.strip()]
        return lineas[-1][:300] if lineas else ""
    except OSError:
        return ""


def adaptadores_del_hub():
    """Estado de los adaptadores NDI y Zixi, si el módulo está (ver adaptadores.py)."""
    try:
        import adaptadores
    except ImportError:
        return {}
    return adaptadores.estado()


def _mirar_fuente(f, info):
    """En un hilo aparte: (pistas, error) de la fuente. En HLS, con los nombres de sus pistas de
    audio, que declara la lista del origen y MediaMTX no conserva."""
    pistas, error = inspeccion.de_path(f["id"], info)
    if pistas and f["protocolo"] == "hls" and f["inicia"] == "hub":
        lista, _ = inspeccion.leer_lista_hls(f["url"])
        if lista:
            n = int(matriz.json_de(f.get("opciones"), {}).get("variante") or 0)
            pistas = inspeccion.con_nombres(pistas, lista["variantes"][n] if n < len(lista["variantes"]) else None)
    return pistas, error


class Supervisor:
    def __init__(self):
        RUN.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
        self.c = db.conectar()
        self.espera = {}                    # salida -> (segundos de espera, cuándo reintentar)
        self.mtx_ok = None
        self.comps = {}                     # composiciones de esta vuelta: {flujo: fila}
        self.comps_fuera = set()            # activas que se quedan paradas por el tope
        self.video = {}                     # composición -> (pid, fotogramas, desde cuándo no cambian)
        self.sin_senal = {}                 # flujo de hub -> desde cuándo su entrada no tiene señal
        self.estado_paths = None            # estado de MediaMTX de la vuelta anterior
        self.indices = {}                   # flujo de hub -> dirección de su entrada en esta vuelta
        self.reposo = None                  # estación en reposo en la vuelta anterior (None: sin saber)
        # ---- hub de señales
        self.hub = None                     # lo que piden las rutas en esta vuelta (None: no se pudo calcular)
        self.codificador = None             # se averigua la primera vez que hace falta
        self.avisos = {}                    # tarea -> (texto, cuándo) de la última caída anotada
        self.senales = {}                   # fuente -> (con señal, cuándo se guardó)
        self.inspectores = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="inspeccion")
        self.mirando = {}                   # fuente -> (huella, tarea en curso)
        self.miradas = {}                   # fuente -> cuándo se intentó por última vez
        self.primera = True                 # primera vuelta: se barre lo que corra sin que nadie lo pida
        self.problemas = None               # servidores de MediaMTX que no se pudieron encender

    def estado(self, sid):
        r = self.c.execute("SELECT * FROM estado_salidas WHERE salida=?", (sid,)).fetchone()
        return dict(r) if r else {"salida": sid, "pid": None, "reinicios": 0, "intentos": 0, "firma": "",
                                  "desde": None}

    def guardar(self, e):
        self.c.execute("""INSERT INTO estado_salidas(salida,pid,desde,reinicios,intentos,error,kbps,firma,visto)
                          VALUES (:salida,:pid,:desde,:reinicios,:intentos,:error,:kbps,:firma,:visto)
                          ON CONFLICT(salida) DO UPDATE SET pid=:pid,desde=:desde,reinicios=:reinicios,
                          intentos=:intentos,error=:error,kbps=:kbps,firma=:firma,visto=:visto""",
                       {"error": "", "kbps": None, "intentos": 0, **e, "visto": time.time()})

    def fuente_de(self, f):
        """Entrada de hub: cuál de sus direcciones (db.fuentes) se usa. Si la que está puesta lleva
        CAMBIO_ENTRADA s sin señal, pasa a la siguiente (el mismo feed en otro hub y, al final, el
        origen por internet) y, tras la última, vuelve a la del hub elegido. Al editar el flujo
        empieza otra vez por el hub elegido. Se guarda en la base para el panel y para que un
        reinicio del supervisor no cambie de dirección."""
        fuentes = db.fuentes(f)
        if not fuentes:
            return 0
        huella = hashlib.sha256("\n".join(fuentes).encode()).hexdigest()[:16]
        r = self.c.execute("SELECT indice, firma FROM entradas_activas WHERE flujo=?", (f["id"],)).fetchone()
        indice = r["indice"] % len(fuentes) if r and r["firma"] == huella else 0
        ahora = time.time()
        if r and r["firma"] != huella:
            self.sin_senal.pop(f["id"], None)
        if f.get("directo"):
            # 1080i directo, sin MediaMTX: hay señal si algún destino envía; sin destinos activos no se juzga
            kbps = [r["kbps"] for r in self.c.execute(
                """SELECT e.kbps FROM salidas s LEFT JOIN estado_salidas e ON e.salida = s.id
                   WHERE s.flujo=? AND s.activo=1 AND s.tipo IN ('srt','rtmp')""", (f["id"],))]
            con_senal = None if not kbps else any(k for k in kbps)
        else:
            con_senal = None if self.estado_paths is None else bool((self.estado_paths.get(f["id"]) or {}).get("ready"))
        if con_senal is None or con_senal:
            self.sin_senal.pop(f["id"], None)       # con señal (o sin saber): no se juzga
        else:
            desde = self.sin_senal.setdefault(f["id"], ahora)
            if len(fuentes) > 1 and ahora - desde > CAMBIO_ENTRADA:
                indice = (indice + 1) % len(fuentes)
                self.sin_senal[f["id"]] = ahora
                host = urllib.parse.urlsplit(fuentes[indice].split("?")[0]).hostname or "?"
                destino = "vuelve al hub elegido" if indice == 0 else f"pasa al respaldo {indice} de {len(fuentes) - 1}"
                db.evento(self.c, f"entrada sin señal {CAMBIO_ENTRADA:.0f} s: {destino} ({host})",
                          flujo=f["id"])
        if not r or (r["indice"], r["firma"]) != (indice, huella):
            self.c.execute("""INSERT INTO entradas_activas(flujo, indice, firma, desde) VALUES (?,?,?,?)
                              ON CONFLICT(flujo) DO UPDATE SET indice=excluded.indice,
                              firma=excluded.firma, desde=excluded.desde""", (f["id"], indice, huella, ahora))
        return indice

    def sincronizar_mediamtx(self, flujos):
        deseados, canales = {}, db.canales_recorder()
        self.indices = {}
        for f in flujos:
            if f["entrada"] == "hub":
                self.indices[f["id"]] = self.fuente_de(f)
            if f.get("directo"):                    # 1080i: MediaMTX no sabe leerlo; no lleva path
                continue
            conf = mtx.conf_de_flujo(f, canales, self.indices.get(f["id"], 0))
            if conf:
                deseados[path_de_flujo(f)] = conf
        for fid in self.comps:                      # la composición publica ahí (desde este equipo)
            deseados[composicion.path_comp(fid)] = {"source": "publisher"}
        del_hub = set()
        if self.hub:                                # fuentes, señales derivadas y destinos que el hub entrega
            del_hub = set(self.hub["paths"]) - set(deseados)
            deseados.update({n: conf for n, conf in self.hub["paths"].items() if n in del_hub})
        try:
            actuales = mtx.paths_config()
            for nombre, conf in deseados.items():
                self.c.execute("INSERT OR IGNORE INTO paths_mtx(nombre) VALUES (?)", (nombre,))
                if mtx.asegurar_path(nombre, conf, actuales) and nombre not in del_hub:
                    db.evento(self.c, f"MediaMTX: path {nombre} declarado")     # los del hub van y vienen con la señal
            # solo se borran paths que creó el supervisor; los de mediamtx.yml no se tocan. Si no se
            # pudo calcular lo que pide el hub en esta vuelta, no se borra nada: podrían ser suyos
            for (nombre,) in self.c.execute("SELECT nombre FROM paths_mtx").fetchall() if self.hub is not None else ():
                if nombre not in deseados:
                    if nombre in actuales:
                        mtx.borrar_path(nombre)
                    self.c.execute("DELETE FROM paths_mtx WHERE nombre=?", (nombre,))
            self.sincronizar_acceso(flujos)
            estado = mtx.paths_estado()
            if self.mtx_ok is False:
                db.evento(self.c, "MediaMTX vuelve a responder")
            self.mtx_ok = True
            return estado
        except mtx.ErrorMTX as e:
            if self.mtx_ok is not False:
                db.evento(self.c, f"MediaMTX no responde: {e}")
            self.mtx_ok = False
            return None

    def sincronizar_acceso(self, flujos):
        """HLS bajo demanda: solo se lee desde la red un flujo activo con un destino HLS activo o
        con la vista previa abierta. Las reglas quedan en MediaMTX y no dependen del panel."""
        activos = {f["id"]: path_de_flujo(f) for f in flujos if not f.get("directo")}
        comps = self.comps
        paths = set()
        for r in self.c.execute("SELECT DISTINCT flujo, fuente FROM salidas WHERE activo=1 AND tipo='hls'"):
            if r["flujo"] in activos:
                paths.add(composicion.path_comp(r["flujo"]) if r["fuente"] == "compuesta" else activos[r["flujo"]])
        self.c.execute("DELETE FROM vistas_previas WHERE hasta < ?", (time.time(),))
        for (f,) in self.c.execute("SELECT flujo FROM vistas_previas").fetchall():
            if f in activos:                        # la vista previa enseña la limpia y la compuesta
                paths.add(activos[f])
                if f in comps:
                    paths.add(composicion.path_comp(f))
        hub = self.hub or matriz.vacio()
        abiertos = paths | hub["abiertos"]["read"]
        # sin saber qué pide el hub (self.hub is None) no se le quitan sus servidores: se dejan como estén
        servicios = matriz.servicios(self.c, db.config()) if self.hub is not None else None
        if self.reposo:
            servicios = {}
        cambio, problemas = mtx.asegurar_acceso(abiertos, hub["usuarios"], hub["abiertos"]["publish"], servicios) \
            if servicios is not None else (mtx.asegurar_acceso_solo(abiertos, hub["usuarios"], hub["abiertos"]["publish"]), {})
        if cambio:
            db.evento(self.c, "Acceso de MediaMTX actualizado. Sin clave se lee: " + (", ".join(sorted(abiertos)) or "nada"))
        if problemas != self.problemas:             # p. ej. el puerto RTMP ocupado por otro programa
            self.problemas = problemas
            db.guardar_ajuste(self.c, "servidores", {"problemas": problemas, "t": time.time()})
            for nombre, motivo in problemas.items():
                db.evento(self.c, f"MediaMTX: no se enciende {nombre.upper()}. {motivo}")

    def composiciones(self, por_id):
        """Composiciones activas de los flujos de esta vuelta, como mucho COMP_MAX: primero las que
        ya corren y después las de los flujos más antiguos. Devuelve (las que pueden correr,
        las que se quedan fuera por el tope)."""
        todas = {fid: k for fid, k in composicion.activas(self.c).items()
                 if fid in por_id and not por_id[fid].get("directo")}

        def corre(fid):
            tarea = composicion.id_tarea(fid)
            return vivo(self.estado(tarea)["pid"], RUN / f"{tarea}.prog")

        def creado(fid):                    # un valor raro en la base no debe romper la vuelta
            v = por_id[fid].get("creado")
            return v if isinstance(v, (int, float)) else float("inf")
        orden = sorted(todas, key=lambda fid: (not corre(fid), creado(fid), fid))
        return {fid: todas[fid] for fid in orden[:COMP_MAX]}, set(orden[COMP_MAX:])

    def video_parado(self, sid, pid, prog):
        """La composición sigue viva pero su vídeo lleva VIDEO_PARADO s sin avanzar. Pasa si se cae
        una captura: con eof_action=endall el vídeo termina, pero el audio va en copia y ffmpeg
        sigue emitiéndolo con la imagen quieta."""
        n, ahora = fotogramas(prog), time.time()
        if n is None:                       # aún sin progreso (arrancando): no se juzga
            self.video.pop(sid, None)
            return False
        antes = self.video.get(sid)
        if not antes or antes[:2] != (pid, n):
            self.video[sid] = (pid, n, ahora)
            return False
        return ahora - antes[2] > VIDEO_PARADO

    def tareas(self, flujos, por_id):
        """Procesos que deben correr: {id: (comando, path que necesita listo, error si no puede)}."""
        comps = self.comps
        tareas = {}
        for fid, comp in comps.items():
            # una composición mal guardada detiene solo esa composición, no la vuelta de las salidas
            try:
                cmd = composicion.comando(comp, path_de_flujo(por_id[fid]), FFMPEG, RUN, COMPONER)
                firma(cmd)                                  # falla si algún valor no es texto
                if any("\0" in a for a in cmd):             # Popen no lo aceptaría
                    raise ValueError("carácter nulo")
                tareas[composicion.id_tarea(fid)] = (cmd, path_de_flujo(por_id[fid]), None)
            except Exception as err:
                tareas[composicion.id_tarea(fid)] = (None, None, f"Composición no válida: {err!r}"[:300])
        limite = f"Límite de {COMP_MAX} composiciones a la vez: arrancará cuando se libere una"
        for fid in self.comps_fuera:                        # el bucle de limpieza las para
            tareas[composicion.id_tarea(fid)] = (None, None, limite)
        for s in db.filas(self.c, "SELECT * FROM salidas WHERE activo=1 AND tipo IN ('srt','rtmp')"):
            if s["flujo"] not in por_id:
                continue
            f = por_id[s["flujo"]]
            if f.get("directo"):                    # 1080i: el destino lee la entrada misma, sin MediaMTX
                if s.get("fuente") == "compuesta":
                    tareas[s["id"]] = (None, None, "Flujo 1080i directo: sin composición")
                    continue
                fuentes = db.fuentes(f)
                directa = fuentes[self.indices.get(f["id"], 0) % len(fuentes)] if fuentes else ""
                tareas[s["id"]] = (comando(s, None, directa), None, None) if directa else \
                    (None, None, "El flujo no tiene entrada")
                continue
            if s.get("fuente") == "compuesta":
                if s["flujo"] not in comps:
                    tareas[s["id"]] = (None, None, limite if s["flujo"] in self.comps_fuera
                                       else "La composición del flujo está desactivada")
                    continue
                path = composicion.path_comp(s["flujo"])
            else:
                path = path_de_flujo(por_id[s["flujo"]])
            tareas[s["id"]] = (comando(s, path), path, None)
        for tid, tarea in (self.hub or {}).get("tareas", {}).items():
            cmd = tarea[0]
            try:                                    # una ruta mal guardada no detiene a las demás
                if cmd is not None:
                    firma(cmd)
                    if any("\0" in a for a in cmd):
                        raise ValueError("carácter nulo")
                tareas[tid] = tarea
            except Exception as err:
                tareas[tid] = (None, None, f"Configuración no válida: {err!r}"[:300])
        return tareas

    # ------------------------------------------------------------ hub de señales
    def calcular_hub(self, reposo):
        """Lo que piden las rutas guardadas. Si falla el cálculo (un dato imposible en la base), el
        hub se queda como está en esta vuelta: ni se lanza ni se para nada suyo."""
        if reposo:
            return matriz.vacio()
        if self.codificador is None:
            self.codificador = codificador_h264(FFMPEG)
            db.evento(self.c, f"conversiones de vídeo con {self.codificador}")
        try:
            return matriz.calcular(self.c, self.estado_paths, self.codificador, adaptadores_del_hub())
        except Exception as err:
            print(f"hub: no se pudo calcular lo que piden las rutas: {err!r}", file=sys.stderr, flush=True)
            return None

    def anotar_senales(self, estado):
        """Cuándo hubo señal en cada fuente (para «última señal»). Se guarda al cambiar y, mientras
        hay señal, cada 10 s: no hace falta más precisión y así no se escribe en cada vuelta."""
        ahora = time.time()
        for f in self.hub["fuentes"]:
            lista = bool((estado.get(f["id"]) or {}).get("ready"))
            antes = self.senales.get(f["id"])
            if antes and antes[0] == lista and (not lista or ahora - antes[1] < 10):
                continue
            self.senales[f["id"]] = (lista, ahora)
            if antes is None or antes[0] != lista:
                self.c.execute("""INSERT INTO senales(path, lista, desde, ultima) VALUES (?,?,?,?)
                                  ON CONFLICT(path) DO UPDATE SET desde=CASE WHEN lista=excluded.lista THEN desde
                                  ELSE excluded.desde END, lista=excluded.lista,
                                  ultima=COALESCE(excluded.ultima, ultima)""",
                               (f["id"], int(lista), ahora, ahora if lista else None))
            else:
                self.c.execute("UPDATE senales SET ultima=? WHERE path=?", (ahora, f["id"]))
        vivas = {f["id"] for f in self.hub["fuentes"]}
        for fid in [x for x in self.senales if x not in vivas]:
            del self.senales[fid]
            self.c.execute("DELETE FROM senales WHERE path=?", (fid,))
            self.c.execute("DELETE FROM inspecciones WHERE fuente=?", (fid,))

    def inspeccionar(self, estado):
        """Mira qué trae cada fuente cuando llega su señal o cambia (otra conexión del origen, otras
        pistas). Lo que MediaMTX ya sabe se guarda al momento: las rutas que copian arrancan sin
        esperar. El resto (fps, fotogramas B) lo mira ffprobe aparte, sin parar la vuelta."""
        ahora = time.time()
        for fid, (huella, tarea) in list(self.mirando.items()):
            if tarea.done():
                del self.mirando[fid]
                try:
                    pistas, error = tarea.result()
                except Exception as err:
                    pistas, error = None, f"inspección fallida: {err!r}"[:300]
                if pistas:                          # solo vale si la señal sigue siendo la misma
                    self.c.execute("UPDATE inspecciones SET pistas=?, error='', t=? WHERE fuente=? AND huella=?",
                                   (json.dumps(pistas, ensure_ascii=False), ahora, fid, huella))
                else:
                    self.c.execute("UPDATE inspecciones SET error=?, t=? WHERE fuente=? AND huella=?",
                                   (error or "sin datos", ahora, fid, huella))
        for f in self.hub["fuentes"]:
            fid, info = f["id"], estado.get(f["id"])
            huella = inspeccion.huella(info)
            if not huella or fid in self.mirando:
                continue
            r = self.c.execute("SELECT huella, pistas FROM inspecciones WHERE fuente=?", (fid,)).fetchone()
            if not r or r["huella"] != huella:
                self.c.execute("""INSERT INTO inspecciones(fuente, t, huella, pistas, error) VALUES (?,?,?,?,'')
                                  ON CONFLICT(fuente) DO UPDATE SET t=excluded.t, huella=excluded.huella,
                                  pistas=excluded.pistas, error=''""",
                               (fid, ahora, huella, json.dumps(inspeccion.de_mediamtx(info), ensure_ascii=False)))
            elif not (matriz.json_de(r["pistas"], None) or {"parcial": True}).get("parcial"):
                continue                            # ya está mirada del todo
            elif ahora - self.miradas.get(fid, 0) < REINSPECCION or not inspeccion.algo_pasa(info):
                continue                            # o nada de lo que trae cabe en el enlace: no hay qué mirar
            self.miradas[fid] = ahora
            self.mirando[fid] = (huella, self.inspectores.submit(_mirar_fuente, dict(f), info))

    def ocultos(self):
        """Claves que el lanzador debe tapar en los registros: todas las guardadas."""
        return secretos.ordenar(set(secretos.de_filas(
            db.filas(self.c, "SELECT url, streamid, passphrase, respaldos FROM flujos"),
            db.filas(self.c, "SELECT tipo, url, streamid, passphrase, clave FROM salidas"))) | matriz.secretos(self.c))

    def anotar_caida(self, sid, texto):
        """La caída va a los eventos, pero la misma (un destino apagado que se reintenta cada pocos
        segundos) solo una vez cada MISMO_AVISO s."""
        antes = self.avisos.get(sid)
        if antes and antes[0] == texto and time.time() - antes[1] < MISMO_AVISO:
            return
        self.avisos[sid] = (texto, time.time())
        db.evento(self.c, f"caída: {texto}", salida=sid)

    def atender_ordenes(self):
        """«Reintentar» desde el panel: la cuenta de intentos vuelve a cero y no se espera más."""
        ordenes = db.filas(self.c, "SELECT id, orden, tarea FROM ordenes ORDER BY id")
        for o in ordenes:
            if o["orden"] == "reintentar":
                self.c.execute("UPDATE estado_salidas SET intentos=0, error='' WHERE salida=?", (o["tarea"],))
                self.espera.pop(o["tarea"], None)
                self.avisos.pop(o["tarea"], None)
                db.evento(self.c, "reintento pedido desde el panel", salida=o["tarea"])
        if ordenes:
            self.c.execute("DELETE FROM ordenes WHERE id<=?", (ordenes[-1]["id"],))

    def vuelta(self):
        recoger_hijos()
        self.atender_ordenes()
        flujos = db.filas(self.c, "SELECT * FROM flujos WHERE activo=1")
        reposo = db.reposo_recorder() is not None
        if reposo != self.reposo:
            if reposo:
                db.evento(self.c, "Recorder en reposo (servicio apagado): se paran todas las salidas")
            elif self.reposo:
                db.evento(self.c, "Recorder encendido tras el reposo: vuelven las salidas activas")
            self.reposo = reposo
        if reposo:                          # como si no hubiera ningún flujo activo
            flujos = []
        por_id = {f["id"]: f for f in flujos}
        # una sola lectura por vuelta y solo de los flujos de esta vuelta (un flujo iniciado entre
        # las dos consultas ya no deja la vuelta a medias), con el tope de composiciones a la vez
        self.comps, self.comps_fuera = self.composiciones(por_id)
        if self.estado_paths is None:               # al arrancar (o si MediaMTX no respondía): el hub decide
            try:                                    # con el estado real, no como si nada tuviera señal
                self.estado_paths = mtx.paths_estado()
            except mtx.ErrorMTX:
                pass
        self.hub = self.calcular_hub(reposo)
        estado_paths = self.estado_paths = self.sincronizar_mediamtx(flujos)
        if self.hub and estado_paths is not None:
            self.anotar_senales(estado_paths)
            self.inspeccionar(estado_paths)
        deseadas = self.tareas(flujos, por_id)
        politicas = (self.hub or {}).get("politicas", {})
        ocultos = None                              # se calculan solo si hay algo que lanzar
        primera, adoptados, lanzados = self.primera, 0, 0

        if self.primera:                            # lo que corre sin que la base lo conozca ni nadie lo pida
            self.primera = False
            conocidos = {e["salida"] for e in db.filas(self.c, "SELECT salida FROM estado_salidas")}
            for tarea, pid in procesos_de_run().items():
                if tarea not in conocidos and tarea not in deseadas and (self.hub is not None or not matriz.es_del_hub(tarea)):
                    matar(pid)
                    db.evento(self.c, "proceso sin dueño detenido", salida=tarea)

        # procesos que ya no deben correr (borrados, desactivados o de un flujo detenido)
        for e in db.filas(self.c, "SELECT * FROM estado_salidas"):
            if self.hub is None and matriz.es_del_hub(e["salida"]):
                continue                            # sin saber qué pide el hub, lo suyo no se toca
            if e["salida"] not in deseadas or deseadas[e["salida"]][0] is None:
                if vivo(e["pid"], RUN / f"{e['salida']}.prog"):
                    matar(e["pid"])
                    db.evento(self.c, "salida detenida", salida=e["salida"])
                if e["salida"] not in deseadas:
                    self.c.execute("DELETE FROM estado_salidas WHERE salida=?", (e["salida"],))
                self.espera.pop(e["salida"], None)
                self.avisos.pop(e["salida"], None)

        for sid, (cmd, path, impedimento) in deseadas.items():
            e = self.estado(sid)
            if cmd is None:                                  # no puede correr (p. ej. sin composición)
                self.guardar({**e, "pid": None, "desde": None, "kbps": None, "error": impedimento})
                continue
            huella = firma(cmd)
            prog, log = RUN / f"{sid}.prog", LOGS / f"{sid}.log"
            corriendo = vivo(e["pid"], prog)
            politica = politicas.get(sid) or matriz.RECONEXION
            e["intentos"] = e.get("intentos") or 0

            if corriendo and e["firma"] != huella:          # se editó: relanzar
                matar(e["pid"])
                corriendo, e["pid"], e["intentos"] = False, None, 0
                self.espera.pop(sid, None)
                db.evento(self.c, "relanzada por cambio de configuración", salida=sid)
            elif not corriendo and e["firma"] and e["firma"] != huella:
                e["intentos"] = 0                           # se editó mientras no corría: cuenta nueva
                self.espera.pop(sid, None)

            if corriendo and cmd[0] == str(COMPONER) and self.video_parado(sid, e["pid"], prog):
                matar(e["pid"])                             # cuenta como caída (abajo) y se relanza
                corriendo = False
                db.evento(self.c, f"vídeo de la composición parado más de {VIDEO_PARADO:.0f} s: se relanza",
                          salida=sid)

            if not corriendo and e["pid"]:                  # murió por su cuenta: cuenta como caída
                if cmd[0] == str(COMPONER):                 # composición: lo que quede de su grupo
                    barrer(e["pid"], prog)
                e["reinicios"] += 1
                e["intentos"] += 1
                e["pid"] = None
                self.anotar_caida(sid, ultimo_error(log))

            if corriendo:
                adoptados += 1
                recortar(prog, log)
                kbps = leer_kbps(prog)
                if kbps and e["desde"] and time.time() - e["desde"] > ESTABLE:
                    self.espera.pop(sid, None)              # un minuto estable: se reinicia la espera
                    e["intentos"] = 0
                self.guardar({**e, "kbps": kbps, "error": ""})
                continue

            # no corre: ¿hay entrada? sin entrada no tiene sentido lanzarlo
            # sin path (flujo 1080i directo): no hay a quién preguntar, se lanza y se reintenta si cae
            listo = path is None or (estado_paths is not None and (estado_paths.get(path) or {}).get("ready"))
            if not listo:
                esperando = "Esperando la composición" if path.startswith("comp_") else "Esperando la entrada"
                self.guardar({**e, "pid": None, "desde": None, "kbps": None,
                              "error": esperando if estado_paths is not None else "MediaMTX no responde"})
                continue

            tope = politica["max_intentos"]
            if tope and e["intentos"] >= tope:              # política del destino: no se insiste más
                self.guardar({**e, "pid": None, "kbps": None, "firma": huella,
                              "error": f"Sin más intentos tras {e['intentos']} fallos seguidos: "
                                       f"{ultimo_error(log) or 'sin detalle'}"[:300]})
                continue

            espera, cuando = self.espera.get(sid, (0.0, 0.0))
            if time.time() < cuando:
                self.guardar({**e, "pid": None, "kbps": None, "firma": huella,
                              "error": ultimo_error(log) or "Reintentando"})
                continue

            ya = buscar(prog)                               # nunca dos procesos para lo mismo
            if ya:
                adoptados += 1
                self.guardar({**e, "pid": ya, "desde": e["desde"] or time.time(), "firma": huella,
                              "kbps": None, "error": ""})
                db.evento(self.c, "proceso que ya corría: adoptado en vez de lanzar otro", salida=sid)
                continue
            try:
                if log.exists() and log.stat().st_size > LOG_MAX:
                    log.write_bytes(log.read_bytes()[-LOG_MAX // 5:])
                prog.unlink(missing_ok=True)
                if ocultos is None:
                    ocultos = self.ocultos()
                # el lanzador escribe la línea de arranque y lo que diga ffmpeg, todo sin claves
                p = subprocess.Popen([sys.executable, str(CORRER), str(log), *cmd], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, start_new_session=True,
                                     env={**os.environ, "MSXS_SECRETOS": json.dumps(ocultos)})
            except OSError as err:
                self.guardar({**e, "pid": None, "error": f"No se pudo lanzar: {err}"})
                continue
            cada = politica["cada_s"]                       # 0: espera creciente, como siempre
            espera = max(1.0, cada) if cada else min(ESPERA_MAX, max(2.0, espera * 2))
            self.espera[sid] = (espera, time.time() + espera)
            self.guardar({**e, "pid": p.pid, "desde": time.time(), "firma": huella,
                          "kbps": None, "error": ""})
            lanzados += 1

        if primera:                                 # para Ajustes: qué encontró el supervisor al arrancar
            rutas = len((self.hub or {}).get("senales", {}))
            db.guardar_ajuste(self.c, "arranque", {"t": time.time(), "siguen": adoptados, "lanzados": lanzados,
                                                   "rutas": rutas, "reposo": bool(reposo)})
            db.evento(self.c, f"arranque: {adoptados} procesos seguían en marcha y se conservan, {lanzados} lanzados")

    def correr(self):
        self.cerrojo = open(RUN / "supervisor.lock", "w")
        try:                                # dos supervisores lanzarían cada cosa dos veces
            fcntl.flock(self.cerrojo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("ya hay otro supervisor en marcha con esta base: este no arranca", file=sys.stderr, flush=True)
            sys.exit(3)
        db.evento(self.c, "supervisor iniciado")
        ocultos = self.ocultos()
        limpios = [r.name for r in LOGS.glob("*.log") if secretos.limpiar_registro(r, ocultos)]
        if limpios:
            db.evento(self.c, f"claves retiradas de {len(limpios)} registros de versiones anteriores")
        parar = []
        signal.signal(signal.SIGTERM, lambda *_: parar.append(1))
        while not parar:
            t0 = time.time()
            try:
                self.vuelta()
            except Exception as err:        # una vuelta fallida no debe tumbar al supervisor
                print(f"error en la vuelta: {err!r}", file=sys.stderr, flush=True)
            time.sleep(max(0.2, VUELTA - (time.time() - t0)))


if __name__ == "__main__":
    Supervisor().correr()
