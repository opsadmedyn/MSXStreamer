#!/usr/bin/env python3
"""Media Syntaxis Streamer - panel web y API.

El panel solo guarda en la base de datos lo que se quiere; el supervisor lo lleva a cabo.
Reiniciar el panel no corta ninguna entrada ni salida.
"""

import hashlib
import hmac
import json
import pathlib
import re
import secrets
import time
import unicodedata
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import mtx
from supervisor import path_de_flujo

ESTATICOS = pathlib.Path(__file__).parent / "static"
CFG = db.config()
HLS_PUERTO = int(CFG.get("hls_puerto", 8888))
try:
    VERSION = (pathlib.Path(__file__).parent.parent / "VERSION").read_text().strip()
except OSError:
    VERSION = "desarrollo"

app = FastAPI(title="Media Syntaxis Streamer")

# ---------------------------------------------------------------- acceso
# Clave única del panel en config.json ("clave_panel"). Sin clave, el panel queda abierto:
# solo es aceptable mientras escuche únicamente en la red local.
CLAVE = CFG.get("clave_panel", "")
SECRETO = hashlib.sha256(("msxs:" + CLAVE).encode()).digest()
PUBLICAS = ("/login", "/static/", "/api/login", "/api/version")


def _firma(valor):
    return hmac.new(SECRETO, valor.encode(), "sha256").hexdigest()[:32]


def _sesion_valida(cookie):
    try:
        caduca, firma = cookie.split(".")
        return int(caduca) > time.time() and hmac.compare_digest(firma, _firma(caduca))
    except (AttributeError, ValueError):
        return False


@app.middleware("http")
async def exigir_sesion(request: Request, call_next):
    if CLAVE and not request.url.path.startswith(PUBLICAS) \
            and not _sesion_valida(request.cookies.get("msxs")):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Sesión caducada"}, status_code=401)
        return RedirectResponse("/login")
    return await call_next(request)


class Login(BaseModel):
    clave: str


@app.post("/api/login")
def login(d: Login):
    if not CLAVE or not hmac.compare_digest(d.clave, CLAVE):
        time.sleep(1)
        raise HTTPException(401, "Clave incorrecta")
    caduca = str(int(time.time()) + 12 * 3600)
    r = JSONResponse({"ok": True})
    r.set_cookie("msxs", f"{caduca}.{_firma(caduca)}", max_age=12 * 3600, httponly=True, samesite="lax")
    return r


@app.post("/api/salir")
def salir():
    r = JSONResponse({"ok": True})
    r.delete_cookie("msxs")
    return r


# ---------------------------------------------------------------- modelos
URL_SRT = r"^srt://[^\s/?#]+(:\d+)?(\?[^\s]*)?$"


