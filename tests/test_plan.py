"""El plan de una ruta: qué se copia, qué se convierte y por qué. Los casos son los ejemplos que el
hub debe saber representar, con los códecs comprobados en el banco de pruebas."""

import plan

CAMARA = {"video": {"codec": "h264", "ancho": 1920, "alto": 1080, "fps": 59.94, "b": True},
          "audio": [{"codec": "aac", "canales": 2, "hz": 48000}]}
ESTUDIO = {"video": {"codec": "h264", "ancho": 1920, "alto": 1080, "fps": 29.97, "b": False},
           "audio": [{"codec": "aac", "canales": 2, "hz": 48000}]}
INTERNACIONAL = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "fps": 29.97, "b": False},
                 "audio": [{"codec": "aac", "canales": 2, "hz": 48000, "nombre": "Español"},
                           {"codec": "aac", "canales": 2, "hz": 48000, "nombre": "Inglés"}]}
P720 = plan.PERFILES_FIJOS[2]
P1080 = plan.PERFILES_FIJOS[1]


def acciones(p):
    return (p["video"] and p["video"]["accion"], [a["accion"] for a in p["audio"]])


def test_camara_srt_a_plataforma_rtmp_se_copia_todo():
    p = plan.planear(CAMARA, "rtmp")
    assert acciones(p) == ("copia", ["copia"])
    assert p["senal"]["firma"] == "" and p["mapa_audio"] == [0]
    assert p["resumen"] == "Se copia todo: solo cambia el transporte."
    assert "RTMP acepta H.264" in p["video"]["motivo"]


def test_camara_con_fotogramas_b_a_webrtc_convierte_video_y_audio():
    p = plan.planear(CAMARA, "webrtc", empuja=False)
    assert acciones(p) == ("convierte", ["convierte"])
    assert "fotogramas B" in p["video"]["motivo"]
    assert p["senal"]["video"] == {"codec": "h264", "ancho": 1920, "alto": 1080, "kbps": 6000}
    assert p["senal"]["audio"] == [{"origen": 0, "codec": "opus", "kbps": 128, "canales": 2}]
    assert p["senal"]["firma"] and p["resumen"] == "Se convierten el vídeo y el audio."


def test_estudio_rtmp_a_hls_y_a_srt_se_copia():
    for protocolo, empuja in (("hls", False), ("srt", True), ("srt", False)):
        p = plan.planear(ESTUDIO, protocolo, empuja=empuja)
        assert acciones(p) == ("copia", ["copia"]), protocolo
        assert p["senal"]["firma"] == ""        # el receptor lee la fuente tal cual


def test_estudio_sin_fotogramas_b_a_webrtc_solo_convierte_el_audio():
    p = plan.planear(ESTUDIO, "webrtc", empuja=False)
    assert acciones(p) == ("copia", ["convierte"])
    assert p["senal"]["video"] == "copia"
    assert p["audio"][0]["a"] == "Opus · estéreo · 128 kb/s"
    assert "WebRTC no admite AAC" in p["audio"][0]["motivo"]
    assert p["resumen"] == "Se copia el vídeo y se convierte el audio."
    assert p["carga"] == "Solo audio: carga mínima."


def test_hls_con_dos_pistas_conserva_ambas_en_srt():
    p = plan.planear(INTERNACIONAL, "srt")
    assert acciones(p) == ("copia", ["copia", "copia"])
    assert p["senal"]["firma"] == "" and p["mapa_audio"] == [0, 1]
    assert [a["nombre"] for a in p["audio"]] == ["Pista 1 · Español", "Pista 2 · Inglés"]


def test_hls_con_dos_pistas_usa_la_elegida_en_rtmp():
    p = plan.planear(INTERNACIONAL, "rtmp", audio="2")
    assert acciones(p) == ("copia", ["omite", "copia"])
    assert "una sola pista" in p["audio"][0]["motivo"]
    # el hub empuja: su propio envío toma la pista 2 de la fuente, sin proceso intermedio
    assert p["senal"]["firma"] == "" and p["mapa_audio"] == [1]


def test_hls_con_dos_pistas_genera_opus_para_el_monitor_webrtc():
    p = plan.planear(INTERNACIONAL, "webrtc", audio="1", empuja=False)
    assert acciones(p) == ("copia", ["convierte", "omite"])
    assert p["senal"]["audio"] == [{"origen": 0, "codec": "opus", "kbps": 128, "canales": 2}]
    assert p["mapa_audio"] == [0]


