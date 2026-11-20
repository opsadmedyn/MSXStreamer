#!/usr/bin/env python3
"""Piezas del ensayo del relevo (tests/banco/relevo.sh). NO ES PARA PRODUCCIÓN.

    relevo_ayuda.py systemctl <orden> [unidades]   un systemd de mentira: lee las unidades de
                                                   /etc/systemd/system y las arranca y para como él
    relevo_ayuda.py medidor <archivo>              lee de la entrada estándar y apunta, cada segundo,
                                                   cuánto llegó y el mayor hueco entre dos llegadas
    relevo_ayuda.py hls <url> <archivo>            pide una lista HLS dos veces por segundo y apunta
                                                   si responde y por qué segmento va
    relevo_ayuda.py foto <archivo>                 cómo está la estación: los procesos de las salidas,
                                                   los servicios y las señales de MediaMTX
    relevo_ayuda.py comparar <antes> <después> [--solo-al-aire] [ids de salidas que sí pueden haber cambiado…]
                                                   lo que cambió y no debía. Con --solo-al-aire no mira
                                                   si el panel y el supervisor quedaron en marcha
    relevo_ayuda.py hueco <archivo> <desde> <hasta>     mayor hueco, en ms, entre esos dos instantes
    relevo_ayuda.py parada <archivo> <desde> <hasta>    mayor rato, en s, sin que la lista HLS avance
    relevo_ayuda.py quejas <registro del supervisor> <registro del panel>
                                                   lo que escribieron de malo desde su último arranque

El systemd de mentira hace lo que importa para el relevo: de cada unidad respeta ExecStart,
WorkingDirectory, Environment, Restart, RestartSec y KillMode. Con KillMode=process (el supervisor)
para solo el proceso principal y deja en marcha lo demás, que es lo que mantiene las salidas al
aire durante un despliegue; sin él para todo lo que la unidad haya lanzado, como systemd con su
grupo de control (aquí se reconoce por una variable de entorno que heredan). /usr/bin/python3 es
el Python de la prueba sin paquetes añadidos, como el del sistema en una estación.
"""

import json
import os
import pathlib
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request

UNIDADES = pathlib.Path("/etc/systemd/system")
BASE = pathlib.Path("/home/mediasat/streamer")
SIN_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------- systemd de mentira
def _sd():
    return pathlib.Path(os.environ["MSX_SD"])


def _unidad(nombre):
    nombre = nombre.removesuffix(".service")
    u = {"KillMode": "control-group", "Restart": "no", "RestartSec": "0.1", "Environment": []}
    for linea in (UNIDADES / f"{nombre}.service").read_text().splitlines():
        if "=" not in linea or linea.startswith(("#", "[")):
            continue
        k, v = linea.split("=", 1)
        if k == "Environment":
            u["Environment"].append(v)
        else:
            u[k.strip()] = v.strip()
    return nombre, u


