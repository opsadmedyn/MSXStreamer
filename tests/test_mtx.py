"""Lo que el supervisor le pide a MediaMTX: los servidores que el hub enciende cuando hacen falta."""

import socket

import mtx


def test_un_puerto_que_se_acaba_de_cerrar_esta_libre_y_uno_con_alguien_escuchando_no():
    oyente = socket.socket()
    oyente.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    oyente.bind(("127.0.0.1", 0))
    oyente.listen(1)
    puerto = oyente.getsockname()[1]
    assert not mtx.puerto_libre(puerto)                 # otro programa escucha ahí: ocupado
    cliente = socket.create_connection(("127.0.0.1", puerto))
    servido, _ = oyente.accept()
    servido.close()                                     # quien cierra primero deja el puerto «en espera» un minuto
    assert cliente.recv(1) == b""
    cliente.close()
    oyente.close()
    # nadie escucha ya, aunque el sistema recuerde la conexión que hubo (TIME_WAIT): MediaMTX puede
    # abrirlo, así que el hub no debe dejar apagado su servidor diciendo que el puerto está ocupado
    assert mtx.puerto_libre(puerto)
    assert mtx.puerto_libre(puerto, "udp")


def test_servidores_que_pide_el_hub():
    apagado = {"rtmp": False, "webrtc": False}
    pide = {"rtmp": {"puerto": 1945}, "webrtc": None}
    conf, problemas = mtx.conf_servidores(pide, apagado, libre=lambda puerto, tipo: True)
    assert conf["rtmp"] is True and conf["rtmpAddress"] == ":1945" and conf["webrtc"] is False and not problemas
    # el puerto es de otro programa: no se le pide a MediaMTX (se cerraría entero) y se dice por qué
    conf, problemas = mtx.conf_servidores(pide, apagado, libre=lambda puerto, tipo: False)
    assert "rtmp" not in conf and problemas == {"rtmp": "El puerto 1945/TCP está ocupado por otro programa"}
    # si ya lo tiene MediaMTX abierto en ese puerto, no hay nada que comprobar: es suyo
    conf, problemas = mtx.conf_servidores(pide, {"rtmp": True, "rtmpAddress": ":1945"}, libre=lambda puerto, tipo: False)
    assert conf["rtmp"] is True and not problemas


def test_de_cada_conexion_solo_sale_lo_que_sirve_al_panel(monkeypatch):
    """MediaMTX devuelve también «query», que en RTMP lleva el usuario y la clave de quien publica."""
    listas = {"/v3/rtmpconns/list": [{"id": "c1", "remoteAddr": "203.0.113.9:51000", "state": "publish", "path": "estudio",
                                      "query": "user=estudio&pass=clave-secreta", "created": "2026-11-19T06:00:00Z",
                                      "bytesReceived": 1000, "bytesSent": 0}],
              "/v3/hlssessions/list": [{"id": "s1", "remoteAddr": "203.0.113.50:40000", "path": "web", "query": "token=otra-clave",
                                        "userAgent": "Mozilla/5.0", "created": "2026-11-19T06:01:00Z", "outboundBytes": 5000}]}
    monkeypatch.setattr(mtx, "_todos", lambda ruta: listas.get(ruta, []))
    c = mtx.conexiones()
    assert c[("rtmpConn", "c1")] == {"protocolo": "RTMP", "remoto": "203.0.113.9:51000", "estado": "publish", "path": "estudio",
                                     "desde": "2026-11-19T06:00:00Z", "recibidos": 1000, "enviados": 0}
    assert c[("hlsSession", "s1")]["protocolo"] == "HLS" and c[("hlsSession", "s1")]["enviados"] == 5000
    assert "clave" not in str(c)
