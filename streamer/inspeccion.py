"""Media Syntaxis Streamer - inspección: qué trae una señal.

Antes de activar una ruta hay que saber qué vídeo y qué audio trae la fuente, para copiar lo que el
destino acepte y convertir solo lo demás. ffprobe mira la señal unos segundos y de ahí salen el
códec, las medidas, los fotogramas por segundo, si es entrelazada, si trae fotogramas B y cada
pista de audio. El resultado tiene la forma que espera plan.planear.

Fotogramas B: se detectan como lo hace MediaMTX al servir por WebRTC, por los paquetes de vídeo cuyo
instante de presentación no coincide con el de decodificación. No hace falta decodificar la imagen.
"""

import json
import pathlib
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import db
import mtx
import protocolos as P
import secretos

SEGUNDOS = 3                    # de señal que se miran
LIMITE = 25                     # segundos que puede tardar ffprobe antes de darlo por perdido
ENTRADAS = ("stream=index,codec_type,codec_name,profile,width,height,avg_frame_rate,r_frame_rate,field_order,"
            "channels,sample_rate,disposition:stream_tags=language,comment,title:packet=stream_index,pts,dts,pts_time")
ENTRELAZADOS = {"tt", "bb", "tb", "bt"}


def ffprobe():
    cfg = db.config()
    if cfg.get("ffprobe"):
        return cfg["ffprobe"]
    ff = cfg.get("ffmpeg", "ffmpeg")
    return str(pathlib.Path(ff).with_name("ffprobe")) if "/" in ff else "ffprobe"


def comando(entrada, segundos=SEGUNDOS):
    """ffprobe para la entrada dada (sus opciones y «-i URL»)."""
    return [ffprobe(), "-v", "error", "-of", "json", "-read_intervals", f"%+{segundos}",
            "-show_entries", ENTRADAS, *entrada]


