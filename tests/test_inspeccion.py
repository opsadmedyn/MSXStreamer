"""De lo que dicen ffprobe y MediaMTX a lo que necesita el plan de una ruta."""

import json

import inspeccion

FFPROBE = {
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "profile": "High", "width": 1920, "height": 1080,
         "avg_frame_rate": "60000/1001", "r_frame_rate": "60000/1001", "field_order": "progressive", "disposition": {}},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000",
         "tags": {"language": "spa", "comment": "Español"}},
        {"index": 2, "codec_type": "audio", "codec_name": "opus", "channels": 6, "sample_rate": "48000", "tags": {"language": "und"}},
        {"index": 3, "codec_type": "data", "codec_name": "bin_data"},
    ],
    "packets": [{"stream_index": 0, "pts": 0, "dts": 0}, {"stream_index": 1, "pts": 10, "dts": 10},
                {"stream_index": 0, "pts": 6006, "dts": 1501}, {"stream_index": 0, "pts": 3003, "dts": 3003}],
}


def test_interpretar_ffprobe():
    p = inspeccion.interpretar(FFPROBE)
    assert p["video"] == {"codec": "h264", "ancho": 1920, "alto": 1080, "fps": 59.94, "perfil": "High",
                          "entrelazado": False, "b": True}
    assert p["audio"] == [{"codec": "aac", "canales": 2, "hz": 48000, "idioma": "spa", "nombre": "Español"},
                          {"codec": "opus", "canales": 6, "hz": 48000, "idioma": "", "nombre": ""}]


def test_sin_fotogramas_b_y_entrelazada():
    datos = {"streams": [dict(FFPROBE["streams"][0], field_order="tt", avg_frame_rate="0/0", r_frame_rate="25/1")],
             "packets": [{"stream_index": 0, "pts": 0, "dts": 0}, {"stream_index": 0, "pts": 3600, "dts": 3600}]}
    v = inspeccion.interpretar(datos)["video"]
    assert v["b"] is False and v["entrelazado"] is True and v["fps"] == 25.0
    # sin paquetes de vídeo no se afirma nada: el plan hacia WebRTC queda provisional
    assert inspeccion.interpretar({"streams": datos["streams"]})["video"]["b"] is None


def _paquetes(por_segundo, segundos=2.0, indice=0):
    """Los paquetes de vídeo que ffprobe vería llegar a ese ritmo."""
    return [{"stream_index": indice, "pts": round(n * 90000 / por_segundo), "dts": round(n * 90000 / por_segundo),
             "pts_time": f"{n / por_segundo:.6f}"} for n in range(int(por_segundo * segundos))]


def test_los_fotogramas_por_segundo_que_declara_ffprobe_se_corrigen_si_no_cuadran_con_los_que_llegan():
    """De un navegador que publica por WebRTC ffprobe a veces no declara nada, o declara el triple."""
    def fps(media, base, paquetes):
        flujo = dict(FFPROBE["streams"][0], avg_frame_rate=media, r_frame_rate=base)
        return inspeccion.interpretar({"streams": [flujo], "packets": paquetes})["video"]["fps"]
    assert fps("0/0", "60/1", _paquetes(20)) == 20.0            # declara 60 y llegan 20
    assert fps("0/0", "90000/1", _paquetes(20)) == 20.0         # no declara nada que valga
    assert fps("0/0", "90000/1", []) is None                    # ni se vio llegar nada
    assert fps("20/1", "20/1", _paquetes(20)) == 20.0
    assert fps("2499/125", "20/1", _paquetes(20)) == 19.992     # una media algo corta se queda como está
    assert fps("60000/1001", "60000/1001", _paquetes(59.94)) == 59.94
    # solo se corrige hacia abajo: entrelazada con un paquete por campo, llegan el doble de los que declara
    assert fps("25/1", "50/1", _paquetes(50)) == 25.0
    # con pocos paquetes, o sin sus tiempos, no se corrige nada
    assert fps("0/0", "60/1", _paquetes(20, segundos=0.4)) == 60.0
    assert fps("0/0", "60/1", [{"stream_index": 0, "pts": n * 4500, "dts": n * 4500} for n in range(40)]) == 60.0
    # los paquetes de otra pista no cuentan
    assert fps("0/0", "60/1", _paquetes(20, indice=1)) == 60.0


def test_solo_audio_y_caratula():
    datos = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                         {"index": 1, "codec_type": "audio", "codec_name": "mp3", "channels": 2, "sample_rate": "44100"}]}
    p = inspeccion.interpretar(datos)
    assert p["video"] is None and p["audio"][0]["codec"] == "mp2" and p["audio"][0]["hz"] == 44100


