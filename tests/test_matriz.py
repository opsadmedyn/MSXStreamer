"""De las rutas guardadas a lo que el supervisor mantiene: paths, usuarios de MediaMTX y procesos."""

import json

import pytest

import db
import matriz

H264_AAC = {"video": {"codec": "h264", "ancho": 1920, "alto": 1080, "fps": 29.97, "b": False},
            "audio": [{"codec": "aac", "canales": 2, "hz": 48000}]}
LEER = "srt://127.0.0.1:8890?streamid=read:"


@pytest.fixture
def c():
    c = db.conectar()
    for tabla in ("rutas", "inspecciones", "fuentes", "destinos", "perfiles", "ajustes", "flujos"):
        c.execute(f"DELETE FROM {tabla}")
    return c


def inspeccionar(c, fuente, pistas):
    c.execute("INSERT OR REPLACE INTO inspecciones(fuente, t, pistas) VALUES (?, 0, ?)", (fuente, json.dumps(pistas)))


def listos(*paths):
    return {p: {"ready": True} for p in paths}


def aceptacion(c):
    """La prueba principal: una fuente RTMP hacia SRT, HLS y WebRTC a la vez."""
    f = matriz.crear(c, "fuentes", nombre="Estudio A", protocolo="rtmp", inicia="origen", clave="clave-de-publicar")
    srt = matriz.crear(c, "destinos", nombre="Receptor SRT", protocolo="srt", inicia="hub",
                       url="srt://198.51.100.7:9000", passphrase="frase-de-paso-1", reconexion={"cada_s": 5})
    hls = matriz.crear(c, "destinos", nombre="Reproductor HLS", protocolo="hls", inicia="receptor")
    web = matriz.crear(c, "destinos", nombre="Monitor WebRTC", protocolo="webrtc", inicia="receptor", clave="clave-de-leer")
    for d in (srt, hls, web):
        matriz.conectar(c, d, f, por="pruebas")
    return f, srt, hls, web


def test_sin_inspeccion_solo_existe_la_fuente(c):
    f, srt, hls, web = aceptacion(c)
    r = matriz.calcular(c)
    assert r["paths"] == {f: {"source": "publisher", "srtPublishPassphrase": ""}}
    assert r["tareas"] == {} and all(p["provisional"] for p in r["planes"].values())
    # el origen publica con la clave de la fuente; MediaMTX no la guarda en claro
    u = next(u for u in r["usuarios"] if u["permissions"][0]["action"] == "publish")
    assert u["permissions"] == [{"action": "publish", "path": f}]
    # las reglas de lectura de los destinos existen desde que se crean, haya señal o no
    assert r["abiertos"]["read"] == {hls} and len(r["usuarios"]) == 2
    assert u["user"] == matriz.huella_mtx(f) and u["pass"].startswith("sha256:") and "clave-de-publicar" not in json.dumps(u)


def test_la_prueba_de_aceptacion_con_la_fuente_inspeccionada(c):
    f, srt, hls, web = aceptacion(c)
    inspeccionar(c, f, H264_AAC)
    r = matriz.calcular(c)                      # aún no se sabe si hay señal
    firma = r["planes"][web]["senal"]["firma"]
    conv = tarea_conv = f"conv_{f}_{firma}"
    assert set(r["tareas"]) == {f"ruta_{srt}", tarea_conv}
    cmd, espera_a, error = r["tareas"][f"ruta_{srt}"]
    assert espera_a == f and error is None and cmd[cmd.index("-i") + 1] == LEER + f and "copy" in cmd
    cmd, espera_a, _ = r["tareas"][tarea_conv]
    assert espera_a == f and "libopus" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"     # solo se convierte el audio
    assert r["politicas"][f"ruta_{srt}"] == {"cada_s": 5.0, "max_intentos": 0}
    assert set(r["paths"]) == {f, conv}         # los destinos que el hub entrega esperan a la señal
    assert r["senales"] == {srt: f, hls: f, web: conv}

    r = matriz.calcular(c, listos(f, conv))
    assert r["paths"][hls] == {"source": LEER + f, "sourceOnDemand": False, "srtReadPassphrase": "", "forward": []}
    assert r["paths"][web]["source"] == LEER + conv
    assert r["abiertos"]["read"] == {hls}                                # HLS sin clave: lo lee cualquiera
    lectura = [u for u in r["usuarios"] if u["permissions"][0]["action"] == "read"]
    assert [u["permissions"][0]["path"] for u in lectura] == [web]       # WebRTC: con la clave del destino

    r = matriz.calcular(c, listos(f))           # la conversión aún no publica: el monitor espera
    assert hls in r["paths"] and web not in r["paths"]


