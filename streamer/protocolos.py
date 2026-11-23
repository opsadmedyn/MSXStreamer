"""Media Syntaxis Streamer - qué admite cada protocolo y quién llama a quién.

Dos preguntas distintas que el panel enseña por separado:
  - quién inicia la conexión: el hub o el otro equipo;
  - en qué dirección viaja la señal: siempre hacia el hub en una fuente y desde el hub en un destino.

En SRT son independientes (un «listener» puede enviar o recibir), y de ahí salen los cuatro casos:
  fuente  + inicia el origen  -> el codificador llama al hub (el hub escucha) y le envía la señal;
  fuente  + inicia el hub     -> el hub llama al origen y la señal viene hacia el hub;
  destino + inicia el hub     -> el hub llama al receptor y le envía la señal;
  destino + inicia el receptor-> el receptor llama al hub (el hub escucha) y se lleva la señal.

Los códecs de las tablas son los que cada salida acepta tal como llegan, comprobados en el banco
de pruebas con MediaMTX 1.21.1 (ver docs/banco-de-pruebas.md). Lo que no está aquí se convierte.
"""

PROTOCOLOS = ("srt", "rtmp", "hls", "webrtc", "ndi", "zixi")
NOMBRES = {"srt": "SRT", "rtmp": "RTMP", "hls": "HLS", "webrtc": "WebRTC", "ndi": "NDI", "zixi": "Zixi"}
CON_ADAPTADOR = ("ndi", "zixi")         # no funcionan sin su adaptador instalado y con licencia

# Fuentes. 'origen': el origen llama al hub. 'hub': el hub llama al origen.
ENTRADAS = {
    "srt": ("origen", "hub"),           # el hub escucha (listener) / el hub llama (caller)
    "rtmp": ("origen", "hub"),          # un codificador publica en el hub / el hub lee un servidor
    "hls": ("hub",),                    # el hub lee una lista .m3u8
    "webrtc": ("origen",),              # un navegador o codificador publica por WHIP
    "ndi": ("hub",),                    # el hub se suscribe a una fuente NDI de la red local
    "zixi": ("origen", "hub"),          # según el modo que habilite la licencia
}

# Destinos. 'hub': el hub se conecta al destino. 'receptor': el hub entrega una dirección.
SALIDAS = {
    "srt": ("hub", "receptor"),         # el hub llama (caller) / el receptor llama al hub (listener)
    "rtmp": ("hub", "receptor"),        # el hub publica con URL y clave / un cliente lee del hub
    "hls": ("receptor",),               # el hub sirve la lista y los segmentos
    "webrtc": ("receptor", "hub"),      # reproducción por WHEP / reenvío a un destino WHIP
    "ndi": ("receptor",),               # el hub publica una fuente NDI y los receptores la abren
    "zixi": ("hub", "receptor"),
}

# Nombres de códec: los de MediaMTX (tracks2) y los de ffprobe, a uno solo en minúsculas.
_CODECS = {
    "h264": "h264", "avc": "h264", "avc1": "h264",
    "h265": "h265", "hevc": "h265", "hvc1": "h265",
    "av1": "av1", "vp9": "vp9", "vp8": "vp8",
    "mpeg-4 video": "mpeg4", "mpeg4": "mpeg4",
    "mpeg-1/2 video": "mpeg2video", "mpeg2video": "mpeg2video", "mpeg1video": "mpeg2video",
    "m-jpeg": "mjpeg", "mjpeg": "mjpeg",
    "mpeg-4 audio": "aac", "mpeg-4 audio latm": "aac", "aac": "aac", "aac_latm": "aac",
    "opus": "opus",
    "mpeg-1/2 audio": "mp2", "mp2": "mp2", "mp3": "mp2",
    "ac-3": "ac3", "ac3": "ac3", "eac3": "eac3",
    "g711": "g711", "pcm_alaw": "g711", "pcm_mulaw": "g711",
    "lpcm": "lpcm",
}
VIDEO = ("h264", "h265", "av1", "vp9", "vp8", "mpeg4", "mpeg2video", "mjpeg")
NOMBRE_CODEC = {"h264": "H.264", "h265": "H.265", "av1": "AV1", "vp9": "VP9", "vp8": "VP8",
                "mpeg4": "MPEG-4", "mpeg2video": "MPEG-2", "mjpeg": "M-JPEG", "aac": "AAC",
                "opus": "Opus", "mp2": "MPEG audio", "ac3": "AC-3", "eac3": "E-AC-3",
                "g711": "G.711", "lpcm": "PCM"}


