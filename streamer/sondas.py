"""Media Syntaxis Streamer - sondas: probar una conexión sin emitir nada.

«Probar» dice si al otro lado hay alguien que hable el protocolo, y lo hace sin publicar ni leer
señal: una prueba no puede sacar nada al aire ni tumbar lo que ya está conectado.
  SRT    el saludo inicial del protocolo (inducción) por UDP: responde quien escucha en modo listener.
         No abre una conexión, así que no ocupa al receptor ni comprueba la passphrase.
  RTMP   conexión TCP (con TLS en rtmps) y el saludo de RTMP: responde un servidor RTMP. No se
         envía la clave de emisión: se comprueba al conectar la ruta.
  HLS    se pide la lista y se leen sus variantes y pistas de audio.
  WHIP   petición OPTIONS con el token: el servidor dice si existe y si lo acepta.
Lo que el hub entrega (el receptor viene a por ello) se prueba mirando que el hub esté escuchando.

Cada prueba devuelve sus pasos, con lo que se comprobó en cada uno, y nunca una clave.
"""

import os
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

import secretos

ESPERA = 4.0                    # segundos como mucho por paso
SRT_MAGIA = 0x4A17              # lo que pone un listener SRT (HSv5) en su respuesta de inducción


def _paso(que, ok, detalle="", ms=None, al_conectar=False):
    """Un paso de la prueba. ok: True, False o None (no se pudo saber). al_conectar: lo que una prueba
    no puede comprobar sin abrir la conexión de verdad (una clave, una passphrase); no cuenta."""
    return {"que": que, "ok": ok, "detalle": secretos.ocultar(detalle), "ms": None if ms is None else round(ms),
            "al_conectar": al_conectar}


def _resultado(pasos, resumen=None):
    """ok: False si algún paso falló, None si alguno no se pudo saber, True si todo lo comprobable fue bien."""
    cuentan = [p for p in pasos if not p["al_conectar"]]
    fallos = [p for p in cuentan if p["ok"] is False]
    dudas = [p for p in cuentan if p["ok"] is None]
    ok = False if fallos else None if dudas or not cuentan else True
    if resumen is None:
        resumen = (fallos or dudas or cuentan[-1:] or [{"detalle": ""}])[0]["detalle"]
    return {"ok": ok, "resumen": secretos.ocultar(resumen), "pasos": pasos, "t": time.time()}


def _ms(desde):
    return (time.monotonic() - desde) * 1000


def _error_red(err):
    """Un error de red dicho de forma que sirva para actuar."""
    if isinstance(err, socket.timeout) or isinstance(err, TimeoutError):
        return "No responde: o el equipo está apagado o un cortafuegos bloquea el puerto"
    if isinstance(err, ConnectionRefusedError):
        return "El equipo responde que ese puerto está cerrado: no hay nada escuchando ahí"
    if isinstance(err, ssl.SSLCertVerificationError):
        return f"El certificado del servidor no es válido: {err.verify_message}"
    if isinstance(err, ssl.SSLError):
        return f"No se pudo establecer la conexión segura (TLS): {err.reason or err}"
    if isinstance(err, OSError) and err.errno in (101, 113):
        return "No hay ruta de red hasta ese equipo"
    return f"No se pudo conectar: {getattr(err, 'strerror', None) or err}"


def resolver(host, puerto, tipo=socket.SOCK_STREAM):
    """(paso, direcciones) de resolver un nombre."""
    t = time.monotonic()
    try:
        info = socket.getaddrinfo(host, puerto, type=tipo)
    except (socket.gaierror, UnicodeError) as err:
        return _paso("Resolver el nombre", False, f"No se encuentra «{host}»: revisa que esté bien escrito", _ms(t)), []
    dirs = []
    for familia, _, _, _, donde in info:
        if (familia, donde[0]) not in [(f, d[0]) for f, d in dirs]:
            dirs.append((familia, donde))
    return _paso("Resolver el nombre", True, f"{host} es {dirs[0][1][0]}", _ms(t)), dirs


def partes_url(url, puerto_defecto=None):
    """(esquema, host, puerto) de una URL, o ValueError con un texto para el operador."""
    try:
        p = urllib.parse.urlsplit(url, allow_fragments=False)
        puerto = p.port or puerto_defecto
    except ValueError:
        raise ValueError("La dirección no es válida")
    if not p.hostname:
        raise ValueError("A la dirección le falta el servidor")
    if not puerto:
        raise ValueError("A la dirección le falta el puerto")
    return p.scheme.lower(), p.hostname, puerto


