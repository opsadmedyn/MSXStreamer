"""Los comandos de ffmpeg de cada pieza: qué leen, qué copian, qué codifican y a dónde lo mandan."""

import json

import comandos
import plan

LEER = "srt://127.0.0.1:8890?streamid=read:"
PUBLICAR = "srt://127.0.0.1:8890?streamid=publish:"


def fuente(**mas):
    return {"id": "f1", "protocolo": "srt", "inicia": "hub", "url": "", "streamid": "", "passphrase": "",
            "latencia_ms": 400, "clave": "", "opciones": "{}", **mas}


def destino(**mas):
    return {"id": "d1", "protocolo": "srt", "inicia": "hub", "url": "", "streamid": "", "passphrase": "",
            "latencia_ms": 300, "clave": "", "opciones": "{}", **mas}


def tras(cmd, opcion):
    return cmd[cmd.index(opcion) + 1]


def test_que_piezas_llevan_proceso():
    assert comandos.con_entrada(fuente(protocolo="srt", inicia="hub"))
    assert not comandos.con_entrada(fuente(protocolo="srt", inicia="origen"))        # publica en MediaMTX
    assert comandos.con_entrada(fuente(protocolo="srt", inicia="origen", opciones='{"puerto": 9101}'))
    assert comandos.con_entrada(fuente(protocolo="hls", inicia="hub"))
    assert not comandos.con_entrada(fuente(protocolo="rtmp", inicia="origen"))
    assert not comandos.con_entrada(fuente(protocolo="webrtc", inicia="origen"))
    assert comandos.con_envio(destino(protocolo="rtmp", inicia="hub"))
    assert not comandos.con_envio(destino(protocolo="rtmp", inicia="receptor"))      # lo sirve MediaMTX
    assert not comandos.con_envio(destino(protocolo="hls", inicia="receptor"))
    assert not comandos.con_envio(destino(protocolo="webrtc", inicia="hub"))         # reenvío WHIP de MediaMTX
    assert comandos.con_envio(destino(protocolo="srt", inicia="receptor", opciones='{"puerto": 9201}'))


def test_entrada_srt_el_hub_llama_con_las_claves_aparte():
    f = fuente(url="srt://203.0.113.20:9000?streamid=en-la-url", streamid="#!::r=canal,password=p4ss",
               passphrase="frase-de-paso-1", latencia_ms=800)
    cmd = comandos.entrada("ent_f1", f, "f1")
    assert tras(cmd, "-passphrase") == "frase-de-paso-1" and tras(cmd, "-srt_streamid") == "#!::r=canal,password=p4ss"
    assert tras(cmd, "-i") == "srt://203.0.113.20:9000?latency=800000&mode=caller"
    assert cmd.index("-passphrase") < cmd.index("-i")                   # opciones de la entrada
    assert cmd[-1] == PUBLICAR + "f1&pkt_size=1316" and cmd[-3:-1] == ["-f", "mpegts"]
    assert "p4ss" not in tras(cmd, "-i") and "copy" in cmd


def test_entrada_srt_con_puerto_propio_escucha():
    cmd = comandos.entrada("ent_f1", fuente(inicia="origen", passphrase="frase-de-paso-1",
                                             opciones=json.dumps({"puerto": 9101})), "f1")
    assert tras(cmd, "-i") == "srt://0.0.0.0:9101?latency=400000&mode=listener"
    assert "-srt_streamid" not in cmd and tras(cmd, "-passphrase") == "frase-de-paso-1"


def test_entrada_rtmp_y_hls():
    cmd = comandos.entrada("ent_f1", fuente(protocolo="rtmp", url="rtmp://origen.example/app/", clave="k3y"), "f1")
    assert tras(cmd, "-i") == "rtmp://origen.example/app/k3y" and "-rw_timeout" in cmd
    cmd = comandos.entrada("ent_f1", fuente(protocolo="hls", url="https://cdn.example/x/master.m3u8",
                                             opciones='{"variante": 1}'), "f1")
    assert tras(cmd, "-i") == "https://cdn.example/x/master.m3u8" and "-re" in cmd
    assert tras(cmd, "-map") == "0:p:1"                                 # la variante elegida y sus audios
    assert tras(comandos.entrada("e", fuente(protocolo="hls", url="https://x/y.m3u8"), "f1"), "-map") == "0:p:0"
    # al ritmo de la señal y con colchón: tres segmentos por detrás del directo (con uno salía a golpes)
    assert "-re" in cmd and tras(cmd, "-live_start_index") == "-3" and cmd.index("-re") < cmd.index("-i")


def test_envio_srt_en_copia_con_todas_las_pistas():
    d = destino(url="srt://198.51.100.7:9000", streamid="canal-7", passphrase="frase-de-paso-1", latencia_ms=250)
    cmd = comandos.envio("ruta_d1", d, "f1", [0, 1], entera=True)
    assert tras(cmd, "-i") == LEER + "f1" and tras(cmd, "-map") == "0"
    assert cmd[-1] == "srt://198.51.100.7:9000?mode=caller&latency=250000&pkt_size=1316"
    assert tras(cmd, "-passphrase") == "frase-de-paso-1" and tras(cmd, "-srt_streamid") == "canal-7"
    assert "run/ruta_d1.prog" in tras(cmd, "-progress")


def test_envio_srt_solo_con_la_pista_elegida():
    cmd = comandos.envio("ruta_d1", destino(url="srt://198.51.100.7:9000"), "f1", [1], entera=False)
    mapas = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert mapas == ["0:v:0?", "0:a:1?"]


