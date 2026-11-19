"""Media Syntaxis Streamer - estados del hub, con la evidencia que los respalda.

Seis estados, los mismos para todo el hub:
  esperando_fuente    no llega señal de la fuente (y no es culpa del hub);
  recibiendo          llegan medios de la fuente;
  enviando            la señal está saliendo hacia el destino;
  esperando_receptor  el hub ofrece la señal y nadie ha venido a por ella;
  reconectando        el hub está intentando conectar (con el origen o con el destino) y reintenta solo;
  error               no va a funcionar sin que alguien cambie algo.
Más uno que no es de operación: desactivada.

Cada estado va con su evidencia: los hechos comprobados de los que sale (qué dice MediaMTX, qué hace
el proceso de ffmpeg, cuántos receptores hay) y el instante en que se comprobaron. Aquí no se
consulta nada: son funciones puras sobre datos ya leídos, que es lo que permite probarlas.
"""

import re
import time

ESPERANDO_FUENTE, RECIBIENDO, ENVIANDO = "esperando_fuente", "recibiendo", "enviando"
ESPERANDO_RECEPTOR, RECONECTANDO, ERROR, DESACTIVADA = "esperando_receptor", "reconectando", "error", "desactivada"
NOMBRES = {ESPERANDO_FUENTE: "Esperando fuente", RECIBIENDO: "Recibiendo medios", ENVIANDO: "Enviando",
           ESPERANDO_RECEPTOR: "Esperando receptor", RECONECTANDO: "Reconectando", ERROR: "Error",
           DESACTIVADA: "Desactivada"}
FUENTE_PARADA = "La fuente está desactivada"     # el mismo texto que matriz.FUENTE_PARADA
ESPERA_LISTA = "Esperando a que la lista HLS"   # el mismo texto que espera_hls.ESPERA
CONVERSION_ROTA = 3             # fallos seguidos de una conversión para dejar de llamarlo «reconectando»
CADUCA = 15.0                   # segundos sin noticias del supervisor para avisar de que el dato es viejo

# Lo que suele decir ffmpeg cuando no puede conectar, en claro. Se mira de la última línea hacia atrás.
MOTIVOS = (
    (r"Connection refused", "El otro equipo rechaza la conexión"),
    (r"timed out|Timeout", "El otro equipo no responde"),
    (r"No route to host|Network is unreachable", "No hay ruta de red hasta el otro equipo"),
    (r"Name or service not known|Failed to resolve|No address associated", "No se encuentra el nombre del servidor"),
    (r"401|403|Unauthorized|Forbidden|denied|[Aa]ccess|bad (name|auth)|NetStream\.Publish", "El otro equipo no acepta la clave"),
    (r"404|Not Found|StreamNotFound", "El otro equipo no tiene esa señal"),
    (r"srt.*(failed|refused)|Connection to srt", "No se pudo establecer la conexión SRT"),
    (r"Broken pipe|Connection reset|End of file|Error closing file|I/O error|Input/output error", "Se cortó la conexión"),
)


def explicar(error):
    """El error de ffmpeg dicho de forma que un operador sepa por dónde empezar. Texto original
    incluido, para quien quiera el detalle."""
    if not error:
        return ""
    for patron, motivo in MOTIVOS:
        if re.search(patron, error):
            return motivo
    return ""


def _hecho(que, desde=None):
    return {"que": que, "desde": desde}


def _estado(estado, detalle, evidencia, actualizado, **mas):
    return {"estado": estado, "nombre": mas.pop("nombre", None) or NOMBRES[estado], "detalle": detalle,
            "evidencia": [h for h in evidencia if h], "actualizado": actualizado, **mas}


def _fallo(tarea):
    """Hecho con el último fallo del proceso de una tarea, o None."""
    error = (tarea or {}).get("error") or ""
    if not error or error.startswith(("Esperando", "Reintentando")):
        return None
    if error.startswith("Sin más intentos"):        # «Sin más intentos tras 20 fallos seguidos: <lo de ffmpeg>»
        error = error.partition(": ")[2] or error
    if error.startswith(("MediaMTX no responde", "No se pudo lanzar")):
        return _hecho(error)                        # no lo dijo ffmpeg: lo anotó el supervisor
    motivo = explicar(error)
    return _hecho(f"Último intento: {motivo}. ffmpeg dijo: {error}" if motivo else f"Último intento, ffmpeg dijo: {error}")


