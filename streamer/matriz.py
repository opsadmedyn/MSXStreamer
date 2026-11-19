"""Media Syntaxis Streamer - la matriz del hub: fuentes, destinos, rutas y cómo se cumplen.

Una fuente es una señal que entra; un destino, un sitio al que sale; una ruta une una fuente con un
destino (un destino recibe una sola fuente a la vez). Cada pieza se apoya en un path de MediaMTX:

  fuente                 path <fuente>. Si el origen llama al hub, publica ahí él mismo (SRT, RTMP o
                         WHIP, con su clave); si el hub va a buscarla, la trae un ffmpeg («entrada»).
  señal derivada         path conv_<fuente>_<firma>. La publica un ffmpeg («conversión») cuando un
                         destino necesita otro perfil u otro audio. Dos rutas de la misma fuente con
                         la misma firma leen el mismo path: una sola conversión.
  destino que el hub     sin path: un ffmpeg («envío») lee la señal y la lleva en copia (SRT, RTMP).
    alimenta             Por WHIP la reenvía MediaMTX desde el path del destino.
  destino que el hub     path <destino>, que lee la señal que toque: la dirección que se entrega no
    entrega              cambia aunque cambie la fuente. Lo sirve MediaMTX por SRT, RTMP, HLS o WebRTC,
                         con la clave del destino.

calcular() devuelve todo lo que el supervisor tiene que mantener: paths, usuarios de MediaMTX y
procesos. No lanza nada ni guarda estado, y el panel usa el mismo cálculo para explicar cada ruta.
"""

import base64
import hashlib
import json
import re
import secrets as aleatorio
import unicodedata
import urllib.parse

import comandos
import composicion
import db
import mtx
import plan
import protocolos as P

FUENTE_PARADA = "La fuente está desactivada"
RECONEXION = {"cada_s": 0, "max_intentos": 0}       # 0 s: espera creciente (2, 4, 8… 30 s); 0 intentos: sin límite


def json_de(texto, defecto):
    try:
        v = json.loads(texto) if texto else defecto
    except (ValueError, TypeError):
        return defecto
    return v if isinstance(v, type(defecto)) or defecto is None else defecto


# ---------------------------------------------------------------- lectura
def fuentes(c):
    return db.filas(c, "SELECT * FROM fuentes ORDER BY creado, id")


def heredadas(c, con_directos=False):
    """Los flujos de la versión anterior del Streamer, vistos como fuentes del hub: la señal limpia de
    cada uno y, si tiene composición, la compuesta. De su entrada (Hub channel, Recorder o SRT) y de
    su path en MediaMTX se sigue ocupando el supervisor como siempre; el hub solo lee de ahí, así
    que una ruta puede llevarlos a cualquier destino. Su id es ese path (rec_<canal>, comp_<flujo>
    o el id del flujo). Un flujo 1080i directo no pasa por MediaMTX: no se puede enrutar."""
    comps = {r["flujo"]: r["activa"] for r in db.filas(c, "SELECT flujo, activa FROM composiciones")}
    vistas, r = set(), []
    for f in db.filas(c, "SELECT * FROM flujos ORDER BY creado, id"):
        if f.get("directo") and not con_directos:
            continue
        base = {"protocolo": "srt", "inicia": "hub", "url": "", "streamid": "", "passphrase": "", "clave": "",
                "latencia_ms": f["latencia_ms"], "opciones": "{}", "creado": f["creado"]}
        path = mtx.path_de_flujo(f)
        if path not in vistas:                      # dos flujos del mismo canal del Recorder: un solo path
            vistas.add(path)
            r.append({**base, "id": path, "nombre": f["nombre"], "activo": f["activo"],
                      "heredada": {"flujo": f["id"], "senal": "limpia", "entrada": f["entrada"]}})
        if f["id"] in comps:
            r.append({**base, "id": composicion.path_comp(f["id"]), "nombre": f["nombre"] + " · compuesta",
                      "activo": int(bool(f["activo"] and comps[f["id"]])),
                      "heredada": {"flujo": f["id"], "senal": "compuesta", "entrada": f["entrada"]}})
    return r


def todas_las_fuentes(c):
    """Las del hub y, detrás, los flujos heredados."""
    return fuentes(c) + heredadas(c)


