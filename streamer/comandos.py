"""Media Syntaxis Streamer - comandos de ffmpeg de cada pieza de una ruta.

Tres piezas, todas vigiladas por el supervisor igual que una salida de siempre:
  - entrada:    el hub va a buscar la fuente (SRT, RTMP, HLS) y la publica en su path de MediaMTX;
  - conversión: lee una fuente y publica una «señal derivada» (otro perfil, otro audio) en su path;
  - envío:      lee una señal (la fuente o una derivada) y la lleva en copia a un destino al que el
                hub se conecta (SRT, RTMP).
Lo que el hub entrega para que otro equipo lo abra (SRT, RTMP, HLS, WebRTC) no lleva proceso: lo
sirve MediaMTX desde el path del destino (ver matriz.py).

Las claves van como opciones aparte (-passphrase, -srt_streamid) y no dentro de la URL siempre que
ffmpeg lo permite: llegan tal cual aunque lleven "#", "," o "=". En RTMP la clave va en la URL, como
hasta ahora; de que no acabe en el registro se encarga el lanzador (correr.py).
"""

import json
import urllib.parse

import db
import mtx

FFMPEG = db.config().get("ffmpeg", "ffmpeg")
RUN = db.BASE / "run"
LATENCIA_LOCAL = "&latency=200000"          # lectura local para convertir: 200 ms, como la composición
# Lo que el hub lee de su propio MediaMTX trae siempre un solo programa. Por defecto ffmpeg sigue
# buscando otros durante 5 s antes de arrancar; así arranca en cuanto conoce las pistas (en el banco,
# 1,3 s en vez de 5,4): menos espera al activar una ruta y en cada reintento. Solo para lo que viene
# del propio hub: la señal de un origen puede traer varios programas y se abre como siempre.
LECTURA_LOCAL = ["-scan_all_pmts", "0"]
PAQUETE = "&pkt_size=1316"
HLS_DESDE = "-3"                            # por qué segmento se empieza a leer una fuente HLS (ver entrada)


def url_salida(s):
    """URL de destino y opciones de ffmpeg que van aparte.

    El streamid y la passphrase van como opciones (-srt_streamid, -passphrase) y no dentro de la
    URL: así llegan tal cual aunque lleven "#", "," o "=" (el streamid de Castr es "#!::r=…,password=…")
    y no dependen de que la versión de ffmpeg decodifique la URL."""
    if s["tipo"] == "rtmp":
        base = s["url"].rstrip("/")
        return (f"{base}/{s['clave']}" if s["clave"] else base), []
    # srt: la latencia se expresa en microsegundos. Sin fragmentos: un "#" pegado en la URL
    # (el streamid de Castr) es parte del valor, no un ancla.
    partes = urllib.parse.urlsplit(s["url"], allow_fragments=False)
    q = dict(urllib.parse.parse_qsl(partes.query))
    streamid = s["streamid"] or q.pop("streamid", "")
    passphrase = s["passphrase"] or q.pop("passphrase", "")
    q.pop("streamid", None); q.pop("passphrase", None)
    q["mode"] = "listener" if s["modo"] == "listener" else "caller"
    q["latency"] = str(int(s["latencia_ms"]) * 1000)
    q["pkt_size"] = "1316"
    host = partes.netloc
    if s["modo"] == "listener":             # escucha en todas las interfaces, en el puerto indicado
        host = "0.0.0.0:" + (partes.port and str(partes.port) or "9000")
    opciones = []
    if passphrase:
        opciones += ["-passphrase", passphrase]
    if streamid:
        opciones += ["-srt_streamid", streamid]
    return urllib.parse.urlunsplit(("srt", host, "", urllib.parse.urlencode(q), "")), opciones


def entrada_directa(url):
    """Opciones de ffmpeg y URL para leer la entrada sin MediaMTX (flujo 1080i directo). Como en
    url_salida, el streamid va aparte: el de Castr lleva "#", "," y "="."""
    partes = urllib.parse.urlsplit(url, allow_fragments=False)
    q = dict(urllib.parse.parse_qsl(partes.query))
    opciones = ["-srt_streamid", q.pop("streamid")] if q.get("streamid") else []
    if q.get("passphrase"):
        opciones += ["-passphrase", q.pop("passphrase")]
    return opciones, urllib.parse.urlunsplit(partes._replace(query=urllib.parse.urlencode(q)))