def _visto(tarea, ahora):
    """Cuándo se comprobó lo que se sabe de un proceso: en la última vuelta del supervisor."""
    return (tarea or {}).get("visto") or ahora


def _intentos(tarea):
    n = (tarea or {}).get("intentos") or 0
    return _hecho(f"Intentos seguidos sin conseguirlo: {n}") if n else None


def _agotada(tarea):
    return ((tarea or {}).get("error") or "").startswith("Sin más intentos")


def en_reposo(desde=None, ahora=None):
    """La estación está en reposo («Apagar servicio» en el Recorder): el hub no mueve ninguna señal."""
    return _estado(DESACTIVADA, "La estación está en reposo: el hub no mueve señales hasta que se encienda el servicio",
                   [_hecho("El Recorder de este equipo tiene el servicio apagado", desde)], ahora or time.time(),
                   nombre="En reposo")


def al_aire(estado):
    """Una ruta está al aire cuando su fuente tiene señal y la ruta la está sacando o intentándolo."""
    return bool(estado) and estado["estado"] in (ENVIANDO, ESPERANDO_RECEPTOR, RECONECTANDO)


def caducado(tarea, ahora):
    """El supervisor lleva demasiado sin pasar por esta tarea: lo que se sabe de ella es viejo."""
    visto = (tarea or {}).get("visto")
    return bool(tarea) and (not visto or ahora - visto > CADUCA)


def _sin_servidor(servidor, extra, ahora, mientras=RECONECTANDO, luego=""):
    """El servidor de MediaMTX por el que entra o sale la señal (RTMP, WebRTC) no está encendido.
    servidor: {"nombre", "motivo"}. Con motivo no se va a encender solo (el puerto es de otro
    programa): error. Sin motivo, el supervisor lo enciende en su próxima vuelta."""
    nombre = servidor["nombre"]
    if servidor.get("motivo"):
        return _estado(ERROR, f"El servidor {nombre} del hub no se puede encender",
                       [_hecho(f"{servidor['motivo']}. Elige otro puerto en Ajustes o libera ese"), *extra], ahora)
    return _estado(mientras, f"El hub está encendiendo su servidor {nombre}" + luego,
                   [_hecho(f"MediaMTX aún no tiene encendido su servidor {nombre}"), *extra], ahora)


