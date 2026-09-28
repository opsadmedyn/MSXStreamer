#!/usr/bin/env python3
"""Media Syntaxis Streamer - panel web y API.

El panel solo guarda en la base de datos lo que se quiere; el supervisor lo lleva a cabo.
Reiniciar el panel no corta ninguna entrada ni salida.
"""

import contextvars
import hashlib
import json
import logging
import os
import pathlib
import re
import secrets
import signal
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal, Optional

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
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
# Se entra solo con los usuarios del Recorder de este mismo equipo, y solo por Tailscale: el panel
# escucha en 127.0.0.1 y lo publica Caddy en https://<dominio del Recorder>/streamer/. El
# navegador trae la cookie de sesión del Recorder (mismo dominio) y se pregunta por ella a su
# /api/auth/yo, que dice email, rol y módulos (columna G de la hoja de usuarios). Hace falta el
# módulo "streamer". Admin: todo. Operador: ver, arrancar/parar destinos y vista previa.
RECORDER_API = CFG.get("recorder_api", "http://127.0.0.1:8081")
COOKIE_RECORDER = "msr_sesion"
PUBLICAS = ("/static/", "/api/version")
USUARIO = contextvars.ContextVar("usuario", default=None)
_cache_sesiones = {}          # hash de la cookie -> (caduca, datos del Recorder o None)
OPERADOR_PUEDE = re.compile(r"^/api/(salidas/[^/]+/(iniciar|detener)|flujos/[^/]+/vista-previa|salir)$")


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
    except urllib.error.HTTPError as e:
        if e.code >= 500:                               # 503: Recorder recién reiniciado, sin hoja
            return guardado[1] if guardado else None    # no se puede comprobar: vale la última
        datos = None                                    # 401: sesión caducada o cerrada
    except (urllib.error.URLError, OSError, ValueError):
        return guardado[1] if guardado else None        # Recorder no responde: vale la última
    if len(_cache_sesiones) > 500:
        _cache_sesiones.clear()
    _cache_sesiones[clave] = (ahora + 10, datos)
    return datos


def _url_login(request: Request):
    """Login del Recorder (mismo dominio, tras Caddy), con vuelta al Streamer."""
    return "/login?volver=" + urllib.parse.quote("/streamer/")


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
    if ruta.startswith("/hls/"):        # vista previa: muchas peticiones, con sus propios hilos (ver abajo)
        usuario, motivo = await anyio.to_thread.run_sync(_usuario, request, limiter=HILOS_VISTA)
    else:
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
    r.delete_cookie(COOKIE_RECORDER, path="/")
    return r


@app.get("/api/yo")
def yo(request: Request):
    u = request.state.usuario
    return {**u, "recorder_url": "/"}


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
URL_EN_TEXTO = re.compile(r"\b(srt|rtmps?)://[^\s\"'<>]+")
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


def _url_en_texto(m):
    url = m.group(0).rstrip(":,.;)]")                   # "rtmp://…/clave: Connection refused"
    return _url_visible(url, "srt" if m.group(1) == "srt" else "rtmp", "") + m.group(0)[len(url):]


def _limpiar(texto, secretos):
    """Error o texto del registro sin la línea de arranque de ffmpeg ni valores secretos. Además,
    toda URL SRT o RTMP sale como la ve un operador: así tampoco aparecen las claves de un destino
    o flujo ya borrado o editado, que ya no están en la base."""
    if not texto:
        return texto
    m = LINEA_ARRANQUE.search(texto)
    if m:
        texto = texto[:m.start()] + "(línea de arranque oculta)"
    elif any(o in texto for o in OPCIONES_SECRETAS):
        return "(línea de arranque oculta)"
    for s in secretos:
        texto = texto.replace(s, OCULTO)
    return URL_EN_TEXTO.sub(_url_en_texto, texto)


# ---------------------------------------------------------------- HLS bajo demanda
# El HLS de un flujo solo se sirve a la red con un destino HLS activo. Las reglas las aplica el
# supervisor en MediaMTX (ver mtx.asegurar_acceso), no este panel. La vista previa del propio panel
# ni las necesita ni las abre: va por el panel, con la sesión (ver _RespuestaHLS).


