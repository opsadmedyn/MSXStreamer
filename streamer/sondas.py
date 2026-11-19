"""Media Syntaxis Streamer - sondas: probar una conexión sin emitir nada.

«Probar» dice si al otro lado hay alguien que hable el protocolo, y lo hace sin publicar ni leer
señal: una prueba no puede sacar nada al aire ni tumbar lo que ya está conectado.
  SRT    el saludo inicial del protocolo (inducción) por UDP: responde quien escucha en modo listener.
         No abre una conexión, así que no ocupa al receptor ni comprueba la passphrase.
  RTMP   conexión TCP (con TLS en rtmps) y el saludo de RTMP: responde un servidor RTMP. No se
         envía la clave de emisión: se comprueba al conectar la ruta.
  HLS    se pide la lista y se leen sus variantes y pistas de audio.
  WHIP   petición OPTIONS con el token: el servidor dice si existe y si lo acepta.
Lo que el hub entrega (el receptor viene a por ello) se prueba mirando que el hub esté escuchando
y, si HLS o WebRTC se entregan por una dirección pública propia (un proxy con certificado delante),
que por esa dirección no se entre sin clave.

Cada prueba devuelve sus pasos, con lo que se comprobó en cada uno, y nunca una clave.
"""

import http.client
import ipaddress
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
    no puede comprobar desde aquí (una clave o una passphrase, que solo se ven al abrir la conexión de
    verdad; la dirección pública del hub, si el propio hub no llega a ella); no cuenta."""
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


# ---------------------------------------------------------------- la dirección pública del hub
CLAVE_PUBLICA = "Clave en la dirección pública"
CERTIFICADO_PUBLICO = "Certificado de la dirección pública"
PROPIO = "127.0.0.1"            # de donde dice venir quien quiere pasar por este mismo equipo
_SIN_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # para hablar con este equipo


def _pagina(url, espera, abrir, origen=""):
    """(código, cuerpo) de pedir una página sin credenciales. El cuerpo, solo si respondió 2xx. Con
    `origen`, la petición dice venir de esa dirección (cabecera X-Forwarded-For), como la mandaría
    quien quisiera pasar por otro."""
    cabeceras = {"User-Agent": "msx-hub", **({"X-Forwarded-For": origen} if origen else {})}
    try:
        with abrir(urllib.request.Request(url, headers=cabeceras), timeout=espera) as r:
            return r.status, r.read(1 << 18) if 200 <= r.status < 300 else b""
    except urllib.error.HTTPError as err:
        return err.code, b""


def exige_clave(publica, local, donde, senal="", espera=ESPERA):
    """¿Pide clave la dirección pública del hub a quien entra por ella sin traerla?

    `publica` es la página de una señal que MediaMTX no enseña sin clave a nadie de la red (la de una
    fuente), pedida por la dirección pública; `local`, la misma página pedida a MediaMTX desde este
    equipo; `donde`, la dirección pública como está en Ajustes y `senal`, el nombre de esa fuente,
    para decirlos. Se pide como la pediría un desconocido, sin credenciales: lo correcto es un 401.

    Hace falta porque un proxy inverso en este mismo equipo llega a MediaMTX desde 127.0.0.1, y
    MediaMTX solo sabe de quién es cada petición si el proxy lo anota en X-Forwarded-For (ver
    mtx.PROXIES_LOCALES). Si no lo anota, todo lo que entra por él pasa por ser del propio hub, que
    lee y publica sin clave. Pedir la página no abre ninguna señal ni arranca nada en MediaMTX.

    Si en vez de un 401 llega una página, se compara con la que MediaMTX da a este equipo: solo si es
    la misma se dice que se entra sin clave (otra web en esa dirección también respondería 200).

    Con el 401 no basta. Un proxy que deja pasar la X-Forwarded-For que traiga la petición (uno que
    solo anota X-Real-IP, por ejemplo) responde 401 a quien pide sin más y abre a quien manda esa
    cabecera con 127.0.0.1, porque MediaMTX se la cree. Así que se pide otra vez, diciéndolo.

    Devuelve los pasos. Lo que no se puede saber desde el propio hub (no llega a su dirección pública,
    o el nombre apunta a este mismo equipo y la petición sería suya de verdad) no cuenta."""
    def desde_aqui_no(motivo, ms=None):
        return [_paso(CLAVE_PUBLICA, None, f"{motivo}. Desde aquí no se puede comprobar que esa dirección pida clave: "
                                           "hay que mirarlo desde otro equipo", ms, al_conectar=True)]
    try:
        esquema, host, puerto = partes_url(publica, 443 if publica.lower().startswith("https") else 80)
        if esquema not in ("http", "https"):
            raise ValueError("Tiene que empezar por https:// (o http://)")
    except ValueError as err:
        return [_paso(CLAVE_PUBLICA, None, f"La dirección pública {donde} no se puede usar. {err}")]
    paso, dirs = resolver(host, puerto)
    if not dirs:
        return desde_aqui_no(f"Este equipo no encuentra «{host}»", paso["ms"])
    if any(ipaddress.ip_address(d[0].split("%")[0]).is_loopback for _, d in dirs):
        return desde_aqui_no(f"«{host}» apunta a este mismo equipo, y lo que se pide desde aquí entra como del propio hub")

    pasos, abrir = [], urllib.request.urlopen
    while True:
        t = time.monotonic()
        try:
            codigo, cuerpo = _pagina(publica, espera, abrir)
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as err:
            motivo = getattr(err, "reason", err)
            if isinstance(motivo, ssl.SSLCertVerificationError) and not pasos:
                # lo que importa aquí es si pide clave: se dice lo del certificado y se pregunta sin comprobarlo
                pasos.append(_paso(CERTIFICADO_PUBLICO, None,
                                   f"El certificado de {donde} no es válido para este equipo ({motivo.verify_message}). "
                                   "Un navegador que tampoco lo dé por bueno no abrirá la señal", _ms(t)))
                sin_comprobar = ssl.create_default_context()
                sin_comprobar.check_hostname, sin_comprobar.verify_mode = False, ssl.CERT_NONE
                abrir = lambda peticion, timeout: urllib.request.urlopen(peticion, timeout=timeout, context=sin_comprobar)
                continue
            texto = _error_red(motivo) if isinstance(motivo, Exception) else f"No se pudo conectar: {motivo}"
            return pasos + desde_aqui_no(f"Este equipo no llega a {donde} ({texto[0].lower()}{texto[1:]})", _ms(t))
        break
    ms = _ms(t)
    de = f" de «{senal}»" if senal else ""

    def la_del_hub(respuesta):
        """¿Es eso lo que MediaMTX da a este equipo por esa misma página?"""
        try:
            return _pagina(local, espera, _SIN_PROXY.open) == respuesta
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
            return False
    if codigo in (401, 403):
        t = time.monotonic()
        try:
            disfrazada = _pagina(publica, espera, abrir, origen=PROPIO)
            dijo = f"respondió {disfrazada[0]}"
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as err:
            motivo = getattr(err, "reason", err)
            texto = _error_red(motivo) if isinstance(motivo, Exception) else f"No se pudo conectar: {motivo}"
            disfrazada, dijo = (0, b""), f"{texto[0].lower()}{texto[1:]}"
        ms += _ms(t)
        if 200 <= disfrazada[0] < 300 and la_del_hub(disfrazada):
            pasos.append(_paso(CLAVE_PUBLICA, False,
                               f"Por {donde} se entra sin clave diciendo venir del propio equipo: al pedir la página{de} "
                               f"con la cabecera X-Forwarded-For: {PROPIO}, responde con ella. El proxy que hay delante "
                               "deja pasar esa cabecera como la trae la petición, en vez de poner en ella la dirección de "
                               "quien conecta, y el hub se la cree: quien lo sepa puede ver cualquier señal y publicar. "
                               "Se corrige en el proxy", ms))
        elif disfrazada[0] in (401, 403):
            pasos.append(_paso(CLAVE_PUBLICA, True, f"Por {donde} no se entra sin clave a lo que el hub no entrega abierto, "
                                                    "ni diciendo venir del propio equipo", ms))
        else:
            pasos.append(_paso(CLAVE_PUBLICA, None,
                               f"Por {donde} no entra sin clave quien pide sin más, pero no se pudo comprobar que tampoco "
                               f"entre quien dice venir del propio equipo (al pedirlo así, {dijo}). Vuelve a probar", ms))
    elif 200 <= codigo < 300:
        if la_del_hub((codigo, cuerpo)):
            pasos.append(_paso(CLAVE_PUBLICA, False,
                               f"Por {donde} se entra sin clave: al pedir sin credenciales la página{de}, que el hub no "
                               "entrega abierta, responde con ella. El proxy que hay delante no le dice al hub de dónde "
                               "viene cada petición (cabecera X-Forwarded-For) y el hub las toma por suyas: quien conozca "
                               "la dirección puede ver cualquier señal y publicar. Se corrige en el proxy", ms))
        else:
            pasos.append(_paso(CLAVE_PUBLICA, None, f"{donde} responde, pero no con lo que da el hub: no parece que esa "
                                                    "dirección lleve hasta él", ms))
    elif codigo == 500:
        # MediaMTX responde así, a cualquiera, cuando aún no conoce la señal por la que se pregunta
        pasos.append(_paso(CLAVE_PUBLICA, None, f"No se pudo comprobar ahora que {donde} pida clave: MediaMTX todavía no "
                                                f"conoce la señal{de}. Vuelve a probar en unos segundos", ms, al_conectar=True))
    elif codigo in (502, 503, 504):
        pasos.append(_paso(CLAVE_PUBLICA, False, f"{donde} respondió {codigo}: el proxy que hay delante no llega al hub", ms))
    else:
        pasos.append(_paso(CLAVE_PUBLICA, None, f"{donde} respondió {codigo} al pedir la página{de}: no parece que esa "
                                                "dirección lleve hasta el hub", ms))
    return pasos


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
