"""Media Syntaxis Streamer - medidores de audio por pista.

Mientras alguien mira una señal en Monitoreo, un ffmpeg la lee de MediaMTX, decodifica solo el audio
y mide cada pista con «astats»: pico y RMS de cada canal, diez veces por segundo. El panel pide los
últimos valores cuando los pinta. Sin nadie mirando no corre nada: el medidor se para a los PARAR
segundos de la última petición, y como mucho hay MAXIMO a la vez.

Los medidores son procesos del panel, no del supervisor: no llevan nada al aire, mueren con el
panel y pararlos no afecta a ninguna ruta.
"""

import ctypes
import os
import re
import selectors
import signal
import subprocess
import threading
import time

import db
import mtx

FFMPEG = db.config().get("ffmpeg", "ffmpeg")
MAXIMO = 4                      # medidores a la vez
PARAR = 8.0                     # segundos sin que nadie pida niveles
PISTAS_MAX = 8
SILENCIO = -120.0               # dBFS que se da por silencio (astats dice «-inf»)
VENTANA = 4800                  # muestras por medida: 0,1 s a 48 kHz
FILTRO = (f"asetnsamples=n={VENTANA}:p=0,astats=metadata=1:reset=1:measure_perchannel=Peak_level+RMS_level:"
          "measure_overall=none,ametadata=mode=print:file=/dev/fd/{fd}:direct=1")
LINEA = re.compile(r"lavfi\.astats\.(\d+)\.(Peak|RMS)_level=(\S+)")
_cerrojo = threading.Lock()
_medidores = {}                 # path -> Medidor


def comando(path, descriptores):
    """ffmpeg que mide las pistas de audio de `path`; cada una escribe sus medidas en su descriptor."""
    grafo = ";".join(f"[0:a:{i}]" + FILTRO.format(fd=fd) + f"[m{i}]" for i, fd in enumerate(descriptores))
    cmd = ["nice", "-n", "10", FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
           "-analyzeduration", "1000000", "-probesize", "1000000", "-vn", "-i", mtx.url_lectura(path) + "&latency=200000",
           "-filter_complex", grafo]
    for i in range(len(descriptores)):
        cmd += ["-map", f"[m{i}]"]
    return cmd + ["-f", "null", "-"]


def _nivel(texto):
    try:
        v = float(texto)
    except ValueError:
        return SILENCIO
    return max(SILENCIO, round(v, 1)) if v == v and v != float("inf") else SILENCIO     # nan e inf: silencio


def leer(texto, canales):
    """Aplica al diccionario {canal: {"pico", "rms"}} las medidas que traiga el texto. Devuelve si traía alguna."""
    hubo = False
    for canal, cual, valor in LINEA.findall(texto):
        canales.setdefault(int(canal), {})["pico" if cual == "Peak" else "rms"] = _nivel(valor)
        hubo = True
    return hubo


def _morir_con_el_panel():
    try:
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)      # PR_SET_PDEATHSIG
    except Exception:
        pass


class Medidor:
    def __init__(self, path, pistas):
        self.path, self.n = path, max(1, min(PISTAS_MAX, pistas))
        self.pedido = self.creado = time.time()
        self.niveles = [{} for _ in range(self.n)]      # por pista: {canal: {"pico", "rms"}}
        self.visto = [0.0] * self.n
        self.error = ""
        tuberias = [os.pipe() for _ in range(self.n)]
        self.lecturas = [r for r, _ in tuberias]
        escrituras = [w for _, w in tuberias]
        try:
            self.p = subprocess.Popen(comando(path, escrituras), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, pass_fds=escrituras, preexec_fn=_morir_con_el_panel)
        finally:
            for w in escrituras:
                os.close(w)
        self.hilo = threading.Thread(target=self._leer, daemon=True, name=f"medidor-{path}")
        self.hilo.start()

    def _leer(self):
        sel = selectors.DefaultSelector()
        restos = {}
        for i, fd in enumerate(self.lecturas):
            os.set_blocking(fd, False)
            sel.register(fd, selectors.EVENT_READ, i)
            restos[i] = ""
        abiertos = len(self.lecturas)
        try:
            while abiertos:
                for clave, _ in sel.select(timeout=1.0):
                    i = clave.data
                    try:
                        trozo = os.read(clave.fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        trozo = b""
                    if not trozo:
                        sel.unregister(clave.fd)
                        abiertos -= 1
                        continue
                    texto = restos[i] + trozo.decode(errors="replace")
                    completo, _, restos[i] = texto.rpartition("\n")
                    if leer(completo, self.niveles[i]):
                        self.visto[i] = time.time()
        finally:
            sel.close()
            for fd in self.lecturas:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                lineas = [x for x in self.p.stderr.read().decode(errors="replace").splitlines() if x.strip()]
                self.error = lineas[-1][:200] if lineas else ""
            except (OSError, ValueError):
                pass
            self.p.wait()

    def vivo(self):
        return self.p.poll() is None

    def parar(self):
        if self.vivo():
            try:
                self.p.terminate()
            except OSError:
                pass

    def estado(self):
        ahora = time.time()
        pistas = []
        for i in range(self.n):
            canales = [dict(self.niveles[i][c], canal=c) for c in sorted(self.niveles[i])]
            fresca = ahora - self.visto[i] < 1.5
            pistas.append({"pista": i + 1, "canales": canales if fresca else [], "t": self.visto[i] or None})
        midiendo = any(p["canales"] for p in pistas)
        return {"activo": self.vivo(), "arrancando": self.vivo() and not midiendo and ahora - self.creado < 12,
                "pistas": pistas, "error": "" if self.vivo() or midiendo else (self.error or "El medidor se detuvo")}


def limpiar(ahora=None):
    """Para los medidores que nadie pide y olvida los que ya terminaron."""
    ahora = ahora or time.time()
    with _cerrojo:
        for path, m in list(_medidores.items()):
            if ahora - m.pedido > PARAR:
                m.parar()
                del _medidores[path]
            elif not m.vivo() and ahora - m.creado > 3:
                del _medidores[path]            # se cayó (la señal se fue): el siguiente que pida lo relanza


def niveles(path, pistas=1):
    """Niveles de las pistas de audio de `path`. Arranca el medidor si hace falta; None si ya hay
    MAXIMO midiendo otras señales."""
    limpiar()
    with _cerrojo:
        m = _medidores.get(path)
        if m and m.n != max(1, min(PISTAS_MAX, pistas)):        # la señal cambió de pistas: otro medidor
            m.parar()
            m = None
        if m is None:
            if len(_medidores) - (path in _medidores) >= MAXIMO:
                return None
            m = _medidores[path] = Medidor(path, pistas)
        m.pedido = time.time()
        return m.estado()


def parar_todos():
    with _cerrojo:
        for m in _medidores.values():
            m.parar()
        _medidores.clear()


def _vigilar():
    while True:
        time.sleep(2.0)
        try:
            limpiar()
        except Exception:
            pass


threading.Thread(target=_vigilar, daemon=True, name="medidores").start()