def _url_hls(request, path):
    """Dirección pública del HLS (la de los destinos HLS, para reproductores externos)."""
    return f"http://{_host(request)}:{HLS_PUERTO}/{path}/"


def _url_vista(path):
    """Vista previa a través del panel. Relativa: vale tras Caddy (/streamer/hls/…) y por un túnel
    SSH al 8095 (/hls/…)."""
    return f"hls/{urllib.parse.quote(path)}/"


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
            path_s = composicion.path_comp(f["id"]) if s["fuente"] == "compuesta" else path
            v["url"] = _url_hls(request, path_s)
            v["url_m3u8"] = v["url"] + "index.m3u8"
            v["ver"] = _url_vista(path_s)           # el enlace "ver" del panel va por el panel
        vs.append(v)
    vf = {
        **{k: f[k] for k in ("id", "nombre", "entrada", "url", "streamid", "latencia_ms", "canal", "activo")},
        "tiene_passphrase": bool(f["passphrase"]), "path": path, "estado": estado,
        "kbps": _kbps_entrada(path, info), "pistas": _pistas(info),
        "lectores": len((info or {}).get("readers") or []),
        "desde": (info or {}).get("readyTime"), "salidas": vs,
        "vista_previa": _url_vista(path),
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
            "vista_previa": _url_vista(composicion.path_comp(f["id"])),
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
def vista_previa(fid: str, fuente: Literal["limpia", "compuesta"] = "limpia"):
    """Enlace de la vista previa, que va por el panel (hls/<path>/, con la sesión), o por qué no se
    puede ver. Ya no abre el HLS del flujo a la red en el 8888 (hasta ahora, una hora y sin sesión
    para quien llegara a ese puerto): eso queda solo para los destinos HLS."""
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
    return {"url": _url_vista(path)}


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


# ---------------------------------------------------------------- vista previa por el panel
# El 8888 de MediaMTX no llega al navegador por Tailscale, así que la vista previa pasa por el
# propio panel: hls/<path>/ (tras Caddy, /streamer/hls/<path>/), con la misma sesión que el resto.
# El panel se la pide a MediaMTX desde 127.0.0.1, que lo puede leer todo (usuario LOCAL): la puerta
# es el panel. Solo paths de flujos que existen o de su composición, con caracteres permitidos, y
# siempre a 127.0.0.1:<hls_puerto>. Solo <path>, <path>/ y <path>/<archivo>: MediaMTX toma como
# nombre del path todo lo que va antes del último tramo, así que no puede haber más tramos.
# No caduca: dura mientras la pestaña esté abierta y la sesión valga (el tope es VISTA_MAX).
# Va en asíncrono, sin hilos: las peticiones que MediaMTX retiene (HLS de baja latencia, hasta
# ~20 s) no ocupan los hilos del resto del panel. Como mucho VISTA_MAX a la vez; las demás reciben
# un 503 al momento y el reproductor reintenta.
VISTA_MAX = 24
VISTA_CONEXION, VISTA_LECTURA, VISTA_TOTAL = 3, 20, 60      # segundos
TRAMO = r"[A-Za-z0-9_~-][A-Za-z0-9_.~-]{0,99}"                # sin "." delante: ni "." ni ".."
RESTO_HLS = re.compile(rf"^{TRAMO}(/({TRAMO})?)?\Z")         # \Z y no $: sin salto de línea final
CLAVE_HLS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}\Z")     # _HLS_msn, _HLS_part, session…
VALOR_HLS = re.compile(r"^[A-Za-z0-9_.-]{0,64}\Z")
UBICACION_HLS = re.compile(r"^/[A-Za-z0-9_.~/-]*(\?[A-Za-z0-9_.=&-]*)?\Z")
CABECERAS_HLS = {"content-type", "cache-control", "content-length"}
VISTAS = anyio.CapacityLimiter(VISTA_MAX)       # peticiones de vista previa en curso
HILOS_VISTA = anyio.CapacityLimiter(4)          # sus consultas de sesión y base: no las del resto
_paths_vista = (0.0, frozenset())


