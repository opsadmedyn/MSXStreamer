"""Media Syntaxis Streamer - API del hub de señales: fuentes, destinos, rutas y su estado.

Como en el resto del panel, aquí solo se guarda lo que se quiere (fuentes, destinos, rutas) y el
supervisor lo cumple. Lo propio de este módulo:

  Permisos   Cada operación declara el permiso que exige (PERMISOS). Los roles son los del Recorder
             (operador, manager, admin) y qué puede cada uno se decide en Ajustes. Tocar una ruta
             que está al aire pide un permiso aparte del de conectar un destino libre.
  Claves     Las que el hub entrega para que otro equipo publique o lea (las genera el hub) solo
             las ve quien tiene el permiso «ver_claves», pidiéndolas aposta, y queda anotado. Las
             de un equipo ajeno (su passphrase, su clave de emisión, lo que lleve su URL) se
             escriben y no vuelven a salir: para cambiarlas se escriben otra vez.
  Cambios    Quién cambió qué y cuándo queda en la tabla «cambios», sin claves.
  Estado     /api/matriz devuelve todo de una vez: cada fuente y cada ruta con su estado, la
             evidencia que lo respalda y cuándo se comprobó (ver estados.py).

Las direcciones de todo esto no chocan con las de los flujos de siempre (/api/flujos, /api/salidas),
que siguen como estaban: un flujo aparece aquí como una fuente más («heredada»).
"""

import calendar
import json
import re
import secrets as aleatorio
import subprocess
import threading
import time
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import adaptadores
import carga
import db
import estados
import inspeccion
import matriz
import medidores
import mtx
import plan
import protocolos as P
import secretos
import sondas
import validacion

router = APIRouter()
CFG = db.config()
# Direcciones de este módulo. El panel deja pasar en ellas a cualquier rol (también lo que no es
# GET), porque aquí cada operación comprueba su permiso.
PROPIAS = re.compile(r"^/api/(matriz|fuentes|destinos|rutas|perfiles|ajustes|adaptadores|cambios|carga|probar|validacion)(/|$)")
SIN_CACHE = {"Cache-Control": "no-store"}

# ---------------------------------------------------------------- permisos
PERMISOS = {
    "ver": "Ver fuentes, destinos y monitoreo",
    "conectar_libre": "Conectar una ruta a un destino libre",
    "modificar_al_aire": "Modificar una ruta al aire",
    "editar": "Crear y editar fuentes y destinos",
    "ver_claves": "Ver claves, passphrases y tokens",
    "ajustes": "Cambiar ajustes y adaptadores",
}
ROLES = ("operador", "manager", "admin")
POR_DEFECTO = {"operador": ("ver", "conectar_libre"),
               "manager": ("ver", "conectar_libre", "modificar_al_aire", "editar")}


def tabla_permisos(c):
    """{rol: [permisos]}. El administrador puede todo, siempre: no se guarda ni se cambia."""
    guardado = db.ajuste(c, "permisos") or {}
    tabla = {}
    for rol, defecto in POR_DEFECTO.items():
        lista = guardado.get(rol) if isinstance(guardado, dict) and isinstance(guardado.get(rol), list) else defecto
        tabla[rol] = [p for p in PERMISOS if p in lista]
    tabla["admin"] = list(PERMISOS)
    return tabla


def permisos_de(c, rol):
    """Permisos de un rol. Uno que el hub no conoce puede lo mismo que el operador."""
    return set(tabla_permisos(c)[rol if rol in ROLES else "operador"])


def _usuario(request):
    u = getattr(request.state, "usuario", None)
    if not u:
        raise HTTPException(401, "Sesión caducada")
    return u


def _puede(request, c, permiso):
    return permiso in permisos_de(c, _usuario(request)["rol"])


def _exigir(request, c, permiso, porque=""):
    u = _usuario(request)
    if permiso not in permisos_de(c, u["rol"]):
        que = PERMISOS[permiso][0].lower() + PERMISOS[permiso][1:]
        raise HTTPException(403, f"Tu rol ({u['rol']}) no puede {que}" + (f": {porque}" if porque else ""))


def exige(permiso):
    """Dependencia de FastAPI: la operación solo pasa con ese permiso. Todas las del hub llevan una
    (lo comprueba tests/test_api_hub.py), porque el panel deja pasar aquí a cualquier rol."""
    def comprobar(request: Request):
        _exigir(request, db.conectar(), permiso)
    comprobar.permiso = permiso
    return Depends(comprobar)


def _ocultos(c):
    """Todo lo guardado que no debe verse en un texto: lo del hub y lo de los flujos de siempre."""
    valores = matriz.secretos(c)
    for fila in [*db.filas(c, "SELECT * FROM flujos"), *db.filas(c, "SELECT * FROM salidas")]:
        valores |= secretos.valores_de(fila)
    return secretos.ordenar(valores)


def _cambio(c, request, accion, objeto="", detalle=""):
    u = _usuario(request)
    db.cambio(c, u["email"], u["rol"], accion, objeto, secretos.ocultar(detalle, _ocultos(c)))


# ---------------------------------------------------------------- lo que hay ahora mismo
_foto = (0.0, None)             # (instante, (paths, conexiones, muxers HLS)): MediaMTX, como mucho una vez por segundo
_muestras = {}                  # (path, 'rx' | 'tx') -> (instante, bytes, última tasa)
_cerrojo = threading.Lock()
_FECHA = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)$")


def epoca(texto):
    """De una fecha de MediaMTX («2026-11-19T06:37:00.890324259Z») a segundos, o None."""
    m = _FECHA.match(texto or "")
    if not m:
        return None
    base = calendar.timegm(tuple(int(m.group(i)) for i in range(1, 7)) + (0, 0, 0))
    zona = m.group(8)
    desfase = 0 if zona == "Z" else (1 if zona[0] == "+" else -1) * (int(zona[1:3]) * 3600 + int(zona[4:6]) * 60)
    return base - desfase + float(m.group(7) or 0)


def _mediamtx():
    """(paths, conexiones, muxers HLS) de MediaMTX, o (None, {}, {}) si no responde."""
    global _foto
    with _cerrojo:
        if time.monotonic() - _foto[0] < 1.0 and _foto[1] is not None:
            return _foto[1]
        try:
            foto = (mtx.paths_estado(), mtx.conexiones(), mtx.muxers_hls())
        except mtx.ErrorMTX:
            foto = (None, {}, {})
        _foto = (time.monotonic(), foto)
        return foto


_servidores = (0.0, None)


def _servidores_mtx():
    """Los servidores que MediaMTX tiene encendidos ({"rtmp": bool, "webrtc": bool}), o None si no
    se sabe (no responde). Como mucho una consulta por segundo."""
    global _servidores
    if time.monotonic() - _servidores[0] < 1.0:
        return _servidores[1]
    try:
        encendidos = mtx.servidores_encendidos()
    except mtx.ErrorMTX:
        encendidos = None
    _servidores = (time.monotonic(), encendidos)
    return encendidos


def _tasa(clave, total, ahora):
    """kb/s a partir de un contador de bytes que crece. La primera lectura no da tasa."""
    antes = _muestras.get(clave)
    if total is None:
        _muestras.pop(clave, None)
        return None
    if not antes or total < antes[1]:                   # contador nuevo (el origen volvió a conectar)
        _muestras[clave] = (ahora, total, None)
        return None
    if ahora - antes[0] < 1.0:
        return antes[2]
    tasa = round((total - antes[1]) * 8 / 1000 / (ahora - antes[0]), 1)
    _muestras[clave] = (ahora, total, tasa)
    return tasa