class Flujo(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    entrada: Literal["pull", "recorder"]
    url: str = ""
    streamid: str = Field("", max_length=512)
    passphrase: str = ""
    latencia_ms: int = Field(400, ge=20, le=8000)
    canal: str = ""


class Salida(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    tipo: Literal["srt", "rtmp", "hls"]
    url: str = ""
    modo: Literal["caller", "listener"] = "caller"
    streamid: str = Field("", max_length=512)
    passphrase: str = ""
    latencia_ms: int = Field(300, ge=20, le=8000)
    clave: str = ""


def _passphrase(p):
    if p and not 10 <= len(p) <= 79:
        raise HTTPException(422, "La passphrase SRT debe tener entre 10 y 79 caracteres")


def _validar_flujo(f: Flujo):
    if f.entrada == "pull":
        if not re.match(URL_SRT, f.url.strip()):
            raise HTTPException(422, "La URL del origen debe ser srt://host:puerto")
        _passphrase(f.passphrase)
    else:
        if f.canal not in {c["id"] for c in db.canales_recorder()}:
            raise HTTPException(422, "Ese canal no existe en MSX Recorder")


def _validar_salida(s: Salida):
    if s.tipo == "srt":
        if not re.match(URL_SRT, s.url.strip()):
            raise HTTPException(422, "La URL de destino debe ser srt://host:puerto")
        _passphrase(s.passphrase)
    elif s.tipo == "rtmp":
        if not re.match(r"^rtmps?://\S+$", s.url.strip()):
            raise HTTPException(422, "El servidor debe empezar por rtmp:// o rtmps://")


def _nuevo_id(c, tabla, nombre):
    base = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:24] or "flujo"
    while True:
        i = f"{base}-{secrets.token_hex(2)}"
        if not c.execute(f"SELECT 1 FROM {tabla} WHERE id=?", (i,)).fetchone():
            return i


# ---------------------------------------------------------------- estado en vivo
_muestras = {}      # path -> (instante, bytes recibidos) para calcular la tasa de entrada


def _kbps_entrada(path, info):
    if not info or not info.get("ready"):
        _muestras.pop(path, None)
        return None
    ahora, recibidos = time.time(), info.get("bytesReceived") or 0
    antes = _muestras.get(path)
    _muestras[path] = (ahora, recibidos) if not antes or ahora - antes[0] > 4 else antes
    if antes and ahora - antes[0] >= 1 and recibidos >= antes[1]:
        return round((recibidos - antes[1]) * 8 / 1000 / (ahora - antes[0]), 1)
    return None


def _pistas(info):
    return [t if isinstance(t, str) else t.get("codec", "") for t in (info or {}).get("tracks") or []]


def _host(request: Request):
    return CFG.get("host_publico") or request.url.hostname


# ---------------------------------------------------------------- HLS bajo demanda
# El HLS de un flujo solo se sirve con un destino HLS activo o con la vista previa abierta. Las
# reglas las aplica el supervisor en MediaMTX (ver mtx.asegurar_acceso), no este panel.
VISTA_SEGUNDOS = 3600


def _url_hls(request, path):
    return f"http://{_host(request)}:{HLS_PUERTO}/{path}/"


def _vista_flujo(f, salidas, estados, paths, request):
    path = path_de_flujo(f)
    info = (paths or {}).get(path)
    listo = bool(info and info.get("ready"))
    if not f["activo"]:
        estado = "detenido"
    elif paths is None:
        estado = "sin_mediamtx"
    else:
        estado = "en_el_aire" if listo else "esperando"
    vs = []
    for s in salidas:
        e = estados.get(s["id"], {})
        if not (f["activo"] and s["activo"]):
            est = "detenida"
        elif s["tipo"] == "hls":
            est = "publicando" if listo else "esperando"
        elif e.get("pid") and e.get("kbps"):
            est = "conectada"
        elif e.get("pid"):
            est = "conectando"
        else:
            est = "esperando" if e.get("error") == "Esperando la entrada" else "reintentando"
        v = {k: s[k] for k in ("id", "nombre", "tipo", "url", "modo", "streamid", "latencia_ms", "activo")}
        v.update(estado=est, kbps=e.get("kbps"), reinicios=e.get("reinicios", 0),
                 error=e.get("error", ""), tiene_clave=bool(s["clave"]),
                 tiene_passphrase=bool(s["passphrase"]))
        if s["tipo"] == "hls":
            v["url"] = _url_hls(request, path)
            v["url_m3u8"] = v["url"] + "index.m3u8"
        vs.append(v)
    return {
        **{k: f[k] for k in ("id", "nombre", "entrada", "url", "streamid", "latencia_ms", "canal", "activo")},
        "tiene_passphrase": bool(f["passphrase"]), "path": path, "estado": estado,
        "kbps": _kbps_entrada(path, info), "pistas": _pistas(info),
        "lectores": len((info or {}).get("readers") or []),
        "desde": (info or {}).get("readyTime"), "salidas": vs,
        "vista_previa": _url_hls(request, path),
    }


@app.get("/api/flujos")
def listar(request: Request):
    c = db.conectar()
    try:
        paths = mtx.paths_estado()
    except mtx.ErrorMTX:
        paths = None
    estados = {e["salida"]: e for e in db.filas(c, "SELECT * FROM estado_salidas")}
    flujos = db.filas(c, "SELECT * FROM flujos ORDER BY creado")
    salidas = db.filas(c, "SELECT * FROM salidas ORDER BY creado")
    return [_vista_flujo(f, [s for s in salidas if s["flujo"] == f["id"]], estados, paths, request)
            for f in flujos]


@app.get("/api/flujos/{fid}")
def ver(fid: str, request: Request):
    for f in listar(request):
        if f["id"] == fid:
            return f
    raise HTTPException(404, "Ese flujo no existe")


# ---------------------------------------------------------------- flujos
@app.post("/api/flujos")
def crear_flujo(d: Flujo):
    _validar_flujo(d)
    c = db.conectar()
    fid = _nuevo_id(c, "flujos", d.nombre)
    c.execute("""INSERT INTO flujos(id,nombre,entrada,url,streamid,passphrase,latencia_ms,canal)
                 VALUES (?,?,?,?,?,?,?,?)""",
              (fid, d.nombre.strip(), d.entrada, d.url.strip(), d.streamid.strip(), d.passphrase,
               d.latencia_ms, d.canal))
    db.evento(c, f"flujo creado: {d.nombre}", flujo=fid)
    return {"id": fid}


@app.put("/api/flujos/{fid}")
def editar_flujo(fid: str, d: Flujo):
    _validar_flujo(d)
    c = db.conectar()
    actual = c.execute("SELECT passphrase FROM flujos WHERE id=?", (fid,)).fetchone()
    if not actual:
        raise HTTPException(404, "Ese flujo no existe")
    # passphrase vacía = conservar la guardada (el panel nunca la recibe de vuelta)
    passphrase = d.passphrase or actual["passphrase"]
    c.execute("""UPDATE flujos SET nombre=?,entrada=?,url=?,streamid=?,passphrase=?,latencia_ms=?,canal=?
                 WHERE id=?""",
              (d.nombre.strip(), d.entrada, d.url.strip(), d.streamid.strip(), passphrase,
               d.latencia_ms, d.canal, fid))
    db.evento(c, "flujo editado", flujo=fid)
    return {"ok": True}


@app.post("/api/flujos/{fid}/salidas")
def crear_salida(fid: str, d: Salida):
    _validar_salida(d)
    c = db.conectar()
    if not c.execute("SELECT 1 FROM flujos WHERE id=?", (fid,)).fetchone():
        raise HTTPException(404, "Ese flujo no existe")
    sid = _nuevo_id(c, "salidas", d.nombre)
    c.execute("""INSERT INTO salidas(id,flujo,nombre,tipo,url,modo,streamid,passphrase,latencia_ms,clave)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (sid, fid, d.nombre.strip(), d.tipo, d.url.strip(), d.modo, d.streamid.strip(),
               d.passphrase, d.latencia_ms, d.clave.strip()))
    db.evento(c, f"salida creada: {d.nombre}", flujo=fid, salida=sid)
    return {"id": sid}


@app.post("/api/flujos/{fid}/vista-previa")
def vista_previa(fid: str, request: Request):
    """Abre el HLS del flujo durante una hora para verlo desde el navegador. Espera a que el
    supervisor lo aplique en MediaMTX para que el enlace funcione a la primera."""
    c = db.conectar()
    f = c.execute("SELECT * FROM flujos WHERE id=?", (fid,)).fetchone()
    if not f:
        raise HTTPException(404, "Ese flujo no existe")
    if not f["activo"]:
        raise HTTPException(409, "Inicia el flujo para ver la vista previa")
    hasta = time.time() + VISTA_SEGUNDOS
    c.execute("INSERT INTO vistas_previas(flujo, hasta) VALUES (?,?) "
              "ON CONFLICT(flujo) DO UPDATE SET hasta=excluded.hasta", (fid, hasta))
    path = path_de_flujo(dict(f))
    for _ in range(20):                         # el supervisor da una vuelta cada 2 s
        if path in (mtx.acceso_red() or set()):
            break
        time.sleep(0.3)
    return {"url": _url_hls(request, path), "hasta": hasta}


@app.post("/api/flujos/{fid}/{accion}")
def accion_flujo(fid: str, accion: Literal["iniciar", "detener"]):
    c = db.conectar()
    if not c.execute("UPDATE flujos SET activo=? WHERE id=?", (int(accion == "iniciar"), fid)).rowcount:
        raise HTTPException(404, "Ese flujo no existe")
    db.evento(c, f"flujo {'iniciado' if accion == 'iniciar' else 'detenido'}", flujo=fid)
    return {"ok": True}


@app.delete("/api/flujos/{fid}")
def borrar_flujo(fid: str):
    c = db.conectar()
    if not c.execute("DELETE FROM flujos WHERE id=?", (fid,)).rowcount:
        raise HTTPException(404, "Ese flujo no existe")
    db.evento(c, "flujo borrado", flujo=fid)
    return {"ok": True}


# ---------------------------------------------------------------- salidas
@app.put("/api/salidas/{sid}")
def editar_salida(sid: str, d: Salida):
    _validar_salida(d)
    c = db.conectar()
    actual = c.execute("SELECT passphrase, clave FROM salidas WHERE id=?", (sid,)).fetchone()
    if not actual:
        raise HTTPException(404, "Esa salida no existe")
    c.execute("""UPDATE salidas SET nombre=?,tipo=?,url=?,modo=?,streamid=?,passphrase=?,latencia_ms=?,clave=?
                 WHERE id=?""",
              (d.nombre.strip(), d.tipo, d.url.strip(), d.modo, d.streamid.strip(),
               d.passphrase or actual["passphrase"], d.latencia_ms, d.clave.strip() or actual["clave"], sid))
    db.evento(c, "salida editada", salida=sid)
    return {"ok": True}


@app.post("/api/salidas/{sid}/{accion}")
def accion_salida(sid: str, accion: Literal["iniciar", "detener"]):
    c = db.conectar()
    if not c.execute("UPDATE salidas SET activo=? WHERE id=?", (int(accion == "iniciar"), sid)).rowcount:
        raise HTTPException(404, "Esa salida no existe")
    db.evento(c, f"salida {'iniciada' if accion == 'iniciar' else 'detenida'}", salida=sid)
    return {"ok": True}


@app.delete("/api/salidas/{sid}")
def borrar_salida(sid: str):
    c = db.conectar()
    if not c.execute("DELETE FROM salidas WHERE id=?", (sid,)).rowcount:
        raise HTTPException(404, "Esa salida no existe")
    db.evento(c, "salida borrada", salida=sid)
    return {"ok": True}


# ---------------------------------------------------------------- recorder, sistema, eventos
@app.get("/api/recorder/canales")
def recorder_canales():
    try:
        paths = mtx.paths_estado()
    except mtx.ErrorMTX:
        paths = {}
    return [{**c, "publicando": bool((paths.get("rec_" + c["id"]) or {}).get("ready"))}
            for c in db.canales_recorder()]


@app.get("/api/sistema")
def sistema():
    c = db.conectar()
    visto = c.execute("SELECT max(visto) v FROM estado_salidas").fetchone()["v"]
    ultimo = c.execute("SELECT max(t) t FROM eventos WHERE texto='supervisor iniciado'").fetchone()["t"]
    try:
        mtx.paths_estado()
        mediamtx = True
    except mtx.ErrorMTX:
        mediamtx = False
    return {"version": VERSION, "mediamtx": mediamtx, "con_clave": bool(CLAVE),
            "supervisor_visto_s": round(time.time() - visto, 1) if visto else None,
            "supervisor_iniciado": ultimo}


@app.get("/api/eventos")
def eventos(flujo: Optional[str] = None, limite: int = 50):
    c = db.conectar()
    if flujo:
        return db.filas(c, """SELECT e.* FROM eventos e LEFT JOIN salidas s ON s.id=e.salida
                              WHERE e.flujo=? OR s.flujo=? ORDER BY t DESC LIMIT ?""", flujo, flujo, limite)
    return db.filas(c, "SELECT * FROM eventos ORDER BY t DESC LIMIT ?", min(limite, 500))


@app.get("/api/version")
def version():
    return VERSION


@app.get("/")
def inicio():
    return FileResponse(ESTATICOS / "index.html")


@app.get("/login")
def pagina_login():
    return FileResponse(ESTATICOS / "login.html")


app.mount("/static", StaticFiles(directory=str(ESTATICOS)), name="static")