def _base(tid):
    return [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning",
            "-progress", str(RUN / f"{tid}.prog"), "-stats_period", "2"]


# ---------------------------------------------------------------- entrada: el hub va a por la fuente
def entrada(tid, f, path):
    """ffmpeg que trae una fuente y la publica en `path`. Solo las fuentes que lo necesitan: aquellas
    a las que el hub se conecta, y las SRT que llaman a un puerto propio del hub."""
    cmd = _base(tid)
    opciones = _opciones(f)
    if f["protocolo"] == "srt":
        partes = urllib.parse.urlsplit(f["url"], allow_fragments=False)
        q = dict(urllib.parse.parse_qsl(partes.query))
        streamid = f["streamid"] or q.get("streamid", "")
        passphrase = f["passphrase"] or q.get("passphrase", "")
        q.pop("streamid", None); q.pop("passphrase", None)
        q["latency"] = str(int(f["latencia_ms"]) * 1000)
        if f["inicia"] == "hub":
            q["mode"], host = "caller", partes.netloc
        else:                               # el codificador llama a un puerto propio de esta fuente
            q["mode"], host = "listener", f"0.0.0.0:{int(opciones['puerto'])}"
        if passphrase:
            cmd += ["-passphrase", passphrase]
        if streamid and f["inicia"] == "hub":
            cmd += ["-srt_streamid", streamid]
        cmd += ["-i", urllib.parse.urlunsplit(("srt", host, "", urllib.parse.urlencode(q), ""))]
        mapas = ["-map", "0:v:0?", "-map", "0:a?"]
    elif f["protocolo"] == "rtmp":
        base = f["url"].rstrip("/")
        cmd += ["-rw_timeout", "10000000", "-i", f"{base}/{f['clave']}" if f["clave"] else base]
        mapas = ["-map", "0:v:0?", "-map", "0:a:0?"]
    elif f["protocolo"] == "hls":
        # -re: al ritmo de la señal y no a golpes de segmento. Y tres segmentos por detrás del directo,
        # como un reproductor: empezando por el último no queda colchón, cada segmento llega tarde y
        # sale de golpe (en el banco, el 35 % del tiempo no salía nada, con parones de 0,9 s, y un
        # navegador lo veía por WebRTC a 24 fps). Con dos por detrás, el 6 %; con tres sale continuo
        cmd += ["-rw_timeout", "15000000", "-re", "-live_start_index", HLS_DESDE, "-i", f["url"]]
        mapas = ["-map", f"0:p:{int(opciones.get('variante') or 0)}"]    # la variante elegida, con sus audios
    else:
        raise ValueError(f"la entrada {f['protocolo']} no lleva proceso")
    return cmd + mapas + ["-c", "copy", "-ignore_unknown", "-f", "mpegts", mtx.url_publicacion(path) + PAQUETE]


def _opciones(fila):
    try:
        o = json.loads(fila.get("opciones") or "{}")
    except ValueError:
        return {}
    return o if isinstance(o, dict) else {}


def con_entrada(f):
    """¿La fuente necesita un ffmpeg que la traiga? Las que publican en el hub por su cuenta no."""
    if f["protocolo"] in ("rtmp", "hls"):
        return f["inicia"] == "hub"
    if f["protocolo"] == "srt":
        return f["inicia"] == "hub" or bool(_opciones(f).get("puerto"))
    return False


# ---------------------------------------------------------------- conversión: una señal derivada
def codificador_video(nombre, kbps, fps=None):
    """Opciones del codificador H.264. Sin fotogramas B (WebRTC no los admite y así la misma señal
    sirve a todos los destinos) y con un fotograma clave cada 2 s."""
    g = str(max(24, round((fps or 30) * 2)))
    if nombre == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr", "-profile:v", "high",
                "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k", "-g", g, "-bf", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-profile:v", "high",
            "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k", "-g", g, "-bf", "0"]