class Contexto:
    """Todo lo que hace falta para describir el hub en un instante: lo guardado, lo que dice MediaMTX,
    lo que anotó el supervisor y lo que el propio hub calcula que debe correr."""

    def __init__(self, c):
        self.c, self.ahora = c, time.time()
        self.paths, self.conexiones, self.hls = _mediamtx()
        self.fuentes = {f["id"]: f for f in matriz.todas_las_fuentes(c)}
        self.destinos = {d["id"]: d for d in matriz.destinos(c)}
        self.rutas = {r["destino"]: r for r in matriz.rutas(c)}
        self.perfiles = matriz.perfiles(c)
        self.tareas = {e["salida"]: e for e in db.filas(c, "SELECT * FROM estado_salidas")}
        self.senales = {s["path"]: s for s in db.filas(c, "SELECT * FROM senales")}
        self.inspecciones = {i["fuente"]: i for i in db.filas(c, "SELECT * FROM inspecciones")}
        self.adaptadores = adaptadores.estado()
        self.reposo = db.reposo_recorder()
        self.hub = matriz.vacio() if self.reposo is not None else \
            matriz.calcular(c, self.paths, adaptadores=self.adaptadores)
        self.red = matriz.red(matriz.ajustes_red(c, CFG))
        self.ocultos = _ocultos(c)
        self.validaciones = validacion.leer(c)
        self.estados_fuente, self.estados_ruta = {}, {}
        self.encendidos = _servidores_mtx()
        self.problemas = (db.ajuste(c, "servidores") or {}).get("problemas") or {}

    def servidor(self, protocolo):
        """None si el servidor de MediaMTX de ese protocolo está encendido (o no hace falta, o no se
        sabe); si no, {"nombre", "motivo"}: con motivo cuando el supervisor no ha podido encenderlo."""
        if protocolo not in mtx.SERVIDORES:
            return None                                 # SRT y HLS están siempre: vienen en mediamtx.yml
        motivo = self.problemas.get(protocolo) or ""
        if motivo or (self.encendidos is not None and not self.encendidos.get(protocolo)):
            return {"nombre": P.NOMBRES[protocolo], "motivo": motivo}
        return None

    def info(self, path):
        """Lo que dice MediaMTX de un path, con su «listo desde» en segundos."""
        i = (self.paths or {}).get(path)
        if not i:
            return None
        return {"ready": bool(i.get("ready")), "listo_desde": epoca(i.get("readyTime")),
                "tracks": [t if isinstance(t, str) else str((t or {}).get("codec", "")) for t in i.get("tracks") or []],
                "source": i.get("source"), "readers": i.get("readers") or [],
                "recibidos": i.get("bytesReceived"), "enviados": i.get("bytesSent")}

    def quien(self, ref):
        """Una conexión de MediaMTX ({type, id}) dicha para el panel, sin nada que no deba verse."""
        tipo = (ref or {}).get("type") or ""
        con = self.conexiones.get((tipo, ref.get("id"))) if ref else None
        if not con:
            return {"protocolo": mtx.PROTOCOLO_DE.get(tipo, tipo), "remoto": "", "desde": None, "local": False}
        remoto = con["remoto"].rsplit(":", 1)[0].strip("[]")
        v = {"protocolo": con["protocolo"], "remoto": remoto, "desde": epoca(con["desde"]), "local": remoto in secretos.LOCALES}
        for k in ("rtt_ms", "perdidos", "reenviados"):
            if con.get(k) is not None:
                v[k] = round(con[k], 1) if isinstance(con[k], float) else con[k]
        return v

    def _limpio(self, e):
        """El estado sin nada que parezca una clave: los textos de ffmpeg ya llegan limpios del
        lanzador (correr.py), pero aquí se vuelven a pasar antes de salir hacia el navegador."""
        e["detalle"] = secretos.ocultar(e["detalle"], self.ocultos)
        for hecho in e["evidencia"]:
            hecho["que"] = secretos.ocultar(hecho["que"], self.ocultos)
        return e

    # ---- fuentes
    def estado_fuente(self, f):
        if f["id"] not in self.estados_fuente:
            self.estados_fuente[f["id"]] = self._limpio(self._estado_fuente(f))
        return self.estados_fuente[f["id"]]

    def _estado_fuente(self, f):
        if self.reposo is not None:
            return estados.en_reposo(self.reposo.get("desde"), self.ahora)
        info, tid = self.info(f["id"]), matriz.id_entrada(f["id"])
        lleva = tid in self.hub["tareas"]
        kbps = _tasa((f["id"], "rx"), info["recibidos"] if info and info["ready"] else None, self.ahora)
        origen = ""
        if info and info["ready"] and (info["source"] or {}).get("id"):
            q = self.quien(info["source"])
            origen = f"{q['remoto']} por {q['protocolo']}" if q["remoto"] and not q["local"] else ""
        h = f.get("heredada")
        sin_senal = "Nadie está publicando en esta fuente" if not h else \
            "La composición del flujo no está en marcha" if h["senal"] == "compuesta" else "El flujo no recibe señal de su entrada"
        e = estados.de_fuente(f, info, tarea=(self.tareas.get(tid) or {}) if lleva else None,
                              senal=self.senales.get(f["id"]), kbps=kbps, mtx_ok=self.paths is not None,
                              impedimento=self.hub["tareas"][tid][2] if lleva else None, origen=origen,
                              ahora=self.ahora, sin_senal=sin_senal,
                              servidor=self.servidor(f["protocolo"]) if not h and matriz.publica_solo(f) else None)
        e["kbps"] = kbps
        return e

    # ---- rutas
    def tipo_de(self, d):
        if d["protocolo"] in P.CON_ADAPTADOR:
            return "adaptador"
        return "empuja" if matriz.empuja(d) else "whip" if matriz.reenvia_whip(d) else "entrega"

    def lectores(self, d):
        """Quién está leyendo ahora el path de un destino que el hub entrega."""
        info, r = self.info(d["id"]), []
        for ref in (info or {}).get("readers") or []:
            if ref.get("type") == "hlsMuxer":
                ultima = epoca((self.hls.get(d["id"]) or {}).get("ultima_peticion"))
                if ultima and self.ahora - ultima <= 20:            # el muxer sigue abierto 60 s sin nadie pidiendo
                    r.append({"protocolo": "HLS", "remoto": "", "desde": None, "ultima_peticion": ultima,
                              "tipo": "un reproductor HLS", "local": False})
            else:
                r.append(self.quien(ref))
        return r

    def estado_ruta(self, r):
        if r["destino"] not in self.estados_ruta:
            self.estados_ruta[r["destino"]] = self._limpio(self._estado_ruta(r))
        return self.estados_ruta[r["destino"]]

    def _estado_ruta(self, r):
        d, f = self.destinos[r["destino"]], self.fuentes.get(r["fuente"])
        if self.reposo is not None:
            return estados.en_reposo(self.reposo.get("desde"), self.ahora)
        if not f:
            return estados.de_ruta(d, impedimento="La fuente de esta ruta ya no existe", ahora=self.ahora)
        did, tipo, tid = d["id"], self.tipo_de(d), matriz.id_ruta(d["id"])
        impedimento = self.hub["impedimentos"].get(did)
        definida = self.hub["tareas"].get(tid)
        if not impedimento and definida and definida[0] is None:
            impedimento = definida[2]
        senal = self.hub["senales"].get(did)
        derivada = bool(senal) and senal != f["id"]
        info_senal = self.info(senal) if senal else None
        info_destino = self.info(did) if tipo in ("entrega", "whip") else None
        lectores = self.lectores(d) if tipo == "entrega" else []
        kbps = _tasa((did, "tx"), info_destino["enviados"] if info_destino and info_destino["ready"] else None, self.ahora) \
            if tipo == "entrega" else None
        e = estados.de_ruta(d, plan=self.hub["planes"].get(did), fuente=self.estado_fuente(f),
                            senal_lista=not senal or bool(info_senal and info_senal["ready"]),
                            conversion=(self.tareas.get(senal) or {}) if derivada else None,
                            tarea=self.tareas.get(tid) if tipo in ("empuja", "adaptador") else None,
                            info_destino=info_destino, lectores=lectores, kbps=kbps, mtx_ok=self.paths is not None,
                            impedimento=impedimento, tipo=tipo, ahora=self.ahora,
                            servidor=self.servidor(d["protocolo"]) if tipo in ("entrega", "whip") else None)
        e.update(senal=senal, derivada=derivada, lectores=lectores, kbps_entrega=kbps)
        return e

    def al_aire(self, destino=None, fuente=None, perfil=None):
        """Rutas al aire de un destino, de una fuente o con un perfil: [(ruta, estado)]."""
        r = []
        for ruta in self.rutas.values():
            if (destino and ruta["destino"] == destino) or (fuente and ruta["fuente"] == fuente) \
                    or (perfil and ruta["perfil"] == perfil):
                e = self.estado_ruta(ruta)
                if estados.al_aire(e):
                    r.append((ruta, e))
        return r

    def nombre(self, tabla, i):
        fila = (self.fuentes if tabla == "fuentes" else self.destinos).get(i)
        return fila["nombre"] if fila else i


# ---------------------------------------------------------------- vistas
def _pistas(pistas):
    """Las pistas de una inspección, cada una con su texto («H.264 · 1920×1080 · 59,94 fps»)."""
    if not pistas:
        return None
    v = pistas.get("video")
    return {"video": dict(v, texto=plan.texto_video(v), pasa=v.get("codec") in P.ENLACE_VIDEO) if v else None,
            "audio": [dict(a, pista=i + 1, texto=plan.texto_audio(a), nombre_pista=plan.nombre_pista(i, a),
                           pasa=a.get("codec") in P.ENLACE_AUDIO) for i, a in enumerate(pistas.get("audio") or [])],
            "parcial": bool(pistas.get("parcial")), "sin_confirmar": bool(pistas.get("sin_confirmar"))}


def _remoto(fila):
    """Lo que se sabe de la dirección de un equipo ajeno, sin sus claves: no vuelven a salir."""
    visible = secretos.url_visible(fila["url"], fila["protocolo"], fila["clave"])
    return {"url": visible, "url_con_claves": visible != fila["url"], "tiene_streamid": bool(fila["streamid"]),
            "tiene_passphrase": bool(fila["passphrase"]), "tiene_clave": bool(fila["clave"])}


def _comun(fila, lado):
    return {"id": fila["id"], "nombre": fila["nombre"], "protocolo": fila["protocolo"],
            "protocolo_nombre": P.NOMBRES.get(fila["protocolo"], fila["protocolo"]), "inicia": fila["inicia"],
            "quien_inicia": P.quien_inicia(lado, fila["protocolo"], fila["inicia"]), "sentido": P.direccion(lado),
            "latencia_ms": fila["latencia_ms"], "opciones": matriz.json_de(fila.get("opciones"), {}), "creado": fila["creado"]}


def _adaptador(fila, x):
    a = x.adaptadores.get(fila["protocolo"]) or {}
    return {"listo": bool(a.get("listo")), "falta": a.get("falta") or "", "tiene_clave": bool(fila["clave"])}


def vista_fuente(f, x):
    fid, h = f["id"], f.get("heredada")
    v = _comun(f, "fuente")
    ins = x.inspecciones.get(fid)
    estado = x.estado_fuente(f)
    v.update(activo=bool(f["activo"]), heredada=h, estado=estado, kbps=estado.get("kbps"),
             pistas=_pistas(matriz.json_de(ins["pistas"], None)) if ins else None,
             inspeccion={"t": ins["t"], "error": secretos.ocultar(ins["error"], x.ocultos)} if ins else None,
             ultima_senal=(x.senales.get(fid) or {}).get("ultima"), con_senal=estado["estado"] == estados.RECIBIENDO,
             rutas=sorted(r["destino"] for r in x.rutas.values() if r["fuente"] == fid),
             vista={"hls": f"hls/{urllib.parse.quote(fid)}/"}, direccion=None, remoto=None)
    if h:
        v["quien_inicia"] = "La genera la composición del flujo" if h["senal"] == "compuesta" else \
            {"hub": "La trae un Hub channel", "recorder": "La entrega el Recorder de este equipo",
             "pull": "El Streamer llama al origen (SRT)"}.get(h["entrada"], v["quien_inicia"])
    elif f["protocolo"] in P.CON_ADAPTADOR:
        v["adaptador"] = _adaptador(f, x)
    elif f["inicia"] == "origen":
        v["direccion"] = matriz.direccion(f, x.red, "fuente")
    else:
        v["remoto"] = _remoto(f)
    return v


def _metricas(d, r, e, x):
    """Lo que se mide de una ruta: lo que sale, desde cuándo, cuántas veces se ha reconectado y quién la recibe."""
    tarea = x.tareas.get(matriz.id_ruta(d["id"])) or {}
    m = {"kbps": tarea.get("kbps") if tarea.get("pid") else e.get("kbps_entrega"),
         "enviando_desde": tarea.get("desde") if tarea.get("pid") else None,
         "reconexiones": tarea.get("reinicios") or 0, "intentos": tarea.get("intentos") or 0,
         "receptores": e.get("lectores") or [], "conversion": None}
    conv = x.tareas.get(e.get("senal")) if e.get("derivada") else None
    if conv is not None:
        m["conversion"] = {"id": e["senal"], "kbps": conv.get("kbps"), "desde": conv.get("desde") if conv.get("pid") else None,
                           "reinicios": conv.get("reinicios") or 0,
                           "compartida_con": sorted(x.nombre("destinos", o) for o, s in x.hub["senales"].items()
                                                    if s == e["senal"] and o != d["id"])}
    return m


