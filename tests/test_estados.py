"""Los seis estados y la evidencia de cada uno."""

import estados as E

AHORA = 1_800_000_000.0
PUBLICA = {"id": "f", "activo": 1, "inicia": "origen", "protocolo": "rtmp"}
PULL = {"id": "f", "activo": 1, "inicia": "hub", "protocolo": "srt"}
LISTO = {"ready": True, "tracks": ["H264", "MPEG-4 Audio"], "listo_desde": AHORA - 120}
PUSH = {"id": "d", "inicia": "hub", "protocolo": "srt"}
RECIBE = E.de_fuente(PUBLICA, LISTO, kbps=5980.4, ahora=AHORA)


def hechos(e):
    return " | ".join(h["que"] for h in e["evidencia"])


def test_fuente_recibiendo_medios():
    e = E.de_fuente(PUBLICA, LISTO, kbps=5980.4, origen="203.0.113.9 por RTMP", ahora=AHORA)
    assert e["estado"] == "recibiendo" and e["nombre"] == "Recibiendo medios" and e["actualizado"] == AHORA
    assert "MediaMTX recibe la señal de 203.0.113.9 por RTMP" in hechos(e) and "Entran 5 980 kb/s" in hechos(e)
    assert e["evidencia"][0]["desde"] == AHORA - 120
    assert "sin medida" in hechos(E.de_fuente(PUBLICA, LISTO, ahora=AHORA))


def test_fuente_esperando_a_que_el_origen_publique():
    e = E.de_fuente(PUBLICA, {"ready": False}, ahora=AHORA)
    assert e["estado"] == "esperando_fuente" and "Aún no se ha recibido señal" in hechos(e)
    e = E.de_fuente(PUBLICA, None, senal={"ultima": AHORA - 600}, ahora=AHORA)
    assert e["estado"] == "esperando_fuente" and e["evidencia"][-1] == {"que": "Última señal recibida", "desde": AHORA - 600}


def test_fuente_que_el_hub_va_a_buscar():
    cae = {"pid": None, "error": "Connection to srt://203.0.113.20:9000?*** failed: Connection refused", "intentos": 4}
    e = E.de_fuente(PULL, None, tarea=cae, ahora=AHORA)
    assert e["estado"] == "esperando_fuente"            # nunca hubo señal: no es «reconectar»
    assert "El otro equipo rechaza la conexión" in hechos(e) and "Intentos seguidos sin conseguirlo: 4" in hechos(e)
    e = E.de_fuente(PULL, None, tarea=cae, senal={"ultima": AHORA - 30}, ahora=AHORA)
    assert e["estado"] == "reconectando"
    e = E.de_fuente(PULL, None, tarea={"pid": 10, "desde": AHORA - 2}, senal={"ultima": AHORA - 30}, ahora=AHORA)
    assert e["estado"] == "reconectando" and "aún no publica" in hechos(e)
    e = E.de_fuente(dict(PULL, inicia="origen"), None, tarea={"pid": 10, "desde": AHORA - 2}, ahora=AHORA)
    assert e["estado"] == "esperando_fuente" and "escucha" in e["detalle"]
    agotada = {"pid": None, "error": "Sin más intentos tras 20 fallos seguidos: timed out", "intentos": 20}
    assert E.de_fuente(PULL, None, tarea=agotada, ahora=AHORA)["estado"] == "error"


def test_fuente_desactivada_sin_mediamtx_y_sin_adaptador():
    assert E.de_fuente(dict(PUBLICA, activo=0), LISTO, ahora=AHORA)["estado"] == "desactivada"
    assert E.de_fuente(PUBLICA, None, mtx_ok=False, ahora=AHORA)["estado"] == "error"
    e = E.de_fuente(dict(PUBLICA, protocolo="ndi"), None, impedimento="Falta el SDK de NDI", ahora=AHORA)
    assert e["estado"] == "error" and e["detalle"] == "Falta el SDK de NDI"