class _SinVistaEnRegistro(logging.Filter):
    """El reproductor pide varias veces por segundo, y el lienzo una foto cada 5 s: si van bien (o
    con el 503 del tope, que el reproductor reintenta), esas líneas no van al registro de accesos
    (journald), que si no se llenaría con cada trozo de vídeo mientras alguien mira. Los rechazos
    (303, 403, 404…) y los fallos de MediaMTX sí se anotan."""

    def filter(self, registro):
        args = registro.args            # (cliente, método, ruta, versión HTTP, código)
        if not (isinstance(args, tuple) and len(args) > 4):
            return True
        ruta = str(args[2])
        if ruta.startswith("/hls/"):
            return args[4] not in (200, 206, 302, 304, 503)
        return not (ruta.startswith("/api/flujos/") and ruta.endswith("/foto.jpg") and args[4] in (200, 204))


logging.getLogger("uvicorn.access").addFilter(_SinVistaEnRegistro())


def _paths_con_vista():
    """Paths que se pueden ver por el panel: el de cada flujo y el de su composición (2 s de caché)."""
    global _paths_vista
    ahora = time.monotonic()
    if ahora - _paths_vista[0] > 2:
        validos = set()
        for f in db.filas(db.conectar(), "SELECT id, entrada, canal FROM flujos"):
            validos |= {path_de_flujo(f), composicion.path_comp(f["id"])}
        _paths_vista = (ahora, frozenset(validos))
    return _paths_vista[1]


def _consulta_hls(consulta):
    """Parámetros para MediaMTX (los del HLS de baja latencia y la sesión): solo los inocuos."""
    pares = urllib.parse.parse_qsl(consulta, keep_blank_values=True)[:10]
    return urllib.parse.urlencode([(k, v) for k, v in pares if CLAVE_HLS.match(k) and VALOR_HLS.match(v)])