def test_dos_destinos_con_el_mismo_perfil_comparten_una_conversion(c):
    f = matriz.crear(c, "fuentes", nombre="Cámara", protocolo="srt", inicia="origen")
    inspeccionar(c, f, H264_AAC)
    d1 = matriz.crear(c, "destinos", nombre="Monitor 1", protocolo="webrtc", inicia="receptor")
    d2 = matriz.crear(c, "destinos", nombre="Monitor 2", protocolo="webrtc", inicia="receptor")
    d3 = matriz.crear(c, "destinos", nombre="Plataforma", protocolo="rtmp", inicia="hub", url="rtmp://a.example/live", clave="k")
    d4 = matriz.crear(c, "destinos", nombre="Socio", protocolo="srt", inicia="hub", url="srt://203.0.113.5:7001")
    matriz.conectar(c, d1, f); matriz.conectar(c, d2, f)
    matriz.conectar(c, d3, f, perfil="720p-3"); matriz.conectar(c, d4, f, perfil="720p-3")
    r = matriz.calcular(c)
    conversiones = [t for t in r["tareas"] if t.startswith("conv_")]
    assert len(conversiones) == 2               # una para los dos monitores y otra para los dos de 720p
    assert r["senales"][d1] == r["senales"][d2] and r["senales"][d3] == r["senales"][d4] != r["senales"][d1]
    cmd = r["tareas"][f"ruta_{d3}"][0]
    assert r["tareas"][f"ruta_{d3}"][1] == r["senales"][d3] and cmd[-1] == "rtmp://a.example/live/k"
    assert r["abiertos"]["publish"] == {f}      # fuente sin clave: publica cualquiera


def test_fuentes_que_el_hub_va_a_buscar_llevan_proceso_y_no_usuario(c):
    f1 = matriz.crear(c, "fuentes", nombre="Remota", protocolo="srt", inicia="hub", url="srt://203.0.113.20:9000",
                      passphrase="frase-de-paso-1")
    f2 = matriz.crear(c, "fuentes", nombre="Canal HLS", protocolo="hls", inicia="hub", url="https://cdn.example/m.m3u8",
                      opciones={"variante": 1})
    f3 = matriz.crear(c, "fuentes", nombre="Cámara", protocolo="srt", inicia="origen", passphrase="frase-de-paso-2", clave="k1")
    matriz.crear(c, "fuentes", nombre="Apagada", protocolo="srt", inicia="hub", url="srt://203.0.113.21:9000", activo=0)
    r = matriz.calcular(c)
    assert set(r["tareas"]) == {f"ent_{f1}", f"ent_{f2}"}
    assert r["tareas"][f"ent_{f1}"][1] is None                      # no espera a ningún path
    assert set(r["paths"]) == {f1, f2, f3}
    assert r["paths"][f1]["srtPublishPassphrase"] == ""             # publica el ffmpeg local
    assert r["paths"][f3]["srtPublishPassphrase"] == "frase-de-paso-2"
    assert len(r["usuarios"]) == 1 and r["abiertos"] == {"publish": set(), "read": set()}


def test_reenvio_whip_y_destino_srt_con_passphrase(c):
    f = matriz.crear(c, "fuentes", nombre="Cabina", protocolo="webrtc", inicia="origen", clave="k")
    inspeccionar(c, f, {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "b": False},
                        "audio": [{"codec": "opus", "canales": 2, "hz": 48000}]})
    whip = matriz.crear(c, "destinos", nombre="Sala", protocolo="webrtc", inicia="hub",
                        url="https://sala.example/live/whip", clave="token-whip")
    srt = matriz.crear(c, "destinos", nombre="Emisora", protocolo="srt", inicia="receptor",
                       passphrase="frase-de-paso-3", clave="clave-emisora")
    matriz.conectar(c, whip, f); matriz.conectar(c, srt, f)
    r = matriz.calcular(c, listos(f))
    assert r["tareas"] == {}                                        # todo en copia y servido por MediaMTX
    assert r["paths"][whip]["forward"] == [{"dest": "whips://sala.example/live/whip", "whipBearerToken": "token-whip"}]
    assert r["paths"][srt]["srtReadPassphrase"] == "frase-de-paso-3" and r["paths"][srt]["forward"] == []
    assert whip not in r["abiertos"]["read"]                        # el path del reenvío no se abre a la red
    assert [u["permissions"][0]["path"] for u in r["usuarios"] if u["permissions"][0]["action"] == "read"] == [srt]


