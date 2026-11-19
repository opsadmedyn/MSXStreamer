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


def test_rtmps_se_enciende_con_rtmp_si_hay_un_certificado_que_valga():
    apagado = {"rtmp": False, "webrtc": False}
    tls = {"puerto": 1946, "cert": "/etc/hub/cert.pem", "clave": "/etc/hub/clave.pem"}
    pide = {"rtmp": {"puerto": 1945, "tls": tls}, "webrtc": None}
    libre, vale = (lambda puerto, tipo: True), (lambda cert, clave: "")
    conf, problemas = mtx.conf_servidores(pide, apagado, libre=libre, certificado=vale)
    assert not problemas and conf["rtmp"] is True and conf["rtmpEncryption"] == "optional"       # con y sin cifrar, cada uno en su puerto
    assert (conf["rtmpsAddress"], conf["rtmpServerCert"], conf["rtmpServerKey"]) == (":1946", "/etc/hub/cert.pem", "/etc/hub/clave.pem")
    # sin certificado configurado, RTMP va sin cifrar y no se toca nada de TLS
    conf, problemas = mtx.conf_servidores({"rtmp": {"puerto": 1945, "tls": None}, "webrtc": None}, apagado, libre=libre, certificado=vale)
    assert conf["rtmpEncryption"] == "no" and "rtmpsAddress" not in conf and not problemas
    # un certificado que no vale no se le pide a MediaMTX (se cerraría entero): RTMP sigue y se dice por qué
    conf, problemas = mtx.conf_servidores(pide, apagado, libre=libre, certificado=lambda cert, clave: "No se encuentra el certificado (/etc/hub/cert.pem)")
    assert conf["rtmp"] is True and conf["rtmpEncryption"] == "no" and "rtmpsAddress" not in conf
    assert problemas == {"rtmps": "No se encuentra el certificado (/etc/hub/cert.pem)"}
    # el puerto de RTMPS es de otro programa
    conf, problemas = mtx.conf_servidores(pide, apagado, libre=lambda puerto, tipo: puerto != 1946, certificado=vale)
    assert conf["rtmpEncryption"] == "no" and problemas == {"rtmps": "El puerto 1946/TCP está ocupado por otro programa"}
    # ya abierto por MediaMTX en ese puerto: es suyo
    abierto = {"rtmp": True, "rtmpAddress": ":1945", "rtmpEncryption": "optional", "rtmpsAddress": ":1946",
               "rtmpServerCert": "/etc/hub/cert.pem", "rtmpServerKey": "/etc/hub/clave.pem"}
    conf, problemas = mtx.conf_servidores(pide, abierto, libre=lambda puerto, tipo: False, certificado=vale)
    assert conf["rtmpEncryption"] == "optional" and not problemas