def _pid(nombre):
    try:
        pid = int((_sd() / nombre / "pid").read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


MARCA = "MSX_UNIDAD_DE_MENTIRA"
ARRANQUE = "--- arranque pedido ---"


def _de_la_unidad(nombre):
    """PIDs de todo lo que lanzó esa unidad y sigue en marcha, como el grupo de control de systemd."""
    marca, suyos = f"{MARCA}={nombre}".encode(), []
    for d in pathlib.Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if marca in (d / "environ").read_bytes().split(b"\0"):
                    suyos.append(int(d.name))
            except OSError:
                pass
    return suyos


def _vigilante(nombre):
    """PID del proceso que hace de systemd para esa unidad, si sigue ahí."""
    try:
        pid = int((_sd() / nombre / "vigilante").read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _vigilar(nombre):
    """Hace de systemd para una unidad: la lanza y, si cae sin que nadie la haya parado, la
    vuelve a lanzar como diga Restart= y lo cuenta."""
    nombre, u = _unidad(nombre)
    d = _sd() / nombre
    (d / "vigilante").write_text(str(os.getpid()))
    orden = shlex.split(u["ExecStart"])
    if orden[0] == "/usr/bin/python3":
        orden[:1] = [os.environ["MSX_PY"], "-S"]
    entorno = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
    for e in u["Environment"]:
        k, v = e.split("=", 1)
        entorno[k] = v
    entorno[MARCA] = nombre                 # la heredan los procesos que lance: hace de grupo de control
    caidas = 0                              # las de este arranque: _arrancar borra la cuenta anterior
    while True:
        with open(_sd() / f"{nombre}.log", "ab") as log:
            p = subprocess.Popen(orden, cwd=u.get("WorkingDirectory") or "/", env=entorno, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
        (d / "pid").write_text(str(p.pid))
        p.wait()
        (d / "pid").unlink(missing_ok=True)
        if (d / "parar").exists() or u["Restart"] == "no":
            break
        caidas += 1
        (d / "caidas").write_text(str(caidas))
        time.sleep(float(u["RestartSec"].rstrip("s")))
        if (d / "parar").exists():
            break
    (d / "vigilante").unlink(missing_ok=True)


def _arrancar(nombre):
    nombre, _ = _unidad(nombre)
    if _vigilante(nombre):
        return
    d = _sd() / nombre
    d.mkdir(parents=True, exist_ok=True)
    for resto in ("parar", "pid", "vigilante", "caidas"):
        (d / resto).unlink(missing_ok=True)
    with open(_sd() / f"{nombre}.log", "a") as log:      # lo que escriba desde aquí es de este arranque
        log.write(ARRANQUE + "\n")
    with open(_sd() / "vigilantes.log", "ab") as log:
        subprocess.Popen([sys.executable, __file__, "vigilar", nombre], stdin=subprocess.DEVNULL, stdout=log,
                         stderr=log, start_new_session=True)
    limite = time.time() + 10
    while not _pid(nombre) and time.time() < limite:
        time.sleep(0.02)


def _parar(nombre):
    nombre, u = _unidad(nombre)
    d = _sd() / nombre
    if not _vigilante(nombre):
        return
    (d / "parar").write_text("")
    pid = _pid(nombre)
    try:
        if pid and u["KillMode"] == "process":      # solo el principal: lo que lanzó sigue en marcha
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    if u["KillMode"] != "process":                  # todo lo que la unidad lanzó
        for suyo in _de_la_unidad(nombre):
            try:
                os.kill(suyo, signal.SIGTERM)
            except OSError:
                pass
    limite = time.time() + 90
    while _vigilante(nombre) and time.time() < limite:
        time.sleep(0.02)
    if _vigilante(nombre):                          # no se fue en 90 s: a la fuerza, como systemd
        for a_quien in (pid, *(_de_la_unidad(nombre) if u["KillMode"] != "process" else ()), _vigilante(nombre)):
            try:
                os.kill(a_quien, signal.SIGKILL)
            except (OSError, TypeError):
                pass
        (d / "vigilante").unlink(missing_ok=True)


def systemctl(args):
    orden, unidades = args[0], [a for a in args[1:] if not a.startswith("-")]
    if orden == "daemon-reload":
        return 0
    if orden == "is-active":
        estados = ["active" if _pid(_unidad(u)[0]) else "activating" if _vigilante(_unidad(u)[0]) else "inactive"
                   for u in unidades]
        print("\n".join(estados))
        return 0 if all(e == "active" for e in estados) else 3
    if orden == "restart":
        # como systemd: para las unidades y después las arranca
        for u in unidades:
            _parar(u)
        for u in unidades:
            _arrancar(u)
        return 0
    if orden in ("start", "enable"):
        if orden == "start" or "--now" in args:
            for u in unidades:
                _arrancar(u)
        return 0
    if orden == "stop":
        for u in unidades:
            _parar(u)
        return 0
    print(f"systemctl de mentira: no sabe hacer «{orden}»", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- medidores
def medidor(archivo):
    """Apunta, cada segundo en que llega algo: instante, bytes y el mayor hueco (ms) entre dos
    llegadas seguidas. Un corte se ve como un hueco grande en la línea en que la señal vuelve."""
    entrada = sys.stdin.buffer
    ultimo, segundo, llegados, hueco = None, None, 0, 0.0
    with open(archivo, "a", buffering=1) as f:
        while True:
            datos = entrada.read1(65536)
            ahora = time.time()
            if not datos:
                break
            if ultimo is not None:
                hueco = max(hueco, ahora - ultimo)
            ultimo = ahora
            if segundo is None:
                segundo = int(ahora)
            if int(ahora) != segundo:
                f.write(f"{segundo} {llegados} {hueco * 1000:.0f}\n")
                segundo, llegados, hueco = int(ahora), 0, 0.0
            llegados += len(datos)
        if segundo is not None:
            f.write(f"{segundo} {llegados} {hueco * 1000:.0f}\n")
        f.write(f"{time.time():.0f} fin 0\n")


def hueco(archivo, desde, hasta):
    """Mayor hueco entre `desde` y `hasta`. Cuenta también el tiempo sin ninguna línea."""
    desde, hasta = float(desde), float(hasta)
    peor, previo, lineas = 0.0, None, 0
    for linea in pathlib.Path(archivo).read_text().split("\n"):
        campos = linea.split()
        if len(campos) != 3:
            continue
        if campos[1] == "fin":                  # el receptor terminó: si fue en ese rato, se cortó
            if desde <= float(campos[0]) <= hasta:
                print("inf")
                return
            continue
        t = float(campos[0])
        if t < desde - 1 or t > hasta:
            continue
        lineas += 1
        peor = max(peor, float(campos[2]))
        if previo is not None:
            peor = max(peor, (t - previo - 1) * 1000)
        previo = t
    if not lineas or previo < hasta - 3:
        print("inf")
        return
    print(f"{peor:.0f}")


def hls(url, archivo):
    """Sigue una lista HLS como un reproductor: pide la principal una vez y después, dos veces por
    segundo, la de su primera variante; si esa falla, vuelve a empezar por la principal. Apunta en
    cada vuelta: instante, respuesta y por dónde va la lista (número de secuencia más segmentos)."""
    variante = None
    with open(archivo, "a", buffering=1) as f:
        while True:
            t = time.time()
            try:
                if variante is None:
                    with SIN_PROXY.open(url, timeout=2) as r:
                        texto = r.read().decode(errors="replace")
                    nombre = next((ln.strip() for ln in texto.splitlines() if ln.strip() and not ln.startswith("#")), "")
                    variante = urllib.parse.urljoin(url, nombre) if ".m3u8" in nombre else url
                with SIN_PROXY.open(variante, timeout=2) as r:
                    texto = r.read().decode(errors="replace")
                pathlib.Path(archivo + ".ultima").write_text(texto)
                secuencia = next((int(ln.split(":")[1]) for ln in texto.splitlines()
                                  if ln.startswith("#EXT-X-MEDIA-SEQUENCE:")), 0)
                segmentos = sum(1 for ln in texto.splitlines() if ln.startswith("#EXTINF"))
                f.write(f"{t:.2f} 200 {secuencia + segmentos}\n")
            except Exception as err:
                variante = None
                f.write(f"{t:.2f} {getattr(err, 'code', 0)} -1\n")
            time.sleep(max(0.0, 0.5 - (time.time() - t)))


def parada(archivo, desde, hasta):
    """Mayor rato, en segundos, sin que la lista avanzara entre `desde` y `hasta`."""
    desde, hasta = float(desde), float(hasta)
    avances, ultima = [], None
    for linea in pathlib.Path(archivo).read_text().splitlines():
        campos = linea.split()
        if len(campos) != 3 or float(campos[0]) > hasta:
            continue
        if int(campos[2]) > 0 and int(campos[2]) != ultima:
            avances.append(float(campos[0]))
            ultima = int(campos[2])
    if not avances:
        print("inf")
        return
    peor = hasta - avances[-1]
    for a, b in zip(avances, avances[1:]):
        if b >= desde:
            peor = max(peor, b - a)
    print(f"{peor:.1f}")


# ---------------------------------------------------------------- la estación, antes y después
def _json(url, cookie=""):
    req = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
    with SIN_PROXY.open(req, timeout=5) as r:
        return json.loads(r.read())


def _inicio(pid):
    """Cuándo arrancó ese proceso (en tics desde el arranque del equipo): distingue un proceso de
    otro que haya heredado su número."""
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def foto(archivo):
    c = sqlite3.connect(BASE / "streamer.db", timeout=10)
    c.row_factory = sqlite3.Row
    salidas = {}
    for e in c.execute("SELECT * FROM estado_salidas"):
        salidas[e["salida"]] = {"pid": e["pid"], "inicio": _inicio(e["pid"]) if e["pid"] else None,
                                "reinicios": e["reinicios"], "firma": e["firma"], "kbps": e["kbps"], "error": e["error"]}
    eventos = [dict(r) for r in c.execute("SELECT t, flujo, salida, texto FROM eventos ORDER BY rowid")]
    servicios = {}
    for nombre in ("msxs-mediamtx", "msxs-supervisor", "msxs-web"):
        pid = _pid(nombre)
        try:
            caidas = int((_sd() / nombre / "caidas").read_text())
        except (OSError, ValueError):
            caidas = 0
        servicios[nombre] = {"pid": pid, "inicio": _inicio(pid) if pid else None, "caidas": caidas}
    paths = {p["name"]: {"lista": p.get("ready"), "desde": p.get("readyTime"),
                         "origen": (p.get("source") or {}).get("id"), "lectores": len(p.get("readers") or [])}
             for p in _json("http://127.0.0.1:9997/v3/paths/list?itemsPerPage=1000")["items"]}
    g = _json("http://127.0.0.1:9997/v3/config/global/get")
    mediamtx = {k: g.get(k) for k in ("rtmp", "webrtc", "hls", "srt", "hlsTrustedProxies", "webrtcTrustedProxies")}
    try:
        version = _json("http://127.0.0.1:8095/api/version")
        flujos = {f["id"]: {"estado": f["estado"], "salidas": {s["id"]: s["estado"] for s in f["salidas"]}}
                  for f in _json("http://127.0.0.1:8095/api/flujos", "msr_sesion=admin")}
    except Exception as err:
        version, flujos = f"el panel no responde: {err}", {}
    pathlib.Path(archivo).write_text(json.dumps(
        {"t": time.time(), "version": version, "salidas": salidas, "servicios": servicios, "paths": paths,
         "mediamtx": mediamtx, "flujos": flujos, "eventos": eventos}, indent=1, ensure_ascii=False))


def quejas(supervisor, panel):
    """Lo que el supervisor y el panel han escrito de malo desde la última vez que se les pidió
    arrancar. El supervisor solo escribe cuando algo le falla (una vuelta, el cálculo de las rutas,
    otro supervisor en marcha): cuenta cualquier línea. Del panel cuentan los errores."""
    def reciente(registro):
        try:
            return pathlib.Path(registro).read_text(errors="replace").rsplit(ARRANQUE + "\n", 1)[-1].splitlines()
        except OSError:
            return []
    malas = [f"supervisor: {ln}" for ln in reciente(supervisor) if ln.strip()]
    malas += [f"panel: {ln}" for ln in reciente(panel) if "error" in ln.lower() or "traceback" in ln.lower()]
    print("\n".join(malas))


def comparar(antes, despues, pueden_cambiar, solo_al_aire=False):
    """Lo que no debe cambiar en un relevo: los procesos de las salidas, MediaMTX y sus señales.
    Y, salvo con `solo_al_aire`, que el panel y el supervisor quedaron en marcha y dicen lo mismo."""
    a, d = json.loads(pathlib.Path(antes).read_text()), json.loads(pathlib.Path(despues).read_text())
    fallos = []
    for sid, e in a["salidas"].items():
        if sid in pueden_cambiar:
            continue
        n = d["salidas"].get(sid)
        if not n:
            fallos.append(f"la salida {sid} ya no está en la base")
        elif (n["pid"], n["inicio"]) != (e["pid"], e["inicio"]) or not n["inicio"]:
            fallos.append(f"la salida {sid} ya no es el mismo proceso ({e['pid']} → {n['pid']})")
        elif n["reinicios"] != e["reinicios"]:
            fallos.append(f"la salida {sid} cuenta {n['reinicios'] - e['reinicios']} caídas más")
    m, n = a["servicios"]["msxs-mediamtx"], d["servicios"]["msxs-mediamtx"]
    if (m["pid"], m["inicio"]) != (n["pid"], n["inicio"]) or not n["pid"]:
        fallos.append("MediaMTX se reinició")
    for nombre, p in a["paths"].items():
        q = d["paths"].get(nombre)
        if not q:
            fallos.append(f"MediaMTX ya no tiene la señal {nombre}")
        elif (q["lista"], q["desde"], q["origen"]) != (p["lista"], p["desde"], p["origen"]):
            fallos.append(f"la señal {nombre} se cortó y volvió a entrar (desde {p['desde']} → {q['desde']})")
    for nombre in () if solo_al_aire else ("msxs-supervisor", "msxs-web"):
        s = d["servicios"][nombre]
        if not s["pid"]:
            fallos.append(f"{nombre} no está en marcha")
        if s["caidas"]:                     # desde que se le pidió arrancar por última vez
            fallos.append(f"{nombre} se cayó al arrancar {s['caidas']} veces")
    for fid, f in ({} if solo_al_aire else a["flujos"]).items():
        for sid, estado in f["salidas"].items():
            ahora = (d["flujos"].get(fid) or {"salidas": {}})["salidas"].get(sid)
            if ahora != estado and sid not in pueden_cambiar:
                fallos.append(f"el panel da la salida {sid} como «{ahora}» y estaba «{estado}»")
    print("\n".join(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    que, resto = sys.argv[1], sys.argv[2:]
    if que == "systemctl":
        sys.exit(systemctl(resto))
    elif que == "vigilar":
        _vigilar(resto[0])
    elif que == "medidor":
        medidor(resto[0])
    elif que == "hls":
        hls(*resto)
    elif que == "foto":
        foto(resto[0])
    elif que == "comparar":
        sys.exit(comparar(resto[0], resto[1], set(resto[2:]) - {"--solo-al-aire"}, "--solo-al-aire" in resto))
    elif que == "quejas":
        quejas(*resto)
    elif que == "hueco":
        hueco(*resto)
    elif que == "parada":
        parada(*resto)
    else:
        sys.exit(f"no sé qué es {que}")