def test_fuente_desactivada_y_adaptadores_sin_instalar(c):
    f = matriz.crear(c, "fuentes", nombre="Apagada", protocolo="rtmp", inicia="origen", activo=0)
    ndi = matriz.crear(c, "fuentes", nombre="Estudio NDI", protocolo="ndi", inicia="hub", opciones={"nombre": "PC (Cam 1)"})
    d1 = matriz.crear(c, "destinos", nombre="Socio", protocolo="srt", inicia="hub", url="srt://203.0.113.5:7001")
    d2 = matriz.crear(c, "destinos", nombre="Estudio Zixi", protocolo="zixi", inicia="hub", url="zixi://z.example:2088/canal")
    matriz.conectar(c, d1, f); matriz.conectar(c, d2, ndi)
    r = matriz.calcular(c, adaptadores={"ndi": {"listo": False, "falta": "Falta el SDK de NDI"}})
    assert r["impedimentos"] == {d1: "La fuente está desactivada"} and f not in r["paths"]
    assert r["tareas"][f"ent_{ndi}"] == (None, None, "Falta el SDK de NDI")
    assert r["tareas"][f"ruta_{d2}"] == (None, None, "Falta el adaptador Zixi")
    assert r["planes"][d2]["adaptador"] == "zixi"


def test_cambiar_la_fuente_de_un_destino_sustituye_la_ruta(c):
    f1 = matriz.crear(c, "fuentes", nombre="Uno", protocolo="rtmp", inicia="origen")
    f2 = matriz.crear(c, "fuentes", nombre="Dos", protocolo="rtmp", inicia="origen")
    d = matriz.crear(c, "destinos", nombre="Aire", protocolo="hls", inicia="receptor", perfil="720p-3", audio="2")
    matriz.conectar(c, d, f1, por="ana")
    assert matriz.rutas(c)[0]["perfil"] == "720p-3" and matriz.rutas(c)[0]["audio"] == "2"   # los del destino
    matriz.conectar(c, d, f2, perfil="original", audio="todas", por="luis")
    (ruta,) = matriz.rutas(c)
    assert (ruta["fuente"], ruta["perfil"], ruta["por"]) == (f2, "original", "luis")
    assert matriz.desconectar(c, d) and not matriz.rutas(c) and not matriz.desconectar(c, d)
    with pytest.raises(KeyError):
        matriz.conectar(c, d, "no-existe")
    assert matriz.borrar(c, "fuentes", f2) and not matriz.borrar(c, "fuentes", f2)
    matriz.conectar(c, d, f1)
    assert matriz.borrar(c, "fuentes", f1) and not matriz.rutas(c)  # borrar una fuente se lleva sus rutas
    f3 = matriz.crear(c, "fuentes", nombre="Tres", protocolo="rtmp", inicia="origen")
    matriz.conectar(c, d, f3)
    assert matriz.borrar(c, "destinos", d) and not matriz.rutas(c)  # y borrar un destino, la suya


