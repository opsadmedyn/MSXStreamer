#!/usr/bin/env python3
"""Media Syntaxis Streamer - prueba de aceptación del hub de señales.

Hace, por la misma API que usa el panel y con una sesión de administrador, la prueba principal de
aceptación, dos veces:

  rtmp   entra una fuente RTMP y se activan a la vez tres salidas: SRT hacia un receptor, HLS hacia
         un reproductor y WebRTC hacia un navegador;
  srt    lo mismo con una entrada SRT y, en lugar de la salida SRT, una salida RTMP.

En cada una pide el plan antes de activar (qué se copia, qué se convierte y por qué), comprueba
audio y vídeo en las tres salidas, corta sin avisar el receptor durante 60 s, comprueba que HLS y
WebRTC siguen reproduciendo y mide cuánto tarda la salida cortada en recuperarse cuando el receptor
vuelve. Mientras tanto apunta los estados que enseña el hub y la carga del equipo, y al final busca
en los registros las claves que usó. Con --reinicio, además, reinicia el hub con las rutas activas
y comprueba que vuelven solas y sin procesos duplicados.

La cámara y los receptores son programas (ffmpeg y un Chromium sin ventana): lo que pasa la prueba
queda anotado en el hub como «verificado en el banco de pruebas», nunca como verificado con equipos
reales. Eso último se anota a mano tras probar con la cámara y el receptor de verdad (docs/validacion.md).

Uso, en la misma máquina que el hub:

    MSXS_SESION=<cookie msr_sesion de un administrador> python3 scripts/aceptacion.py \\
        --lugar "HP Z8 (EZC80)" --registros /home/mediasat/streamer/logs --journal

Desde otra máquina de la red (la carga que mide sigue siendo la del hub):

    python3 scripts/aceptacion.py --hub http://ezc80:8095 --receptor <IP de esta máquina vista desde el hub>

Necesita ffmpeg y ffprobe con libsrt y, para WebRTC y para ver el HLS en un navegador, Node con
Playwright (playwright o playwright-core, con su Chromium; en una estación vale el del compositor:
ver docs/banco-de-pruebas.md). Sin navegador (--sin-navegador) WebRTC queda sin comprobar y la
prueba lo dice. No usa nada fuera de la biblioteca estándar.

Sale con 0 si todo pasa, 1 si algo falla y 3 si algo quedó sin comprobar. Deja en la carpeta de
resultados informe.md (para leer) y datos.json (todas las medidas).
"""

import argparse
import datetime
import http.client
import json
import os
import pathlib
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PREFIJO = "Aceptación · "           # lo que crea la prueba en el hub se llama así, y solo eso borra
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
NOMBRES = {"srt": "SRT", "rtmp": "RTMP", "hls": "HLS", "webrtc": "WebRTC"}
SENAL = {"ancho": 1280, "alto": 720, "fps": 30, "kbps": 2500, "audio_hz": 48000, "tono_hz": 1000}

# Lo que se exige. Los márgenes absorben el arranque de cada reproductor, no una señal a trompicones.
FOTOGRAMAS_MIN = 0.85               # de los fotogramas que tocan por duración
VOLUMEN_MIN_DB = -50.0              # por debajo, el audio se da por silencio
HLS_AVANCE_MIN = 0.90               # segundos reproducidos por segundo de reloj
HLS_PARADA_MAX = 4.0                # s seguidos sin que avance el reproductor
WEBRTC_AVANCE_MIN = 0.90            # de los fotogramas (y muestras de audio) que tocan
WEBRTC_PARADA_MAX = 2.0
DETECTA_MAX = 20.0                  # s para que el hub deje de decir «enviando» tras el corte
RECUPERA_MAX = 40.0                 # s para que la salida vuelva tras regresar el receptor
SIN_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def hora(t):
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "—"


def decir(texto):
    print(f"[{hora(time.time())}] {texto}", flush=True)


def veces(n):
    return "1 vez" if n == 1 else f"{n} veces"


def esperar(condicion, segundos, cada=0.5):
    """Lo que devuelva `condicion` en cuanto sea cierto, o None si pasan los segundos."""
    limite = time.time() + segundos
    while True:
        r = condicion()
        if r or time.time() >= limite:
            return r or None
        time.sleep(cada)


# ---------------------------------------------------------------- el hub, por su API
class ErrorHub(Exception):
    def __init__(self, codigo, detalle):
        super().__init__(f"{codigo}: {detalle}" if codigo else str(detalle))
        self.codigo, self.detalle = codigo, detalle


class Hub:
    def __init__(self, url, sesion):
        self.url, self.sesion = url.rstrip("/"), sesion

    def pedir(self, metodo, ruta, cuerpo=None, espera=15, crudo=False):
        datos = None if cuerpo is None else json.dumps(cuerpo).encode()
        peticion = urllib.request.Request(self.url + ruta, data=datos, method=metodo, headers={
            "Cookie": f"msr_sesion={self.sesion}", "Content-Type": "application/json"})
        try:
            with SIN_PROXY.open(peticion, timeout=espera) as r:
                texto = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            texto = e.read().decode("utf-8", "replace")
            try:
                detalle = json.loads(texto).get("detail")
            except (ValueError, AttributeError):
                detalle = texto[:300]
            raise ErrorHub(e.code, detalle)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise ErrorHub(0, f"el hub no responde ({e})")
        return texto if crudo else (json.loads(texto) if texto else None)

    def responde(self):
        try:
            self.pedir("GET", "/api/version", espera=4)
            return True
        except ErrorHub:
            return False

    def destino(self, did):
        return next((d for d in self.pedir("GET", "/api/destinos") if d["id"] == did), None)

    def fuente(self, fid):
        return next((f for f in self.pedir("GET", "/api/fuentes") if f["id"] == fid), None)


def secretos_de(direccion):
    """Las claves que lleva una dirección entregada por el hub, para buscarlas luego en los registros."""
    s = set()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(direccion.get("url") or "").query))
    s.update(q.get(k, "") for k in ("pass", "passphrase"))
    if q.get("streamid", "").count(":") >= 3:
        s.add(q["streamid"].rsplit(":", 1)[1])
    for c in direccion.get("campos") or []:
        if c.get("secreto") and c["nombre"].startswith("Token"):
            s.add(c["valor"].split(":", 1)[-1])
        elif c.get("secreto") and c["nombre"] in ("Contraseña", "Passphrase"):
            s.add(c["valor"])
    return {x for x in s if x and x != "***"}


# ---------------------------------------------------------------- programas que lanza la prueba
class Proceso:
    """Un programa de la prueba, en su grupo de procesos. Lo que escribe va a su registro sin las claves."""

    def __init__(self, nombre, orden, carpeta, ocultar=(), entrada=subprocess.DEVNULL, salida=None):
        self.nombre, self.orden, self.carpeta = nombre, [str(x) for x in orden], pathlib.Path(carpeta)
        self.ocultar, self.entrada, self.salida, self.p = [x for x in ocultar if x], entrada, salida, None

    def lanzar(self):
        entorno = dict(os.environ)
        entorno["no_proxy"] = entorno["NO_PROXY"] = ",".join(filter(None, [entorno.get("no_proxy"), "127.0.0.1,localhost"]))
        self.p = subprocess.Popen(self.orden, stdin=self.entrada, stdout=self.salida or subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, start_new_session=True, env=entorno)
        threading.Thread(target=self._registrar, args=(self.p,), daemon=True).start()
        return self

    def _registrar(self, p):
        with open(self.carpeta / f"{self.nombre}.log", "a", encoding="utf-8") as registro:
            for linea in p.stderr:
                texto = linea.decode("utf-8", "replace")
                for s in self.ocultar:
                    texto = texto.replace(s, "***")
                registro.write(texto)
                registro.flush()

    def vivo(self):
        return self.p is not None and self.p.poll() is None

    def parar(self, brusco=False, espera=6):
        if self.p is None:
            return
        for senal in ([signal.SIGKILL] if brusco else [signal.SIGTERM, signal.SIGKILL]):
            try:
                os.killpg(self.p.pid, senal)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self.p.wait(espera)
                return
            except subprocess.TimeoutExpired:
                continue


class Emisor(threading.Thread):
    """La cámara de pruebas: publica la señal y, si se le corta, vuelve a publicar como un codificador de verdad."""

    def __init__(self, protocolo, url, carpeta, ocultar, con_b=False):
        super().__init__(daemon=True)
        self.protocolo, self.url, self.carpeta, self.ocultar, self.con_b = protocolo, url, carpeta, ocultar, con_b
        self.fin, self.p, self.lanzamientos = threading.Event(), None, 0

    def orden(self):
        video = ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p",
                 "-b:v", f"{SENAL['kbps']}k", "-maxrate", f"{SENAL['kbps']}k", "-bufsize", f"{SENAL['kbps'] * 2}k",
                 "-g", str(SENAL["fps"] * 2)]
        video += ["-bf", "2"] if self.con_b else ["-tune", "zerolatency", "-bf", "0"]
        orden = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-re",
                 "-f", "lavfi", "-i", f"testsrc2=size={SENAL['ancho']}x{SENAL['alto']}:rate={SENAL['fps']}",
                 "-f", "lavfi", "-i", f"sine=frequency={SENAL['tono_hz']}:sample_rate={SENAL['audio_hz']}",
                 *video, "-c:a", "aac", "-b:a", "128k", "-ar", str(SENAL["audio_hz"]), "-ac", "2"]
        if self.protocolo == "rtmp":
            return orden + ["-f", "flv", self.url]
        return orden + ["-f", "mpegts", self.url + ("&" if "?" in self.url else "?") + "pkt_size=1316"]

    def run(self):
        while not self.fin.is_set():
            self.p = Proceso("camara", self.orden(), self.carpeta, self.ocultar).lanzar()
            self.lanzamientos += 1
            while self.p.vivo() and not self.fin.is_set():
                time.sleep(0.2)
            if not self.fin.is_set():
                self.fin.wait(1.5)
        self.p.parar()

    def parar(self):
        self.fin.set()
        self.join(12)