def limpiar_huerfanas(c):
    """Quita las rutas cuya fuente ya no existe (se borró la fuente o el flujo). Devuelve cuántas."""
    conocidas = [f["id"] for f in fuentes(c) + heredadas(c, con_directos=True)]
    marcas = ",".join("?" * len(conocidas))
    n = c.execute(f"DELETE FROM rutas WHERE fuente NOT IN ({marcas})", conocidas).rowcount
    c.execute(f"DELETE FROM inspecciones WHERE fuente NOT IN ({marcas})", conocidas)
    return n


def destinos(c):
    return db.filas(c, "SELECT * FROM destinos ORDER BY creado, id")


def rutas(c):
    return db.filas(c, "SELECT * FROM rutas")


def perfiles(c):
    """Los fijos y, detrás, los que se hayan creado en Ajustes."""
    propios = [{"id": r["id"], "nombre": r["nombre"], "video": json_de(r["video"], None),
                "audio": json_de(r["audio"], None)} for r in db.filas(c, "SELECT * FROM perfiles ORDER BY nombre")]
    fijos = {p["id"] for p in plan.PERFILES_FIJOS}
    return [dict(p, fijo=True) for p in plan.PERFILES_FIJOS] + [p for p in propios if p["id"] not in fijos]


def pistas(c):
    """{fuente: lo que trae (como lo espera plan.planear) o None si aún no se ha podido mirar}."""
    return {r["fuente"]: json_de(r["pistas"], None)
            for r in db.filas(c, "SELECT fuente, pistas FROM inspecciones")}


def nuevo_id(c, nombre, defecto="senal"):
    """Identificador legible y único entre fuentes, destinos y flujos: todos comparten los paths
    de MediaMTX."""
    base = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:24] or defecto
    while True:
        i = f"{base}-{aleatorio.token_hex(2)}"
        if not any(c.execute(f"SELECT 1 FROM {t} WHERE id=?", (i,)).fetchone()
                   for t in ("fuentes", "destinos", "flujos")):
            return i


def nueva_clave():
    """Clave para publicar en una fuente o leer de un destino: 20 caracteres que valen en una URL."""
    return aleatorio.token_urlsafe(15)


def reconexion(d):
    """Política de reconexión de un destino, con sus valores dentro de lo razonable."""
    r = {**RECONEXION, **json_de(d.get("reconexion"), {})}
    try:
        cada, tope = float(r["cada_s"] or 0), int(r["max_intentos"] or 0)
    except (TypeError, ValueError):
        return dict(RECONEXION)
    return {"cada_s": max(0.0, min(600.0, cada)), "max_intentos": max(0, min(100000, tope))}


# ---------------------------------------------------------------- escritura
COLUMNAS = {
    "fuentes": ("nombre", "protocolo", "inicia", "url", "streamid", "passphrase", "latencia_ms", "clave",
                "opciones", "activo"),
    "destinos": ("nombre", "protocolo", "inicia", "url", "streamid", "passphrase", "latencia_ms", "clave",
                 "perfil", "audio", "reconexion", "opciones"),
}


def _valores(tabla, campos):
    desconocidos = set(campos) - set(COLUMNAS[tabla])
    if desconocidos:
        raise ValueError(f"campos desconocidos: {sorted(desconocidos)}")
    return {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in campos.items()}


def crear(c, tabla, **campos):
    """Crea una fuente o un destino y devuelve su identificador."""
    v = _valores(tabla, campos)
    i = nuevo_id(c, v.get("nombre") or "", "fuente" if tabla == "fuentes" else "destino")
    columnas = ["id", *v]
    c.execute(f"INSERT INTO {tabla}({','.join(columnas)}) VALUES ({','.join('?' * len(columnas))})", (i, *v.values()))
    return i


def editar(c, tabla, i, **campos):
    v = _valores(tabla, campos)
    if not v:
        return bool(c.execute(f"SELECT 1 FROM {tabla} WHERE id=?", (i,)).fetchone())
    return bool(c.execute(f"UPDATE {tabla} SET {','.join(k + '=?' for k in v)} WHERE id=?", (*v.values(), i)).rowcount)


