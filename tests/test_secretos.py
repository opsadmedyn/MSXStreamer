"""Las claves no llegan a los registros ni a los textos que enseña el panel."""

import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import secretos

AQUI = pathlib.Path(__file__).resolve().parent
CORRER = AQUI.parent / "streamer" / "correr.py"


def test_url_visible():
    v = secretos.url_visible
    assert v("srt://203.0.113.9:9000?streamid=abc&passphrase=clave-larga-1", "srt") == "srt://203.0.113.9:9000?***"
    assert v("rtmp://a.rtmp.example/live2/abcd-1234", "rtmp") == "rtmp://a.rtmp.example/live2/***"
    assert v("rtmp://a.rtmp.example/live2", "rtmp", "abcd-1234") == "rtmp://a.rtmp.example/live2"
    assert v("https://usuario:secreta@cdn.example/x/master.m3u8?token=T0K3N", "hls") == "https://cdn.example/x/master.m3u8?***"
    assert v("", "srt") == ""


def test_en_este_equipo_solo_se_ve_entero_el_enlace_interno_del_hub():
    v = secretos.url_visible
    interno = "srt://127.0.0.1:8890?streamid=read:conv_camara-1_a1b2c3&latency=200000&pkt_size=1316"
    assert v(interno, "srt") == interno
    # otro servidor que corra en este mismo equipo, con su clave: se tapa como cualquiera de fuera
    assert v("rtmp://127.0.0.1:1935/live/clave-del-restreamer", "rtmp") == "rtmp://127.0.0.1:1935/live/***"
    assert v("srt://127.0.0.1:9000?passphrase=frase-de-paso-1", "srt") == "srt://127.0.0.1:9000?***"
    assert v("srt://127.0.0.1:8890?streamid=read:camara-1:usuario:clave-de-lectura", "srt") == "srt://127.0.0.1:8890?***"
    assert v("http://127.0.0.1:8080/whip/sala?token=T0K3N", "webrtc") == "http://127.0.0.1:8080/whip/sala?***"


def test_valores_de_una_fila():
    fila = {"protocolo": "rtmp", "url": "rtmps://live.example:443/app/CLAVE-EN-RUTA?firma=F1RMA", "clave": "",
            "streamid": "#!::r=canal,password=p4ss", "passphrase": "frase-de-paso-1"}
    v = secretos.valores_de(fila)
    assert {"CLAVE-EN-RUTA", "F1RMA", "#!::r=canal,password=p4ss", "frase-de-paso-1"} <= v
    # con la clave aparte, el último tramo de la URL es la aplicación, no una clave
    assert "app" not in secretos.valores_de({"protocolo": "rtmp", "url": "rtmp://h/app", "clave": "K"})


def test_ocultar_tapa_claves_tambien_codificadas_y_las_de_destinos_borrados():
    ocultos = secretos.ordenar({"#!::r=canal,password=p4ss", "frase-de-paso-1", "abcd-1234"})
    texto = ("[srt @ 0x55] Connection to srt://198.51.100.7:9000?passphrase=frase-de-paso-1&streamid=%23%21%3A%3Ar%3Dcanal%2Cpassword%3Dp4ss failed\n"
             "[rtmp @ 0x56] Error opening output rtmp://a.rtmp.example/live2/abcd-1234: Connection refused\n"
             "[rtmp @ 0x57] Cannot open rtmp://otra.example/app/clave-que-ya-no-esta-en-la-base: timeout")
    limpio = secretos.ocultar(texto, ocultos)
    for clave in ("frase-de-paso-1", "p4ss", "abcd-1234", "clave-que-ya-no-esta-en-la-base", "%23%21"):
        assert clave not in limpio
    assert "srt://198.51.100.7:9000?***" in limpio and "rtmp://a.rtmp.example/live2/***: Connection refused" in limpio


def test_linea_de_arranque_sin_claves():
    cmd = ["/usr/local/bin/ffmpeg", "-hide_banner", "-i", "srt://127.0.0.1:8890?streamid=read:camara-1",
           "-map", "0", "-c", "copy", "-f", "mpegts", "-passphrase", "frase-de-paso-1",
           "-srt_streamid", "#!::r=canal, con espacio,password=p4ss",
           "srt://198.51.100.7:9000?mode=caller&latency=300000&pkt_size=1316"]
    linea = secretos.ocultar_comando(cmd, secretos.ordenar({"frase-de-paso-1"}))
    assert "frase-de-paso-1" not in linea and "p4ss" not in linea and "con espacio" not in linea
    assert "-passphrase ***" in linea and "-srt_streamid ***" in linea
    assert linea.startswith("(sin claves) /usr/local/bin/ffmpeg -hide_banner") and "198.51.100.7:9000?***" in linea
    assert "srt://127.0.0.1:8890?streamid=read:camara-1" in linea       # lo local se ve: dice qué path se lee


def test_limpiar_oculta_la_linea_de_arranque_de_los_registros_antiguos():
    viejo = "Connection refused\n== 2026-11-01 10:00:00 ffmpeg -passphrase frase-de-paso-1 srt://198.51.100.7:9000"
    assert secretos.limpiar(viejo, []) == "Connection refused\n(línea de arranque oculta)"
    assert secretos.limpiar("ffmpeg -i x -passphrase abc srt://h:1", []) == "(línea de arranque oculta)"
    assert secretos.limpiar("", []) == ""