class Receptor:
    """El receptor de pruebas de la salida que el hub empuja: un ffmpeg a la escucha que guarda lo que llega.
    Cada vez que se abre empieza una captura nueva."""

    def __init__(self, protocolo, puerto, secreto, carpeta):
        self.protocolo, self.puerto, self.secreto, self.carpeta = protocolo, puerto, secreto, pathlib.Path(carpeta)
        self.n, self.p, self.archivo, self.capturas = 0, None, None, []
        self.muestras, self.fin = [], threading.Event()     # [(instante, bytes recibidos entre todas las capturas)]
        threading.Thread(target=self._apuntar, daemon=True).start()

    def _apuntar(self):
        while not self.fin.wait(0.5):
            total = 0
            for archivo in list(self.capturas):
                try:
                    total += archivo.stat().st_size
                except OSError:
                    pass
            self.muestras.append((time.time(), total))

    def abrir(self):
        self.n += 1
        self.archivo = self.carpeta / f"receptor_{self.protocolo}_{self.n}.ts"
        if self.protocolo == "srt":
            entrada = ["-passphrase", self.secreto, "-i", f"srt://0.0.0.0:{self.puerto}?mode=listener&latency=300000"]
        else:
            entrada = ["-listen", "1", "-i", f"rtmp://0.0.0.0:{self.puerto}/live/{self.secreto}"]
        orden = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", *entrada, "-map", "0", "-c", "copy",
                 "-f", "mpegts", "-flush_packets", "1", "-y", self.archivo]
        self.p = Proceso(f"receptor_{self.protocolo}", orden, self.carpeta, [self.secreto]).lanzar()
        self.capturas.append(self.archivo)
        return time.time()

    def bytes(self):
        try:
            return self.archivo.stat().st_size
        except (OSError, AttributeError):
            return 0

    def vivo(self):
        return self.p is not None and self.p.vivo()

    def cortar(self):
        """Como un receptor que se apaga o se queda sin red: desaparece sin despedirse."""
        self.p.parar(brusco=True)
        return time.time()

    def cerrar(self):
        self.fin.set()
        if self.p:
            self.p.parar()


def puerto_libre(puerto, udp):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
    try:
        if not udp:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)     # un puerto recién cerrado no está ocupado
        s.bind(("0.0.0.0", puerto))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ---------------------------------------------------------------- qué trae una señal
def decodificar(entrada, segundos=None, espera=180):
    """Decodifica un archivo o una dirección y dice qué trae de verdad: códecs, fotogramas, si la
    imagen se queda congelada y el volumen del audio."""
    r = {"video": None, "audio": None, "error": ""}
    try:
        sonda = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(entrada)],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=40)
        pistas = json.loads(sonda.stdout or b"{}").get("streams") or []
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return dict(r, error=f"ffprobe: {e}"[:200])
    for p in pistas:
        if p.get("codec_type") == "video" and not r["video"]:
            r["video"] = {"codec": p.get("codec_name"), "ancho": p.get("width"), "alto": p.get("height")}
        elif p.get("codec_type") == "audio" and not r["audio"]:
            r["audio"] = {"codec": p.get("codec_name"), "canales": p.get("channels"), "hz": int(p.get("sample_rate") or 0)}
    if not pistas:
        return dict(r, error=(sonda.stderr.decode("utf-8", "replace").strip() or "no trae pistas")[:200])
    orden = [FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info", "-progress", "pipe:1", "-i", str(entrada)]
    orden += ["-t", str(segundos)] if segundos else []
    if r["video"]:
        orden += ["-map", "0:v:0", "-vf", "freezedetect=n=-60dB:d=1.5"]
    if r["audio"]:
        orden += ["-map", "0:a:0", "-af", "volumedetect"]
    try:
        d = subprocess.run(orden + ["-f", "null", "-"], stdin=subprocess.DEVNULL, capture_output=True, timeout=espera)
    except (OSError, subprocess.TimeoutExpired) as e:
        return dict(r, error=f"ffmpeg: {e}"[:200])
    avance, errores = d.stdout.decode("utf-8", "replace"), d.stderr.decode("utf-8", "replace")
    fotogramas = re.findall(r"^frame=\s*(\d+)", avance, re.M)
    tiempo = re.findall(r"^out_time_us=(\d+)", avance, re.M)
    r["segundos"] = round(int(tiempo[-1]) / 1e6, 2) if tiempo else 0.0
    if r["video"]:
        r["video"].update(fotogramas=int(fotogramas[-1]) if fotogramas else 0,
                          congelado=len(re.findall(r"freezedetect\.freeze_start", errores)))
    if r["audio"]:
        medio, maximo = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)", errores), re.search(r"max_volume:\s*(-?[\d.]+|-inf)", errores)
        r["audio"].update(volumen_medio_db=float(medio.group(1)) if medio else None,
                          volumen_max_db=float(maximo.group(1)) if maximo else None)
    return r


def juzgar_senal(d, fps):
    """(¿vídeo bien?, ¿audio bien?, texto) de lo que devolvió decodificar()."""
    v, a = d.get("video"), d.get("audio")
    tocan = max(1.0, d.get("segundos", 0) * fps)
    video = bool(v and v.get("fotogramas", 0) >= FOTOGRAMAS_MIN * tocan and d.get("segundos", 0) >= 3 and not v.get("congelado"))
    audio = bool(a and a.get("volumen_medio_db") is not None and a["volumen_medio_db"] > VOLUMEN_MIN_DB)
    partes = []
    if v:
        partes.append(f"vídeo {str(v.get('codec')).upper()} {v.get('ancho')}×{v.get('alto')}, {v.get('fotogramas', 0)} fotogramas "
                      f"en {d.get('segundos', 0):.1f} s" + (", imagen congelada" if v.get("congelado") else ""))
    else:
        partes.append("sin vídeo")
    if a:
        partes.append(f"audio {str(a.get('codec')).upper()} {a.get('canales')} canales {a.get('hz')} Hz, "
                      f"volumen medio {a.get('volumen_medio_db')} dB")
    else:
        partes.append("sin audio")
    return video, audio, "; ".join(partes) + (f" ({d['error']})" if d.get("error") else "")


# ---------------------------------------------------------------- lo que se va apuntando mientras tanto
def campo(m, *claves):
    for k in claves:
        if not isinstance(m, dict):
            return None
        m = m.get(k)
    return m


def tramo(serie, t0, t1):
    return [(t, v) for t, v in serie if t0 <= t <= t1 and v is not None]


def continuidad(serie, minimo=0.0):
    """De [(instante, contador)]: cuánto avanzó, en cuánto tiempo y la parada más larga (el tiempo
    seguido en que no avanzó más de `minimo` entre dos lecturas)."""
    r = {"avance": 0.0, "segundos": 0.0, "parada_max_s": None, "muestras": len(serie)}
    if len(serie) < 2:
        return r
    mayor, desde = 0.0, None
    for (ta, va), (tb, vb) in zip(serie, serie[1:]):
        if vb - va > minimo:
            desde = None
        else:
            desde = ta if desde is None else desde
            mayor = max(mayor, tb - desde)
    r.update(avance=round(serie[-1][1] - serie[0][1], 3), segundos=round(serie[-1][0] - serie[0][0], 2), parada_max_s=round(mayor, 1))
    return r


def avanza(serie, segundos, minimo):
    """¿El contador de [(instante, contador)] ha avanzado al menos `minimo` en los últimos segundos?"""
    ahora = time.time()
    t = tramo(serie, ahora - segundos, ahora)
    return len(t) >= 2 and t[-1][1] - t[0][1] >= minimo


class Navegador:
    """Un Chromium sin ventana con las páginas HLS y WebRTC que entrega el hub (ver aceptacion_navegador.js)."""

    def __init__(self, node, paginas, carpeta, ocultar):
        self.node, self.paginas, self.carpeta, self.ocultar = node, paginas, pathlib.Path(carpeta), ocultar
        self.muestras, self.p, self.fatal = [], None, ""

    def lanzar(self):
        config = self.carpeta / "navegador.json"            # lleva la clave de lectura: solo para quien corre la prueba
        config.touch(mode=0o600)
        config.write_text(json.dumps({"paginas": self.paginas}))
        guion = pathlib.Path(__file__).with_name("aceptacion_navegador.js")
        self.p = Proceso("navegador", [self.node, guion, config], self.carpeta, self.ocultar,
                         entrada=subprocess.PIPE, salida=subprocess.PIPE).lanzar()
        threading.Thread(target=self._leer, daemon=True).start()
        return self

    def _leer(self):
        with open(self.carpeta / "navegador.jsonl", "a", encoding="utf-8") as copia:     # cada lectura, por si hay que mirarla
            for linea in self.p.p.stdout:
                try:
                    m = json.loads(linea)
                except ValueError:
                    continue
                copia.write(linea.decode("utf-8", "replace"))
                copia.flush()
                if m.get("fatal"):
                    self.fatal = m["fatal"]
                self.muestras.append(m)

    def serie(self, pagina, *claves):
        return [(m["t"], campo(m.get(pagina), *claves)) for m in list(self.muestras) if "t" in m]

    def ultima(self, pagina):
        return next((m[pagina] for m in reversed(list(self.muestras)) if pagina in m), None) or {}

    def recargar(self):
        try:
            self.p.p.stdin.write(b"recargar\n")
            self.p.p.stdin.flush()
        except (OSError, ValueError):
            pass

    def vivo(self):
        return self.p is not None and self.p.vivo()

    def parar(self):
        if self.p is None:
            return
        try:
            self.p.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.p.wait(8)
        except subprocess.TimeoutExpired:
            self.p.parar()
        (self.carpeta / "navegador.json").unlink(missing_ok=True)


