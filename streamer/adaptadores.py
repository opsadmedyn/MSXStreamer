"""Media Syntaxis Streamer - adaptadores NDI y Zixi: qué hay instalado y qué falta.

NDI y Zixi no forman parte del núcleo: ni MediaMTX ni FFmpeg los hablan, y cada uno depende de un
componente con licencia propia que no se puede distribuir con el Streamer. Mientras falte, sus
fuentes y destinos esperan con el requisito pendiente a la vista, y el resto del hub sigue igual.

NDI   Componente: el SDK de NDI para Linux (libndi.so, de Vizrt NDI AB), que se descarga de ndi.video
      aceptando su licencia. Para pasar la señal entre NDI y el hub hace falta además GStreamer con
      el plugin «ndi» (de gst-plugins-rs): FFmpeg dejó de incluir NDI en 2019. Para descubrir las
      fuentes de la red local, avahi (mDNS) o un servidor de descubrimiento NDI.
Zixi  Componente: Zixi Broadcaster (u otro componente de Zixi) con su licencia. El hub no habla Zixi:
      intercambia MPEG-TS con ese componente por una dirección local, y es él quien recibe o entrega
      por Zixi, con los modos y los límites que le dé su licencia.

Estado de hoy: este módulo detecta los componentes y dice qué falta. El paso de señal (el «puente»)
de cada adaptador se termina y se valida con el componente instalado y un equipo real delante; hasta
entonces ninguna ruta NDI o Zixi figura como verificada (ver docs/validacion.md).

Lo usa el supervisor (cada vuelta, por eso lo detectado se guarda unos minutos) y el panel.
"""

import ctypes.util
import glob
import os
import shutil
import socket
import subprocess
import time
import urllib.parse

import db

CADUCA = 300.0                      # segundos que vale una detección
_guardado = {}                      # nombre -> (instante, estado)
LIBNDI = ("/usr/local/lib/libndi.so*", "/usr/lib/libndi.so*", "/usr/lib/x86_64-linux-gnu/libndi.so*",
          "/opt/ndi/lib/x86_64-linux-gnu/libndi.so*")
SIN_PUENTE = "El puente {0} del hub se termina y se valida con el componente instalado y un equipo real"


def _config(nombre):
    c = (db.config().get("adaptadores") or {}).get(nombre) or {}
    return c if isinstance(c, dict) else {}


def _requisito(que, ok, detalle):
    return {"que": que, "ok": bool(ok), "detalle": detalle}


def libndi(cfg):
    """Ruta de libndi.so, o "" si el SDK no está instalado."""
    if cfg.get("libndi"):
        return cfg["libndi"] if os.path.exists(cfg["libndi"]) else ""
    for patron in LIBNDI:
        encontrados = sorted(glob.glob(patron))
        if encontrados:
            return encontrados[-1]
    return ctypes.util.find_library("ndi") or ""