def conversion(tid, path_fuente, senal, path_senal, pistas=None, codificador="libx264"):
    """ffmpeg que lee la fuente y publica la señal derivada que describe `senal` (ver plan.planear).
    Lo que la señal marca como copia no se toca; el resto se codifica una sola vez para todas las
    rutas que la usan."""
    cmd = _base(tid) + LECTURA_LOCAL + ["-thread_queue_size", "512", "-i", mtx.url_lectura(path_fuente) + LATENCIA_LOCAL]
    v = senal.get("video")
    origen = (pistas or {}).get("video") or {}
    con_video = isinstance(v, dict)
    if v == "copia":
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    elif con_video:
        filtros = []
        if origen.get("entrelazado"):       # un fotograma por cuadro: mantiene los fps de la fuente
            filtros.append("yadif=mode=send_frame:parity=auto:deint=interlaced")
        # siempre a la medida de salida, aunque la fuente ya la traiga (entonces no cuesta nada): un
        # origen WebRTC cambia de tamaño sobre la marcha y el codificador tiene que recibir siempre el mismo
        filtros += [f"scale={v['ancho']}:{v['alto']}", "setsar=1", "format=yuv420p"]
        cmd += ["-map", "0:v:0", "-vf", ",".join(filtros), *codificador_video(codificador, v["kbps"], origen.get("fps"))]
    for n, t in enumerate(senal.get("audio") or []):
        cmd += ["-map", f"0:a:{t['origen']}"]
        if t["codec"] == "copia":
            cmd += [f"-c:a:{n}", "copy"]
        else:
            cmd += [f"-c:a:{n}", "libopus" if t["codec"] == "opus" else "aac", f"-b:a:{n}", f"{t['kbps']}k",
                    f"-ac:a:{n}", str(t["canales"]), f"-ar:a:{n}", "48000"]
    cmd += ["-f", "mpegts", mtx.url_publicacion(path_senal) + PAQUETE]
    # codificar vídeo cede el paso a lo que va en copia y al Recorder si falta CPU
    return ["nice", "-n", "5", *cmd] if con_video else cmd


# ---------------------------------------------------------------- envío: el hub se conecta al destino
def envio(tid, d, path_senal, mapa_audio=None, entera=True):
    """ffmpeg que lleva la señal de `path_senal`, en copia, al destino `d` (SRT o RTMP).
    mapa_audio: pistas de la señal que van, por orden. entera: van todas las pistas tal cual."""
    cmd = _base(tid) + LECTURA_LOCAL + ["-i", mtx.url_lectura(path_senal)]
    mapa = list(mapa_audio or [])
    if d["protocolo"] == "rtmp":            # FLV admite un vídeo y un audio
        cmd += ["-map", "0:v:0?", "-map", f"0:a:{mapa[0] if mapa else 0}?", "-c", "copy", "-f", "flv"]
        url, opciones = url_salida({"tipo": "rtmp", "url": d["url"], "clave": d["clave"]})
    elif d["protocolo"] == "srt":
        if entera:
            cmd += ["-map", "0"]
        else:
            cmd += ["-map", "0:v:0?"] + [x for i in mapa for x in ("-map", f"0:a:{i}?")]
        cmd += ["-c", "copy", "-f", "mpegts"]
        puerto = _opciones(d).get("puerto")
        modo = "caller" if d["inicia"] == "hub" else "listener"
        url, opciones = url_salida({"tipo": "srt", "modo": modo, "streamid": d["streamid"] if modo == "caller" else "",
                                    "url": d["url"] if modo == "caller" else f"srt://0.0.0.0:{int(puerto)}",
                                    "passphrase": d["passphrase"], "latencia_ms": d["latencia_ms"]})
    else:
        raise ValueError(f"el destino {d['protocolo']} no lleva proceso")
    return cmd + opciones + [url]


def con_envio(d):
    """¿El destino necesita un ffmpeg que le lleve la señal? Lo que el hub entrega desde MediaMTX, no."""
    if d["protocolo"] == "rtmp":
        return d["inicia"] == "hub"
    if d["protocolo"] == "srt":
        return d["inicia"] == "hub" or bool(_opciones(d).get("puerto"))
    return False
