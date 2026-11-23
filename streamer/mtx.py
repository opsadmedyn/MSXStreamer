"""Cliente mínimo de la API de MediaMTX (v3).

Los paths creados por API no se guardan en mediamtx.yml, así que el supervisor los vuelve a
declarar en cada vuelta: si MediaMTX se reinicia, en unos segundos recupera todos los flujos.
"""

import hashlib
import json
import os
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
              ("rtmpsConn", "/v3/rtmpsconns/list"), ("webRTCSession", "/v3/webrtcsessions/list"),
              ("hlsSession", "/v3/hlssessions/list"))       # cada reproductor HLS, desde MediaMTX 1.21
PROTOCOLO_DE = {"srtConn": "SRT", "rtmpConn": "RTMP", "rtmpsConn": "RTMPS", "webRTCSession": "WebRTC",
                "hlsSession": "HLS", "hlsMuxer": "HLS", "rtspSession": "RTSP", "rtspsSession": "RTSPS"}


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
                 "recibidos": x.get("bytesReceived") or 0, "enviados": x.get("bytesSent") or x.get("outboundBytes") or 0}
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


# Un proxy inverso en este mismo equipo (el Caddy del Recorder delante de HLS o de WebRTC) llega a
# MediaMTX desde 127.0.0.1, que para él es el usuario LOCAL. Probado con la 1.21.1: sin más, quien
# entra por ese proxy lee cualquier señal por HLS y mira o publica por WebRTC sin clave ni token.
# Con estas listas MediaMTX juzga lo que le llega de este equipo por la dirección que el proxy anota
# en X-Forwarded-For; lo que no la trae (el panel y los ffmpeg del hub) sigue siendo local, y quien
# llama directamente desde la red no puede hacerse pasar por local poniéndola él.
# Pedírselas a un MediaMTX que no las tiene reinicia sus servidores HLS y WebRTC (en el banco, quien
# miraba por HLS estuvo 10 s sin imagen): pasa una vez, la primera que el supervisor se las pide.
PROXIES_LOCALES = ["127.0.0.1/32", "::1/128"]        # escritas como las devuelve MediaMTX


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
    return {"authMethod": "internal", "authInternalUsers": lista, "hlsAlwaysRemux": False,
            "hlsTrustedProxies": PROXIES_LOCALES, "webrtcTrustedProxies": PROXIES_LOCALES}


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
#
# RTMPS (RTMP con TLS) es otro servidor de MediaMTX, con su puerto y un certificado. Probado con la
# 1.21.1: si se le pide con un certificado que no puede cargar, MediaMTX se cierra entero (por eso
# se comprueba antes); encenderlo o apagarlo reinicia también el servidor RTMP y corta a quien
# publica por RTMP; una vez en marcha guarda el certificado en memoria, lo recarga solo cuando los
# archivos cambian (una renovación) y, si lo nuevo no vale, sigue con el anterior; y ni un cambio de
# usuarios, ni encender WebRTC, ni cambiar el puerto RTMP lo reinician. Por eso un RTMPS que ya está
# en marcha no se toca cuando lo que se pide ahora no se puede aplicar (el certificado del disco deja
# de valer, o config.json pide otro certificado u otro puerto que no valen): sigue como estaba, que
# no corta a nadie, y se avisa (RTMPS_DISCO).
SERVIDORES = {
    "rtmp": (("rtmpAddress", "puerto", "tcp"),),
    "webrtc": (("webrtcAddress", "puerto", "tcp"), ("webrtcLocalUDPAddress", "udp", "udp")),
}


RTMPS_DISCO = "rtmps_certificado"       # aviso: RTMPS sigue en marcha como estaba; lo que se pide ahora no vale


def aviso(nombre, motivo):
    """Para el registro: el texto de un servidor que no se puede encender (o del aviso de RTMPS)."""
    if nombre == RTMPS_DISCO:
        return ("MediaMTX: RTMPS sigue en marcha como estaba, con el certificado que ya tenía cargado, porque lo "
                f"que se pide ahora no se puede aplicar. {motivo}")
    return f"MediaMTX: no se enciende {nombre.upper()}. {motivo}"