def vista_destino(d, x):
    did = d["id"]
    v = _comun(d, "destino")
    entrega = d["inicia"] == "receptor"
    v.update(modo="entrega" if entrega else "conecta",
             modo_texto="El hub entrega esta dirección para que otro equipo la abra" if entrega
             else "El hub se conecta a este destino",
             perfil=d["perfil"], audio=d["audio"], reconexion=matriz.reconexion(d), direccion=None, remoto=None, ruta=None)
    if d["protocolo"] in P.CON_ADAPTADOR:
        v["adaptador"] = _adaptador(d, x)
    elif entrega:
        v["direccion"] = matriz.direccion(d, x.red, "destino")
    else:
        v["remoto"] = _remoto(d)
    r = x.rutas.get(did)
    if r:
        e = x.estado_ruta(r)
        f = x.fuentes.get(r["fuente"])
        senal = e.get("senal") or (f["id"] if f else None)
        v["ruta"] = {"fuente": r["fuente"], "fuente_nombre": f["nombre"] if f else "(fuente borrada)",
                     "perfil": r["perfil"], "audio": r["audio"], "desde": r["desde"], "por": r["por"],
                     "estado": {k: e[k] for k in ("estado", "nombre", "detalle", "evidencia", "actualizado")},
                     "sin_confirmar": bool(e.get("sin_confirmar")), "al_aire": estados.al_aire(e),
                     "plan": x.hub["planes"].get(did), "senal": e.get("senal"),
                     "vista": {"hls": f"hls/{urllib.parse.quote(senal)}/"} if senal else None,
                     "metricas": _metricas(d, r, e, x),
                     "validacion": validacion.de(x.validaciones, f, d) if f else None}
    return v


def _sistema(x):
    """Lo que mueve el hub: MediaMTX responde y el supervisor sigue dando vueltas (anota un latido en cada una)."""
    latido = db.ajuste(x.c, "latido")
    hace = round(x.ahora - latido, 1) if isinstance(latido, (int, float)) else None
    servidores = db.ajuste(x.c, "servidores") or {}
    return {"mediamtx": x.paths is not None, "supervisor": hace is not None and hace < estados.CADUCA,
            "supervisor_visto_s": hace,
            "reposo": None if x.reposo is None else {"desde": x.reposo.get("desde"), "por": x.reposo.get("por")},
            "problemas": servidores.get("problemas") or {}, "arranque": db.ajuste(x.c, "arranque")}


def _estado_completo(c):
    x = Contexto(c)
    return {"t": x.ahora, "sistema": _sistema(x),
            "fuentes": [vista_fuente(f, x) for f in x.fuentes.values()],
            "destinos": [vista_destino(d, x) for d in x.destinos.values()],
            "perfiles": x.perfiles,
            "protocolos": {p: {"nombre": P.NOMBRES[p], "entradas": list(P.ENTRADAS[p]), "salidas": list(P.SALIDAS[p]),
                               "adaptador": p in P.CON_ADAPTADOR} for p in P.PROTOCOLOS}}


_ultimo = (0.0, None)


def _estado_del_hub(c):
    """El estado completo, calculado como mucho una vez por segundo aunque lo pidan varios paneles."""
    global _ultimo
    if _ultimo[1] is not None and time.monotonic() - _ultimo[0] < 1.0:
        return _ultimo[1]
    estado = _estado_completo(c)
    _ultimo = (time.monotonic(), estado)
    return estado


def _olvidar():
    """Tras un cambio: la siguiente consulta no usa lo calculado antes."""
    global _ultimo
    _ultimo = (0.0, None)


def paths_con_vista(c):
    """Paths del hub que se pueden ver por el panel (vista previa): las fuentes y las señales
    convertidas. Los de los destinos no: así un lector en el path de un destino es siempre un
    receptor de verdad, nunca el propio panel."""
    paths = {f["id"] for f in matriz.fuentes(c)}
    try:
        paths |= {p for p in matriz.calcular(c)["paths"] if p.startswith("conv_")}
    except Exception:                       # una ruta mal guardada no deja al panel sin vista previa
        pass
    return paths


@router.get("/api/matriz", dependencies=[exige("ver")])
def ver_matriz(request: Request):
    """Todo el hub de una vez: fuentes, destinos con su ruta, perfiles y lo que puede hacer quien pregunta."""
    c = db.conectar()
    return {**_estado_del_hub(c), "permisos": sorted(permisos_de(c, _usuario(request)["rol"]))}


@router.get("/api/fuentes", dependencies=[exige("ver")])
def listar_fuentes():
    return _estado_del_hub(db.conectar())["fuentes"]


@router.get("/api/destinos", dependencies=[exige("ver")])
def listar_destinos():
    return _estado_del_hub(db.conectar())["destinos"]


# ---------------------------------------------------------------- validación de lo que se guarda
class Fuente(BaseModel):
    nombre: Optional[str] = None
    protocolo: Optional[str] = None
    inicia: Optional[str] = None
    url: Optional[str] = None
    streamid: Optional[str] = None
    passphrase: Optional[str] = None
    clave: Optional[str] = None             # sin dar: se conserva (al crear, la genera el hub); "": sin clave
    latencia_ms: Optional[int] = None
    puerto: Optional[int] = None            # SRT con puerto propio en el hub (0: quitarlo)
    variante: Optional[int] = None          # HLS: qué variante de la lista
    opciones: Optional[dict] = None         # NDI y Zixi
    activo: Optional[bool] = None


class Destino(Fuente):
    perfil: Optional[str] = None
    audio: Optional[str] = None
    reconexion: Optional[dict] = None


URL_SRT = re.compile(r"^srt://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+):(\d{1,5})(\?[^\s]*)?$")
URL_RTMP = re.compile(r"^rtmps?://[^\s/?#]+(/[^\s]*)?$")
URL_HTTP = re.compile(r"^https?://[^\s/?#]+([/?][^\s]*)?$")
URL_DE = {"srt": (URL_SRT, "srt://203.0.113.20:9000"), "rtmp": (URL_RTMP, "rtmp://a.rtmp.youtube.com/live2"),
          "hls": (URL_HTTP, "https://ejemplo.com/senal/index.m3u8"), "webrtc": (URL_HTTP, "https://ejemplo.com/whip/sala")}
CLAVE_PROPIA = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
TEXTO = re.compile(r"^[^\x00-\x1f\x7f]*$")
QUE = {"fuente": ("fuente", "una fuente"), "destino": ("destino", "un destino")}
# Lo que se puede guardar de un adaptador. Sus claves no van aquí: van en «clave», que no vuelve a salir.
OPCIONES_ADAPTADOR = {"ndi": ("nombre", "equipo", "grupo", "calidad"), "zixi": ("modo", "host", "puerto", "canal")}
NOMBRES_CAMPOS = {"nombre": "nombre", "url": "dirección", "streamid": "stream ID", "passphrase": "passphrase",
                  "clave": "clave", "latencia_ms": "latencia", "opciones": "opciones", "activo": "activación",
                  "perfil": "perfil", "audio": "audio", "reconexion": "reconexión"}
CORTAN = ("url", "streamid", "passphrase", "clave", "opciones", "latencia_ms", "activo")    # cambiarlos toca lo que esté al aire


def _texto(valor, nombre, maximo, vacio=True):
    valor = (valor or "").strip()
    if not valor and not vacio:
        raise HTTPException(422, f"Falta {nombre}")
    if len(valor) > maximo or not TEXTO.match(valor):
        raise HTTPException(422, f"{nombre[0].upper() + nombre[1:]} no es válido (máximo {maximo} caracteres, sin saltos de línea)")
    return valor


def _passphrase(valor):
    if valor and not 10 <= len(valor) <= 79:
        raise HTTPException(422, "La passphrase de SRT debe tener entre 10 y 79 caracteres")
    if valor and not TEXTO.match(valor):
        raise HTTPException(422, "La passphrase no puede llevar saltos de línea")
    return valor or ""


def _audio(valor):
    valor = str(valor).strip().lower()
    if valor != "todas" and not (valor.isdigit() and 1 <= int(valor) <= 16):
        raise HTTPException(422, "El audio es «todas» o el número de una pista (desde 1)")
    return valor


def _reconexion(valor):
    try:
        cada, tope = float(valor.get("cada_s") or 0), int(valor.get("max_intentos") or 0)
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(422, "La reconexión lleva «cada_s» (segundos entre intentos) y «max_intentos»")
    if not (0 <= cada <= 600 and 0 <= tope <= 100000):
        raise HTTPException(422, "La reconexión admite de 0 a 600 s entre intentos (0: espera creciente) "
                                 "y de 0 a 100000 intentos (0: sin límite)")
    return {"cada_s": cada, "max_intentos": tope}


def _puertos_ocupados(c, excepto=""):
    """Puertos de este equipo que ya usa el hub: los suyos y los propios de otras fuentes y destinos."""
    n = matriz.ajustes_red(c, CFG)
    usados = {matriz.red(n)["srt"]: "el SRT del hub", n["rtmp_puerto"]: "el RTMP del hub",
              n["webrtc_puerto"]: "el WebRTC del hub", n["webrtc_udp"]: "los medios de WebRTC",
              int(CFG.get("hls_puerto", 8888)): "el HLS del hub", int(CFG.get("panel_puerto", 8095)): "el panel",
              (urllib.parse.urlsplit(CFG.get("mediamtx_api", "http://127.0.0.1:9997")).port or 9997): "la API de MediaMTX"}
    if matriz.rtmps(n):
        usados[matriz.rtmps(n)["puerto"]] = "el RTMPS del hub"
    for fila in [*matriz.fuentes(c), *matriz.destinos(c)]:
        puerto = matriz.json_de(fila.get("opciones"), {}).get("puerto")
        if puerto and fila["id"] != excepto:
            usados[int(puerto)] = f"«{fila['nombre']}»"
    for s in db.filas(c, "SELECT nombre, url FROM salidas WHERE tipo='srt' AND modo='listener'"):
        try:
            usados[urllib.parse.urlsplit(s["url"]).port or 9000] = f"la salida «{s['nombre']}»"
        except ValueError:
            pass
    return usados


def _url(valor, protocolo, antes):
    """La URL nueva, o la guardada si el panel devuelve la que se le enseñó (sin las claves)."""
    valor = (valor or "").strip()
    if antes and valor == secretos.url_visible(antes["url"], antes["protocolo"], antes["clave"]):
        return antes["url"]
    patron, ejemplo = URL_DE[protocolo]
    if not patron.match(valor) or len(valor) > 2000:
        raise HTTPException(422, f"La dirección no es válida. Ejemplo: {ejemplo}")
    if protocolo == "srt" and not 1 <= int(patron.match(valor).group(2)) <= 65535:
        raise HTTPException(422, "El puerto de la dirección no es válido")
    return valor


