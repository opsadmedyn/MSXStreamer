"""La API del hub por HTTP, a través del panel de verdad: permisos por rol, claves, rutas al aire,
registro de cambios. MediaMTX se simula (lo que diría su API); la sesión, también (lo que diría el
Recorder de quien trae la cookie)."""

import json
import socket
import time

import pytest
from fastapi.testclient import TestClient

import api_hub
import db
import estados
import matriz
import servidor
import sondas

H264_AAC = {"video": {"codec": "h264", "ancho": 1920, "alto": 1080, "fps": 29.97, "b": False},
            "audio": [{"codec": "aac", "canales": 2, "hz": 48000}]}
TABLAS = ("rutas", "inspecciones", "senales", "fuentes", "destinos", "perfiles", "ajustes", "flujos", "cambios",
          "ordenes", "estado_salidas", "eventos")


class Como:
    """Un usuario del Recorder con ese rol hablando con el panel (la cookie dice quién es)."""

    def __init__(self, cliente, rol):
        self.cliente, self.cabeceras = cliente, {"Cookie": f"msr_sesion={rol}"}

    def __getattr__(self, metodo):
        return lambda url, **mas: getattr(self.cliente, metodo)(url, headers=self.cabeceras, **mas)


@pytest.fixture
def c():
    c = db.conectar()
    for tabla in TABLAS:
        c.execute(f"DELETE FROM {tabla}")
    return c


@pytest.fixture
def mtx_falso(monkeypatch):
    """Lo que diría MediaMTX: se rellena en cada prueba. paths=None: MediaMTX no responde."""
    falso = {"paths": {}, "conexiones": {}, "hls": {}}

    monkeypatch.setattr(api_hub, "_mediamtx", lambda: (falso["paths"], falso["conexiones"], falso["hls"]))

    def sin_mediamtx(*_, **__):             # aunque en este equipo corra un MediaMTX de verdad (el banco), aquí no se le pregunta
        raise api_hub.mtx.ErrorMTX("MediaMTX no responde")
    monkeypatch.setattr(api_hub.mtx, "_pedir", sin_mediamtx)
    # cada consulta mira de nuevo: las pruebas cambian el estado entre dos peticiones seguidas
    falso["con_cache"] = api_hub._estado_del_hub
    monkeypatch.setattr(api_hub, "_estado_del_hub", api_hub._estado_completo)
    return falso


@pytest.fixture
def panel(c, mtx_falso, monkeypatch):
    monkeypatch.setattr(servidor, "_sesion_recorder",
                        lambda token: {"email": f"{token}@ejemplo.com", "rol": token, "modulos": ["streamer"]})
    monkeypatch.setattr(servidor, "_paths_vista", (0.0, frozenset(), frozenset()))
    api_hub._olvidar()
    api_hub._muestras.clear()
    cliente = TestClient(servidor.app)
    return {rol: Como(cliente, rol) for rol in ("admin", "manager", "operador", "invitado")} | {"anonimo": cliente}


def lista(path, **mas):
    return {"name": path, "ready": True, "readyTime": "2026-11-19T06:00:00Z", "tracks": ["H264", "MPEG-4 Audio"],
            "source": {"type": "rtmpConn", "id": "c1"}, "readers": [], "bytesReceived": 1000, "bytesSent": 0, **mas}


def inspeccionar(c, fuente, pistas=H264_AAC):
    c.execute("INSERT OR REPLACE INTO inspecciones(fuente, t, pistas) VALUES (?, ?, ?)", (fuente, time.time(), json.dumps(pistas)))