def borrar(c, tabla, i):
    """Borra una fuente o un destino, con sus rutas y lo que el supervisor anotó de ella."""
    if not c.execute(f"DELETE FROM {tabla} WHERE id=?", (i,)).rowcount:
        return False
    if tabla == "fuentes":                          # las rutas de un destino las borra la propia base
        for sql in ("DELETE FROM rutas WHERE fuente=?", "DELETE FROM inspecciones WHERE fuente=?",
                    "DELETE FROM senales WHERE path=?"):
            c.execute(sql, (i,))
    return True


def conectar(c, destino, fuente, perfil=None, audio=None, por=""):
    """Conecta la fuente al destino (si el destino tenía otra, la sustituye). Sin perfil o audio, los
    que tenga el destino como propios."""
    d = c.execute("SELECT perfil, audio FROM destinos WHERE id=?", (destino,)).fetchone()
    if not d or not any(f["id"] == fuente for f in todas_las_fuentes(c)):
        raise KeyError("no existe esa fuente o ese destino")
    c.execute("""INSERT INTO rutas(destino, fuente, perfil, audio, desde, por) VALUES (?,?,?,?,strftime('%s','now'),?)
                 ON CONFLICT(destino) DO UPDATE SET fuente=excluded.fuente, perfil=excluded.perfil,
                 audio=excluded.audio, desde=excluded.desde, por=excluded.por""",
              (destino, fuente, perfil or d["perfil"], str(audio or d["audio"]), por))


def desconectar(c, destino):
    return bool(c.execute("DELETE FROM rutas WHERE destino=?", (destino,)).rowcount)


# ---------------------------------------------------------------- nombres
def path_senal(fuente_id, firma):
    return fuente_id if not firma else f"conv_{fuente_id}_{firma}"


# Los procesos del hub se identifican con un "_", que nunca aparece en el id de un flujo, de una
# salida, de una fuente o de un destino (son nombres pasados a minúsculas, cifras y guiones).
def id_entrada(fuente_id):
    return "ent_" + fuente_id


def id_conversion(fuente_id, firma):
    return path_senal(fuente_id, firma)             # el mismo nombre que el path donde publica


def id_ruta(destino_id):
    return "ruta_" + destino_id


def es_del_hub(tarea):
    return tarea.startswith(("ent_", "conv_", "ruta_"))


def vacio():
    return {"paths": {}, "usuarios": [], "abiertos": {"publish": set(), "read": set()}, "tareas": {},
            "politicas": {}, "planes": {}, "senales": {}, "impedimentos": {}, "fuentes": []}


def empuja(d):
    """El hub lleva la señal con un proceso propio (que además elige las pistas al enviar)."""
    return comandos.con_envio(d)


def reenvia_whip(d):
    return d["protocolo"] == "webrtc" and d["inicia"] == "hub"


def entrega(d):
    """El hub entrega una dirección y es el receptor quien viene a por la señal."""
    return d["protocolo"] not in P.CON_ADAPTADOR and not empuja(d) and not reenvia_whip(d)


def publica_solo(f):
    """El origen publica en MediaMTX por su cuenta (con la clave de la fuente)."""
    return f["protocolo"] not in P.CON_ADAPTADOR and f["inicia"] == "origen" and not comandos.con_entrada(f)


# ---------------------------------------------------------------- acceso
def huella_mtx(valor):
    """Como guarda MediaMTX un usuario o una clave sin tenerlos en claro (ni en su registro ni en
    lo que devuelve su API)."""
    return "sha256:" + base64.b64encode(hashlib.sha256(valor.encode()).digest()).decode()


def _usuario(nombre, clave, accion, path):
    return {"user": huella_mtx(nombre), "pass": huella_mtx(clave), "ips": [],
            "permissions": [{"action": accion, "path": path}]}


# ---------------------------------------------------------------- el cálculo
def plan_de(r, f, d, lista_perfiles, que_trae):
    return plan.planear(que_trae.get(f["id"]), d["protocolo"], plan.perfil_por_id(lista_perfiles, r["perfil"]),
                        r["audio"] or "todas", empuja=empuja(d))


