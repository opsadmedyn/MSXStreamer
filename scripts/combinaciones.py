#!/usr/bin/env python3
"""Media Syntaxis Streamer - las demás formas de conexión del hub, con señal de verdad.

La prueba de aceptación (aceptacion.py) hace lo que se pidió como prueba principal. Este guion
comprueba, por la misma API del panel y con señales reales, lo que aquella no toca:

  entregas     lo que el hub entrega para que otro equipo lo abra: SRT con stream ID y passphrase,
               SRT con puerto propio (el receptor llama al hub) y RTMP (un cliente lee del hub)
  llamadas     otras formas de recibir la fuente. El hub va a buscarla: SRT (llama a un codificador a
               la escucha, y a otro hub con stream ID y passphrase), RTMP (lee de un servidor) y HLS
               (lee una lista .m3u8). O la espera en un puerto propio (SRT, sin stream ID)
  rtmps        RTMPS en los cuatro sentidos: un codificador publica cifrado en el hub y un cliente
               lee cifrado de él (si el hub tiene certificado), y el hub publica en una plataforma
               y lee de un servidor (un MediaMTX aparte, con un certificado de usar y tirar)
  webrtc       WHIP de entrada desde un navegador, hacia SRT y hacia RTMP (el audio pasa de Opus a
               AAC), y reenvío WHIP a otro servidor, visto después por WHEP
  perfiles     un destino con su resolución y su bitrate, y dos destinos con el mismo perfil que
               comparten una sola conversión
  fotogramas   H.264 con fotogramas B hacia WebRTC: se convierte el vídeo; hacia HLS se copia
  audio        una fuente con dos pistas: un destino las lleva todas y otros eligen la segunda
  fuente       se corta la fuente 20 s con tres rutas activas: pasan a «esperando fuente» y vuelven solas
  reconexion   un destino con tope de intentos pasa a «error», y «reintentar» lo arranca
  cambio       cambiar la fuente de un destino al aire exige decir cuál se sustituye
  matriz       cada una de las seis formas de entrada, conectada a la vez a las siete formas de salida:
               las 42 combinaciones de SRT, RTMP, HLS y WebRTC, con audio y vídeo comprobados en cada una
  https        HLS y WebRTC con HTTPS por delante, como se sirven fuera de la red local: un proxy con
               certificado en el mismo equipo (un Caddy de verdad si el equipo lo tiene, y si no uno de
               pruebas que hace lo mismo) y el hub entregando esas direcciones porque se le dicen en
               Ajustes. Un lector y un navegador reproducen por HTTPS, un navegador publica por WHIP y
               quien entra por el proxy sin token no pasa, ni diciendo venir del propio equipo. Con
               otros dos proxies mal puestos (uno que no anota de dónde viene cada petición y otro que
               deja pasar lo que la petición diga de sí misma) sí se entra sin clave, y «Probar la
               conexión» lo dice
  ice          WebRTC con una dirección anunciada y un servidor TURN (un coturn de pruebas): cambiar esos
               ajustes con alguien mirando exige decir qué se corta y no corta lo que va por SRT, y un
               navegador que solo puede salir por el TURN reproduce y publica

Como en la prueba de aceptación, la cámara y los receptores son programas: lo que pasa queda
anotado en el hub como «verificado en el banco de pruebas», nunca con equipos reales.

    MSXS_SESION=admin python3 scripts/combinaciones.py --registros .banco --sesion-operador operador

Sale con 0 si todo pasa, 1 si algo falla y 3 si algo quedó sin comprobar. Deja informe.md y
datos.json en la carpeta de resultados. Usa las piezas de aceptacion.py (misma carpeta).
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import aceptacion as A
from aceptacion import NOMBRES, PREFIJO, ErrorHub, campo, decir, esperar

LADOS = {"srt:origen": "SRT (el codificador llama al hub)", "srt:hub": "SRT (el hub llama)",
         "rtmp:origen": "RTMP (el codificador publica en el hub)", "rtmp:hub": "RTMP (el hub llama)",
         "hls:hub": "HLS (el hub lee la lista)", "webrtc:origen": "WebRTC (el origen publica por WHIP)",
         "srt:receptor": "SRT (el receptor llama al hub)", "rtmp:receptor": "RTMP (un cliente lee del hub)",
         "hls:receptor": "HLS (el reproductor pide la lista)", "webrtc:receptor": "WebRTC (el navegador pide la señal, WHEP)",
         "webrtc:hub": "WebRTC (el hub reenvía por WHIP)"}
ENTRADAS = {"srt:origen": "SRT, el codificador llama al hub", "srt:hub": "SRT, el hub llama al codificador",
            "rtmp:origen": "RTMP, el codificador publica en el hub", "rtmp:hub": "RTMP, el hub lee de un servidor",
            "hls:hub": "HLS, el hub lee una lista", "webrtc:origen": "WebRTC, un navegador publica por WHIP"}
SALIDAS = {"srt:hub": "SRT (el hub llama al receptor)", "srt:receptor": "SRT (el receptor llama al hub)",
           "rtmp:hub": "RTMP (el hub publica en un servidor)", "rtmp:receptor": "RTMP (un cliente lee del hub)",
           "hls:receptor": "HLS", "webrtc:receptor": "WebRTC (un navegador la abre por WHEP)",
           "webrtc:hub": "WebRTC (el hub la reenvía por WHIP)"}
FPS_NAVEGADOR = 10                  # un navegador que publica baja los fotogramas si le falta CPU: se exige que fluya


def trozo(nombre):
    """Un nombre de carpeta a partir de un texto."""
    plano = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plano.lower()).strip("-")[:48] or "x"


class Camara(A.Emisor):
    """La cámara de pruebas con lo que hace falta aquí: dos pistas de audio, otro tamaño o fotogramas B.
    Si su dirección es SRT en modo listener, espera a que el hub la llame."""

    def __init__(self, protocolo, url, carpeta, ocultar, con_b=False, audios=1, alto=None):
        super().__init__(protocolo, url, carpeta, ocultar, con_b)
        self.audios, self.alto = audios, alto or A.SENAL["alto"]

    def orden(self):
        s = A.SENAL
        ancho = round(self.alto * 16 / 9 / 2) * 2
        orden = [A.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-re",
                 "-f", "lavfi", "-i", f"testsrc2=size={ancho}x{self.alto}:rate={s['fps']}",
                 "-f", "lavfi", "-i", f"sine=frequency={s['tono_hz']}:sample_rate={s['audio_hz']}"]
        if self.audios > 1:                 # la segunda pista: otro tono y 12 dB más baja, para distinguirla al medir
            orden += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={s['audio_hz']},volume=0.25"]
        orden += ["-map", "0:v", "-map", "1:a"] + (["-map", "2:a"] if self.audios > 1 else [])
        orden += ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p",
                  "-b:v", f"{s['kbps']}k", "-maxrate", f"{s['kbps']}k", "-bufsize", f"{s['kbps'] * 2}k", "-g", str(s["fps"] * 2)]
        orden += ["-bf", "2"] if self.con_b else ["-tune", "zerolatency", "-bf", "0"]
        orden += ["-c:a", "aac", "-b:a", "128k", "-ar", str(s["audio_hz"]), "-ac", "2"]
        if self.protocolo == "rtmp":
            return orden + ["-f", "flv", self.url]
        return orden + ["-f", "mpegts", self.url + ("&" if "?" in self.url else "?") + "pkt_size=1316"]


class Lector:
    """Un equipo que viene a por la señal que el hub entrega: un ffmpeg que la lee y guarda lo que llega."""

    def __init__(self, nombre, url, carpeta, ocultar):
        self.nombre, self.url, self.carpeta, self.ocultar = trozo(nombre), url, pathlib.Path(carpeta), ocultar
        self.n, self.p, self.archivo = 0, None, None

    def abrir(self):
        self.n += 1
        self.archivo = self.carpeta / f"{self.nombre}_{self.n}.ts"
        # por SRT llega MPEG-TS: sin -scan_all_pmts 0, ffmpeg se guarda 5 s antes de escribir nada
        ts = ["-scan_all_pmts", "0"] if self.url.startswith("srt://") else []
        orden = [A.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-rw_timeout", "15000000", *ts, "-i", self.url,
                 "-map", "0", "-c", "copy", "-f", "mpegts", "-flush_packets", "1", "-y", self.archivo]
        self.p = A.Proceso(self.nombre, orden, self.carpeta, self.ocultar).lanzar()
        return time.time()

    def bytes(self):
        try:
            return self.archivo.stat().st_size
        except (OSError, AttributeError):
            return 0

    def vivo(self):
        return self.p is not None and self.p.vivo()

    def cerrar(self):
        if self.p:
            self.p.parar()


class Caso:
    def __init__(self, clave, titulo):
        self.clave, self.titulo, self.comprobaciones, self.verificadas = clave, titulo, [], []

    def comprobar(self, que, ok, detalle=""):
        """ok: True, False o None (no se pudo comprobar)."""
        self.comprobaciones.append({"que": que, "ok": ok, "detalle": detalle})
        decir(f"   {({True: 'bien', False: 'FALLA', None: 'sin comprobar'})[ok]}: {que}" + (f" ({detalle})" if detalle else ""))
        return bool(ok)

    def verificada(self, fuente, destino, ok):
        """Una combinación de entrada y salida por la que pasó señal de verdad en este caso."""
        if ok and (fuente, destino) not in self.verificadas:
            self.verificadas.append((fuente, destino))

    def resultado(self):
        oks = [x["ok"] for x in self.comprobaciones]
        return "falla" if False in oks or not oks else "sin comprobar" if None in oks else "pasa"


class Banco:
    """El hub visto por su API, y los programas que hacen de cámaras y de receptores."""

    def __init__(self, hub, args, carpeta, node):
        self.hub, self.args, self.carpeta, self.node = hub, args, pathlib.Path(carpeta), node
        self.secretos, self.comun = set(), {}
        self.creado = {"destinos": [], "fuentes": [], "perfiles": []}     # lo que hay que borrar del hub al acabar
        self.del_caso = {"destinos": [], "fuentes": []}
        self.abierto, self.abierto_caso = [], []                           # lo que hay que parar
        self.camaras = {}                                                  # fuente -> su cámara de pruebas
        self._puerto = {"srt": args.puerto_srt, "rtmp": args.puerto_rtmp}

    # -- el hub
    def nuevo(self, lado, nombre, comun=False, **campos):
        i = self.hub.pedir("POST", f"/api/{lado}", {"nombre": PREFIJO + nombre, **campos})["id"]
        (self.creado if comun else self.del_caso)[lado].append(i)
        return i

    def direccion(self, lado, i):
        d = self.hub.pedir("GET", f"/api/{lado}/{i}/claves")
        self.secretos |= A.secretos_de(d)
        return d

    def ruta(self, did):
        """Lo que el hub enseña de la ruta de un destino: estado, detalle y evidencia."""
        d = next((d for d in self.hub.pedir("GET", "/api/matriz")["destinos"] if d["id"] == did), None)
        return campo(d, "ruta", "estado") or {}

    def receptores(self, did):
        """Quién está leyendo ahora un destino que el hub entrega, según el hub."""
        d = next((d for d in self.hub.pedir("GET", "/api/matriz")["destinos"] if d["id"] == did), None)
        return campo(d, "ruta", "metricas", "receptores") or []

    def fuente_lista(self, fid, segundos=75):
        """La fuente, cuando el hub la recibe y ya sabe qué pistas trae; None si no llega a tiempo."""
        def lista():
            f = self.hub.fuente(fid)
            return f if f and f["estado"]["estado"] == "recibiendo" and f.get("pistas") and not f["pistas"]["parcial"] else None
        return esperar(lista, segundos)

    def plan(self, fid, did, **extra):
        return self.hub.pedir("POST", "/api/rutas/plan", {"fuente": fid, "destino": did, **extra})["plan"]

    def conectar(self, fid, did, **extra):
        return self.hub.pedir("PUT", f"/api/rutas/{did}", {"fuente": fid, **extra})

    def conversiones(self):
        return sorted(k for k, m in (self.hub.pedir("GET", "/api/carga").get("tareas") or {}).items() if m.get("tipo") == "conversion")

    # -- los programas de la prueba
    def sub(self, nombre):
        carpeta = self.carpeta / trozo(nombre)
        carpeta.mkdir(parents=True, exist_ok=True)
        return carpeta

    def puerto(self, proto):
        """Un puerto libre de esta máquina para un receptor (o para un puerto propio del hub, que aquí es la misma)."""
        while True:
            self._puerto[proto] += 1
            if A.puerto_libre(self._puerto[proto] - 1, udp=proto == "srt"):
                return self._puerto[proto] - 1

    def camara(self, nombre, protocolo="srt", comun=False, campos=None, **opciones):
        """Una fuente a la que la cámara de pruebas llama, ya recibida: (id, lo que el hub dice de ella o None).
        campos: lo demás de la fuente (un puerto propio, una passphrase); opciones: las de la cámara."""
        fid = self.nuevo("fuentes", nombre, comun=comun, protocolo=protocolo, inicia="origen", **(campos or {}))
        cam = Camara(protocolo, self.direccion("fuentes", fid)["url"], self.sub(nombre), self.secretos, **opciones)
        cam.start()
        self.camaras[fid] = cam
        (self.abierto if comun else self.abierto_caso).append(lambda: self.camaras[fid].parar())
        return fid, self.fuente_lista(fid)

    def encender(self, fid):
        """La cámara de una fuente, de nuevo, tras haberla parado: como el codificador que vuelve."""
        vieja = self.camaras[fid]
        self.camaras[fid] = Camara(vieja.protocolo, vieja.url, vieja.carpeta, self.secretos, vieja.con_b, vieja.audios, vieja.alto)
        self.camaras[fid].start()

    def procesos(self, *tipos):
        """Cuántos procesos tiene el hub en marcha de esos tipos (envio, conversion, entrada)."""
        tareas = (self.hub.pedir("GET", "/api/carga").get("tareas") or {}).values()
        return sum(int(m.get("procesos") or 0) for m in tareas if m.get("tipo") in tipos)

    def principal(self):
        """La cámara que comparten los casos: SRT, 1280×720 a 30 fps con una pista de audio."""
        if "principal" not in self.comun:
            fid, f = self.camara("cámara principal (SRT)", comun=True)
            if not f:
                raise RuntimeError(f"el hub no recibe la cámara de pruebas: {campo(self.hub.fuente(fid), 'estado', 'detalle')}")
            self.comun["principal"] = (fid, f, campo(f, "pistas", "video", "fps") or A.SENAL["fps"])
        return self.comun["principal"]

    def empuje(self, proto, nombre, **campos):
        """Un receptor de pruebas a la escucha y el destino del hub que lo alimenta: (receptor, id del destino)."""
        puerto, secreto = self.puerto(proto), secrets.token_hex(12)
        self.secretos.add(secreto)
        if proto == "srt":
            cuerpo = {"protocolo": "srt", "inicia": "hub", "url": f"srt://{self.args.receptor}:{puerto}",
                      "passphrase": secreto, "latencia_ms": 300}
        else:
            cuerpo = {"protocolo": "rtmp", "inicia": "hub", "url": f"rtmp://{self.args.receptor}:{puerto}/live", "clave": secreto}
        cuerpo["reconexion"] = {"cada_s": 2, "max_intentos": 0}
        cuerpo.update(campos)
        did = self.nuevo("destinos", nombre, **cuerpo)
        receptor = A.Receptor(proto, puerto, secreto, self.sub(nombre))
        receptor.abrir()
        self.abierto_caso.append(receptor.cerrar)
        return receptor, did

    def lector(self, nombre, url):
        lector = Lector(nombre, url, self.sub(nombre), self.secretos)
        self.abierto_caso.append(lector.cerrar)
        return lector

    def navegador(self, nombre, paginas):
        nav = A.Navegador(self.node, paginas, self.sub(nombre), self.secretos).lanzar()
        self.abierto_caso.append(nav.parar)
        return nav

    def capturar(self, cosa, did=None, segundos=10, espera=60, cerrar=True):
        """Espera a que a un receptor o a un lector le llegue señal (relanzándolo si se cae antes: aún
        no había nada que leer) y la deja `segundos`. Devuelve (archivo, lo que el hub decía de la
        ruta mientras llegaba); el archivo es None si no llegó nada."""
        limite = time.time() + espera
        while cosa.bytes() == 0 and time.time() < limite:
            if not cosa.vivo():
                cosa.abrir()
            time.sleep(0.5)
        if cosa.bytes() == 0:
            cosa.cerrar()
            return None, self.ruta(did) if did else {}
        time.sleep(segundos)
        estado = self.ruta(did) if did else {}
        if cerrar:
            cosa.cerrar()
            time.sleep(0.3)
        return cosa.archivo, estado

    def esperar_senal(self, cosas, espera=60):
        """Espera a que a cada receptor o lector le llegue algo, relanzando al que se caiga antes de
        que haya nada que leer. Los deja abiertos."""
        limite = time.time() + espera
        while time.time() < limite and any(x.bytes() == 0 for x in cosas):
            for x in cosas:
                if x.bytes() == 0 and not x.vivo():
                    x.abrir()
            time.sleep(0.5)

    def tono(self):
        """Un archivo con un tono, para que suene el micrófono de mentira del navegador que publica."""
        archivo = self.carpeta / "tono.wav"
        if not archivo.exists():
            subprocess.run([A.FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                            "-t", "10", "-ac", "2", "-y", str(archivo)], stdin=subprocess.DEVNULL, check=False)
        return archivo

    def senal(self, c, que, archivo, fps, estado=None, exige=None):
        """Decodifica lo que llegó y lo apunta. exige(d) -> (ok, texto): algo más que se pide de la señal."""
        dice = f"; el hub dice «{estado.get('estado')}»: {estado.get('detalle')}" if estado else ""
        if not archivo:
            return c.comprobar(que, False, "no llegó señal" + dice), {}
        d = A.decodificar(archivo)
        video, audio, texto = A.juzgar_senal(d, fps)
        mas_ok, mas = exige(d) if exige else (True, "")
        ok = video and audio and mas_ok
        return c.comprobar(que, ok, texto + (f"; {mas}" if mas else "") + ("" if ok else dice)), d

    def cerrar_caso(self):
        for parar in reversed(self.abierto_caso):
            try:
                parar()
            except Exception as e:                          # que un programa no pare no deja el hub sin limpiar
                decir(f"   (al parar: {e})")
        self.abierto_caso = []
        self._borrar(self.del_caso)

    def _borrar(self, cosas):
        if self.args.conservar:
            return
        for did in cosas["destinos"]:
            for ruta in (f"/api/rutas/{did}", f"/api/destinos/{did}"):
                try:
                    self.hub.pedir("DELETE", ruta)
                except ErrorHub as e:
                    decir(f"   (al borrar {ruta}: {e})")
        for lado in ("fuentes", "perfiles"):
            for i in cosas.get(lado, []):
                try:
                    self.hub.pedir("DELETE", f"/api/{lado}/{i}")
                except ErrorHub as e:
                    decir(f"   (al borrar {lado}/{i}: {e})")
        for lista in cosas.values():
            del lista[:]

    def cerrar(self):
        self.cerrar_caso()
        for parar in reversed(self.abierto):
            try:
                parar()
            except Exception as e:
                decir(f"   (al parar: {e})")
        self._borrar(self.creado)


def evidencia(estado):
    return "; ".join(x.get("que", "") for x in estado.get("evidencia") or []) or "sin evidencia"


def credencial(direccion, nombre):
    return next((x["valor"] for x in direccion.get("campos") or [] if x["nombre"].startswith(nombre)), "")


def piezas(plan):
    """El plan dicho en una línea: qué hace con el vídeo y con cada audio."""
    v, audios = plan.get("video") or {}, plan.get("audio") or []
    return f"vídeo: {v.get('accion')}" + (f" ({v.get('motivo')})" if v.get("accion") != "copia" else "") + "; audio: " + \
        (", ".join(f"{a.get('accion')}" + (f" ({a.get('motivo')})" if a.get("accion") != "copia" else "") for a in audios) or "ninguno")


def audios_de(archivo):
    """Volumen medio (dB) de cada pista de audio de una captura, por orden."""
    try:
        sonda = subprocess.run([A.FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json",
                                str(archivo)], stdin=subprocess.DEVNULL, capture_output=True, timeout=40)
        cuantas = len(json.loads(sonda.stdout or b"{}").get("streams") or [])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    volumenes = []
    for n in range(cuantas):
        r = subprocess.run([A.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-i", str(archivo), "-map", f"0:a:{n}",
                            "-af", "volumedetect", "-f", "null", "-"], stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        medio = re.search(r"mean_volume:\s*(-?[\d.]+)", r.stderr.decode("utf-8", "replace"))
        volumenes.append(float(medio.group(1)) if medio else None)
    return volumenes


def reproduce(nav, pagina, segundos, fps, espera=45):
    """¿El navegador reproduce por WebRTC lo que le llega? (ok, texto), midiendo `segundos` desde que arranca."""
    def arranco():
        u = nav.ultima(pagina).get("rtc") or {}
        return (campo(u, "video", "fotogramas") or 0) > 5 and (campo(u, "audio", "energia") or 0) > 0
    recargas, t = 0, time.time()
    while not arranco() and time.time() - t < espera:
        if time.time() - t > 14 * (recargas + 1) and recargas < 2:        # la página no llegó a conectar: F5
            recargas += 1
            nav.recargar()
        time.sleep(0.5)
    if not arranco():
        u = nav.ultima(pagina)
        return False, "el navegador no llega a reproducir: " + json.dumps({k: u.get(k) for k in ("rtc", "errores", "error", "texto")},
                                                                         ensure_ascii=False)[:300]
    t0 = time.time()
    time.sleep(segundos)
    t1 = time.time()
    video = A.continuidad(A.tramo(nav.serie(pagina, "rtc", "video", "fotogramas"), t0, t1))
    sonido = A.continuidad(A.tramo(nav.serie(pagina, "rtc", "audio", "energia"), t0, t1))
    u = nav.ultima(pagina).get("rtc") or {}
    ritmo = video["avance"] / video["segundos"] if video["segundos"] else 0
    ok = ritmo >= A.WEBRTC_AVANCE_MIN * fps and (video["parada_max_s"] or 0) <= A.WEBRTC_PARADA_MAX \
        and sonido["avance"] > 0 and (sonido["parada_max_s"] or 0) <= A.WEBRTC_PARADA_MAX
    return ok, (f"{video['avance']:.0f} fotogramas en {video['segundos']:.1f} s ({ritmo:.1f} fps), mayor parada {video['parada_max_s']} s; "
                f"{campo(u, 'video', 'ancho')}×{campo(u, 'video', 'alto')} {campo(u, 'video', 'codec')}; audio {campo(u, 'audio', 'codec')} "
                f"que suena, mayor silencio {sonido['parada_max_s']} s")


def reproduce_hls(nav, pagina, segundos, espera=45):
    """¿El navegador reproduce la página HLS que entrega el hub? (ok, texto), midiendo `segundos` desde
    que empieza a reproducir de verdad (ver A.hls_reproduce). Lo que tardó en empezar se dice aparte."""
    if not esperar(lambda: A.hls_reproduce(nav.ultima(pagina).get("video")), espera, cada=0.5):
        u = nav.ultima(pagina)
        return False, "el navegador no llega a reproducir: " + json.dumps({k: u.get(k) for k in ("video", "errores", "error", "texto")},
                                                                         ensure_ascii=False)[:300]
    muestras = [(t, v) for t, v in nav.serie(pagina, "video") if v]
    empieza = next((t for t, v in muestras if A.hls_reproduce(v)), None)
    arranque = f"empezó {empieza - muestras[0][0]:.0f} s después de cargar la página; " if empieza is not None else ""
    t0 = time.time()
    time.sleep(segundos)
    t1 = time.time()
    avance = A.continuidad(A.tramo(nav.serie(pagina, "video", "tiempo"), t0, t1), 0.05)
    video = A.continuidad(A.tramo(nav.serie(pagina, "video", "bytes_video"), t0, t1))["avance"]
    sonido = A.continuidad(A.tramo(nav.serie(pagina, "video", "bytes_audio"), t0, t1))["avance"]
    u = nav.ultima(pagina).get("video") or {}
    ok = avance["segundos"] > 0 and avance["avance"] >= A.HLS_AVANCE_MIN * avance["segundos"] \
        and (avance["parada_max_s"] or 0) <= A.HLS_PARADA_MAX and video > 0 and sonido > 0
    return ok, (f"{arranque}reprodujo {avance['avance']:.1f} s de {avance['segundos']:.1f} s; mayor parada {avance['parada_max_s']} s; "
                f"{u.get('ancho')}×{u.get('alto')}; decodificó {video / 1e6:.1f} MB de vídeo y {sonido / 1e3:.0f} kB de audio")


def pagina_whep(nombre, direccion):
    ficha = credencial(direccion, "Token").split(":", 1)
    return {"nombre": nombre, "url": direccion["pagina"], "usuario": ficha[0] if len(ficha) == 2 else "",
            "clave": ficha[1] if len(ficha) == 2 else ""}


# ---------------------------------------------------------------- los casos
def trae(f):
    """Lo que el hub dice que trae una fuente, en una línea."""
    return "; ".join(filter(None, [campo(f, "pistas", "video", "texto"), *[a["texto"] for a in campo(f, "pistas", "audio") or []]]))


def llega(b, c, etiqueta, fid, salida, fps=None, exige_plan=None, f=None):
    """La fuente `fid`, ¿llega con audio y vídeo a una salida? Espera a que el hub la reciba (salvo que
    ya se sepa: `f`), la conecta a un destino nuevo (SRT o RTMP hacia un receptor, o HLS que lee un
    ffmpeg) y decodifica lo que llega."""
    if not f:
        f = b.fuente_lista(fid)
        if not c.comprobar(f"{etiqueta}: el hub recibe la fuente y sabe qué pistas trae", bool(f),
                           trae(f) if f else str(campo(b.hub.fuente(fid), "estado", "detalle"))):
            return False
    fps = fps or campo(f, "pistas", "video", "fps") or A.SENAL["fps"]
    if salida == "hls":
        did = b.nuevo("destinos", f"{etiqueta} → HLS", protocolo="hls", inicia="receptor")
        cosa = None
    else:
        cosa, did = b.empuje(salida, f"{etiqueta} → {NOMBRES[salida]}")
    plan = b.plan(fid, did)
    bien = True
    if exige_plan:
        bien = c.comprobar(f"{etiqueta} → {NOMBRES[salida]}: el plan dice qué copia y qué convierte", exige_plan(plan), piezas(plan))
    b.conectar(fid, did)
    if cosa is None:
        esperar(lambda: b.ruta(did).get("estado") in ("esperando_receptor", "enviando"), 40)
        cosa = b.lector(f"{etiqueta} → HLS", b.direccion("destinos", did)["url"])
    archivo, estado = b.capturar(cosa, did=did)
    ok, _ = b.senal(c, f"{etiqueta} → {NOMBRES[salida]}: llegan audio y vídeo", archivo, fps, estado)
    return ok and bien


def entregas(b, c):
    fid, _, fps = b.principal()
    frase = secrets.token_hex(10)
    b.secretos.add(frase)
    variantes = [("srt", "SRT con stream ID y passphrase", {"protocolo": "srt", "inicia": "receptor", "passphrase": frase, "latencia_ms": 300}),
                 ("srt", "SRT con puerto propio", {"protocolo": "srt", "inicia": "receptor", "puerto": b.puerto("srt"), "latencia_ms": 300}),
                 ("rtmp", "RTMP", {"protocolo": "rtmp", "inicia": "receptor"})]
    bien = {"srt": True, "rtmp": True}
    for proto, nombre, campos in variantes:
        did = b.nuevo("destinos", f"entrega {nombre}", **campos)
        b.conectar(fid, did)
        lista = esperar(lambda: (lambda e: e if e.get("estado") == "esperando_receptor" else None)(b.ruta(did)), 40)
        a = c.comprobar(f"{nombre}: sin nadie conectado, el hub lo enseña como «esperando receptor»", bool(lista),
                        evidencia(lista) if lista else f"dice «{b.ruta(did).get('estado')}»: {b.ruta(did).get('detalle')}")
        archivo, estado = b.capturar(b.lector(f"lector {nombre}", b.direccion("destinos", did)["url"]), did=did)
        s, _ = b.senal(c, f"{nombre}: el equipo que llama al hub recibe audio y vídeo", archivo, fps, estado)
        e = c.comprobar(f"{nombre}: mientras lee, el hub lo enseña como «enviando» y dice qué lo respalda",
                        estado.get("estado") == "enviando", f"«{estado.get('estado')}»: {evidencia(estado)}")
        bien[proto] = bien[proto] and a and s and e
    c.verificada("srt:origen", "srt:receptor", bien["srt"])
    c.verificada("srt:origen", "rtmp:receptor", bien["rtmp"])


def llamadas(b, c):
    fid, _, fps = b.principal()
    host = b.args.receptor

    # SRT: el hub llama a un codificador que está a la escucha
    puerto = b.puerto("srt")
    cam = Camara("srt", f"srt://0.0.0.0:{puerto}?mode=listener", b.sub("cámara a la escucha"), b.secretos)
    cam.start()
    b.abierto_caso.append(cam.parar)
    f1 = b.nuevo("fuentes", "codificador SRT a la escucha", protocolo="srt", inicia="hub", url=f"srt://{host}:{puerto}", latencia_ms=300)
    c.verificada("srt:hub", "srt:hub", llega(b, c, "SRT, el hub llama a un codificador a la escucha", f1, "srt"))

    # SRT: el hub llama a otro hub, con stream ID y passphrase (el otro hub es este mismo, por su salida SRT)
    frase = secrets.token_hex(10)
    b.secretos.add(frase)
    d_srt = b.nuevo("destinos", "salida SRT para otro hub", protocolo="srt", inicia="receptor", passphrase=frase, latencia_ms=300)
    b.conectar(fid, d_srt)
    partes = urllib.parse.urlsplit(b.direccion("destinos", d_srt)["url"])
    streamid = dict(urllib.parse.parse_qsl(partes.query))["streamid"]
    f2 = b.nuevo("fuentes", "otro hub por SRT", protocolo="srt", inicia="hub", url=f"srt://{partes.netloc}",
                 streamid=streamid, passphrase=frase, latencia_ms=300)
    c.verificada("srt:hub", "hls:receptor", llega(b, c, "SRT, el hub llama a otro hub con stream ID y passphrase", f2, "hls"))

    # RTMP: el hub lee de un servidor (la salida RTMP de este mismo hub)
    d_rtmp = b.nuevo("destinos", "salida RTMP para otro hub", protocolo="rtmp", inicia="receptor")
    b.conectar(fid, d_rtmp)
    f3 = b.nuevo("fuentes", "servidor RTMP", protocolo="rtmp", inicia="hub", url=b.direccion("destinos", d_rtmp)["url"])
    c.verificada("rtmp:hub", "rtmp:hub", llega(b, c, "RTMP, el hub lee de un servidor", f3, "rtmp"))

    # HLS: el hub lee una lista .m3u8 (la salida HLS de este mismo hub)
    d_hls = b.nuevo("destinos", "salida HLS para otro hub", protocolo="hls", inicia="receptor")
    b.conectar(fid, d_hls)
    f4 = b.nuevo("fuentes", "lista HLS", protocolo="hls", inicia="hub", url=b.direccion("destinos", d_hls)["url"])
    c.verificada("hls:hub", "srt:hub", llega(b, c, "HLS, el hub lee una lista .m3u8", f4, "srt"))

    # SRT: el codificador llama, pero a un puerto propio de esta fuente (sin stream ID) y con passphrase
    frase = secrets.token_hex(10)
    b.secretos.add(frase)
    etiqueta = "SRT, el codificador llama a un puerto propio del hub"
    f5, lista = b.camara("codificador SRT a un puerto propio", campos={"puerto": b.puerto("srt"), "passphrase": frase, "latencia_ms": 300})
    if c.comprobar(f"{etiqueta}: el hub recibe la fuente y sabe qué pistas trae", bool(lista),
                   trae(lista) if lista else str(campo(b.hub.fuente(f5), "estado", "detalle"))):
        c.verificada("srt:origen", "rtmp:hub", llega(b, c, etiqueta, f5, "rtmp", f=lista))


def plataforma_rtmps(b):
    """Un servidor RTMPS de pruebas: otro MediaMTX, solo con RTMPS y un certificado de usar y tirar.
    Devuelve (puerto, "") o (None, por qué no se puede montar)."""
    binario = b.args.mediamtx or next((str(x) for x in [*(pathlib.Path(r) / "bin" / "mediamtx" for r in b.args.registros),
                                                          pathlib.Path("/home/mediasat/streamer/bin/mediamtx")] if x.is_file()), "") \
        or shutil.which("mediamtx")
    if not binario:
        return None, "no se encuentra MediaMTX para hacer de plataforma (indicarlo con --mediamtx)"
    if not shutil.which("openssl"):
        return None, "falta openssl para crear el certificado de la plataforma de pruebas"
    carpeta, puerto = b.sub("plataforma RTMPS"), b.puerto("rtmp")
    hecho = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", carpeta / "clave.pem", "-out",
                            carpeta / "cert.pem", "-days", "2", "-subj", "/CN=plataforma-de-pruebas"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60)
    if hecho.returncode:
        return None, "openssl no pudo crear el certificado: " + hecho.stderr.decode("utf-8", "replace")[-160:]
    (carpeta / "plataforma.yml").write_text(
        # moq: no, porque desde la v1.21 MediaMTX abre si no los puertos 8892 y 8893 y deja un
        # certificado suyo (auto.crt, auto.key) en la carpeta desde la que se lanza
        "logLevel: warn\napi: no\nmetrics: no\npprof: no\nplayback: no\nrtsp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\n"
        f"rtmp: yes\nrtmpEncryption: strict\nrtmpsAddress: :{puerto}\nrtmpServerKey: {carpeta / 'clave.pem'}\n"
        f"rtmpServerCert: {carpeta / 'cert.pem'}\npaths:\n  all_others:\n")
    servidor = A.Proceso("plataforma", [binario, carpeta / "plataforma.yml"], carpeta, b.secretos).lanzar()
    b.abierto_caso.append(servidor.parar)
    if not esperar(lambda: not A.puerto_libre(puerto, udp=False), 12, cada=0.25) or not servidor.vivo():
        return None, f"la plataforma de pruebas no arranca (ver {carpeta / 'plataforma.log'})"
    return puerto, ""


def rtmps_del_hub(b, c):
    """El propio hub por RTMPS: un codificador publica en él cifrado y un cliente lee de él cifrado."""
    f = b.nuevo("fuentes", "codificador RTMPS", protocolo="rtmp", inicia="origen")
    d = b.direccion("fuentes", f)
    if not d.get("url_rtmps"):
        c.comprobar("RTMPS en el propio hub: publicar en él y leer de él", None,
                    "este hub no tiene certificado para RTMPS (rtmps_puerto, rtmps_cert y rtmps_clave en config.json)")
        return

    def atiende():
        prueba = b.hub.pedir("POST", f"/api/fuentes/{f}/probar")
        paso = next((p for p in prueba.get("pasos") or [] if "RTMPS" in f"{p.get('que')} {p.get('detalle')}"), None)
        return (prueba, paso) if paso and paso.get("ok") else None
    listo = esperar(atiende, 30)
    if not c.comprobar("RTMPS de entrada: «Probar la conexión» de la fuente confirma que el hub atiende RTMPS", bool(listo),
                       listo[1]["detalle"] if listo else str(b.hub.pedir("POST", f"/api/fuentes/{f}/probar").get("resumen"))):
        return
    cam = Camara("rtmp", d["url_rtmps"], b.sub("codificador RTMPS"), b.secretos)
    cam.start()
    b.abierto_caso.append(cam.parar)
    etiqueta = "RTMPS, el codificador publica cifrado en el hub"
    lista = b.fuente_lista(f)
    if not c.comprobar(f"{etiqueta}: el hub recibe la fuente y sabe qué pistas trae", bool(lista),
                       trae(lista) if lista else str(campo(b.hub.fuente(f), "estado", "detalle"))):
        return
    fps = campo(lista, "pistas", "video", "fps") or A.SENAL["fps"]
    entra = llega(b, c, etiqueta, f, "srt", f=lista)
    did = b.nuevo("destinos", "entrega RTMPS", protocolo="rtmp", inicia="receptor")
    b.conectar(f, did)
    esperar(lambda: b.ruta(did).get("estado") in ("esperando_receptor", "enviando"), 40)
    lector = b.lector("lector RTMPS del hub", b.direccion("destinos", did)["url_rtmps"])
    archivo, estado = b.capturar(lector, did=did, cerrar=False)
    por = sorted({str(x.get("protocolo")) for x in b.receptores(did)})
    lector.cerrar()
    time.sleep(0.3)
    sale, _ = b.senal(c, "RTMPS de entrega: quien lee del hub por RTMPS recibe audio y vídeo", archivo, fps, estado)
    dice = c.comprobar("RTMPS de entrega: mientras lee, el hub lo enseña como «enviando» y sabe que el lector va por RTMPS",
                       estado.get("estado") == "enviando" and por == ["RTMPS"],
                       f"«{estado.get('estado')}»: {evidencia(estado)}; lectores por {', '.join(por) or 'ninguno'}")
    c.verificada("rtmp:origen", "srt:hub", entra)
    c.verificada("rtmp:origen", "rtmp:receptor", sale and dice)


def rtmps(b, c):
    fid, _, fps = b.principal()
    rtmps_del_hub(b, c)
    puerto, motivo = plataforma_rtmps(b)
    if not puerto:
        c.comprobar("RTMPS: publicar en una plataforma y leer de un servidor", None, motivo)
        return
    clave = secrets.token_hex(12)
    b.secretos.add(clave)
    base = f"rtmps://{b.args.receptor}:{puerto}/live"
    did = b.nuevo("destinos", "plataforma RTMPS", protocolo="rtmp", inicia="hub", url=base, clave=clave,
                  reconexion={"cada_s": 2, "max_intentos": 0})
    prueba = b.hub.pedir("POST", f"/api/destinos/{did}/probar")
    tls = next((p for p in prueba.get("pasos") or [] if "TLS" in p["que"]), {})
    saludo = next((p for p in prueba.get("pasos") or [] if p["que"] == "Saludo RTMP"), {})
    c.comprobar("RTMPS: «Probar la conexión» llega hasta el saludo RTMP y avisa de que el certificado de pruebas no es de fiar",
                saludo.get("ok") is True and tls.get("ok") is None and prueba.get("ok") is None, str(prueba.get("resumen")))
    b.conectar(fid, did)
    archivo, estado = b.capturar(b.lector("lector de la plataforma RTMPS", f"{base}/{clave}"), did=did)
    sale, _ = b.senal(c, "RTMPS de salida: lo que el hub publica en la plataforma llega con audio y vídeo", archivo, fps, estado)
    c.comprobar("RTMPS de salida: el hub lo enseña como «enviando» y dice qué lo respalda", estado.get("estado") == "enviando",
                f"«{estado.get('estado')}»: {evidencia(estado)}")
    # y al revés: el hub lee de ese servidor lo que él mismo publicó
    f2 = b.nuevo("fuentes", "servidor RTMPS", protocolo="rtmp", inicia="hub", url=base, clave=clave)
    entra = llega(b, c, "RTMPS de entrada, el hub lee de un servidor", f2, "srt")
    c.verificada("srt:origen", "rtmp:hub", sale)
    c.verificada("rtmp:hub", "srt:hub", entra)


def webrtc(b, c):
    if not b.node:
        c.comprobar("WebRTC: WHIP de entrada y reenvío WHIP", None, "sin Node con Playwright no hay navegador")
        return
    fid, _, fps = b.principal()

    # un navegador publica por WHIP su cámara y su micrófono (los de mentira de Chromium, con un tono)
    tono = b.tono()
    f_nav = b.nuevo("fuentes", "navegador que publica (WHIP)", protocolo="webrtc", inicia="origen")
    d = b.direccion("fuentes", f_nav)
    t0 = time.time()                                    # lo que haría quien la acaba de crear: «Probar la conexión»
    prueba = esperar(lambda: (lambda r: r if r.get("ok") else None)(b.hub.pedir("POST", f"/api/fuentes/{f_nav}/probar")), 20, cada=0.2)
    c.comprobar("WHIP: «Probar la conexión» dice que el hub ya escucha en la dirección que entrega", bool(prueba),
                f"a los {time.time() - t0:.1f} s de crear la fuente: {prueba.get('resumen')}" if prueba
                else str(b.hub.pedir("POST", f"/api/fuentes/{f_nav}/probar").get("resumen")))
    origen = d["url"].rsplit("/", 2)[0]
    nav = b.navegador("navegador que publica", [{"nombre": "publica", "url": f"{origen}/__camara_de_pruebas", "publicar": {
        "whip": d["url"], "token": credencial(d, "Token"), "codec": "H264", "audio_wav": str(tono)}}])
    f = b.fuente_lista(f_nav, 60)
    if not f:
        u = nav.ultima("publica")
        no_puede = "no codifica" in json.dumps(u, ensure_ascii=False)
        c.comprobar("WHIP: el hub recibe lo que publica un navegador", None if no_puede else False,
                    f"el hub dice: {campo(b.hub.fuente(f_nav), 'estado', 'detalle')}; el navegador: "
                    + json.dumps({k: u.get(k) for k in ("rtc", "errores", "error")}, ensure_ascii=False)[:300])
    else:
        c.comprobar("WHIP: el hub recibe lo que publica un navegador y sabe qué pistas trae", True, trae(f))
        copia = lambda p: campo(p, "video", "accion") == "copia" and all(a.get("accion") == "copia" for a in p.get("audio") or [])
        a_aac = lambda p: campo(p, "video", "accion") == "copia" and [a.get("accion") for a in p.get("audio") or []] == ["convierte"]
        c.verificada("webrtc:origen", "srt:hub", llega(b, c, "WHIP desde un navegador", f_nav, "srt", FPS_NAVEGADOR, copia, f))
        c.verificada("webrtc:origen", "rtmp:hub", llega(b, c, "WHIP desde un navegador", f_nav, "rtmp", FPS_NAVEGADOR, a_aac, f))
        # un navegador empieza con la imagen pequeña y la sube en sus primeros segundos, sin volver a conectar
        envia = lambda: (lambda v: (v.get("ancho"), v.get("alto")))(campo(nav.ultima("publica"), "rtc", "envia", "video") or {})
        visto = lambda: (lambda v: (v.get("ancho"), v.get("alto")))(campo(b.hub.fuente(f_nav), "pistas", "video") or {})
        al_dia = esperar(lambda: all(envia()) and visto() == envia(), 60, cada=1)
        c.comprobar("WHIP: cuando el navegador sube la resolución, el hub lo refleja", bool(al_dia),
                    f"al inspeccionarla era {campo(f, 'pistas', 'video', 'texto')}; el navegador envía ahora "
                    f"{envia()[0]}×{envia()[1]} y el hub enseña {campo(b.hub.fuente(f_nav), 'pistas', 'video', 'texto')}")

    # reenvío WHIP: el hub publica la cámara principal en otro servidor WHIP (una fuente WHIP de este mismo hub)
    f_w = b.nuevo("fuentes", "servidor WHIP de otro equipo", protocolo="webrtc", inicia="origen")
    dw = b.direccion("fuentes", f_w)
    d_fwd = b.nuevo("destinos", "reenvío WHIP", protocolo="webrtc", inicia="hub", url=dw["url"], clave=credencial(dw, "Token"))
    plan = b.plan(fid, d_fwd)
    p_ok = c.comprobar("Reenvío WHIP: el plan copia el vídeo y convierte el audio a Opus",
                       campo(plan, "video", "accion") == "copia" and [a.get("accion") for a in plan.get("audio") or []] == ["convierte"],
                       piezas(plan))
    b.conectar(fid, d_fwd)
    fw = b.fuente_lista(f_w, 60)
    estado = b.ruta(d_fwd)
    r_ok = c.comprobar("Reenvío WHIP: el otro servidor recibe la señal", bool(fw),
                       trae(fw) + f". El hub lo enseña como «{estado.get('estado')}»: {estado.get('detalle')}" if fw
                       else f"el otro servidor dice: {campo(b.hub.fuente(f_w), 'estado', 'detalle')}; el hub enseña el reenvío como "
                            f"«{estado.get('estado')}»: {estado.get('detalle')}")
    c.verificada("srt:origen", "webrtc:hub", p_ok and r_ok)
    if fw:                                              # y lo que llegó por WHIP se ve por WHEP en un navegador
        d_whep = b.nuevo("destinos", "navegador que mira lo reenviado", protocolo="webrtc", inicia="receptor")
        b.conectar(f_w, d_whep)
        esperar(lambda: b.ruta(d_whep).get("estado") in ("esperando_receptor", "enviando"), 40)
        mira = b.navegador("navegador que mira", [pagina_whep("mira", b.direccion("destinos", d_whep))])
        ok, texto = reproduce(mira, "mira", 10, fps)
        c.verificada("webrtc:origen", "webrtc:receptor", c.comprobar("WHIP → WHEP: un navegador reproduce audio y vídeo de lo que entró por WHIP", ok, texto))


def perfiles(b, c):
    fid, _, fps = b.principal()
    pid = b.hub.pedir("POST", "/api/perfiles", {"nombre": PREFIJO + "360p · 800 kb/s", "video": {"alto": 360, "kbps": 800},
                                                 "audio": {"kbps": 96}})["id"]
    b.creado["perfiles"].append(pid)
    antes = b.conversiones()
    receptores, planes = {}, {}
    for proto in ("srt", "rtmp"):                       # el segundo se planea con el primero ya activo
        receptores[proto], did = b.empuje(proto, f"destino {NOMBRES[proto]} a 360p", perfil=pid)
        planes[proto] = b.plan(fid, did)
        b.conectar(fid, did)
        receptores[proto] = (receptores[proto], did)
        esperar(lambda: b.ruta(did).get("estado") == "enviando", 40)
    v = planes["srt"].get("video") or {}
    c.comprobar("Antes de activar, el plan dice que el vídeo se convierte a 640×360 y por qué",
                v.get("accion") == "convierte" and "360" in str(v.get("a")) and bool(v.get("motivo")),
                f"{v.get('de')} → {v.get('a')}. {v.get('motivo')} {planes['srt'].get('carga') or ''}".strip())
    c.comprobar("El plan del segundo destino avisa de que usa la conversión del primero", bool(planes["rtmp"].get("comparte")),
                str(planes["rtmp"].get("carga")))

    def a_360(archivo):
        def exige(d):
            kbps = archivo.stat().st_size * 8 / 1000 / d["segundos"] if d.get("segundos") else 0
            ok = (campo(d, "video", "ancho"), campo(d, "video", "alto")) == (640, 360) and 300 <= kbps <= 1500
            return ok, f"{kbps:.0f} kb/s en total (el perfil pide 800 de vídeo y 96 de audio; la fuente trae {A.SENAL['kbps']})"
        return exige
    bien = {}
    for proto, (receptor, did) in receptores.items():
        archivo, estado = b.capturar(receptor, did=did)
        bien[proto], _ = b.senal(c, f"{NOMBRES[proto]}: llega a 640×360 y con el bitrate del perfil", archivo, fps, estado,
                                 a_360(archivo) if archivo else None)
    nuevas = [x for x in b.conversiones() if x not in antes]
    una = c.comprobar("Los dos destinos comparten una sola conversión", len(nuevas) == 1, f"{len(nuevas)} conversión nueva para dos destinos")
    c.verificada("srt:origen", "srt:hub", bien.get("srt") and una)


def fotogramas(b, c):
    fid, f = b.camara("cámara con fotogramas B (RTMP)", "rtmp", con_b=True)
    if not c.comprobar("El hub recibe la fuente con fotogramas B", bool(f),
                       str(campo(f, "pistas", "video", "texto")) if f else str(campo(b.hub.fuente(fid), "estado", "detalle"))):
        return
    fps = campo(f, "pistas", "video", "fps") or A.SENAL["fps"]
    d_hls = b.nuevo("destinos", "HLS de la fuente con fotogramas B", protocolo="hls", inicia="receptor")
    ph = b.plan(fid, d_hls)
    c.comprobar("Hacia HLS el plan copia el vídeo: HLS admite fotogramas B", campo(ph, "video", "accion") == "copia", piezas(ph))
    b.conectar(fid, d_hls)
    d_web = b.nuevo("destinos", "WebRTC de la fuente con fotogramas B", protocolo="webrtc", inicia="receptor")
    pw = b.plan(fid, d_web)
    v = pw.get("video") or {}
    c.comprobar("Hacia WebRTC el plan convierte el vídeo y dice que es por los fotogramas B",
                v.get("accion") == "convierte" and "fotogramas B" in str(v.get("motivo")), f"{v.get('motivo')} {pw.get('carga') or ''}".strip())
    b.conectar(fid, d_web)
    esperar(lambda: b.ruta(d_hls).get("estado") in ("esperando_receptor", "enviando"), 40)
    archivo, estado = b.capturar(b.lector("lector HLS con fotogramas B", b.direccion("destinos", d_hls)["url"]), did=d_hls)
    b.senal(c, "HLS: llegan audio y vídeo, en copia", archivo, fps, estado)
    if not b.node:
        c.comprobar("WebRTC: un navegador reproduce la señal convertida", None, "sin Node con Playwright no hay navegador")
        return
    esperar(lambda: b.ruta(d_web).get("estado") in ("esperando_receptor", "enviando"), 60)
    nav = b.navegador("navegador con fotogramas B", [pagina_whep("mira", b.direccion("destinos", d_web))])
    ok, texto = reproduce(nav, "mira", 12, fps)
    c.comprobar("WebRTC: un navegador reproduce audio y vídeo de la señal convertida", ok, texto)


def audio(b, c):
    fid, f = b.camara("cámara con dos pistas de audio (SRT)", "srt", audios=2)
    pistas = [a["texto"] for a in campo(f, "pistas", "audio") or []]
    if not c.comprobar("El hub ve las dos pistas de audio de la fuente", len(pistas) == 2,
                       "; ".join(pistas) if f else str(campo(b.hub.fuente(fid), "estado", "detalle"))):
        return
    fps = campo(f, "pistas", "video", "fps") or A.SENAL["fps"]
    r_todas, d_todas = b.empuje("srt", "destino SRT con todas las pistas", audio="todas")
    r_dos, d_dos = b.empuje("srt", "destino SRT con la pista 2", audio="2")
    d_hls = b.nuevo("destinos", "HLS con la pista 2", protocolo="hls", inicia="receptor", audio="2")
    p_dos = b.plan(fid, d_dos)
    van = [a.get("pista") for a in p_dos.get("audio") or [] if a.get("accion") != "omite"]
    fuera = [a for a in p_dos.get("audio") or [] if a.get("accion") == "omite"]
    c.comprobar("El plan del destino con la pista 2 dice cuál lleva y cuál se queda fuera, y por qué",
                van == [2] and len(fuera) == 1 and bool(fuera[0].get("motivo")) and "no se envía" in str(p_dos.get("resumen")),
                f"{p_dos.get('resumen')} " + " ".join(f"{a.get('nombre')}: {a.get('accion')} ({a.get('motivo')})" for a in p_dos.get("audio") or []))
    p_hls = b.plan(fid, d_hls)
    c.comprobar("Para HLS, que es el reproductor quien viene a por la señal, el hub prepara una señal con esa sola pista",
                [a.get("pista") for a in p_hls.get("audio") or [] if a.get("accion") != "omite"] == [2] and "pistas" in str(p_hls.get("carga")),
                f"{p_hls.get('resumen')} {p_hls.get('carga')}")
    for did in (d_todas, d_dos, d_hls):
        b.conectar(fid, did)
    archivo, estado = b.capturar(r_todas, did=d_todas)
    b.senal(c, "«Todas»: llegan audio y vídeo", archivo, fps, estado)
    todas = audios_de(archivo) if archivo else []
    distintas = len(todas) == 2 and None not in todas and todas[0] - todas[1] >= 6
    c.comprobar("«Todas»: llegan las dos pistas, cada una con su sonido", distintas,
                f"volumen medio de cada pista: {', '.join(f'{x} dB' for x in todas) or 'ninguna'} (la segunda se emite 12 dB más baja)")

    def es_la_segunda(archivo):
        def exige(d):
            v = audios_de(archivo)
            if not distintas:
                return False, "sin las dos pistas de referencia no se puede saber cuál es"
            ok = len(v) == 1 and v[0] is not None and abs(v[0] - todas[1]) < abs(v[0] - todas[0])
            return ok, f"{len(v)} pista de audio, a {v[0] if v else '?'} dB (la primera va a {todas[0]} dB y la segunda a {todas[1]} dB)"
        return exige
    archivo, estado = b.capturar(r_dos, did=d_dos)
    dos, _ = b.senal(c, "«Pista 2» por SRT: llega solo la segunda pista", archivo, fps, estado, es_la_segunda(archivo) if archivo else None)
    esperar(lambda: b.ruta(d_hls).get("estado") in ("esperando_receptor", "enviando"), 40)
    archivo, estado = b.capturar(b.lector("lector HLS pista 2", b.direccion("destinos", d_hls)["url"]), did=d_hls)
    hls, _ = b.senal(c, "«Pista 2» por HLS: llega solo la segunda pista", archivo, fps, estado, es_la_segunda(archivo) if archivo else None)
    c.verificada("srt:origen", "srt:hub", distintas and dos)
    c.verificada("srt:origen", "hls:receptor", hls)


def corte_de_fuente(b, c):
    fid, f = b.camara("cámara que se corta (SRT)")
    if not c.comprobar("El hub recibe la fuente", bool(f), trae(f) if f else str(campo(b.hub.fuente(fid), "estado", "detalle"))):
        return
    fps = campo(f, "pistas", "video", "fps") or A.SENAL["fps"]
    pid = b.hub.pedir("POST", "/api/perfiles", {"nombre": PREFIJO + "corte · 360p", "video": {"alto": 360, "kbps": 800},
                                                 "audio": {"kbps": 96}})["id"]
    b.creado["perfiles"].append(pid)
    r_srt, d_srt = b.empuje("srt", "receptor SRT de la fuente que se corta")
    r_rtmp, d_rtmp = b.empuje("rtmp", "servidor RTMP a 360p de la fuente que se corta", perfil=pid)
    d_hls = b.nuevo("destinos", "reproductor HLS de la fuente que se corta", protocolo="hls", inicia="receptor")
    rutas = {"SRT en copia": d_srt, "RTMP con conversión a 360p": d_rtmp, "HLS": d_hls}
    for did in rutas.values():
        b.conectar(fid, did)
    en_marcha = lambda: b.ruta(d_srt).get("estado") == "enviando" and b.ruta(d_rtmp).get("estado") == "enviando" \
        and b.ruta(d_hls).get("estado") in ("esperando_receptor", "enviando") and r_srt.bytes() > 0 and r_rtmp.bytes() > 0
    if not c.comprobar("Las tres rutas de la fuente están en marcha (SRT en copia, RTMP con conversión y HLS)", bool(esperar(en_marcha, 60)),
                       "; ".join(f"{k}: «{b.ruta(d).get('estado')}»" for k, d in rutas.items())):
        return
    time.sleep(4)
    antes = b.procesos("envio", "conversion")

    t_corte = time.time()
    b.camaras[fid].parar()                              # el codificador se apaga
    sin_fuente = lambda: campo(b.hub.fuente(fid), "estado", "estado") == "esperando_fuente" \
        and all(b.ruta(d).get("estado") == "esperando_fuente" for d in rutas.values())
    visto = esperar(sin_fuente, 30, cada=0.5)
    e = b.ruta(d_srt)
    c.comprobar("Al cortarse la fuente, el hub la enseña a ella y a sus tres rutas como «esperando fuente», y dice en qué se basa", bool(visto),
                f"a los {time.time() - t_corte:.1f} s del corte: {e.get('detalle')}; {evidencia(e)}" if visto
                else "; ".join(f"{k}: «{b.ruta(d).get('estado')}» ({b.ruta(d).get('detalle')})" for k, d in rutas.items()))
    time.sleep(max(0.0, 20 - (time.time() - t_corte)))
    durante = b.procesos("envio", "conversion")
    c.comprobar("Con la fuente cortada no quedan procesos de envío ni de conversión dando vueltas", durante == 0,
                f"{antes} procesos antes del corte, {durante} a los 20 s")
    for receptor in (r_srt, r_rtmp):                    # los receptores siguen ahí, a la escucha, como equipos de verdad
        receptor.p.parar()
        receptor.abrir()

    t_vuelta = time.time()
    b.encender(fid)                                     # el codificador vuelve
    vuelve = lambda: en_marcha() and campo(b.hub.fuente(fid), "estado", "estado") == "recibiendo"
    de_vuelta = esperar(vuelve, 75, cada=0.5)
    c.comprobar("Al volver la fuente, las tres rutas se recuperan solas", bool(de_vuelta),
                f"a los {time.time() - t_vuelta:.1f} s de volver el codificador, tras {t_vuelta - t_corte:.0f} s de corte" if de_vuelta
                else "; ".join(f"{k}: «{b.ruta(d).get('estado')}» ({b.ruta(d).get('detalle')})" for k, d in rutas.items()))
    if not de_vuelta:
        return
    archivo, estado = b.capturar(r_srt, did=d_srt, segundos=8)
    b.senal(c, "SRT: tras la vuelta llegan audio y vídeo", archivo, fps, estado)
    archivo, estado = b.capturar(r_rtmp, did=d_rtmp, segundos=2)
    b.senal(c, "RTMP: tras la vuelta llega a 640×360, con la conversión otra vez en marcha", archivo, fps, estado,
            lambda d: ((campo(d, "video", "ancho"), campo(d, "video", "alto")) == (640, 360), "con el perfil a 360p"))
    archivo, estado = b.capturar(b.lector("lector HLS tras el corte", b.direccion("destinos", d_hls)["url"]), did=d_hls)
    b.senal(c, "HLS: tras la vuelta llegan audio y vídeo", archivo, fps, estado)
    despues = b.procesos("envio", "conversion")
    c.comprobar("Tras la vuelta hay los mismos procesos que antes del corte: nada duplicado", despues == antes,
                f"{antes} antes del corte y {despues} después")


def reconexion(b, c):
    fid, _, fps = b.principal()
    puerto, secreto = b.puerto("srt"), secrets.token_hex(12)
    b.secretos.add(secreto)
    did = b.nuevo("destinos", "receptor apagado (tope de 3 intentos)", protocolo="srt", inicia="hub", passphrase=secreto,
                  url=f"srt://{b.args.receptor}:{puerto}", latencia_ms=300, reconexion={"cada_s": 1, "max_intentos": 3})
    b.conectar(fid, did)
    t0 = time.time()
    intentando = esperar(lambda: (lambda e: e if e.get("estado") == "reconectando" and e.get("evidencia") else None)(b.ruta(did)), 30)
    c.comprobar("Con el receptor apagado, el hub lo enseña como «reconectando» mientras lo intenta, y dice en qué se basa", bool(intentando),
                f"{intentando.get('detalle')}; {evidencia(intentando)}" if intentando
                else f"dice «{b.ruta(did).get('estado')}»: {b.ruta(did).get('detalle')}; {evidencia(b.ruta(did))}")
    error = esperar(lambda: (lambda e: e if e.get("estado") == "error" else None)(b.ruta(did)), 120, cada=1)
    c.comprobar("Agotados los 3 intentos, el hub deja de intentarlo y lo enseña como «error», con el motivo", bool(error),
                f"a los {time.time() - t0:.0f} s: {error.get('detalle')}; {evidencia(error)}" if error
                else f"sigue en «{b.ruta(did).get('estado')}»: {b.ruta(did).get('detalle')}")
    receptor = A.Receptor("srt", puerto, secreto, b.sub("receptor que se enciende tarde"))
    b.abierto_caso.append(receptor.cerrar)
    receptor.abrir()
    time.sleep(6)
    quieto = b.ruta(did).get("estado") == "error" and receptor.bytes() == 0
    c.comprobar("Aunque el receptor se encienda, el hub no vuelve a intentarlo por su cuenta", quieto,
                f"6 s después sigue en «{b.ruta(did).get('estado')}» y al receptor le han llegado {receptor.bytes()} bytes")
    b.hub.pedir("POST", f"/api/rutas/{did}/reintentar")
    archivo, estado = b.capturar(receptor, did=did, espera=45)
    b.senal(c, "Al pedir «reintentar», la salida arranca y llegan audio y vídeo", archivo, fps, estado)


def cambio(b, c):
    fid, f, fps = b.principal()
    f2, ff2 = b.camara("segunda cámara (RTMP, 640×360)", "rtmp", alto=360)
    if not c.comprobar("El hub recibe la segunda cámara", bool(ff2), str(campo(ff2, "pistas", "video", "texto"))):
        return
    receptor, did = b.empuje("srt", "destino al aire")
    b.conectar(fid, did)
    archivo, estado = b.capturar(receptor, did=did, segundos=6, cerrar=False)
    if not c.comprobar("El destino está al aire con la cámara principal", bool(archivo) and estado.get("estado") == "enviando",
                       f"«{estado.get('estado')}»: {evidencia(estado)}"):
        return
    try:
        b.conectar(f2, did)
        c.comprobar("Cambiar la fuente de un destino al aire sin confirmar no hace nada", False, "el hub aceptó el cambio sin confirmación")
    except ErrorHub as e:
        detalle = e.detalle if isinstance(e.detalle, dict) else {}
        c.comprobar("Cambiar la fuente de un destino al aire sin confirmar no hace nada, y el hub dice cuál está al aire",
                    e.codigo == 409 and detalle.get("sustituye") == fid and f["nombre"] in str(detalle.get("mensaje"))
                    and b.ruta(did).get("estado") == "enviando", str(detalle.get("mensaje") or e))
    if b.args.sesion_operador:
        try:
            A.Hub(b.args.hub, b.args.sesion_operador).pedir("PUT", f"/api/rutas/{did}", {"fuente": f2, "sustituye": fid})
            c.comprobar("Un operador no puede cambiar la fuente de un destino al aire", False, "el hub lo permitió")
        except ErrorHub as e:
            c.comprobar("Un operador no puede cambiar la fuente de un destino al aire", e.codigo == 403, str(e.detalle))
    b.conectar(f2, did, sustituye=fid)
    receptor.p.parar()                                  # la conexión de antes se cierra; el receptor vuelve a escuchar
    receptor.abrir()
    archivo, estado = b.capturar(receptor, did=did, segundos=8)
    b.senal(c, "Confirmando cuál se sustituye, el destino pasa a la segunda cámara", archivo, campo(ff2, "pistas", "video", "fps") or fps, estado,
            lambda d: ((campo(d, "video", "ancho"), campo(d, "video", "alto")) == (640, 360), "la segunda cámara emite a 640×360"))
    cambios = b.hub.pedir("GET", "/api/cambios?limite=20")
    filas = cambios if isinstance(cambios, list) else cambios.get("cambios") or []
    anotado = next((x for x in filas if "fuente al aire" in str(x.get("accion", "")).lower()), None)
    c.comprobar("El cambio queda en el registro, con quién lo hizo", bool(anotado),
                f"{anotado.get('accion')}: {anotado.get('objeto')} ({anotado.get('detalle')}), por {anotado.get('quien')}" if anotado
                else "no aparece en /api/cambios")


def a_todas_las_salidas(b, c, lado, fid, fps):
    """Una fuente conectada a la vez a las siete formas de salida: ¿llega a todas con audio y vídeo?"""
    de = ENTRADAS[lado]
    corto = "matriz " + lado.replace(":", " ")          # para los nombres que se crean en el hub, que admiten 80 caracteres
    frase = secrets.token_hex(10)
    b.secretos.add(frase)
    r_srt, d_srt = b.empuje("srt", f"{corto} → receptor SRT")
    r_rtmp, d_rtmp = b.empuje("rtmp", f"{corto} → servidor RTMP")
    f_w = b.nuevo("fuentes", f"{corto} → servidor WHIP de otro equipo", protocolo="webrtc", inicia="origen")
    dw = b.direccion("fuentes", f_w)
    destinos = {
        "srt:hub": d_srt,
        "srt:receptor": b.nuevo("destinos", f"{corto} → SRT que abre el receptor", protocolo="srt", inicia="receptor",
                                passphrase=frase, latencia_ms=300),
        "rtmp:hub": d_rtmp,
        "rtmp:receptor": b.nuevo("destinos", f"{corto} → RTMP que lee un cliente", protocolo="rtmp", inicia="receptor"),
        "hls:receptor": b.nuevo("destinos", f"{corto} → HLS", protocolo="hls", inicia="receptor"),
        "webrtc:receptor": b.nuevo("destinos", f"{corto} → WebRTC que abre un navegador", protocolo="webrtc", inicia="receptor"),
        "webrtc:hub": b.nuevo("destinos", f"{corto} → reenvío WHIP", protocolo="webrtc", inicia="hub", url=dw["url"],
                              clave=credencial(dw, "Token"))}
    planes = {k: b.plan(fid, did) for k, did in destinos.items()}
    c.comprobar(f"{de}: antes de activar, el plan de cada una de las siete salidas dice qué copia y qué convierte",
                all(p.get("posible", True) and not p.get("provisional") for p in planes.values()),
                " · ".join(f"{SALIDAS[k]}: {piezas(p)}" for k, p in planes.items()))
    for did in destinos.values():                       # las siete a la vez
        b.conectar(fid, did)
    entregadas = ("srt:receptor", "rtmp:receptor", "hls:receptor", "webrtc:receptor")
    esperar(lambda: all(b.ruta(destinos[k]).get("estado") in ("esperando_receptor", "enviando") for k in entregadas), 45)
    cosas = {"srt:hub": r_srt, "rtmp:hub": r_rtmp}
    for k in ("srt:receptor", "rtmp:receptor", "hls:receptor"):
        cosas[k] = b.lector(f"{corto} → lector {k.replace(':', ' ')}", b.direccion("destinos", destinos[k])["url"])
    mira = b.navegador(f"{corto} → navegador que mira", [pagina_whep("mira", b.direccion("destinos", destinos["webrtc:receptor"]))]) \
        if b.node else None
    b.esperar_senal(list(cosas.values()))
    if mira:                                            # mientras los demás capturan, el navegador mide lo que reproduce
        ok_whep, texto_whep = reproduce(mira, "mira", 8, fps)
    else:
        time.sleep(8)
    estados = {k: b.ruta(did) for k, did in destinos.items()}
    for x in cosas.values():
        x.cerrar()
    time.sleep(0.3)
    for k, x in cosas.items():
        ok, _ = b.senal(c, f"{de} → {SALIDAS[k]}: llegan audio y vídeo", x.archivo if x.bytes() else None, fps, estados[k])
        c.verificada(lado, k, ok)
    que = f"{de} → {SALIDAS['webrtc:receptor']}: un navegador reproduce audio y vídeo"
    if mira:
        c.verificada(lado, "webrtc:receptor", c.comprobar(que, ok_whep, texto_whep))
        mira.parar()
    else:
        c.comprobar(que, None, "sin Node con Playwright no hay navegador")
    fw = b.fuente_lista(f_w, 45)
    e = estados["webrtc:hub"]
    llego = bool(fw) and bool(campo(fw, "pistas", "video")) and bool(campo(fw, "pistas", "audio"))
    c.verificada(lado, "webrtc:hub", c.comprobar(
        f"{de} → {SALIDAS['webrtc:hub']}: el otro servidor recibe audio y vídeo", llego,
        trae(fw) if fw else f"el otro servidor dice: {campo(b.hub.fuente(f_w), 'estado', 'detalle')}; el hub enseña el reenvío como "
                            f"«{e.get('estado')}»: {e.get('detalle')}"))


def matriz(b, c):
    fid, f, _ = b.principal()
    host = b.args.receptor

    def a_la_escucha():
        puerto = b.puerto("srt")
        cam = Camara("srt", f"srt://0.0.0.0:{puerto}?mode=listener", b.sub("matriz, cámara a la escucha"), b.secretos)
        cam.start()
        b.abierto_caso.append(cam.parar)
        return b.nuevo("fuentes", "matriz, codificador SRT a la escucha", protocolo="srt", inicia="hub",
                       url=f"srt://{host}:{puerto}", latencia_ms=300), None

    def de_un_servidor(proto):                          # el servidor es este mismo hub, por una salida de la cámara principal
        d = b.nuevo("destinos", f"matriz, salida {NOMBRES[proto]} que hace de servidor", protocolo=proto, inicia="receptor")
        b.conectar(fid, d)
        return b.nuevo("fuentes", f"matriz, servidor {NOMBRES[proto]}", protocolo=proto, inicia="hub",
                       url=b.direccion("destinos", d)["url"]), None

    def navegador():
        if not b.node:
            return None, "sin Node con Playwright no hay navegador"
        f_nav = b.nuevo("fuentes", "matriz, navegador que publica (WHIP)", protocolo="webrtc", inicia="origen")
        d = b.direccion("fuentes", f_nav)
        esperar(lambda: b.hub.pedir("POST", f"/api/fuentes/{f_nav}/probar").get("ok"), 20, cada=0.2)
        # a 640×360: la subida de resolución del navegador acaba antes y la ronda carga menos el equipo
        nav = b.navegador("matriz, navegador que publica", [{"nombre": "publica", "url": d["url"].rsplit("/", 2)[0] + "/__camara_de_pruebas",
                          "publicar": {"whip": d["url"], "token": credencial(d, "Token"), "codec": "H264", "audio_wav": str(b.tono()),
                                       "ancho": 640, "alto": 360}}])
        envia = lambda: (lambda v: (v.get("ancho"), v.get("alto")))(campo(nav.ultima("publica"), "rtc", "envia", "video") or {})
        visto = lambda: (lambda v: (v.get("ancho"), v.get("alto")))(campo(b.hub.fuente(f_nav), "pistas", "video") or {})
        if b.fuente_lista(f_nav, 60):                   # y a que el hub haya seguido la subida de resolución del navegador
            esperar(lambda: all(envia()) and envia()[1] >= 360 and visto() == envia(), 75, cada=1)
        return f_nav, b.fuente_lista(f_nav, 5)

    entradas = [("srt:origen", lambda: (fid, f)), ("srt:hub", a_la_escucha),
                ("rtmp:origen", lambda: b.camara("matriz, codificador RTMP", protocolo="rtmp")),
                ("rtmp:hub", lambda: de_un_servidor("rtmp")), ("hls:hub", lambda: de_un_servidor("hls")),
                ("webrtc:origen", navegador)]
    for lado, crear in entradas:
        de = ENTRADAS[lado]
        decir(f"   -- {de}")
        try:
            una, lista = crear()
            if una is None:
                c.comprobar(f"{de}: hacia las siete salidas", None, str(lista))
                continue
            lista = lista or b.fuente_lista(una)
            if c.comprobar(f"{de}: el hub recibe la fuente y sabe qué pistas trae", bool(lista),
                           trae(lista) if lista else str(campo(b.hub.fuente(una), "estado", "detalle"))):
                fps = FPS_NAVEGADOR if lado == "webrtc:origen" else campo(lista, "pistas", "video", "fps") or A.SENAL["fps"]
                a_todas_las_salidas(b, c, lado, una, fps)
        except (ErrorHub, RuntimeError, OSError, KeyError, subprocess.SubprocessError) as e:
            c.comprobar(f"{de}: la ronda llega hasta el final", False, f"{type(e).__name__}: {e}"[:300])
        finally:
            b.cerrar_caso()                             # cada ronda deja el hub como lo encontró
            time.sleep(2)


PROPIO = "127.0.0.1"                # de donde dice venir quien quiere pasar por el propio equipo


def codigo_https(url, metodo="GET", cuerpo=None, tipo="", origen=""):
    """Con qué código responde, sin credenciales, una dirección https de pruebas (su certificado no se
    comprueba). Con `origen`, la petición dice venir de esa dirección (cabecera X-Forwarded-For)."""
    contexto = ssl.create_default_context()
    contexto.check_hostname, contexto.verify_mode = False, ssl.CERT_NONE
    abrir = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=contexto))
    cabeceras = {**({"Content-Type": tipo} if tipo else {}), **({"X-Forwarded-For": origen} if origen else {})}
    try:
        return abrir.open(urllib.request.Request(url, data=cuerpo, method=metodo, headers=cabeceras), timeout=15).status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError) as e:
        return f"sin respuesta ({e})"[:80]


CLAVE_PUBLICA, CERTIFICADO_PUBLICO = "Clave en la dirección pública", "Certificado de la dirección pública"


def paso_de(prueba, que):
    """De lo que devuelve «Probar la conexión», el paso de ese nombre ({} si no está)."""
    return next((p for p in prueba.get("pasos") or [] if p.get("que") == que), {})


def caddy_delante(b, carpeta, de_red, dentro):
    """Un Caddy de verdad delante de HLS y de WebRTC, si este equipo lo tiene. Corre aparte del que haya
    en el sistema: con su configuración, sus puertos y su almacén dentro de la carpeta de la prueba, sin
    puerto de administración y sin instalar su certificado raíz en ningún sitio.

    Devuelve ({hls_url, webrtc_url}, "Caddy <versión>"), o (None, por qué no)."""
    if not shutil.which("caddy"):
        return None, "este equipo no tiene Caddy"
    dice = subprocess.run(["caddy", "version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20).stdout.split()
    version = (dice or ["de versión desconocida"])[0].lstrip("v")
    puertos = {k: b.puerto("rtmp") for k in dentro}
    almacen = carpeta / "caddy"
    (carpeta / "Caddyfile").write_text(
        "{\n\tadmin off\n\tauto_https disable_redirects\n\tskip_install_trust\n"
        f'\tstorage file_system "{almacen}"\n\tdefault_sni {de_red}\n}}\n'
        + "".join(f"https://{de_red}:{puertos[k]} {{\n\tbind {de_red}\n\ttls internal\n\treverse_proxy 127.0.0.1:{dentro[k]}\n}}\n"
                  for k in dentro), encoding="utf-8")
    caddy = A.Proceso("caddy", ["env", f"XDG_DATA_HOME={almacen}", f"XDG_CONFIG_HOME={almacen}", "caddy", "run",
                                "--config", carpeta / "Caddyfile", "--adapter", "caddyfile"], carpeta, b.secretos).lanzar()
    b.abierto_caso.append(caddy.parar)
    publicas = {f"{k}_url": f"https://{de_red}:{puertos[k]}" for k in dentro}
    # atiende cuando ya tiene su certificado: hasta entonces no completa la conexión segura
    if not esperar(lambda: caddy.vivo() and all(isinstance(codigo_https(u + "/"), int) for u in publicas.values()), 30, cada=0.5):
        caddy.parar()
        return None, f"el Caddy {version} de este equipo no arranca con la configuración de la prueba (ver {carpeta / 'caddy.log'})"
    return publicas, f"Caddy {version}"


def https_delante(b, c):
    """HLS y WebRTC con HTTPS por delante, como se sirven fuera de la red local: un proxy con certificado
    delante de MediaMTX y el hub entregando esas direcciones porque se le dicen en Ajustes (hls_url y
    webrtc_url). El proxy es un Caddy de verdad si este equipo lo tiene y, si no, uno de pruebas que
    trata como Caddy la dirección de origen (tests/banco/https_delante.py). Está en el mismo equipo que
    el hub, como el Caddy de una estación, y quien mira y quien publica entran por la dirección de red
    del equipo: para MediaMTX vienen de fuera, y sin su token no deben pasar.

    Al final se cambia ese proxy por dos mal puestos, uno tras otro: uno que no anota de dónde viene
    cada petición y otro que deja pasar lo que la petición diga de sí misma. Por los dos se entra sin
    clave, que el hub no puede impedir, y «Probar la conexión» tiene que decirlo."""
    titulo = "HLS y WebRTC con HTTPS por delante"
    red = b.hub.pedir("GET", "/api/ajustes")["red"]
    if red.get("hls_url_propia") or red.get("webrtc_url_propia"):
        c.comprobar(titulo, None, "este hub ya entrega HLS o WebRTC con una dirección propia, y la prueba no la cambia")
        return
    if not shutil.which("openssl"):
        c.comprobar(titulo, None, "falta openssl para crear el certificado del proxy de pruebas")
        return
    de_red = A.direccion_de_red()
    if not de_red:
        c.comprobar(titulo, None, "este equipo no tiene más dirección que la local: no se puede hacer de cliente de fuera")
        return
    dentro = {k: urllib.parse.urlsplit(red[f"{k}_url"]).port for k in ("hls", "webrtc")}     # donde atiende MediaMTX
    fid, _, fps = b.principal()
    carpeta = b.sub("proxy HTTPS")
    hecho = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", carpeta / "clave.pem", "-out",
                            carpeta / "cert.pem", "-days", "2", "-subj", "/CN=proxy-de-pruebas"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60)
    if hecho.returncode:
        c.comprobar(titulo, None, "openssl no pudo crear el certificado: " + hecho.stderr.decode("utf-8", "replace")[-160:])
        return
    programa = pathlib.Path(__file__).resolve().parent.parent / "tests" / "banco" / "https_delante.py"

    def proxy_delante(nombre, *opciones):
        """Lanza el proxy de pruebas y devuelve las direcciones públicas que atiende, o None si no arranca."""
        puertos = {k: b.puerto("rtmp") for k in dentro}
        proxy = A.Proceso(nombre, [sys.executable, programa, *opciones, carpeta / "cert.pem", carpeta / "clave.pem", de_red,
                                   *(f"{puertos[k]}={dentro[k]}" for k in dentro)], carpeta, b.secretos).lanzar()
        b.abierto_caso.append(proxy.parar)
        if not esperar(lambda: not any(A.puerto_libre(p, udp=False) for p in puertos.values()), 12, cada=0.25) or not proxy.vivo():
            return None
        return {f"{k}_url": f"https://{de_red}:{puertos[k]}" for k in dentro}
    publicas, delante = caddy_delante(b, carpeta, de_red, dentro)
    if not publicas:
        publicas, delante = proxy_delante("proxy"), f"el proxy de pruebas, haciendo de Caddy ({delante})"
    if not publicas:
        c.comprobar(titulo, None, f"el proxy de pruebas no arranca (ver {carpeta / 'proxy.log'})")
        return
    b.hub.pedir("PUT", "/api/ajustes/red", publicas)
    b.abierto_caso.append(lambda: b.hub.pedir("PUT", "/api/ajustes/red", {k: "" for k in publicas}))     # como estaba

    d_hls = b.nuevo("destinos", "HTTPS → HLS", protocolo="hls", inicia="receptor")
    d_rtc = b.nuevo("destinos", "HTTPS → WebRTC que abre un navegador", protocolo="webrtc", inicia="receptor")
    f_whip = b.nuevo("fuentes", "HTTPS, navegador que publica (WHIP)", protocolo="webrtc", inicia="origen")
    dir_hls, dir_rtc, dir_whip = b.direccion("destinos", d_hls), b.direccion("destinos", d_rtc), b.direccion("fuentes", f_whip)
    entregadas = {"la lista HLS": (dir_hls["url"], "hls"), "la página del reproductor WebRTC": (dir_rtc["pagina"], "webrtc"),
                  "la dirección WHIP": (dir_whip["url"], "webrtc")}
    sitio = lambda u: (lambda p: f"{p.scheme}://{p.hostname}:{p.port}")(urllib.parse.urlsplit(u))     # sin la ruta ni la clave
    c.comprobar("Con hls_url y webrtc_url en Ajustes, el hub entrega direcciones https://",
                all(sitio(u) == publicas[f"{k}_url"] for u, k in entregadas.values()),
                f"delante, {delante} · " + " · ".join(f"{nombre}: {sitio(u)}" for nombre, (u, _) in entregadas.items()))

    b.conectar(fid, d_hls)
    b.conectar(fid, d_rtc)
    esperar(lambda: all(b.ruta(d).get("estado") in ("esperando_receptor", "enviando") for d in (d_hls, d_rtc)), 45)
    # el proxy llega a MediaMTX desde 127.0.0.1, que para MediaMTX es el propio hub y entra sin clave:
    # lo que pasa por el proxy tiene que juzgarse por la dirección de quien conecta
    oferta = {"cuerpo": b"v=0\r\n", "tipo": "application/sdp"}
    whep, camara = dir_rtc["pagina"].rstrip("/") + "/whep", f"{publicas['hls_url']}/{fid}/index.m3u8"
    sin_token = {"mirar por WebRTC": codigo_https(whep, "POST", **oferta),
                 "publicar por WebRTC": codigo_https(dir_whip["url"], "POST", **oferta),
                 "leer por HLS la cámara, que no se entrega por HLS": codigo_https(camara)}
    c.comprobar("Quien entra por el proxy sin token no mira ni publica por WebRTC, ni lee por HLS una señal que no se entrega así",
                all(x == 401 for x in sin_token.values()), " · ".join(f"{k}: responde {v}" for k, v in sin_token.items()))
    # y lo que la petición diga de sí misma no cuenta: el proxy pone la dirección de quien conecta
    disfrazado = {"mirar por WebRTC": codigo_https(whep, "POST", origen=PROPIO, **oferta),
                  "publicar por WebRTC": codigo_https(dir_whip["url"], "POST", origen=PROPIO, **oferta),
                  "leer por HLS la cámara": codigo_https(camara, origen=PROPIO)}
    c.comprobar(f"Tampoco pasa quien dice venir del propio equipo (cabecera X-Forwarded-For: {PROPIO})",
                all(x == 401 for x in disfrazado.values()), " · ".join(f"{k}: responde {v}" for k, v in disfrazado.items()))
    # «Probar la conexión» pide por la dirección pública, sin clave, la página de una fuente: con este
    # proxy tiene que ver que no se entra. El certificado es de usar y tirar, y eso también lo dice
    pruebas = {k: b.hub.pedir("POST", f"/api/destinos/{d}/probar") for k, d in (("HLS", d_hls), ("WebRTC", d_rtc))}
    c.comprobar("«Probar la conexión» ve que por la dirección pública no se entra sin clave, y avisa del certificado de pruebas",
                all(paso_de(p, CLAVE_PUBLICA).get("ok") is True and paso_de(p, CERTIFICADO_PUBLICO).get("ok") is None
                    and p.get("ok") is None for p in pruebas.values()),
                " · ".join(f"{k}: {paso_de(p, CLAVE_PUBLICA).get('detalle') or p.get('resumen')}" for k, p in pruebas.items()))
    archivo, estado = b.capturar(b.lector("HTTPS → lector HLS", dir_hls["url"]), did=d_hls)
    b.senal(c, "HLS por HTTPS: un lector recibe audio y vídeo de la lista", archivo, fps, estado)
    if not b.node:
        c.comprobar("HTTPS: un navegador reproduce HLS y WebRTC y publica por WHIP", None, "sin Node con Playwright no hay navegador")
        return
    mira = b.navegador("HTTPS → navegador que mira", [
        {"nombre": "hls", "url": dir_hls["url"].rsplit("/", 1)[0] + "/", "usuario": credencial(dir_hls, "Usuario"),
         "clave": credencial(dir_hls, "Contraseña"), "sin_verificar": True},
        dict(pagina_whep("webrtc", dir_rtc), sin_verificar=True)])
    c.comprobar("HLS por HTTPS: un navegador reproduce audio y vídeo en la página que entrega el hub", *reproduce_hls(mira, "hls", 10))
    c.comprobar("WebRTC por HTTPS: un navegador reproduce audio y vídeo en la página que entrega el hub (WHEP)",
                *reproduce(mira, "webrtc", 10, fps))
    estados = {k: b.ruta(d) for k, d in (("HLS", d_hls), ("WebRTC", d_rtc))}
    c.comprobar("Con el proxy delante, el hub enseña «enviando» y la dirección de quien mira, no la del proxy",
                all(e.get("estado") == "enviando" and f"({de_red})" in evidencia(e) for e in estados.values()),
                " · ".join(f"{k}: «{e.get('estado')}», {evidencia(e)}" for k, e in estados.items()))

    origen = dir_whip["url"].rsplit("/", 2)[0]
    publica = b.navegador("HTTPS → navegador que publica", [{"nombre": "publica", "url": f"{origen}/__camara_de_pruebas", "sin_verificar": True,
                          "publicar": {"whip": dir_whip["url"], "token": credencial(dir_whip, "Token"), "codec": "H264",
                                       "audio_wav": str(b.tono()), "ancho": 640, "alto": 360}}])
    f = b.fuente_lista(f_whip, 60)
    u = publica.ultima("publica")
    c.comprobar("WHIP por HTTPS: el hub recibe lo que publica un navegador con su token", bool(f),
                trae(f) if f else f"el hub dice: {campo(b.hub.fuente(f_whip), 'estado', 'detalle')}; el navegador: "
                + json.dumps({k: u.get(k) for k in ("rtc", "errores", "error")}, ensure_ascii=False)[:300])

    # Un proxy que no anota de dónde viene cada petición (nginx no lo hace si no se le dice): a MediaMTX
    # todo le llega de 127.0.0.1 y lo toma por suyo. El hub no puede impedirlo: tiene que avisar
    mudas = proxy_delante("proxy-sin-origen", "--sin-origen")
    if not mudas:
        c.comprobar("Un proxy que no anota de dónde viene cada petición", None,
                    f"el segundo proxy de pruebas no arranca (ver {carpeta / 'proxy-sin-origen.log'})")
        return
    b.hub.pedir("PUT", "/api/ajustes/red", mudas)
    abierta = codigo_https(f"{mudas['hls_url']}/{fid}/index.m3u8")
    pruebas = {k: b.hub.pedir("POST", f"/api/destinos/{d}/probar") for k, d in (("HLS", d_hls), ("WebRTC", d_rtc))}
    dicho = lambda pruebas: " · ".join(f"{k}: {(paso_de(p, CLAVE_PUBLICA).get('detalle') or p.get('resumen') or '').split('. ')[0]}"
                                       for k, p in pruebas.items())
    c.comprobar("Con un proxy que no anota de dónde viene cada petición se entra sin clave, y «Probar la conexión» lo dice",
                abierta == 200 and all(p.get("ok") is False and paso_de(p, CLAVE_PUBLICA).get("ok") is False for p in pruebas.values()),
                f"sin clave, la lista HLS de la cámara responde {abierta} · {dicho(pruebas)}")

    # Un proxy que anota la dirección de quien conecta en otra cabecera (X-Real-IP) y deja pasar la
    # X-Forwarded-For que traiga la petición, como un nginx al que solo se le puso la primera. A quien
    # pide sin más le responde 401; a quien manda X-Forwarded-For: 127.0.0.1, MediaMTX lo toma por el
    # propio equipo. Con mirar si responde 401 no basta: «Probar la conexión» tiene que pedirlo también así
    credulas = proxy_delante("proxy-solo-real-ip", "--solo-real-ip")
    if not credulas:
        c.comprobar("Un proxy que deja pasar lo que la petición diga de sí misma", None,
                    f"el tercer proxy de pruebas no arranca (ver {carpeta / 'proxy-solo-real-ip.log'})")
        return
    b.hub.pedir("PUT", "/api/ajustes/red", credulas)
    camara = f"{credulas['hls_url']}/{fid}/index.m3u8"
    whep, whip = b.direccion("destinos", d_rtc)["pagina"].rstrip("/") + "/whep", b.direccion("fuentes", f_whip)["url"]

    def pedir(origen):
        return {"la lista HLS de la cámara": codigo_https(camara, origen=origen), "WHEP": codigo_https(whep, "POST", origen=origen, **oferta),
                "WHIP": codigo_https(whip, "POST", origen=origen, **oferta)}
    sin_mas, diciendo = pedir(""), pedir(PROPIO)
    # con la puerta pasada, a la lista se responde con ella y a la oferta de pruebas, que no es una oferta, con un 4xx que no es el 401
    pasa = lambda codigo: isinstance(codigo, int) and codigo < 500 and codigo not in (401, 403)
    pruebas = {k: b.hub.pedir("POST", f"/api/destinos/{d}/probar") for k, d in (("HLS", d_hls), ("WebRTC", d_rtc))}
    c.comprobar("Con un proxy que deja pasar lo que la petición diga de sí misma, quien dice venir del propio equipo entra sin clave, "
                "y «Probar la conexión» lo dice",
                all(x == 401 for x in sin_mas.values()) and diciendo["la lista HLS de la cámara"] == 200
                and pasa(diciendo["WHEP"]) and pasa(diciendo["WHIP"])
                and all(p.get("ok") is False and "diciendo venir del propio equipo" in (paso_de(p, CLAVE_PUBLICA).get("detalle") or "")
                        for p in pruebas.values()),
                "sin token y sin más: " + ", ".join(f"{k} responde {v}" for k, v in sin_mas.items())
                + f" · sin token y con X-Forwarded-For: {PROPIO}: " + ", ".join(f"{k} responde {v}" for k, v in diciendo.items())
                + " (a la oferta de pruebas, que no es una oferta, se le responde así una vez pasada la puerta)"
                + f" · {dicho(pruebas)}")


ANUNCIADA = "203.0.113.7"           # una dirección de documentación (RFC 5737): no es de nadie ni lleva a ningún sitio
TURN_RELES = (49160, 49259)         # puertos UDP entre los que el servidor TURN de pruebas reparte sus relés


def corte_visto(nav, pagina, t0, espera=60):
    """Lo que le pasa desde t0 a quien miraba por WebRTC: (se cortó, volvió, segundos sin imagen). Se
    queda sin imagen cuando dejan de subir los fotogramas decodificados y vuelve cuando suben otra vez."""
    def muestras():
        return [(t, v) for t, v in nav.serie(pagina, "rtc", "video", "fotogramas") if t >= t0 - 2]

    def avanza(a, b):
        return a[1] is not None and b[1] is not None and b[1] > a[1]

    def parado():
        m = muestras()
        return len(m) >= 2 and m[-1][0] > t0 and not avanza(m[-2], m[-1])
    if not esperar(parado, 12, cada=0.5):
        return False, True, 0.0
    volvio = esperar(lambda: (lambda m: len(m) >= 2 and avanza(m[-2], m[-1]))(muestras()), espera, cada=0.5)
    m = muestras()
    paro = next(i for i in range(1, len(m)) if m[i][0] > t0 and not avanza(m[i - 1], m[i]))
    vuelta = next((i for i in range(paro + 1, len(m)) if avanza(m[i - 1], m[i])), None)
    return True, volvio, (m[vuelta][0] if vuelta else m[-1][0]) - m[paro - 1][0]


def ajustes_webrtc(b, cuerpo):
    """Deja los ajustes de WebRTC del hub como dice `cuerpo`, confirmando lo que el hub diga que se corta
    (para deshacer lo que hizo la prueba, que entonces solo tiene al aire lo suyo)."""
    try:
        b.hub.pedir("PUT", "/api/ajustes/webrtc", cuerpo)
    except ErrorHub as e:
        if e.codigo != 409 or not isinstance(e.detalle, dict):
            raise
        b.hub.pedir("PUT", "/api/ajustes/webrtc", {**cuerpo, "corta": e.detalle.get("corta") or []})


def ice(b, c):
    """WebRTC para fuera de la red local, lo que se puede ver en un banco sin un NAT de por medio: qué pasa
    al cambiar esos ajustes con alguien mirando, que la dirección anunciada llega a quien conecta y que un
    navegador que solo puede salir por un servidor TURN reproduce y publica. El TURN es un coturn de pruebas
    en este equipo, con secreto compartido: al navegador le llega una credencial temporal, no el secreto."""
    titulo = "WebRTC con una dirección anunciada y un servidor TURN"
    ajustes = b.hub.pedir("GET", "/api/ajustes")["webrtc"]
    if ajustes.get("hosts") or ajustes.get("ice"):
        c.comprobar(titulo, None, "este hub ya tiene direcciones anunciadas o servidores ICE en Ajustes, y la prueba no los cambia")
        return
    if not shutil.which("turnserver"):
        c.comprobar(titulo, None, "falta coturn (turnserver) para hacer de servidor TURN de pruebas")
        return
    if not b.node:
        c.comprobar(titulo, None, "sin Node con Playwright no hay navegador")
        return
    fid, _, fps = b.principal()
    puerto, secreto, carpeta = b.puerto("srt"), secrets.token_hex(12), b.sub("servidor TURN")
    b.secretos.add(secreto)
    # coturn escribe su registro por la salida normal: se le pasa a la de errores, que es la que se guarda sin las claves
    turn = A.Proceso("turn", ["sh", "-c", 'exec "$@" >&2', "sh", shutil.which("turnserver"), "-n", "--listening-ip", "127.0.0.1",
                              "--listening-port", puerto, "--relay-ip", "127.0.0.1", "--min-port", TURN_RELES[0], "--max-port", TURN_RELES[1],
                              "--use-auth-secret", "--static-auth-secret", secreto, "--realm", "banco", "--no-tls", "--no-dtls",
                              "--no-cli", "--allow-loopback-peers", "--log-file", "stdout", "--simple-log", "--pidfile", carpeta / "turn.pid"],
                     carpeta, b.secretos).lanzar()
    b.abierto_caso.append(turn.parar)
    if not esperar(lambda: not A.puerto_libre(puerto, udp=True), 12, cada=0.25) or not turn.vivo():
        c.comprobar(titulo, None, f"el servidor TURN de pruebas no arranca (ver {carpeta / 'turn.log'})")
        return

    d_rtc = b.nuevo("destinos", "TURN → WebRTC que abre un navegador", protocolo="webrtc", inicia="receptor")
    b.conectar(fid, d_rtc)
    esperar(lambda: b.ruta(d_rtc).get("estado") in ("esperando_receptor", "enviando"), 45)
    direccion = b.direccion("destinos", d_rtc)
    directo = b.navegador("TURN → navegador que ya miraba", [pagina_whep("mira", direccion)])
    if not c.comprobar("Antes de tocar los ajustes, un navegador mira la salida WebRTC", *reproduce(directo, "mira", 5, fps)):
        return
    d_srt = b.nuevo("destinos", "TURN → SRT que lee otro equipo", protocolo="srt", inicia="receptor")       # lo que no debe cortarse
    b.conectar(fid, d_srt)
    esperar(lambda: b.ruta(d_srt).get("estado") in ("esperando_receptor", "enviando"), 40)
    por_srt = b.lector("TURN → lector SRT", b.direccion("destinos", d_srt)["url"])
    b.esperar_senal([por_srt])
    t_srt = time.time()
    nuevos = {"hosts": [ANUNCIADA], "ice": [{"url": f"turn:127.0.0.1:{puerto}", "usuario": "AUTH_SECRET", "clave": secreto}]}
    que = "Cambiar la dirección anunciada y los servidores ICE con alguien mirando no hace nada sin decir qué se corta"
    try:
        b.hub.pedir("PUT", "/api/ajustes/webrtc", nuevos)
        b.abierto_caso.append(lambda: ajustes_webrtc(b, {"hosts": [], "ice": []}))
        c.comprobar(que, False, "el hub aceptó el cambio sin confirmación")
        return
    except ErrorHub as e:
        detalle = e.detalle if isinstance(e.detalle, dict) else {}
        corta = detalle.get("corta") or []
        if e.codigo == 409 and set(corta) - {d_rtc}:
            c.comprobar(titulo, None, "en este hub hay otras rutas al aire por WebRTC, y la prueba las cortaría")
            return
        sigue = b.hub.pedir("GET", "/api/ajustes")["webrtc"]
        if not c.comprobar(que + ", y el hub dice qué está al aire", e.codigo == 409 and corta == [d_rtc]
                           and not sigue.get("hosts") and not sigue.get("ice"), str(detalle.get("mensaje") or e)):
            return
    t0 = time.time()
    b.hub.pedir("PUT", "/api/ajustes/webrtc", {**nuevos, "corta": corta})
    b.abierto_caso.append(lambda: ajustes_webrtc(b, {"hosts": [], "ice": []}))        # como estaba
    cortado, volvio, segundos = corte_visto(directo, "mira", t0)
    c.comprobar("Diciéndolo se aplica, y quien miraba vuelve a tener imagen sin tocar nada", volvio,
                (f"MediaMTX reinicia su servidor WebRTC: el navegador que miraba estuvo {segundos:.0f} s sin imagen"
                 + (" y su página volvió a conectar sola" if volvio else " y no ha vuelto")) if cortado else "no se quedó sin imagen")
    time.sleep(2)
    por_srt.cerrar()
    time.sleep(0.3)
    lapso, v = time.time() - t_srt, A.tramo_de_video(por_srt.archivo)
    c.comprobar("Mientras tanto, la salida SRT de la misma fuente sigue sin ninguna parada",
                bool(v) and v["dura"] >= lapso - 4 and v["salto_max"] <= 0.5,
                f"llegaron {v['dura']:.0f} s de vídeo seguidos, con {v['salto_max'] * 1000:.0f} ms como mayor hueco entre dos fotogramas"
                if v else "no se pudo medir lo que llegó por SRT")

    rele = b.navegador("TURN → navegador que solo sale por el relé", [dict(pagina_whep("mira", direccion), solo_rele=True)])
    ok, texto = reproduce(rele, "mira", 10, fps)
    visto = campo(rele.ultima("mira"), "rtc", "ice") or {}
    por = campo(visto, "elegido", "propio") or {}
    c.comprobar("Un navegador que solo puede salir por el servidor TURN reproduce audio y vídeo de la salida WebRTC",
                ok and por.get("tipo") == "relay", f"{texto}; sus medios van por un candidato «{por.get('tipo')}» de {por.get('servidor')}")
    servidores = visto.get("servidores") or []
    c.comprobar("Al navegador le llega el servidor TURN con una credencial temporal, no el secreto guardado en el hub",
                bool(servidores) and all(s.get("con_credencial") and s.get("usuario_temporal") for s in servidores),
                " · ".join(f"{s.get('url')}: " + ("usuario temporal" if s.get("usuario_temporal") else "usuario fijo")
                           + (" con credencial" if s.get("con_credencial") else " sin credencial") for s in servidores) or "ninguno")
    # cada candidato viene una vez por pista (audio y vídeo): se cuenta una
    anunciados = [dict(x) for x in dict.fromkeys(tuple(sorted(x.items())) for x in visto.get("anunciados") or [])]
    c.comprobar("La dirección anunciada en Ajustes llega a quien conecta, entre las del hub",
                any(x.get("donde", "").startswith(ANUNCIADA + ":") for x in anunciados),
                "el hub ofrece " + (", ".join(f"{x.get('donde')} ({x.get('tipo')})" for x in anunciados) or "nada"))
    c.comprobar("MediaMTX pide además su propio relé al servidor TURN y lo ofrece",
                any(x.get("tipo") == "relay" for x in anunciados),
                "; ".join(f"{x.get('donde')}" for x in anunciados if x.get("tipo") == "relay") + ": por ahí puede llegar al hub quien no "
                "alcanza sus direcciones" if any(x.get("tipo") == "relay" for x in anunciados) else "no ofrece ningún candidato de relé")
    estado = b.ruta(d_rtc)
    c.comprobar("El hub ve a quien mira por el relé y enseña «enviando»", estado.get("estado") == "enviando",
                f"«{estado.get('estado')}»: {evidencia(estado)}")

    f_whip = b.nuevo("fuentes", "TURN, navegador que publica (WHIP)", protocolo="webrtc", inicia="origen")
    dir_whip = b.direccion("fuentes", f_whip)
    publica = b.navegador("TURN → navegador que publica por el relé", [{
        "nombre": "publica", "url": dir_whip["url"].rsplit("/", 2)[0] + "/__camara_de_pruebas", "solo_rele": True,
        "publicar": {"whip": dir_whip["url"], "token": credencial(dir_whip, "Token"), "codec": "H264", "audio_wav": str(b.tono()),
                     "ancho": 640, "alto": 360, "ice_del_servidor": True}}])
    recibida = b.fuente_lista(f_whip, 60)
    u = publica.ultima("publica")
    por = campo(u, "rtc", "ice", "elegido", "propio") or {}
    c.comprobar("Un navegador que solo puede salir por el servidor TURN publica por WHIP y el hub lo recibe",
                bool(recibida) and por.get("tipo") == "relay",
                (f"{trae(recibida)}; sus medios van por un candidato «{por.get('tipo')}» de {por.get('servidor')}") if recibida
                else f"el hub dice: {campo(b.hub.fuente(f_whip), 'estado', 'detalle')}; el navegador: "
                + json.dumps({k: u.get(k) for k in ("rtc", "errores", "error")}, ensure_ascii=False)[:300])


CASOS = {"entregas": ("Lo que el hub entrega para que otro equipo lo abra (SRT y RTMP)", entregas),
         "llamadas": ("Otras formas de recibir la fuente: el hub va a buscarla (SRT, RTMP, HLS) o la espera en un puerto propio", llamadas),
         "rtmps": ("RTMPS: el hub recibe y entrega cifrado, publica en una plataforma y lee de un servidor", rtmps),
         "webrtc": ("WebRTC: WHIP de entrada y reenvío WHIP", webrtc),
         "perfiles": ("Un destino con su resolución y su bitrate, y una conversión compartida", perfiles),
         "fotogramas": ("H.264 con fotogramas B hacia WebRTC y hacia HLS", fotogramas),
         "audio": ("Selección de pistas de audio por destino", audio),
         "fuente": ("Corte de la fuente durante 20 s con tres rutas activas", corte_de_fuente),
         "reconexion": ("Política de reconexión con tope de intentos", reconexion),
         "cambio": ("Cambiar la fuente de un destino al aire", cambio),
         "matriz": ("Cada forma de entrada hacia las siete formas de salida a la vez", matriz),
         "https": ("HLS y WebRTC con HTTPS por delante, como se sirven fuera de la red local", https_delante),
         "ice": ("WebRTC con una dirección anunciada y un servidor TURN", ice)}


# ---------------------------------------------------------------- el informe
def informe(datos):
    eq, motor = datos.get("equipo") or {}, datos.get("motor") or {}
    L = [f"# Las demás formas de conexión del hub · {datos['lugar']}", "",
         f"- **Cuándo:** {datos['fecha']}",
         f"- **Dónde corrió el hub:** {datos['lugar']} · {eq.get('nombre', '?')} · {eq.get('cpu', '?')} · {datos.get('nucleos') or '?'} núcleos",
         f"- **Hub:** Streamer {datos.get('version', '?')} · MediaMTX {campo(motor, 'mediamtx', 'version') or '?'} · "
         f"FFmpeg {campo(motor, 'ffmpeg', 'version') or '?'} · conversiones de vídeo con {campo(motor, 'ffmpeg', 'codificador') or '?'}",
         "- **Con qué se comprobó:** la cámara y los receptores son programas (ffmpeg y un Chromium sin ventana). Donde hace falta "
         "«otro equipo» que sirva o reciba la señal (otro hub, un servidor RTMP, una lista HLS, un servidor WHIP) lo hace este mismo "
         "hub por otra de sus salidas o entradas; la plataforma RTMPS es un segundo MediaMTX con un certificado de pruebas; el "
         "proxy HTTPS, el que dice su caso (un Caddy lanzado para la prueba o, si el equipo no lo tiene, un programa del banco), "
         "también con un certificado de pruebas; el servidor TURN, un coturn en este mismo equipo. Es una verificación de banco "
         "de pruebas, no con equipos reales.",
         f"- **Cómo se manejó el hub:** por la API del panel, con una sesión de administrador ({datos['hub']}).", "",
         "Complementa a la prueba de aceptación ([aceptacion-banco.md](aceptacion-banco.md)), que cubre la entrada RTMP y la entrada "
         "SRT con sus salidas SRT, RTMP, HLS y WebRTC, el corte de 60 s y los reinicios.", "",
         "## Resultado", "", "| Qué | Resultado |", "|---|---|"]
    for c in datos["casos"]:
        L.append(f"| {c['titulo']} | {A.VEREDICTO[c['resultado']]} |")
    L.append(f"| Las claves de la prueba no aparecen en los registros ni en lo que devuelve la API | {A.MARCA[datos['claves']['ok']]} |")
    L.append("")
    for c in datos["casos"]:
        L += [f"## {c['titulo']}", "", "| Qué se comprobó | Resultado | Medida |", "|---|---|---|"]
        for x in c["comprobaciones"]:
            L.append(f"| {x['que']} | {A.MARCA[x['ok']]} | {str(x['detalle']).replace('|', '/')} |")
        if c.get("error"):
            L += ["", f"El caso se interrumpió: {c['error']}"]
        L.append("")
    k = datos["claves"]
    L += ["## Las claves", "", f"La prueba usó {k['cuantas']} claves y passphrases y las buscó después:", ""]
    L += [f"- Registros mirados: {x}" for x in k["mirado"]] or ["- No se miraron registros en disco (usar --registros o --journal)."]
    L += [f"- Aparecen en: {', '.join(k['hallazgos'])}" if k["hallazgos"] else "- En los registros mirados no aparece ninguna.",
          "- En las respuestas de la API (matriz, carga y registro de cambios): "
          + (f"aparecen en {', '.join(k['fugas'])}" if k["fugas"] else "no aparece ninguna."), ""]
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
def argumentos():
    p = argparse.ArgumentParser(description="Las demás formas de conexión del hub, con señal de verdad (ver la cabecera del guion).")
    p.add_argument("--hub", default=os.environ.get("MSXS_HUB", "http://127.0.0.1:8095"), help="dirección del panel del hub")
    p.add_argument("--sesion-archivo", help="archivo con el valor de la cookie msr_sesion de un administrador (o MSXS_SESION)")
    p.add_argument("--sesion-operador", default="", help="cookie msr_sesion de un operador, para comprobar que no puede cambiar lo que está al aire")
    p.add_argument("--receptor", default="127.0.0.1", help="IP de esta máquina vista desde el hub (la prueba corre en la máquina del hub)")
    p.add_argument("--puerto-srt", type=int, default=9321, help="primer puerto UDP para los receptores y cámaras SRT de la prueba")
    p.add_argument("--puerto-rtmp", type=int, default=1961, help="primer puerto TCP para los receptores RTMP de la prueba")
    p.add_argument("--casos", default=",".join(CASOS), help="cuáles, separados por comas: " + ", ".join(CASOS))
    p.add_argument("--registros", action="append", default=[], metavar="CARPETA", help="registros del hub donde buscar las claves")
    p.add_argument("--journal", action="store_true", help="buscar las claves también en journald (servicios msxs-*)")
    p.add_argument("--carpeta", default="", help="dónde dejar el informe (por defecto combinaciones-<fecha>)")
    p.add_argument("--lugar", default="banco de pruebas", help="dónde corre el hub, para el título del informe")
    p.add_argument("--node", default=os.environ.get("NODE", "node"), help="el Node con Playwright")
    p.add_argument("--mediamtx", default=os.environ.get("MEDIAMTX", ""),
                   help="binario de MediaMTX para la plataforma RTMPS de pruebas (por defecto, el de la carpeta de --registros)")
    p.add_argument("--sin-navegador", action="store_true", help="no abrir navegador: lo de WebRTC queda sin comprobar")
    p.add_argument("--no-anotar", action="store_true", help="no anotar en el hub las combinaciones que pasen")
    p.add_argument("--conservar", action="store_true", help="no borrar del hub lo que crea la prueba")
    return p.parse_args()


def main():
    args = argumentos()
    for senal in (A.signal.SIGTERM, A.signal.SIGHUP):       # si la cortan, recoge lo suyo (ver aceptacion.py)
        A.signal.signal(senal, A._cortada)
    sesion = os.environ.get("MSXS_SESION", "")
    if args.sesion_archivo:
        sesion = pathlib.Path(args.sesion_archivo).read_text().strip()
    if not sesion:
        sys.exit("Falta la sesión: MSXS_SESION o --sesion-archivo con la cookie msr_sesion de un administrador.")
    pedidos = [x.strip() for x in args.casos.split(",") if x.strip()]
    if not pedidos or set(pedidos) - set(CASOS):
        sys.exit("--casos admite: " + ", ".join(CASOS))
    for programa in (A.FFMPEG, A.FFPROBE):
        if not shutil.which(programa):
            sys.exit(f"Falta {programa}.")
    hub = A.Hub(args.hub, sesion)
    carpeta = pathlib.Path(args.carpeta or datetime.datetime.now().strftime("combinaciones-%Y%m%d-%H%M%S")).resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    node = None if args.sin_navegador else shutil.which(args.node)
    if node and subprocess.run([node, "-e", "try { require('playwright') } catch (e) { require('playwright-core') }"],
                               capture_output=True).returncode != 0:
        node = None
    if not node:
        decir("AVISO: sin Node con Playwright no hay navegador: lo de WebRTC quedará sin comprobar")
    try:
        yo = hub.pedir("GET", "/api/matriz")
    except ErrorHub as e:
        sys.exit(f"No se puede entrar al hub en {args.hub}: {e}")
    faltan = {"editar", "ver_claves", "modificar_al_aire", "ajustes"} - set(yo.get("permisos") or [])
    if faltan:
        sys.exit(f"La sesión no es de administrador: le faltan los permisos {', '.join(sorted(faltan))}.")
    if not (yo["sistema"]["mediamtx"] and yo["sistema"]["supervisor"]) or yo["sistema"].get("reposo"):
        sys.exit(f"El hub no está en marcha del todo: {json.dumps(yo['sistema'], ensure_ascii=False)}")
    A.limpiar_restos(hub)
    for p in hub.pedir("GET", "/api/perfiles"):
        if p["nombre"].startswith(PREFIJO):
            hub.pedir("DELETE", f"/api/perfiles/{p['id']}")
    inicio, b, casos = time.time(), Banco(hub, args, carpeta, node), []
    vigia = A.Vigia(hub)                    # de paso mira que lo que devuelve la API no lleve ninguna clave de la prueba
    vigia.secretos = b.secretos             # el mismo conjunto: las que se vayan creando también se buscan
    vigia.start()
    try:
        for clave in pedidos:
            titulo, funcion = CASOS[clave]
            c = Caso(clave, titulo)
            decir(f"== {titulo}")
            error = ""
            try:
                funcion(b, c)
            except (ErrorHub, RuntimeError, OSError, KeyError, subprocess.SubprocessError) as e:
                error = f"{type(e).__name__}: {e}"[:300]
                c.comprobar("El caso llega hasta el final", False, error)
            finally:
                b.cerrar_caso()
            casos.append({"clave": clave, "titulo": titulo, "comprobaciones": c.comprobaciones, "verificadas": c.verificadas,
                          "resultado": c.resultado(), "error": error})
            time.sleep(2)
    finally:
        try:
            vigia._mirar("/api/cambios", hub.pedir("GET", "/api/cambios?limite=200", crudo=True))
        except ErrorHub:
            pass
        vigia.parar()
        b.cerrar()
    mirado, hallazgos = A.buscar_claves(b.secretos, args.registros, args.journal, inicio)
    claves = {"cuantas": len(b.secretos), "mirado": mirado, "hallazgos": hallazgos, "fugas": sorted(vigia.fugas),
              "ok": (None if not mirado else True) if not hallazgos and not vigia.fugas else False}
    anotado, vistas = [], set()
    if not args.no_anotar:
        nota = f"Prueba automática de formas de conexión del {datetime.date.today().isoformat()} en {args.lugar}"
        for c in casos:
            for fuente, destino in c["verificadas"]:
                if (fuente, destino) in vistas:
                    continue
                vistas.add((fuente, destino))
                try:
                    r = hub.pedir("PUT", "/api/validacion", {"fuente": fuente, "destino": destino, "nivel": "laboratorio", "nota": nota})
                    anotado.append({"fuente": LADOS[fuente], "destino": LADOS[destino],
                                    "nivel": "Verificada en el banco de pruebas" if r.get("anotado") else "Ya estaba verificada con equipos reales"})
                except ErrorHub as e:
                    decir(f"(no se pudo anotar {fuente} → {destino}: {e})")
    datos = {"fecha": datetime.datetime.fromtimestamp(inicio).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
             "lugar": args.lugar, "hub": args.hub, "casos": casos, "claves": claves, "anotado": anotado,
             "equipo": vigia.equipo, "nucleos": next((c.get("nucleos") for _, c in vigia.cargas), None),
             "sin_respuesta": len(vigia.fallos)}
    try:
        datos["version"] = hub.pedir("GET", "/api/sistema").get("version")
        datos["motor"] = hub.pedir("GET", "/api/adaptadores")
    except ErrorHub:
        pass
    (carpeta / "datos.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (carpeta / "informe.md").write_text(informe(datos), encoding="utf-8")
    decir(f"informe en {carpeta / 'informe.md'}")
    for c in casos:
        decir(f"{c['titulo']}: {c['resultado'].upper()}")
    resultados = {c["resultado"] for c in casos} | ({"falla"} if claves["ok"] is False else set())
    sys.exit(1 if "falla" in resultados else 3 if "sin comprobar" in resultados else 0)


if __name__ == "__main__":
    main()