def de_fuente(f, info=None, tarea=None, senal=None, kbps=None, mtx_ok=True, impedimento=None, origen="",
              ahora=None, sin_senal="Nadie está publicando en esta fuente", servidor=None):
    """Estado de una fuente.
    info: lo que dice MediaMTX de su path (None: no existe). tarea: fila de estado_salidas de su
    proceso de entrada, si lo lleva. senal: fila de `senales` (cuándo hubo señal por última vez).
    kbps: tasa de entrada medida. impedimento: por qué no puede correr su proceso (adaptador, datos).
    origen: quién publica, si se sabe («203.0.113.9 por RTMP»). sin_senal: cómo decir que no hay
    señal cuando no la trae un proceso del hub (un flujo heredado lo dice a su manera).
    servidor: {"nombre", "motivo"} si el origen publica en un servidor del hub que no está encendido."""
    ahora = ahora or time.time()
    ultima = (senal or {}).get("ultima")
    antes = _hecho("Última señal recibida", ultima) if ultima else _hecho("Aún no se ha recibido señal de esta fuente")
    if not f["activo"]:
        return _estado(DESACTIVADA, "La fuente está desactivada", [antes if ultima else None], ahora)
    if impedimento:
        return _estado(ERROR, impedimento, [antes if ultima else None], ahora)
    if not mtx_ok:
        return _estado(ERROR, "MediaMTX no responde", [_hecho("La API de MediaMTX no contesta en este equipo")], ahora)
    if servidor and not (info and info.get("ready")):
        return _sin_servidor(servidor, [antes], ahora, mientras=ESPERANDO_FUENTE, luego=": en unos segundos se podrá publicar")
    if info and info.get("ready"):
        pistas = ", ".join(info.get("tracks") or []) or "sin pistas"
        hechos = [_hecho("MediaMTX recibe la señal" + (f" de {origen}" if origen else ""), info.get("listo_desde")),
                  _hecho(f"Pistas: {pistas}"),
                  _hecho(f"Entran {round(kbps):,} kb/s".replace(",", " ")) if kbps else
                  _hecho("Aún sin medida de bitrate (hacen falta dos lecturas)")]
        return _estado(RECIBIENDO, "Llegan medios de la fuente", hechos, ahora)
    if tarea is not None:                       # el hub va a buscarla (o escucha en un puerto propio)
        visto = _visto(tarea, ahora)
        if _agotada(tarea):
            return _estado(ERROR, "El hub dejó de intentarlo", [_fallo(tarea), _intentos(tarea), antes], visto)
        if tarea.get("pid"):
            if f["inicia"] == "origen":
                return _estado(ESPERANDO_FUENTE, "El hub escucha y nadie ha llamado todavía",
                               [_hecho("El proceso de entrada escucha en el puerto de la fuente", tarea.get("desde")), antes], visto)
            if (tarea.get("error") or "").startswith(ESPERA_LISTA):    # lo dice el propio proceso de entrada
                return _estado(RECONECTANDO if ultima else ESPERANDO_FUENTE,
                               "El origen ya sirve la lista y el hub espera a que tenga margen: leída desde su "
                               "primer segmento, la señal saldría a golpes",
                               [_hecho(tarea["error"], tarea.get("desde")), antes], visto)
            return _estado(RECONECTANDO if ultima else ESPERANDO_FUENTE, "El hub está conectando con el origen",
                           [_hecho("El proceso de entrada está en marcha y aún no publica", tarea.get("desde")),
                            _intentos(tarea), antes], visto)
        if ultima:
            return _estado(RECONECTANDO, "Se perdió la señal y el hub reintenta la conexión con el origen",
                           [_fallo(tarea), _intentos(tarea), antes], visto)
        return _estado(ESPERANDO_FUENTE, "El hub intenta conectar con el origen y aún no lo ha conseguido",
                       [_fallo(tarea), _intentos(tarea), antes], visto)
    return _estado(ESPERANDO_FUENTE, sin_senal, [_hecho("MediaMTX no tiene a nadie publicando en su path"), antes], ahora)


