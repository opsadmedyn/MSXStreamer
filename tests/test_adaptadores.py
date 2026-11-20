"""Los adaptadores dicen qué falta y nunca se dan por listos sin su componente."""

import adaptadores
import db
import matriz


def test_sin_nada_instalado_dicen_que_falta(monkeypatch):
    monkeypatch.setattr(adaptadores, "libndi", lambda cfg: "")
    monkeypatch.setattr(adaptadores.shutil, "which", lambda x: None)
    monkeypatch.setattr(adaptadores, "avahi_en_marcha", lambda: False)
    e = adaptadores.estado(forzar=True)
    assert set(e) == {"ndi", "zixi"}
    ndi, zixi = e["ndi"], e["zixi"]
    assert not ndi["listo"] and "SDK de NDI para Linux" in ndi["falta"] and "ndi.video" in ndi["falta"]
    assert [r["ok"] for r in ndi["requisitos"]] == [False] * 5
    assert not zixi["listo"] and "Zixi Broadcaster" in zixi["falta"] and zixi["validacion"] == "pendiente"
    assert {v["nombre"] for v in ndi["variantes"]} == {"NDI de ancho de banda completo", "NDI|HX"}


def test_con_los_componentes_sigue_pendiente_el_puente(monkeypatch):
    monkeypatch.setattr(db, "config", lambda: {"adaptadores": {"ndi": {"activo": True}, "zixi": {"activo": True, "api": "http://127.0.0.1:4444"}}})
    monkeypatch.setattr(adaptadores, "libndi", lambda cfg: "/usr/local/lib/libndi.so.6")
    monkeypatch.setattr(adaptadores.shutil, "which", lambda x: "/usr/bin/" + x)
    monkeypatch.setattr(adaptadores, "gst_tiene", lambda inspect, elemento: True)
    monkeypatch.setattr(adaptadores, "avahi_en_marcha", lambda: True)
    monkeypatch.setattr(adaptadores, "responde", lambda url, espera=1.5: True)
    e = adaptadores.estado(forzar=True)
    assert [r["ok"] for r in e["ndi"]["requisitos"]] == [True, True, True, True, False]
    assert not e["ndi"]["listo"] and "Puente NDI" in e["ndi"]["falta"]
    assert e["zixi"]["requisitos"][0]["ok"] and not e["zixi"]["listo"] and "Licencia de Zixi" in e["zixi"]["falta"]
    assert "entrada" not in e["ndi"] and "salida" not in e["zixi"]      # sin puente no hay nada que lanzar


def test_la_deteccion_se_guarda_y_un_fallo_no_rompe(monkeypatch):
    llamadas = []
    monkeypatch.setitem(adaptadores.DETECTORES, "ndi", lambda: llamadas.append(1) or adaptadores._estado("ndi", "NDI", [], False, ""))
    adaptadores.estado(forzar=True); adaptadores.estado(); adaptadores.estado()
    assert len(llamadas) == 1
    monkeypatch.setitem(adaptadores.DETECTORES, "zixi", lambda: 1 / 0)
    e = adaptadores.estado(forzar=True)
    assert not e["zixi"]["listo"] and "ZeroDivisionError" in e["zixi"]["falta"]


def test_una_fuente_ndi_espera_con_el_requisito_a_la_vista(monkeypatch):
    monkeypatch.setattr(adaptadores, "libndi", lambda cfg: "")
    monkeypatch.setattr(adaptadores.shutil, "which", lambda x: None)
    c = db.conectar()
    for tabla in ("rutas", "inspecciones", "fuentes", "destinos", "flujos"):
        c.execute(f"DELETE FROM {tabla}")
    f = matriz.crear(c, "fuentes", nombre="Estudio NDI", protocolo="ndi", inicia="hub", opciones={"nombre": "ESTUDIO (CAM 1)"})
    d = matriz.crear(c, "destinos", nombre="Socio", protocolo="srt", inicia="hub", url="srt://198.51.100.7:9000")
    matriz.conectar(c, d, f)
    r = matriz.calcular(c, {}, adaptadores=adaptadores.estado(forzar=True))
    cmd, _, impedimento = r["tareas"][matriz.id_entrada(f)]
    assert cmd is None and "SDK de NDI para Linux" in impedimento
    assert f in r["paths"]                           # el path existe: el resto de la ruta está preparado
