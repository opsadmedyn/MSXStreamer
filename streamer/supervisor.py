#!/usr/bin/env python3
"""Media Syntaxis Streamer - supervisor.

Cada 2 s compara lo que pide la base de datos con lo que está corriendo y lo corrige:
  - declara en MediaMTX los paths de los flujos activos (y borra los que sobran);
  - mantiene un ffmpeg por cada salida SRT/RTMP activa, en copia, y lo relanza si cae,
    con espera creciente (2, 4, 8… hasta 30 s);
  - las salidas HLS no necesitan proceso: las sirve MediaMTX.

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

import db
import mtx

FFMPEG = db.config().get("ffmpeg", "ffmpeg")
RUN = db.BASE / "run"
LOGS = db.BASE / "logs"
LOG_MAX = 5 * 1024 * 1024
VUELTA = 2.0
ESPERA_MAX = 30.0


def path_de_flujo(f):
    return f["id"] if f["entrada"] == "pull" else "rec_" + f["canal"]


def url_salida(s):
    if s["tipo"] == "rtmp":
        base = s["url"].rstrip("/")
        return f"{base}/{s['clave']}" if s["clave"] else base
    # srt: los parámetros de ffmpeg van en la URL; la latencia se expresa en microsegundos
    # sin fragmentos: el streamid de Castr y otros ("#!::r=…") empieza por "#" y se perdería
    partes = urllib.parse.urlsplit(s["url"], allow_fragments=False)
    q = dict(urllib.parse.parse_qsl(partes.query))
    q["mode"] = "listener" if s["modo"] == "listener" else "caller"
    q["latency"] = str(int(s["latencia_ms"]) * 1000)
    q["pkt_size"] = "1316"
    if s["passphrase"]:
        q["passphrase"] = s["passphrase"]
    if s["streamid"]:
        q["streamid"] = s["streamid"]
    host = partes.netloc
    if s["modo"] == "listener":             # escucha en todas las interfaces, en el puerto indicado
        host = "0.0.0.0:" + (partes.port and str(partes.port) or "9000")
    return urllib.parse.urlunsplit(("srt", host, "", urllib.parse.urlencode(q), ""))


def comando(s, path):
    prog = RUN / f"{s['id']}.prog"
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning",
           "-progress", str(prog), "-stats_period", "2",
           "-i", mtx.url_lectura(path)]
    if s["tipo"] == "rtmp":                 # FLV admite un vídeo y un audio
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "flv"]
    else:
        cmd += ["-map", "0", "-c", "copy", "-f", "mpegts"]
    return cmd + [url_salida(s)]


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

    def vuelta(self):
        recoger_hijos()
        flujos = db.filas(self.c, "SELECT * FROM flujos WHERE activo=1")
        por_id = {f["id"]: f for f in flujos}
        estado_paths = self.sincronizar_mediamtx(flujos)
        salidas = db.filas(self.c, "SELECT * FROM salidas WHERE activo=1 AND tipo IN ('srt','rtmp')")
        deseadas = {s["id"]: s for s in salidas if s["flujo"] in por_id}

        # salidas que ya no deben correr (borradas, desactivadas o de un flujo detenido)
        for e in db.filas(self.c, "SELECT * FROM estado_salidas"):
            if e["salida"] not in deseadas:
                if vivo(e["pid"], RUN / f"{e['salida']}.prog"):
                    matar(e["pid"])
                    db.evento(self.c, "salida detenida", salida=e["salida"])
                self.c.execute("DELETE FROM estado_salidas WHERE salida=?", (e["salida"],))
                self.espera.pop(e["salida"], None)

        for sid, s in deseadas.items():
            path = path_de_flujo(por_id[s["flujo"]])
            cmd = comando(s, path)
            huella = firma(cmd)
            prog, log = RUN / f"{sid}.prog", LOGS / f"{sid}.log"
            e = self.estado(sid)
            corriendo = vivo(e["pid"], prog)

            if corriendo and e["firma"] != huella:          # se editó la salida: relanzar
                matar(e["pid"])
                corriendo, e["pid"] = False, None
                self.espera.pop(sid, None)
                db.evento(self.c, "salida relanzada por cambio de configuración", salida=sid)

            if not corriendo and e["pid"]:                  # murió por su cuenta: cuenta como caída
                e["reinicios"] += 1
                e["pid"] = None
                db.evento(self.c, f"salida caída: {ultimo_error(log)}", salida=sid)

            if corriendo:
                kbps = leer_kbps(prog)
                if kbps and e["desde"] and time.time() - e["desde"] > 60:
                    self.espera.pop(sid, None)              # un minuto estable: se reinicia la espera
                self.guardar({**e, "kbps": kbps, "error": ""})
                continue

            # no corre: ¿hay entrada? sin entrada no tiene sentido lanzar ffmpeg
            listo = estado_paths is not None and (estado_paths.get(path) or {}).get("ready")
            if not listo:
                self.guardar({**e, "pid": None, "desde": None, "kbps": None,
                              "error": "Esperando la entrada" if estado_paths is not None
                              else "MediaMTX no responde"})
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
                self.guardar({**e, "pid": None, "error": f"No se pudo lanzar ffmpeg: {err}"})
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