def fuente_rtmp(panel, nombre="Estudio A"):
    r = panel["admin"].post("/api/fuentes", json={"nombre": nombre, "protocolo": "rtmp", "inicia": "origen"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def destino_srt(panel, nombre="Receptor SRT", **mas):
    r = panel["admin"].post("/api/destinos", json={"nombre": nombre, "protocolo": "srt", "inicia": "hub",
                                                    "url": "srt://203.0.113.20:9000", **mas})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def con_senal(c, mtx_falso, fuente):
    """La fuente publica y ya está inspeccionada."""
    mtx_falso["paths"][fuente] = lista(fuente)
    mtx_falso["conexiones"][("rtmpConn", "c1")] = {"protocolo": "RTMP", "remoto": "203.0.113.9:51000", "estado": "publish",
                                                    "path": fuente, "desde": "2026-11-19T06:00:00Z", "recibidos": 1000, "enviados": 0}
    inspeccionar(c, fuente)


def cambios(c):
    return [(r["accion"], r["objeto"], r["detalle"], r["quien"]) for r in db.filas(c, "SELECT * FROM cambios ORDER BY rowid")]


# ---------------------------------------------------------------- permisos
def test_todas_las_operaciones_del_hub_declaran_su_permiso():
    assert len(api_hub.router.routes) > 30
    for ruta in api_hub.router.routes:
        permisos = [getattr(d.dependency, "permiso", None) for d in ruta.dependencies]
        assert permisos and all(p in api_hub.PERMISOS for p in permisos), ruta.path
        assert api_hub.PROPIAS.match(ruta.path), ruta.path      # y el panel sabe que el permiso lo pone el hub
        if ruta.methods - {"GET"} and ruta.path not in ("/api/rutas/plan", "/api/rutas/{did}",
                                                        "/api/fuentes/{fid}/probar", "/api/destinos/{did}/probar"):
            assert permisos != ["ver"], ruta.path               # cambiar algo nunca es solo «ver»
    # lo que no es del hub sigue como estaba: solo un administrador
    assert not api_hub.PROPIAS.match("/api/flujos") and not api_hub.PROPIAS.match("/api/salidas/x/iniciar")


def test_cada_rol_puede_lo_suyo(panel, c):
    assert panel["anonimo"].get("/api/matriz").status_code == 401
    nueva = {"nombre": "Cámara 1", "protocolo": "srt", "inicia": "origen"}
    assert panel["operador"].get("/api/matriz").status_code == 200
    r = panel["operador"].post("/api/fuentes", json=nueva)
    assert r.status_code == 403 and "operador" in r.json()["detail"] and "crear y editar fuentes" in r.json()["detail"].lower()
    fid = panel["manager"].post("/api/fuentes", json=nueva).json()["id"]
    assert panel["manager"].get(f"/api/fuentes/{fid}/claves").status_code == 403       # editar no es ver claves
    assert panel["manager"].put("/api/ajustes/permisos", json={"operador": [], "manager": []}).status_code == 403
    assert panel["admin"].get(f"/api/fuentes/{fid}/claves").status_code == 200
    # un rol que el hub no conoce puede lo mismo que un operador
    assert panel["invitado"].get("/api/matriz").json()["permisos"] == ["conectar_libre", "ver"]
    assert panel["invitado"].delete(f"/api/fuentes/{fid}").status_code == 403
    # y fuera del hub, lo de siempre: lo que no es GET es del administrador
    r = panel["manager"].post("/api/flujos", json={})
    assert r.status_code == 403 and r.json()["detail"] == "Solo un administrador puede hacer esto"


def test_la_tabla_de_permisos_se_cambia_en_ajustes(panel, c):
    r = panel["admin"].put("/api/ajustes/permisos", json={"operador": ["ver", "conectar_libre", "editar"], "manager": ["ver"]})
    assert r.status_code == 200 and r.json()["tabla"]["admin"] == list(api_hub.PERMISOS)      # el admin no se toca
    assert panel["operador"].post("/api/fuentes", json={"nombre": "X", "protocolo": "rtmp", "inicia": "origen"}).status_code == 200
    assert panel["manager"].post("/api/fuentes", json={"nombre": "Y", "protocolo": "rtmp", "inicia": "origen"}).status_code == 403
    assert panel["admin"].put("/api/ajustes/permisos", json={"operador": ["mandar"], "manager": []}).status_code == 422
    accion, _, detalle, quien = cambios(c)[0]
    assert accion == "Cambió los permisos" and quien == "admin@ejemplo.com"
    assert "operador ya puede crear y editar fuentes y destinos" in detalle and "manager ya no puede modificar una ruta al aire" in detalle
    # sin «ver» no se ve nada del hub
    panel["admin"].put("/api/ajustes/permisos", json={"operador": [], "manager": ["ver"]})
    assert panel["operador"].get("/api/matriz").status_code == 403


# ---------------------------------------------------------------- claves
def test_las_claves_de_un_equipo_ajeno_se_escriben_y_no_vuelven_a_salir(panel, c):
    secretos = ["frase-de-paso-ajena", "#!::r=canal,password=p4ss", "clave-de-emision-yt", "token-whip-1234", "firma-en-la-url"]
    srt = destino_srt(panel, url="srt://203.0.113.20:9000?token=firma-en-la-url", passphrase=secretos[0], streamid=secretos[1])
    rtmp = panel["admin"].post("/api/destinos", json={"nombre": "YouTube", "protocolo": "rtmp", "inicia": "hub",
                                                       "url": "rtmp://a.rtmp.youtube.com/live2", "clave": secretos[2]}).json()["id"]
    whip = panel["admin"].post("/api/destinos", json={"nombre": "Sala", "protocolo": "webrtc", "inicia": "hub",
                                                       "url": "https://sala.example/whip/1", "clave": secretos[3]}).json()["id"]
    local = panel["admin"].post("/api/destinos", json={"nombre": "Restreamer", "protocolo": "rtmp", "inicia": "hub",
                                                        "url": "rtmp://127.0.0.1:1935/live/clave-del-restreamer"}).json()["id"]
    for rol in ("admin", "operador"):                   # ni siquiera a quien puede ver claves
        texto = panel[rol].get("/api/matriz").text
        assert not any(s in texto for s in [*secretos, "clave-del-restreamer", "p4ss"])
    d = {x["id"]: x for x in panel["admin"].get("/api/destinos").json()}
    assert d[srt]["remoto"] == {"url": "srt://203.0.113.20:9000?***", "url_con_claves": True, "tiene_streamid": True,
                                "tiene_passphrase": True, "tiene_clave": False}
    assert d[rtmp]["remoto"]["url"] == "rtmp://a.rtmp.youtube.com/live2" and d[rtmp]["remoto"]["tiene_clave"]
    assert d[local]["remoto"]["url"] == "rtmp://127.0.0.1:1935/live/***"       # otro servidor en este mismo equipo
    assert d[srt]["modo"] == "conecta" and d[srt]["direccion"] is None and d[whip]["quien_inicia"] == "El hub publica en el destino (WHIP)"
    assert panel["admin"].get(f"/api/destinos/{srt}/claves").status_code == 404        # no hay nada que enseñar
    # editar devolviendo la dirección tal como se enseñó conserva la guardada; lo que no se manda, también
    r = panel["admin"].put(f"/api/destinos/{srt}", json={"nombre": "Receptor del socio", "url": "srt://203.0.113.20:9000?***"})
    assert r.json() == {"ok": True, "cambios": ["nombre"]}
    fila = dict(c.execute("SELECT * FROM destinos WHERE id=?", (srt,)).fetchone())
    assert fila["url"].endswith("?token=firma-en-la-url") and fila["passphrase"] == secretos[0] and fila["streamid"] == secretos[1]
    # y una cadena vacía la quita
    assert panel["admin"].put(f"/api/destinos/{srt}", json={"passphrase": ""}).json()["cambios"] == ["passphrase"]
    assert c.execute("SELECT passphrase FROM destinos WHERE id=?", (srt,)).fetchone()["passphrase"] == ""
    assert not any(s in json.dumps(cambios(c)) for s in secretos)


def test_las_claves_que_entrega_el_hub_solo_se_ven_con_permiso_y_queda_anotado(panel, c):
    fid = fuente_rtmp(panel)
    clave = c.execute("SELECT clave FROM fuentes WHERE id=?", (fid,)).fetchone()["clave"]
    assert len(clave) == 20                             # la genera el hub
    for rol in ("operador", "manager", "admin"):        # en la lista nadie la ve: hay que pedirla aposta
        f = panel[rol].get("/api/fuentes").json()[0]
        assert clave not in json.dumps(f) and f["direccion"]["url"] == f"rtmp://127.0.0.1:1935/{fid}?user={fid}&pass=***"
        assert f["direccion"]["protegida"] and not f["direccion"]["con_claves"]
    assert panel["operador"].get(f"/api/fuentes/{fid}/claves").status_code == 403
    r = panel["admin"].get(f"/api/fuentes/{fid}/claves")
    assert r.json()["url"].endswith(f"pass={clave}") and r.headers["cache-control"] == "no-store"
    assert ("Consultó una clave", "Estudio A", "el valor no queda registrado", "admin@ejemplo.com") in cambios(c)
    nueva = panel["admin"].post(f"/api/fuentes/{fid}/clave-nueva").json()["url"].rpartition("pass=")[2]
    assert nueva != clave and c.execute("SELECT clave FROM fuentes WHERE id=?", (fid,)).fetchone()["clave"] == nueva
    assert panel["manager"].post(f"/api/fuentes/{fid}/clave-nueva").status_code == 403
    assert clave not in json.dumps(cambios(c)) and nueva not in json.dumps(cambios(c))
    # un destino HLS nace sin clave (lo abre un reproductor); uno SRT que el receptor viene a buscar, con ella
    hls = panel["admin"].post("/api/destinos", json={"nombre": "Web", "protocolo": "hls", "inicia": "receptor"}).json()["id"]
    srt = panel["admin"].post("/api/destinos", json={"nombre": "Socio", "protocolo": "srt", "inicia": "receptor",
                                                      "passphrase": "frase-del-hub-1"}).json()["id"]
    d = {x["id"]: x for x in panel["operador"].get("/api/destinos").json()}
    assert d[hls]["direccion"] == {"url": f"http://127.0.0.1:8888/{hls}/index.m3u8", "campos": [], "con_claves": False, "protegida": False}
    assert d[srt]["modo"] == "entrega" and "frase-del-hub-1" not in json.dumps(d[srt]) and "passphrase=***" in d[srt]["direccion"]["url"]
    assert "frase-del-hub-1" in panel["admin"].get(f"/api/destinos/{srt}/claves").json()["url"]
    assert panel["admin"].post("/api/destinos", json={"nombre": "Mal", "protocolo": "srt", "inicia": "receptor",
                                                       "clave": "corta"}).status_code == 422


def test_un_error_de_ffmpeg_con_una_clave_dentro_no_llega_al_panel(panel, c, mtx_falso):
    fid = fuente_rtmp(panel)
    did = destino_srt(panel, passphrase="frase-de-paso-ajena")
    con_senal(c, mtx_falso, fid)
    assert panel["admin"].put(f"/api/rutas/{did}", json={"fuente": fid}).status_code == 200
    c.execute("INSERT INTO estado_salidas(salida, error, intentos, visto) VALUES (?,?,?,?)",
              ("ruta_" + did, "Connection to srt://203.0.113.20:9000?passphrase=frase-de-paso-ajena failed: timed out", 3, time.time()))
    texto = panel["admin"].get("/api/matriz").text
    assert "frase-de-paso-ajena" not in texto and "El otro equipo no responde" in texto and "srt://203.0.113.20:9000?***" in texto


# ---------------------------------------------------------------- validación de lo que se guarda
def test_lo_que_no_se_puede_guardar_se_dice_claro(panel, c):
    def crear(tabla, **datos):
        r = panel["admin"].post(f"/api/{tabla}", json=datos)
        return r.status_code, (r.json().get("detail") if r.status_code != 200 else "")
    assert crear("fuentes", nombre="", protocolo="rtmp", inicia="origen") == (422, "Falta el nombre")
    assert crear("fuentes", nombre="X", protocolo="hls", inicia="origen")[1] == "HLS no admite ese modo de conexión como fuente"
    assert crear("fuentes", nombre="X", protocolo="srt", inicia="hub", url="203.0.113.20:9000")[1].startswith("La dirección no es válida")
    assert crear("fuentes", nombre="X", protocolo="srt", inicia="hub", url="srt://h:9000", passphrase="corta")[1].startswith("La passphrase")
    assert crear("destinos", nombre="X", protocolo="hls", inicia="hub")[0] == 422
    assert crear("destinos", nombre="X", protocolo="rtmp", inicia="hub", url="rtmp://h/app", perfil="no-existe")[1] == "Ese perfil no existe"
    assert crear("destinos", nombre="X", protocolo="rtmp", inicia="hub", url="rtmp://h/app", audio="tres")[0] == 422
    assert crear("destinos", nombre="X", protocolo="rtmp", inicia="hub", url="rtmp://h/app", reconexion={"cada_s": -1})[0] == 422
    assert crear("fuentes", nombre="X", protocolo="ndi", inicia="hub", opciones={})[1] == "Falta el nombre de la fuente NDI"
    assert crear("fuentes", nombre="X", protocolo="ndi", inicia="hub", opciones={"nombre": "CAM", "password": "x"})[0] == 422
    assert c.execute("SELECT count(*) n FROM fuentes").fetchone()["n"] == 0 and c.execute("SELECT count(*) n FROM destinos").fetchone()["n"] == 0
    # el protocolo y quién inicia no se cambian después
    fid = fuente_rtmp(panel)
    assert panel["admin"].put(f"/api/fuentes/{fid}", json={"protocolo": "srt"}).status_code == 422
    # SRT con puerto propio: sin clave (no hay stream ID donde mandarla) y sin chocar con otro puerto del hub
    a = panel["admin"].post("/api/fuentes", json={"nombre": "Cámara", "protocolo": "srt", "inicia": "origen", "puerto": 9100})
    fila = dict(c.execute("SELECT * FROM fuentes WHERE id=?", (a.json()["id"],)).fetchone())
    assert json.loads(fila["opciones"]) == {"puerto": 9100} and fila["clave"] == ""
    assert crear("destinos", nombre="Otro", protocolo="srt", inicia="receptor", puerto=9100)[1] == "El puerto 9100 ya lo usa «Cámara»"
    assert crear("destinos", nombre="Otro", protocolo="srt", inicia="receptor", puerto=8890)[1] == "El puerto 8890 ya lo usa el SRT del hub"
    assert crear("destinos", nombre="Otro", protocolo="srt", inicia="receptor", puerto=80)[0] == 422


def test_reconexion_perfil_y_audio_propios_de_cada_destino(panel, c):
    did = destino_srt(panel, perfil="720p-3", audio="2", reconexion={"cada_s": 5, "max_intentos": 20}, latencia_ms=120)
    d = panel["operador"].get("/api/destinos").json()[0]
    assert (d["perfil"], d["audio"], d["reconexion"], d["latencia_ms"]) == ("720p-3", "2", {"cada_s": 5.0, "max_intentos": 20}, 120)
    assert panel["admin"].put(f"/api/destinos/{did}", json={"reconexion": {"cada_s": 5, "max_intentos": 20}}).json()["cambios"] == []
    assert panel["admin"].put(f"/api/destinos/{did}", json={"reconexion": {"cada_s": 0}}).json()["cambios"] == ["reconexion"]
    assert matriz.calcular(c)["politicas"] == {}        # sin ruta no hay nada que reintentar todavía


# ---------------------------------------------------------------- rutas
def test_el_plan_dice_que_se_copia_y_que_se_convierte_antes_de_activar(panel, c, mtx_falso):
    fid = fuente_rtmp(panel)
    srt = destino_srt(panel)
    web = panel["admin"].post("/api/destinos", json={"nombre": "Monitor", "protocolo": "webrtc", "inicia": "receptor"}).json()["id"]
    r = panel["operador"].post("/api/rutas/plan", json={"fuente": fid, "destino": web}).json()
    assert r["plan"]["provisional"] and r["permiso"] == "conectar_libre" and r["puedes"] and r["actual"] is None
    con_senal(c, mtx_falso, fid)
    copia = panel["operador"].post("/api/rutas/plan", json={"fuente": fid, "destino": srt}).json()["plan"]
    assert copia["resumen"] == "Se copia todo: solo cambia el transporte." and copia["carga"] == "Sin conversión."
    r = panel["operador"].post("/api/rutas/plan", json={"fuente": fid, "destino": web}).json()
    assert r["plan"]["resumen"] == "Se copia el vídeo y se convierte el audio."
    assert r["plan"]["video"]["accion"] == "copia" and r["plan"]["audio"][0]["accion"] == "convierte" and r["plan"]["audio"][0]["motivo"]
    assert r["validacion"]["nivel"] == "pendiente" and c.execute("SELECT count(*) n FROM rutas").fetchone()["n"] == 0   # no cambia nada
    # un segundo destino con el mismo perfil comparte la conversión
    assert panel["operador"].put(f"/api/rutas/{web}", json={"fuente": fid}).status_code == 200
    otro = panel["admin"].post("/api/destinos", json={"nombre": "Monitor 2", "protocolo": "webrtc", "inicia": "receptor"}).json()["id"]
    p = panel["operador"].post("/api/rutas/plan", json={"fuente": fid, "destino": otro}).json()["plan"]
    assert p["comparte"] == ["Monitor"] and p["carga"] == "Usa la conversión que ya alimenta a «Monitor»: no añade carga."
    # lo que el hub no puede llevar no se activa
    inspeccionar(c, fid, {"video": {"codec": "vp8", "ancho": 1280, "alto": 720}, "audio": []})
    r = panel["admin"].put(f"/api/rutas/{srt}", json={"fuente": fid})
    assert r.status_code == 422 and "VP8" in r.json()["detail"] and c.execute("SELECT count(*) n FROM rutas").fetchone()["n"] == 1


def test_un_destino_al_aire_no_cambia_de_fuente_sin_saber_cual_se_quita_ni_sin_permiso(panel, c, mtx_falso):
    a, b = fuente_rtmp(panel, "Estudio A"), fuente_rtmp(panel, "Estudio B")
    did = destino_srt(panel, "YouTube")
    con_senal(c, mtx_falso, a)
    r = panel["operador"].put(f"/api/rutas/{did}", json={"fuente": a})          # destino libre: el operador puede
    assert r.status_code == 200 and r.json()["plan"]["resumen"].startswith("Se copia todo")
    assert panel["operador"].put(f"/api/rutas/{did}", json={"fuente": a}).json() == {"ok": True, "sin_cambios": True}
    c.execute("INSERT INTO estado_salidas(salida, pid, desde, kbps, visto) VALUES (?,?,?,?,?)",
              ("ruta_" + did, 4242, time.time() - 60, 6200.0, time.time()))
    ruta = panel["operador"].get("/api/destinos").json()[0]["ruta"]
    assert ruta["estado"]["estado"] == "enviando" and ruta["al_aire"] and ruta["por"] == "operador@ejemplo.com"
    # el operador no puede tocar lo que está al aire, y se le dice con qué fuente está
    r = panel["operador"].put(f"/api/rutas/{did}", json={"fuente": b, "sustituye": a})
    assert r.status_code == 403 and "«YouTube» está al aire con «Estudio A»" in r.json()["detail"]
    assert panel["operador"].delete(f"/api/rutas/{did}").status_code == 403
    plan = panel["operador"].post("/api/rutas/plan", json={"fuente": b, "destino": did}).json()
    assert (plan["permiso"], plan["puedes"], plan["sustituye"], plan["actual"]["fuente_nombre"]) == ("modificar_al_aire", False, a, "Estudio A")
    # el manager sí, pero antes tiene que confirmar qué fuente sustituye
    r = panel["manager"].put(f"/api/rutas/{did}", json={"fuente": b})
    assert r.status_code == 409 and r.json()["detail"]["sustituye"] == a
    assert r.json()["detail"]["mensaje"] == "«YouTube» ya recibe «Estudio A» (enviando). Confirma el cambio para sustituirla."
    assert panel["manager"].put(f"/api/rutas/{did}", json={"fuente": b, "sustituye": "otra"}).status_code == 409
    assert c.execute("SELECT fuente FROM rutas").fetchone()["fuente"] == a
    assert panel["manager"].put(f"/api/rutas/{did}", json={"fuente": b, "sustituye": a}).status_code == 200
    assert c.execute("SELECT fuente, por FROM rutas").fetchone()["fuente"] == b
    assert ("Cambió la fuente al aire", "YouTube", "de «Estudio A» a «Estudio B»", "manager@ejemplo.com") in cambios(c)
    # ahora la fuente (B) no tiene señal: la ruta no está al aire y el operador puede desconectarla
    ruta = panel["operador"].get("/api/destinos").json()[0]["ruta"]
    assert ruta["estado"]["estado"] == "esperando_fuente" and not ruta["al_aire"]
    assert panel["operador"].delete(f"/api/rutas/{did}").status_code == 200
    assert cambios(c)[-1][:3] == ("Desactivó una ruta", "Estudio B → YouTube", "")


def test_tocar_una_fuente_o_un_destino_al_aire_pide_el_permiso_de_modificar_al_aire(panel, c, mtx_falso):
    panel["admin"].put("/api/ajustes/permisos", json={"operador": ["ver", "conectar_libre", "editar"],
                                                      "manager": ["ver", "conectar_libre", "modificar_al_aire", "editar"]})
    fid, did = fuente_rtmp(panel), destino_srt(panel, "YouTube")
    con_senal(c, mtx_falso, fid)
    panel["operador"].put(f"/api/rutas/{did}", json={"fuente": fid})
    c.execute("INSERT INTO estado_salidas(salida, pid, desde, kbps, visto) VALUES (?,?,?,?,?)",
              ("ruta_" + did, 4242, time.time(), 6200.0, time.time()))
    for metodo, url, datos in (("delete", f"/api/fuentes/{fid}", None), ("post", f"/api/fuentes/{fid}/desactivar", None),
                               ("put", f"/api/fuentes/{fid}", {"clave": "otra-clave-nueva"}), ("delete", f"/api/destinos/{did}", None),
                               ("put", f"/api/destinos/{did}", {"url": "srt://203.0.113.99:9000"})):
        r = getattr(panel["operador"], metodo)(url, **({"json": datos} if datos else {}))
        assert r.status_code == 403 and "está al aire hacia «YouTube»" in r.json()["detail"], url
    # lo que no corta la señal sí puede cambiarlo quien edita
    assert panel["operador"].put(f"/api/destinos/{did}", json={"nombre": "YouTube principal", "reconexion": {"cada_s": 10}}).status_code == 200
    assert panel["operador"].put(f"/api/fuentes/{fid}", json={"nombre": "Estudio A1"}).status_code == 200
    assert panel["manager"].post(f"/api/fuentes/{fid}/desactivar").status_code == 200
    ruta = panel["operador"].get("/api/destinos").json()[0]["ruta"]
    assert ruta["estado"]["detalle"] == "La fuente está desactivada" and not ruta["al_aire"]
    assert panel["operador"].delete(f"/api/fuentes/{fid}").status_code == 200       # ya no está al aire
    assert c.execute("SELECT count(*) n FROM rutas").fetchone()["n"] == 0
    assert cambios(c)[-1][:3] == ("Borró una fuente", "Estudio A1", "con sus rutas hacia YouTube principal")


def test_la_matriz_dice_el_estado_de_cada_ruta_con_su_evidencia(panel, c, mtx_falso):
    fid = fuente_rtmp(panel)
    srt = destino_srt(panel)
    hls = panel["admin"].post("/api/destinos", json={"nombre": "Web", "protocolo": "hls", "inicia": "receptor"}).json()["id"]
    web = panel["admin"].post("/api/destinos", json={"nombre": "Monitor", "protocolo": "webrtc", "inicia": "receptor"}).json()["id"]
    m = panel["operador"].get("/api/matriz").json()
    f = m["fuentes"][0]
    assert f["estado"]["estado"] == "esperando_fuente" and f["quien_inicia"] == "El codificador publica en el hub"
    assert f["sentido"] == "Hacia el hub" and f["pistas"] is None and not f["con_senal"]
    assert m["sistema"]["mediamtx"] and m["permisos"] == ["conectar_libre", "ver"]
    assert not m["sistema"]["supervisor"] and m["sistema"]["supervisor_visto_s"] is None      # sin latido, no se da por vivo
    db.guardar_ajuste(c, "latido", time.time() - 3)
    assert panel["operador"].get("/api/matriz").json()["sistema"]["supervisor"]
    db.guardar_ajuste(c, "latido", time.time() - 60)
    assert not panel["operador"].get("/api/matriz").json()["sistema"]["supervisor"]
    con_senal(c, mtx_falso, fid)
    for d in (srt, hls, web):
        assert panel["operador"].put(f"/api/rutas/{d}", json={"fuente": fid}).status_code == 200
    firma = matriz.calcular(c)["planes"][web]["senal"]["firma"]
    conv = f"conv_{fid}_{firma}"
    ahora = time.time()
    c.execute("INSERT INTO estado_salidas(salida, pid, desde, kbps, reinicios, visto) VALUES (?,?,?,?,?,?)",
              ("ruta_" + srt, 4242, ahora - 90, 6100.0, 2, ahora - 1))
    c.execute("INSERT INTO estado_salidas(salida, pid, desde, kbps, visto) VALUES (?,?,?,?,?)", (conv, 4243, ahora - 80, 6000.0, ahora - 1))
    mtx_falso["paths"].update({conv: lista(conv), hls: lista(hls), web: lista(web, readers=[{"type": "webRTCSession", "id": "w1"}],
                                                                              bytesSent=5000)})
    mtx_falso["conexiones"][("webRTCSession", "w1")] = {"protocolo": "WebRTC", "remoto": "100.64.0.7:50000", "estado": "read",
                                                         "path": web, "desde": "2026-11-19T06:10:00Z", "recibidos": 0, "enviados": 5000}
    m = panel["operador"].get("/api/matriz").json()
    f, d = m["fuentes"][0], {x["id"]: x for x in m["destinos"]}
    assert f["estado"]["estado"] == "recibiendo" and f["pistas"]["video"]["texto"] == "H.264 · 1920×1080 · 29,97 fps"
    assert f["estado"]["evidencia"][0]["que"] == "MediaMTX recibe la señal de 203.0.113.9 por RTMP" and f["estado"]["evidencia"][0]["desde"]
    assert f["pistas"]["audio"][0]["nombre_pista"] == "Pista 1" and sorted(f["rutas"]) == sorted([srt, hls, web])
    e = d[srt]["ruta"]["estado"]
    assert e["estado"] == "enviando" and e["actualizado"] == pytest.approx(ahora - 1, abs=0.01)       # cuándo lo vio el supervisor
    assert e["evidencia"][0]["que"] == "El proceso de envío manda 6 100 kb/s" and "Reconexiones desde que se activó: 2" in json.dumps(e, ensure_ascii=False)
    assert d[srt]["ruta"]["metricas"]["kbps"] == 6100.0 and d[srt]["ruta"]["metricas"]["reconexiones"] == 2
    assert d[hls]["ruta"]["estado"]["estado"] == "esperando_receptor" and d[hls]["ruta"]["al_aire"]
    e = d[web]["ruta"]
    assert e["estado"]["estado"] == "enviando" and e["senal"] == conv and e["vista"] == {"hls": f"hls/{conv}/"}
    assert e["metricas"]["receptores"][0]["remoto"] == "100.64.0.7" and e["metricas"]["conversion"]["id"] == conv
    assert "Receptores conectados: 1 (100.64.0.7)" in json.dumps(e, ensure_ascii=False)
    # un reproductor HLS cuenta mientras sigue pidiendo; MediaMTX mantiene su muxer un minuto más
    mtx_falso["paths"][hls]["readers"] = [{"type": "hlsMuxer", "id": ""}]
    mtx_falso["hls"][hls] = {"creado": "", "ultima_peticion": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "enviados": 0}
    estado_hls = lambda: {x["id"]: x for x in panel["operador"].get("/api/destinos").json()}[hls]["ruta"]["estado"]
    assert estado_hls()["estado"] == "enviando" and estado_hls()["evidencia"][0]["que"] == "Receptores conectados: 1 (un reproductor HLS)"
    mtx_falso["hls"][hls]["ultima_peticion"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 45))
    assert estado_hls()["estado"] == "esperando_receptor"
    # sin MediaMTX no se inventa nada
    mtx_falso["paths"] = None
    m = panel["operador"].get("/api/matriz").json()
    assert not m["sistema"]["mediamtx"] and m["fuentes"][0]["estado"]["detalle"] == "MediaMTX no responde"


def test_no_se_ofrece_una_direccion_antes_de_que_su_servidor_este_encendido(panel, c, mtx_falso, monkeypatch):
    """Lo encontró la prueba de aceptación: el hub decía «esperando receptor» con la dirección WebRTC
    cuando MediaMTX aún no había encendido su servidor WebRTC, y el navegador no podía abrirla."""
    fid = fuente_rtmp(panel)
    did = panel["admin"].post("/api/destinos", json={"nombre": "Monitor", "protocolo": "webrtc", "inicia": "receptor"}).json()["id"]
    con_senal(c, mtx_falso, fid)
    inspeccionar(c, fid, {"video": H264_AAC["video"], "audio": [{"codec": "opus", "canales": 2, "hz": 48000}]})
    assert panel["admin"].put(f"/api/rutas/{did}", json={"fuente": fid}).status_code == 200
    mtx_falso["paths"][did] = lista(did, source={"type": "srtSource", "id": ""})

    def ruta():
        return next(d for d in panel["admin"].get("/api/destinos").json() if d["id"] == did)["ruta"]["estado"]
    monkeypatch.setattr(api_hub, "_servidores_mtx", lambda: {"rtmp": True, "webrtc": False})
    assert ruta()["estado"] == "reconectando" and "encendiendo su servidor WebRTC" in ruta()["detalle"]
    monkeypatch.setattr(api_hub, "_servidores_mtx", lambda: {"rtmp": True, "webrtc": True})
    assert ruta()["estado"] == "esperando_receptor"
    # el puerto del servidor es de otro programa: el supervisor lo deja anotado y la ruta lo dice como error
    db.guardar_ajuste(c, "servidores", {"problemas": {"webrtc": "El puerto 8889/TCP está ocupado por otro programa"}, "t": time.time()})
    assert ruta()["estado"] == "error" and "8889/TCP" in ruta()["evidencia"][0]["que"]
    # y una fuente RTMP a la que aún no se puede publicar no dice que solo falta que alguien publique
    monkeypatch.setattr(api_hub, "_servidores_mtx", lambda: {"rtmp": False, "webrtc": True})
    mtx_falso["paths"].clear()
    f = next(f for f in panel["admin"].get("/api/fuentes").json() if f["id"] == fid)
    assert f["estado"]["estado"] == "esperando_fuente" and "encendiendo su servidor RTMP" in f["estado"]["detalle"]


def test_de_un_destino_hls_se_dice_quien_lo_esta_viendo(panel, c, mtx_falso):
    fid = fuente_rtmp(panel)
    did = panel["admin"].post("/api/destinos", json={"nombre": "Web", "protocolo": "hls", "inicia": "receptor"}).json()["id"]
    con_senal(c, mtx_falso, fid)
    assert panel["admin"].put(f"/api/rutas/{did}", json={"fuente": fid}).status_code == 200
    mtx_falso["paths"][did] = lista(did, source={"type": "srtSource", "id": ""}, readers=[{"type": "hlsSession", "id": "s1"}])
    mtx_falso["conexiones"][("hlsSession", "s1")] = {"protocolo": "HLS", "remoto": "203.0.113.50:40000", "estado": "", "path": did,
                                                      "desde": "2026-11-19T06:01:00Z", "recibidos": 0, "enviados": 5000}
    ruta = next(d for d in panel["admin"].get("/api/destinos").json() if d["id"] == did)["ruta"]
    assert ruta["estado"]["estado"] == "enviando"
    assert ruta["estado"]["evidencia"][0]["que"] == "Receptores conectados: 1 (203.0.113.50)"
    assert ruta["metricas"]["receptores"][0]["protocolo"] == "HLS"


def test_reintentar_deja_la_orden_para_el_supervisor(panel, c, mtx_falso):
    fid, did = fuente_rtmp(panel), destino_srt(panel)
    assert panel["operador"].post(f"/api/rutas/{did}/reintentar").status_code == 404
    con_senal(c, mtx_falso, fid)
    panel["operador"].put(f"/api/rutas/{did}", json={"fuente": fid})
    assert panel["operador"].post(f"/api/rutas/{did}/reintentar").status_code == 200
    assert [(r["orden"], r["tarea"]) for r in db.filas(c, "SELECT * FROM ordenes")] == [("reintentar", "ruta_" + did)]
    assert cambios(c)[-1][:2] == ("Reintentó una ruta", "Estudio A → Receptor SRT")


def test_un_flujo_de_la_version_anterior_se_enruta_como_una_fuente_mas(panel, c, mtx_falso):
    c.execute("INSERT INTO flujos(id, nombre, entrada, canal) VALUES ('noticias', 'Noticias', 'recorder', 'canal1')")
    c.execute("INSERT INTO flujos(id, nombre, entrada, url) VALUES ('directo', 'Feed 1080i', 'hub', 'srt://h:1')")
    c.execute("UPDATE flujos SET directo=1 WHERE id='directo'")
    fs = panel["operador"].get("/api/fuentes").json()
    assert [(f["id"], f["heredada"]["entrada"], f["quien_inicia"]) for f in fs] == \
        [("rec_canal1", "recorder", "La entrega el Recorder de este equipo")]        # el 1080i directo no pasa por MediaMTX
    assert panel["admin"].put("/api/fuentes/rec_canal1", json={"nombre": "Otro"}).status_code == 409
    assert panel["admin"].delete("/api/fuentes/rec_canal1").status_code == 409
    did = destino_srt(panel)
    mtx_falso["paths"]["rec_canal1"] = lista("rec_canal1", source={"type": "udpSource", "id": ""})
    inspeccionar(c, "rec_canal1")
    assert panel["operador"].put(f"/api/rutas/{did}", json={"fuente": "rec_canal1"}).status_code == 200
    ruta = panel["operador"].get("/api/destinos").json()[0]["ruta"]
    assert ruta["fuente_nombre"] == "Noticias" and ruta["validacion"]["nivel"] == "pendiente"
    assert panel["operador"].post("/api/fuentes/rec_canal1/probar").json()["ok"] is True
    # borrar el flujo (lo de siempre, solo un administrador) se lleva sus rutas del hub y queda anotado
    assert panel["admin"].delete("/api/flujos/noticias").status_code == 200
    assert c.execute("SELECT count(*) n FROM rutas").fetchone()["n"] == 0
    assert cambios(c)[-1][:3] == ("Borró un flujo", "Noticias", "con 1 ruta del hub que salía de él")


# ---------------------------------------------------------------- probar, medir, ajustes
def test_probar_no_emite_y_dice_que_comprobo(panel, c, mtx_falso, monkeypatch):
    llamadas = []

    def srt(host, puerto, espera=4.0):
        llamadas.append((host, puerto))
        return sondas.resultado([sondas.paso("Receptor SRT", True, "Hay un equipo SRT escuchando", ms=12)])
    monkeypatch.setattr(sondas, "srt_escucha", srt)
    did = destino_srt(panel, passphrase="frase-de-paso-ajena")
    r = panel["operador"].post(f"/api/destinos/{did}/probar").json()
    assert r["ok"] is True and llamadas == [("203.0.113.20", 9000)] and "frase-de-paso-ajena" not in json.dumps(r)
    assert r["pasos"][1]["al_conectar"] and r["pasos"][1]["ok"] is None            # la passphrase solo se sabe al conectar
    # con la ruta enviando no se sondea (un receptor SRT conectado no contesta): la prueba es que la señal sale
    fid = fuente_rtmp(panel)
    con_senal(c, mtx_falso, fid)
    panel["admin"].put(f"/api/rutas/{did}", json={"fuente": fid})
    c.execute("INSERT INTO estado_salidas(salida, pid, desde, kbps, visto) VALUES (?,?,?,?,?)",
              ("ruta_" + did, 4242, time.time(), 6200.0, time.time()))
    r = panel["operador"].post(f"/api/destinos/{did}/probar").json()
    assert r["ok"] is True and r["resumen"].startswith("La ruta está enviando ahora mismo") and len(llamadas) == 1
    assert panel["operador"].post(f"/api/fuentes/{fid}/probar").json()["resumen"].startswith("Ya llega señal de esta fuente")
    # antes de guardar: lo escrito en el formulario, con o sin nombre
    assert panel["operador"].post("/api/probar", json={"lado": "destino", "protocolo": "srt", "inicia": "hub"}).status_code == 403
    r = panel["manager"].post("/api/probar", json={"lado": "destino", "protocolo": "srt", "inicia": "hub", "url": "srt://198.51.100.7:9001"})
    assert r.json()["ok"] is True and llamadas[-1] == ("198.51.100.7", 9001)
    assert panel["manager"].post("/api/probar", json={"lado": "destino", "protocolo": "srt", "inicia": "hub", "url": "mal"}).status_code == 422
    # una fuente HLS: la lista, sus variantes y sus pistas de audio, para elegir
    lista_hls = {"maestra": True, "variantes": [{"indice": 0, "alto": 720, "audios": [{"nombre": "Español", "idioma": "spa"},
                                                                                     {"nombre": "Inglés", "idioma": "eng"}]}]}
    monkeypatch.setattr(api_hub.inspeccion, "leer_lista_hls", lambda url: (lista_hls, ""))
    r = panel["manager"].post("/api/probar", json={"lado": "fuente", "protocolo": "hls", "inicia": "hub",
                                                    "url": "https://cdn.example/x/master.m3u8"}).json()
    assert r["ok"] is True and r["hls"] == lista_hls and r["pasos"][1]["detalle"] == "2 declaradas: Español, Inglés"
    # lo que el hub entrega: ¿está escuchando donde dice? Aquí no hay MediaMTX, y lo dice
    monkeypatch.setattr(sondas, "hub_escucha", lambda puerto, tipo="tcp", que="": sondas.paso(
        f"Puerto {puerto}/{tipo.upper()} del hub", False, f"Nada escucha en el puerto {puerto}: {que} no está en marcha"))
    mtx_falso["paths"].clear()
    r = panel["operador"].post(f"/api/fuentes/{fid}/probar").json()
    assert r["ok"] is False and r["pasos"][0]["que"] == "Puerto 1935/TCP del hub"
    assert r["pasos"][-1]["que"] == "Dirección pública" and r["pasos"][-1]["ok"] is None


def test_niveles_solo_con_senal_y_audio_que_el_hub_pueda_medir(panel, c, mtx_falso, monkeypatch):
    pedidos = []
    monkeypatch.setattr(api_hub.medidores, "niveles", lambda path, pistas=1: pedidos.append((path, pistas)) or {
        "activo": True, "arrancando": False, "error": "", "pistas": [{"pista": 1, "canales": [{"pico": -12.0, "rms": -20.0, "canal": 1}], "t": 1}]})
    fid = fuente_rtmp(panel)
    assert panel["operador"].get("/api/fuentes/no-existe/niveles").status_code == 404
    assert panel["operador"].get(f"/api/fuentes/{fid}/niveles").json()["error"] == "La fuente no tiene señal"
    mtx_falso["paths"][fid] = lista(fid)
    assert panel["operador"].get(f"/api/fuentes/{fid}/niveles").json()["error"] == "Se está inspeccionando la señal"
    inspeccionar(c, fid, {"video": None, "audio": [{"codec": "g711", "canales": 1}, {"codec": "aac", "canales": 2, "nombre": "Inglés"}]})
    r = panel["operador"].get(f"/api/fuentes/{fid}/niveles").json()
    assert pedidos == [(fid, 1)]                        # solo la pista que cruza el enlace interno
    assert r["pistas"][0]["pista"] == 2 and r["pistas"][0]["nombre"] == "Pista 2 · Inglés" and r["pistas"][0]["canales"][0]["pico"] == -12.0


def test_ajustes_de_red_y_de_webrtc(panel, c):
    a = panel["operador"].get("/api/ajustes").json()
    assert [p["puerto"] for p in a["puertos"]] == [8890, 8888, 1935, 8889, 8189] and not a["red"]["https"]
    assert a["puertos"][2]["estado"]["ok"] is None and "Apagado" in a["puertos"][2]["estado"]["detalle"]     # nadie usa RTMP todavía
    assert panel["manager"].put("/api/ajustes/red", json={"rtmp_puerto": 1945}).status_code == 403
    ocupado = socket.socket()
    ocupado.bind(("0.0.0.0", 0))
    ocupado.listen(1)
    try:
        r = panel["admin"].put("/api/ajustes/red", json={"rtmp_puerto": ocupado.getsockname()[1]})
        assert r.status_code == 409 and "otro programa de este equipo" in r.json()["detail"]
    finally:
        ocupado.close()
    assert panel["admin"].put("/api/ajustes/red", json={"rtmp_puerto": 8890}).json()["detail"] == "El puerto 8890 ya lo usa el SRT del hub"
    libre = socket.socket()
    libre.bind(("0.0.0.0", 0))
    puerto = libre.getsockname()[1]
    libre.close()
    r = panel["admin"].put("/api/ajustes/red", json={"rtmp_puerto": puerto, "host_publico": "hub.ejemplo.com",
                                                      "webrtc_url": "https://hub.ejemplo.com/webrtc/"})
    assert r.status_code == 200 and r.json()["red"] == {"host_publico": "hub.ejemplo.com", "hls_url": "http://hub.ejemplo.com:8888",
                                                        "webrtc_url": "https://hub.ejemplo.com/webrtc", "hls_url_propia": False,
                                                        "webrtc_url_propia": True, "https": True, "rtmps": False}
    assert matriz.ajustes_red(c, {})["rtmp_puerto"] == puerto
    fid = fuente_rtmp(panel)
    assert panel["operador"].get("/api/fuentes").json()[0]["direccion"]["url"].startswith(f"rtmp://hub.ejemplo.com:{puerto}/{fid}")
    # servidores ICE: la credencial se guarda, no vuelve a salir y se conserva si no se reescribe
    ice = {"hosts": ["203.0.113.5"], "ice": [{"url": "turn:turn.ejemplo.com:3478", "usuario": "hub", "clave": "credencial-turn"},
                                             {"url": "stun:stun.ejemplo.com:3478"}]}
    r = panel["admin"].put("/api/ajustes/webrtc", json=ice)
    assert r.status_code == 200 and "credencial-turn" not in r.text
    assert r.json()["webrtc"]["ice"][0] == {"url": "turn:turn.ejemplo.com:3478", "usuario": "hub", "tiene_clave": True}
    del ice["ice"][0]["clave"]
    panel["admin"].put("/api/ajustes/webrtc", json=ice)
    assert db.ajuste(c, "webrtc")["ice"][0]["clave"] == "credencial-turn"
    assert panel["admin"].put("/api/ajustes/webrtc", json={"hosts": [], "ice": [{"url": "http://x"}]}).status_code == 422
    assert "credencial-turn" not in json.dumps(cambios(c)) and "credencial-turn" not in panel["operador"].get("/api/ajustes").text


def test_rtmps_en_ajustes_en_las_direcciones_y_en_la_prueba(panel, c, monkeypatch):
    escucha = socket.socket()
    escucha.bind(("127.0.0.1", 0))
    escucha.listen(1)
    monkeypatch.setitem(api_hub.CFG, "rtmps_puerto", escucha.getsockname()[1])
    monkeypatch.setitem(api_hub.CFG, "rtmps_cert", "/etc/hub/cert.pem")
    monkeypatch.setitem(api_hub.CFG, "rtmps_clave", "/etc/hub/clave.pem")
    try:
        a = panel["operador"].get("/api/ajustes").json()
        fila = a["puertos"][-1]
        assert a["red"]["rtmps"] and fila["servicio"] == "RTMPS" and fila["puerto"] == escucha.getsockname()[1]
        assert fila["clave"] is None and fila["estado"]["ok"] is None and "Apagado" in fila["estado"]["detalle"]   # nadie usa RTMP
        # su puerto cuenta como ocupado por el hub
        r = panel["admin"].put("/api/ajustes/red", json={"rtmp_puerto": escucha.getsockname()[1]})
        assert r.status_code == 409 and "el RTMPS del hub" in r.json()["detail"]
        fid = fuente_rtmp(panel)
        claves = panel["admin"].get(f"/api/fuentes/{fid}/claves").json()
        assert claves["url_rtmps"].startswith(f"rtmps://127.0.0.1:{escucha.getsockname()[1]}/{fid}?user={fid}&pass=")
        assert "Servidor RTMPS (cifrado)" in [x["nombre"] for x in claves["campos"]]
        assert panel["operador"].get("/api/fuentes").json()[0]["direccion"]["url_rtmps"].endswith("pass=***")
        assert panel["operador"].get("/api/ajustes").json()["puertos"][-1]["estado"]["ok"] is True      # alguien escucha en él
        # el supervisor no ha podido encenderlo: se dice por qué y dónde se corrige, y RTMP sigue
        db.guardar_ajuste(c, "servidores", {"problemas": {"rtmps": "No se encuentra el certificado (/etc/hub/cert.pem)"}})
        estado = panel["operador"].get("/api/ajustes").json()["puertos"][-1]["estado"]
        assert estado["ok"] is False and "No se encuentra el certificado" in estado["detalle"] and "config.json" in estado["detalle"]
        # en marcha, pero el certificado del disco ha dejado de valer: sigue, y se avisa
        db.guardar_ajuste(c, "servidores", {"problemas": {api_hub.mtx.RTMPS_DISCO: "El certificado y su clave no se pueden usar juntos"}})
        estado = panel["operador"].get("/api/ajustes").json()["puertos"][-1]["estado"]
        assert estado["ok"] is None and "sigue en marcha" in estado["detalle"] and "no se pueden usar juntos" in estado["detalle"]
        # si lo que no vale es un puerto nuevo, sigue en el que tenía: en este no escucha nadie, pero MediaMTX dice que RTMPS está
        escucha.close()
        monkeypatch.setattr(api_hub, "_servidores_mtx", lambda: {"rtmp": True, "webrtc": False, "rtmps": True})
        estado = panel["operador"].get("/api/ajustes").json()["puertos"][-1]["estado"]
        assert estado["ok"] is None and "sigue en marcha" in estado["detalle"]
        monkeypatch.setattr(api_hub, "_servidores_mtx", lambda: None)           # MediaMTX no responde: no se afirma nada
        estado = panel["operador"].get("/api/ajustes").json()["puertos"][-1]["estado"]
        assert estado["ok"] is False and "Nada escucha" in estado["detalle"]
    finally:
        escucha.close()


def test_perfiles_propios(panel, c, mtx_falso):
    assert [p["id"] for p in panel["operador"].get("/api/perfiles").json()] == ["original", "1080p-6", "720p-3"]
    assert panel["manager"].post("/api/perfiles", json={"nombre": "540p", "video": {"alto": 540, "kbps": 2000}}).status_code == 403
    assert panel["admin"].post("/api/perfiles", json={"nombre": "Nada"}).status_code == 422
    assert panel["admin"].post("/api/perfiles", json={"nombre": "Raro", "video": {"alto": 541, "kbps": 2000}}).status_code == 422
    pid = panel["admin"].post("/api/perfiles", json={"nombre": "540p · 2 Mb/s", "video": {"alto": 540, "kbps": 2000},
                                                      "audio": {"kbps": 96}}).json()["id"]
    did = destino_srt(panel, perfil=pid)
    assert panel["operador"].get("/api/perfiles").json()[-1]["en_uso"] == ["Receptor SRT"]
    assert panel["admin"].delete(f"/api/perfiles/{pid}").status_code == 409
    assert panel["admin"].put("/api/perfiles/original", json={"nombre": "X", "audio": {"kbps": 96}}).status_code == 404   # los fijos no se tocan
    fid = fuente_rtmp(panel)
    con_senal(c, mtx_falso, fid)
    p = panel["operador"].post("/api/rutas/plan", json={"fuente": fid, "destino": did}).json()
    # el perfil baja el vídeo a 540p; el audio AAC ya vale para SRT y se copia (su bitrate solo cuenta si hay que convertirlo)
    assert p["perfil"] == pid and p["plan"]["resumen"] == "Se convierte el vídeo y el audio se copia." and "960×540" in p["plan"]["carga"]
    panel["admin"].put(f"/api/destinos/{did}", json={"perfil": "original"})
    assert panel["admin"].delete(f"/api/perfiles/{pid}").status_code == 200


def test_las_combinaciones_verificadas_se_anotan_y_se_ven_en_cada_ruta(panel, c, mtx_falso):
    v = panel["operador"].get("/api/validacion").json()
    assert all(x["nivel"] == "pendiente" for x in v["combinaciones"]) and len(v["combinaciones"]) == 120
    nota = {"fuente": "rtmp:origen", "destino": "srt:hub", "nivel": "laboratorio", "nota": "Prueba de aceptación en el banco"}
    assert panel["manager"].put("/api/validacion", json=nota).status_code == 403
    assert panel["admin"].put("/api/validacion", json=nota).json() == {"ok": True, "anotado": True}
    assert panel["admin"].put("/api/validacion", json={**nota, "nivel": "real", "nota": ""}).status_code == 422    # ¿con qué equipo?
    assert panel["admin"].put("/api/validacion", json={**nota, "fuente": "hls:origen"}).status_code == 422
    fid, did = fuente_rtmp(panel), destino_srt(panel)
    con_senal(c, mtx_falso, fid)
    panel["admin"].put(f"/api/rutas/{did}", json={"fuente": fid})
    v = panel["operador"].get("/api/destinos").json()[0]["ruta"]["validacion"]
    assert v["nivel"] == "laboratorio" and v["nombre"] == "Verificada en el banco de pruebas" and v["quien"] == "admin@ejemplo.com"
    panel["admin"].put("/api/validacion", json={**nota, "nivel": "real", "nota": "Cámara Sony y receptor Haivision del estudio"})
    assert panel["admin"].put("/api/validacion", json=nota).json()["anotado"] is False     # una prueba de banco no rebaja lo real
    assert panel["operador"].get("/api/destinos").json()[0]["ruta"]["validacion"]["nivel"] == "real"


def test_la_vista_previa_por_el_panel_solo_alcanza_fuentes_y_senales_convertidas(panel, c, mtx_falso):
    fid = fuente_rtmp(panel)
    web = panel["admin"].post("/api/destinos", json={"nombre": "Monitor", "protocolo": "webrtc", "inicia": "receptor"}).json()["id"]
    con_senal(c, mtx_falso, fid)
    panel["admin"].put(f"/api/rutas/{web}", json={"fuente": fid})
    conv = matriz.path_senal(fid, matriz.calcular(c)["planes"][web]["senal"]["firma"])
    assert servidor._paths_con_vista("operador") == {fid, conv}     # el path del destino no: ahí solo leen receptores
    assert servidor._paths_con_vista() == frozenset()
    panel["admin"].put("/api/ajustes/permisos", json={"operador": [], "manager": []})
    assert servidor._paths_con_vista("operador") == frozenset()


def test_adaptadores_y_registro_de_cambios(panel, c):
    a = panel["operador"].get("/api/adaptadores").json()
    assert not a["mediamtx"]["en_marcha"] and set(a["adaptadores"]) == {"ndi", "zixi"}
    assert not a["adaptadores"]["ndi"]["listo"] and a["adaptadores"]["ndi"]["falta"] and a["adaptadores"]["ndi"]["validacion"] == "pendiente"
    r = panel["admin"].post("/api/fuentes", json={"nombre": "NDI estudio", "protocolo": "ndi", "inicia": "hub",
                                                   "opciones": {"nombre": "ESTUDIO (CAM 1)"}})
    assert r.status_code == 200
    f = panel["operador"].get("/api/fuentes").json()[0]
    assert f["estado"]["estado"] == "error" and f["estado"]["detalle"].startswith("Adaptador NDI: falta")
    assert f["adaptador"] == {"listo": False, "falta": f["estado"]["detalle"], "tiene_clave": False}
    assert panel["operador"].post(f"/api/fuentes/{f['id']}/probar").json()["ok"] is False
    assert panel["operador"].get("/api/adaptadores").json()["adaptadores"]["ndi"]["en_espera"] == {"fuentes": ["NDI estudio"], "destinos": []}
    assert panel["manager"].post("/api/adaptadores/comprobar").status_code == 403
    registro = panel["operador"].get("/api/cambios").json()
    assert [(x["accion"], x["objeto"], x["detalle"], x["rol"]) for x in registro] == [("Creó una fuente", "NDI estudio", "NDI", "admin")]


def test_en_reposo_el_hub_no_mueve_nada_y_lo_dice(panel, c, mtx_falso, monkeypatch):
    fid, did = fuente_rtmp(panel), destino_srt(panel)
    con_senal(c, mtx_falso, fid)
    panel["admin"].put(f"/api/rutas/{did}", json={"fuente": fid})
    monkeypatch.setattr(db, "reposo_recorder", lambda: {"desde": 1795000000, "por": "admin@ejemplo.com"})
    m = panel["operador"].get("/api/matriz").json()
    assert m["sistema"]["reposo"] == {"desde": 1795000000, "por": "admin@ejemplo.com"}
    assert m["fuentes"][0]["estado"]["nombre"] == "En reposo" and m["destinos"][0]["ruta"]["estado"]["nombre"] == "En reposo"
    assert not m["destinos"][0]["ruta"]["al_aire"] and estados.DESACTIVADA == m["destinos"][0]["ruta"]["estado"]["estado"]


def test_varios_paneles_abiertos_no_multiplican_el_calculo(panel, c, mtx_falso, monkeypatch):
    veces = []
    completo = api_hub._estado_completo
    monkeypatch.setattr(api_hub, "_estado_completo", lambda c: veces.append(1) or completo(c))
    monkeypatch.setattr(api_hub, "_estado_del_hub", mtx_falso["con_cache"])
    for _ in range(5):
        assert panel["operador"].get("/api/matriz").status_code == 200
    assert len(veces) == 1                              # como mucho una vez por segundo
    fuente_rtmp(panel)                                  # pero un cambio se ve en la siguiente consulta
    assert len(panel["manager"].get("/api/matriz").json()["fuentes"]) == 1 and len(veces) == 2
    assert panel["manager"].get("/api/matriz").json()["permisos"] != panel["operador"].get("/api/matriz").json()["permisos"]