def test_elegir_pistas_para_un_receptor_que_viene_al_hub_pide_senal_derivada():
    """El receptor lee un path de MediaMTX: no puede elegir pistas, así que se le prepara la señal."""
    p = plan.planear(INTERNACIONAL, "srt", audio="2", empuja=False)
    assert acciones(p) == ("copia", ["omite", "copia"])
    assert p["senal"]["firma"] and p["mapa_audio"] == [0]
    assert p["audio"][0]["motivo"] == "El destino usa solo la pista 2."
    assert p["carga"] == "Solo se reparten las pistas: carga mínima."


def test_dos_destinos_con_el_mismo_perfil_comparten_la_conversion():
    a = plan.planear(ESTUDIO, "webrtc", empuja=False)
    b = plan.planear(ESTUDIO, "webrtc", empuja=True)       # reenvío WHIP
    assert a["senal"]["firma"] == b["senal"]["firma"] != ""
    c = plan.planear(ESTUDIO, "rtmp", perfil=P720)
    d = plan.planear(ESTUDIO, "srt", perfil=P720)
    assert c["senal"]["firma"] == d["senal"]["firma"] != a["senal"]["firma"]


def test_la_firma_no_depende_del_destino_sino_de_lo_que_sale():
    assert plan.planear(ESTUDIO, "rtmp", perfil=P720)["senal"]["firma"] != \
        plan.planear(ESTUDIO, "rtmp", perfil=P1080)["senal"]["firma"]
    assert plan.planear(ESTUDIO, "rtmp", perfil=P720)["senal"]["firma"] != \
        plan.planear(CAMARA, "webrtc")["senal"]["firma"]


def test_perfil_720p_reduce_y_no_amplia():
    p = plan.planear(CAMARA, "rtmp", perfil=P720)
    assert p["senal"]["video"] == {"codec": "h264", "ancho": 1280, "alto": 720, "kbps": 3000}
    assert p["video"]["a"] == "H.264 · 1280×720 · 3 Mb/s · sin fotogramas B"
    assert acciones(p) == ("convierte", ["copia"]) and p["resumen"] == "Se convierte el vídeo y el audio se copia."
    q = plan.planear(INTERNACIONAL, "rtmp", perfil=P1080)   # fuente 720p con perfil 1080p
    assert (q["senal"]["video"]["ancho"], q["senal"]["video"]["alto"]) == (1280, 720)
    assert any("menos resolución" in a for a in q["avisos"])


def test_medidas_pares_y_con_la_proporcion_de_la_fuente():
    assert plan.medidas({"ancho": 1920, "alto": 1080}, 720) == (1280, 720)
    assert plan.medidas({"ancho": 1440, "alto": 1080}, 720) == (960, 720)      # 4:3
    assert plan.medidas({"ancho": 853, "alto": 481}, None) == (852, 480)
    assert plan.medidas({}, 720) == (1280, 720)


def test_fuente_sin_inspeccionar_da_un_plan_provisional():
    p = plan.planear(None, "rtmp")
    assert p["provisional"] and p["senal"] is None and "inspeccionado" in p["resumen"]


def test_webrtc_sin_saber_si_hay_fotogramas_b_es_provisional():
    pistas = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "b": None}, "audio": []}
    assert plan.planear(pistas, "webrtc")["provisional"]
    assert not plan.planear(pistas, "rtmp")["provisional"]      # a RTMP le da igual


def test_cabina_webrtc_con_opus():
    cabina = {"video": None, "audio": [{"codec": "opus", "canales": 2, "hz": 48000}]}
    a_rtmp = plan.planear(cabina, "rtmp")
    assert acciones(a_rtmp) == (None, ["convierte"]) and a_rtmp["audio"][0]["a"] == "AAC · estéreo · 160 kb/s"
    assert a_rtmp["resumen"] == "Se convierte el audio." and "no trae vídeo" in a_rtmp["avisos"][0]
    assert acciones(plan.planear(cabina, "srt")) == (None, ["copia"])
    assert acciones(plan.planear(cabina, "webrtc")) == (None, ["copia"])


def test_h265_se_copia_a_srt_y_se_convierte_para_rtmp_y_webrtc():
    hevc = {"video": {"codec": "h265", "ancho": 3840, "alto": 2160, "fps": 50, "b": True},
            "audio": [{"codec": "aac", "canales": 2, "hz": 48000}]}
    assert acciones(plan.planear(hevc, "srt")) == ("copia", ["copia"])
    r = plan.planear(hevc, "rtmp")
    assert r["video"]["accion"] == "convierte" and "no admite H.265" in r["video"]["motivo"]
    assert r["senal"]["video"]["kbps"] == 16000
    assert plan.planear(hevc, "webrtc")["video"]["accion"] == "convierte"


