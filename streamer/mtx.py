"""Cliente mínimo de la API de MediaMTX (v3).

Los paths creados por API no se guardan en mediamtx.yml, así que el supervisor los vuelve a
declarar en cada vuelta: si MediaMTX se reinicia, en unos segundos recupera todos los flujos.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import db

CFG = db.config()
API = CFG.get("mediamtx_api", "http://127.0.0.1:9997")
SRT_LOCAL = CFG.get("mediamtx_srt", "srt://127.0.0.1:8890")


class ErrorMTX(Exception):
    pass


def _pedir(metodo, ruta, cuerpo=None, timeout=3):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(API + ruta, data=datos, method=metodo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read()
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as e:
        raise ErrorMTX(f"{metodo} {ruta}: {e.code} {e.read().decode(errors='replace')[:200]}")
    except (urllib.error.URLError, OSError) as e:
        raise ErrorMTX(f"MediaMTX no responde: {e}")


def _todos(ruta):
    items, pagina = [], 0
    while True:
        r = _pedir("GET", f"{ruta}?page={pagina}&itemsPerPage=200")
        items += r.get("items") or []
        pagina += 1
        if pagina >= r.get("pageCount", 1):
            return items


def paths_config():
    return {p["name"]: p for p in _todos("/v3/config/paths/list")}


def paths_estado():
    return {p["name"]: p for p in _todos("/v3/paths/list")}


# Quién está conectado a MediaMTX. De cada conexión se toma solo lo que sirve al panel: MediaMTX
# devuelve también `query`, que en RTMP lleva el usuario y la clave con los que se publicó.
CONEXIONES = (("srtConn", "/v3/srtconns/list"), ("rtmpConn", "/v3/rtmpconns/list"),
              ("rtmpsConn", "/v3/rtmpsconns/list"), ("webRTCSession", "/v3/webrtcsessions/list"))
PROTOCOLO_DE = {"srtConn": "SRT", "rtmpConn": "RTMP", "rtmpsConn": "RTMPS", "webRTCSession": "WebRTC",
                "hlsMuxer": "HLS", "rtspSession": "RTSP", "rtspsSession": "RTSPS"}


def conexiones():
    """{(tipo, id): {"protocolo", "remoto", "estado", "path", "desde", "recibidos", "enviados", …}}.
    Un servidor apagado (RTMP o WebRTC sin usar) responde 404: no tiene conexiones."""
    r = {}
    for tipo, ruta in CONEXIONES:
        try:
            items = _todos(ruta)
        except ErrorMTX:
            continue
        for x in items:
            c = {"protocolo": PROTOCOLO_DE[tipo], "remoto": x.get("remoteAddr") or "", "estado": x.get("state") or "",
                 "path": x.get("path") or "", "desde": x.get("created") or "",
                 "recibidos": x.get("bytesReceived") or 0, "enviados": x.get("bytesSent") or 0}
            if tipo == "srtConn":
                c.update(rtt_ms=x.get("msRTT"), perdidos=(x.get("packetsSendLoss") or 0) + (x.get("packetsReceivedLoss") or 0),
                         reenviados=(x.get("packetsRetrans") or 0) + (x.get("packetsReceivedRetrans") or 0))
            if tipo == "webRTCSession":
                c["establecida"] = bool(x.get("peerConnectionEstablished"))
            r[(tipo, x.get("id"))] = c
    return r


def muxers_hls():
    """{path: {"creado", "ultima_peticion", "enviados"}} de los HLS que alguien está pidiendo."""
    try:
        return {x.get("path"): {"creado": x.get("created") or "", "ultima_peticion": x.get("lastRequest") or "",
                                "enviados": x.get("bytesSent") or 0} for x in _todos("/v3/hlsmuxers/list")}
    except ErrorMTX:
        return {}


def _url_srt(url, streamid="", passphrase=""):
    """Se respetan los parámetros pegados en la URL; los campos del formulario mandan."""
    base, _, consulta = url.partition("?")
    q = dict(urllib.parse.parse_qsl(consulta))
    if streamid:
        q["streamid"] = streamid
    if passphrase:
        q["passphrase"] = passphrase
    return base + ("?" + urllib.parse.urlencode(q) if q else "")


def path_de_flujo(f):
    """Path de MediaMTX de un flujo: el suyo o, si lee un canal del Recorder, el del canal."""
    return "rec_" + f["canal"] if f["entrada"] == "recorder" else f["id"]


def conf_de_flujo(f, canales, indice=0):
    """Configuración del path de MediaMTX que corresponde a un flujo (None: aún no hay entrada).
    hub: `indice` dice cuál de sus direcciones (db.fuentes) se usa; la elige el supervisor."""
    if f["entrada"] == "pull":
        return {"source": _url_srt(f["url"], f["streamid"], f["passphrase"]), "sourceOnDemand": False}
    if f["entrada"] == "hub":
        fuentes = db.fuentes(f)
        return {"source": _url_srt(fuentes[indice % len(fuentes)]), "sourceOnDemand": False} if fuentes else None
    # recorder: el grabador del canal reenvía por UDP local lo mismo que graba. Es UDP a
    # propósito: si MediaMTX está parado, el grabador no se entera ni se bloquea.
    # Sin reenvío, el Streamer lee el mismo origen que el canal (si es SRT: la lectura del hub).
    c = next((c for c in canales if c["id"] == f["canal"]), None)
    if c and c["reenvio_udp"]:
        return {"source": f"udp+mpegts://127.0.0.1:{c['reenvio_udp']}"}
    if c and c.get("url", "").startswith("srt://"):
        return {"source": _url_srt(c["url"]), "sourceOnDemand": False}
    return None


def _coincide(actual, deseado):
    """¿Lo que tiene MediaMTX cumple lo deseado? Solo se miran las claves que se piden: MediaMTX
    devuelve además las que tienen su valor por defecto (y cada versión añade alguna)."""
    if isinstance(deseado, dict):
        return isinstance(actual, dict) and all(_coincide(actual.get(k), v) for k, v in deseado.items())
    if isinstance(deseado, list):
        return (isinstance(actual, list) and len(actual) == len(deseado)
                and all(_coincide(a, d) for a, d in zip(actual, deseado)))
    if actual == OCULTA and deseado:        # una clave: MediaMTX no la devuelve (ver asegurar_acceso)
        return True
    return actual == deseado


def asegurar_path(nombre, conf, actuales):
    """Crea o corrige el path si no coincide con lo que debe ser. Devuelve True si cambió."""
    actual = actuales.get(nombre)
    if actual is None:
        _pedir("POST", f"/v3/config/paths/add/{urllib.parse.quote(nombre)}", conf)
        return True
    if not _coincide(actual, conf):
        _pedir("PATCH", f"/v3/config/paths/patch/{urllib.parse.quote(nombre)}", conf)
        return True
    return False


def borrar_path(nombre):
    _pedir("DELETE", f"/v3/config/paths/delete/{urllib.parse.quote(nombre)}")


def url_lectura(path):
    return f"{SRT_LOCAL}?streamid=read:{path}"


def url_publicacion(path):
    return f"{SRT_LOCAL}?streamid=publish:{path}"


# ---------------------------------------------------------------- acceso (HLS bajo demanda)
# Las reglas de acceso viven en el propio MediaMTX (authMethod internal) y el supervisor las
# ajusta por la API. Así no dependen del panel: si el panel está parado, las salidas se siguen
# conectando. Probado: cambiar estas reglas en caliente no corta a los lectores conectados.
LOCAL = {"user": "any", "pass": "", "ips": ["127.0.0.1", "::1"],
         "permissions": [{"action": "publish", "path": ""}, {"action": "read", "path": ""},
                         {"action": "api", "path": ""}]}


def _patron(paths):
    return "~^(" + "|".join(re.escape(p) for p in sorted(paths)) + ")$"


def acceso_deseado(paths_hls, usuarios=(), publicar=()):
    """Desde este equipo, todo (las salidas leen de aquí; el panel y el supervisor usan la API).
    Desde la red, sin clave: leer los flujos con HLS activo y los destinos del hub que se entregan
    sin clave (`paths_hls`), y publicar en las fuentes del hub sin clave (`publicar`). Con clave:
    los `usuarios` del hub (matriz.py), uno por fuente o destino y solo para su path."""
    lista, permisos = [LOCAL], []
    if paths_hls:
        permisos.append({"action": "read", "path": _patron(paths_hls)})
    if publicar:
        permisos.append({"action": "publish", "path": _patron(publicar)})
    if permisos:
        lista.append({"user": "any", "pass": "", "ips": [], "permissions": permisos})
    lista += sorted(usuarios, key=lambda u: json.dumps(u, sort_keys=True))
    return {"authMethod": "internal", "authInternalUsers": lista, "hlsAlwaysRemux": False}


OCULTA = "<redacted>"       # así devuelve MediaMTX una contraseña guardada: no se puede comparar


def _normalizar(g):
    ip = lambda x: x.removesuffix("/32").removesuffix("/128")
    usuarios = tuple(
        (u.get("user"), OCULTA if u.get("pass") else "", tuple(sorted(ip(i) for i in u.get("ips") or [])),
         tuple(sorted((q.get("action"), q.get("path") or "") for q in u.get("permissions") or [])))
        for u in g.get("authInternalUsers") or [])
    return g.get("authMethod"), usuarios, bool(g.get("hlsAlwaysRemux"))


# ---------------------------------------------------------------- servidores que el hub necesita
# RTMP y WebRTC vienen apagados en mediamtx.yml y los enciende el supervisor por la API cuando una
# fuente o un destino del hub los usa. Probado con la 1.21.1: encenderlos así no corta las
# conexiones SRT en curso. También probado: si el puerto está ocupado por otro programa, MediaMTX
# se cierra entero al aplicar el cambio. Por eso antes de pedirle un puerto nuevo se comprueba que
# esté libre, y si no lo está no se le pide: el servidor se queda apagado y el panel dice por qué.
SERVIDORES = {
    "rtmp": (("rtmpAddress", "puerto", "tcp"),),
    "webrtc": (("webrtcAddress", "puerto", "tcp"), ("webrtcLocalUDPAddress", "udp", "udp")),
}


def puerto_libre(puerto, tipo="tcp"):
    """¿Se puede abrir ese puerto en este equipo ahora mismo?"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if tipo == "udp" else socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", int(puerto)))
        return True
    except (OSError, ValueError, OverflowError):
        return False
    finally:
        s.close()