def codec(nombre):
    """Nombre de códec normalizado; lo desconocido sale tal cual en minúsculas (y se convertirá)."""
    n = (nombre or "").strip().lower()
    return _CODECS.get(n, n)


def nombre_codec(c):
    return NOMBRE_CODEC.get(c, (c or "?").upper())


# Lo que cada salida acepta sin convertir. En WebRTC el H.264 solo vale sin fotogramas B
# (MediaMTX cierra la sesión: «WebRTC doesn't support H264 streams with B-frames»).
VIDEO_ACEPTA = {
    "srt": ("h264", "h265", "mpeg4", "mpeg2video"),     # lo que cabe en MPEG-TS
    "rtmp": ("h264",),
    "hls": ("h264", "h265"),
    "webrtc": ("h264", "vp8", "vp9", "av1"),
}
AUDIO_ACEPTA = {
    "srt": ("aac", "opus", "mp2", "ac3"),
    "rtmp": ("aac",),
    "hls": ("aac",),
    "webrtc": ("opus",),
}
AUDIO_PREFERIDO = {"srt": "aac", "rtmp": "aac", "hls": "aac", "webrtc": "opus"}
# Las piezas del hub se pasan la señal por el SRT de MediaMTX (MPEG-TS). Lo que no cabe ahí no llega
# de la fuente a una conversión ni a un destino: ni se puede copiar ni se puede convertir.
ENLACE_VIDEO = VIDEO_ACEPTA["srt"]
ENLACE_AUDIO = AUDIO_ACEPTA["srt"]
VARIAS_PISTAS = {"srt": True, "hls": True, "rtmp": False, "webrtc": False}
SIN_FOTOGRAMAS_B = ("webrtc",)
CANALES_MAX = {"webrtc": 2}             # Opus por WebRTC: mono o estéreo


def valida_entrada(protocolo, inicia):
    return inicia in ENTRADAS.get(protocolo, ())


def valida_salida(protocolo, inicia):
    return inicia in SALIDAS.get(protocolo, ())


def quien_inicia(lado, protocolo, inicia):
    """Texto de «quién inicia la conexión» para una fuente ('fuente') o un destino ('destino')."""
    if lado == "fuente":
        if inicia == "hub":
            return {"srt": "El hub llama al origen", "rtmp": "El hub lee del servidor de origen",
                    "hls": "El hub lee la lista del origen", "ndi": "El hub se suscribe a la fuente NDI",
                    "zixi": "El hub llama al origen"}.get(protocolo, "El hub")
        return {"srt": "El codificador llama al hub", "rtmp": "El codificador publica en el hub",
                "webrtc": "El navegador o codificador publica en el hub (WHIP)",
                "zixi": "El origen llama al hub"}.get(protocolo, "El origen")
    if inicia == "hub":
        return {"srt": "El hub llama al receptor", "rtmp": "El hub publica en el servidor del destino",
                "webrtc": "El hub publica en el destino (WHIP)",
                "zixi": "El hub llama al receptor"}.get(protocolo, "El hub")
    return {"srt": "El receptor llama al hub", "rtmp": "El cliente lee del hub",
            "hls": "El reproductor pide la lista al hub", "webrtc": "El navegador pide la señal al hub (WHEP)",
            "ndi": "Los receptores NDI abren la fuente del hub",
            "zixi": "El receptor llama al hub"}.get(protocolo, "El receptor")


def direccion(lado):
    """Texto de «en qué dirección viaja la señal»: no depende de quién llame."""
    return "Hacia el hub" if lado == "fuente" else "Desde el hub"
