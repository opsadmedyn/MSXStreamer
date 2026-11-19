"""Las sondas de «Probar la conexión»: dicen si al otro lado hay alguien que hable el protocolo sin
publicar ni leer señal, y nunca devuelven una clave. Los otros equipos son servidores de mentira en
127.0.0.1 (y, para SRT, un ffmpeg en modo listener de verdad si este ffmpeg trae libsrt)."""

import http.server
import shutil
import socket
import struct
import subprocess
import threading
import time

import pytest

import sondas


def puerto_libre(tipo=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, tipo)
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    return puerto


def servidor_tcp(responder):
    """Escucha en un puerto y, a cada conexión, llama a responder(conexión) en un hilo."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(4)

    def atender():
        while True:
            try:
                conexion, _ = s.accept()
            except OSError:
                return
            with conexion:
                try:
                    responder(conexion)
                except OSError:
                    pass
    threading.Thread(target=atender, daemon=True).start()
    return s, s.getsockname()[1]


def leer(conexion, n):
    datos = b""
    while len(datos) < n:
        trozo = conexion.recv(n - len(datos))
        if not trozo:
            break
        datos += trozo
    return datos


# ---------------------------------------------------------------- piezas
def test_una_prueba_solo_da_por_buena_la_conexion_si_todo_lo_comprobable_fue_bien():
    bien = sondas.paso("Saludo", True, "responde")
    clave = sondas.paso("Clave", None, "se comprueba al conectar", al_conectar=True)
    duda = sondas.paso("Dirección", None, "no se puede saber sin publicar")
    mal = sondas.paso("Conexión", False, "cerrado")
    assert sondas.resultado([bien, clave])["ok"] is True          # lo que solo se ve al conectar no cuenta
    assert sondas.resultado([bien, duda])["ok"] is None
    assert sondas.resultado([bien, duda, mal])["ok"] is False
    assert sondas.resultado([bien, mal])["resumen"] == "cerrado"   # el resumen es el primer fallo


def test_una_direccion_sin_servidor_o_sin_puerto_se_explica():
    with pytest.raises(ValueError, match="le falta el servidor"):
        sondas.partes_url("rtmp:///live")
    with pytest.raises(ValueError, match="le falta el puerto"):
        sondas.partes_url("srt://receptor.ejemplo.com")
    assert sondas.partes_url("rtmps://a.rtmps.youtube.com/live2", 443) == ("rtmps", "a.rtmps.youtube.com", 443)
    assert sondas.partes_url("RTMP://Servidor:1936/live", 1935) == ("rtmp", "servidor", 1936)


def test_la_respuesta_de_induccion_se_reconoce_solo_si_es_para_este_saludo():
    def respuesta(id_socket, version=5, extension=sondas.SRT_MAGIA, tipo=1):
        return (struct.pack(">IIII", 0x80000000, 0, 0, id_socket)
                + struct.pack(">IHHIIIiII", version, 0, extension, 0, 1500, 8192, tipo, 99, 0) + bytes(16))
    assert sondas.leer_induccion(respuesta(1234), 1234) == {"version": 5, "srt": True}
    assert sondas.leer_induccion(respuesta(1234, version=4, extension=2), 1234) == {"version": 4, "srt": False}
    assert sondas.leer_induccion(respuesta(1234), 4321) is None                 # era para otro
    assert sondas.leer_induccion(respuesta(1234, tipo=0), 1234) is None         # no es la respuesta de inducción
    assert sondas.leer_induccion(b"\x80" * 20, 1234) is None                    # demasiado corta
    assert len(sondas.paquete_induccion(7)) == 64


# ---------------------------------------------------------------- SRT
def test_srt_responde_quien_contesta_al_saludo_como_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))

    def contestar():
        datos, quien = s.recvfrom(1500)
        id_socket = struct.unpack(">I", datos[40:44])[0]              # el «socket id» de quien llama
        s.sendto(struct.pack(">IIII", 0x80000000, 0, 0, id_socket)
                 + struct.pack(">IHHIIIiII", 5, 0, sondas.SRT_MAGIA, 0, 1500, 8192, 1, 77, 0) + bytes(16), quien)
    threading.Thread(target=contestar, daemon=True).start()
    try:
        r = sondas.srt_escucha("127.0.0.1", s.getsockname()[1], espera=2)
    finally:
        s.close()
    assert r["ok"] is True
    assert [p["que"] for p in r["pasos"]] == ["Resolver el nombre", "Saludo SRT", "Passphrase y stream ID"]
    assert "se comprueban al conectar" in r["resumen"]


def test_srt_sin_nadie_escuchando_lo_dice_y_no_espera_de_mas():
    t = time.monotonic()
    r = sondas.srt_escucha("127.0.0.1", puerto_libre(socket.SOCK_DGRAM), espera=0.6)
    assert time.monotonic() - t < 3
    assert r["ok"] is False
    assert r["pasos"][-1]["que"] == "Saludo SRT" and r["pasos"][-1]["ok"] is False


def test_un_nombre_que_no_existe_se_dice_antes_de_intentar_nada():
    r = sondas.srt_escucha("no-existe.invalid", 9000, espera=0.5)
    assert r["ok"] is False and "No se encuentra «no-existe.invalid»" in r["resumen"]


def _ffmpeg_con_srt():
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    r = subprocess.run([ff, "-hide_banner", "-protocols"], capture_output=True, text=True, timeout=20)
    return ff if "srt" in r.stdout.split() else None


@pytest.mark.skipif(not _ffmpeg_con_srt(), reason="este ffmpeg no trae libsrt")
def test_srt_reconoce_a_un_listener_de_verdad_sin_ocuparlo():
    puerto = puerto_libre(socket.SOCK_DGRAM)
    receptor = subprocess.Popen([_ffmpeg_con_srt(), "-hide_banner", "-loglevel", "error", "-f", "mpegts",
                                 "-i", f"srt://127.0.0.1:{puerto}?mode=listener", "-f", "null", "-"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = None
        for _ in range(20):                     # lo que tarde ffmpeg en ponerse a escuchar
            r = sondas.srt_escucha("127.0.0.1", puerto, espera=0.5)
            if r["ok"]:
                break
            time.sleep(0.2)
        assert r["ok"] is True, r
        assert receptor.poll() is None          # el saludo no abre conexión: el receptor sigue esperando
    finally:
        receptor.kill()
        receptor.wait()


# ---------------------------------------------------------------- RTMP
def test_rtmp_responde_un_servidor_que_saluda_como_rtmp_y_no_se_le_manda_la_clave():
    recibido = []

    def servidor(conexion):
        recibido.append(leer(conexion, 1537))                         # C0 + C1
        conexion.sendall(b"\x03" + bytes(1536) + bytes(1536))         # S0 + S1 + S2
        recibido.append(conexion.recv(4096))                          # lo que llegue después: nada
    s, puerto = servidor_tcp(servidor)
    try:
        r = sondas.rtmp_responde(f"rtmp://127.0.0.1:{puerto}/live/CLAVE-DE-EMISION", espera=2)
    finally:
        s.close()
    assert r["ok"] is True
    assert [p["que"] for p in r["pasos"]] == ["Resolver el nombre", "Conexión TCP", "Saludo RTMP", "Clave de emisión"]
    time.sleep(0.1)
    assert all(b"CLAVE-DE-EMISION" not in x for x in recibido)
    assert "CLAVE-DE-EMISION" not in str(r)


def test_rtmp_distingue_un_puerto_cerrado_de_un_servidor_que_no_habla_rtmp():
    r = sondas.rtmp_responde(f"rtmp://127.0.0.1:{puerto_libre()}/live", espera=1)
    assert r["ok"] is False and r["pasos"][-1]["que"] == "Conexión TCP"
    assert "no hay nada escuchando" in r["resumen"]

    s, puerto = servidor_tcp(lambda c: (leer(c, 1537), c.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")))
    try:
        r = sondas.rtmp_responde(f"rtmp://127.0.0.1:{puerto}/live", espera=2)
    finally:
        s.close()
    assert r["ok"] is False and r["pasos"][-1]["que"] == "Saludo RTMP"
    assert "no responde como RTMP" in r["resumen"]


def test_rtmps_sin_tls_al_otro_lado_falla_en_la_conexion_segura():
    s, puerto = servidor_tcp(lambda c: (c.recv(4096), c.sendall(b"\x03" + bytes(3072))))
    try:
        r = sondas.rtmp_responde(f"rtmps://127.0.0.1:{puerto}/live", espera=2)
    finally:
        s.close()
    assert r["ok"] is False and r["pasos"][-1]["que"] == "Conexión segura (TLS)"


@pytest.mark.skipif(not shutil.which("openssl"), reason="hace falta openssl para crear el certificado de la prueba")
def test_rtmps_con_un_certificado_que_no_es_valido_no_se_da_por_caido(tmp_path):
    """Un servidor RTMPS propio con certificado autofirmado: quien envía (ffmpeg) no comprueba el
    certificado, así que la ruta conectará. La prueba lo dice como aviso y sigue hasta el saludo."""
    import ssl
    clave, cert = tmp_path / "clave.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(clave), "-out", str(cert),
                    "-days", "2", "-subj", "/CN=servidor-de-pruebas"], check=True, capture_output=True, timeout=60)
    servidor_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    servidor_tls.load_cert_chain(str(cert), str(clave))

    def servidor(conexion):
        try:
            tls = servidor_tls.wrap_socket(conexion, server_side=True)
        except ssl.SSLError:                                # el primer intento: el cliente rechaza el certificado
            return
        leer(tls, 1537)
        tls.sendall(b"\x03" + bytes(3072))
        tls.recv(4096)
    s, puerto = servidor_tcp(servidor)
    try:
        r = sondas.rtmp_responde(f"rtmps://127.0.0.1:{puerto}/live/CLAVE-DE-EMISION", espera=3)
    finally:
        s.close()
    pasos = {p["que"]: p for p in r["pasos"]}
    assert pasos["Conexión segura (TLS)"]["ok"] is None and "certificado del servidor no es válido" in pasos["Conexión segura (TLS)"]["detalle"]
    assert pasos["Saludo RTMP"]["ok"] is True
    assert r["ok"] is None and "emite igualmente" in r["resumen"]           # ni «todo bien» ni «no conecta»
    assert "CLAVE-DE-EMISION" not in str(r)


# ---------------------------------------------------------------- WHIP
class _Whip(http.server.BaseHTTPRequestHandler):
    token = "token-correcto"

    def do_OPTIONS(self):
        if self.path != "/whip/estudio":
            self.send_response(404)
        elif self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_response(401)
        else:
            self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def whip(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")         # el servidor de mentira está en este equipo
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Whip)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def test_whip_dice_si_la_direccion_existe_y_si_acepta_el_token(whip):
    bien = sondas.whip_responde(f"{whip}/whip/estudio", "token-correcto", espera=2)
    assert bien["ok"] is True and "acepta la dirección y el token" in bien["resumen"]

    mal_token = sondas.whip_responde(f"{whip}/whip/estudio", "token-equivocado", espera=2)
    assert mal_token["ok"] is False and mal_token["pasos"][-1]["que"] == "Token"
    assert "token-equivocado" not in str(mal_token)

    sin_token = sondas.whip_responde(f"{whip}/whip/estudio", "", espera=2)
    assert sin_token["ok"] is False and "pide un token" in sin_token["resumen"]

    no_existe = sondas.whip_responde(f"{whip}/whip/otro", "token-correcto", espera=2)
    assert no_existe["ok"] is False and "no existe" in no_existe["resumen"]


def test_whip_solo_admite_direcciones_http():
    r = sondas.whip_responde("rtmp://servidor:1935/live", "x")
    assert r["ok"] is False and "https://" in r["resumen"]


# ---------------------------------------------------------------- lo que escucha el hub
def test_lo_que_el_hub_entrega_se_prueba_mirando_que_este_escuchando():
    s, puerto = servidor_tcp(lambda c: None)
    try:
        assert sondas.hub_escucha(puerto, "tcp", "MediaMTX (HLS)")["ok"] is True
    finally:
        s.close()
    assert sondas.hub_escucha(puerto_libre(), "tcp", "MediaMTX (HLS)")["ok"] is False

    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind(("0.0.0.0", 0))
    try:
        assert sondas.hub_escucha(u.getsockname()[1], "udp", "MediaMTX (SRT)")["ok"] is True
    finally:
        u.close()
    assert sondas.hub_escucha(puerto_libre(socket.SOCK_DGRAM), "udp", "MediaMTX (SRT)")["ok"] is False
