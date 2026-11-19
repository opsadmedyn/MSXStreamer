"""De lo que dicen ffprobe y MediaMTX a lo que necesita el plan de una ruta."""

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
