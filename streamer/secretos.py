"""Media Syntaxis Streamer - claves: qué se enseña y qué llega a los registros.

Una clave (passphrase, stream ID, clave de emisión, usuario y contraseña de una URL y lo que va tras
"?") se guarda en la base y viaja al proceso que la necesita, y a ningún sitio más:
  - por la API solo la ve quien tiene permiso, y las de un equipo ajeno (la passphrase o la clave de
    emisión de un destino) no vuelven nunca: solo se escriben;
  - en los registros (logs/<id>.log, eventos y errores que enseña el panel) no sale: el lanzador
    (correr.py) filtra la línea de arranque y todo lo que escribe ffmpeg, y los textos guardados
    antes de existir el lanzador pasan por limpiar() antes de enseñarse.

Este módulo no importa la base al cargarse: el lanzador lo usa y tiene que arrancar ligero.
"""

import re
import urllib.parse

OCULTO = "***"
LINEA_ARRANQUE = re.compile(r"== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d ")     # la que se escribe al lanzar
OPCIONES_SECRETAS = (" -srt_streamid ", " -passphrase ")
# opciones de ffmpeg cuyo valor es una clave (o puede llevarla)
OPCIONES_CON_CLAVE = {"-passphrase", "-srt_streamid", "-rtmp_playpath", "-headers", "-cookies", "-password"}
URL_EN_TEXTO = re.compile(r"\b(srt|rtmps?|https?|wss?|whips?|wheps?)://[^\s\"'<>]+")
PARAMS_NO_SECRETOS = {"mode", "latency", "rcvlatency", "peerlatency", "pkt_size", "payload_size", "transtype"}
SIN_CLAVES = "(sin claves) "
LOCALES = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
ENLACE_INTERNO = re.compile(r"^(read|publish):[A-Za-z0-9_-]+$")     # el stream ID con el que el hub se lee a sí mismo
MINIMO = 1          # una clave de un carácter también se oculta: mejor un texto raro que una clave a la vista


def clave_en_ruta(ruta):
    """Último tramo no vacío de la ruta de una URL RTMP: la clave, si no va aparte."""
    return ruta.rstrip("/").rpartition("/")[2]


def _interna(p):
    """¿Es el enlace de una pieza del hub con otra, por el SRT de MediaMTX en este equipo? Lo que lleva
    es el nombre del path («streamid=read:camara-1»), no una clave. Cualquier otra dirección de este
    equipo (otro servidor que corra aquí, con su clave o su passphrase) se tapa como las de fuera."""
    if p.scheme != "srt" or p.hostname not in LOCALES or p.username or p.password:
        return False
    for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
        if k not in PARAMS_NO_SECRETOS and not (k == "streamid" and ENLACE_INTERNO.match(v)):
            return False
    return True


def url_visible(url, tipo="", clave=""):
    """URL sin usuario ni contraseña, sin parámetros y, en RTMP sin clave aparte, sin su último tramo."""
    if not url:
        return url
    try:
        p = urllib.parse.urlsplit(url, allow_fragments=False)
    except ValueError:
        return OCULTO
    if _interna(p):
        return url                      # de una pieza del hub a otra: así el registro dice qué path se lee
    ruta = p.path
    if tipo == "rtmp" and not clave and clave_en_ruta(ruta):
        sin_barra = ruta.rstrip("/")
        ruta = sin_barra.rpartition("/")[0] + "/" + OCULTO + ruta[len(sin_barra):]
    return urllib.parse.urlunsplit((p.scheme, p.netloc.rpartition("@")[2], ruta, OCULTO if p.query else "", ""))


def valores_de(fila):
    """Valores de una fila (flujo, salida, fuente, destino o {"url": …}) que no deben verse."""
    valores = {fila.get(k) or "" for k in ("streamid", "passphrase", "clave", "token", "password")}
    url = fila.get("url") or ""
    try:
        partes = urllib.parse.urlsplit(url, allow_fragments=False)
        valores.update(v for k, v in urllib.parse.parse_qsl(partes.query) if k not in PARAMS_NO_SECRETOS)
        valores.add(partes.password or "")
        ruta = partes.path
    except ValueError:                  # URL que no se puede analizar: se usa tal cual
        ruta = url.split("?")[0]
    if (fila.get("tipo") or fila.get("protocolo")) == "rtmp" and not fila.get("clave"):
        valores.add(clave_en_ruta(ruta))
    return {v for v in valores if isinstance(v, str)}