def _opciones_adaptador(protocolo, lado, extra):
    permitidas = OPCIONES_ADAPTADOR[protocolo]
    if not isinstance(extra, dict) or set(extra) - set(permitidas):
        raise HTTPException(422, f"Las opciones de {P.NOMBRES[protocolo]} son: {', '.join(permitidas)}")
    opciones = {}
    for k, v in extra.items():
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise HTTPException(422, f"La opción «{k}» debe ser un texto o un número")
        opciones[k] = _texto(v, f"la opción «{k}»", 200) if isinstance(v, str) else v
    if protocolo == "ndi" and not opciones.get("nombre"):
        raise HTTPException(422, "Falta el nombre de la fuente NDI" if lado == "fuente"
                            else "Falta el nombre con el que se publica la salida NDI")
    return {k: v for k, v in opciones.items() if v != ""}


def validar(lado, d, antes, c, probando=False):
    """Campos que se guardan de una fuente o un destino, comprobados. `antes`: la fila guardada, o
    None si se crea. Lo que no se manda en una edición se conserva. probando: son los datos de un
    formulario a medio rellenar, que solo se van a probar (el nombre aún no importa)."""
    que = QUE[lado]
    protocolo = antes["protocolo"] if antes else (d.protocolo or "")
    inicia = antes["inicia"] if antes else (d.inicia or "")
    if antes and ((d.protocolo and d.protocolo != protocolo) or (d.inicia and d.inicia != inicia)):
        raise HTTPException(422, f"El protocolo y quién inicia la conexión no se cambian: crea {que[1]} con los datos nuevos")
    if not (P.valida_entrada if lado == "fuente" else P.valida_salida)(protocolo, inicia):
        raise HTTPException(422, f"{P.NOMBRES.get(protocolo, 'Ese protocolo')} no admite ese modo de conexión como {que[0]}")
    guardadas = matriz.json_de(antes["opciones"], {}) if antes else {}
    campos, opciones = {}, dict(guardadas)
    if not probando and (d.nombre is not None or not antes):
        campos["nombre"] = _texto(d.nombre, "el nombre", 80, vacio=False)
    if d.latencia_ms is not None:
        if not 20 <= d.latencia_ms <= 8000:
            raise HTTPException(422, "La latencia debe estar entre 20 y 8000 ms")
        campos["latencia_ms"] = d.latencia_ms
    if protocolo in P.CON_ADAPTADOR:
        if d.opciones is not None or not antes:
            opciones = _opciones_adaptador(protocolo, lado, d.opciones or {})
        if d.clave is not None:
            campos["clave"] = _texto(d.clave, "la clave", 512)
    elif inicia == "hub":                               # el hub llama: las claves son del otro equipo
        if d.url is not None or not antes:
            campos["url"] = _url(d.url, protocolo, antes)
        if protocolo == "srt":
            if d.streamid is not None:
                campos["streamid"] = _texto(d.streamid, "el stream ID", 512)
            if d.passphrase is not None:
                campos["passphrase"] = _passphrase(d.passphrase)
        elif protocolo in ("rtmp", "webrtc") and d.clave is not None:
            campos["clave"] = _texto(d.clave, "la clave", 512)
        if protocolo == "hls" and d.variante is not None:
            if not 0 <= d.variante <= 63:
                raise HTTPException(422, "La variante de la lista HLS no es válida")
            opciones["variante"] = d.variante
    else:                                               # el otro equipo llama al hub: las claves las pone el hub
        if protocolo == "srt":
            if d.puerto is not None:
                if d.puerto == 0:
                    opciones.pop("puerto", None)
                else:
                    ocupado = _puertos_ocupados(c, antes["id"] if antes else "").get(d.puerto)
                    if not 1024 <= d.puerto <= 65535 or ocupado:
                        raise HTTPException(422, f"El puerto {d.puerto} ya lo usa {ocupado}" if ocupado
                                            else "El puerto propio debe estar entre 1024 y 65535")
                    opciones["puerto"] = d.puerto
            if d.passphrase is not None:
                campos["passphrase"] = _passphrase(d.passphrase)
        con_puerto = bool(opciones.get("puerto"))
        if d.clave is not None:
            if d.clave and not CLAVE_PROPIA.match(d.clave):
                raise HTTPException(422, "La clave debe tener entre 8 y 64 letras, cifras, guiones o guiones bajos")
            campos["clave"] = d.clave
        elif not antes:                                 # al crear: protegida, salvo un destino HLS (lo abre un reproductor)
            campos["clave"] = "" if lado == "destino" and protocolo == "hls" else matriz.nueva_clave()
        if con_puerto:                                  # un puerto propio no lleva stream ID: no hay dónde mandar la clave
            campos["clave"] = ""
    if opciones != guardadas:
        campos["opciones"] = opciones
    if lado == "fuente" and d.activo is not None:
        campos["activo"] = int(d.activo)
    if lado == "destino":
        if d.perfil is not None:
            if d.perfil not in {p["id"] for p in matriz.perfiles(c)}:
                raise HTTPException(422, "Ese perfil no existe")
            campos["perfil"] = d.perfil
        if d.audio is not None:
            campos["audio"] = _audio(d.audio)
        if d.reconexion is not None:
            campos["reconexion"] = _reconexion(d.reconexion)
    return campos


def _novedades(campos, antes):
    """De lo validado, solo lo que de verdad cambia respecto de lo guardado."""
    r = {}
    for k, v in campos.items():
        guardado = antes[k]
        if k == "opciones":
            guardado = matriz.json_de(guardado, {})
        elif k == "reconexion":
            guardado = matriz.reconexion(antes)
        if v != guardado:
            r[k] = v
    return r


def _fila(c, tabla, i):
    r = c.execute(f"SELECT * FROM {tabla} WHERE id=?", (i,)).fetchone()
    if r:
        return dict(r)
    if tabla == "fuentes" and any(f["id"] == i for f in matriz.heredadas(c)):
        raise HTTPException(409, "Esta fuente es un flujo del Streamer: se cambia desde su flujo, no desde aquí")
    raise HTTPException(404, "Esa fuente no existe" if tabla == "fuentes" else "Ese destino no existe")


def _exigir_aire(request, x, rutas, que):
    """Si lo que se va a tocar alimenta rutas al aire, hace falta el permiso de modificarlas."""
    if rutas:
        nombres = ", ".join(f"«{x.nombre('destinos', r['destino'])}»" for r, _ in rutas[:3])
        _exigir(request, x.c, "modificar_al_aire", f"{que} está al aire hacia {nombres}")


def _campos_dichos(campos):
    return ", ".join(sorted(NOMBRES_CAMPOS.get(k, k) for k in campos)) or "sin cambios"


def _red(c):
    return matriz.red(matriz.ajustes_red(c, CFG))


# ---------------------------------------------------------------- fuentes
@router.post("/api/fuentes", dependencies=[exige("editar")])
def crear_fuente(d: Fuente, request: Request):
    c = db.conectar()
    campos = validar("fuente", d, None, c)
    fid = matriz.crear(c, "fuentes", protocolo=d.protocolo, inicia=d.inicia, **campos)
    _cambio(c, request, "Creó una fuente", campos["nombre"], P.NOMBRES[d.protocolo])
    _olvidar()
    return {"id": fid}


@router.put("/api/fuentes/{fid}", dependencies=[exige("editar")])
def editar_fuente(fid: str, d: Fuente, request: Request):
    c = db.conectar()
    antes = _fila(c, "fuentes", fid)
    campos = _novedades(validar("fuente", d, antes, c), antes)
    if any(k in campos for k in CORTAN):
        x = Contexto(c)
        _exigir_aire(request, x, x.al_aire(fuente=fid), f"«{antes['nombre']}»")
    matriz.editar(c, "fuentes", fid, **campos)
    if campos:
        _cambio(c, request, "Editó una fuente", campos.get("nombre", antes["nombre"]), _campos_dichos(campos))
    _olvidar()
    return {"ok": True, "cambios": sorted(campos)}


def _activar(fid, activar, request):
    c = db.conectar()
    antes = _fila(c, "fuentes", fid)
    if bool(antes["activo"]) == activar:
        return {"ok": True, "sin_cambios": True}
    if not activar:
        x = Contexto(c)
        _exigir_aire(request, x, x.al_aire(fuente=fid), f"«{antes['nombre']}»")
    matriz.editar(c, "fuentes", fid, activo=int(activar))
    _cambio(c, request, "Activó una fuente" if activar else "Desactivó una fuente", antes["nombre"])
    _olvidar()
    return {"ok": True}


@router.post("/api/fuentes/{fid}/activar", dependencies=[exige("editar")])
def activar_fuente(fid: str, request: Request):
    return _activar(fid, True, request)


@router.post("/api/fuentes/{fid}/desactivar", dependencies=[exige("editar")])
def desactivar_fuente(fid: str, request: Request):
    return _activar(fid, False, request)


@router.delete("/api/fuentes/{fid}", dependencies=[exige("editar")])
def borrar_fuente(fid: str, request: Request):
    c = db.conectar()
    antes = _fila(c, "fuentes", fid)
    x = Contexto(c)
    _exigir_aire(request, x, x.al_aire(fuente=fid), f"«{antes['nombre']}»")
    destinos = [x.nombre("destinos", r["destino"]) for r in x.rutas.values() if r["fuente"] == fid]
    matriz.borrar(c, "fuentes", fid)
    _cambio(c, request, "Borró una fuente", antes["nombre"],
            "con sus rutas hacia " + ", ".join(destinos) if destinos else "no tenía rutas")
    _olvidar()
    return {"ok": True}


def _claves(tabla, lado, i, request, nueva=False):
    """La dirección que el hub entrega, con sus claves a la vista. nueva: antes cambia la clave."""
    c = db.conectar()
    fila = _fila(c, tabla, i)
    if not matriz.direccion(fila, _red(c), lado):
        raise HTTPException(404, "Aquí es el hub quien se conecta: las claves son del otro equipo y no se enseñan")
    if nueva:
        if not fila["clave"] and (matriz.json_de(fila["opciones"], {}).get("puerto")):
            raise HTTPException(422, "Con puerto propio no hay clave: la protección es la passphrase")
        x = Contexto(c)
        _exigir_aire(request, x, x.al_aire(**{"fuente" if lado == "fuente" else "destino": i}), f"«{fila['nombre']}»")
        matriz.editar(c, tabla, i, clave=matriz.nueva_clave())
        fila = _fila(c, tabla, i)
        _cambio(c, request, "Cambió una clave", fila["nombre"], "clave nueva; el valor no queda registrado")
        _olvidar()
    else:
        _cambio(c, request, "Consultó una clave", fila["nombre"], "el valor no queda registrado")
    return JSONResponse(matriz.direccion(fila, _red(c), lado, ver_claves=True), headers=SIN_CACHE)


