"""Cliente mínimo de la API de MediaMTX (v3).

Los paths creados por API no se guardan en mediamtx.yml, así que el supervisor los vuelve a
declarar en cada vuelta: si MediaMTX se reinicia, en unos segundos recupera todos los flujos.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import db

CFG = db.config()
API = CFG.get("mediamtx_api", "http://127.0.0.1:9997")
SRT_LOCAL = CFG.get("mediamtx_srt", "srt://127.0.0.1:8890")


class ErrorMTX(Exception):
    pass


def _pedir(metodo, ruta, cuerpo=None, timeout=3):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(API + ruta, data=datos, method=metodo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read()
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as e:
        raise ErrorMTX(f"{metodo} {ruta}: {e.code} {e.read().decode(errors='replace')[:200]}")
    except (urllib.error.URLError, OSError) as e:
        raise ErrorMTX(f"MediaMTX no responde: {e}")


def _todos(ruta):
    items, pagina = [], 0
    while True:
        r = _pedir("GET", f"{ruta}?page={pagina}&itemsPerPage=200")
        items += r.get("items") or []
        pagina += 1
        if pagina >= r.get("pageCount", 1):
            return items


def paths_config():
    return {p["name"]: p for p in _todos("/v3/config/paths/list")}


def paths_estado():
    return {p["name"]: p for p in _todos("/v3/paths/list")}


def conf_de_flujo(f, canales):
    """Configuración del path de MediaMTX que corresponde a un flujo (None: aún no hay entrada)."""
    if f["entrada"] == "pull":
        # se respetan los parámetros pegados en la URL; los campos del formulario mandan
        base, _, consulta = f["url"].partition("?")
        q = dict(urllib.parse.parse_qsl(consulta))
        if f["streamid"]:
            q["streamid"] = f["streamid"]
        if f["passphrase"]:
            q["passphrase"] = f["passphrase"]
        url = base + ("?" + urllib.parse.urlencode(q) if q else "")
        return {"source": url, "sourceOnDemand": False}
    # recorder: el grabador del canal reenvía por UDP local lo mismo que graba. Es UDP a
    # propósito: si MediaMTX está parado, el grabador no se entera ni se bloquea.
    puerto = next((c["reenvio_udp"] for c in canales if c["id"] == f["canal"]), None)
    return {"source": f"udp+mpegts://127.0.0.1:{puerto}"} if puerto else None


def asegurar_path(nombre, conf, actuales):
    """Crea o corrige el path si no coincide con lo que debe ser. Devuelve True si cambió."""
    actual = actuales.get(nombre)
    if actual is None:
        _pedir("POST", f"/v3/config/paths/add/{urllib.parse.quote(nombre)}", conf)
        return True
    if any(actual.get(k) != v for k, v in conf.items()):
        _pedir("PATCH", f"/v3/config/paths/patch/{urllib.parse.quote(nombre)}", conf)
        return True
    return False


def borrar_path(nombre):
    _pedir("DELETE", f"/v3/config/paths/delete/{urllib.parse.quote(nombre)}")


def url_lectura(path):
    return f"{SRT_LOCAL}?streamid=read:{path}"


def url_publicacion(path):
    return f"{SRT_LOCAL}?streamid=publish:{path}"