def test_envio_srt_a_un_receptor_que_llama_a_un_puerto_propio():
    d = destino(inicia="receptor", passphrase="frase-de-paso-1", streamid="no-se-usa", opciones='{"puerto": 9201}')
    cmd = comandos.envio("ruta_d1", d, "f1")
    assert cmd[-1] == "srt://0.0.0.0:9201?mode=listener&latency=300000&pkt_size=1316"
    assert "-srt_streamid" not in cmd


def test_envio_rtmp_lleva_un_video_y_la_pista_elegida():
    d = destino(protocolo="rtmp", url="rtmps://live.example:443/app/", clave="abcd-1234")
    cmd = comandos.envio("ruta_d1", d, "f1", [1], entera=False)
    mapas = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert mapas == ["0:v:0?", "0:a:1?"] and cmd[-3:] == ["-f", "flv", "rtmps://live.example:443/app/abcd-1234"]


def test_conversion_solo_de_audio_copia_el_video():
    pistas = {"video": {"codec": "h264", "ancho": 1920, "alto": 1080, "fps": 29.97, "b": False},
              "audio": [{"codec": "aac", "canales": 2, "hz": 48000}]}
    p = plan.planear(pistas, "webrtc", empuja=False)
    cmd = comandos.conversion("conv_f1_x", "f1", p["senal"], "conv_f1_x", pistas)
    assert cmd[0].endswith("ffmpeg")                                    # sin «nice»: no codifica vídeo
    assert tras(cmd, "-c:v") == "copy" and tras(cmd, "-c:a:0") == "libopus" and tras(cmd, "-b:a:0") == "128k"
    assert tras(cmd, "-ac:a:0") == "2" and "-vf" not in cmd
    assert tras(cmd, "-i") == LEER + "f1&latency=200000" and cmd[-1] == PUBLICAR + "conv_f1_x&pkt_size=1316"


def test_conversion_de_video_escala_y_quita_los_fotogramas_b():
    pistas = {"video": {"codec": "h264", "ancho": 1920, "alto": 1080, "fps": 59.94, "b": True, "entrelazado": True},
              "audio": [{"codec": "aac", "canales": 2, "hz": 48000}, {"codec": "aac", "canales": 2, "hz": 48000}]}
    p = plan.planear(pistas, "srt", perfil=plan.PERFILES_FIJOS[2])      # 720p · 3 Mb/s, dos pistas en copia
    cmd = comandos.conversion("conv_f1_x", "f1", p["senal"], "conv_f1_x", pistas, codificador="h264_nvenc")
    assert cmd[:3] == ["nice", "-n", "5"]
    assert tras(cmd, "-vf") == "yadif=mode=send_frame:parity=auto:deint=interlaced,scale=1280:720,setsar=1,format=yuv420p"
    assert tras(cmd, "-c:v") == "h264_nvenc" and tras(cmd, "-b:v") == "3000k" and tras(cmd, "-bf") == "0"
    assert tras(cmd, "-g") == "120"                                     # un fotograma clave cada 2 s
    assert tras(cmd, "-c:a:0") == "copy" and tras(cmd, "-c:a:1") == "copy"
    mapas = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert mapas == ["0:v:0", "0:a:0", "0:a:1"]
    x264 = comandos.conversion("conv_f1_x", "f1", p["senal"], "conv_f1_x", pistas)
    assert tras(x264, "-c:v") == "libx264" and tras(x264, "-tune") == "zerolatency"


def test_conversion_sin_cambiar_medidas_fija_igualmente_la_medida_de_salida():
    """Un navegador que publica por WebRTC cambia el tamaño de la imagen sobre la marcha: el
    codificador debe recibir siempre el de salida, también cuando la fuente «ya lo trae»."""
    pistas = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "fps": 30, "b": True},
              "audio": [{"codec": "opus", "canales": 6, "hz": 48000}]}
    p = plan.planear(pistas, "webrtc", empuja=False)
    cmd = comandos.conversion("conv_f1_x", "f1", p["senal"], "conv_f1_x", pistas)
    assert tras(cmd, "-vf") == "scale=1280:720,setsar=1,format=yuv420p" and tras(cmd, "-ac:a:0") == "2"


def test_lo_que_se_lee_del_propio_hub_arranca_sin_esperar_a_otros_programas():
    """El enlace interno trae un solo programa: no hay que esperar 5 s por si aparece otro. La señal
    de un origen sí puede traer varios y se abre como siempre."""
    pistas = {"video": {"codec": "h264", "ancho": 1280, "alto": 720, "fps": 30, "b": True}, "audio": []}
    lectores = [comandos.envio("ruta_d1", destino(url="srt://198.51.100.7:9000"), "f1"),
                comandos.envio("ruta_d1", destino(protocolo="rtmp", url="rtmp://live.example/app", clave="k"), "f1"),
                comandos.conversion("conv_f1_x", "f1", plan.planear(pistas, "webrtc", empuja=False)["senal"], "conv_f1_x", pistas)]
    for cmd in lectores:
        assert tras(cmd, "-scan_all_pmts") == "0" and cmd.index("-scan_all_pmts") < cmd.index("-i")
    for f in (fuente(url="srt://198.51.100.7:9000"), fuente(protocolo="rtmp", url="rtmp://origen.example/app"),
              fuente(protocolo="hls", url="https://cdn.example/x/master.m3u8")):
        assert "-scan_all_pmts" not in comandos.entrada("ent_f1", f, "f1")


def test_la_firma_del_comando_no_cambia_entre_vueltas():
    d = destino(url="srt://198.51.100.7:9000")
    assert comandos.envio("ruta_d1", d, "f1", [0]) == comandos.envio("ruta_d1", dict(d), "f1", [0])