def test_ruta_enviando_con_proceso():
    tarea = {"pid": 44, "kbps": 5990.0, "desde": AHORA - 300, "reinicios": 2, "intentos": 0, "error": ""}
    e = E.de_ruta(PUSH, fuente=RECIBE, tarea=tarea, ahora=AHORA)
    assert e["estado"] == "enviando" and "manda 5 990 kb/s" in hechos(e) and "Reconexiones desde que se activó: 2" in hechos(e)
    e = E.de_ruta(PUSH, fuente=RECIBE, tarea=tarea, conversion={"pid": 50, "kbps": 3000}, ahora=AHORA)
    assert "señal convertida" in hechos(e)


def test_ruta_cuyo_destino_no_responde_reintenta_sola():
    tarea = {"pid": None, "error": "Error opening output files: Input/output error", "intentos": 7, "reinicios": 7}
    e = E.de_ruta(PUSH, fuente=RECIBE, tarea=tarea, ahora=AHORA)
    assert e["estado"] == "reconectando" and "Se cortó la conexión" in hechos(e) and "7" in hechos(e)
    e = E.de_ruta(PUSH, fuente=RECIBE, tarea={"pid": 9, "kbps": None, "desde": AHORA - 1, "intentos": 7}, ahora=AHORA)
    assert e["estado"] == "reconectando" and e["detalle"] == "El hub está conectando con el destino"
    agotada = {"pid": None, "error": "Sin más intentos tras 20 fallos seguidos: timed out", "intentos": 20}
    assert E.de_ruta(PUSH, fuente=RECIBE, tarea=agotada, ahora=AHORA)["estado"] == "error"
    assert E.de_ruta(PUSH, fuente=RECIBE, tarea=None, ahora=AHORA)["estado"] == "reconectando"


def test_ruta_esperando_fuente():
    sin_senal = E.de_fuente(PUBLICA, None, ahora=AHORA)
    e = E.de_ruta(PUSH, fuente=sin_senal, tarea={"pid": None, "error": "Esperando la entrada"}, ahora=AHORA)
    assert e["estado"] == "esperando_fuente" and "Nadie está publicando" in hechos(e)
    e = E.de_ruta(PUSH, plan={"posible": True, "provisional": True, "resumen": "Se está inspeccionando la señal de la fuente."},
                  fuente=RECIBE, ahora=AHORA)
    assert e["estado"] == "esperando_fuente" and "inspeccionando" in e["detalle"]
    e = E.de_ruta(PUSH, impedimento="La fuente está desactivada", ahora=AHORA)
    assert e["estado"] == "esperando_fuente"
    assert E.de_ruta(PUSH, impedimento="Falta el adaptador Zixi", ahora=AHORA)["estado"] == "error"
    assert E.de_ruta(PUSH, plan={"posible": False, "resumen": "No se puede"}, fuente=RECIBE, ahora=AHORA)["estado"] == "error"
    # sin señal, la ruta espera: lo que la fuente trajo la última vez puede no ser lo que traiga la próxima
    e = E.de_ruta(PUSH, plan={"posible": False, "resumen": "No se puede"}, fuente=sin_senal, ahora=AHORA)
    assert e["estado"] == "esperando_fuente"


def test_ruta_que_el_hub_entrega():
    d = {"id": "d", "inicia": "receptor", "protocolo": "hls"}
    e = E.de_ruta(d, fuente=RECIBE, info_destino=LISTO, tipo="entrega", ahora=AHORA)
    assert e["estado"] == "esperando_receptor" and "no tiene receptores" in hechos(e)
    lectores = [{"tipo": "hlsMuxer", "remoto": "198.51.100.7"}, {"tipo": "hlsMuxer", "remoto": "198.51.100.8"}]
    e = E.de_ruta(d, fuente=RECIBE, info_destino=LISTO, lectores=lectores, kbps=2500, tipo="entrega", ahora=AHORA)
    assert e["estado"] == "enviando" and "Receptores conectados: 2 (198.51.100.7, 198.51.100.8)" in hechos(e)
    assert "Salen 2 500 kb/s" in hechos(e)
    e = E.de_ruta(d, fuente=RECIBE, info_destino=None, tipo="entrega", ahora=AHORA)
    assert e["estado"] == "reconectando"