def _whip(url):
    """Como quiere MediaMTX el destino de un reenvío WHIP: whip:// o whips://."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(({"https": "whips", "http": "whip"}.get(p.scheme, p.scheme), *p[1:]))


def calcular(c, estado_paths=None, codificador="libx264", adaptadores=None):
    """Lo que debe existir para cumplir las rutas guardadas.

    estado_paths: estado de MediaMTX ({path: info}) o None si no se sabe. Los paths que leen de otro
        path (los de los destinos que el hub entrega) solo se declaran mientras ese otro tiene
        señal: sin ella MediaMTX reintentaría cada pocos segundos llenando su registro.
    adaptadores: {"ndi": {"listo": bool, "falta": texto, …}, "zixi": …} (ver adaptadores.py).
    """
    adaptadores = adaptadores or {}
    r = vacio()
    fs = {f["id"]: f for f in todas_las_fuentes(c)}
    r["fuentes"] = [f for f in fs.values() if f["activo"]]
    ds = {d["id"]: d for d in destinos(c)}
    lista_perfiles, que_trae = perfiles(c), pistas(c)
    con_senal = lambda path: bool(estado_paths and (estado_paths.get(path) or {}).get("ready"))

    for f in fs.values():
        if not f["activo"] or f.get("heredada"):    # un flujo heredado ya tiene quien lo traiga
            continue
        if f["protocolo"] in P.CON_ADAPTADOR:
            a = adaptadores.get(f["protocolo"]) or {}
            r["paths"][f["id"]] = {"source": "publisher", "srtPublishPassphrase": ""}
            cmd = a.get("entrada") and a["entrada"](f)
            r["tareas"][id_entrada(f["id"])] = (cmd, None, None) if cmd else \
                (None, None, a.get("falta") or f"Falta el adaptador {P.NOMBRES[f['protocolo']]}")
            continue
        if comandos.con_entrada(f):
            r["paths"][f["id"]] = {"source": "publisher", "srtPublishPassphrase": ""}
            try:
                r["tareas"][id_entrada(f["id"])] = (comandos.entrada(id_entrada(f["id"]), f, f["id"]), None, None)
            except (ValueError, TypeError, KeyError) as err:
                r["tareas"][id_entrada(f["id"])] = (None, None, f"Fuente mal configurada: {err}"[:300])
            continue
        # el origen publica por su cuenta: con la clave de la fuente o, sin clave, cualquiera
        con_frase = f["protocolo"] == "srt" and f["passphrase"]
        r["paths"][f["id"]] = {"source": "publisher", "srtPublishPassphrase": f["passphrase"] if con_frase else ""}
        if f["clave"]:
            r["usuarios"].append(_usuario(f["id"], f["clave"], "publish", f["id"]))
        else:
            r["abiertos"]["publish"].add(f["id"])

    # Quién puede leer cada destino que el hub entrega. No depende de que haya ruta ni señal: así las
    # reglas de MediaMTX solo cambian cuando se crea, edita o borra un destino.
    for d in ds.values():
        if not entrega(d):
            continue
        if d["clave"]:
            r["usuarios"].append(_usuario(d["id"], d["clave"], "read", d["id"]))
        else:
            r["abiertos"]["read"].add(d["id"])

    for ruta in rutas(c):
        d, f = ds.get(ruta["destino"]), fs.get(ruta["fuente"])
        if not d or not f:
            continue
        did = d["id"]
        if not f["activo"]:
            r["impedimentos"][did] = FUENTE_PARADA
            continue
        try:
            p = plan_de(ruta, f, d, lista_perfiles, que_trae)
        except (ValueError, TypeError, KeyError) as err:
            r["impedimentos"][did] = f"Ruta mal configurada: {err}"[:300]
            continue
        r["planes"][did] = p
        if d["protocolo"] in P.CON_ADAPTADOR:
            a = adaptadores.get(d["protocolo"]) or {}
            cmd = a.get("salida") and a["salida"](d, f["id"])
            r["senales"][did] = f["id"]
            r["tareas"][id_ruta(did)] = (cmd, f["id"], None) if cmd else \
                (None, None, a.get("falta") or f"Falta el adaptador {P.NOMBRES[d['protocolo']]}")
            continue
        if p["provisional"] or not p["posible"]:
            continue                                # aún no se sabe qué hay que hacer: no se lanza nada
        if isinstance(p["senal"]["video"], dict) and (que_trae.get(f["id"]) or {}).get("parcial"):
            # para codificar vídeo hacen falta los datos que da la inspección completa (fps, entrelazado):
            # se espera a tenerlos en vez de arrancar ahora y relanzar a los pocos segundos
            p.update(provisional=True, resumen="Se está inspeccionando la señal de la fuente.")
            continue
        firma = p["senal"]["firma"]
        senal = r["senales"][did] = path_senal(f["id"], firma)
        if firma:
            r["paths"][senal] = {"source": "publisher", "srtPublishPassphrase": ""}
            tid = id_conversion(f["id"], firma)
            if tid not in r["tareas"]:
                r["tareas"][tid] = (comandos.conversion(tid, f["id"], p["senal"], senal, que_trae.get(f["id"]),
                                                        codificador), f["id"], None)
        if empuja(d):
            try:
                cmd = comandos.envio(id_ruta(did), d, senal, p["mapa_audio"], entera=bool(firma) or p["entera"])
                r["tareas"][id_ruta(did)] = (cmd, senal, None)
            except (ValueError, TypeError, KeyError) as err:
                r["tareas"][id_ruta(did)] = (None, None, f"Destino mal configurado: {err}"[:300])
            r["politicas"][id_ruta(did)] = reconexion(d)
            continue
        if not con_senal(senal):
            continue                                # el path del destino se declara al llegar la señal
        conf = {"source": mtx.url_lectura(senal), "sourceOnDemand": False,
                "srtReadPassphrase": d["passphrase"] if d["protocolo"] == "srt" else "", "forward": []}
        if reenvia_whip(d):
            conf["forward"] = [{"dest": _whip(d["url"]), "whipBearerToken": d["clave"]}]
        r["paths"][did] = conf
    return r


# ---------------------------------------------------------------- servidores de MediaMTX
RED = {"rtmp_puerto": 1935, "webrtc_puerto": 8889, "webrtc_udp": 8189}


def ajustes_red(c, cfg):
    """Puertos y direcciones del hub: los de Ajustes y, si no se han tocado, los de config.json."""
    guardado = db.ajuste(c, "red") or {}
    r = {}
    for clave, defecto in RED.items():
        try:
            r[clave] = int(guardado.get(clave) or cfg.get(clave) or defecto)
        except (TypeError, ValueError):
            r[clave] = defecto
    for clave in ("host_publico", "hls_url", "webrtc_url", "hls_puerto", "mediamtx_srt", "srt_puerto"):
        if guardado.get(clave) or cfg.get(clave):
            r[clave] = guardado.get(clave) or cfg.get(clave)
    return r


def servicios(c, cfg):
    """Servidores de MediaMTX que hacen falta: RTMP si alguna fuente activa publica por RTMP o algún
    destino se entrega por RTMP, y WebRTC igual. Si nadie los usa, se quedan apagados."""
    fs = [f for f in fuentes(c) if f["activo"] and publica_solo(f)]
    ds = [d for d in destinos(c) if entrega(d) or reenvia_whip(d)]
    usados = {x["protocolo"] for x in [*fs, *ds]}
    n, webrtc = ajustes_red(c, cfg), db.ajuste(c, "webrtc") or {}
    return {"rtmp": {"puerto": n["rtmp_puerto"]} if "rtmp" in usados else None,
            "webrtc": {"puerto": n["webrtc_puerto"], "udp": n["webrtc_udp"], "hosts": webrtc.get("hosts") or [],
                       "ice": webrtc.get("ice") or []} if "webrtc" in usados else None}


# ---------------------------------------------------------------- direcciones que el hub entrega
def red(cfg):
    """Direcciones públicas del hub, tal como hay que dárselas a otro equipo. `cfg`: config.json o,
    mejor, ajustes_red() (que añade lo que se haya cambiado en Ajustes)."""
    host = cfg.get("host_publico") or "127.0.0.1"
    srt = urllib.parse.urlsplit(cfg.get("mediamtx_srt", "srt://127.0.0.1:8890")).port or 8890
    return {"host": host, "srt": int(cfg.get("srt_puerto") or srt),
            "rtmp": int(cfg.get("rtmp_puerto") or RED["rtmp_puerto"]),
            "hls": (cfg.get("hls_url") or f"http://{host}:{int(cfg.get('hls_puerto') or 8888)}").rstrip("/"),
            "webrtc": (cfg.get("webrtc_url")
                       or f"http://{host}:{int(cfg.get('webrtc_puerto') or RED['webrtc_puerto'])}").rstrip("/")}


def _campo(nombre, valor, secreto=False):
    return {"nombre": nombre, "valor": valor, "secreto": secreto}


def _srt_entrega(n, accion, fila, clave, frase):
    streamid = f"{accion}:{fila['id']}" + (f":{fila['id']}:{clave}" if clave else "")
    q = {"streamid": streamid, "latency": str(int(fila["latencia_ms"]) * 1000)}
    if frase:
        q["passphrase"] = frase
    campos = [_campo("Servidor", n["host"]), _campo("Puerto", str(n["srt"])),
              _campo("Modo del otro equipo", "caller (llama al hub)"),
              _campo("Stream ID", streamid, bool(clave)), _campo("Latencia", f"{int(fila['latencia_ms'])} ms")]
    if frase:
        campos.append(_campo("Passphrase", frase, True))
    return {"url": f"srt://{n['host']}:{n['srt']}?" + urllib.parse.urlencode(q, safe=":"), "campos": campos}


def _direccion(fila, n, lado, clave, frase):
    proto, i = fila["protocolo"], fila["id"]
    opciones = json_de(fila.get("opciones"), {})
    if proto == "srt":
        if opciones.get("puerto"):          # puerto propio: sin stream ID
            q = {"latency": str(int(fila["latencia_ms"]) * 1000), **({"passphrase": frase} if frase else {})}
            campos = [_campo("Servidor", n["host"]), _campo("Puerto", str(int(opciones["puerto"]))),
                      _campo("Modo del otro equipo", "caller (llama al hub)"),
                      _campo("Latencia", f"{int(fila['latencia_ms'])} ms")]
            if frase:
                campos.append(_campo("Passphrase", frase, True))
            return {"url": f"srt://{n['host']}:{int(opciones['puerto'])}?" + urllib.parse.urlencode(q), "campos": campos}
        return _srt_entrega(n, "publish" if lado == "fuente" else "read", fila, clave, frase)
    if proto == "rtmp":
        llave = i + (f"?user={i}&pass={clave}" if clave else "")
        servidor = f"rtmp://{n['host']}:{n['rtmp']}/"
        nombre_llave = "Clave de emisión" if lado == "fuente" else "Nombre de la señal"
        return {"url": servidor + llave, "campos": [_campo("Servidor", servidor), _campo(nombre_llave, llave, bool(clave))]}
    if proto == "hls":
        campos = [_campo("Usuario", i), _campo("Contraseña", clave, True)] if clave else []
        return {"url": f"{n['hls']}/{i}/index.m3u8", "campos": campos}
    if proto == "webrtc":
        final = "whip" if lado == "fuente" else "whep"
        campos = [_campo("Token (Bearer)", f"{i}:{clave}", True)] if clave else []
        r = {"url": f"{n['webrtc']}/{i}/{final}", "campos": campos}
        if lado == "destino":
            r["pagina"] = f"{n['webrtc']}/{i}/"
        return r
    return None


def direccion(fila, n, lado, ver_claves=False):
    """Dirección que el hub entrega para una fuente a la que el origen llama o para un destino al que
    viene el receptor: la URL completa y sus campos por separado. Sin permiso para ver claves, cada
    clave sale como «***»."""
    if fila["protocolo"] in P.CON_ADAPTADOR:
        return None
    if lado == "fuente" and fila["inicia"] != "origen":
        return None
    if lado == "destino" and not (entrega(fila) or (fila["protocolo"] == "srt" and fila["inicia"] == "receptor")):
        return None
    clave = fila["clave"] if not json_de(fila.get("opciones"), {}).get("puerto") else ""
    frase = fila["passphrase"] if fila["protocolo"] == "srt" else ""
    if not ver_claves:
        clave, frase = ("***" if clave else ""), ("***" if frase else "")
    r = _direccion(fila, n, lado, clave, frase)
    if r:
        r["con_claves"] = bool(ver_claves)
        r["protegida"] = bool(clave or frase)
    return r


# ---------------------------------------------------------------- claves
def secretos(c):
    """Valores del hub que no deben aparecer en un registro ni en un texto: las claves y passphrases
    de fuentes y destinos, lo que lleven sus URL y las credenciales de los servidores ICE."""
    import secretos as S
    valores = set()
    for fila in [*fuentes(c), *destinos(c)]:
        valores |= S.valores_de(fila)
    for servidor in (db.ajuste(c, "webrtc") or {}).get("ice") or []:
        if isinstance(servidor, dict):
            valores.add(str(servidor.get("clave") or ""))
    return {v for v in valores if v}