class _RespuestaHLS(Response):
    """Respuesta de MediaMTX pasada al navegador a trozos, según llega. Si el navegador se va, la
    petición a MediaMTX se corta en el acto."""

    def __init__(self, resto, consulta):
        self.resto, self.consulta, self.arriba, self.empezada = resto, consulta, None, False
        self.status_code, self.background, self.raw_headers = 200, None, []

    async def _texto(self, send, codigo, texto):
        cuerpo = texto.encode()
        await send({"type": "http.response.start", "status": codigo, "headers": [
            (b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store"),
            (b"content-length", str(len(cuerpo)).encode())]})
        self.empezada = True
        await send({"type": "http.response.body", "body": cuerpo})

    async def __call__(self, scope, receive, send):
        try:
            VISTAS.acquire_on_behalf_of_nowait(self)
        except anyio.WouldBlock:
            return await self._texto(send, 503, "Demasiadas peticiones de vista previa a la vez")
        try:
            async with anyio.create_task_group() as tg:
                async def vigilar():
                    while (await receive())["type"] != "http.disconnect":
                        pass
                    tg.cancel_scope.cancel()            # el navegador se fue
                tg.start_soon(vigilar)
                await self._pasar(send)
                tg.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                if self.arriba is not None:
                    await self.arriba.aclose()
                if not self.empezada:       # se fue antes de la respuesta: nadie la verá, pero sin
                    with anyio.move_on_after(2):     # ella el middleware anota "No response returned"
                        await self._texto(send, 502, "Vista previa cortada")
            VISTAS.release_on_behalf_of(self)

    def _cabeceras(self, cabecera):
        """(estado, cabeceras que pasan, Content-Length o None) de la respuesta de MediaMTX."""
        lineas = cabecera.split("\r\n")
        m = re.match(r"HTTP/1\.[01] ([1-5]\d\d) ", lineas[0] + " ")
        if not m:
            raise ValueError("respuesta no HTTP")
        cabeceras, largo = [(b"x-content-type-options", b"nosniff")], None
        for linea in lineas[1:]:
            nombre, _, valor = linea.partition(":")
            nombre, valor = nombre.strip().lower(), valor.strip()
            if not re.fullmatch(r"[\x20-\x7e]*", valor):
                continue
            if nombre in CABECERAS_HLS:
                if nombre == "content-length":
                    largo = int(valor)
                cabeceras.append((nombre.encode(), valor.encode()))
            elif nombre == "location" and UBICACION_HLS.match(valor):
                # MediaMTX redirige a /<path>/…: relativa a esta petición, vale bajo cualquier prefijo
                cabeceras.append((b"location", ("../" * self.resto.count("/") + valor[1:]).encode()))
        return int(m.group(1)), cabeceras, largo

    async def _pasar(self, send):
        limite = time.monotonic() + VISTA_TOTAL

        def queda(tope=VISTA_LECTURA):              # plazo de la próxima espera, dentro del total
            return max(0.0, min(tope, limite - time.monotonic()))
        consulta = "?" + self.consulta if self.consulta else ""
        try:
            # blindada: si el navegador se va justo cuando conecta, anyio.connect_tcp pierde el
            # socket ya abierto (queda sin cerrar hasta que pasa el recolector). Así termina de
            # conectar, el corte llega en el envío y el finally cierra self.arriba.
            with anyio.CancelScope(shield=True), anyio.fail_after(VISTA_CONEXION):
                self.arriba = await anyio.connect_tcp("127.0.0.1", HLS_PUERTO)
            # HTTP/1.0: MediaMTX responde sin trozos y cierra al acabar (o con Content-Length)
            await self.arriba.send(f"GET /{urllib.parse.quote(self.resto)}{consulta} HTTP/1.0\r\n"
                                   f"Host: 127.0.0.1:{HLS_PUERTO}\r\nUser-Agent: msxs-panel\r\n\r\n".encode())
            lector = BufferedByteReceiveStream(self.arriba)
            with anyio.fail_after(queda()):
                cabecera = (await lector.receive_until(b"\r\n\r\n", 16384)).decode("latin-1")
            estado, cabeceras, pendiente = self._cabeceras(cabecera)
        except TimeoutError:
            return await self._texto(send, 504, "MediaMTX no respondió a tiempo")
        except (OSError, ValueError, anyio.BrokenResourceError, anyio.EndOfStream,
                anyio.IncompleteRead, anyio.DelimiterNotFound):
            return await self._texto(send, 502, "MediaMTX no responde")
        await send({"type": "http.response.start", "status": estado, "headers": cabeceras})
        self.empezada = True        # solo si ha salido: si el corte llega antes, el finally manda el 502
        try:
            while pendiente is None or pendiente > 0:
                with anyio.fail_after(queda()):
                    trozo = await lector.receive(65536 if pendiente is None else min(65536, pendiente))
                if pendiente is not None:
                    pendiente -= len(trozo)
                with anyio.fail_after(queda(VISTA_TOTAL)):
                    await send({"type": "http.response.body", "body": trozo, "more_body": True})
        except anyio.EndOfStream:
            if pendiente:
                return                                  # cortada a medias: no se da por buena
        except (TimeoutError, OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
            return
        await send({"type": "http.response.body", "body": b"", "more_body": False})


@app.get("/hls/{resto:path}")
async def vista_hls(resto: str, request: Request):
    """Vista previa por el panel: la página de MediaMTX, sus listas y sus segmentos."""
    if not RESTO_HLS.match(resto):
        raise HTTPException(404, "No existe")
    if resto.split("/", 1)[0] not in await anyio.to_thread.run_sync(_paths_con_vista, limiter=HILOS_VISTA):
        raise HTTPException(404, "Ese flujo no existe")
    return _RespuestaHLS(resto, _consulta_hls(request.url.query))


# ---------------------------------------------------------------- foto de la entrada (lienzo)
# El lienzo de la composición muestra una foto de la entrada limpia, que el panel renueva cada 5 s
# mientras se ve. La saca el ffmpeg de la máquina: un fotograma a 640 px, desentrelazado si llega
# entrelazado (canal1 es 1080i), con prioridad mínima y como mucho FOTO_LIMITE s (después se mata).
# Cada flujo guarda la última FOTO_CACHE s; quien la pide mientras se saca espera a esa misma. Como
# mucho FOTO_MAX a la vez, con sus propios hilos; si no, 503 al momento.
FFMPEG = CFG.get("ffmpeg", "/usr/local/bin/ffmpeg")
FOTO_CACHE, FOTO_LIMITE, FOTO_MAX = 4.0, 8.0, 2
FOTO_FILTRO = "yadif=mode=send_frame:parity=auto:deint=interlaced,scale=640:trunc(ow/dar/2)*2,setsar=1"
HILOS_FOTO = anyio.CapacityLimiter(FOTO_MAX)
SIN_CACHE = {"Cache-Control": "no-store"}
_fotos = {}             # flujo -> (cuándo se pidió, jpg o None, código HTTP)
_fotos_en_curso = {}    # flujo -> anyio.Event de la foto que se está sacando
_fotos_fallo = {}       # path -> último error de ffmpeg anotado (para no repetirlo cada 5 s)


def _foto_de(path):
    """JPEG de un fotograma del path, o None si ffmpeg falla o no lo saca en FOTO_LIMITE s."""
    # -analyzeduration 1 s: con 1080i, ffmpeg apura por defecto los 5 s; no hace falta
    cmd = ["nice", "-n", "19", FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
           "-filter_threads", "1", "-threads", "1", "-analyzeduration", "1000000",
           "-i", mtx.url_lectura(path), "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1",
           "-vf", FOTO_FILTRO, "-threads", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "5", "pipe:1"]
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    except OSError as err:
        jpg, error = None, f"no se pudo lanzar ffmpeg: {err}"
    else:
        try:
            jpg, salida_err = p.communicate(timeout=FOTO_LIMITE)
            error = ""
            if p.returncode or not jpg.startswith(b"\xff\xd8"):
                lineas = salida_err.decode(errors="replace").strip().splitlines()
                error = (lineas[-1] if lineas else f"ffmpeg terminó con {p.returncode}")[:200]
        except subprocess.TimeoutExpired:
            jpg, error = None, f"sin foto en {FOTO_LIMITE:.0f} s"
        finally:
            if p.poll() is None:                    # sigue vivo: fuera todo su grupo, y sin zombi
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.communicate()
    if error and _fotos_fallo.get(path) != error:
        print(f"foto de {path}: {error}", flush=True)
    _fotos_fallo[path] = error
    return None if error else jpg


def _capturar(fid):
    """(jpg o None, código HTTP) de la entrada limpia del flujo. Sin ffmpeg si el flujo está
    parado o su entrada no está lista."""
    f = db.conectar().execute("SELECT * FROM flujos WHERE id=?", (fid,)).fetchone()
    if not f:
        return None, 404
    path = path_de_flujo(dict(f))
    try:
        listo = f["activo"] and (mtx.paths_estado().get(path) or {}).get("ready")
    except mtx.ErrorMTX:
        listo = False
    jpg = _foto_de(path) if listo else None
    return jpg, 200 if jpg else 204


@app.get("/api/flujos/{fid}/foto.jpg")
async def foto_entrada(fid: str):
    """Foto de la entrada limpia (JPEG de 640 px), o 204 si ahora no hay."""
    guardada = _fotos.get(fid)
    if not guardada or time.monotonic() - guardada[0] > FOTO_CACHE:
        en_curso = _fotos_en_curso.get(fid)
        if en_curso is not None:                    # ya se está sacando: se espera a esa
            await en_curso.wait()
        elif len(_fotos_en_curso) >= FOTO_MAX:
            return Response(status_code=503, headers=SIN_CACHE)
        else:
            _fotos_en_curso[fid] = en_curso = anyio.Event()
            resultado, pedida = (None, 204), time.monotonic()     # la edad cuenta desde que se pide
            try:
                with anyio.CancelScope(shield=True):   # se termina aunque este navegador se vaya
                    resultado = await anyio.to_thread.run_sync(_capturar, fid, limiter=HILOS_FOTO)
            except Exception as err:                # la foto nunca da un 500
                print(f"foto de {fid}: {err!r}", flush=True)
            finally:
                if len(_fotos) > 200:
                    _fotos.clear()
                _fotos[fid] = (pedida, *resultado)
                del _fotos_en_curso[fid]
                en_curso.set()
        guardada = _fotos.get(fid)
    _, jpg, codigo = guardada or (0, None, 204)
    if codigo == 404:
        return JSONResponse({"detail": "Ese flujo no existe"}, status_code=404, headers=SIN_CACHE)
    if not jpg:
        return Response(status_code=204, headers=SIN_CACHE)
    return Response(jpg, media_type="image/jpeg", headers=SIN_CACHE)


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
    return {"version": VERSION, "mediamtx": mediamtx,
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


app.mount("/static", StaticFiles(directory=str(ESTATICOS)), name="static")