def test_srt_con_puerto_propio_espera_al_receptor():
    d = {"id": "d", "inicia": "receptor", "protocolo": "srt"}
    e = E.de_ruta(d, fuente=RECIBE, tarea={"pid": 9, "kbps": None, "desde": AHORA - 20}, ahora=AHORA)
    assert e["estado"] == "esperando_receptor" and "escucha" in e["detalle"]


def test_conversion_que_arranca_o_que_no_puede():
    web = {"id": "d", "inicia": "receptor", "protocolo": "webrtc"}
    e = E.de_ruta(web, fuente=RECIBE, senal_lista=False, conversion={"pid": 5, "desde": AHORA - 1}, tipo="entrega", ahora=AHORA)
    assert e["estado"] == "reconectando" and e["detalle"] == "La conversión está arrancando"
    falla = {"pid": None, "error": "OpenEncodeSessionEx failed: out of memory (10)", "intentos": 5}
    e = E.de_ruta(web, fuente=RECIBE, senal_lista=False, conversion=falla, tipo="entrega", ahora=AHORA)
    assert e["estado"] == "error" and "OpenEncodeSessionEx" in hechos(e)
    assert E.de_ruta(web, fuente=RECIBE, senal_lista=False, conversion=dict(falla, intentos=1), tipo="entrega",
                     ahora=AHORA)["estado"] == "reconectando"


def test_reenvio_whip_no_se_puede_confirmar():
    d = {"id": "d", "inicia": "hub", "protocolo": "webrtc"}
    e = E.de_ruta(d, fuente=RECIBE, info_destino=LISTO, tipo="whip", ahora=AHORA)
    assert e["estado"] == "enviando" and e["sin_confirmar"] and "no informa" in hechos(e)


def test_explicar_y_caducidad():
    assert E.explicar("[rtmp @ 0x1] Server error: NetStream.Publish.BadName") == "El otro equipo no acepta la clave"
    assert E.explicar("Connection timed out") == "El otro equipo no responde"
    assert E.explicar("algo que nadie ha visto nunca") == "" and E.explicar("") == ""
    assert E.caducado({"visto": AHORA - 60}, AHORA) and not E.caducado({"visto": AHORA - 3}, AHORA)
    assert not E.caducado(None, AHORA)


def test_la_hora_de_la_evidencia_es_la_de_quien_la_comprobo():
    """Lo que se sabe de un proceso lo comprobó el supervisor en su última vuelta; lo de MediaMTX, ahora."""
    tarea = {"pid": 44, "kbps": 5990.0, "desde": AHORA - 300, "reinicios": 0, "visto": AHORA - 1.5}
    assert E.de_ruta(PUSH, fuente=RECIBE, tarea=tarea, ahora=AHORA)["actualizado"] == AHORA - 1.5
    assert RECIBE["actualizado"] == AHORA


def test_textos_del_supervisor_que_no_son_de_ffmpeg():
    e = E.de_ruta(PUSH, fuente=RECIBE, tarea={"pid": None, "error": "Esperando la entrada", "intentos": 0}, ahora=AHORA)
    assert e["estado"] == "reconectando" and e["detalle"] == "El envío está a punto de arrancar"
    e = E.de_ruta(PUSH, fuente=RECIBE, tarea={"pid": None, "error": "No se pudo lanzar: [Errno 2] ffmpeg", "intentos": 0}, ahora=AHORA)
    assert "ffmpeg dijo" not in hechos(e) and "No se pudo lanzar" in hechos(e)
    agotada = {"pid": None, "error": "Sin más intentos tras 20 fallos seguidos: Connection refused", "intentos": 20}
    e = E.de_ruta(PUSH, fuente=RECIBE, tarea=agotada, ahora=AHORA)
    assert "El otro equipo rechaza la conexión. ffmpeg dijo: Connection refused" in hechos(e)