def puerto_libre(puerto, tipo="tcp"):
    """¿Se puede abrir ese puerto en este equipo ahora mismo? En TCP se pregunta como lo abrirá
    MediaMTX (SO_REUSEADDR): un puerto que se acaba de cerrar guarda durante un minuto las conexiones
    que tuvo (TIME_WAIT) y sin eso parecería ocupado, aunque nadie escuche ya en él. Si otro
    programa escucha, sigue saliendo ocupado."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if tipo == "udp" else socket.SOCK_STREAM)
    try:
        if tipo != "udp":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", int(puerto)))
        return True
    except (OSError, ValueError, OverflowError):
        return False
    finally:
        s.close()


def certificado_valido(cert, clave):
    """"" si el certificado y su clave se pueden cargar juntos; si no, por qué. MediaMTX se cierra
    entero cuando se le pide encender un servidor con un certificado que no puede usar: el hub lo
    comprueba antes y, si no vale, no lo pide."""
    import ssl
    for que, ruta, marca, falta in (
            ("el certificado", cert, "-----BEGIN CERTIFICATE-----", "El archivo del certificado no tiene un certificado en formato PEM"),
            ("la clave del certificado", clave, "PRIVATE KEY-----", "El archivo de la clave no tiene una clave privada en formato PEM")):
        if not os.path.isfile(ruta):
            return f"No se encuentra {que} ({ruta})"
        try:
            with open(ruta, encoding="ascii", errors="replace") as f:
                texto = f.read(1_000_000)
        except OSError:
            return f"Este usuario no puede leer {que} ({ruta})"
        if marca not in texto:
            return f"{falta} ({ruta})"
        if "ENCRYPTED" in texto:
            return f"La clave del certificado está cifrada con contraseña y MediaMTX la necesita sin cifrar ({ruta})"
    try:
        # con contraseña vacía: sin ella, OpenSSL la pediría por el terminal si la clave estuviera cifrada
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, clave, password="")
    except (ssl.SSLError, OSError, ValueError) as err:
        if getattr(err, "reason", None) == "KEY_VALUES_MISMATCH":
            return "El certificado y su clave no se pueden usar juntos: la clave es de otro certificado"
        return f"El certificado y su clave no se pueden usar juntos ({getattr(err, 'reason', None) or err})"
    return ""


def conf_servidores(servicios, actual, libre=puerto_libre, certificado=certificado_valido):
    """Claves globales de MediaMTX para los servidores que pide el hub y, aparte, los que no se
    pueden encender: ({clave: valor}, {servidor: motivo}).
    servicios: {"rtmp": {"puerto", "tls": {"puerto", "cert", "clave"} | None} | None,
                "webrtc": {"puerto", "udp", "hosts", "ice"} | None}.
    RTMPS va con el servidor RTMP (cifrado «optional»: se atiende con y sin TLS, cada uno en su
    puerto). Si no se puede encender, RTMP se enciende sin él y el motivo queda en problemas["rtmps"];
    si ya está en marcha y lo que se pide ahora no vale, sigue como estaba y se avisa en problemas[RTMPS_DISCO]."""
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
        if nombre == "rtmp":
            tls = pedido.get("tls")
            conf["rtmpEncryption"] = "no"
            if tls:
                pedida = {"rtmpEncryption": "optional", "rtmpsAddress": f":{int(tls['puerto'])}",
                          "rtmpServerKey": str(tls["clave"]), "rtmpServerCert": str(tls["cert"])}
                en_marcha = bool(actual.get("rtmp")) and actual.get("rtmpEncryption") == "optional"
                ya_abierto = en_marcha and actual.get("rtmpsAddress") == pedida["rtmpsAddress"]
                motivo = certificado(tls["cert"], tls["clave"])
                if not motivo and not ya_abierto and not libre(tls["puerto"], "tcp"):
                    motivo = f"El puerto {int(tls['puerto'])}/TCP está ocupado por otro programa"
                if not motivo:
                    conf.update(pedida)
                elif en_marcha:
                    # ya corre, con el certificado que cargó: se deja como está. Apagarlo reiniciaría el
                    # servidor RTMP y cortaría a quien publica, y lo nuevo no se puede aplicar
                    conf.update({k: actual.get(k) for k in pedida})
                    problemas[RTMPS_DISCO] = motivo
                else:
                    problemas["rtmps"] = motivo
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


def asegurar_acceso(paths_hls, usuarios=(), publicar=(), servicios=None, libre=puerto_libre, certificado=certificado_valido):
    """Aplica las reglas de acceso y los servidores del hub si MediaMTX tiene otra cosa (p. ej. tras
    reiniciarse). Devuelve (cambió, {servidor: por qué no se puede encender})."""
    deseado = acceso_deseado(paths_hls, usuarios, publicar)
    actual = _pedir("GET", "/v3/config/global/get")
    servidores, problemas = conf_servidores(servicios, actual, libre, certificado)
    return _aplicar({**deseado, **servidores}, actual), problemas


def servidores_encendidos():
    """{"rtmp": bool, "webrtc": bool, "rtmps": bool}: los servidores que MediaMTX tiene encendidos ahora mismo."""
    g = _pedir("GET", "/v3/config/global/get")
    encendidos = {nombre: bool(g.get(nombre)) for nombre in SERVIDORES}
    encendidos["rtmps"] = encendidos["rtmp"] and g.get("rtmpEncryption") in ("optional", "strict")
    return encendidos


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