# ---------------------------------------------------------------- SRT
def paquete_induccion(id_socket):
    """Primer paquete de quien llama (caller) en SRT: saludo de inducción, versión 4."""
    cabecera = struct.pack(">IIII", 0x80000000, 0, 0, 0)          # control, tipo 0 (handshake), destino 0
    cuerpo = struct.pack(">IHHIIIiII", 4, 0, 2, 0, 1500, 8192, 1, id_socket, 0) + bytes(16)
    return cabecera + cuerpo


def leer_induccion(datos, id_socket):
    """De la respuesta de un listener: {"version", "srt": bool} o None si no es una respuesta al saludo."""
    if len(datos) < 64:
        return None
    control, _, _, destino = struct.unpack(">IIII", datos[:16])
    if control != 0x80000000 or destino != id_socket:
        return None
    version, _, extension, _, _, _, tipo = struct.unpack(">IHHIIIi", datos[16:40])
    if tipo != 1:
        return None
    return {"version": version, "srt": version == 5 and extension == SRT_MAGIA}


def srt_escucha(host, puerto, espera=ESPERA):
    """¿Hay un equipo SRT en modo listener en host:puerto? Solo el saludo: no se abre conexión."""
    paso, dirs = resolver(host, puerto, socket.SOCK_DGRAM)
    pasos = [paso]
    if not dirs:
        return _resultado(pasos)
    familia, donde = dirs[0]
    id_socket = struct.unpack(">I", os.urandom(4))[0] & 0x3FFFFFFF or 1
    s = socket.socket(familia, socket.SOCK_DGRAM)
    t = time.monotonic()
    try:
        s.connect(donde)
        limite = t + espera
        while True:                                 # como hace SRT: repite el saludo cada 250 ms
            s.send(paquete_induccion(id_socket))
            s.settimeout(0.25)
            try:
                r = leer_induccion(s.recv(1500), id_socket)
            except socket.timeout:
                r = None
            if r:
                break
            if time.monotonic() > limite:
                raise socket.timeout()
        if r["srt"]:
            pasos.append(_paso("Saludo SRT", True, f"Un equipo SRT escucha en {host}:{puerto}", _ms(t)))
            pasos.append(_paso("Passphrase y stream ID", None,
                               "No se pueden comprobar sin abrir la conexión: se verán al conectar la ruta", al_conectar=True))
            return _resultado(pasos, f"Un equipo SRT escucha en {host}:{puerto}. La passphrase y el stream ID "
                                     "se comprueban al conectar.")
        pasos.append(_paso("Saludo SRT", None, f"Responde un equipo UDT o SRT antiguo (versión {r['version']})", _ms(t)))
    except (OSError, socket.timeout) as err:
        texto = _error_red(err)
        if isinstance(err, (socket.timeout, TimeoutError)):
            texto = (f"Nadie responde al saludo SRT en {host}:{puerto} (UDP). O no hay un equipo escuchando en modo "
                     "listener, o ya está ocupado con otra conexión, o un cortafuegos bloquea el puerto")
        pasos.append(_paso("Saludo SRT", False, texto, _ms(t)))
    finally:
        s.close()
    return _resultado(pasos)


# ---------------------------------------------------------------- RTMP
def rtmp_responde(url, espera=ESPERA):
    """¿Hay un servidor RTMP en la dirección? Conexión y saludo; no se publica ni se manda la clave."""
    try:
        esquema, host, puerto = partes_url(url, 443 if url.lower().startswith("rtmps") else 1935)
    except ValueError as err:
        return _resultado([_paso("Dirección", False, str(err))])
    paso, dirs = resolver(host, puerto)
    pasos = [paso]
    if not dirs:
        return _resultado(pasos)
    t = time.monotonic()
    s = None
    try:
        s = socket.create_connection((host, puerto), timeout=espera)
        pasos.append(_paso("Conexión TCP", True, f"{host}:{puerto} acepta conexiones", _ms(t)))
        if esquema == "rtmps":
            t = time.monotonic()
            try:
                s = ssl.create_default_context().wrap_socket(s, server_hostname=host)
                pasos.append(_paso("Conexión segura (TLS)", True, "Certificado válido", _ms(t)))
            except ssl.SSLCertVerificationError as err:
                # Quien envía (ffmpeg) cifra pero no comprueba el certificado: la ruta conectará igual.
                # No es un fallo de conexión sino algo que el operador debe saber; se sigue con el
                # saludo por una conexión nueva, sin esa comprobación.
                s.close()
                sin_comprobar = ssl.create_default_context()
                sin_comprobar.check_hostname, sin_comprobar.verify_mode = False, ssl.CERT_NONE
                s = sin_comprobar.wrap_socket(socket.create_connection((host, puerto), timeout=espera), server_hostname=host)
                pasos.append(_paso("Conexión segura (TLS)", None,
                                   f"El certificado del servidor no es válido ({err.verify_message}). El hub emite "
                                   "igualmente y cifrado, pero sin garantía de que el servidor sea quien dice ser", _ms(t)))
        t = time.monotonic()
        s.settimeout(espera)
        s.sendall(b"\x03" + struct.pack(">II", 0, 0) + os.urandom(1528))        # C0 + C1
        recibido = b""
        while len(recibido) < 1537:                                             # S0 + S1
            trozo = s.recv(4096)
            if not trozo:
                break
            recibido += trozo
        if len(recibido) >= 1537 and recibido[0] == 3:
            pasos.append(_paso("Saludo RTMP", True, "Responde un servidor RTMP", _ms(t)))
            pasos.append(_paso("Clave de emisión", None, "No se envía en la prueba: se comprueba al conectar la ruta",
                               al_conectar=True))
            dudas = any(p["ok"] is None and not p["al_conectar"] for p in pasos)     # el certificado: que sea eso el resumen
            return _resultado(pasos, None if dudas else
                              f"Responde un servidor RTMP en {host}:{puerto}. La clave se comprueba al conectar.")
        pasos.append(_paso("Saludo RTMP", False, "El servidor acepta la conexión pero no responde como RTMP", _ms(t)))
    except (OSError, socket.timeout) as err:
        nombre = "Conexión segura (TLS)" if isinstance(err, ssl.SSLError) else \
            "Saludo RTMP" if len(pasos) > 1 and pasos[-1]["ok"] else "Conexión TCP"
        pasos.append(_paso(nombre, False, _error_red(err), _ms(t)))
    finally:
        if s is not None:
            s.close()
    return _resultado(pasos)