def test_audio_51_a_webrtc_baja_a_estereo():
    fuente = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "b": False},
              "audio": [{"codec": "opus", "canales": 6, "hz": 48000}]}
    p = plan.planear(fuente, "webrtc")
    assert p["audio"][0]["accion"] == "convierte" and p["senal"]["audio"][0]["canales"] == 2
    assert "estéreo" in p["audio"][0]["motivo"]


def test_pista_elegida_que_no_existe_usa_la_primera():
    p = plan.planear(ESTUDIO, "rtmp", audio="3")
    assert p["mapa_audio"] == [0] and any("no tiene pista 3" in a for a in p["avisos"])


def test_destinos_con_adaptador():
    p = plan.planear(ESTUDIO, "ndi")
    assert p["adaptador"] == "ndi" and "adaptador NDI" in p["resumen"]


def test_textos():
    assert plan.texto_video(CAMARA["video"]) == "H.264 · 1920×1080 · 59,94 fps"
    assert plan.texto_audio(CAMARA["audio"][0]) == "AAC · estéreo · 48 kHz"
    assert plan.texto_audio({"codec": "aac", "canales": 2, "hz": 44100}) == "AAC · estéreo · 44,1 kHz"
    assert plan.perfil_por_id(plan.PERFILES_FIJOS, "no-existe")["id"] == "original"


def test_video_que_no_cabe_en_el_enlace_interno_no_se_puede_enrutar():
    """Un navegador que publica en VP8 por WHIP: el hub no puede llevarlo de un proceso a otro."""
    cabina = {"video": {"codec": "vp8", "ancho": 1280, "alto": 720, "fps": 30, "b": False},
              "audio": [{"codec": "opus", "canales": 2, "hz": 48000}]}
    for protocolo in ("webrtc", "srt", "rtmp", "hls"):
        p = plan.planear(cabina, protocolo)
        assert not p["posible"] and not p["provisional"] and p["senal"] is None
        assert "VP8" in p["resumen"] and "H.264" in p["resumen"] and p["video"]["accion"] == "imposible"
    for codec in ("vp9", "av1", "mjpeg"):
        assert not plan.planear({"video": {"codec": codec}, "audio": []}, "srt")["posible"]


def test_audio_que_no_cabe_en_el_enlace_interno_se_omite_y_se_avisa():
    """G.711 junto a AAC: la pista 1 no sale, y la pista 2 es la primera (y única) del enlace."""
    fuente = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "fps": 25, "b": False},
              "audio": [{"codec": "g711", "canales": 1, "hz": 8000}, {"codec": "aac", "canales": 2, "hz": 48000}]}
    p = plan.planear(fuente, "srt")
    assert p["posible"] and [a["accion"] for a in p["audio"]] == ["omite", "copia"]
    assert p["mapa_audio"] == [0] and p["entera"] and p["senal"]["firma"] == ""
    assert any("pista 1 (G.711) no llega" in a.lower() or "La pista 1 (G.711) no llega" in a for a in p["avisos"])
    p = plan.planear(fuente, "rtmp")                         # una sola pista: la primera que el hub puede llevar
    assert p["mapa_audio"] == [0] and p["audio"][1]["accion"] == "copia"
    p = plan.planear(fuente, "rtmp", audio="1")              # se pidió justo la que no pasa
    assert p["mapa_audio"] == [0] and any("no puede llevar la pista 1" in a for a in p["avisos"])
    p = plan.planear(fuente, "webrtc")                       # y al convertir, el origen es el número en el enlace
    assert p["senal"]["audio"] == [{"origen": 0, "codec": "opus", "kbps": 128, "canales": 2}]


def test_fuente_sin_nada_que_llevar():
    assert not plan.planear({"video": None, "audio": []}, "srt")["posible"]
    solo_g711 = {"video": None, "audio": [{"codec": "g711", "canales": 1, "hz": 8000}]}
    p = plan.planear(solo_g711, "srt")
    assert not p["posible"] and "G.711" in p["resumen"]
    con_video = {"video": {"codec": "h264", "ancho": 640, "alto": 360, "b": False}, "audio": solo_g711["audio"]}
    p = plan.planear(con_video, "rtmp")                      # el vídeo sí sale; sin audio y avisando
    assert p["posible"] and p["mapa_audio"] == [] and p["video"]["accion"] == "copia"