@router.get("/api/fuentes/{fid}/claves", dependencies=[exige("ver_claves")])
def claves_fuente(fid: str, request: Request):
    """La dirección que el hub entrega para publicar en esta fuente, con sus claves a la vista."""
    return _claves("fuentes", "fuente", fid, request)


@router.post("/api/fuentes/{fid}/clave-nueva", dependencies=[exige("editar"), exige("ver_claves")])
def clave_nueva_fuente(fid: str, request: Request):
    """Cambia la clave con la que se publica en la fuente. Quien esté publicando sigue hasta que
    reconecte; desde entonces necesita la nueva."""
    return _claves("fuentes", "fuente", fid, request, nueva=True)


# ---------------------------------------------------------------- destinos
@router.post("/api/destinos", dependencies=[exige("editar")])
def crear_destino(d: Destino, request: Request):
    c = db.conectar()
    campos = validar("destino", d, None, c)
    did = matriz.crear(c, "destinos", protocolo=d.protocolo, inicia=d.inicia, **campos)
    _cambio(c, request, "Creó un destino", campos["nombre"], P.NOMBRES[d.protocolo])
    _olvidar()
    return {"id": did}


@router.put("/api/destinos/{did}", dependencies=[exige("editar")])
def editar_destino(did: str, d: Destino, request: Request):
    c = db.conectar()
    antes = _fila(c, "destinos", did)
    campos = _novedades(validar("destino", d, antes, c), antes)
    if any(k in campos for k in CORTAN):
        x = Contexto(c)
        _exigir_aire(request, x, x.al_aire(destino=did), f"«{antes['nombre']}»")
    matriz.editar(c, "destinos", did, **campos)
    if campos:
        _cambio(c, request, "Editó un destino", campos.get("nombre", antes["nombre"]), _campos_dichos(campos))
    _olvidar()
    return {"ok": True, "cambios": sorted(campos)}


@router.delete("/api/destinos/{did}", dependencies=[exige("editar")])
def borrar_destino(did: str, request: Request):
    c = db.conectar()
    antes = _fila(c, "destinos", did)
    x = Contexto(c)
    _exigir_aire(request, x, x.al_aire(destino=did), f"«{antes['nombre']}»")
    matriz.borrar(c, "destinos", did)
    _cambio(c, request, "Borró un destino", antes["nombre"])
    _olvidar()
    return {"ok": True}


@router.get("/api/destinos/{did}/claves", dependencies=[exige("ver_claves")])
def claves_destino(did: str, request: Request):
    """La dirección que el hub entrega para abrir este destino, con sus claves a la vista."""
    return _claves("destinos", "destino", did, request)


@router.post("/api/destinos/{did}/clave-nueva", dependencies=[exige("editar"), exige("ver_claves")])
def clave_nueva_destino(did: str, request: Request):
    """Cambia la clave con la que se lee el destino. Quien esté conectado sigue hasta que
    reconecte; desde entonces necesita la nueva."""
    return _claves("destinos", "destino", did, request, nueva=True)


# ---------------------------------------------------------------- rutas
class Ruta(BaseModel):
    fuente: str
    destino: Optional[str] = None           # solo para pedir el plan
    perfil: Optional[str] = None
    audio: Optional[str] = None
    sustituye: Optional[str] = None         # la fuente que hoy tiene el destino: confirma que se sabe cuál se quita


def _actual(x, did):
    """La ruta que tiene hoy un destino, dicha para quien va a cambiarla."""
    r = x.rutas.get(did)
    if not r:
        return None
    e = x.estado_ruta(r)
    return {"fuente": r["fuente"], "fuente_nombre": x.nombre("fuentes", r["fuente"]), "desde": r["desde"], "por": r["por"],
            "perfil": r["perfil"], "audio": r["audio"], "estado": e["estado"], "estado_nombre": e["nombre"],
            "detalle": e["detalle"], "al_aire": estados.al_aire(e)}


def _plan(x, f, d, perfil, audio):
    """El plan de llevar la fuente al destino, con lo que el operador debe saber antes de activar."""
    if not f["activo"]:
        return dict(plan.planear(None, d["protocolo"]), provisional=True,
                    resumen=matriz.FUENTE_PARADA + ": la ruta esperará a que se active.")
    que_trae = {f["id"]: matriz.json_de((x.inspecciones.get(f["id"]) or {}).get("pistas"), None)}
    try:
        p = matriz.plan_de({"perfil": perfil, "audio": audio}, f, d, x.perfiles, que_trae)
    except (ValueError, TypeError, KeyError) as err:
        raise HTTPException(422, f"No se puede calcular el plan de esta ruta: {err}"[:300])
    for proto in sorted({d["protocolo"], f["protocolo"]} & set(P.CON_ADAPTADOR)):
        a = x.adaptadores.get(proto) or {}
        if not a.get("listo"):
            p["avisos"] = [*p.get("avisos", []), a.get("falta") or f"Falta el adaptador {P.NOMBRES[proto]}"]
    firma = (p.get("senal") or {}).get("firma")
    p["comparte"] = []
    if firma:                                           # ¿ya hay otra ruta de esta fuente con la misma conversión?
        senal = matriz.path_senal(f["id"], firma)
        p["comparte"] = sorted(x.nombre("destinos", o) for o, s in x.hub["senales"].items() if s == senal and o != d["id"])
        if p["comparte"]:
            p["carga"] = "Usa la conversión que ya alimenta a " + ", ".join(f"«{o}»" for o in p["comparte"]) + ": no añade carga."
    return p


def _perfil_y_audio(x, d, destino):
    perfil = d.perfil or destino["perfil"]
    if perfil not in {p["id"] for p in x.perfiles}:
        raise HTTPException(422, "Ese perfil no existe")
    return perfil, _audio(d.audio or destino["audio"])


@router.post("/api/rutas/plan", dependencies=[exige("ver")])
def plan_de_ruta(d: Ruta, request: Request):
    """Antes de activar: qué pistas se copian, cuáles se convierten y por qué; qué hay hoy en el
    destino y qué permiso hace falta para cambiarlo. No cambia nada."""
    c = db.conectar()
    x = Contexto(c)
    f, destino = x.fuentes.get(d.fuente), x.destinos.get(d.destino or "")
    if not f or not destino:
        raise HTTPException(404, "Esa fuente o ese destino no existen")
    perfil, audio = _perfil_y_audio(x, d, destino)
    actual = _actual(x, destino["id"])
    permiso = "modificar_al_aire" if actual and actual["al_aire"] else "conectar_libre"
    cambia = bool(actual) and actual["fuente"] != f["id"]
    return {"fuente": {"id": f["id"], "nombre": f["nombre"]}, "destino": {"id": destino["id"], "nombre": destino["nombre"]},
            "perfil": perfil, "audio": audio, "plan": _plan(x, f, destino, perfil, audio), "actual": actual,
            "sustituye": actual["fuente"] if cambia else None, "permiso": permiso, "puedes": _puede(request, c, permiso),
            "validacion": validacion.de(x.validaciones, f, destino)}


@router.put("/api/rutas/{did}", dependencies=[exige("ver")])
def conectar_ruta(did: str, d: Ruta, request: Request):
    """Conecta la fuente al destino. Si el destino ya recibe otra fuente, hay que confirmar diciendo
    cuál se sustituye: así nadie cambia lo que está al aire sin haber visto qué era. El permiso
    depende de lo que haya: conectar un destino libre o modificar una ruta al aire."""
    c = db.conectar()
    x = Contexto(c)
    destino, f = x.destinos.get(did), x.fuentes.get(d.fuente)
    if not destino or not f:
        raise HTTPException(404, "Esa fuente o ese destino no existen")
    perfil, audio = _perfil_y_audio(x, d, destino)
    actual = _actual(x, did)
    if actual and (actual["fuente"], actual["perfil"], actual["audio"]) == (f["id"], perfil, audio):
        return {"ok": True, "sin_cambios": True}        # ya está así: no hay nada que permitir ni que anotar
    al_aire = bool(actual and actual["al_aire"])
    _exigir(request, c, "modificar_al_aire" if al_aire else "conectar_libre",
            f"«{destino['nombre']}» está al aire con «{actual['fuente_nombre']}»" if al_aire else "")
    if actual and actual["fuente"] != f["id"] and d.sustituye != actual["fuente"]:
        raise HTTPException(409, {"mensaje": f"«{destino['nombre']}» ya recibe «{actual['fuente_nombre']}» "
                                             f"({actual['estado_nombre'].lower()}). Confirma el cambio para sustituirla.",
                                  "actual": actual, "sustituye": actual["fuente"]})
    p = _plan(x, f, destino, perfil, audio)
    if not p.get("posible", True):
        raise HTTPException(422, f"No se puede activar esta ruta. {p.get('resumen', '')}".strip())
    matriz.conectar(c, did, f["id"], perfil, audio, por=_usuario(request)["email"])
    objeto = f"{f['nombre']} → {destino['nombre']}"
    perfil_nombre = plan.perfil_por_id(x.perfiles, perfil)["nombre"]
    if not actual:
        _cambio(c, request, "Activó una ruta", objeto, f"perfil {perfil_nombre}, audio {audio}")
    elif actual["fuente"] != f["id"]:
        _cambio(c, request, "Cambió la fuente al aire" if al_aire else "Cambió la fuente de una ruta",
                destino["nombre"], f"de «{actual['fuente_nombre']}» a «{f['nombre']}»")
    else:
        _cambio(c, request, "Cambió el perfil de una ruta al aire" if al_aire else "Cambió el perfil de una ruta",
                objeto, f"perfil {perfil_nombre}, audio {audio}")
    _olvidar()
    return {"ok": True, "plan": p}


@router.delete("/api/rutas/{did}", dependencies=[exige("ver")])
def desconectar_ruta(did: str, request: Request):
    """Desconecta el destino de su fuente. Como al conectar, el permiso depende de si está al aire."""
    c = db.conectar()
    x = Contexto(c)
    if did not in x.destinos:
        raise HTTPException(404, "Ese destino no existe")
    actual = _actual(x, did)
    if not actual:
        _exigir(request, c, "conectar_libre")
        return {"ok": True, "sin_cambios": True}
    nombre = x.destinos[did]["nombre"]
    _exigir(request, c, "modificar_al_aire" if actual["al_aire"] else "conectar_libre",
            f"«{nombre}» está al aire con «{actual['fuente_nombre']}»" if actual["al_aire"] else "")
    matriz.desconectar(c, did)
    _cambio(c, request, "Desactivó una ruta", f"{actual['fuente_nombre']} → {nombre}",
            "estaba al aire" if actual["al_aire"] else "")
    _olvidar()
    return {"ok": True}