def test_un_rtmps_en_marcha_no_se_toca_aunque_el_certificado_del_disco_deje_de_valer():
    tls = {"puerto": 1946, "cert": "/etc/hub/cert.pem", "clave": "/etc/hub/clave.pem"}
    pide = {"rtmp": {"puerto": 1945, "tls": tls}, "webrtc": None}
    en_marcha = {"rtmp": True, "rtmpAddress": ":1945", "rtmpEncryption": "optional", "rtmpsAddress": ":1946",
                 "rtmpServerCert": "/etc/hub/cert.pem", "rtmpServerKey": "/etc/hub/clave.pem"}
    roto = lambda cert, clave: "El certificado y su clave no se pueden usar juntos (KEY_VALUES_MISMATCH)"
    # MediaMTX sigue con el que cargó: apagarle RTMPS reiniciaría el servidor RTMP y cortaría a quien publica
    conf, problemas = mtx.conf_servidores(pide, en_marcha, libre=lambda puerto, tipo: False, certificado=roto)
    assert all(conf[k] == en_marcha[k] for k in en_marcha)
    assert problemas == {mtx.RTMPS_DISCO: "El certificado y su clave no se pueden usar juntos (KEY_VALUES_MISMATCH)"}
    assert "sigue en marcha como estaba" in mtx.aviso(mtx.RTMPS_DISCO, problemas[mtx.RTMPS_DISCO])
    assert mtx.aviso("rtmps", "No se encuentra el certificado (x)") == "MediaMTX: no se enciende RTMPS. No se encuentra el certificado (x)"
    # tampoco si lo que cambia es lo que se pide (otro certificado u otro puerto en config.json) y lo nuevo no vale:
    # aplicarlo cerraría MediaMTX entero y apagar RTMPS cortaría a quien publica. Sigue como estaba y se avisa
    for otro in (dict(tls, cert="/etc/hub/nuevo.pem"), dict(tls, puerto=1950)):
        conf, problemas = mtx.conf_servidores({"rtmp": {"puerto": 1945, "tls": otro}, "webrtc": None}, en_marcha,
                                              libre=lambda puerto, tipo: True, certificado=roto)
        assert all(conf[k] == en_marcha[k] for k in en_marcha) and list(problemas) == [mtx.RTMPS_DISCO]
    # un puerto nuevo que es de otro programa: lo mismo
    vale = lambda cert, clave: ""
    conf, problemas = mtx.conf_servidores({"rtmp": {"puerto": 1945, "tls": dict(tls, puerto=1950)}, "webrtc": None}, en_marcha,
                                          libre=lambda puerto, tipo: False, certificado=vale)
    assert conf["rtmpsAddress"] == ":1946" and problemas == {mtx.RTMPS_DISCO: "El puerto 1950/TCP está ocupado por otro programa"}
    # y lo nuevo, si vale, se aplica
    conf, problemas = mtx.conf_servidores({"rtmp": {"puerto": 1945, "tls": dict(tls, puerto=1950, cert="/etc/hub/nuevo.pem")}, "webrtc": None},
                                          en_marcha, libre=lambda puerto, tipo: True, certificado=vale)
    assert (conf["rtmpsAddress"], conf["rtmpServerCert"], conf["rtmpEncryption"]) == (":1950", "/etc/hub/nuevo.pem", "optional") and not problemas


def test_el_certificado_se_comprueba_antes_de_darselo_a_mediamtx(tmp_path):
    import shutil
    import subprocess
    assert "No se encuentra el certificado" in mtx.certificado_valido(str(tmp_path / "no-existe.pem"), str(tmp_path / "tampoco.pem"))
    cert, clave = tmp_path / "cert.pem", tmp_path / "clave.pem"
    cert.write_text("esto no es un certificado")
    clave.write_text("ni esto una clave")
    assert "no tiene un certificado en formato PEM" in mtx.certificado_valido(str(cert), str(clave))
    cert.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    assert "no tiene una clave privada en formato PEM" in mtx.certificado_valido(str(cert), str(clave))
    clave.write_text("-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n-----END ENCRYPTED PRIVATE KEY-----\n")
    assert "cifrada con contraseña" in mtx.certificado_valido(str(cert), str(clave))
    clave.write_text("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
    assert "no se pueden usar juntos" in mtx.certificado_valido(str(cert), str(clave))             # tienen la forma, pero no cargan
    if not shutil.which("openssl"):
        return
    for nombre in ("a", "b"):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(tmp_path / f"{nombre}.key"),
                        "-out", str(tmp_path / f"{nombre}.crt"), "-days", "2", "-subj", "/CN=hub-de-pruebas"],
                       check=True, capture_output=True, timeout=60)
    assert mtx.certificado_valido(str(tmp_path / "a.crt"), str(tmp_path / "a.key")) == ""
    assert "la clave es de otro certificado" in mtx.certificado_valido(str(tmp_path / "a.crt"), str(tmp_path / "b.key"))
    # una clave cifrada de verdad: se dice, y no se queda esperando a que alguien escriba la contraseña
    subprocess.run(["openssl", "pkey", "-in", str(tmp_path / "a.key"), "-aes256", "-passout", "pass:secreta", "-out", str(tmp_path / "c.key")],
                   check=True, capture_output=True, timeout=60)
    assert "cifrada con contraseña" in mtx.certificado_valido(str(tmp_path / "a.crt"), str(tmp_path / "c.key"))


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
