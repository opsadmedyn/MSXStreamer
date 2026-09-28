#!/usr/bin/env python3
"""Media Syntaxis Streamer - panel web y API.

El panel solo guarda en la base de datos lo que se quiere; el supervisor lo lleva a cabo.
Reiniciar el panel no corta ninguna entrada ni salida.
"""

import contextvars
import hashlib
import hmac
import json
import pathlib
import re
import secrets
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import composicion
import db
import mtx
from supervisor import path_de_flujo

ESTATICOS = pathlib.Path(__file__).parent / "static"
CFG = db.config()
HLS_PUERTO = int(CFG.get("hls_puerto", 8888))
COMP_MAX = composicion.maximo(CFG)
try:
    VERSION = (pathlib.Path(__file__).parent.parent / "VERSION").read_text().strip()
except OSError:
    VERSION = "desarrollo"

app = FastAPI(title="Media Syntaxis Streamer")

# ---------------------------------------------------------------- acceso
# Se entra con los usuarios del Recorder de este mismo equipo: el navegador trae la cookie de
# sesión del Recorder (mismo equipo; por HTTPS, mismo dominio en /streamer/) y se pregunta por
# ella a su /api/auth/yo, que dice email, rol y módulos (columna G de la hoja de usuarios).
# Hace falta el módulo "streamer". Admin: todo. Operador: ver, arrancar/parar destinos y vista
# previa. La clave del panel (config.json → clave_panel) queda como acceso de emergencia (admin).
CLAVE = CFG.get("clave_panel", "")
SECRETO = hashlib.sha256(("msxs:" + CLAVE).encode()).digest()
RECORDER_API = CFG.get("recorder_api", "http://127.0.0.1:8081")
COOKIE_RECORDER = "msr_sesion"
PUBLICAS = ("/login", "/static/", "/api/login", "/api/version")
USUARIO = contextvars.ContextVar("usuario", default=None)
_cache_sesiones = {}          # hash de la cookie -> (caduca, datos del Recorder o None)
OPERADOR_PUEDE = re.compile(r"^/api/(salidas/[^/]+/(iniciar|detener)|flujos/[^/]+/vista-previa|salir)$")


def _firma(valor):
    return hmac.new(SECRETO, valor.encode(), "sha256").hexdigest()[:32]


def _sesion_valida(cookie):
    try:
        caduca, firma = cookie.split(".")
        return int(caduca) > time.time() and hmac.compare_digest(firma, _firma(caduca))
    except (AttributeError, ValueError):
        return False


