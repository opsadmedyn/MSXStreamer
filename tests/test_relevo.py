"""Desplegar esta versión no relanza las salidas que ya estaban al aire con la 0.3.9: el supervisor
arma para cada una la misma orden, con la misma firma, y por eso se queda con el proceso que
encuentra en marcha (tests/banco/relevo.sh lo ensaya entero, con señal).

Las firmas de abajo las dio el código de la 0.3.9 (commit 7094db7) con estas mismas filas. Si una
cambia, desplegar corta unos segundos todas las salidas de ese tipo, que se relanzan."""

import pathlib

import mtx
import supervisor

# (firma en la 0.3.9, salida como la guarda la base, path de MediaMTX, entrada directa de un flujo 1080i)
SALIDAS = [
    ("2260f94ab0ee1dae",
     {"id": "castr-1a2b", "tipo": "srt", "url": "srt://live.example.net:9998", "modo": "caller",
      "streamid": "#!::r=canal/uno,password=clave-de-castr", "passphrase": "frase de paso de la salida",
      "latencia_ms": 300, "clave": ""}, "rec_canal1", None),
    ("6a95dfa6fc005bf9",
     {"id": "en-la-url-3c4d", "tipo": "srt",
      "url": "srt://203.0.113.7:7001?streamid=publish/dos&passphrase=otra-frase-de-paso&oheadbw=25",
      "modo": "caller", "streamid": "", "passphrase": "", "latencia_ms": 2000, "clave": ""}, "hub-feed-9e8f", None),
    ("75d84c3714654c6b",
     {"id": "escucha-5e6f", "tipo": "srt", "url": "srt://0.0.0.0:9100", "modo": "listener", "streamid": "",
      "passphrase": "", "latencia_ms": 120, "clave": ""}, "pull-7a7b", None),
    ("4d6ec2474ab8f63b",
     {"id": "escucha-sin-puerto-0a0b", "tipo": "srt", "url": "srt://0.0.0.0", "modo": "listener", "streamid": "",
      "passphrase": "frase-de-quien-llama", "latencia_ms": 400, "clave": ""}, "pull-7a7b", None),
    ("347cbfbec9d1cae9",
     {"id": "youtube-7a8b", "tipo": "rtmp", "url": "rtmp://a.rtmp.youtube.com/live2/", "modo": "caller",
      "streamid": "", "passphrase": "", "latencia_ms": 300, "clave": "abcd-efgh-ijkl-mnop"}, "rec_canal2", None),
    ("b0a6358bd763ceaf",
     {"id": "rtmps-con-clave-en-la-url-9c0d", "tipo": "rtmp",
      "url": "rtmps://live-api-s.example.com:443/rtmp/FB-123-0-Ab?s_bl=1", "modo": "caller", "streamid": "",
      "passphrase": "", "latencia_ms": 300, "clave": ""}, "comp_pull-7a7b", None),
    ("00b262b8c03f509a",
     {"id": "directo-1e2f", "tipo": "srt", "url": "srt://198.51.100.20:6000", "modo": "caller",
      "streamid": "entrada-1080i", "passphrase": "", "latencia_ms": 800, "clave": ""}, None,
     "srt://10.20.30.40:8890?streamid=play/1BN+INT&passphrase=frase-del-hub&latency=400000"),
    ("ca732e1f431da518",
     {"id": "directo-rtmp-3a4b", "tipo": "rtmp", "url": "rtmp://127.0.0.1:1935/live", "modo": "caller",
      "streamid": "", "passphrase": "", "latencia_ms": 300, "clave": "clave"}, None,
     "srt://10.20.30.41:8890?streamid=play/2BN"),
]


def _como_en_la_estacion(monkeypatch):
    monkeypatch.setattr(supervisor, "FFMPEG", "/usr/local/bin/ffmpeg")
    monkeypatch.setattr(supervisor, "RUN", pathlib.Path("/home/mediasat/streamer/run"))
    monkeypatch.setattr(mtx, "SRT_LOCAL", "srt://127.0.0.1:8890")


def test_las_salidas_que_venian_de_la_0_3_9_conservan_su_firma(monkeypatch):
    _como_en_la_estacion(monkeypatch)
    distintas = [s["id"] for firma, s, path, directa in SALIDAS
                 if supervisor.firma(supervisor.comando(s, path, directa)) != firma]
    assert distintas == []


def test_la_orden_de_una_salida_srt_y_la_de_un_flujo_directo_son_las_de_la_0_3_9(monkeypatch):
    _como_en_la_estacion(monkeypatch)
    _, s, path, _ = SALIDAS[0]
    assert supervisor.comando(s, path) == [
        "/usr/local/bin/ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-progress", "/home/mediasat/streamer/run/castr-1a2b.prog", "-stats_period", "2",
        "-i", "srt://127.0.0.1:8890?streamid=read:rec_canal1", "-map", "0", "-c", "copy", "-f", "mpegts",
        "-passphrase", "frase de paso de la salida", "-srt_streamid", "#!::r=canal/uno,password=clave-de-castr",
        "srt://live.example.net:9998?mode=caller&latency=300000&pkt_size=1316"]
    _, s, _, directa = SALIDAS[6]
    assert supervisor.comando(s, None, directa) == [
        "/usr/local/bin/ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-progress", "/home/mediasat/streamer/run/directo-1e2f.prog", "-stats_period", "2",
        "-srt_streamid", "play/1BN INT", "-passphrase", "frase-del-hub", "-i", "srt://10.20.30.40:8890?latency=400000",
        "-map", "0", "-c", "copy", "-f", "mpegts", "-srt_streamid", "entrada-1080i",
        "srt://198.51.100.20:6000?mode=caller&latency=800000&pkt_size=1316"]