def ordenar(valores):
    """Lista lista para ocultar(): también codificados como van en una URL, y los largos primero
    (si una clave contiene a otra, se quita antes la larga)."""
    valores = {v for v in valores if v and len(v) >= MINIMO}
    valores |= {urllib.parse.quote_plus(v) for v in valores} | {urllib.parse.quote(v, safe="") for v in valores}
    return sorted(valores, key=len, reverse=True)


def de_filas(flujos, salidas, *mas):
    """Valores guardados que no deben aparecer en un texto, también codificados como van en una
    URL (mtx.conf_de_flujo los codifica, y un error de MediaMTX puede repetir esa URL)."""
    import db
    valores = set()
    respaldos = []
    for f in flujos:                        # hub: el origen de respaldo (Castr) lleva la clave en el streamid
        respaldos += [{"url": u} for u in db.fuentes({"url": "", "respaldos": f.get("respaldos")})]
    respaldos += [{"url": c["url"]} for c in db.canales_recorder() if c.get("url")]   # canal sin reenvío
    for fila in [*flujos, *salidas, *respaldos, *(f for grupo in mas for f in grupo)]:
        valores |= valores_de(fila)
    return ordenar(valores)


def _url_en_texto(m):
    url = m.group(0).rstrip(":,.;)]")                   # "rtmp://…/clave: Connection refused"
    tipo = "rtmp" if m.group(1).startswith("rtmp") else m.group(1)
    return url_visible(url, tipo, "") + m.group(0)[len(url):]


def ocultar(texto, secretos=()):
    """El texto sin los valores secretos y con toda URL como la vería quien no puede ver claves.
    Lo segundo cubre las claves que ya no están en la base (un destino borrado o editado)."""
    if not texto:
        return texto
    for s in secretos:
        texto = texto.replace(s, OCULTO)
    return URL_EN_TEXTO.sub(_url_en_texto, texto)


def ocultar_comando(cmd, secretos=()):
    """Línea de arranque que sí puede ir al registro: el comando con cada clave como «***». Empieza
    por SIN_CLAVES, que la distingue de las que escribía la 0.3.9 con las claves a la vista."""
    visibles, tapar = [], False
    for a in cmd:
        visibles.append(OCULTO if tapar else a)
        tapar = a in OPCIONES_CON_CLAVE
    return SIN_CLAVES + ocultar(" ".join(visibles), secretos)


def limpiar(texto, secretos):
    """Error o texto del registro sin la línea de arranque de ffmpeg ni valores secretos. Hasta la
    0.3.9 esa línea se escribía entera en el registro, y esos archivos siguen en disco."""
    if not texto:
        return texto
    m = LINEA_ARRANQUE.search(texto)
    if m:
        texto = texto[:m.start()] + "(línea de arranque oculta)"
    elif any(o in texto for o in OPCIONES_SECRETAS):
        return "(línea de arranque oculta)"
    return ocultar(texto, secretos)


def limpiar_registro(ruta, secretos=()):
    """Reescribe un registro antiguo sin claves (las líneas de arranque que escribía la 0.3.9 y lo que
    ffmpeg repitió de ellas). Devuelve True si cambió algo. El archivo puede estar abierto por un
    proceso que sigue al aire: escribe siempre al final, así que basta con reescribir lo anterior."""
    try:
        with open(ruta, "r+b") as f:
            antes = f.read()
            lineas = []
            for linea in antes.decode(errors="replace").split("\n"):
                m = LINEA_ARRANQUE.match(linea)
                if m and linea[m.end():].startswith(SIN_CLAVES):
                    pass                    # la escribió el lanzador: ya va sin claves
                elif m:                     # se conserva la fecha; el resto llevaba todas las claves
                    linea = m.group(0) + "(línea de arranque de una versión anterior, oculta)"
                else:
                    linea = ocultar(linea, secretos)
                lineas.append(linea)
            despues = "\n".join(lineas).encode()
            if despues == antes:
                return False
            f.seek(0)
            f.truncate()
            f.write(despues)
            return True
    except OSError:
        return False