def _sesion_recorder(token):
    """Pregunta al Recorder por su sesión (con 10 s de caché: el panel refresca cada 2 s)."""
    clave = hashlib.sha256(token.encode()).hexdigest()
    ahora = time.time()
    guardado = _cache_sesiones.get(clave)
    if guardado and guardado[0] > ahora:
        return guardado[1]
    req = urllib.request.Request(RECORDER_API + "/api/auth/yo",
                                 headers={"Cookie": f"{COOKIE_RECORDER}={token}"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            datos = json.loads(r.read())
    except urllib.error.HTTPError:
        datos = None                                    # 401: sesión caducada o cerrada
    except (urllib.error.URLError, OSError, ValueError):
        return guardado[1] if guardado else None        # Recorder no responde: vale la última
    if len(_cache_sesiones) > 500:
        _cache_sesiones.clear()
    _cache_sesiones[clave] = (ahora + 10, datos)
    return datos


def _url_login(request: Request):
    """Login del Recorder de este equipo, con vuelta a esta página."""
    if request.url.scheme == "https":                   # tras Caddy: mismo dominio, /streamer/
        return "/login?volver=" + urllib.parse.quote("/streamer/")
    volver = f"http://{request.url.hostname}:{request.url.port or 80}/"
    return f"http://{request.url.hostname}:8081/login?volver=" + urllib.parse.quote(volver, safe="")


def _usuario(request: Request):
    """(usuario, motivo del rechazo). Usuario: {"email", "rol", "via"}."""
    token = request.cookies.get(COOKIE_RECORDER)
    if token:
        yo = _sesion_recorder(token)
        if yo:
            # Recorder antiguo (1.4.1: su respuesta no trae "modulos"): solo pasan los admins
            mods = yo.get("modulos")
            permitido = (yo.get("rol") == "admin") if mods is None else ("streamer" in mods)
            if not permitido:
                return None, "sin_modulo"
            return {"email": yo["email"], "rol": yo["rol"], "via": "recorder"}, None
    if CLAVE and _sesion_valida(request.cookies.get("msxs")):
        return {"email": "clave del panel", "rol": "admin", "via": "clave"}, None
    return None, "sin_sesion"


def _de_otro_sitio(request: Request):
    """Petición que cambia algo y que un navegador manda desde otra página (CSRF): su Origin no es
    este panel. Tras Caddy vale el Host o el X-Forwarded-Host (un navegador no puede poner este
    último en una petición a otro sitio sin permiso CORS, que el panel no da). Sin Origin se deja
    pasar: los navegadores actuales siempre lo mandan en un POST desde otro sitio."""
    origen = request.headers.get("origin")
    if request.method in ("GET", "HEAD", "OPTIONS") or origen is None:
        return False
    propios = {request.headers.get("host", "").lower(), request.headers.get("x-forwarded-host", "").lower()}
    try:
        netloc = urllib.parse.urlsplit(origen).netloc.lower()
    except ValueError:
        netloc = ""
    return not netloc or netloc not in propios          # Origin "null" (sin netloc): fuera


@app.middleware("http")
async def exigir_sesion(request: Request, call_next):
    ruta = request.url.path
    if _de_otro_sitio(request):
        h = request.headers
        print(f"petición rechazada (Origin ajeno): {request.method} {ruta} Origin={h.get('origin')!r} "
              f"Host={h.get('host')!r} X-Forwarded-Host={h.get('x-forwarded-host')!r}", flush=True)
        return JSONResponse({"detail": "Petición rechazada: no viene de este panel"}, status_code=403)
    if ruta.startswith(PUBLICAS):
        return await call_next(request)
    usuario, motivo = await run_in_threadpool(_usuario, request)
    if motivo == "sin_modulo":
        texto = "Tu usuario no tiene acceso al Streamer. Pide a un administrador que añada «streamer» en la columna Módulos de la hoja de usuarios."
        if ruta.startswith("/api/"):
            return JSONResponse({"detail": texto}, status_code=403)
        return HTMLResponse(f"<p style='font-family:sans-serif;padding:24px'>{texto}</p>", status_code=403)
    if not usuario:
        if ruta.startswith("/api/"):
            return JSONResponse({"detail": "Sesión caducada", "login": _url_login(request)}, status_code=401)
        return RedirectResponse(_url_login(request), status_code=303)
    if usuario["rol"] != "admin" and request.method != "GET" and not OPERADOR_PUEDE.match(ruta):
        return JSONResponse({"detail": "Solo un administrador puede hacer esto"}, status_code=403)
    request.state.usuario = usuario
    USUARIO.set(usuario)
    return await call_next(request)


def evento(c, texto, flujo=None, salida=None):
    """Como db.evento, pero anotando quién lo hizo desde el panel."""
    u = USUARIO.get()
    db.evento(c, f"{texto} · {u['email']}" if u else texto, flujo=flujo, salida=salida)


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
def salir(request: Request):
    """Cierra la sesión del panel. Con usuario del Recorder, la cierra también allí (es la misma)."""
    token = request.cookies.get(COOKIE_RECORDER)
    if token:
        try:
            urllib.request.urlopen(urllib.request.Request(
                RECORDER_API + "/api/auth/salir", method="POST", data=b"",
                headers={"Cookie": f"{COOKIE_RECORDER}={token}"}), timeout=3).close()
        except (urllib.error.URLError, OSError):
            pass
        _cache_sesiones.pop(hashlib.sha256(token.encode()).hexdigest(), None)
    r = JSONResponse({"ok": True, "login": _url_login(request)})
    r.delete_cookie("msxs")
    r.delete_cookie(COOKIE_RECORDER, path="/")
    return r


@app.get("/api/yo")
def yo(request: Request):
    u = request.state.usuario
    recorder = "/" if request.url.scheme == "https" else f"http://{request.url.hostname}:8081/"
    return {**u, "recorder_url": recorder}


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
    fuente: Literal["limpia", "compuesta"] = "limpia"


class Capa(BaseModel):
    url: str = Field("", max_length=2000)
    activa: bool = True
    encima: bool = True


class Composicion(BaseModel):
    activa: bool = False
    x: int = Field(0, ge=0, le=1920)
    y: int = Field(0, ge=0, le=1080)
    ancho: int = Field(1920, ge=320, le=1920)
    fondo: str = "#000000"
    capas: list[Capa] = Field(default_factory=list, max_length=2)
    kbps: int = Field(8000, ge=1000, le=40000)


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


# ---------------------------------------------------------------- credenciales a la vista
# Un operador no ve el streamid, lo que va en las URL tras "?" o antes de "@", ni la clave RTMP
# pegada al final de la URL (decisión 3 de la propuesta). A nadie se le enseña, en un error o en
# el registro, la línea con la que el supervisor arranca ffmpeg (lleva todas las claves) ni un
# valor guardado de los anteriores. Solo cambia lo que se devuelve: la base y el supervisor, no.
OCULTO = "***"
LINEA_ARRANQUE = re.compile(r"== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d ")     # la que escribe el supervisor al lanzar
OPCIONES_SECRETAS = (" -srt_streamid ", " -passphrase ")
PARAMS_NO_SECRETOS = {"mode", "latency", "rcvlatency", "peerlatency", "pkt_size", "payload_size", "transtype"}


def _clave_en_ruta(ruta):
    """Último tramo no vacío de la ruta de una URL RTMP: la clave, si no va aparte."""
    return ruta.rstrip("/").rpartition("/")[2]


def _url_visible(url, tipo, clave):
    """URL sin usuario ni contraseña, sin parámetros y, en RTMP sin clave aparte, sin su último tramo."""
    if not url:
        return url
    try:
        p = urllib.parse.urlsplit(url, allow_fragments=False)
    except ValueError:
        return OCULTO
    ruta = p.path
    if tipo == "rtmp" and not clave and _clave_en_ruta(ruta):
        sin_barra = ruta.rstrip("/")
        ruta = sin_barra.rpartition("/")[0] + "/" + OCULTO + ruta[len(sin_barra):]
    return urllib.parse.urlunsplit((p.scheme, p.netloc.rpartition("@")[2], ruta, OCULTO if p.query else "", ""))


def _secretos(flujos, salidas):
    """Valores guardados que no deben aparecer en un texto, también codificados como van en una
    URL (mtx.conf_de_flujo los codifica, y un error de MediaMTX puede repetir esa URL)."""
    valores = set()
    for fila in [*flujos, *salidas]:
        valores.update(fila.get(k) or "" for k in ("streamid", "passphrase", "clave"))
        url = fila.get("url") or ""
        try:
            partes = urllib.parse.urlsplit(url, allow_fragments=False)
            valores.update(v for k, v in urllib.parse.parse_qsl(partes.query) if k not in PARAMS_NO_SECRETOS)
            valores.add(partes.password or "")
            ruta = partes.path
        except ValueError:                  # URL que no se puede analizar: se usa tal cual
            ruta = url.split("?")[0]
        if fila.get("tipo") == "rtmp" and not fila.get("clave"):
            valores.add(_clave_en_ruta(ruta))
    valores |= {urllib.parse.quote_plus(v) for v in valores}
    return sorted((v for v in valores if v), key=len, reverse=True)


def _limpiar(texto, secretos):
    """Error o texto del registro sin la línea de arranque de ffmpeg ni valores secretos."""
    if not texto:
        return texto
    m = LINEA_ARRANQUE.search(texto)
    if m:
        texto = texto[:m.start()] + "(línea de arranque oculta)"
    elif any(o in texto for o in OPCIONES_SECRETAS):
        return "(línea de arranque oculta)"
    for s in secretos:
        texto = texto.replace(s, OCULTO)
    return texto


# ---------------------------------------------------------------- HLS bajo demanda
# El HLS de un flujo solo se sirve con un destino HLS activo o con la vista previa abierta. Las
# reglas las aplica el supervisor en MediaMTX (ver mtx.asegurar_acceso), no este panel.
VISTA_SEGUNDOS = 3600


def _url_hls(request, path):
    return f"http://{_host(request)}:{HLS_PUERTO}/{path}/"


def _vista_flujo(f, salidas, estados, paths, request, secretos):
    ve_claves = (getattr(request.state, "usuario", None) or {}).get("rol") == "admin"
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
            if s["fuente"] == "compuesta":
                ci = (paths or {}).get(composicion.path_comp(f["id"]))
                est = "publicando" if ci and ci.get("ready") else "esperando"
            else:
                est = "publicando" if listo else "esperando"
        elif e.get("pid") and e.get("kbps"):
            est = "conectada"
        elif e.get("pid"):
            est = "conectando"
        else:
            est = "esperando" if (e.get("error") or "").startswith(("Esperando", "La composición")) else "reintentando"
        v = {k: s[k] for k in ("id", "nombre", "tipo", "url", "modo", "streamid", "latencia_ms", "activo", "fuente")}
        v.update(estado=est, kbps=e.get("kbps"), reinicios=e.get("reinicios", 0),
                 error=_limpiar(e.get("error", ""), secretos), tiene_clave=bool(s["clave"]),
                 tiene_passphrase=bool(s["passphrase"]))
        if not ve_claves:       # "streamid" se queda (vacío): un app.js en caché lo sigue leyendo
            v.update(streamid="", tiene_streamid=bool(s["streamid"]), url=_url_visible(s["url"], s["tipo"], s["clave"]))
        if s["tipo"] == "hls":
            v["url"] = _url_hls(request, composicion.path_comp(f["id"]) if s["fuente"] == "compuesta" else path)
            v["url_m3u8"] = v["url"] + "index.m3u8"
        vs.append(v)
    vf = {
        **{k: f[k] for k in ("id", "nombre", "entrada", "url", "streamid", "latencia_ms", "canal", "activo")},
        "tiene_passphrase": bool(f["passphrase"]), "path": path, "estado": estado,
        "kbps": _kbps_entrada(path, info), "pistas": _pistas(info),
        "lectores": len((info or {}).get("readers") or []),
        "desde": (info or {}).get("readyTime"), "salidas": vs,
        "vista_previa": _url_hls(request, path),
        "composicion": _vista_composicion(f, estados, paths, request, secretos),
    }
    if not ve_claves:
        vf.update(streamid="", tiene_streamid=bool(f["streamid"]), url=_url_visible(f["url"], "srt", ""))
    return vf


def _vista_composicion(f, estados, paths, request, secretos):
    c = db.conectar()
    k = composicion.leer(c, f["id"])
    e = estados.get(composicion.id_tarea(f["id"]), {})
    info = (paths or {}).get(composicion.path_comp(f["id"]))
    if not (k["activa"] and f["activo"]):
        estado = "desactivada"
    elif info and info.get("ready"):
        estado = "componiendo"
    elif e.get("pid"):
        estado = "arrancando"
    elif (e.get("error") or "").startswith("Esperando"):
        estado = "esperando"
    else:
        estado = "reintentando"
    x, y, w, h = composicion.normalizar(k)
    try:
        capas = json.loads(k["capas"] or "[]")
    except ValueError:
        capas = []
    return {"activa": bool(k["activa"]), "x": x, "y": y, "ancho": w, "alto": h, "fondo": k["fondo"],
            "capas": capas, "kbps": k["kbps"], "estado": estado,
            "fps": composicion.fps_de(db.BASE / "run" / f"{composicion.id_tarea(f['id'])}.prog") if estado == "componiendo" else None,
            "kbps_salida": e.get("kbps"), "reinicios": e.get("reinicios", 0),
            "error": _limpiar(e.get("error", ""), secretos) if estado == "reintentando" else "",
            "path": composicion.path_comp(f["id"]),
            "vista_previa": _url_hls(request, composicion.path_comp(f["id"])),
            "preajustes": composicion.PREAJUSTES}


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
    secretos = _secretos(flujos, salidas)
    return [_vista_flujo(f, [s for s in salidas if s["flujo"] == f["id"]], estados, paths, request, secretos)
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
    evento(c, f"flujo creado: {d.nombre}", flujo=fid)
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
    evento(c, "flujo editado", flujo=fid)
    return {"ok": True}


@app.post("/api/flujos/{fid}/salidas")
def crear_salida(fid: str, d: Salida):
    _validar_salida(d)
    c = db.conectar()
    if not c.execute("SELECT 1 FROM flujos WHERE id=?", (fid,)).fetchone():
        raise HTTPException(404, "Ese flujo no existe")
    sid = _nuevo_id(c, "salidas", d.nombre)
    c.execute("""INSERT INTO salidas(id,flujo,nombre,tipo,url,modo,streamid,passphrase,latencia_ms,clave,fuente)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (sid, fid, d.nombre.strip(), d.tipo, d.url.strip(), d.modo, d.streamid.strip(),
               d.passphrase, d.latencia_ms, d.clave.strip(), d.fuente))
    evento(c, f"salida creada: {d.nombre}", flujo=fid, salida=sid)
    return {"id": sid}


@app.post("/api/flujos/{fid}/vista-previa")
def vista_previa(fid: str, request: Request, fuente: Literal["limpia", "compuesta"] = "limpia"):
    """Abre el HLS del flujo durante una hora para verlo desde el navegador. Espera a que el
    supervisor lo aplique en MediaMTX para que el enlace funcione a la primera."""
    c = db.conectar()
    f = c.execute("SELECT * FROM flujos WHERE id=?", (fid,)).fetchone()
    if not f:
        raise HTTPException(404, "Ese flujo no existe")
    if not f["activo"]:
        raise HTTPException(409, "Inicia el flujo para ver la vista previa")
    path = path_de_flujo(dict(f))
    if fuente == "compuesta":
        if not composicion.leer(c, fid)["activa"]:
            raise HTTPException(409, "Activa la composición para verla")
        path = composicion.path_comp(fid)
    # se abre (limpia y compuesta) solo cuando ya no hay motivo para rechazarla
    hasta = time.time() + VISTA_SEGUNDOS
    c.execute("INSERT INTO vistas_previas(flujo, hasta) VALUES (?,?) "
              "ON CONFLICT(flujo) DO UPDATE SET hasta=excluded.hasta", (fid, hasta))
    for _ in range(20):                         # el supervisor da una vuelta cada 2 s
        if path in (mtx.acceso_red() or set()):
            break
        time.sleep(0.3)
    return {"url": _url_hls(request, path), "hasta": hasta}


@app.put("/api/flujos/{fid}/composicion")
def guardar_composicion(fid: str, d: Composicion):
    c = db.conectar()
    if not c.execute("SELECT 1 FROM flujos WHERE id=?", (fid,)).fetchone():
        raise HTTPException(404, "Ese flujo no existe")
    if not composicion.validar_fondo(d.fondo):
        raise HTTPException(422, "El color de fondo debe ser #RRGGBB")
    for i, capa in enumerate(d.capas, start=1):
        if capa.url.strip() and not composicion.validar_url_capa(capa.url.strip()):
            raise HTTPException(422, f"La URL de la capa {i} debe empezar por http:// o https://")
    if d.activa:        # tope de composiciones a la vez; una que ya estaba activa se puede editar siempre
        antes = c.execute("SELECT activa FROM composiciones WHERE flujo=?", (fid,)).fetchone()
        otras = c.execute("""SELECT COUNT(*) FROM composiciones k JOIN flujos f ON f.id = k.flujo
                             WHERE k.activa = 1 AND f.activo = 1 AND k.flujo <> ?""", (fid,)).fetchone()[0]
        if not (antes and antes["activa"]) and otras >= COMP_MAX:
            raise HTTPException(409, f"Ya hay {otras} composiciones activas y el máximo es {COMP_MAX} a la vez. "
                                     "Desactiva otra antes de activar esta.")
    x, y, w, _ = composicion.normalizar(d.model_dump())
    capas = json.dumps([{"url": k.url.strip(), "activa": k.activa, "encima": k.encima} for k in d.capas])
    c.execute("""INSERT INTO composiciones(flujo,activa,x,y,ancho,fondo,capas,kbps) VALUES (?,?,?,?,?,?,?,?)
                 ON CONFLICT(flujo) DO UPDATE SET activa=excluded.activa,x=excluded.x,y=excluded.y,
                 ancho=excluded.ancho,fondo=excluded.fondo,capas=excluded.capas,kbps=excluded.kbps""",
              (fid, int(d.activa), x, y, w, d.fondo.lower(), capas, d.kbps))
    evento(c, "composición " + ("activada" if d.activa else "guardada (desactivada)"), flujo=fid)
    return {"ok": True}


@app.post("/api/flujos/{fid}/{accion}")
def accion_flujo(fid: str, accion: Literal["iniciar", "detener"]):
    c = db.conectar()
    if not c.execute("UPDATE flujos SET activo=? WHERE id=?", (int(accion == "iniciar"), fid)).rowcount:
        raise HTTPException(404, "Ese flujo no existe")
    evento(c, f"flujo {'iniciado' if accion == 'iniciar' else 'detenido'}", flujo=fid)
    return {"ok": True}


@app.delete("/api/flujos/{fid}")
def borrar_flujo(fid: str):
    c = db.conectar()
    if not c.execute("DELETE FROM flujos WHERE id=?", (fid,)).rowcount:
        raise HTTPException(404, "Ese flujo no existe")
    evento(c, "flujo borrado", flujo=fid)
    return {"ok": True}


# ---------------------------------------------------------------- salidas
@app.put("/api/salidas/{sid}")
def editar_salida(sid: str, d: Salida):
    _validar_salida(d)
    c = db.conectar()
    actual = c.execute("SELECT passphrase, clave FROM salidas WHERE id=?", (sid,)).fetchone()
    if not actual:
        raise HTTPException(404, "Esa salida no existe")
    c.execute("""UPDATE salidas SET nombre=?,tipo=?,url=?,modo=?,streamid=?,passphrase=?,latencia_ms=?,clave=?,fuente=?
                 WHERE id=?""",
              (d.nombre.strip(), d.tipo, d.url.strip(), d.modo, d.streamid.strip(),
               d.passphrase or actual["passphrase"], d.latencia_ms, d.clave.strip() or actual["clave"],
               d.fuente, sid))
    evento(c, "salida editada", salida=sid)
    return {"ok": True}


@app.post("/api/salidas/{sid}/{accion}")
def accion_salida(sid: str, accion: Literal["iniciar", "detener"]):
    c = db.conectar()
    if not c.execute("UPDATE salidas SET activo=? WHERE id=?", (int(accion == "iniciar"), sid)).rowcount:
        raise HTTPException(404, "Esa salida no existe")
    evento(c, f"salida {'iniciada' if accion == 'iniciar' else 'detenida'}", salida=sid)
    return {"ok": True}


@app.delete("/api/salidas/{sid}")
def borrar_salida(sid: str):
    c = db.conectar()
    if not c.execute("DELETE FROM salidas WHERE id=?", (sid,)).rowcount:
        raise HTTPException(404, "Esa salida no existe")
    evento(c, "salida borrada", salida=sid)
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
        filas = db.filas(c, """SELECT e.* FROM eventos e LEFT JOIN salidas s ON s.id=e.salida
                               WHERE e.flujo=? OR s.flujo=? ORDER BY t DESC LIMIT ?""", flujo, flujo, limite)
    else:
        filas = db.filas(c, "SELECT * FROM eventos ORDER BY t DESC LIMIT ?", min(limite, 500))
    secretos = _secretos(db.filas(c, "SELECT url, streamid, passphrase FROM flujos"),
                         db.filas(c, "SELECT tipo, url, streamid, passphrase, clave FROM salidas"))
    for e in filas:
        e["texto"] = _limpiar(e["texto"], secretos)
    return filas


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