class VigiaHLS(threading.Thread):
    """Pide la lista HLS cada segundo, como un reproductor, y apunta cuándo aparece cada segmento nuevo."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url, self.fin, self.muestras, self.errores, self.lista = url, threading.Event(), [], [], None

    def _leer(self, url):
        with SIN_PROXY.open(url, timeout=10) as r:          # MediaMTX redirige una vez para fijar la sesión
            return r.geturl(), r.read().decode("utf-8", "replace")

    def run(self):
        while not self.fin.is_set():
            try:
                if not self.lista:
                    final, texto = self._leer(self.url)
                    variantes = [x for x in texto.splitlines() if x and not x.startswith("#") and ".m3u8" in x]
                    self.lista = urllib.parse.urljoin(final, variantes[0]) if variantes else final
                _, texto = self._leer(self.lista)
                primero = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", texto)
                self.muestras.append((time.time(), int(primero.group(1) if primero else 0) + len(re.findall(r"^#EXTINF:", texto, re.M))))
            except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
                self.errores.append((time.time(), str(e)[:160]))
                self.lista = None
            self.fin.wait(1.0)

    def parar(self):
        self.fin.set()
        self.join(12)


class Vigia(threading.Thread):
    """Apunta cada segundo lo que el hub enseña de cada ruta y, cada dos, la carga del equipo. De
    paso mira que en lo que la API devuelve no viaje ninguna de las claves de la prueba."""

    def __init__(self, hub):
        super().__init__(daemon=True)
        self.hub, self.fin = hub, threading.Event()
        self.estados, self.cargas, self.fallos, self.fugas, self.secretos = [], [], [], set(), set()
        self.equipo = None

    def _mirar(self, ruta, texto):
        for s in list(self.secretos):
            if s in texto:
                self.fugas.add(ruta)

    def run(self):
        n = 0
        while not self.fin.is_set():
            t = time.time()
            try:
                texto = self.hub.pedir("GET", "/api/matriz", crudo=True)
                self._mirar("/api/matriz", texto)
                m = json.loads(texto)
                foto = {"_sistema": m.get("sistema")}
                for f in m.get("fuentes") or []:
                    foto[f["id"]] = {"estado": f["estado"]["estado"], "kbps": f.get("kbps")}
                for d in m.get("destinos") or []:
                    if d.get("ruta"):
                        e, metricas = d["ruta"]["estado"], d["ruta"]["metricas"]
                        foto[d["id"]] = {"estado": e["estado"], "detalle": e["detalle"], "kbps": metricas.get("kbps"),
                                         "evidencia": [x["que"] for x in e.get("evidencia") or []],
                                         "receptores": len(metricas.get("receptores") or []),
                                         "reconexiones": metricas.get("reconexiones")}
                self.estados.append((t, foto))
                if n % 2 == 0:
                    texto = self.hub.pedir("GET", "/api/carga", crudo=True)
                    self._mirar("/api/carga", texto)
                    carga = json.loads(texto)
                    self.equipo = carga.get("equipo") or self.equipo
                    self.cargas.append((t, carga))
            except (ErrorHub, ValueError, KeyError, TypeError) as e:
                self.fallos.append((t, str(e)[:160]))
            n += 1
            self.fin.wait(max(0.1, 1.0 - (time.time() - t)))

    def estado(self, i):
        for t, foto in reversed(self.estados[-3:]):
            if i in foto:
                return foto[i]
        return {}

    def linea(self, i, t0, t1):
        """[(instante, estado)] cada vez que cambia el estado de una ruta entre t0 y t1."""
        r = []
        for t, foto in list(self.estados):
            if t0 <= t <= t1 and i in foto and (not r or r[-1][1] != foto[i]["estado"]):
                r.append((t, foto[i]["estado"]))
        return r

    def parar(self):
        self.fin.set()
        self.join(8)


def resumen_carga(cargas, t0, t1):
    """Media y máximo de la carga del equipo y de cada proceso del hub entre dos instantes."""
    dentro = [c for t, c in cargas if t0 <= t <= t1]
    if not dentro:
        return None

    def de(valores):
        valores = [v for v in valores if v is not None]
        return {"media": round(sum(valores) / len(valores), 1), "max": round(max(valores), 1)} if valores else None
    tareas = {}
    for c in dentro:
        for nombre, m in (c.get("tareas") or {}).items():
            tareas.setdefault(nombre, {"texto": m.get("texto", nombre), "tipo": m.get("tipo"), "cpu": [], "mem": []})
            tareas[nombre]["cpu"].append(m.get("cpu_pct"))
            tareas[nombre]["mem"].append(m.get("memoria_mb"))
    gpus = [g for c in dentro for g in (c.get("gpu") or [])[:1]]
    ultimo = dentro[-1]
    return {"muestras": len(dentro), "segundos": round(t1 - t0, 1), "nucleos": ultimo.get("nucleos"),
            "cpu_pct": de([c.get("cpu_pct") for c in dentro]), "carga_1min": de([(c.get("carga") or [None])[0] for c in dentro]),
            "memoria": ultimo.get("memoria"),
            "gpu": {"nombre": gpus[-1].get("nombre"), "uso_pct": de([g.get("uso_pct") for g in gpus]),
                    "codificador_pct": de([g.get("codificador_pct") for g in gpus])} if gpus else None,
            "tareas": {n: {"texto": t["texto"], "tipo": t["tipo"], "cpu_pct": de(t["cpu"]), "memoria_mb": de(t["mem"])}
                       for n, t in sorted(tareas.items())}}


def ffmpeg_de_rutas(base):
    """{tarea: [pid]} de los ffmpeg de las rutas del hub, si corren en esta máquina: cada uno lleva
    <base>/run/<tarea>.prog en su línea de órdenes."""
    patron, r = re.compile(re.escape(str(base).rstrip("/")) + r"/run/([A-Za-z0-9_.-]+)\.prog"), {}
    for d in pathlib.Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if not (d / "comm").read_text().startswith("ffmpeg"):
                continue
            tarea = patron.search((d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace"))
        except OSError:
            continue
        if tarea:
            r.setdefault(tarea.group(1), []).append(int(d.name))
    return r


# ---------------------------------------------------------------- una prueba: una entrada y sus tres salidas
class Caso:
    def __init__(self, ctx, entrada, empuje):
        self.ctx, self.hub, self.vigia, self.entrada, self.empuje = ctx, ctx.hub, ctx.vigia, entrada, empuje
        self.carpeta = ctx.carpeta / f"entrada-{entrada}"
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.titulo = f"Entrada {NOMBRES[entrada]} con salidas {NOMBRES[empuje]}, HLS y WebRTC"
        self.r = {"entrada": entrada, "empuje": empuje, "titulo": self.titulo, "comprobaciones": [], "rutas": {},
                  "planes": {}, "tiempos": {}, "corte": {}, "reinicios": []}
        self.fid = self.d_emp = self.d_hls = self.d_web = None
        self.emisor = self.receptor = self.navegador = self.vigia_hls = None
        self.secretos, self.fps = set(), SENAL["fps"]

    # -- apuntar resultados
    def comprobar(self, ruta, que, ok, detalle=""):
        """ok: True, False o None (no se pudo comprobar). ruta: «empuje», «hls», «webrtc» o «» (de toda la prueba)."""
        self.r["comprobaciones"].append({"ruta": ruta, "que": que, "ok": ok, "detalle": detalle})
        marca = {True: "bien", False: "FALLA", None: "sin comprobar"}[ok]
        decir(f"   {marca}: {que}" + (f" ({detalle})" if detalle else ""))
        return ok

    def de_la_ruta(self, ruta):
        return [c for c in self.r["comprobaciones"] if c["ruta"] == ruta]

    def con_host(self, url):
        """La dirección que da el hub, con el nombre del hub visto desde aquí si la prueba corre en otra máquina."""
        p = urllib.parse.urlsplit(url)
        if self.ctx.host_hub and p.hostname in ("127.0.0.1", "localhost"):
            return urllib.parse.urlunsplit(p._replace(netloc=p.netloc.replace(p.hostname, self.ctx.host_hub, 1)))
        return url

    # -- montar
    def montar(self):
        hub, ctx = self.hub, self.ctx
        decir(f"== {self.titulo}")
        self.fid = hub.pedir("POST", "/api/fuentes", {"nombre": f"{PREFIJO}fuente {NOMBRES[self.entrada]}",
                                                        "protocolo": self.entrada, "inicia": "origen"})["id"]
        publicar = hub.pedir("GET", f"/api/fuentes/{self.fid}/claves")
        secreto = secrets.token_hex(12)
        if self.empuje == "srt":
            puerto = ctx.args.puerto_srt
            destino = {"nombre": f"{PREFIJO}receptor SRT", "protocolo": "srt", "inicia": "hub", "latencia_ms": 300,
                       "url": f"srt://{ctx.args.receptor}:{puerto}", "passphrase": secreto}
        else:
            puerto = ctx.args.puerto_rtmp
            destino = {"nombre": f"{PREFIJO}servidor RTMP", "protocolo": "rtmp", "inicia": "hub",
                       "url": f"rtmp://{ctx.args.receptor}:{puerto}/live", "clave": secreto}
        destino["reconexion"] = {"cada_s": ctx.args.reintento, "max_intentos": 0}
        self.d_emp = hub.pedir("POST", "/api/destinos", destino)["id"]
        self.d_hls = hub.pedir("POST", "/api/destinos", {"nombre": f"{PREFIJO}reproductor HLS", "protocolo": "hls",
                                                           "inicia": "receptor"})["id"]
        self.d_web = hub.pedir("POST", "/api/destinos", {"nombre": f"{PREFIJO}navegador WebRTC", "protocolo": "webrtc",
                                                           "inicia": "receptor"})["id"]
        self.r["rutas"] = {"empuje": {"destino": self.d_emp, "protocolo": self.empuje, "nombre": destino["nombre"]},
                           "hls": {"destino": self.d_hls, "protocolo": "hls", "nombre": f"{PREFIJO}reproductor HLS"},
                           "webrtc": {"destino": self.d_web, "protocolo": "webrtc", "nombre": f"{PREFIJO}navegador WebRTC"}}
        self.dir_hls = hub.pedir("GET", f"/api/destinos/{self.d_hls}/claves")
        self.dir_web = hub.pedir("GET", f"/api/destinos/{self.d_web}/claves")
        self.secretos = {secreto} | secretos_de(publicar) | secretos_de(self.dir_hls) | secretos_de(self.dir_web)
        self.vigia.secretos |= self.secretos
        ctx.secretos |= self.secretos

        self.emisor = Emisor(self.entrada, self.con_host(publicar["url"]), self.carpeta, self.secretos, ctx.args.fotogramas_b)
        self.emisor.start()
        decir(f"   la cámara de pruebas publica por {NOMBRES[self.entrada]}; esperando a que el hub la reciba y la inspeccione")

        def lista():
            f = hub.fuente(self.fid)
            return f if f and f["estado"]["estado"] == "recibiendo" and f.get("pistas") and not f["pistas"]["parcial"] else None
        f = esperar(lista, 75)
        if not self.comprobar("", "El hub recibe la fuente y sabe qué pistas trae", bool(f),
                              "; ".join(filter(None, [campo(f, "pistas", "video", "texto"),
                                                      *[a["texto"] for a in campo(f, "pistas", "audio") or []]])) if f
                              else str(campo(hub.fuente(self.fid), "estado", "detalle"))):
            return False
        self.fps = campo(f, "pistas", "video", "fps") or SENAL["fps"]
        self.publicaciones = self.emisor.lanzamientos       # desde aquí, la cámara no debería volver a publicar
        self.r["fuente"] = {"id": self.fid, "video": campo(f, "pistas", "video", "texto"),
                            "audio": [a["texto"] for a in campo(f, "pistas", "audio") or []],
                            "quien_inicia": f.get("quien_inicia"), "sentido": f.get("sentido")}
        self.receptor = Receptor(self.empuje, puerto, secreto, self.carpeta)
        self.receptor.abrir()
        time.sleep(0.5)
        return True

    def planes(self):
        """Antes de activar: qué dice el hub que copiará y que convertirá en cada ruta, y por qué."""
        completos = True
        for ruta, datos in self.r["rutas"].items():
            p = self.hub.pedir("POST", "/api/rutas/plan", {"fuente": self.fid, "destino": datos["destino"]})
            plan = p["plan"]
            self.r["planes"][ruta] = {"resumen": plan.get("resumen"), "carga": plan.get("carga"), "video": plan.get("video"),
                                      "audio": plan.get("audio"), "avisos": plan.get("avisos"), "permiso": p.get("permiso"),
                                      "validacion": campo(p, "validacion", "nombre")}
            piezas = [plan.get("video") or {}] + list(plan.get("audio") or [])
            completos = completos and bool(plan.get("posible")) and not plan.get("provisional") and len(piezas) >= 2 \
                and all(x.get("accion") and x.get("motivo") for x in piezas)
        self.comprobar("", "Antes de activar, el hub dice qué copia y qué convierte en cada ruta, y por qué", completos,
                       " · ".join(f"{NOMBRES[d['protocolo']]}: {self.r['planes'][k]['resumen']}" for k, d in self.r["rutas"].items()))
        return completos

    def activar(self):
        """Las tres rutas, pedidas a la vez."""
        respuestas, t0 = {}, time.time()

        def una(ruta, did):
            try:
                self.hub.pedir("PUT", f"/api/rutas/{did}", {"fuente": self.fid})
                respuestas[ruta] = time.time() - t0
            except ErrorHub as e:
                respuestas[ruta] = e
        hilos = [threading.Thread(target=una, args=(k, d["destino"])) for k, d in self.r["rutas"].items()]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.r["tiempos"]["activacion"] = t0
        fallos = {k: str(v) for k, v in respuestas.items() if isinstance(v, ErrorHub)}
        return self.comprobar("", "Las tres rutas se activan a la vez desde la API del panel", not fallos,
                              str(fallos) if fallos else f"las tres peticiones, en {max(respuestas.values()):.2f} s")

    def abrir_reproductores(self):
        """Abre el reproductor HLS y el navegador WebRTC cuando el hub dice que sus direcciones están
        listas («esperando receptor»), que es cuando las abriría quien mira el panel."""
        t0 = self.r["tiempos"]["activacion"]
        listas = esperar(lambda: all(self.vigia.estado(self.r["rutas"][k]["destino"]).get("estado") in ("esperando_receptor", "enviando")
                                     for k in ("hls", "webrtc")), 40, cada=0.25)
        self.r["tiempos"]["direcciones_listas_s"] = round(time.time() - t0, 1)
        self.comprobar("", "El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor»", bool(listas),
                       f"a los {self.r['tiempos']['direcciones_listas_s']} s de activar" if listas else
                       "; ".join(self._diagnostico(k) for k in ("hls", "webrtc")))
        self.vigia_hls = VigiaHLS(self.con_host(self.dir_hls["url"]))
        self.vigia_hls.start()
        if not self.ctx.node:
            return
        credencial = lambda d, nombre: next((c["valor"] for c in d.get("campos") or [] if c["nombre"].startswith(nombre)), "")
        ficha = credencial(self.dir_web, "Token").split(":", 1)
        paginas = [{"nombre": "hls", "url": self.con_host(self.dir_hls["url"]).rsplit("/", 1)[0] + "/",
                    "usuario": credencial(self.dir_hls, "Usuario"), "clave": credencial(self.dir_hls, "Contraseña")},
                   {"nombre": "webrtc", "url": self.con_host(self.dir_web["pagina"]),
                    "usuario": ficha[0] if len(ficha) == 2 else "", "clave": ficha[1] if len(ficha) == 2 else ""}]
        self.navegador = Navegador(self.ctx.node, paginas, self.carpeta, self.secretos).lanzar()

    # -- lo que se ve en cada salida
    def _hls_reproduce(self):
        u = self.navegador.ultima("hls").get("video") or {}
        return (u.get("tiempo") or 0) > 1 and (u.get("bytes_video") or 0) > 0 and (u.get("bytes_audio") or 0) > 0

    def _webrtc_reproduce(self):
        u = self.navegador.ultima("webrtc").get("rtc") or {}
        return u.get("estado") == "connected" and (campo(u, "video", "fotogramas") or 0) > 5 \
            and (campo(u, "audio", "energia") or 0) > 0

    def _empuje_llega(self):
        return self.vigia.estado(self.d_emp).get("estado") == "enviando" and self.receptor.bytes() > 0

    def esperar_medios(self, segundos=90):
        """Hasta que las tres salidas llevan señal. Apunta cuánto tardó cada una desde la activación."""
        t0, listos = self.r["tiempos"]["activacion"], {}
        vistas = {"empuje": self._empuje_llega}
        if self.navegador:
            vistas.update(hls=self._hls_reproduce, webrtc=self._webrtc_reproduce)
        else:
            vistas["hls"] = lambda: len(self.vigia_hls.muestras) > 3

        recargas = [time.time(), 0]

        def todas():
            for k, ver in vistas.items():
                if k not in listos and ver():
                    listos[k] = round(time.time() - t0, 1)
            if self.navegador and time.time() - recargas[0] > 12 and recargas[1] < 3 and any(
                    k not in listos and not self.navegador.ultima(k).get("video") for k in ("hls", "webrtc")):
                recargas[0], recargas[1] = time.time(), recargas[1] + 1
                self.navegador.recargar()                   # la página no llegó a cargar el reproductor: F5
            return len(listos) == len(vistas)
        esperar(todas, segundos, cada=0.25)
        self.r["tiempos"]["primeros_medios_s"] = listos
        self.r["tiempos"]["recargas"] = recargas[1]
        for k in vistas:
            self.comprobar(k, f"{NOMBRES[self.r['rutas'][k]['protocolo']]}: llega señal tras activar", k in listos,
                           f"a los {listos[k]} s" if k in listos else self._diagnostico(k))
        return len(listos) == len(vistas)

    def _diagnostico(self, ruta):
        e = self.vigia.estado(self.r["rutas"][ruta]["destino"])
        partes = [f"el hub dice «{e.get('estado')}»: {e.get('detalle')}"]
        if self.navegador and ruta in ("hls", "webrtc"):
            u = self.navegador.ultima(ruta)
            partes.append(f"el navegador: {json.dumps({k: u.get(k) for k in ('video', 'rtc', 'errores', 'error', 'texto')}, ensure_ascii=False)[:300]}")
            if self.navegador.fatal:
                partes.append(f"el navegador no arrancó: {self.navegador.fatal[:200]}")
        return "; ".join(partes)

    def medir_navegador(self, t0, t1, cuando):
        """Continuidad de HLS y de WebRTC en el navegador entre dos instantes."""
        r = {}
        if not self.navegador:
            return r
        n = self.navegador
        hls = continuidad(tramo(n.serie("hls", "video", "tiempo"), t0, t1), 0.05)
        hls["bytes_video"] = continuidad(tramo(n.serie("hls", "video", "bytes_video"), t0, t1))["avance"]
        hls["bytes_audio"] = continuidad(tramo(n.serie("hls", "video", "bytes_audio"), t0, t1))["avance"]
        u = n.ultima("hls").get("video") or {}
        hls.update(ancho=u.get("ancho"), alto=u.get("alto"))
        hls["ok"] = hls["segundos"] > 0 and hls["avance"] >= HLS_AVANCE_MIN * hls["segundos"] \
            and hls["parada_max_s"] <= HLS_PARADA_MAX and hls["bytes_video"] > 0 and hls["bytes_audio"] > 0
        web = continuidad(tramo(n.serie("webrtc", "rtc", "video", "fotogramas"), t0, t1), 0)
        audio = continuidad(tramo(n.serie("webrtc", "rtc", "audio", "muestras"), t0, t1), 0)
        energia = continuidad(tramo(n.serie("webrtc", "rtc", "audio", "energia"), t0, t1), 1e-9)
        u = n.ultima("webrtc").get("rtc") or {}
        perdidos = continuidad(tramo(n.serie("webrtc", "rtc", "video", "perdidos"), t0, t1))["avance"]
        web.update(fps=round(web["avance"] / web["segundos"], 2) if web["segundos"] else 0, audio_muestras=audio["avance"],
                   audio_parada_max_s=audio["parada_max_s"], energia=energia["avance"], silencio_max_s=energia["parada_max_s"],
                   paquetes_perdidos=perdidos, ancho=campo(u, "video", "ancho"), alto=campo(u, "video", "alto"),
                   codec_video=campo(u, "video", "codec"), codec_audio=campo(u, "audio", "codec"))
        web["ok_video"] = web["segundos"] > 0 and web["avance"] >= WEBRTC_AVANCE_MIN * self.fps * web["segundos"] \
            and web["parada_max_s"] <= WEBRTC_PARADA_MAX
        web["ok_audio"] = audio["segundos"] > 0 and audio["avance"] >= WEBRTC_AVANCE_MIN * SENAL["audio_hz"] * audio["segundos"] \
            and energia["avance"] > 0 and (energia["parada_max_s"] or 0) <= WEBRTC_PARADA_MAX
        r["hls"], r["webrtc"] = hls, web
        self.comprobar("hls", f"HLS: el navegador reproduce audio y vídeo {cuando}", hls["ok"],
                       f"reprodujo {hls['avance']:.1f} s de {hls['segundos']:.1f} s; mayor parada {hls['parada_max_s']} s; "
                       f"{hls['ancho']}×{hls['alto']}; decodificó {hls['bytes_video'] / 1e6:.1f} MB de vídeo y "
                       f"{hls['bytes_audio'] / 1e3:.0f} kB de audio")
        self.comprobar("webrtc", f"WebRTC: el navegador reproduce vídeo {cuando}", web["ok_video"],
                       f"{web['avance']:.0f} fotogramas en {web['segundos']:.1f} s ({web['fps']} fps); mayor parada "
                       f"{web['parada_max_s']} s; {web['ancho']}×{web['alto']} {web['codec_video'] or ''}; "
                       f"{web['paquetes_perdidos']:.0f} paquetes perdidos")
        self.comprobar("webrtc", f"WebRTC: el navegador recibe audio que suena {cuando}", web["ok_audio"],
                       f"{web['audio_muestras'] / SENAL['audio_hz']:.1f} s de audio {web['codec_audio'] or ''} en "
                       f"{audio['segundos']:.1f} s; mayor silencio {web['silencio_max_s']} s")
        return r

    def medir_lista_hls(self, t0, t1, cuando):
        v = self.vigia_hls
        c = continuidad(tramo(v.muestras, t0, t1), 0)
        c["errores"] = len([e for e in v.errores if t0 <= e[0] <= t1])
        ok = c["segundos"] > 0 and c["avance"] > 0 and c["parada_max_s"] <= 3 * HLS_PARADA_MAX and not c["errores"]
        self.comprobar("hls", f"HLS: la lista del hub sigue sacando segmentos {cuando}", ok,
                       f"{c['avance']:.0f} segmentos nuevos en {c['segundos']:.1f} s; mayor espera {c['parada_max_s']} s; "
                       f"{c['errores']} peticiones fallidas")
        return c

    def estable(self):
        """Con las tres salidas en marcha: audio y vídeo en cada una, y la carga del equipo."""
        args = self.ctx.args
        decir("   comprobando audio y vídeo del HLS con ffmpeg (aparte del navegador)")
        d = decodificar(self.con_host(self.dir_hls["url"]), segundos=8, espera=60)
        video, audio, texto = juzgar_senal(d, self.fps)
        self.r["hls_ffmpeg"] = d
        self.comprobar("hls", "HLS: ffmpeg decodifica el audio y el vídeo de la lista", video and audio, texto)
        decir(f"   {args.estable} s con las tres salidas en marcha, midiendo la carga")
        t0 = time.time()
        time.sleep(args.estable)
        t1 = time.time()
        self.r["tiempos"].update(estable_desde=t0, estable_hasta=t1)
        self.r["estable"] = self.medir_navegador(t0, t1, "con las tres salidas en marcha")
        self.r["estable"]["lista_hls"] = self.medir_lista_hls(t0, t1, "con las tres salidas en marcha")
        if not self.navegador:
            self.comprobar("webrtc", "WebRTC: audio y vídeo en un navegador", None, "no hay navegador para la prueba (Node con Playwright)")
        self.r["carga"] = resumen_carga(self.vigia.cargas, t0, t1)
        estados = {k: self.vigia.estado(d["destino"]) for k, d in self.r["rutas"].items()}
        self.r["estados_estable"] = estados
        self.comprobar("", "El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda",
                       all(e.get("estado") == "enviando" and e.get("evidencia") for e in estados.values()),
                       " · ".join(f"{NOMBRES[self.r['rutas'][k]['protocolo']]}: {e.get('estado')} ({'; '.join(e.get('evidencia') or [])})"
                                  for k, e in estados.items()))

    def corte(self):
        """Corta el receptor, espera, lo devuelve y mide."""
        args, nombre = self.ctx.args, NOMBRES[self.empuje]
        t_corte = self.receptor.cortar()
        decir(f"   CORTE: el receptor {nombre} desaparece durante {args.corte} s")
        time.sleep(args.corte)
        t_vuelta = self.receptor.abrir()
        decir(f"   el receptor {nombre} vuelve; esperando a que el hub recupere la salida")
        t_datos = esperar(lambda: time.time() if self.receptor.bytes() > 0 else None, RECUPERA_MAX + 20, cada=0.1)
        t_estado = esperar(lambda: time.time() if self.vigia.estado(self.d_emp).get("estado") == "enviando" else None,
                           RECUPERA_MAX + 20, cada=0.25)
        time.sleep(12)                                      # material para comprobar audio y vídeo tras la recuperación
        t_fin = time.time()
        linea = self.vigia.linea(self.d_emp, t_corte - 3, t_fin)
        detecta = next((t for t, e in linea if t >= t_corte and e != "enviando"), None)
        # lo que enseñaba el hub con el receptor ya ausente: a media espera, o al detectarlo si el corte fue corto
        durante = next((foto[self.d_emp] for t, foto in self.vigia.estados
                        if max(detecta or t_corte, t_corte + min(15, args.corte / 2)) <= t <= t_vuelta and self.d_emp in foto), None) \
            or next((foto[self.d_emp] for t, foto in self.vigia.estados if detecta and t >= detecta and self.d_emp in foto), {})
        c = self.r["corte"] = {
            "segundos": args.corte, "corte": t_corte, "vuelta": t_vuelta, "fin": t_fin,
            "detecta_s": round(detecta - t_corte, 1) if detecta else None,
            "estado_durante": durante.get("estado"), "detalle_durante": durante.get("detalle"),
            "evidencia_durante": durante.get("evidencia"),
            "datos_s": round(t_datos - t_vuelta, 1) if t_datos else None,
            "estado_s": round(t_estado - t_vuelta, 1) if t_estado else None,
            "linea": {k: self.vigia.linea(d["destino"], t_corte - 3, t_fin) for k, d in self.r["rutas"].items()},
            "sin_senal_s": continuidad(tramo(self.receptor.muestras, t_corte - 3, t_fin))["parada_max_s"],
            "reintento_s": args.reintento}
        self.comprobar("empuje", f"{nombre}: el hub detecta que el receptor se fue y lo enseña", bool(detecta)
                       and c["detecta_s"] <= DETECTA_MAX and durante.get("estado") in ("reconectando", "error"),
                       f"a los {c['detecta_s']} s del corte pasa a «{durante.get('estado')}»: {durante.get('detalle')}"
                       if detecta else "siguió diciendo «enviando»")
        otras = {k: sorted({e for t, e in self.vigia.linea(self.r["rutas"][k]["destino"], t_corte, t_vuelta)}) for k in ("hls", "webrtc")}
        if self.navegador:
            self.comprobar("", "Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando»",
                           all(v == ["enviando"] for v in otras.values()),
                           " · ".join(f"{NOMBRES[k]}: {', '.join(v) or 'sin datos'}" for k, v in otras.items()))
        c["durante"] = self.medir_navegador(t_corte, t_vuelta, f"durante los {args.corte} s de corte")
        c["durante"]["lista_hls"] = self.medir_lista_hls(t_corte, t_vuelta, f"durante los {args.corte} s de corte")
        self.comprobar("empuje", f"{nombre}: la salida se recupera sola al volver el receptor",
                       t_datos is not None and t_estado is not None and c["datos_s"] <= RECUPERA_MAX,
                       f"el receptor vuelve a tener señal a los {c['datos_s']} s y el hub marca «enviando» a los {c['estado_s']} s "
                       f"(reintenta cada {args.reintento:g} s)" if t_datos and t_estado
                       else f"no volvió en {RECUPERA_MAX + 20:.0f} s: {self._diagnostico('empuje')}")
        c["despues"] = self.medir_navegador(t_vuelta, t_fin, "mientras la salida cortada se recupera")
        if self.emisor.lanzamientos != self.publicaciones:
            self.comprobar("", "La cámara de pruebas publica sin cortes durante toda la prueba", False,
                           f"tuvo que volver a publicar {veces(self.emisor.lanzamientos - self.publicaciones)}")

    # -- reinicio con las rutas activas
    def _series(self):
        """De cada salida, un contador que crece mientras llega señal y cuánto debe crecer en 4 s."""
        r = {"empuje": (lambda: self.receptor.muestras, 1)}
        if self.navegador:
            r["hls"] = (lambda: self.navegador.serie("hls", "video", "tiempo"), 2.0)
            r["webrtc"] = (lambda: self.navegador.serie("webrtc", "rtc", "video", "fotogramas"), self.fps * 2)
        return r

    def _tareas(self):
        """Los procesos que el hub lleva por las rutas (envíos, conversiones, entradas), sin contar MediaMTX."""
        return {k: m for k, m in (self.hub.pedir("GET", "/api/carga").get("tareas") or {}).items() if m.get("tipo") != "motor"}

    def reinicio(self, orden, suave=False):
        """Reinicia el hub con las rutas activas. suave: solo sus servicios (como al desplegar una
        versión), que no debe cortar ninguna señal. Si no, como reiniciar el equipo: todo cae y debe
        volver solo, con un proceso por tarea."""
        hub, base, series = self.hub, self.ctx.args.base, self._series()
        decir(f"   REINICIO {'de los servicios del hub' if suave else 'completo'} con las rutas activas: {orden}")
        antes = {"tareas": sorted(self._tareas()), "ffmpeg": ffmpeg_de_rutas(base) if base else None,
                 "camara": self.emisor.lanzamientos}
        t0 = time.time()
        if orden == "manual":
            decir("   reinicia ahora el equipo del hub: la prueba espera a que deje de responder y a que vuelva (hasta 20 min)")
            esperar(lambda: not hub.responde(), 600, cada=1)
            salida = 0
        else:
            salida = subprocess.run(orden, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=600).returncode
        vuelve = esperar(lambda: time.time() if hub.responde() else None, 1200 if orden == "manual" else 180, cada=1)
        r = {"orden": orden, "suave": suave, "salida": salida, "t": t0, "api_s": round(vuelve - t0, 1) if vuelve else None}
        self.r["reinicios"].append(r)
        if not self.comprobar("", "Tras el reinicio, el panel del hub vuelve a responder", bool(vuelve) and salida == 0,
                              f"a los {r['api_s']} s" if vuelve else f"no respondió (la orden salió con {salida})"):
            return
        recargas = [time.time(), 0]

        def todas():
            if not self.receptor.vivo():                    # el receptor se quedó sin llamada: vuelve a escuchar
                self.receptor.abrir()
            bien = {k: avanza(serie(), 4, minimo) for k, (serie, minimo) in series.items()}
            listas = all(self.vigia.estado(self.r["rutas"][k]["destino"]).get("estado") in ("esperando_receptor", "enviando")
                         for k in ("hls", "webrtc"))
            if self.navegador and listas and not (bien["hls"] and bien["webrtc"]) and time.time() - recargas[0] > 15 \
                    and recargas[1] < 5:
                recargas[0], recargas[1] = time.time(), recargas[1] + 1
                self.navegador.recargar()                   # como quien ve el reproductor parado con la ruta ya lista: F5
            return all(bien.values())
        volvieron = esperar(todas, 180, cada=1)
        time.sleep(8)
        t1 = time.time()
        paradas = {k: continuidad(tramo(serie(), t0 - 2, t1), 0.05 if k == "hls" else 0)["parada_max_s"] for k, (serie, _) in series.items()}
        estados = {k: self.vigia.estado(d["destino"]).get("estado") for k, d in self.r["rutas"].items()}
        carga = self._tareas()
        despues = ffmpeg_de_rutas(base) if base else None
        arranque = campo(hub.pedir("GET", "/api/matriz"), "sistema", "arranque") or {}
        mismos = None if despues is None else sum(1 for k, v in despues.items() if antes["ffmpeg"].get(k) == v)
        r.update(paradas_s=paradas, estados=estados, recargas=recargas[1], tareas_antes=antes["tareas"], tareas_despues=sorted(carga),
                 ffmpeg_antes=antes["ffmpeg"], ffmpeg_despues=despues, mismos_procesos=mismos, arranque=arranque,
                 camara_republico=self.emisor.lanzamientos - antes["camara"])
        nombres = {k: NOMBRES[self.r["rutas"][k]["protocolo"]] for k in series}
        dicho = " · ".join(f"{nombres[k]}: {paradas[k]} s sin señal" for k in series)
        en_marcha = bool(volvieron) and all(e == "enviando" for k, e in estados.items() if k in series)
        if suave:
            sigue = en_marcha and all(p is not None and p <= 2.0 for p in paradas.values()) \
                and (despues is None or mismos == len(antes["ffmpeg"]) == len(despues))
            self.comprobar("", "Reiniciar los servicios del hub no corta ninguna salida", sigue,
                           dicho + (f"; los {mismos} ffmpeg de las rutas son los mismos procesos de antes" if despues is not None else "")
                           + f"; el supervisor anotó al arrancar que {arranque.get('siguen')} seguían en marcha y que lanzó {arranque.get('lanzados')}")
        else:
            self.comprobar("", "Tras el reinicio, las rutas que estaban activas vuelven solas", en_marcha,
                           dicho + "; ahora: " + ", ".join(f"{nombres[k]} {estados[k]}" for k in series)
                           + (f"; páginas de los reproductores recargadas {veces(recargas[1])}" if recargas[1] else ""))
        unico = sorted(carga) == antes["tareas"] and all(m.get("procesos") == 2 for m in carga.values())
        detalle = f"{len(carga)} procesos del hub, los mismos que antes ({', '.join(m.get('texto', k) for k, m in carga.items())})"
        if despues is not None:
            unico = unico and all(len(v) == 1 for v in despues.values()) and sorted(despues) == sorted(antes["ffmpeg"])
            detalle += "; un solo ffmpeg por tarea"
        self.comprobar("", "Tras el reinicio no hay publicaciones ni procesos duplicados", unico, detalle)

    # -- desmontar y mirar las capturas
    def desmontar(self):
        for cosa in (self.navegador, self.vigia_hls, self.receptor and type("", (), {"parar": self.receptor.cerrar})(), self.emisor):
            try:
                if cosa:
                    cosa.parar()
            except Exception as e:                          # que un programa no pare no deja el hub sin limpiar
                decir(f"   (al parar: {e})")
        if self.ctx.args.conservar:
            return
        for did in (self.d_emp, self.d_hls, self.d_web):
            for ruta in (f"/api/rutas/{did}", f"/api/destinos/{did}"):
                try:
                    if did:
                        self.hub.pedir("DELETE", ruta)
                except ErrorHub as e:
                    decir(f"   (no se pudo borrar {ruta}: {e})")
        try:
            if self.fid:
                self.hub.pedir("DELETE", f"/api/fuentes/{self.fid}")
        except ErrorHub as e:
            decir(f"   (no se pudo borrar la fuente: {e})")

    def capturas(self):
        """Lo que guardó el receptor antes del corte y después: ¿trae audio y vídeo de verdad?"""
        if not self.receptor:
            return
        nombre, r = NOMBRES[self.empuje], []
        for n, archivo in enumerate(self.receptor.capturas[:2]):
            cuando = "antes del corte" if n == 0 else "tras recuperarse"
            d = decodificar(archivo)
            video, audio, texto = juzgar_senal(d, self.fps)
            r.append(dict(d, archivo=archivo.name, cuando=cuando))
            self.comprobar("empuje", f"{nombre}: el receptor tiene audio y vídeo {cuando}", video and audio, texto)
        self.r["capturas"] = r
        if not self.ctx.args.capturas:
            for archivo in self.receptor.capturas:
                archivo.unlink(missing_ok=True)

    def correr(self, reinicios=()):
        """reinicios: [(orden, suave)] que se hacen al final, con las rutas aún activas."""
        try:
            if self.montar() and self.planes() and self.activar():
                self.abrir_reproductores()
                if self.esperar_medios():
                    self.estable()
                    self.corte()
                    for orden, suave in reinicios:
                        self.reinicio(orden, suave)
        except ErrorHub as e:
            self.comprobar("", "La prueba pudo hablar con el hub hasta el final", False, str(e))
        finally:
            self.r["tiempos"]["fin"] = time.time()
            self.r["cambios"] = self._cambios()
            self.desmontar()
        self.capturas()
        self.r["pasa"] = {k: self._veredicto(self.de_la_ruta(k)) for k in self.r["rutas"]}
        self.r["resultado"] = self._veredicto(self.r["comprobaciones"])
        return self.r

    def _cambios(self):
        """Lo que el registro de cambios del hub anotó de esta prueba (quién hizo qué), sin claves."""
        try:
            texto = self.hub.pedir("GET", "/api/cambios?limite=60", crudo=True)
        except ErrorHub:
            return []
        self.vigia._mirar("/api/cambios", texto)
        return [c for c in json.loads(texto) if c["t"] >= self.r["tiempos"].get("activacion", time.time()) - 120
                and PREFIJO.strip() in (c.get("objeto") or "") + (c.get("detalle") or "")][:20]

    @staticmethod
    def _veredicto(comprobaciones):
        if any(c["ok"] is False for c in comprobaciones) or not comprobaciones:
            return "falla"
        return "sin comprobar" if any(c["ok"] is None for c in comprobaciones) else "pasa"


# ---------------------------------------------------------------- las claves, fuera de los registros
def buscar_claves(secretos, carpetas, journal, desde):
    """Busca las claves de la prueba en los registros del hub. Devuelve (dónde se miró, dónde aparecieron)."""
    mirado, hallazgos = [], []
    claves = [s.encode() for s in secretos if s]
    for carpeta in carpetas:
        carpeta = pathlib.Path(carpeta)
        archivos = [carpeta] if carpeta.is_file() else [a for a in carpeta.rglob("*") if a.is_file()] if carpeta.is_dir() else []
        try:
            dicha = carpeta.resolve().relative_to(pathlib.Path.cwd())
        except ValueError:
            dicha = carpeta
        mirado.append(f"{dicha} ({len(archivos)} archivos)")
        for a in archivos:
            try:
                if a.suffix in (".db", ".db-wal", ".db-shm") or a.stat().st_size > 200 * 1024 * 1024:
                    continue                                # la base guarda las claves a propósito: no es un registro
                contenido = a.read_bytes()
            except OSError:
                continue
            if any(k in contenido for k in claves):
                hallazgos.append(str(a))
    if journal and shutil.which("journalctl"):
        unidades = ["msxs-mediamtx", "msxs-supervisor", "msxs-web"]
        try:
            r = subprocess.run(["journalctl", "--no-pager", "-o", "cat", "--since", f"@{int(desde)}", *[x for u in unidades for x in ("-u", u)]],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
            mirado.append(f"journald de {', '.join(unidades)} ({len(r.stdout.splitlines())} líneas)")
            if any(k in r.stdout for k in claves):
                hallazgos.append("journald")
        except (OSError, subprocess.TimeoutExpired) as e:
            mirado.append(f"journald: no se pudo leer ({e})")
    return mirado, hallazgos


# ---------------------------------------------------------------- el informe
MARCA = {True: "Pasa", False: "**Falla**", None: "Sin comprobar"}
VEREDICTO = {"pasa": "Pasa", "falla": "**Falla**", "sin comprobar": "Sin comprobar"}
ESTADOS = {"esperando_fuente": "esperando fuente", "recibiendo": "recibiendo medios", "enviando": "enviando",
           "esperando_receptor": "esperando receptor", "reconectando": "reconectando", "error": "error", "desactivada": "desactivada"}


def _pieza(p):
    if not p:
        return "—"
    accion = {"copia": "Se copia", "convierte": "Se convierte"}.get(p.get("accion"), str(p.get("accion")))
    return f"{accion}: {p.get('de') or '—'}" + (f" → {p['a']}" if p.get("a") else "") + f". {p.get('motivo') or ''}".rstrip()


def _de(v, unidad=""):
    return "—" if not v else f"{v['media']:g}{unidad} de media, {v['max']:g}{unidad} de máximo"


def informe(datos):
    eq, motor = datos.get("equipo") or {}, datos.get("motor") or {}
    L = [f"# Prueba de aceptación del hub de señales · {datos['lugar']}", ""]
    L += [f"- **Cuándo:** {datos['fecha']}", f"- **Dónde corrió el hub:** {datos['lugar']} · {eq.get('nombre', '?')} · "
          f"{eq.get('cpu', '?')} · {datos.get('nucleos') or '?'} núcleos · {eq.get('sistema', '')}",
          f"- **Hub:** Streamer {datos.get('version', '?')} · MediaMTX {campo(motor, 'mediamtx', 'version') or '?'} · "
          f"FFmpeg {campo(motor, 'ffmpeg', 'version') or '?'} · conversiones de vídeo con {campo(motor, 'ffmpeg', 'codificador') or '?'}",
          f"- **Señal de prueba:** H.264 {SENAL['ancho']}×{SENAL['alto']} a {SENAL['fps']} fps y {SENAL['kbps'] / 1000:g} Mb/s "
          f"({'con' if datos['fotogramas_b'] else 'sin'} fotogramas B) con un tono de {SENAL['tono_hz']} Hz en AAC estéreo a 48 kHz, "
          "generada con ffmpeg.",
          "- **Con qué se comprobó:** la cámara y los receptores son programas (ffmpeg y un Chromium sin ventana). "
          "Es una verificación de banco de pruebas, no con equipos reales.",
          f"- **Cómo se manejó el hub:** por la API del panel, con una sesión de administrador ({datos['hub']}).", ""]
    L += ["## Resultado", "", "| Prueba | Resultado |", "|---|---|"]
    for c in datos["casos"]:
        L.append(f"| {c['titulo']} | {VEREDICTO[c['resultado']]} |")
    L.append(f"| Las claves de la prueba no aparecen en los registros ni en lo que devuelve la API | "
             f"{MARCA[datos['claves']['ok']]} |")
    L.append("")
    for c in datos["casos"]:
        emp = NOMBRES[c["empuje"]]
        L += [f"## {c['titulo']}", ""]
        f = c.get("fuente")
        if f:
            L += [f"La fuente: {f.get('video')}; {', '.join(f.get('audio') or []) or 'sin audio'}. "
                  f"Quién inicia la conexión: {str(f.get('quien_inicia', '')).lower()}. Dirección de la señal: {str(f.get('sentido', '')).lower()}.", ""]
        if c["planes"]:
            L += ["### Antes de activar: qué se copia y qué se convierte", "",
                  "| Salida | Vídeo | Audio | Carga prevista |", "|---|---|---|---|"]
            for k, p in c["planes"].items():
                L.append(f"| {NOMBRES[c['rutas'][k]['protocolo']]} | {_pieza(p.get('video'))} | "
                         f"{' '.join(_pieza(a) for a in p.get('audio') or []) or '—'} | {p.get('carga') or '—'} |")
            L.append("")
        L += ["### Comprobaciones", "", "| Salida | Qué se comprobó | Resultado | Medida |", "|---|---|---|---|"]
        for x in c["comprobaciones"]:
            salida = NOMBRES[c["rutas"][x["ruta"]]["protocolo"]] if x["ruta"] else "Todas"
            que = x["que"].split(": ", 1)[1] if x["ruta"] and x["que"].startswith(salida + ": ") else x["que"]
            L.append(f"| {salida} | {que[0].upper() + que[1:]} | {MARCA[x['ok']]} | {str(x['detalle']).replace('|', '/')} |")
        L.append("")
        k = c.get("corte")
        if k:
            L += [f"### El corte del receptor {emp}", "", "| Momento | Hora | Qué pasó |", "|---|---|---|",
                  f"| Corte | {hora(k['corte'])} | El receptor {emp} desaparece sin despedirse |",
                  f"| El hub lo enseña | +{k['detecta_s']} s | La ruta pasa a «{ESTADOS.get(k['estado_durante'], k['estado_durante'])}»: "
                  f"{k.get('detalle_durante') or ''}. Evidencia: {'; '.join(k.get('evidencia_durante') or []) or '—'} |",
                  f"| Vuelve el receptor | {hora(k['vuelta'])} | A los {k['segundos']} s del corte |",
                  f"| Vuelve la señal al receptor | +{k['datos_s']} s | El hub reintenta cada {k['reintento_s']:g} s. "
                  f"En total el receptor estuvo {k.get('sin_senal_s')} s sin recibir |",
                  f"| El hub marca «enviando» | +{k['estado_s']} s | |", "",
                  "Estados que fue enseñando el hub en cada ruta:", ""]
            for ruta, linea in k["linea"].items():
                L.append(f"- {NOMBRES[c['rutas'][ruta]['protocolo']]}: " + " → ".join(f"{hora(t)} {ESTADOS.get(e, e)}" for t, e in linea))
            L.append("")
        for r in c.get("reinicios") or []:
            sin = ", ".join(f"{NOMBRES[c['rutas'][x]['protocolo']]} {p} s" for x, p in (r.get("paradas_s") or {}).items())
            siguen = campo(r, "arranque", "siguen")
            L += [f"### Reinicio {'de los servicios del hub' if r.get('suave') else 'completo'} con las rutas activas", "",
                  f"- Orden: `{r['orden']}`. El panel volvió a responder a los {r.get('api_s')} s.",
                  f"- Tiempo sin señal en cada salida: {sin or '—'}.",
                  f"- En su primera vuelta tras arrancar, el supervisor encontró {siguen} procesos en marcha"
                  + (" (los conserva)" if siguen else "")
                  + f" y lanzó {campo(r, 'arranque', 'lanzados')}; los demás los lanza cuando su fuente vuelve a tener señal."
                  + (f" De los ffmpeg de las rutas, {r['mismos_procesos']} de {len(r.get('ffmpeg_despues') or {})} son el mismo "
                     "proceso de antes." if r.get("mismos_procesos") is not None else ""),
                  f"- La cámara de pruebas tuvo que volver a publicar {veces(r.get('camara_republico', 0))}"
                  + (f" y las páginas de los reproductores se recargaron {veces(r['recargas'])}." if r.get("recargas") else "."), ""]
        g = c.get("carga")
        if g:
            L += [f"### Carga con las tres salidas en marcha ({g['segundos']:.0f} s, {g['muestras']} lecturas)", "",
                  f"- Uso de CPU de todo el equipo: {_de(g.get('cpu_pct'), ' %')} (de {g.get('nucleos')} núcleos). "
                  "Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.",
                  f"- Carga media del sistema (1 min): {_de(g.get('carga_1min'))}.",
                  f"- Memoria: {campo(g, 'memoria', 'disponible_mb')} MB disponibles de {campo(g, 'memoria', 'total_mb')} MB."]
            if g.get("gpu"):
                L.append(f"- Tarjeta {g['gpu']['nombre']}: uso {_de(g['gpu'].get('uso_pct'), ' %')}; codificador {_de(g['gpu'].get('codificador_pct'), ' %')}.")
            else:
                L.append("- Sin tarjeta NVIDIA a la vista (nvidia-smi no responde): las conversiones de vídeo irían por CPU.")
            L += ["", "| Proceso del hub | CPU (100 % = un núcleo) | Memoria |", "|---|---|---|"]
            for t in sorted(g["tareas"].values(), key=lambda t: t.get("tipo") != "motor"):
                L.append(f"| {t['texto']} | {_de(t.get('cpu_pct'), ' %')} | {_de(t.get('memoria_mb'), ' MB')} |")
            L += ["", "HLS y WebRTC no llevan proceso propio: los sirve MediaMTX, y su consumo es el de la fila de MediaMTX.", ""]
    k = datos["claves"]
    L += ["## Las claves", "", f"La prueba usó {k['cuantas']} claves (la de publicación de cada fuente, la passphrase SRT, la clave RTMP "
          "y la de lectura de WebRTC) y las buscó después:", ""]
    L += [f"- Registros mirados: {x}" for x in k["mirado"]] or ["- No se miraron registros en disco (la prueba corrió sin acceso a ellos: usar --registros o --journal)."]
    L += [f"- Aparecen en: {', '.join(k['hallazgos'])}" if k["hallazgos"] else "- En los registros mirados no aparece ninguna.",
          f"- En las respuestas de la API (matriz, carga y registro de cambios): "
          + (f"aparecen en {', '.join(k['fugas'])}" if k["fugas"] else "no aparece ninguna; salen como «***»."), ""]
    L += ["## Qué queda anotado como comprobado", ""]
    if datos["anotado"]:
        L += ["| Entrada | Salida | Nivel |", "|---|---|---|"]
        L += [f"| {a['fuente']} | {a['destino']} | {a['nivel']} |" for a in datos["anotado"]]
    else:
        L.append("Nada: la prueba no anotó ninguna combinación (no pasaron, o se pidió --no-anotar).")
    L += ["", "«Verificada en el banco de pruebas» quiere decir con programas haciendo de cámara y de receptor. Ninguna "
          "combinación queda como «verificada con equipos reales» hasta que un administrador lo anote tras probarla con ellos.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- principal
class Contexto:
    pass


def limpiar_restos(hub):
    """Borra lo que dejara en el hub una prueba anterior que no acabó (solo lo que se llama «Aceptación · …»)."""
    n = 0
    for d in hub.pedir("GET", "/api/destinos"):
        if d["nombre"].startswith(PREFIJO):
            for ruta in (f"/api/rutas/{d['id']}", f"/api/destinos/{d['id']}"):
                hub.pedir("DELETE", ruta)
            n += 1
    for f in hub.pedir("GET", "/api/fuentes"):
        if f["nombre"].startswith(PREFIJO) and not f.get("heredada"):
            hub.pedir("DELETE", f"/api/fuentes/{f['id']}")
            n += 1
    if n:
        decir(f"borrados {n} restos de una prueba anterior")


def argumentos():
    p = argparse.ArgumentParser(description="Prueba de aceptación del hub de señales (ver la cabecera del guion).")
    p.add_argument("--hub", default=os.environ.get("MSXS_HUB", "http://127.0.0.1:8095"), help="dirección del panel del hub")
    p.add_argument("--sesion-archivo", help="archivo con el valor de la cookie msr_sesion de un administrador (o MSXS_SESION)")
    p.add_argument("--host-hub", default="", help="nombre o IP del hub visto desde esta máquina, si no es la misma")
    p.add_argument("--receptor", default="127.0.0.1", help="IP de esta máquina vista desde el hub (adonde empuja SRT y RTMP)")
    p.add_argument("--puerto-srt", type=int, default=9301, help="puerto UDP del receptor SRT de pruebas")
    p.add_argument("--puerto-rtmp", type=int, default=1950, help="puerto TCP del receptor RTMP de pruebas")
    p.add_argument("--pruebas", default="rtmp,srt", help="cuáles: rtmp (entrada RTMP, salida SRT), srt (entrada SRT, salida RTMP)")
    p.add_argument("--corte", type=float, default=60, help="segundos que el receptor está cortado")
    p.add_argument("--estable", type=float, default=30, help="segundos con las tres salidas en marcha antes del corte")
    p.add_argument("--reintento", type=float, default=2, help="política de reconexión del destino cortado: segundos entre intentos")
    p.add_argument("--fotogramas-b", action="store_true", help="la cámara de pruebas emite con fotogramas B (obliga a convertir el vídeo para WebRTC)")
    p.add_argument("--reinicio-suave", action="append", default=[], metavar="ORDEN",
                   help="reinicia solo los servicios del hub con las rutas activas (p. ej. «sudo systemctl restart msxs-supervisor "
                        "msxs-web») y comprueba que ninguna salida se corta")
    p.add_argument("--reinicio", action="append", default=[], metavar="ORDEN",
                   help="reinicia el hub entero con las rutas activas (una orden del sistema, o «manual» para reiniciar el equipo "
                        "a mano desde otra máquina) y comprueba que vuelven solas y sin duplicados")
    p.add_argument("--base", default=os.environ.get("MSXS_BASE", ""), help="carpeta del hub en esta máquina, para contar sus ffmpeg")
    p.add_argument("--registros", action="append", default=[], metavar="CARPETA", help="registros del hub donde buscar las claves")
    p.add_argument("--journal", action="store_true", help="buscar las claves también en journald (servicios msxs-*)")
    p.add_argument("--carpeta", default="", help="dónde dejar el informe (por defecto aceptacion-<fecha>)")
    p.add_argument("--lugar", default="banco de pruebas", help="dónde corre el hub, para el título del informe: «HP Z8 (EZC80)»")
    p.add_argument("--node", default=os.environ.get("NODE", "node"), help="el Node con Playwright")
    p.add_argument("--sin-navegador", action="store_true", help="no abrir navegador: WebRTC queda sin comprobar")
    p.add_argument("--no-anotar", action="store_true", help="no anotar en el hub las combinaciones que pasen")
    p.add_argument("--conservar", action="store_true", help="no borrar del hub lo que crea la prueba")
    p.add_argument("--capturas", action="store_true", help="conservar lo que grabó el receptor")
    return p.parse_args()


def main():
    args = argumentos()
    sesion = os.environ.get("MSXS_SESION", "")
    if args.sesion_archivo:
        sesion = pathlib.Path(args.sesion_archivo).read_text().strip()
    if not sesion:
        sys.exit("Falta la sesión: MSXS_SESION o --sesion-archivo con la cookie msr_sesion de un administrador.")
    pruebas = [x.strip() for x in args.pruebas.split(",") if x.strip()]
    if not pruebas or set(pruebas) - {"rtmp", "srt"}:
        sys.exit("--pruebas admite rtmp, srt o las dos separadas por una coma.")
    for programa in (FFMPEG, FFPROBE):
        if not shutil.which(programa):
            sys.exit(f"Falta {programa}.")
    args.base = str(pathlib.Path(args.base).resolve()) if args.base else ""
    ctx = Contexto()
    ctx.args, ctx.hub, ctx.secretos = args, Hub(args.hub, sesion), set()
    ctx.host_hub = args.host_hub or (urllib.parse.urlsplit(args.hub).hostname if urllib.parse.urlsplit(args.hub).hostname
                                     not in ("127.0.0.1", "localhost") else "")
    ctx.carpeta = pathlib.Path(args.carpeta or datetime.datetime.now().strftime("aceptacion-%Y%m%d-%H%M%S")).resolve()
    ctx.carpeta.mkdir(parents=True, exist_ok=True)
    ctx.node = None if args.sin_navegador else shutil.which(args.node)
    if ctx.node and subprocess.run([ctx.node, "-e", "try { require('playwright') } catch (e) { require('playwright-core') }"],
                                   capture_output=True).returncode != 0:
        ctx.node = None
    if not ctx.node:
        decir("AVISO: sin Node con Playwright no hay navegador: WebRTC quedará sin comprobar")
    try:
        yo = ctx.hub.pedir("GET", "/api/matriz")
    except ErrorHub as e:
        sys.exit(f"No se puede entrar al hub en {args.hub}: {e}")
    faltan = {"editar", "ver_claves", "modificar_al_aire", "ajustes"} - set(yo.get("permisos") or [])
    if faltan:
        sys.exit(f"La sesión no es de administrador: le faltan los permisos {', '.join(sorted(faltan))}.")
    if not (yo["sistema"]["mediamtx"] and yo["sistema"]["supervisor"]) or yo["sistema"].get("reposo"):
        sys.exit(f"El hub no está en marcha del todo: {json.dumps(yo['sistema'], ensure_ascii=False)}")
    for puerto, udp, nombre in ((args.puerto_srt, True, "--puerto-srt"), (args.puerto_rtmp, False, "--puerto-rtmp")):
        if not puerto_libre(puerto, udp):
            sys.exit(f"El puerto {puerto} está ocupado en esta máquina: elige otro con {nombre}.")
    limpiar_restos(ctx.hub)
    inicio = time.time()
    ctx.vigia = Vigia(ctx.hub)
    ctx.vigia.start()
    casos = []
    try:
        for n, entrada in enumerate(pruebas):
            caso = Caso(ctx, entrada, "srt" if entrada == "rtmp" else "rtmp")
            reinicios = [(o, True) for o in args.reinicio_suave] + [(o, False) for o in args.reinicio]
            casos.append(caso.correr(reinicios if n == len(pruebas) - 1 else ()))
            time.sleep(3)
    finally:
        ctx.vigia.parar()
    mirado, hallazgos = buscar_claves(ctx.secretos, args.registros, args.journal, inicio)
    claves = {"cuantas": len(ctx.secretos), "mirado": mirado, "hallazgos": hallazgos, "fugas": sorted(ctx.vigia.fugas),
              "ok": (None if not mirado else True) if not hallazgos and not ctx.vigia.fugas else False}
    anotado = []
    if not args.no_anotar:
        nota = f"Prueba de aceptación automática del {datetime.date.today().isoformat()} en {args.lugar}: corte de {args.corte:g} s superado"
        for c in casos:
            for ruta, veredicto in c["pasa"].items():
                if veredicto != "pasa":
                    continue
                lado_d = f"{c['rutas'][ruta]['protocolo']}:{'hub' if ruta == 'empuje' else 'receptor'}"
                try:
                    r = ctx.hub.pedir("PUT", "/api/validacion", {"fuente": f"{c['entrada']}:origen", "destino": lado_d,
                                                                 "nivel": "laboratorio", "nota": nota})
                    anotado.append({"fuente": f"{NOMBRES[c['entrada']]} (el codificador llama al hub)",
                                    "destino": f"{NOMBRES[c['rutas'][ruta]['protocolo']]} ({'el hub llama al receptor' if ruta == 'empuje' else 'el receptor pide la señal al hub'})",
                                    "nivel": "Verificada en el banco de pruebas" if r.get("anotado") else "Ya estaba verificada con equipos reales"})
                except ErrorHub as e:
                    decir(f"(no se pudo anotar {c['entrada']} → {lado_d}: {e})")
    try:
        motor, version = ctx.hub.pedir("GET", "/api/adaptadores"), ctx.hub.pedir("GET", "/api/sistema").get("version")
        motor = {k: motor.get(k) for k in ("mediamtx", "ffmpeg")}
    except ErrorHub:
        motor, version = {}, "?"
    datos = {"fecha": datetime.datetime.fromtimestamp(inicio).astimezone().strftime("%Y-%m-%d %H:%M %Z"), "lugar": args.lugar,
             "hub": args.hub, "version": version, "motor": motor, "equipo": ctx.vigia.equipo,
             "nucleos": next((c.get("nucleos") for _, c in ctx.vigia.cargas), None), "fotogramas_b": args.fotogramas_b,
             "casos": casos, "claves": claves, "anotado": anotado,
             "sin_respuesta": len(ctx.vigia.fallos), "criterios": {
                 "fotogramas_min": FOTOGRAMAS_MIN, "volumen_min_db": VOLUMEN_MIN_DB, "hls_avance_min": HLS_AVANCE_MIN,
                 "hls_parada_max_s": HLS_PARADA_MAX, "webrtc_avance_min": WEBRTC_AVANCE_MIN, "webrtc_parada_max_s": WEBRTC_PARADA_MAX,
                 "detecta_max_s": DETECTA_MAX, "recupera_max_s": RECUPERA_MAX}}
    (ctx.carpeta / "datos.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (ctx.carpeta / "informe.md").write_text(informe(datos), encoding="utf-8")
    resultados = [c["resultado"] for c in casos] + ([] if claves["ok"] is not False else ["falla"])
    decir(f"informe en {ctx.carpeta / 'informe.md'}")
    for c in casos:
        decir(f"{c['titulo']}: {c['resultado'].upper()}")
    sys.exit(1 if "falla" in resultados else 3 if "sin comprobar" in resultados else 0)


if __name__ == "__main__":
    main()