def gst_tiene(gst_inspect, elemento):
    """¿GStreamer tiene ese elemento? (gst-inspect-1.0 sale con 0 si existe.)"""
    try:
        return subprocess.run([gst_inspect, "--exists", elemento], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def avahi_en_marcha():
    return os.path.exists("/run/avahi-daemon/socket") or os.path.exists("/var/run/avahi-daemon/socket")


def responde(url, espera=1.5):
    """¿Hay algo escuchando en la dirección (host y puerto) de esa URL?"""
    try:
        p = urllib.parse.urlsplit(url)
        socket.create_connection((p.hostname, p.port or (443 if p.scheme == "https" else 80)), timeout=espera).close()
        return True
    except (OSError, ValueError, TypeError):
        return False


def _ndi():
    cfg = _config("ndi")
    lib = libndi(cfg)
    inspect = shutil.which(cfg.get("gst_inspect") or "gst-inspect-1.0")
    plugin = bool(inspect) and gst_tiene(inspect, "ndisrc")
    servidor = (cfg.get("descubrimiento") or "").strip()
    requisitos = [
        _requisito("SDK de NDI para Linux", lib, f"Instalado: {lib}" if lib else
                   "No está instalado. Se descarga de ndi.video aceptando su licencia; su libndi.so va en /usr/local/lib."),
        _requisito("GStreamer", inspect, f"Instalado: {inspect}" if inspect else
                   "No está instalado (paquetes gstreamer1.0-tools y gstreamer1.0-plugins-base, -good y -bad)."),
        _requisito("Plugin «ndi» de GStreamer", plugin, "Instalado" if plugin else
                   "No está instalado. Es parte de gst-plugins-rs (gst-plugin-ndi) y necesita el SDK."),
        _requisito("Descubrimiento de fuentes", servidor or avahi_en_marcha(),
                   f"Servidor de descubrimiento NDI: {servidor}" if servidor else
                   "avahi en marcha (mDNS en la red local)" if avahi_en_marcha() else
                   "Ni avahi (mDNS) está en marcha ni hay un servidor de descubrimiento NDI configurado."),
        _requisito("Puente NDI del hub", False, SIN_PUENTE.format("NDI") + ": una fuente NDI en la red local."),
    ]
    variantes = [
        {"nombre": "NDI de ancho de banda completo", "recibir": "Con el SDK estándar", "publicar": "Con el SDK estándar"},
        {"nombre": "NDI|HX", "recibir": "Según la versión del SDK instalada",
         "publicar": "Exige el SDK avanzado de NDI y su licencia"},
    ]
    return _estado("ndi", "NDI", requisitos, activo=bool(cfg.get("activo")), variantes=variantes,
                   componente="SDK de NDI para Linux (Vizrt NDI AB) y GStreamer con el plugin «ndi»")


def _zixi():
    cfg = _config("zixi")
    api = (cfg.get("api") or "").strip()
    hay = bool(api) and responde(api)
    requisitos = [
        _requisito("Componente de Zixi (Zixi Broadcaster)", hay,
                   f"Responde en {api}" if hay else f"Configurado en {api}, pero no responde." if api else
                   "No hay ninguno configurado. Indica su dirección en config.json (adaptadores.zixi.api)."),
        _requisito("Licencia de Zixi", False, "La da Zixi con el contrato. El hub mostrará sus modos y límites "
                                              "cuando se valide la integración con el componente instalado."),
        _requisito("Puente Zixi del hub", False, SIN_PUENTE.format("Zixi") + ": un emisor o un receptor Zixi."),
    ]
    modos = [
        {"nombre": "Entrada", "detalle": "El origen llama al hub (push) o el hub llama al origen (pull), según la licencia"},
        {"nombre": "Salida", "detalle": "El hub llama al receptor (push) o el receptor llama al hub (pull), según la licencia"},
        {"nombre": "Límites", "detalle": "Número de entradas y salidas y bitrate máximo: los fija la licencia"},
    ]
    return _estado("zixi", "Zixi", requisitos, activo=bool(cfg.get("activo")), modos=modos,
                   componente="Zixi Broadcaster (u otro componente de Zixi) con licencia")


def _estado(clave, nombre, requisitos, activo, componente, **mas):
    pendientes = [r for r in requisitos if not r["ok"]]
    listo = activo and not pendientes
    if pendientes:
        falta = f"Adaptador {nombre}: falta «{pendientes[0]['que']}». {pendientes[0]['detalle']}"
    else:
        falta = "" if activo else f"Adaptador {nombre} instalado pero sin activar (adaptadores.{clave}.activo en config.json)."
    return {"nombre": nombre, "listo": listo, "activo": activo, "falta": falta, "componente": componente,
            "requisitos": requisitos, "validacion": "pendiente", "comprobado": time.time(), **mas}


DETECTORES = {"ndi": _ndi, "zixi": _zixi}


def estado(forzar=False):
    """{"ndi": {...}, "zixi": {...}}. Cada uno: listo, falta (el requisito pendiente, en una frase),
    requisitos uno a uno y, cuando haya puente, cómo lanzarlo ("entrada", "salida": ver matriz.calcular)."""
    ahora = time.time()
    r = {}
    for nombre, detectar in DETECTORES.items():
        antes = _guardado.get(nombre)
        if forzar or not antes or ahora - antes[0] > CADUCA:
            try:
                antes = (ahora, detectar())
            except Exception as err:                # una detección rota no detiene al supervisor
                antes = (ahora, _estado(nombre, nombre.upper(), [_requisito("Detección", False, f"{err!r}"[:200])],
                                        False, ""))
            _guardado[nombre] = antes
        r[nombre] = antes[1]
    return r