def test_limpiar_registro_reescribe_un_registro_antiguo(tmp_path):
    log = tmp_path / "salida.log"
    log.write_text("\n== 2026-11-01 10:00:00 /usr/local/bin/ffmpeg -i srt://127.0.0.1:8890?streamid=read:x -c copy "
                   "-passphrase frase-de-paso-1 -srt_streamid #!::r=a, b,password=p4ss srt://198.51.100.7:9000?mode=caller\n"
                   "[rtmp @ 0x1] Error opening output rtmp://a.rtmp.example/live2/abcd-1234: Connection refused\n")
    assert secretos.limpiar_registro(log, secretos.ordenar({"otra-clave"})) is True
    texto = log.read_text()
    for clave in ("frase-de-paso-1", "p4ss", "abcd-1234"):
        assert clave not in texto
    assert "== 2026-11-01 10:00:00 (línea de arranque de una versión anterior, oculta)" in texto
    assert secretos.limpiar_registro(log) is False             # ya está limpio: no se toca
    # las líneas de arranque que escribe el lanzador ya van sin claves: se conservan
    nuevo = "\n== 2026-11-19 06:38:23 " + secretos.ocultar_comando(["ffmpeg", "-passphrase", "frase-de-paso-1", "srt://h:1"]) + "\n"
    log.write_text(nuevo)
    assert secretos.limpiar_registro(log) is False and log.read_text() == nuevo
    assert secretos.limpiar_registro(tmp_path / "no-existe.log") is False


def _lanzar(tmp_path, guion, ocultos):
    falso = tmp_path / "ffmpeg-falso.sh"
    falso.write_text("#!/bin/bash\n" + guion)
    falso.chmod(0o755)
    log = tmp_path / "ruta.log"
    entorno = {**os.environ, "MSXS_SECRETOS": json.dumps(ocultos)}
    cmd = [sys.executable, str(CORRER), str(log), str(falso), "-passphrase", "frase-de-paso-1",
           "rtmp://a.rtmp.example/live2/abcd-1234"]
    return subprocess.Popen(cmd, env=entorno, start_new_session=True), log


def test_el_lanzador_no_deja_claves_en_el_registro(tmp_path):
    guion = ('echo "[rtmp @ 0x1] Error opening output $3: Connection refused" >&2\n'
             'echo "clave en el entorno: ${MSXS_SECRETOS:-ninguna}" >&2\n'
             'printf "sin salto de linea final con abcd-1234" >&2\nexit 7\n')
    p, log = _lanzar(tmp_path, guion, ["abcd-1234", "frase-de-paso-1"])
    assert p.wait(timeout=20) == 7                              # sale con el código de ffmpeg
    texto = log.read_text()
    assert "abcd-1234" not in texto and "frase-de-paso-1" not in texto
    assert "-passphrase ***" in texto and "rtmp://a.rtmp.example/live2/***: Connection refused" in texto
    assert "clave en el entorno: ninguna" in texto              # la lista de claves no pasa al comando
    assert "sin salto de linea final con ***" in texto


def test_el_lanzador_para_a_su_ffmpeg(tmp_path):
    guion = 'echo $$ > "$(dirname "$0")/pid"\ntrap "echo terminado >&2; exit 0" TERM\nwhile true; do sleep 0.1; done\n'
    p, log = _lanzar(tmp_path, guion, [])
    pid_archivo = tmp_path / "pid"
    for _ in range(100):
        if pid_archivo.exists() and pid_archivo.read_text().strip():
            break
        time.sleep(0.05)
    hijo = int(pid_archivo.read_text())
    os.killpg(p.pid, signal.SIGTERM)                            # así lo para el supervisor
    assert p.wait(timeout=20) == 0
    assert "terminado" in log.read_text()
    assert not pathlib.Path(f"/proc/{hijo}").exists()


def test_si_el_lanzador_muere_su_ffmpeg_tambien(tmp_path):
    """Un ffmpeg sin lanzador seguiría emitiendo sin vigilancia y el supervisor lanzaría otro igual."""
    guion = 'echo $$ > "$(dirname "$0")/pid"\nwhile true; do sleep 0.1; done\n'
    p, _ = _lanzar(tmp_path, guion, [])
    pid_archivo = tmp_path / "pid"
    for _ in range(100):
        if pid_archivo.exists() and pid_archivo.read_text().strip():
            break
        time.sleep(0.05)
    hijo = int(pid_archivo.read_text())
    os.kill(p.pid, signal.SIGKILL)                              # solo el lanzador
    p.wait(timeout=20)
    for _ in range(100):
        estado = pathlib.Path(f"/proc/{hijo}/stat")
        if not estado.exists() or estado.read_text().split(")")[-1].split()[0] == "Z":
            break
        time.sleep(0.05)
    else:
        os.kill(hijo, signal.SIGKILL)
        raise AssertionError("el ffmpeg siguió vivo sin su lanzador")


def test_el_lanzador_sale_aunque_otro_proceso_siga_con_la_salida_abierta(tmp_path):
    """En una composición, las capturas heredan la salida de errores: si ffmpeg cae, el lanzador no
    debe quedarse esperándolas (el supervisor creería que la composición sigue viva)."""
    guion = 'sleep 30 &\necho $! > "$(dirname "$0")/pid"\necho "cae ffmpeg" >&2\nexit 3\n'
    p, log = _lanzar(tmp_path, guion, [])
    try:
        assert p.wait(timeout=10) == 3
        assert "cae ffmpeg" in log.read_text()
    finally:
        os.kill(int((tmp_path / "pid").read_text()), signal.SIGKILL)