INFO = {"ready": True, "readyTime": "2026-11-19T06:27:27Z", "tracks": ["H264", "MPEG-4 Audio", "Opus"],
        "tracks2": [{"codec": "H264", "codecProps": {"width": 1280, "height": 720, "profile": "High", "level": "3.1"}},
                    {"codec": "MPEG-4 Audio", "codecProps": {"sampleRate": 48000, "channelCount": 2}},
                    {"codec": "Opus", "codecProps": {"channelCount": 2}}]}


def test_lo_que_mediamtx_sabe_al_instante_es_parcial():
    p = inspeccion.de_mediamtx(INFO)
    assert p["parcial"] and p["video"]["ancho"] == 1280 and p["video"]["b"] is None and p["video"]["fps"] is None
    assert [a["codec"] for a in p["audio"]] == ["aac", "opus"] and p["audio"][0]["hz"] == 48000
    assert inspeccion.de_mediamtx({"ready": False}) is None and inspeccion.de_mediamtx(None) is None
    # un MediaMTX antiguo solo da los nombres de los códecs
    assert inspeccion.de_mediamtx({"ready": True, "tracks": ["H265", "MPEG-4 Audio"]})["video"]["codec"] == "h265"


def _info_con(ancho, alto):
    return dict(INFO, tracks2=[{"codec": "H264", "codecProps": {"width": ancho, "height": alto}}] + INFO["tracks2"][1:])


def test_un_origen_que_cambia_el_tamano_de_la_imagen_sin_volver_a_conectar_se_nota():
    """Un navegador que publica por WebRTC empieza pequeño y sube: la huella no cambia (es la misma
    conexión y las mismas pistas), pero MediaMTX deja de ver el tamaño que veía al inspeccionar."""
    inspeccionada = {"video": {"codec": "h264", "ancho": 320, "alto": 180, "fps": 20.0, "b": False}, "audio": [],
                     "segun_mediamtx": [320, 180]}
    assert inspeccion.huella(_info_con(320, 180)) == inspeccion.huella(_info_con(1280, 720))
    assert inspeccion.medidas_nuevas(inspeccionada, _info_con(320, 180)) is None
    assert inspeccion.medidas_nuevas(inspeccionada, _info_con(1280, 720)) == [1280, 720]
    # una inspección guardada por una versión anterior: se compara con lo que midió entonces
    assert inspeccion.medidas_nuevas({"video": {"codec": "h264", "ancho": 320, "alto": 180}}, _info_con(480, 270)) == [480, 270]
    assert inspeccion.medidas_nuevas({"video": {"codec": "h264", "ancho": 320, "alto": 180}}, _info_con(320, 180)) is None


def test_que_ffprobe_y_mediamtx_no_coincidan_en_el_tamano_no_es_un_cambio():
    """Si no, la fuente se volvería a mirar sin fin y sus conversiones se reiniciarían cada vez."""
    inspeccionada = {"video": {"codec": "h264", "ancho": 1920, "alto": 1080}, "audio": [], "segun_mediamtx": [1920, 1088]}
    assert inspeccion.medidas_nuevas(inspeccionada, _info_con(1920, 1088)) is None


def test_sin_medidas_o_sin_video_no_hay_cambio_que_mirar():
    solo_nombres = {"ready": True, "tracks": ["H264"]}                  # un MediaMTX que no dice las medidas
    assert inspeccion.medidas_de(solo_nombres) is None and inspeccion.medidas_de(None) is None
    assert inspeccion.medidas_de(INFO) == [1280, 720]
    assert inspeccion.medidas_nuevas({"video": {"codec": "h264", "ancho": 320, "alto": 180}}, solo_nombres) is None
    assert inspeccion.medidas_nuevas({"video": None, "audio": [{"codec": "aac"}]}, INFO) is None
    assert inspeccion.medidas_nuevas(None, INFO) is None


def test_un_origen_que_vuelve_con_lo_mismo_se_reconoce_por_lo_que_dice_mediamtx():
    resumen = inspeccion.resumen_mediamtx(_info_con(1280, 720))
    assert resumen["video"][:3] == ["h264", 1280, 720] and len(resumen["audio"]) == len(_info_con(1280, 720)["tracks2"]) - 1
    assert inspeccion.resumen_mediamtx(None) is None and inspeccion.resumen_mediamtx({"ready": False}) is None
    vista = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "fps": 29.97, "b": False}, "audio": [{"codec": "aac"}],
             "resumen_mediamtx": json.loads(json.dumps(resumen))}           # como vuelve de la base de datos
    vuelve = dict(_info_con(1280, 720), readyTime="2026-11-19T08:00:00Z")   # otra conexión, la misma señal
    assert inspeccion.la_misma(vista, vuelve)
    assert not inspeccion.la_misma(vista, _info_con(1920, 1080))            # vuelve con otro tamaño
    otra_pista = _info_con(1280, 720)
    otra_pista["tracks2"] = otra_pista["tracks2"][:1]
    assert not inspeccion.la_misma(vista, otra_pista)                       # vuelve sin audio
    assert not inspeccion.la_misma(dict(vista, parcial=True), vuelve)       # lo que se sabía no era completo
    sin_resumen = {k: v for k, v in vista.items() if k != "resumen_mediamtx"}
    assert not inspeccion.la_misma(sin_resumen, vuelve)                     # inspecciones de antes de guardar el resumen
    assert not inspeccion.la_misma(None, vuelve) and not inspeccion.la_misma(vista, None)