def conf_servidores(servicios, actual, libre=puerto_libre):
    """Claves globales de MediaMTX para los servidores que pide el hub y, aparte, los que no se
    pueden encender: ({clave: valor}, {servidor: motivo}).
    servicios: {"rtmp": {"puerto"} | None, "webrtc": {"puerto", "udp", "hosts", "ice"} | None}."""
    conf, problemas = {}, {}
    for nombre, direcciones in SERVIDORES.items():
        pedido = (servicios or {}).get(nombre)
        if not pedido:
            conf[nombre] = False
            continue
        propias, ocupado = {}, None
        for clave, campo, tipo in direcciones:
            direccion = f":{int(pedido[campo])}"
            propias[clave] = direccion
            ya_abierto = bool(actual.get(nombre)) and actual.get(clave) == direccion
            if not ya_abierto and not libre(pedido[campo], tipo):
                ocupado = f"El puerto {int(pedido[campo])}/{tipo.upper()} está ocupado por otro programa"
        if ocupado:
            problemas[nombre] = ocupado
            continue                        # no se toca: como esté ahora
        conf[nombre] = True
        conf.update(propias)
        if nombre == "webrtc":
            conf["webrtcAdditionalHosts"] = [h for h in pedido.get("hosts") or [] if h]
            conf["webrtcICEServers2"] = [
                {"url": i["url"], "username": i.get("usuario") or "", "password": i.get("clave") or "",
                 "clientOnly": False} for i in pedido.get("ice") or [] if i.get("url")]
    return conf, problemas