def test_direcciones_que_el_hub_entrega_con_y_sin_claves(c):
    n = matriz.red({"host_publico": "ezc80.example", "rtmp_puerto": 1945, "hls_url": "https://ezc80.example/hls/",
                    "webrtc_url": "https://ezc80.example:8889"})
    f = {"id": "estudio-a1b2", "protocolo": "rtmp", "inicia": "origen", "clave": "CLAVE", "passphrase": "", "latencia_ms": 400}
    d = matriz.direccion(f, n, "fuente", ver_claves=True)
    assert d["url"] == "rtmp://ezc80.example:1945/estudio-a1b2?user=estudio-a1b2&pass=CLAVE" and d["con_claves"]
    d = matriz.direccion(f, n, "fuente")
    assert "CLAVE" not in json.dumps(d) and d["url"].endswith("pass=***") and d["protegida"]
    srt = {"id": "camara-c3d4", "protocolo": "srt", "inicia": "origen", "clave": "CLAVE", "passphrase": "frase-de-paso-1",
           "latencia_ms": 250}
    d = matriz.direccion(srt, n, "fuente", ver_claves=True)
    assert d["url"] == "srt://ezc80.example:8890?streamid=publish:camara-c3d4:camara-c3d4:CLAVE&latency=250000&passphrase=frase-de-paso-1"
    assert {x["nombre"]: x["valor"] for x in d["campos"]}["Stream ID"] == "publish:camara-c3d4:camara-c3d4:CLAVE"
    assert "frase-de-paso-1" not in json.dumps(matriz.direccion(srt, n, "fuente"))
    propio = dict(srt, opciones='{"puerto": 9101}')
    assert matriz.direccion(propio, n, "fuente", True)["url"] == "srt://ezc80.example:9101?latency=250000&passphrase=frase-de-paso-1"
    hls = {"id": "aire-e5f6", "protocolo": "hls", "inicia": "receptor", "clave": "", "passphrase": "", "latencia_ms": 300}
    assert matriz.direccion(hls, n, "destino") == {"url": "https://ezc80.example/hls/aire-e5f6/index.m3u8", "campos": [],
                                                   "con_claves": False, "protegida": False}
    web = dict(hls, protocolo="webrtc", clave="CLAVE")
    d = matriz.direccion(web, n, "destino", ver_claves=True)
    assert d["url"] == "https://ezc80.example:8889/aire-e5f6/whep" and d["pagina"] == "https://ezc80.example:8889/aire-e5f6/"
    assert d["campos"][0]["valor"] == "aire-e5f6:CLAVE"
    # lo que el hub alimenta no tiene dirección que entregar
    assert matriz.direccion(dict(hls, protocolo="rtmp", inicia="hub"), n, "destino") is None
    assert matriz.direccion(dict(f, inicia="hub"), n, "fuente") is None


def test_identificadores_unicos_entre_fuentes_destinos_y_flujos(c):
    a = matriz.crear(c, "fuentes", nombre="Cámara Ñandú 1", protocolo="srt", inicia="origen")
    b = matriz.crear(c, "destinos", nombre="Cámara Ñandú 1", protocolo="hls", inicia="receptor")
    assert a != b and a.startswith("camara-nandu-1-") and b.startswith("camara-nandu-1-")
    assert matriz.crear(c, "fuentes", nombre="¿?", protocolo="srt", inicia="origen").startswith("fuente-")
    with pytest.raises(ValueError):
        matriz.crear(c, "fuentes", nombre="x", protocolo="srt", inicia="origen", id="otro")
    assert matriz.editar(c, "fuentes", a, nombre="Otra", opciones={"puerto": 9101})
    assert matriz.fuentes(c)[0]["opciones"] == '{"puerto": 9101}' and not matriz.editar(c, "fuentes", "no-existe", nombre="x")


def test_politica_de_reconexion_y_claves_del_hub(c):
    assert matriz.reconexion({"reconexion": ""}) == {"cada_s": 0, "max_intentos": 0}
    assert matriz.reconexion({"reconexion": '{"cada_s": 5, "max_intentos": 20}'}) == {"cada_s": 5.0, "max_intentos": 20}
    assert matriz.reconexion({"reconexion": '{"cada_s": "mal"}'}) == {"cada_s": 0, "max_intentos": 0}
    assert matriz.reconexion({"reconexion": '{"cada_s": 99999, "max_intentos": -3}'}) == {"cada_s": 600.0, "max_intentos": 0}
    matriz.crear(c, "fuentes", nombre="A", protocolo="rtmp", inicia="hub", url="rtmp://o.example/app/CLAVE-RUTA")
    matriz.crear(c, "destinos", nombre="B", protocolo="srt", inicia="hub", url="srt://h:1?streamid=S1D", passphrase="frase-de-paso-9")
    db.guardar_ajuste(c, "webrtc", {"ice": [{"url": "turn:turn.example:3478", "usuario": "hub", "clave": "turn-secreta"}]})
    assert {"CLAVE-RUTA", "S1D", "frase-de-paso-9", "turn-secreta"} <= matriz.secretos(c)
    assert len(matriz.nueva_clave()) == 20


# ---------------------------------------------------------------- flujos heredados de la 0.3.9
def flujo(c, fid, nombre, entrada="pull", canal="", directo=0, activo=1):
    c.execute("INSERT INTO flujos(id, nombre, entrada, url, canal, directo, activo) VALUES (?,?,?,?,?,?,?)",
              (fid, nombre, entrada, "srt://203.0.113.5:9000" if entrada != "recorder" else "", canal, directo, activo))