@router.post("/api/rutas/{did}/reintentar", dependencies=[exige("conectar_libre")])
def reintentar_ruta(did: str, request: Request):
    """Vuelve a intentar ya una ruta que el hub dejó de intentar (o que espera su próximo intento)."""
    c = db.conectar()
    x = Contexto(c)
    if did not in x.rutas:
        raise HTTPException(404, "Ese destino no tiene ruta")
    r = x.rutas[did]
    tareas = [matriz.id_ruta(did)]
    senal = x.hub["senales"].get(did)
    if senal and senal != r["fuente"]:
        tareas.append(senal)                            # y su conversión, si era la que no arrancaba
    if matriz.id_entrada(r["fuente"]) in x.hub["tareas"]:
        tareas.append(matriz.id_entrada(r["fuente"]))   # y la entrada de la fuente, si el hub va a por ella
    for t in tareas:
        c.execute("INSERT INTO ordenes(orden, tarea) VALUES ('reintentar', ?)", (t,))
    _cambio(c, request, "Reintentó una ruta", f"{x.nombre('fuentes', r['fuente'])} → {x.destinos[did]['nombre']}")
    _olvidar()
    return {"ok": True}


# ---------------------------------------------------------------- probar una conexión
_pruebas = threading.BoundedSemaphore(4)


def _con_prueba(funcion):
    if not _pruebas.acquire(blocking=False):
        raise HTTPException(503, "Hay demasiadas pruebas en curso: espera unos segundos")
    try:
        return funcion()
    finally:
        _pruebas.release()


def _prueba_ajena(fila):
    """El hub es quien llama: se sondea al otro equipo sin emitir nada."""
    if fila["protocolo"] == "srt":
        try:
            _, host, puerto = sondas.partes_url(fila["url"])
        except ValueError as err:
            return sondas.resultado([sondas.paso("Dirección", False, str(err))])
        r = sondas.srt_escucha(host, puerto)
        if fila["passphrase"] or fila["streamid"]:
            r["pasos"].append(sondas.paso("Passphrase y stream ID", None, "Solo se comprueban al conectar de verdad",
                                          al_conectar=True))
        return r
    if fila["protocolo"] == "rtmp":
        r = sondas.rtmp_responde(fila["url"])
        r["pasos"].append(sondas.paso("Clave de emisión", None, "Solo se comprueba al emitir de verdad", al_conectar=True))
        return r
    if fila["protocolo"] == "hls":
        lista, error = inspeccion.leer_lista_hls(fila["url"])
        if not lista:
            return sondas.resultado([sondas.paso("Lista HLS", False, error)])
        variantes = lista["variantes"]
        n = int(matriz.json_de(fila.get("opciones"), {}).get("variante") or 0)
        pasos = [sondas.paso("Lista HLS", True, f"La lista responde y declara {len(variantes)} "
                                                 f"variante{'s' if len(variantes) != 1 else ''}")]
        if n >= len(variantes):
            pasos.append(sondas.paso("Variante elegida", False, f"La lista no tiene variante {n + 1}"))
        else:
            audios = variantes[n]["audios"]
            pasos.append(sondas.paso("Pistas de audio", True, f"{len(audios)} declaradas: " + ", ".join(
                a["nombre"] or a["idioma"] or "sin nombre" for a in audios) if audios else "Las que traiga el propio vídeo"))
        r = sondas.resultado(pasos)
        r["hls"] = lista
        return r
    return sondas.whip_responde(fila["url"], fila["clave"])


def _prueba_propia(fila, lado, c):
    """El otro equipo es quien llama: se comprueba que el hub esté escuchando donde dice."""
    n = matriz.ajustes_red(c, CFG)
    puerto_propio = matriz.json_de(fila.get("opciones"), {}).get("puerto")
    proto = fila["protocolo"]
    if proto == "srt" and puerto_propio:
        paso = sondas.hub_escucha(puerto_propio, "udp", "El proceso de esta " + ("fuente" if lado == "fuente" else "ruta"))
        if paso["ok"] is False:
            paso = sondas.paso(paso["que"], None, "El puerto se abre al conectar una fuente a este destino" if lado == "destino"
                               else "El puerto se abre al guardar y activar la fuente")
    elif proto == "srt":
        paso = sondas.hub_escucha(matriz.red(n)["srt"], "udp", "MediaMTX (SRT)")
    elif proto == "rtmp":
        paso = sondas.hub_escucha(n["rtmp_puerto"], "tcp", "MediaMTX (RTMP)")
    elif proto == "hls":
        paso = sondas.hub_escucha(int(CFG.get("hls_puerto", 8888)), "tcp", "MediaMTX (HLS)")
    else:
        paso = sondas.hub_escucha(n["webrtc_puerto"], "tcp", "MediaMTX (WebRTC)")
    pasos = [paso]
    problemas = (db.ajuste(c, "servidores") or {}).get("problemas") or {}
    problema = problemas.get(proto)
    if problema:
        pasos = [sondas.paso(paso["que"], False, f"MediaMTX no puede encender {P.NOMBRES[proto]}. {problema}. "
                                                 f"Cambia el puerto en Ajustes")]
    elif paso["ok"] is False and proto in ("rtmp", "webrtc") and not fila.get("id"):
        pasos = [sondas.paso(paso["que"], None, f"El servidor {P.NOMBRES[proto]} del hub se enciende al guardar")]
    tls = matriz.rtmps(n) if proto == "rtmp" else None
    if tls and pasos[0]["ok"]:              # el hub entrega también la dirección cifrada: ¿la atiende?
        pasos.append(sondas.paso("RTMPS", None, f"El hub atiende RTMP, pero no RTMPS. {problemas['rtmps']}")
                     if problemas.get("rtmps") else sondas.hub_escucha(tls["puerto"], "tcp", "MediaMTX (RTMPS)"))
        aviso = problemas.get(mtx.RTMPS_DISCO)
        if aviso and pasos[-1]["ok"]:
            pasos.append(sondas.paso("Certificado de RTMPS", None, _certificado_del_disco(aviso)))
        elif aviso and (_servidores_mtx() or {}).get("rtmps"):      # sigue en marcha, en el puerto que tenía
            pasos[-1] = sondas.paso("RTMPS", None, _certificado_del_disco(aviso))
    if not n.get("host_publico"):
        pasos.append(sondas.paso("Dirección pública", None, "Falta la dirección pública del hub en Ajustes: la dirección "
                                                           "que se entrega solo vale dentro de este equipo"))
    return sondas.resultado(pasos)


def _probar(lado, fila, x):
    """Prueba de una fuente o un destino guardados. Si ya hay señal pasando, esa es la prueba."""
    if fila.get("heredada"):
        e = x.estado_fuente(fila)
        return sondas.resultado([sondas.paso("Señal del flujo", e["estado"] == estados.RECIBIENDO, e["detalle"])])
    if fila["protocolo"] in P.CON_ADAPTADOR:
        a = x.adaptadores.get(fila["protocolo"]) or {}
        return sondas.resultado([sondas.paso(f"Adaptador {P.NOMBRES[fila['protocolo']]}", bool(a.get("listo")),
                                             a.get("falta") or "Listo")])
    if lado == "fuente":
        e = x.estado_fuente(fila)
        if e["estado"] == estados.RECIBIENDO:
            return sondas.resultado([sondas.paso("Señal", True, "Ya llega señal de esta fuente. " + ". ".join(
                h["que"] for h in e["evidencia"][:2]))])
    else:
        r = x.rutas.get(fila["id"])
        e = x.estado_ruta(r) if r else None
        if e and e["estado"] == estados.ENVIANDO and not e.get("sin_confirmar"):
            # un receptor SRT ya conectado no contesta a otro saludo: la prueba es que la señal sale
            return sondas.resultado([sondas.paso("Señal", True, "La ruta está enviando ahora mismo. " + ". ".join(
                h["que"] for h in e["evidencia"][:2]))])
    return _prueba_ajena(fila) if fila["inicia"] == "hub" else _prueba_propia(fila, lado, x.c)


@router.post("/api/fuentes/{fid}/probar", dependencies=[exige("ver")])
def probar_fuente(fid: str):
    """Comprueba la conexión de la fuente sin cambiar nada: si el origen responde (cuando llama el
    hub) o si el hub escucha donde dice (cuando llama el origen)."""
    c = db.conectar()
    f = next((f for f in matriz.todas_las_fuentes(c) if f["id"] == fid), None)
    if not f:
        raise HTTPException(404, "Esa fuente no existe")
    return _con_prueba(lambda: _probar("fuente", f, Contexto(c)))


@router.post("/api/destinos/{did}/probar", dependencies=[exige("ver")])
def probar_destino(did: str):
    """Comprueba la conexión del destino sin emitir nada."""
    c = db.conectar()
    d = _fila(c, "destinos", did)
    return _con_prueba(lambda: _probar("destino", d, Contexto(c)))


class Prueba(Destino):
    lado: str = "fuente"
    id: Optional[str] = None                # al editar: lo que no se mande se toma de lo guardado


@router.post("/api/probar", dependencies=[exige("editar")])
def probar_sin_guardar(d: Prueba):
    """Prueba lo que hay escrito en el formulario, antes de guardarlo. En HLS devuelve además las
    variantes y las pistas de audio de la lista, para elegir."""
    if d.lado not in QUE:
        raise HTTPException(422, "lado: fuente o destino")
    c = db.conectar()
    antes = _fila(c, "fuentes" if d.lado == "fuente" else "destinos", d.id) if d.id else None
    campos = validar(d.lado, d, antes, c, probando=True)
    fila = {"id": "", "url": "", "streamid": "", "passphrase": "", "clave": "", "opciones": "{}", "latencia_ms": 300,
            **(antes or {"protocolo": d.protocolo, "inicia": d.inicia}), **campos}
    if isinstance(fila["opciones"], dict):
        fila["opciones"] = json.dumps(fila["opciones"])
    if fila["protocolo"] in P.CON_ADAPTADOR:
        return _probar(d.lado, fila, Contexto(c))
    if fila["inicia"] == "hub":
        return _con_prueba(lambda: _prueba_ajena(fila))
    return _con_prueba(lambda: _prueba_propia(fila, d.lado, c))