_aplicado = None            # huella de lo último que este proceso pidió a MediaMTX


def _huella(conf):
    return hashlib.sha256(json.dumps(conf, sort_keys=True).encode()).hexdigest()


def _aplicar(deseado, actual):
    """Pide a MediaMTX la configuración global deseada si no la tiene ya. MediaMTX no devuelve las
    contraseñas guardadas, así que con ellas no basta comparar: además se recuerda qué se le pidió
    la última vez (si cambia una clave, o tras reiniciar el supervisor, se le vuelve a pedir).
    Al cambiar la configuración global MediaMTX reinicia su API: se espera a que vuelva."""
    global _aplicado
    con_claves = OCULTA in json.dumps(actual) or any(u.get("pass") for u in deseado.get("authInternalUsers") or []) \
        or any(i.get("password") for i in deseado.get("webrtcICEServers2") or [])
    acceso = {k: deseado[k] for k in ("authMethod", "authInternalUsers", "hlsAlwaysRemux")}
    resto = {k: v for k, v in deseado.items() if k not in acceso}
    if _normalizar(actual) == _normalizar(acceso) and _coincide(actual, resto) \
            and (not con_claves or _aplicado == _huella(deseado)):
        return False
    _pedir("PATCH", "/v3/config/global/patch", deseado)
    _aplicado = _huella(deseado)
    for _ in range(30):
        try:
            _pedir("GET", "/v3/paths/list?itemsPerPage=1", timeout=1)
            break
        except ErrorMTX:
            time.sleep(0.1)
    return True


def asegurar_acceso(paths_hls, usuarios=(), publicar=(), servicios=None, libre=puerto_libre):
    """Aplica las reglas de acceso y los servidores del hub si MediaMTX tiene otra cosa (p. ej. tras
    reiniciarse). Devuelve (cambió, {servidor: por qué no se puede encender})."""
    deseado = acceso_deseado(paths_hls, usuarios, publicar)
    actual = _pedir("GET", "/v3/config/global/get")
    servidores, problemas = conf_servidores(servicios, actual, libre)
    return _aplicar({**deseado, **servidores}, actual), problemas


def asegurar_acceso_solo(paths_hls, usuarios=(), publicar=()):
    """Solo las reglas de acceso, sin tocar qué servidores están encendidos. Devuelve True si cambió."""
    return _aplicar(acceso_deseado(paths_hls, usuarios, publicar), _pedir("GET", "/v3/config/global/get"))


def acceso_red():
    """Paths que MediaMTX deja leer desde la red ahora mismo (None si no responde)."""
    try:
        g = _pedir("GET", "/v3/config/global/get")
    except ErrorMTX:
        return None
    paths = set()
    for u in g.get("authInternalUsers") or []:
        for q in u.get("permissions") or []:
            m = re.fullmatch(r"~\^\((.*)\)\$", q.get("path") or "")
            if q.get("action") == "read" and m:
                paths |= {re.sub(r"\\(.)", r"\1", x) for x in m.group(1).split("|")}
    return paths