def test_los_flujos_de_la_version_anterior_son_fuentes_del_hub(c):
    flujo(c, "canal-int-1a2b", "Internacional", entrada="hub")
    flujo(c, "grabado-3c4d", "Canal 1 del Recorder", entrada="recorder", canal="canal1")
    flujo(c, "otro-5e6f", "Canal 1 otra vez", entrada="recorder", canal="canal1")
    flujo(c, "entrelazado-7a8b", "1080i directo", entrada="hub", directo=1)
    c.execute("INSERT INTO composiciones(flujo, activa) VALUES ('canal-int-1a2b', 1), ('grabado-3c4d', 0)")
    hs = {f["id"]: f for f in matriz.heredadas(c)}
    assert list(hs) == ["canal-int-1a2b", "comp_canal-int-1a2b", "rec_canal1", "comp_grabado-3c4d"]
    assert hs["canal-int-1a2b"]["heredada"] == {"flujo": "canal-int-1a2b", "senal": "limpia", "entrada": "hub"}
    assert hs["comp_canal-int-1a2b"]["nombre"] == "Internacional · compuesta" and hs["comp_canal-int-1a2b"]["activo"]
    assert not hs["comp_grabado-3c4d"]["activo"]                    # composición guardada pero apagada
    assert "entrelazado-7a8b" in {f["id"] for f in matriz.heredadas(c, con_directos=True)}


def test_una_ruta_lleva_un_flujo_heredado_y_su_composicion_a_destinos_del_hub(c):
    flujo(c, "canal-int-1a2b", "Internacional", entrada="hub")
    c.execute("INSERT INTO composiciones(flujo, activa) VALUES ('canal-int-1a2b', 1)")
    srt = matriz.crear(c, "destinos", nombre="Socio", protocolo="srt", inicia="hub", url="srt://198.51.100.7:9000")
    web = matriz.crear(c, "destinos", nombre="Monitor", protocolo="webrtc", inicia="receptor")
    matriz.conectar(c, srt, "canal-int-1a2b")
    matriz.conectar(c, web, "comp_canal-int-1a2b")
    inspeccionar(c, "canal-int-1a2b", H264_AAC)
    inspeccionar(c, "comp_canal-int-1a2b", H264_AAC)
    r = matriz.calcular(c, listos("canal-int-1a2b", "comp_canal-int-1a2b"))
    # el path y la entrada del flujo siguen siendo cosa del supervisor de siempre: el hub no los declara
    assert "canal-int-1a2b" not in r["paths"] and "comp_canal-int-1a2b" not in r["paths"]
    assert not [t for t in r["tareas"] if t.startswith("ent_")] and not r["usuarios"]
    assert {f["id"] for f in r["fuentes"]} == {"canal-int-1a2b", "comp_canal-int-1a2b"}     # se inspeccionan igual
    envio = r["tareas"][matriz.id_ruta(srt)]
    assert LEER + "canal-int-1a2b" in envio[0] and envio[1] == "canal-int-1a2b"
    senal = r["senales"][web]                       # la compuesta, con el audio en Opus para WebRTC
    assert senal.startswith("conv_comp_canal-int-1a2b_") and matriz.es_del_hub(senal)
    assert r["tareas"][senal][1] == "comp_canal-int-1a2b" and r["paths"][senal]["source"] == "publisher"


def test_flujo_detenido_o_borrado(c):
    flujo(c, "canal-int-1a2b", "Internacional", activo=0)
    flujo(c, "entrelazado-7a8b", "1080i directo", directo=1)
    d = matriz.crear(c, "destinos", nombre="Socio", protocolo="srt", inicia="hub", url="srt://198.51.100.7:9000")
    matriz.conectar(c, d, "canal-int-1a2b")
    r = matriz.calcular(c, {})
    assert r["impedimentos"][d] == matriz.FUENTE_PARADA and not r["tareas"]
    with pytest.raises(KeyError):
        matriz.conectar(c, d, "entrelazado-7a8b")   # sin MediaMTX no hay de dónde leer
    assert matriz.limpiar_huerfanas(c) == 0         # el flujo sigue existiendo: la ruta se queda
    c.execute("DELETE FROM flujos WHERE id='canal-int-1a2b'")
    assert matriz.limpiar_huerfanas(c) == 1 and not matriz.rutas(c)