# ---------------------------------------------------------------- medidores y carga
@router.get("/api/fuentes/{fid}/niveles", dependencies=[exige("ver")])
def niveles_fuente(fid: str):
    """Pico y RMS de cada canal de cada pista de audio de la fuente. Pedirlo mantiene el medidor en
    marcha; al dejar de pedirlo se para solo."""
    c = db.conectar()
    paths, _, _ = _mediamtx()
    if not any(f["id"] == fid for f in matriz.todas_las_fuentes(c)):
        raise HTTPException(404, "Esa fuente no existe")
    nada = {"activo": False, "arrancando": False, "pistas": []}
    if db.reposo_recorder() is not None:
        return {**nada, "error": "La estación está en reposo"}
    if not ((paths or {}).get(fid) or {}).get("ready"):
        return {**nada, "error": "La fuente no tiene señal"}
    ins = c.execute("SELECT pistas FROM inspecciones WHERE fuente=?", (fid,)).fetchone()
    audios = ((matriz.json_de(ins["pistas"], None) or {}).get("audio") or []) if ins else []
    pasan = [i for i, a in enumerate(audios) if a.get("codec") in P.ENLACE_AUDIO]
    if not pasan:
        return {**nada, "error": "La fuente no trae audio que el hub pueda medir" if ins else "Se está inspeccionando la señal"}
    r = medidores.niveles(fid, len(pasan))
    if r is None:
        raise HTTPException(503, f"Ya hay {medidores.MAXIMO} medidores en marcha: cierra alguno")
    for pista, i in zip(r["pistas"], pasan):            # con el número y el nombre que tiene en la fuente
        pista.update(pista=i + 1, nombre=plan.nombre_pista(i, audios[i]), texto=plan.texto_audio(audios[i]))
    return r


_carga = carga.Medidor()


MOTOR = "mediamtx"


def _etiqueta(tarea, x):
    """Qué hace cada proceso del hub, para leer la carga sin saberse los identificadores."""
    if tarea == MOTOR:
        return {"tipo": "motor", "texto": "MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC)"}
    if tarea.startswith("ruta_"):
        return {"tipo": "envio", "texto": f"Envío a «{x.nombre('destinos', tarea[5:])}»"}
    if tarea.startswith("ent_"):
        return {"tipo": "entrada", "texto": f"Entrada de «{x.nombre('fuentes', tarea[4:])}»"}
    if tarea.startswith("conv_"):
        return {"tipo": "conversion", "texto": f"Conversión de «{x.nombre('fuentes', tarea[5:].rsplit('_', 1)[0])}»"}
    return {"tipo": "flujo", "texto": tarea}


@router.get("/api/carga", dependencies=[exige("ver")])
def ver_carga():
    """Carga del equipo y de cada proceso del hub (el porcentaje de cada proceso es de un núcleo)."""
    c = db.conectar()
    x = Contexto(c)
    procesos = {t: e["pid"] for t, e in x.tareas.items() if e.get("pid")}
    motor = carga.pid_de("mediamtx", (db.BASE, db.BASE.parent))     # lo que no lleva ffmpeg lo mueve MediaMTX
    if motor:
        procesos[MOTOR] = motor
    r = _carga.medir(procesos)
    for tarea, medida in r["tareas"].items():
        medida.update(_etiqueta(tarea, x))
    return r


# ---------------------------------------------------------------- perfiles
class Perfil(BaseModel):
    nombre: str
    video: Optional[dict] = None            # {"alto", "kbps"} o nada: el vídeo no se toca
    audio: Optional[dict] = None            # {"kbps"} o nada


def _perfil(d):
    nombre = _texto(d.nombre, "el nombre", 60, vacio=False)
    video = audio = None
    try:
        if d.video:
            video = {"alto": int(d.video["alto"]), "kbps": int(d.video["kbps"])}
            if not (144 <= video["alto"] <= 2160 and video["alto"] % 2 == 0 and 200 <= video["kbps"] <= 60000):
                raise ValueError
        if d.audio:
            audio = {"kbps": int(d.audio["kbps"])}
            if not 32 <= audio["kbps"] <= 512:
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Perfil no válido: alto par entre 144 y 2160, vídeo entre 200 y 60000 kb/s, "
                                 "audio entre 32 y 512 kb/s")
    if not video and not audio:
        raise HTTPException(422, "Un perfil que no cambia ni el vídeo ni el audio es el «Original», que ya existe")
    return nombre, video, audio


def _usos_perfil(c, pid):
    return sorted({r["nombre"] for r in db.filas(c, """SELECT d.nombre FROM destinos d LEFT JOIN rutas r ON r.destino=d.id
                                                       WHERE d.perfil=? OR r.perfil=?""", pid, pid)})


@router.get("/api/perfiles", dependencies=[exige("ver")])
def listar_perfiles():
    c = db.conectar()
    return [dict(p, en_uso=_usos_perfil(c, p["id"])) for p in matriz.perfiles(c)]


@router.post("/api/perfiles", dependencies=[exige("ajustes")])
def crear_perfil(d: Perfil, request: Request):
    c = db.conectar()
    nombre, video, audio = _perfil(d)
    fijos = {p["id"] for p in plan.PERFILES_FIJOS}
    while True:
        pid = "perfil-" + aleatorio.token_hex(3)
        if pid not in fijos and not c.execute("SELECT 1 FROM perfiles WHERE id=?", (pid,)).fetchone():
            break
    c.execute("INSERT INTO perfiles(id, nombre, video, audio) VALUES (?,?,?,?)", (pid, nombre, json.dumps(video), json.dumps(audio)))
    _cambio(c, request, "Creó un perfil", nombre)
    _olvidar()
    return {"id": pid}


@router.put("/api/perfiles/{pid}", dependencies=[exige("ajustes")])
def editar_perfil(pid: str, d: Perfil, request: Request):
    """Cambiar un perfil relanza las conversiones que lo usan: si alguna está al aire, hace falta
    además el permiso de modificar rutas al aire."""
    c = db.conectar()
    nombre, video, audio = _perfil(d)
    if not c.execute("SELECT 1 FROM perfiles WHERE id=?", (pid,)).fetchone():
        raise HTTPException(404, "Ese perfil no existe o es uno de los fijos, que no se cambian")
    x = Contexto(c)
    _exigir_aire(request, x, x.al_aire(perfil=pid), "Este perfil")
    c.execute("UPDATE perfiles SET nombre=?, video=?, audio=? WHERE id=?", (nombre, json.dumps(video), json.dumps(audio), pid))
    _cambio(c, request, "Editó un perfil", nombre)
    _olvidar()
    return {"ok": True}


@router.delete("/api/perfiles/{pid}", dependencies=[exige("ajustes")])
def borrar_perfil(pid: str, request: Request):
    c = db.conectar()
    fila = c.execute("SELECT nombre FROM perfiles WHERE id=?", (pid,)).fetchone()
    if not fila:
        raise HTTPException(404, "Ese perfil no existe o es uno de los fijos, que no se borran")
    usos = _usos_perfil(c, pid)
    if usos:
        raise HTTPException(409, "El perfil lo usan " + ", ".join(f"«{u}»" for u in usos[:5]) + ": cámbialo ahí antes de borrarlo")
    c.execute("DELETE FROM perfiles WHERE id=?", (pid,))
    _cambio(c, request, "Borró un perfil", fila["nombre"])
    _olvidar()
    return {"ok": True}


# ---------------------------------------------------------------- ajustes
class Red(BaseModel):
    rtmp_puerto: Optional[int] = None
    webrtc_puerto: Optional[int] = None
    webrtc_udp: Optional[int] = None
    host_publico: Optional[str] = None
    hls_url: Optional[str] = None
    webrtc_url: Optional[str] = None


class Ice(BaseModel):
    url: str
    usuario: str = ""
    clave: Optional[str] = None             # sin dar: se conserva la guardada para esa misma dirección


class WebRTC(BaseModel):
    hosts: list[str] = []
    ice: list[Ice] = []


class Permisos(BaseModel):
    operador: list[str]
    manager: list[str]


PUERTOS_RED = (("RTMP", "rtmp_puerto", "tcp", "rtmp"), ("WebRTC (señalización)", "webrtc_puerto", "tcp", "webrtc"),
               ("WebRTC (medios)", "webrtc_udp", "udp", "webrtc"))


def _certificado_del_disco(motivo):
    return ("RTMPS sigue en marcha como estaba, con el certificado que MediaMTX ya tenía cargado, pero lo que se pide "
            f"ahora no se puede aplicar: {motivo}. Si MediaMTX se reinicia, RTMPS no volverá hasta corregirlo")


def _vista_ajustes(c):
    n = matriz.ajustes_red(c, CFG)
    red = matriz.red(n)
    webrtc = db.ajuste(c, "webrtc") or {}
    servicios = matriz.servicios(c, CFG)
    problemas = (db.ajuste(c, "servidores") or {}).get("problemas") or {}
    hls = int(CFG.get("hls_puerto", 8888))
    puertos = [{"servicio": "SRT", "puerto": red["srt"], "tipo": "UDP", "clave": None, "en_uso": True,
                "estado": sondas.hub_escucha(red["srt"], "udp", "MediaMTX (SRT)")},
               {"servicio": "HLS", "puerto": hls, "tipo": "TCP", "clave": None, "en_uso": True,
                "estado": sondas.hub_escucha(hls, "tcp", "MediaMTX (HLS)")}]
    for servicio, clave, tipo, usa in PUERTOS_RED:
        nombre = f"Puerto {n[clave]}/{tipo.upper()} del hub"
        if problemas.get(usa):
            estado = sondas.paso(nombre, False, f"{problemas[usa]}. Elige otro puerto")
        elif servicios[usa]:
            estado = sondas.hub_escucha(n[clave], tipo, f"MediaMTX ({servicio})")
        else:
            estado = sondas.paso(nombre, None, "Apagado: se enciende solo cuando una fuente o un destino usa este protocolo")
        puertos.append({"servicio": servicio, "puerto": n[clave], "tipo": tipo.upper(), "clave": clave,
                        "en_uso": bool(servicios[usa]), "estado": estado})
    tls = matriz.rtmps(n)
    if tls:                                 # va con RTMP; su puerto y su certificado se ponen en config.json
        nombre = f"Puerto {tls['puerto']}/TCP del hub"
        if problemas.get("rtmps"):
            estado = sondas.paso(nombre, False, f"{problemas['rtmps']}. El puerto y los archivos de RTMPS se indican en "
                                                "config.json (rtmps_puerto, rtmps_cert, rtmps_clave)")
        elif servicios["rtmp"]:
            estado = sondas.hub_escucha(tls["puerto"], "tcp", "MediaMTX (RTMPS)")
            # con el aviso, RTMPS sigue en marcha: en este puerto o, si lo que no vale es un puerto nuevo, en el que tenía
            if problemas.get(mtx.RTMPS_DISCO) and (estado["ok"] or (_servidores_mtx() or {}).get("rtmps")):
                estado = sondas.paso(nombre, None, _certificado_del_disco(problemas[mtx.RTMPS_DISCO]))
        else:
            estado = sondas.paso(nombre, None, "Apagado: se enciende solo cuando una fuente o un destino usa RTMP")
        puertos.append({"servicio": "RTMPS", "puerto": tls["puerto"], "tipo": "TCP", "clave": None,
                        "en_uso": bool(servicios["rtmp"]), "estado": estado})
    return {"red": {"host_publico": n.get("host_publico") or "", "hls_url": red["hls"], "webrtc_url": red["webrtc"],
                    "hls_url_propia": bool(n.get("hls_url")), "webrtc_url_propia": bool(n.get("webrtc_url")),
                    "https": red["webrtc"].startswith("https://"), "rtmps": bool(tls)},
            "puertos": puertos, "problemas": problemas,
            "webrtc": {"hosts": webrtc.get("hosts") or [],
                       "ice": [{"url": s.get("url", ""), "usuario": s.get("usuario", ""), "tiene_clave": bool(s.get("clave"))}
                               for s in webrtc.get("ice") or [] if isinstance(s, dict)]},
            "permisos": {"roles": list(ROLES), "nombres": PERMISOS, "tabla": tabla_permisos(c)},
            "arranque": db.ajuste(c, "arranque")}