def _fps(s, llegan=None):
    """Fotogramas por segundo: los que declara ffprobe (la media o, si no la da, el ritmo base) salvo
    que no cuadren con los que se vieron llegar. De una señal de ritmo irregular (un navegador que
    publica por WebRTC) ffprobe a veces no declara nada o declara el triple (60 donde hay 20), y con
    eso una conversión pondría los fotogramas clave tres veces más separados. `llegan` son los
    paquetes de vídeo por segundo que se contaron, o None. Solo corrigen hacia abajo: una señal
    entrelazada puede traer dos paquetes por fotograma."""
    declarados = None
    for clave in ("avg_frame_rate", "r_frame_rate"):
        try:
            n, _, d = (s.get(clave) or "").partition("/")
            v = float(n) / float(d or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= v <= 240:
            declarados = round(v, 3)
            break
    if llegan and (declarados is None or declarados > 1.5 * llegan):
        return round(llegan, 2)
    return declarados


def _llegan(paquetes):
    """Paquetes de vídeo por segundo, por sus tiempos de presentación. None si son pocos para decirlo."""
    tiempos = []
    for p in paquetes:
        try:
            tiempos.append(float(p["pts_time"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(tiempos) < 10:
        return None
    dura = max(tiempos) - min(tiempos)
    ritmo = (len(tiempos) - 1) / dura if dura >= 0.4 else 0
    return ritmo if 1 <= ritmo <= 240 else None


def interpretar(datos):
    """De la salida JSON de ffprobe a {"video": {...} | None, "audio": [{...}]}."""
    video, audios, indice_video = None, [], None
    for s in datos.get("streams") or []:
        etiquetas = s.get("tags") or {}
        if s.get("codec_type") == "video" and video is None:
            if (s.get("disposition") or {}).get("attached_pic"):
                continue                            # la carátula de un audio no es vídeo
            indice_video = s.get("index")
            llegan = _llegan([p for p in datos.get("packets") or [] if p.get("stream_index") == indice_video])
            video = {"codec": P.codec(s.get("codec_name")), "ancho": s.get("width") or None,
                     "alto": s.get("height") or None, "fps": _fps(s, llegan), "perfil": s.get("profile") or "",
                     "entrelazado": (s.get("field_order") or "") in ENTRELAZADOS, "b": None}
        elif s.get("codec_type") == "audio":
            try:
                hz = int(s.get("sample_rate") or 0) or None
            except ValueError:
                hz = None
            audios.append({"codec": P.codec(s.get("codec_name")), "canales": s.get("channels") or None, "hz": hz,
                           "idioma": (etiquetas.get("language") or "").replace("und", ""),
                           "nombre": etiquetas.get("title") or etiquetas.get("comment") or ""})
    if video is not None:
        paquetes = [p for p in datos.get("packets") or [] if p.get("stream_index") == indice_video]
        if paquetes:                                # sin paquetes de vídeo no se puede afirmar nada
            video["b"] = any(p.get("pts") is not None and p.get("dts") is not None and p["pts"] != p["dts"]
                             for p in paquetes)
    return {"video": video, "audio": audios}


def mirar(entrada, segundos=SEGUNDOS, limite=LIMITE, ocultos=()):
    """(pistas, error): lo que trae la entrada o por qué no se pudo mirar (sin claves)."""
    try:
        r = subprocess.run(comando(entrada, segundos), stdin=subprocess.DEVNULL, capture_output=True, timeout=limite)
    except subprocess.TimeoutExpired:
        return None, f"Sin respuesta en {limite} s"
    except OSError as err:
        return None, f"No se pudo lanzar ffprobe: {err.strerror or err}"
    try:
        datos = json.loads(r.stdout or b"{}")
    except ValueError:
        datos = {}
    if not datos.get("streams"):
        lineas = [x for x in r.stderr.decode(errors="replace").splitlines() if x.strip()]
        return None, secretos.ocultar(lineas[-1] if lineas else "La señal no trae vídeo ni audio", ocultos)[:300]
    return interpretar(datos), ""


def de_mediamtx(info):
    """Lo que MediaMTX ya sabe de la señal de un path, al instante: códecs, medidas y canales. Es
    parcial: faltan los fotogramas por segundo, si es entrelazada y si trae fotogramas B. Basta para
    las rutas que copian (salvo hacia WebRTC); el resto espera a de_path()."""
    if not info or not info.get("ready"):
        return None
    video, audios = None, []
    for t in info.get("tracks2") or [{"codec": x} for x in info.get("tracks") or []]:
        codec, props = P.codec(t.get("codec")), t.get("codecProps") or {}
        if codec in P.VIDEO:
            if video is None:
                video = {"codec": codec, "ancho": props.get("width") or None, "alto": props.get("height") or None,
                         "fps": None, "perfil": props.get("profile") or "", "entrelazado": None, "b": None}
        else:
            audios.append({"codec": codec, "canales": props.get("channelCount") or None,
                           "hz": props.get("sampleRate") or None, "idioma": "", "nombre": ""})
    return {"video": video, "audio": audios, "parcial": True}


def _completa(pistas, info):
    """¿ffprobe vio todo lo que MediaMTX dice que hay?"""
    if not pistas:
        return False
    esperado = de_mediamtx(info) if info else None
    v = pistas.get("video")
    if v and not (v.get("ancho") and v.get("alto") and v.get("b") is not None):
        return False
    if esperado:                # de lo que hay, lo que cabe en el enlace interno: el resto no lo verá nunca
        hay_video = bool(esperado["video"]) and esperado["video"]["codec"] in P.ENLACE_VIDEO
        return bool(v) == hay_video and len(pistas["audio"]) == len(_que_pasan(esperado["audio"]))
    return True


def _que_pasan(audios):
    return [a for a in audios if a["codec"] in P.ENLACE_AUDIO]


def algo_pasa(info):
    """¿Algo de lo que trae el path cabe en el enlace interno? Si no, ffprobe no tiene nada que mirar."""
    sabido = de_mediamtx(info)
    if not sabido:
        return False
    return bool(sabido["video"] and sabido["video"]["codec"] in P.ENLACE_VIDEO) or bool(_que_pasan(sabido["audio"]))


def con_lo_que_no_pasa(pistas, info):
    """ffprobe mira la señal por el enlace interno del hub (MPEG-TS), que no lleva todos los códecs.
    Lo que MediaMTX dice que hay y por ahí no pasa se añade, en su sitio, con lo que MediaMTX sabe:
    así el plan explica que esa pista no sale en vez de callarla."""
    sabido = de_mediamtx(info)
    if not pistas or not sabido:
        return pistas
    if sabido["video"] and not pistas.get("video") and sabido["video"]["codec"] not in P.ENLACE_VIDEO:
        pistas["video"] = sabido["video"]
    if len(sabido["audio"]) > len(pistas.get("audio") or []) and len(_que_pasan(sabido["audio"])) < len(sabido["audio"]):
        vistas, juntas = list(pistas.get("audio") or []), []
        for a in sabido["audio"]:
            juntas.append(vistas.pop(0) if a["codec"] in P.ENLACE_AUDIO and vistas else a)
        pistas["audio"] = juntas
    return pistas


def de_path(path, info=None):
    """(pistas, error) de un path de MediaMTX: la fuente tal como la reparte el hub. Primero mira
    deprisa (unos 2 s); si no le da tiempo a verlo todo (un origen con pocos fotogramas clave),
    vuelve a mirar con calma."""
    url = mtx.url_lectura(path)
    pistas, error = mirar(["-analyzeduration", "2000000", "-i", url], 2)
    if not _completa(pistas, info):
        despacio, error2 = mirar(["-i", url], SEGUNDOS)
        if despacio:
            pistas, error = despacio, ""
        elif not pistas:
            error = error2 or error
    if pistas and info:                             # lo que ffprobe no llegó a ver y MediaMTX sí sabe
        sabido = de_mediamtx(info) or {}
        if pistas.get("video") and sabido.get("video"):
            for k in ("ancho", "alto"):
                pistas["video"][k] = pistas["video"].get(k) or sabido["video"].get(k)
        pistas = con_lo_que_no_pasa(pistas, info)
        pistas["segun_mediamtx"] = medidas_de(info)     # para saber después si la imagen cambia de tamaño
        pistas["resumen_mediamtx"] = resumen_mediamtx(info)     # y si, cuando el origen vuelva, trae lo mismo
    return pistas, error


def resumen_mediamtx(info):
    """Lo que MediaMTX dice de una señal, en corto y comparable entre dos momentos: códecs, medidas,
    perfil y canales. None si no hay señal."""
    sabido = de_mediamtx(info)
    if not sabido:
        return None
    v = sabido["video"]
    return {"video": [v["codec"], v["ancho"], v["alto"], v["perfil"]] if v else None,
            "audio": [[a["codec"], a["canales"], a["hz"]] for a in sabido["audio"]]}


def la_misma(pistas, info):
    """¿La señal que llega ahora es, para MediaMTX, la que ya se inspeccionó del todo? Un codificador
    que se cae y vuelve trae casi siempre lo mismo: lo que se sabía de él sirve mientras ffprobe lo
    confirma, y las conversiones no tienen que esperar a esa segunda mirada."""
    if not pistas or pistas.get("parcial") or not pistas.get("resumen_mediamtx"):
        return False
    return pistas["resumen_mediamtx"] == resumen_mediamtx(info)


def medidas_de(info):
    """[ancho, alto] del vídeo de un path según MediaMTX, o None si no lo sabe."""
    v = (de_mediamtx(info) or {}).get("video") or {}
    return [v["ancho"], v["alto"]] if v.get("ancho") and v.get("alto") else None


def medidas_nuevas(pistas, info):
    """Un origen WebRTC (un navegador) cambia el tamaño de la imagen según su ancho de banda sin
    volver a conectar: empieza pequeño y sube en los primeros segundos. Devuelve el tamaño que
    MediaMTX ve ahora cuando no es el que veía al inspeccionar la señal, o None.
    Se compara lo que dice MediaMTX consigo mismo, no con lo que midió ffprobe: si en alguna señal
    los dos no coinciden, eso no es un cambio y no hay que volver a mirarla una y otra vez."""
    ahora = medidas_de(info)
    v = (pistas or {}).get("video")
    if not ahora or not v:
        return None
    antes = pistas.get("segun_mediamtx") or [v.get("ancho"), v.get("alto")]
    return ahora if list(antes) != ahora else None


def huella(info):
    """De la señal de un path: cambia cuando el origen vuelve a conectar o cambian sus pistas."""
    if not info or not info.get("ready"):
        return ""
    return f"{info.get('readyTime') or ''}|{json.dumps(info.get('tracks') or [], sort_keys=True)}"


# ---------------------------------------------------------------- listas HLS
_ATRIBUTO = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _atributos(linea):
    return {k: v.strip('"') for k, v in _ATRIBUTO.findall(linea.partition(":")[2])}


def lista_hls(texto):
    """Variantes y pistas de audio de una lista HLS maestra, en el orden en que las declara.
    Si la lista no es maestra (un solo vídeo), devuelve una variante sin datos."""
    variantes, audios, pendiente = [], [], None
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("#EXT-X-MEDIA:"):
            a = _atributos(linea)
            if a.get("TYPE") == "AUDIO":
                audios.append({"grupo": a.get("GROUP-ID", ""), "nombre": a.get("NAME", ""),
                               "idioma": a.get("LANGUAGE", ""), "defecto": a.get("DEFAULT") == "YES"})
        elif linea.startswith("#EXT-X-STREAM-INF:"):
            pendiente = _atributos(linea)
        elif linea and not linea.startswith("#") and pendiente is not None:
            ancho, _, alto = (pendiente.get("RESOLUTION") or "").partition("x")
            try:
                kbps = round(int(pendiente.get("BANDWIDTH") or 0) / 1000)
            except ValueError:
                kbps = 0
            variantes.append({"indice": len(variantes), "ancho": int(ancho) if ancho.isdigit() else None,
                              "alto": int(alto) if alto.isdigit() else None, "kbps": kbps or None,
                              "codecs": pendiente.get("CODECS", ""), "grupo_audio": pendiente.get("AUDIO", "")})
            pendiente = None
    if not variantes:
        variantes = [{"indice": 0, "ancho": None, "alto": None, "kbps": None, "codecs": "", "grupo_audio": ""}]
    for v in variantes:
        v["audios"] = [a for a in audios if a["grupo"] == v["grupo_audio"]] if v["grupo_audio"] else []
    return {"maestra": "#EXT-X-STREAM-INF" in texto, "variantes": variantes}


def leer_lista_hls(url, limite=8):
    """(lista, error) de una dirección .m3u8."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return None, "La lista HLS debe empezar por http:// o https://"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "msx-hub"}), timeout=limite) as r:
            texto = r.read(1_000_000).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"El servidor respondió {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, secretos.ocultar(f"No se pudo leer la lista: {getattr(e, 'reason', e)}")[:300]
    if "#EXTM3U" not in texto[:200]:
        return None, "Esa dirección no devuelve una lista HLS"
    return lista_hls(texto), ""


def con_nombres(pistas, variante):
    """Pone a las pistas de audio el nombre y el idioma que declara la lista HLS (MediaMTX no los
    conserva). Solo si coinciden en número: si no, no se sabe cuál es cuál."""
    audios = (variante or {}).get("audios") or []
    if pistas and len(audios) == len(pistas.get("audio") or []):
        for pista, a in zip(pistas["audio"], audios):
            pista["nombre"], pista["idioma"] = pista.get("nombre") or a["nombre"], pista.get("idioma") or a["idioma"]
    return pistas