def test_flujo_heredado_sin_senal_lo_dice_a_su_manera():
    f = {"id": "rec_canal1", "activo": 1, "inicia": "hub", "protocolo": "srt"}
    e = E.de_fuente(f, None, ahora=AHORA, sin_senal="El flujo no recibe señal de su entrada")
    assert e["estado"] == "esperando_fuente" and e["detalle"] == "El flujo no recibe señal de su entrada"


def test_al_aire_y_reposo():
    enviando = E.de_ruta(PUSH, fuente=RECIBE, tarea={"pid": 44, "kbps": 5990.0}, ahora=AHORA)
    reintenta = E.de_ruta(PUSH, fuente=RECIBE, tarea={"pid": None, "error": "Connection refused", "intentos": 3}, ahora=AHORA)
    espera = E.de_ruta(PUSH, fuente=E.de_fuente(PUBLICA, None, ahora=AHORA), ahora=AHORA)
    assert E.al_aire(enviando) and E.al_aire(reintenta) and not E.al_aire(espera) and not E.al_aire(None)
    r = E.en_reposo(AHORA - 60, AHORA)
    assert r["estado"] == "desactivada" and r["nombre"] == "En reposo" and r["evidencia"][0]["desde"] == AHORA - 60
    web = {"id": "d", "inicia": "receptor", "protocolo": "webrtc"}
    e = E.de_ruta(web, fuente=RECIBE, info_destino=LISTO, conversion={}, tipo="entrega", ahora=AHORA)
    assert "señal convertida" in hechos(e)      # la conversión aún no tiene fila, pero la ruta lee de ella


def test_sin_el_servidor_del_hub_no_se_dice_que_la_direccion_esta_lista():
    # el destino WebRTC tiene la señal en su path, pero MediaMTX aún no ha encendido su servidor
    # WebRTC: quien abriera la dirección no encontraría a nadie. No es «esperando receptor»
    web = {"id": "d", "inicia": "receptor", "protocolo": "webrtc"}
    e = E.de_ruta(web, fuente=RECIBE, info_destino=LISTO, tipo="entrega", ahora=AHORA,
                  servidor={"nombre": "WebRTC", "motivo": ""})
    assert e["estado"] == "reconectando" and e["detalle"] == "El hub está encendiendo su servidor WebRTC"
    assert "MediaMTX aún no tiene encendido su servidor WebRTC" in hechos(e)
    # encendido (servidor=None), la misma ruta sí espera al receptor
    assert E.de_ruta(web, fuente=RECIBE, info_destino=LISTO, tipo="entrega", ahora=AHORA)["estado"] == "esperando_receptor"
    # si el puerto es de otro programa no se va a arreglar solo: error, y se dice aunque falte la fuente
    ocupado = {"nombre": "WebRTC", "motivo": "El puerto 8889/TCP está ocupado por otro programa"}
    sin_fuente = E.de_fuente(PUBLICA, None, ahora=AHORA)
    e = E.de_ruta(web, fuente=sin_fuente, tipo="entrega", ahora=AHORA, servidor=ocupado)
    assert e["estado"] == "error" and "no se puede encender" in e["detalle"]
    assert "El puerto 8889/TCP está ocupado por otro programa. Elige otro puerto en Ajustes" in hechos(e)
    assert not E.al_aire(e)


def test_fuente_que_publica_en_un_servidor_del_hub_aun_apagado():
    e = E.de_fuente(PUBLICA, None, ahora=AHORA, servidor={"nombre": "RTMP", "motivo": ""})
    assert e["estado"] == "esperando_fuente" and "encendiendo su servidor RTMP" in e["detalle"]
    e = E.de_fuente(PUBLICA, None, ahora=AHORA, servidor={"nombre": "RTMP", "motivo": "El puerto 1935/TCP está ocupado por otro programa"})
    assert e["estado"] == "error" and "El puerto 1935/TCP está ocupado" in hechos(e)
    # si la señal está llegando, el servidor está encendido digan lo que digan los apuntes
    assert E.de_fuente(PUBLICA, LISTO, ahora=AHORA, servidor={"nombre": "RTMP", "motivo": ""})["estado"] == "recibiendo"
