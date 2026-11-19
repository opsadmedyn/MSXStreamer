"""Piezas del supervisor que se pueden probar sin lanzar nada."""

import json
import types

import db
import inspeccion
import matriz
import supervisor


def _info(ancho, alto):
    return {"ready": True, "readyTime": "2026-11-19T09:00:00Z", "tracks": ["H264", "Opus"],
            "tracks2": [{"codec": "H264", "codecProps": {"width": ancho, "height": alto}},
                        {"codec": "Opus", "codecProps": {"channelCount": 1}}]}


def test_un_tamano_de_imagen_nuevo_solo_se_vuelve_a_mirar_cuando_lleva_un_rato_estable():
    """Un navegador que publica por WebRTC sube de 320×180 a 1280×720 en escalones de pocos segundos.
    Rehacer la inspección (y con ella las conversiones) a cada escalón cortaría las salidas varias
    veces: se espera a que el tamaño deje de moverse."""
    pistas = {"video": {"codec": "h264", "ancho": 320, "alto": 180, "fps": 20.0, "b": False},
              "audio": [{"codec": "opus", "canales": 1}], "segun_mediamtx": [320, 180]}
    s = types.SimpleNamespace(medidas={}, miradas={"f1": 1000.0})      # se inspeccionó en el segundo 1000

    def mirar(ancho, alto, t):
        return supervisor.Supervisor.otras_medidas(s, "f1", pistas, _info(ancho, alto), t)

    assert not mirar(320, 180, 1002)                    # sigue como se inspeccionó
    assert not mirar(480, 270, 1004)                    # primer escalón: se apunta y se espera
    assert not mirar(640, 360, 1008)                    # otro escalón: la cuenta vuelve a empezar
    assert not mirar(1280, 720, 1012)
    assert not mirar(1280, 720, 1020)                   # 8 s sin moverse: aún no
    assert mirar(1280, 720, 1012 + supervisor.MEDIDAS_ESTABLES)
    s.miradas["f1"] = 1022.0                            # lo que hace inspeccionar() al lanzar la mirada
    assert not mirar(1280, 720, 1025)                   # si esa mirada falla, no se repite en cada vuelta
    assert mirar(1280, 720, 1022 + supervisor.MEDIDAS_ESTABLES)
    assert not mirar(320, 180, 1040) and "f1" not in s.medidas          # volvió al tamaño inspeccionado: nada que mirar


class _Hilos:
    """En vez de los hilos que miran con ffprobe: apunta qué se pidió mirar y deja la tarea sin acabar."""

    def __init__(self):
        self.pedidas = []

    def submit(self, funcion, fuente, info):
        self.pedidas.append(fuente["id"])
        return types.SimpleNamespace(done=lambda: False)


def _supervisor_con(fuente):
    c = db.conectar()
    c.execute("DELETE FROM inspecciones")
    s = types.SimpleNamespace(c=c, hub={"fuentes": [fuente]}, mirando={}, miradas={}, medidas={}, inspectores=_Hilos())
    s.otras_medidas = lambda *a: supervisor.Supervisor.otras_medidas(s, *a)
    return s


def _guardada(s, fid):
    return matriz.json_de(s.c.execute("SELECT pistas FROM inspecciones WHERE fuente=?", (fid,)).fetchone()["pistas"], None)


def test_un_codificador_que_vuelve_con_la_misma_senal_no_para_las_conversiones():
    """Tras un corte, el origen vuelve a conectar (otra huella). Si para MediaMTX trae lo mismo que ya se
    inspeccionó del todo, lo que se sabía se conserva mientras ffprobe lo confirma: una inspección
    parcial dejaría las conversiones paradas unos segundos más."""
    f = {"id": "f1", "protocolo": "srt", "inicia": "origen"}
    s = _supervisor_con(f)
    antes = _info(1280, 720)
    completa = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "fps": 30.0, "entrelazado": False, "b": False},
                "audio": [{"codec": "opus", "canales": 1}], "segun_mediamtx": [1280, 720],
                "resumen_mediamtx": inspeccion.resumen_mediamtx(antes)}
    s.c.execute("INSERT INTO inspecciones(fuente, t, huella, pistas, error) VALUES ('f1', 0, ?, ?, '')",
                (inspeccion.huella(antes), json.dumps(completa)))
    supervisor.Supervisor.inspeccionar(s, {"f1": antes})
    assert s.inspectores.pedidas == [] and "sin_confirmar" not in _guardada(s, "f1")     # sigue igual: nada que mirar

    vuelve = dict(antes, readyTime="2026-11-19T09:05:00Z")              # el codificador se cayó y ha vuelto
    supervisor.Supervisor.inspeccionar(s, {"f1": vuelve})
    guardada = _guardada(s, "f1")
    assert guardada["sin_confirmar"] and not guardada.get("parcial") and guardada["video"]["fps"] == 30.0
    assert s.inspectores.pedidas == ["f1"]                              # y se vuelve a mirar, para confirmarlo
    # la mirada acaba: lo que diga ffprobe sustituye a lo conservado
    confirmada = dict(completa, video=dict(completa["video"], fps=25.0))
    s.mirando["f1"] = (inspeccion.huella(vuelve), types.SimpleNamespace(done=lambda: True, result=lambda: (confirmada, "")))
    supervisor.Supervisor.inspeccionar(s, {"f1": vuelve})
    assert "sin_confirmar" not in _guardada(s, "f1") and _guardada(s, "f1")["video"]["fps"] == 25.0
    assert s.inspectores.pedidas == ["f1"]                              # confirmada: no se mira más


def test_si_la_confirmacion_falla_se_repite_cada_tanto_y_si_vuelve_distinta_se_empieza_de_cero():
    f = {"id": "f1", "protocolo": "srt", "inicia": "origen"}
    s = _supervisor_con(f)
    antes = _info(1280, 720)
    completa = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "fps": 30.0, "entrelazado": False, "b": False},
                "audio": [{"codec": "opus", "canales": 1}], "resumen_mediamtx": inspeccion.resumen_mediamtx(antes)}
    s.c.execute("INSERT INTO inspecciones(fuente, t, huella, pistas, error) VALUES ('f1', 0, ?, ?, '')",
                (inspeccion.huella(antes), json.dumps(completa)))
    vuelve = dict(antes, readyTime="2026-11-19T09:05:00Z")
    supervisor.Supervisor.inspeccionar(s, {"f1": vuelve})
    s.mirando["f1"] = (inspeccion.huella(vuelve), types.SimpleNamespace(done=lambda: True, result=lambda: (None, "ffprobe no ve nada")))
    supervisor.Supervisor.inspeccionar(s, {"f1": vuelve})
    assert _guardada(s, "f1")["sin_confirmar"] and s.inspectores.pedidas == ["f1"]       # falló: sigue lo conservado, sin insistir ya
    s.miradas["f1"] -= supervisor.REINSPECCION + 1
    supervisor.Supervisor.inspeccionar(s, {"f1": vuelve})
    assert s.inspectores.pedidas == ["f1", "f1"]                        # pasado el rato, se intenta otra vez
    # otra conexión, y ahora en 1080p: lo de antes ya no vale, se parte de lo que diga MediaMTX
    s.mirando.clear()
    otra = dict(_info(1920, 1080), readyTime="2026-11-19T09:10:00Z")
    supervisor.Supervisor.inspeccionar(s, {"f1": otra})
    guardada = _guardada(s, "f1")
    assert guardada["parcial"] and guardada["video"]["alto"] == 1080 and "sin_confirmar" not in guardada