def test_la_huella_cambia_cuando_el_origen_vuelve_o_cambian_sus_pistas():
    h = inspeccion.huella(INFO)
    assert h and inspeccion.huella(dict(INFO)) == h
    assert inspeccion.huella(dict(INFO, readyTime="2026-11-19T07:00:00Z")) != h
    assert inspeccion.huella(dict(INFO, tracks=["H264"])) != h
    assert inspeccion.huella({"ready": False}) == "" and inspeccion.huella(None) == ""


MAESTRA = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Español",DEFAULT=YES,LANGUAGE="spa",URI="a_es/index.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Inglés, original",DEFAULT=NO,LANGUAGE="eng",URI="a_en/index.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="Español",URI="s/index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=3440800,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2",AUDIO="aud"
v_720p/index.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=1020800,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2"
v_360p/index.m3u8
"""


def test_lista_hls_maestra_con_variantes_y_pistas_de_audio():
    lista = inspeccion.lista_hls(MAESTRA)
    assert lista["maestra"] and [(v["indice"], v["alto"], v["kbps"]) for v in lista["variantes"]] == [(0, 720, 3441), (1, 360, 1021)]
    assert [(a["nombre"], a["idioma"], a["defecto"]) for a in lista["variantes"][0]["audios"]] == \
        [("Español", "spa", True), ("Inglés, original", "eng", False)]
    assert lista["variantes"][1]["audios"] == []            # sin grupo: el audio va dentro del vídeo
    simple = inspeccion.lista_hls("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg1.ts\n")
    assert not simple["maestra"] and len(simple["variantes"]) == 1 and simple["variantes"][0]["audios"] == []


def test_los_nombres_de_la_lista_pasan_a_las_pistas_si_coinciden_en_numero():
    variante = inspeccion.lista_hls(MAESTRA)["variantes"][0]
    pistas = {"video": None, "audio": [{"codec": "aac", "nombre": "", "idioma": ""}, {"codec": "aac", "nombre": "", "idioma": ""}]}
    assert [a["nombre"] for a in inspeccion.con_nombres(pistas, variante)["audio"]] == ["Español", "Inglés, original"]
    una = {"video": None, "audio": [{"codec": "aac", "nombre": "", "idioma": ""}]}
    assert inspeccion.con_nombres(una, variante)["audio"][0]["nombre"] == ""
    assert inspeccion.con_nombres(None, variante) is None


def test_direcciones_que_no_son_listas():
    assert inspeccion.leer_lista_hls("ftp://x/y.m3u8")[1].startswith("La lista HLS debe empezar")
    assert inspeccion.leer_lista_hls("http://127.0.0.1:9/x.m3u8")[0] is None


def test_lo_que_no_pasa_por_el_enlace_se_anota_con_lo_que_sabe_mediamtx():
    info = {"ready": True, "tracks": ["VP8", "G711", "Opus"], "tracks2": [
        {"codec": "VP8", "codecProps": {"width": 1280, "height": 720}},
        {"codec": "G711", "codecProps": {"channelCount": 1, "sampleRate": 8000}},
        {"codec": "Opus", "codecProps": {"channelCount": 2}}]}
    visto = {"video": None, "audio": [{"codec": "opus", "canales": 2, "hz": 48000, "idioma": "", "nombre": ""}]}
    assert inspeccion._completa(visto, info)            # ffprobe ya vio todo lo que podía ver
    junto = inspeccion.con_lo_que_no_pasa(visto, info)
    assert junto["video"]["codec"] == "vp8" and junto["video"]["ancho"] == 1280
    assert [a["codec"] for a in junto["audio"]] == ["g711", "opus"] and junto["audio"][1]["hz"] == 48000
    assert inspeccion.algo_pasa(info)
    assert not inspeccion.algo_pasa({"ready": True, "tracks": ["VP9"], "tracks2": [{"codec": "VP9"}]})
    normal = {"ready": True, "tracks": ["H264", "MPEG-4 Audio"], "tracks2": [{"codec": "H264"}, {"codec": "MPEG-4 Audio"}]}
    igual = {"video": {"codec": "h264", "ancho": 1, "alto": 1, "b": False}, "audio": [{"codec": "aac"}]}
    assert inspeccion.con_lo_que_no_pasa(dict(igual), normal) == igual
