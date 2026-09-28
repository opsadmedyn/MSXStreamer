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

Los ffmpeg corren en su propia sesión: si el supervisor se reinicia (por ejemplo al desplegar),
las salidas siguen al aire y el supervisor las vuelve a adoptar por su PID.
"""

import hashlib
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.parse

import composicion
import db
import mtx

FFMPEG = db.config().get("ffmpeg", "ffmpeg")
RUN = db.BASE / "run"
LOGS = db.BASE / "logs"
LOG_MAX = 5 * 1024 * 1024
VUELTA = 2.0
ESPERA_MAX = 30.0
COMPONER = pathlib.Path(__file__).parent / "componer.sh"


def path_de_flujo(f):
    return f["id"] if f["entrada"] == "pull" else "rec_" + f["canal"]


def url_salida(s):
    """URL de destino y opciones de ffmpeg que van aparte.

    El streamid y la passphrase van como opciones (-srt_streamid, -passphrase) y no dentro de la
    URL: así llegan tal cual aunque lleven "#", "," o "=" (el streamid de Castr es "#!::r=…,password=…")
    y no dependen de que la versión de ffmpeg decodifique la URL."""
    if s["tipo"] == "rtmp":
        base = s["url"].rstrip("/")
        return (f"{base}/{s['clave']}" if s["clave"] else base), []
    # srt: la latencia se expresa en microsegundos. Sin fragmentos: un "#" pegado en la URL
    # (el streamid de Castr) es parte del valor, no un ancla.
    partes = urllib.parse.urlsplit(s["url"], allow_fragments=False)
    q = dict(urllib.parse.parse_qsl(partes.query))
    streamid = s["streamid"] or q.pop("streamid", "")
    passphrase = s["passphrase"] or q.pop("passphrase", "")
    q.pop("streamid", None); q.pop("passphrase", None)
    q["mode"] = "listener" if s["modo"] == "listener" else "caller"
    q["latency"] = str(int(s["latencia_ms"]) * 1000)
    q["pkt_size"] = "1316"
    host = partes.netloc
    if s["modo"] == "listener":             # escucha en todas las interfaces, en el puerto indicado
        host = "0.0.0.0:" + (partes.port and str(partes.port) or "9000")
    opciones = []
    if passphrase:
        opciones += ["-passphrase", passphrase]
    if streamid:
        opciones += ["-srt_streamid", streamid]
    return urllib.parse.urlunsplit(("srt", host, "", urllib.parse.urlencode(q), "")), opciones


def comando(s, path):
    prog = RUN / f"{s['id']}.prog"
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning",
           "-progress", str(prog), "-stats_period", "2",
           "-i", mtx.url_lectura(path)]
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


def matar(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except OSError:
            return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def recoger_hijos():
    while True:                             # evita zombis de los ffmpeg que lanzamos nosotros
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def leer_kbps(prog):
    try:
        txt = prog.read_text()[-600:]
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


def ultimo_error(log):
    try:
        with open(log, "rb") as f:
            f.seek(max(0, os.path.getsize(log) - 2000))
            lineas = [l for l in f.read().decode(errors="replace").splitlines() if l.strip()]
        return lineas[-1][:300] if lineas else ""
    except OSError:
        return ""


class Supervisor:
    def __init__(self):
        RUN.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
        self.c = db.conectar()
        self.espera = {}                    # salida -> (segundos de espera, cuándo reintentar)
        self.mtx_ok = None
        self.comps = {}                     # composiciones de esta vuelta: {flujo: fila}

    def estado(self, sid):
        r = self.c.execute("SELECT * FROM estado_salidas WHERE salida=?", (sid,)).fetchone()
        return dict(r) if r else {"salida": sid, "pid": None, "reinicios": 0, "firma": "", "desde": None}

    def guardar(self, e):
        self.c.execute("""INSERT INTO estado_salidas(salida,pid,desde,reinicios,error,kbps,firma,visto)
                          VALUES (:salida,:pid,:desde,:reinicios,:error,:kbps,:firma,:visto)
                          ON CONFLICT(salida) DO UPDATE SET pid=:pid,desde=:desde,reinicios=:reinicios,
                          error=:error,kbps=:kbps,firma=:firma,visto=:visto""",
                       {"error": "", "kbps": None, **e, "visto": time.time()})

    def sincronizar_mediamtx(self, flujos):
        deseados, canales = {}, db.canales_recorder()
        for f in flujos:
            conf = mtx.conf_de_flujo(f, canales)
            if conf:
                deseados[path_de_flujo(f)] = conf
        for fid in self.comps:                      # la composición publica ahí (desde este equipo)
            deseados[composicion.path_comp(fid)] = {"source": "publisher"}
        try:
            actuales = mtx.paths_config()
            for nombre, conf in deseados.items():
                self.c.execute("INSERT OR IGNORE INTO paths_mtx(nombre) VALUES (?)", (nombre,))
                if mtx.asegurar_path(nombre, conf, actuales):
                    db.evento(self.c, f"MediaMTX: path {nombre} declarado")
            # solo se borran paths que creó el supervisor; los de mediamtx.yml no se tocan
            for (nombre,) in self.c.execute("SELECT nombre FROM paths_mtx").fetchall():
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
        activos = {f["id"]: path_de_flujo(f) for f in flujos}
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
        if mtx.asegurar_acceso(paths):
            db.evento(self.c, "HLS publicado: " + (", ".join(sorted(paths)) or "ninguno"))

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
        for s in db.filas(self.c, "SELECT * FROM salidas WHERE activo=1 AND tipo IN ('srt','rtmp')"):
            if s["flujo"] not in por_id:
                continue
            if s.get("fuente") == "compuesta":
                if s["flujo"] not in comps:
                    tareas[s["id"]] = (None, None, "La composición del flujo está desactivada")
                    continue
                path = composicion.path_comp(s["flujo"])
            else:
                path = path_de_flujo(por_id[s["flujo"]])
            tareas[s["id"]] = (comando(s, path), path, None)
        return tareas

    def vuelta(self):
        recoger_hijos()
        flujos = db.filas(self.c, "SELECT * FROM flujos WHERE activo=1")
        por_id = {f["id"]: f for f in flujos}
        # una sola lectura por vuelta y solo de los flujos de esta vuelta (un flujo iniciado entre
        # las dos consultas ya no deja la vuelta a medias)
        self.comps = {fid: k for fid, k in composicion.activas(self.c).items() if fid in por_id}
        estado_paths = self.sincronizar_mediamtx(flujos)
        deseadas = self.tareas(flujos, por_id)

        # procesos que ya no deben correr (borrados, desactivados o de un flujo detenido)
        for e in db.filas(self.c, "SELECT * FROM estado_salidas"):
            if e["salida"] not in deseadas or deseadas[e["salida"]][0] is None:
                if vivo(e["pid"], RUN / f"{e['salida']}.prog"):
                    matar(e["pid"])
                    db.evento(self.c, "salida detenida", salida=e["salida"])
                if e["salida"] not in deseadas:
                    self.c.execute("DELETE FROM estado_salidas WHERE salida=?", (e["salida"],))
                self.espera.pop(e["salida"], None)

        for sid, (cmd, path, impedimento) in deseadas.items():
            e = self.estado(sid)
            if cmd is None:                                  # no puede correr (p. ej. sin composición)
                self.guardar({**e, "pid": None, "desde": None, "kbps": None, "error": impedimento})
                continue
            huella = firma(cmd)
            prog, log = RUN / f"{sid}.prog", LOGS / f"{sid}.log"
            corriendo = vivo(e["pid"], prog)

            if corriendo and e["firma"] != huella:          # se editó: relanzar
                matar(e["pid"])
                corriendo, e["pid"] = False, None
                self.espera.pop(sid, None)
                db.evento(self.c, "relanzada por cambio de configuración", salida=sid)

            if not corriendo and e["pid"]:                  # murió por su cuenta: cuenta como caída
                e["reinicios"] += 1
                e["pid"] = None
                db.evento(self.c, f"caída: {ultimo_error(log)}", salida=sid)

            if corriendo:
                kbps = leer_kbps(prog)
                if kbps and e["desde"] and time.time() - e["desde"] > 60:
                    self.espera.pop(sid, None)              # un minuto estable: se reinicia la espera
                self.guardar({**e, "kbps": kbps, "error": ""})
                continue

            # no corre: ¿hay entrada? sin entrada no tiene sentido lanzarlo
            listo = estado_paths is not None and (estado_paths.get(path) or {}).get("ready")
            if not listo:
                esperando = "Esperando la composición" if path.startswith("comp_") else "Esperando la entrada"
                self.guardar({**e, "pid": None, "desde": None, "kbps": None,
                              "error": esperando if estado_paths is not None else "MediaMTX no responde"})
                continue

            espera, cuando = self.espera.get(sid, (0.0, 0.0))
            if time.time() < cuando:
                self.guardar({**e, "pid": None, "kbps": None,
                              "error": ultimo_error(log) or "Reintentando"})
                continue

            try:
                if log.exists() and log.stat().st_size > LOG_MAX:
                    log.write_bytes(log.read_bytes()[-LOG_MAX // 5:])
                prog.unlink(missing_ok=True)
                with open(log, "ab") as salida_log:
                    salida_log.write(f"\n== {time.strftime('%F %T')} {' '.join(cmd)}\n".encode())
                    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=salida_log, start_new_session=True)
            except OSError as err:
                self.guardar({**e, "pid": None, "error": f"No se pudo lanzar: {err}"})
                continue
            espera = min(ESPERA_MAX, max(2.0, espera * 2))
            self.espera[sid] = (espera, time.time() + espera)
            self.guardar({**e, "pid": p.pid, "desde": time.time(), "firma": huella,
                          "kbps": None, "error": ""})

    def correr(self):
        db.evento(self.c, "supervisor iniciado")
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