def de_ruta(d, plan=None, fuente=None, senal_lista=True, conversion=None, tarea=None, info_destino=None,
            lectores=(), kbps=None, mtx_ok=True, impedimento=None, tipo="empuja", ahora=None, servidor=None):
    """Estado de una ruta (una fuente conectada a un destino).
    plan: el de plan.planear. fuente: el estado de su fuente (de_fuente). senal_lista: la señal que
    lee la ruta (la fuente o una derivada) está publicada. conversion: fila de estado_salidas de la
    conversión, si la ruta usa una señal derivada. tarea: la del proceso de envío. info_destino: lo
    que dice MediaMTX del path del destino. lectores: receptores conectados a ese path.
    tipo: 'empuja' (el hub se conecta), 'entrega' (el receptor viene), 'whip' o 'adaptador'.
    servidor: {"nombre", "motivo"} si la señal sale por un servidor del hub que no está encendido."""
    ahora = ahora or time.time()
    if impedimento:
        apagada = impedimento == FUENTE_PARADA
        return _estado(ESPERANDO_FUENTE if apagada else ERROR, impedimento, [], ahora)
    if not mtx_ok:
        return _estado(ERROR, "MediaMTX no responde", [_hecho("La API de MediaMTX no contesta en este equipo")], ahora)
    if servidor and servidor.get("motivo"):         # no se arregla solo: se dice aunque la fuente aún no tenga señal
        return _sin_servidor(servidor, [], ahora)
    if fuente and fuente["estado"] != RECIBIENDO:
        return _estado(ESPERANDO_FUENTE, "La fuente no tiene señal",
                       [_hecho(f"Fuente: {fuente['nombre'].lower()}. {fuente['detalle']}")], ahora)
    if plan is not None and not plan.get("posible", True):
        return _estado(ERROR, plan.get("resumen") or "Esta fuente no se puede llevar a este destino", [], ahora)
    if plan is not None and plan.get("provisional"):
        return _estado(ESPERANDO_FUENTE, "Se está inspeccionando la señal de la fuente",
                       [_hecho(plan.get("resumen") or "Aún no se sabe qué trae la fuente")], ahora)
    if not senal_lista:                          # la ruta lee una señal derivada que aún no se publica
        rota = ((conversion or {}).get("intentos") or 0) >= CONVERSION_ROTA
        if conversion and conversion.get("pid"):
            return _estado(RECONECTANDO, "La conversión está arrancando",
                           [_hecho("El proceso de conversión está en marcha y aún no publica", conversion.get("desde"))],
                           _visto(conversion, ahora))
        return _estado(ERROR if rota else RECONECTANDO,
                       "La conversión no consigue arrancar" if rota else "La conversión se está relanzando",
                       [_fallo(conversion), _intentos(conversion)], _visto(conversion, ahora))
    convierte = _hecho("Lee la señal convertida, que se está publicando") if conversion is not None else None

    if tipo in ("empuja", "adaptador"):
        if tarea is None:
            return _estado(RECONECTANDO, "El supervisor aún no ha pasado por esta ruta", [convierte], ahora)
        visto = _visto(tarea, ahora)
        if _agotada(tarea):
            return _estado(ERROR, "El hub dejó de intentarlo: pulsa Reintentar cuando el destino esté listo",
                           [_fallo(tarea), _intentos(tarea)], visto)
        if tarea.get("pid") and tarea.get("kbps"):
            hechos = [_hecho(f"El proceso de envío manda {round(tarea['kbps']):,} kb/s".replace(",", " "), tarea.get("desde")),
                      _hecho(f"Reconexiones desde que se activó: {tarea.get('reinicios') or 0}"), convierte]
            return _estado(ENVIANDO, "La señal está saliendo hacia el destino", hechos, visto)
        if tarea.get("pid"):
            if d["inicia"] == "receptor":       # SRT con puerto propio: el hub escucha
                return _estado(ESPERANDO_RECEPTOR, "El hub escucha y el receptor aún no ha llamado",
                               [_hecho("El proceso de envío escucha en el puerto del destino", tarea.get("desde")), convierte], visto)
            return _estado(RECONECTANDO, "El hub está conectando con el destino",
                           [_hecho("El proceso de envío está en marcha y aún no manda datos", tarea.get("desde")),
                            _intentos(tarea), convierte], visto)
        if not _fallo(tarea) and not tarea.get("intentos"):     # aún no se ha intentado: va a arrancar
            return _estado(RECONECTANDO, "El envío está a punto de arrancar", [convierte], visto)
        return _estado(RECONECTANDO, "El destino no responde y el hub reintenta",
                       [_fallo(tarea), _intentos(tarea), convierte], visto)

    # el hub sirve la señal desde el path del destino
    if servidor:                                # la señal puede estar lista, pero nadie podría abrirla todavía
        return _sin_servidor(servidor, [convierte], ahora)
    if not (info_destino and info_destino.get("ready")):
        return _estado(RECONECTANDO, "El hub está preparando la señal del destino",
                       [_hecho("MediaMTX aún no tiene la señal en el path del destino"), convierte], ahora)
    listo = _hecho("MediaMTX tiene la señal en el path del destino", info_destino.get("listo_desde"))
    if tipo == "whip":
        return _estado(ENVIANDO, "MediaMTX reenvía la señal al destino por WHIP",
                       [listo, _hecho("MediaMTX no informa de si el destino la recibe: compruébalo en el destino"), convierte],
                       ahora, sin_confirmar=True)
    if lectores:
        quienes = ", ".join(sorted({x.get("remoto") or x.get("tipo") or "?" for x in lectores})[:4])
        hechos = [_hecho(f"Receptores conectados: {len(lectores)} ({quienes})"),
                  _hecho(f"Salen {round(kbps):,} kb/s".replace(",", " ")) if kbps else None, listo, convierte]
        return _estado(ENVIANDO, "Hay receptores leyendo la señal", hechos, ahora)
    return _estado(ESPERANDO_RECEPTOR, "La dirección está lista y nadie la ha abierto",
                   [listo, _hecho("MediaMTX no tiene receptores en el path del destino"), convierte], ahora)