@router.get("/api/ajustes", dependencies=[exige("ver")])
def ver_ajustes():
    return _vista_ajustes(db.conectar())


@router.put("/api/ajustes/red", dependencies=[exige("ajustes")])
def guardar_red(d: Red, request: Request):
    """Puertos de RTMP y WebRTC y direcciones públicas. Cambiar un puerto en uso reconecta a quien
    esté conectado por ese protocolo: con rutas al aire por él hace falta el permiso de modificarlas."""
    c = db.conectar()
    guardado = dict(db.ajuste(c, "red") or {})
    actual = matriz.ajustes_red(c, CFG)
    ocupados = _puertos_ocupados(c)
    cambios, protocolos = [], set()
    for _, clave, tipo, usa in PUERTOS_RED:
        puerto = getattr(d, clave)
        if puerto is None or puerto == actual[clave]:
            continue
        if not 1024 <= puerto <= 65535:
            raise HTTPException(422, "Los puertos deben estar entre 1024 y 65535")
        if ocupados.get(puerto) or not mtx.puerto_libre(puerto, tipo):
            raise HTTPException(409, f"El puerto {puerto} ya lo usa {ocupados.get(puerto) or 'otro programa de este equipo'}")
        guardado[clave] = puerto
        ocupados[puerto] = "el hub"
        protocolos.add(usa)
        cambios.append(f"{clave.replace('_', ' ')} {puerto}")
    if d.host_publico is not None:
        host = d.host_publico.strip()
        if host and not re.match(r"^[A-Za-z0-9._:\[\]-]{1,253}$", host):
            raise HTTPException(422, "La dirección pública no es válida: un nombre o una IP, sin http://")
        guardado["host_publico"] = host
        cambios.append("dirección pública")
    for clave in ("hls_url", "webrtc_url"):
        valor = getattr(d, clave)
        if valor is not None:
            valor = valor.strip().rstrip("/")
            if valor and not URL_HTTP.match(valor):
                raise HTTPException(422, "La dirección debe empezar por http:// o https://")
            guardado[clave] = valor
            cambios.append(clave.replace("_", " "))
    if protocolos:
        x = Contexto(c)
        afectadas = [(r, e) for r, e in ((r, x.estado_ruta(r)) for r in x.rutas.values()) if estados.al_aire(e)
                     and {x.destinos[r["destino"]]["protocolo"], (x.fuentes.get(r["fuente"]) or {}).get("protocolo")} & protocolos]
        _exigir_aire(request, x, afectadas, "Ese puerto")
    db.guardar_ajuste(c, "red", {k: v for k, v in guardado.items() if v not in ("", None)})
    _cambio(c, request, "Cambió los ajustes de red", "", ", ".join(cambios) or "sin cambios")
    _olvidar()
    return _vista_ajustes(c)


@router.put("/api/ajustes/webrtc", dependencies=[exige("ajustes")])
def guardar_webrtc(d: WebRTC, request: Request):
    """Direcciones que el hub anuncia y servidores STUN o TURN para WebRTC fuera de la red local."""
    c = db.conectar()
    antes = {s.get("url"): s for s in (db.ajuste(c, "webrtc") or {}).get("ice") or [] if isinstance(s, dict)}
    hosts = []
    for h in d.hosts[:8]:
        h = h.strip()
        if not re.match(r"^[A-Za-z0-9._:-]{1,253}$", h):
            raise HTTPException(422, f"«{h[:40]}» no es una dirección ni un nombre de equipo")
        hosts.append(h)
    ice = []
    for s in d.ice[:8]:
        url = s.url.strip()
        if not re.match(r"^(stun|turn|turns):[A-Za-z0-9._\[\]:-]{1,253}(\?transport=(udp|tcp))?$", url):
            raise HTTPException(422, "Un servidor ICE se escribe stun:servidor:3478 o turn:servidor:3478")
        clave = s.clave if s.clave is not None else (antes.get(url) or {}).get("clave", "")
        ice.append({"url": url, "usuario": _texto(s.usuario, "el usuario", 200), "clave": _texto(clave, "la credencial", 500)})
    db.guardar_ajuste(c, "webrtc", {"hosts": hosts, "ice": ice})
    _cambio(c, request, "Cambió los ajustes de WebRTC", "", f"{len(hosts)} direcciones anunciadas, {len(ice)} servidores ICE")
    _olvidar()
    return _vista_ajustes(c)


@router.put("/api/ajustes/permisos", dependencies=[exige("ajustes")])
def guardar_permisos(d: Permisos, request: Request):
    """Qué puede hacer el operador y el manager. El administrador puede todo siempre."""
    c = db.conectar()
    tabla = {}
    for rol in ("operador", "manager"):
        lista = getattr(d, rol)
        if any(p not in PERMISOS for p in lista):
            raise HTTPException(422, "Permiso desconocido")
        tabla[rol] = [p for p in PERMISOS if p in lista]
    antes = tabla_permisos(c)
    db.guardar_ajuste(c, "permisos", tabla)
    detalle = []
    for rol in tabla:
        detalle += [f"{rol} ya puede {PERMISOS[p].lower()}" for p in PERMISOS if p in tabla[rol] and p not in antes[rol]]
        detalle += [f"{rol} ya no puede {PERMISOS[p].lower()}" for p in PERMISOS if p in antes[rol] and p not in tabla[rol]]
    _cambio(c, request, "Cambió los permisos", "", "; ".join(detalle) or "sin cambios")
    return {"roles": list(ROLES), "nombres": PERMISOS, "tabla": tabla_permisos(c)}


# ---------------------------------------------------------------- adaptadores, motor y registro
_versiones = {}


def _version_ffmpeg():
    if "ffmpeg" not in _versiones:
        try:
            r = subprocess.run([CFG.get("ffmpeg", "ffmpeg"), "-hide_banner", "-version"], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=5).stdout.decode(errors="replace").splitlines()
            _versiones["ffmpeg"] = r[0].split("Copyright")[0].replace("ffmpeg version", "").strip() if r else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return _versiones["ffmpeg"]


@router.get("/api/adaptadores", dependencies=[exige("ver")])
def ver_adaptadores():
    """El motor (MediaMTX y FFmpeg) y los adaptadores NDI y Zixi, con el requisito que le falte a cada uno."""
    c = db.conectar()
    try:
        info = mtx._pedir("GET", "/v3/info") or {}
    except mtx.ErrorMTX:
        info = None
    codificador = c.execute("SELECT texto FROM eventos WHERE texto LIKE 'conversiones de vídeo con %' ORDER BY t DESC LIMIT 1").fetchone()
    estado = adaptadores.estado()
    espera = {p: {"fuentes": [f["nombre"] for f in matriz.fuentes(c) if f["protocolo"] == p],
                  "destinos": [d["nombre"] for d in matriz.destinos(c) if d["protocolo"] == p]} for p in P.CON_ADAPTADOR}
    return {"mediamtx": {"en_marcha": info is not None, "version": (info or {}).get("version", ""),
                         "desde": epoca((info or {}).get("started"))},
            "ffmpeg": {"version": _version_ffmpeg(),
                       "codificador": codificador["texto"].rsplit(" ", 1)[1] if codificador else ""},
            "adaptadores": {p: {**{k: v for k, v in estado[p].items() if not callable(v)}, "en_espera": espera.get(p)}
                            for p in estado}}


@router.post("/api/adaptadores/comprobar", dependencies=[exige("ajustes")])
def comprobar_adaptadores():
    """Vuelve a mirar ahora si están el SDK, la licencia y lo demás (si no, se mira cada 5 minutos)."""
    adaptadores.estado(forzar=True)
    _olvidar()
    return ver_adaptadores()


@router.get("/api/cambios", dependencies=[exige("ver")])
def ver_cambios(limite: int = 100):
    """Registro de cambios: quién cambió qué y cuándo. Nunca lleva claves."""
    c = db.conectar()
    return db.filas(c, "SELECT t, quien, rol, accion, objeto, detalle FROM cambios ORDER BY t DESC, rowid DESC LIMIT ?",
                    max(1, min(1000, limite)))


# ---------------------------------------------------------------- validación con equipos reales
class Validacion(BaseModel):
    fuente: str                             # «rtmp:origen», «srt:hub», «flujo:hub»…
    destino: str                            # «srt:hub», «hls:receptor»…
    nivel: str                              # pendiente | laboratorio | real
    nota: str = ""                          # con qué equipo se comprobó


@router.get("/api/validacion", dependencies=[exige("ver")])
def ver_validacion():
    """Cada combinación de entrada y salida, con su nivel: pendiente, verificada en el banco de
    pruebas o verificada con equipos reales (y quién lo anotó, cuándo y con qué)."""
    return {"niveles": validacion.NIVELES, "combinaciones": validacion.tabla(db.conectar())}


@router.put("/api/validacion", dependencies=[exige("ajustes")])
def anotar_validacion(d: Validacion, request: Request):
    """Anota que una combinación se ha comprobado (o que vuelve a estar pendiente)."""
    c = db.conectar()
    if not validacion.valida(d.fuente, d.destino) or d.nivel not in validacion.NIVELES:
        raise HTTPException(422, "Combinación o nivel desconocidos")
    nota = _texto(d.nota, "la nota", 500)
    if d.nivel == validacion.REAL and not nota:
        raise HTTPException(422, "Anota con qué equipo se comprobó")
    anotado = validacion.anotar(c, d.fuente, d.destino, d.nivel, _usuario(request)["email"], nota)
    if anotado:
        _cambio(c, request, "Anotó una validación", f"{d.fuente} → {d.destino}", f"{validacion.NIVELES[d.nivel]}. {nota}".strip())
    _olvidar()
    return {"ok": True, "anotado": anotado}
