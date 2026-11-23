"""La espera de una fuente HLS: no empezar a leer una lista que acaba de nacer hasta que tenga margen."""

import http.server
import pathlib
import subprocess
import sys
import threading
import time

import pytest

import comandos
import espera_hls as E
import estados

MAESTRA = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Español",URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,AUDIO="a"
alta/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="a"
https://otro.example/baja/video.m3u8?t=abc
"""


def lista(segmentos, dura=2, cerrada=False, partes=0, huecos=0):
    """Una lista de medios como las de MediaMTX: los huecos de relleno van delante de los segmentos."""
    lineas = ["#EXTM3U", "#EXT-X-VERSION:9", f"#EXT-X-TARGETDURATION:{dura}", "#EXT-X-MEDIA-SEQUENCE:0",
              '#EXT-X-MAP:URI="init.mp4"']
    for _ in range(huecos):
        lineas += ["#EXT-X-GAP", f"#EXTINF:{dura:.5f},", "gap.mp4"]
    for i in range(segmentos):
        lineas += ["#EXT-X-PROGRAM-DATE-TIME:2026-11-19T13:20:31.558Z", f"#EXTINF:{dura:.5f},", f"seg{i}.mp4"]
    lineas += [f'#EXT-X-PART:DURATION=0.2,URI="parte{i}.mp4"' for i in range(partes)]
    return "\n".join(lineas + (["#EXT-X-ENDLIST"] if cerrada else [])) + "\n"


class Origen:
    """Lo que hace de red en las pruebas de `esperar`: sirve las listas que se le van poniendo y
    lleva un reloj que solo avanza cuando el programa duerme."""

    def __init__(self, paginas, al_dormir=None):
        self.paginas, self.al_dormir, self.t, self.pedidas, self.dicho = paginas, al_dormir, 100.0, [], []

    def abrir(self, url):
        self.pedidas.append(url)
        pagina = self.paginas[url]
        if isinstance(pagina, Exception):
            raise pagina
        return pagina, url

    def dormir(self, s):
        self.t += s
        if self.al_dormir:
            self.al_dormir(self)

    def esperar(self, url, variante=0, hacen_falta=3):
        E.esperar(url, variante, hacen_falta, self.dicho.append, abrir=self.abrir, ahora=lambda: self.t, dormir=self.dormir)
        return self.t - 100.0


def test_variantes_y_medios():
    assert E.variantes(MAESTRA) == ["alta/video.m3u8", "https://otro.example/baja/video.m3u8?t=abc"]
    assert E.variantes(lista(3)) == []
    assert E.medios(lista(3)) == (3, 2.0, False)
    assert E.medios(lista(2, dura=6, cerrada=True)) == (2, 6.0, True)
    assert E.medios(lista(1, partes=4)) == (1, 2.0, False)              # los trozos de baja latencia no son segmentos
    sin_objetivo = "#EXTM3U\n#EXTINF:4.004,\na.ts\n#EXTINF:3.9,\nb.ts\n"
    assert E.medios(sin_objetivo) == (2, 4.004, False)                  # sin duración declarada: la del mayor
    assert E.medios("#EXTM3U\n#EXTINF:mal,\na.ts\n") == (1, E.SIN_DURACION, False)


def test_los_huecos_de_relleno_no_son_segmentos():
    """MediaMTX sirve una lista recién nacida con sus siete entradas, casi todas huecos (#EXT-X-GAP,
    gap.mp4): ffmpeg los pide, recibe un 404 y se los salta. Lo que da margen son los segmentos de
    verdad que hay al final, que es por donde se empieza a leer."""
    assert E.medios(lista(1, huecos=6)) == (1, 2.0, False)
    assert E.medios(lista(3, huecos=4)) == (3, 2.0, False)
    assert E.medios(lista(0, huecos=7)) == (0, 2.0, False)
    en_medio = lista(2) + "#EXT-X-GAP\n#EXTINF:2.00000,\ngap.mp4\n#EXTINF:2.00000,\nseg9.mp4\n"
    assert E.medios(en_medio) == (1, 2.0, False)                        # un hueco en medio: solo vale lo que le sigue

    def crece(o):                                       # como MediaMTX: cada 2 s un hueco menos y un segmento más
        hechos = min(7, 1 + int((o.t - 100.0) // 2))
        o.paginas["http://o/v.m3u8"] = lista(hechos, huecos=7 - hechos)
    o = Origen({"http://o/v.m3u8": lista(1, huecos=6)}, crece)
    assert 4.0 <= o.esperar("http://o/v.m3u8") <= 4.5 and o.dicho[0].endswith("tiene 1") and "ya tiene 3" in o.dicho[-1]


def test_una_lista_con_margen_o_cerrada_se_lee_sin_esperar_y_sin_decir_nada():
    for pagina in (lista(3), lista(7), lista(1, cerrada=True)):
        o = Origen({"http://o/v.m3u8": pagina})
        assert o.esperar("http://o/v.m3u8") == 0 and o.dicho == [] and len(o.pedidas) == 1


def test_una_lista_recien_nacida_se_espera_hasta_que_tiene_tres_segmentos():
    def crece(o):                                       # un segmento nuevo cada 2 s
        o.paginas["http://o/v.m3u8"] = lista(1 + int((o.t - 100.0) // 2))
    o = Origen({"http://o/v.m3u8": lista(1)}, crece)
    tardo = o.esperar("http://o/v.m3u8")
    assert 4.0 <= tardo <= 4.5                          # lo que tardan en salir los dos que faltaban
    assert o.dicho == ["Esperando a que la lista HLS del origen tenga 3 segmentos para leerla con margen: tiene 1",
                       "Esperando a que la lista HLS del origen tenga 3 segmentos para leerla con margen: tiene 2",
                       "La lista HLS del origen ya tiene 3 segmentos: el hub empieza a leerla"]
    assert all(x.startswith(E.ESPERA) for x in o.dicho[:2]) and not o.dicho[-1].startswith(E.ESPERA)


def test_de_una_lista_maestra_se_mira_la_variante_elegida():
    paginas = {"https://cdn.example/x/master.m3u8": MAESTRA, "https://cdn.example/x/alta/video.m3u8": lista(5),
               "https://otro.example/baja/video.m3u8?t=abc": lista(4)}
    o = Origen(dict(paginas))
    o.esperar("https://cdn.example/x/master.m3u8", variante=0)
    assert o.pedidas == ["https://cdn.example/x/master.m3u8", "https://cdn.example/x/alta/video.m3u8"]
    o = Origen(dict(paginas))
    o.esperar("https://cdn.example/x/master.m3u8", variante=1)
    assert o.pedidas[-1] == "https://otro.example/baja/video.m3u8?t=abc"
    with pytest.raises(IndexError):                     # una variante que la lista no tiene: lo dirá ffmpeg
        Origen(dict(paginas)).esperar("https://cdn.example/x/master.m3u8", variante=2)


def test_la_espera_tiene_tope():
    """Un origen que nunca llega a tres segmentos (una ventana de dos, o se quedó parado) no deja a la
    fuente esperando para siempre: pasado lo que deberían tardar los que faltan más un margen, se lee."""
    o = Origen({"http://o/v.m3u8": lista(2, dura=6)})
    tardo = o.esperar("http://o/v.m3u8")
    assert 6 + E.MARGEN <= tardo <= 6 + E.MARGEN + 1    # faltaba uno, de 6 s
    assert o.dicho[-1] == "La lista HLS del origen sigue con 2 segmentos tras 11 s de espera: el hub empieza a leerla sin el margen de 3"
    o = Origen({"http://o/v.m3u8": lista(0, dura=30)})
    assert E.TOPE <= o.esperar("http://o/v.m3u8") <= E.TOPE + 1         # segmentos enormes: nunca más del tope
    assert "sigue con 0 segmentos" in o.dicho[-1]


def test_lo_que_no_se_puede_leer_no_se_espera():
    with pytest.raises(OSError):
        Origen({"http://o/v.m3u8": OSError("Connection refused")}).esperar("http://o/v.m3u8")

    def se_cae(o):                                      # el origen deja de responder a media espera
        o.paginas["http://o/v.m3u8"] = OSError("HTTP Error 404")
    with pytest.raises(OSError):
        Origen({"http://o/v.m3u8": lista(1)}, se_cae).esperar("http://o/v.m3u8")
    with pytest.raises(ValueError):
        E.leer("srt://198.51.100.7:9000")
    with pytest.raises(ValueError):
        E.leer("/etc/hostname")


# ---------------------------------------------------------------- el programa entero, contra un servidor
class _Servidor(http.server.BaseHTTPRequestHandler):
    nace = 0.0                                          # cuándo «arrancó» el origen
    cada = 0.5                                          # cada cuánto saca un segmento

    def do_GET(self):
        if self.path == "/roto.m3u8":
            self.send_error(404)
            return
        if self.path == "/pagina.m3u8":
            cuerpo = b"<html>no es una lista</html>"
        else:
            cuerpo = lista(1 + int((time.time() - self.nace) / self.cada), dura=1).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


@pytest.fixture
def origen():
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Servidor)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def correr(url, variante="0"):
    """Lanza el programa como lo lanza el hub y devuelve (lo que escribió, el pid que tenía el
    programa, el pid del comando en que se convirtió, cuánto tardó)."""
    hijo = "import os, sys; print('arrancado', os.getpid(), *sys.argv[1:], file=sys.stderr)"
    t0 = time.time()
    p = subprocess.Popen([sys.executable, str(comandos.ESPERA_HLS), variante, "3", sys.executable, "-c", hijo, "-i", url],
                         stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                         env={"PATH": "/usr/bin:/bin", "no_proxy": "*"})
    _, escrito = p.communicate(timeout=30)
    lineas = escrito.strip().splitlines()
    assert p.returncode == 0 and lineas[-1].startswith("arrancado ")
    return lineas[:-1], p.pid, int(lineas[-1].split()[1]), time.time() - t0


def test_el_programa_espera_y_luego_es_el_ffmpeg(origen):
    _Servidor.nace = time.time()
    dicho, pid, pid_del_comando, tardo = correr(f"{origen}/vivo.m3u8")
    assert pid_del_comando == pid                       # no lanza otro proceso: se convierte en él
    assert dicho[0].startswith(E.ESPERA) and dicho[-1] == "La lista HLS del origen ya tiene 3 segmentos: el hub empieza a leerla"
    assert 0.8 <= tardo < 6                             # el tercer segmento sale al segundo de nacer la lista
    assert not any(origen in linea for linea in dicho)  # la dirección, que puede llevar una clave, no se escribe

    _Servidor.nace = time.time() - 60                   # un origen que lleva rato emitiendo: ni espera ni dice nada
    dicho, _, _, tardo = correr(f"{origen}/vivo.m3u8")
    assert dicho == [] and tardo < 3


def test_si_no_puede_mirar_la_lista_arranca_el_comando_sin_esperar(origen):
    for url in (f"{origen}/roto.m3u8", f"{origen}/pagina.m3u8", "http://127.0.0.1:9/nadie.m3u8", "srt://198.51.100.7:9000"):
        dicho, pid, pid_del_comando, tardo = correr(url)
        assert dicho == [] and pid_del_comando == pid and tardo < 3


def test_un_comando_que_no_existe_lo_dice_y_sale_con_127(origen):
    _Servidor.nace = time.time() - 60
    p = subprocess.run([sys.executable, str(comandos.ESPERA_HLS), "0", "3", "/no/existe/ffmpeg", "-i", f"{origen}/vivo.m3u8"],
                       stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30, env={"no_proxy": "*"})
    assert p.returncode == 127 and p.stderr.startswith("No se pudo lanzar: ")
    assert subprocess.run([sys.executable, str(comandos.ESPERA_HLS), "0"], stderr=subprocess.PIPE).returncode == 2


def test_el_programa_no_necesita_nada_del_hub():
    """Corre con el Python del sistema y sin la base del hub: solo la biblioteca estándar."""
    importados = [linea.split()[1] for linea in pathlib.Path(comandos.ESPERA_HLS).read_text().splitlines()
                  if linea.startswith(("import ", "from "))]
    assert set(importados) <= {"os", "sys", "time", "urllib.parse", "urllib.request"}


def test_los_tres_modulos_dicen_lo_mismo():
    assert estados.ESPERA_LISTA == E.ESPERA
    assert int(comandos.HLS_DESDE) == -3