# ---------------------------------------------------------------- WHIP
def whip_responde(url, token="", espera=ESPERA):
    """¿Existe el destino WHIP y acepta el token? Con OPTIONS: no se publica nada."""
    try:
        esquema, host, _ = partes_url(url, 443 if url.lower().startswith("https") else 80)
    except ValueError as err:
        return _resultado([_paso("Dirección", False, str(err))])
    if esquema not in ("http", "https"):
        return _resultado([_paso("Dirección", False, "Un destino WHIP empieza por https:// (o http:// en la red local)")])
    cabeceras = {"User-Agent": "msx-hub"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    t = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="OPTIONS", headers=cabeceras), timeout=espera) as r:
            codigo = r.status
    except urllib.error.HTTPError as err:
        codigo = err.code
    except (urllib.error.URLError, OSError, ValueError) as err:
        motivo = getattr(err, "reason", err)
        texto = _error_red(motivo) if isinstance(motivo, Exception) else f"No se pudo conectar: {motivo}"
        return _resultado([_paso("Conexión con el servidor", False, texto, _ms(t))])
    pasos = [_paso("Conexión con el servidor", True, f"{host} responde", _ms(t))]
    if codigo in (401, 403):
        pasos.append(_paso("Token", False, "El servidor rechaza el token" if token else "El servidor pide un token"))
    elif codigo == 404:
        pasos.append(_paso("Dirección WHIP", False, "El servidor dice que esa dirección no existe"))
    elif codigo in (405, 501) or 300 <= codigo < 400:
        pasos.append(_paso("Dirección WHIP", None, "El servidor no permite comprobar la dirección sin publicar: "
                                                    "se verá al conectar la ruta"))
    elif 200 <= codigo < 300:
        pasos.append(_paso("Dirección WHIP", True, "El servidor acepta la dirección" + (" y el token" if token else "")))
    else:
        pasos.append(_paso("Dirección WHIP", False, f"El servidor respondió {codigo}"))
    return _resultado(pasos)


# ---------------------------------------------------------------- lo que escucha el hub
def hub_escucha(puerto, tipo="tcp", que=""):
    """¿Hay algo escuchando en ese puerto de este equipo? (Lo que el hub entrega lo abre el otro.)
    En UDP no se puede preguntar sin molestar: se mira si el puerto está ocupado."""
    nombre = f"Puerto {puerto}/{tipo.upper()} del hub"
    if tipo == "udp":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("0.0.0.0", int(puerto)))
        except OSError:
            return _paso(nombre, True, f"{que or 'El hub'} escucha en el puerto {puerto}")
        finally:
            s.close()
        return _paso(nombre, False, f"Nada escucha en el puerto {puerto}: {que or 'el servicio'} no está en marcha")
    try:
        socket.create_connection(("127.0.0.1", int(puerto)), timeout=1.5).close()
    except OSError:
        return _paso(nombre, False, f"Nada escucha en el puerto {puerto}: {que or 'el servicio'} no está en marcha")
    return _paso(nombre, True, f"{que or 'El hub'} escucha en el puerto {puerto}")


resultado, paso = _resultado, _paso
