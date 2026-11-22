#!/usr/bin/env python3
"""Antes de desplegar: qué haría esta versión, al arrancar, con lo que la estación tiene al aire.
No cambia nada.

Se ejecuta en la estación, como el usuario del servicio, con el código de esta versión en la
carpeta `streamer` de al lado. Lo lleva y lo lanza scripts/previo.sh, en una carpeta temporal que
borra al terminar:
    python3 -B previo.py [carpeta del servicio]          (por defecto, /home/mediasat/streamer)

Recorre lo mismo que el supervisor de esta versión en su primera vuelta, sin tocar nada:
  · trabaja con una copia de la base (copia en caliente de SQLite; la de verdad solo se abre para
    leer), es la copia la que se pone al día con el esquema de esta versión, y la borra al terminar;
  · a MediaMTX solo le pregunta: lo que le pediría cambiar se apunta y no se le envía;
  · no lanza, no para ni adopta ningún proceso, y no escribe en la carpeta del servicio.

De cada proceso que está al aire dice si esta versión lo conservaría (calcula para él el mismo
comando con el que corre) o lo cortaría (lo relanzaría con otro comando, o lo pararía). De MediaMTX,
qué paths y qué claves de su configuración general le pediría cambiar, y si alguien mira ahora
por HLS. Solo escribe nombres: nunca comandos, direcciones ni claves.

Termina con 0 si no cortaría nada, con 1 si cortaría algo y con 2 si no pudo comprobarlo.
"""
import contextlib
import io
import os
import pathlib
import sqlite3
import sys
import traceback
import urllib.parse

os.umask(0o077)                             # la copia de la base lleva claves: solo para este usuario
sys.dont_write_bytecode = True
AQUI = pathlib.Path(__file__).resolve().parent
CODIGO = AQUI / "streamer"

# claves de la configuración general de MediaMTX y lo que pasa al cambiarlas (probado con la 1.21.1)
REINICIAN_HLS = ("hlsTrustedProxies", "webrtcTrustedProxies")
EFECTOS = {
    "reglas de acceso": "cambiarlas en caliente no corta a quien está conectado",
    "hlsTrustedProxies": "MediaMTX reinicia una vez su servidor HLS: quien mire por HLS se queda unos segundos sin imagen",
    "webrtcTrustedProxies": "MediaMTX reinicia una vez su servidor WebRTC",
    "rtmp": "enciende o apaga el servidor RTMP: las conexiones SRT siguen",
    "webrtc": "enciende o apaga el servidor WebRTC: las conexiones SRT siguen",
}


def copiar_base(real, copia):
    """Copia en caliente de SQLite. La base de verdad se abre solo para leer."""
    origen = sqlite3.connect(f"file:{urllib.parse.quote(str(real))}?mode=ro", uri=True, timeout=10)
    destino = sqlite3.connect(copia)
    try:
        origen.backup(destino)
    finally:
        destino.close()
        origen.close()


def revisar(base):
    """(líneas que decir, cuántas cosas se cortarían) o lanza NoSePuede."""
    os.environ["MSXS_BASE"] = str(base)
    sys.path.insert(0, str(CODIGO))
    copia = AQUI / "streamer.db"
    copiar_base(base / "streamer.db", copia)
    import db
    db.RUTA = copia                         # todo lo que esta versión escriba va a la copia
    import composicion
    import mtx
    import supervisor

    pedidos, preguntar = [], mtx._pedir     # lo que se le pediría cambiar a MediaMTX: no se envía

    def solo_preguntar(metodo, ruta, cuerpo=None, timeout=3):
        if metodo == "GET":
            return preguntar(metodo, ruta, cuerpo, timeout)
        pedidos.append((metodo, ruta, cuerpo))
        return None

    def prohibido(*_a, **_k):
        raise RuntimeError("la comprobación previa no para ni lanza procesos")
    mtx._pedir = solo_preguntar
    supervisor.matar = supervisor.barrer = prohibido

    def instalado(cmd):
        """El comando como lo calculará el código una vez instalado: lo que aquí apunta a esta
        carpeta temporal (componer.sh, espera_hls.py) apuntará a la del servicio."""
        return [a.replace(str(CODIGO), str(base / "streamer")) for a in cmd]

    # ---- lo mismo que Supervisor.vuelta() hasta saber qué debe correr
    sup = supervisor.Supervisor()
    c = sup.c
    if not any(c.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] for tabla in ("fuentes", "destinos", "rutas")):
        sup.codificador = "libx264"         # con el hub vacío nadie usa el codificador ni los adaptadores:
        supervisor.adaptadores_del_hub = dict       # no se prueba la tarjeta ni se buscan NDI y Zixi
    flujos = db.filas(c, "SELECT * FROM flujos WHERE activo=1")
    sup.reposo = reposo = db.reposo_recorder() is not None
    if reposo:
        flujos = []
    por_id = {f["id"]: f for f in flujos}
    sup.comps, sup.comps_fuera = sup.composiciones(por_id)
    try:
        sup.estado_paths = mtx.paths_estado()
        actuales = mtx.paths_config()
        general = preguntar("GET", "/v3/config/global/get")
    except mtx.ErrorMTX:
        raise NoSePuede("MediaMTX no responde")
    with contextlib.redirect_stderr(io.StringIO()):         # el detalle puede llevar direcciones
        sup.hub = sup.calcular_hub(reposo)
    if sup.hub is None:
        raise NoSePuede("esta versión no sabe calcular lo que piden las rutas guardadas")
    estado = sup.sincronizar_mediamtx(flujos)
    if estado is None:
        raise NoSePuede("MediaMTX dejó de responder")
    deseadas = sup.tareas(flujos, por_id)

    # ---- nombres para decirlo
    de_flujo = {f["id"]: f["nombre"] for f in db.filas(c, "SELECT id, nombre FROM flujos")}
    salidas = {s["id"]: s for s in db.filas(c, "SELECT id, flujo, nombre, tipo FROM salidas")}
    comps = {composicion.id_tarea(fid): fid for fid in de_flujo}

    def nombre(tarea):
        if tarea in salidas:
            s = salidas[tarea]
            return f"salida «{s['nombre']}» ({s['tipo']}) del flujo «{de_flujo.get(s['flujo'], s['flujo'])}»"
        if tarea in comps:
            return f"composición del flujo «{de_flujo[comps[tarea]]}»"
        return f"tarea {tarea}"

    # ---- los procesos: las mismas decisiones que la primera vuelta, sin tomarlas
    run = supervisor.RUN
    conocidas = {e["salida"]: e for e in db.filas(c, "SELECT salida, pid, firma FROM estado_salidas")}
    conserva, relanza, para, sin_proceso = [], [], [], []
    for tarea, pid in supervisor.procesos_de_run().items():
        if tarea not in conocidas and tarea not in deseadas:
            para.append((tarea, pid, "corre sin que la base lo conozca ni nadie lo pida"))
    for sid, e in conocidas.items():
        if (sid not in deseadas or deseadas[sid][0] is None) and supervisor.vivo(e["pid"], run / f"{sid}.prog"):
            motivo = "ya no lo pide nadie: borrado, desactivado o de un flujo detenido" if sid not in deseadas \
                else str(deseadas[sid][2]).split(":")[0]
            para.append((sid, e["pid"], motivo))
    for sid, (cmd, _path, _impedimento) in deseadas.items():
        if cmd is None:
            continue
        e = conocidas.get(sid) or {"pid": None, "firma": ""}
        if supervisor.vivo(e["pid"], run / f"{sid}.prog"):
            (conserva if e["firma"] == supervisor.firma(instalado(cmd)) else relanza).append((sid, e["pid"]))
            continue
        suelto = supervisor.buscar(run / f"{sid}.prog")
        if suelto:                          # corre y la base no tiene su número: lo adoptaría tal cual
            conserva.append((sid, suelto))
        else:
            sin_proceso.append(sid)

    # ---- MediaMTX: lo que se le habría pedido
    def ocupado(path):
        info = estado.get(path) or {}
        return bool(info.get("ready") or info.get("readers"))
    nuevos, cambiados, generales = [], [], None
    for metodo, ruta, cuerpo in pedidos:
        partes = ruta.split("/")
        if ruta.startswith("/v3/config/paths/"):
            path = urllib.parse.unquote(partes[5])
            if partes[4] == "add":
                nuevos.append(path)
            elif partes[4] == "delete":
                cambiados.append((path, "lo borraría", ocupado(path)))
            else:
                claves = sorted(k for k, v in (cuerpo or {}).items() if not mtx._coincide((actuales.get(path) or {}).get(k), v))
                cambiados.append((path, "le cambiaría " + ", ".join(claves or ["algo"]), ocupado(path)))
        elif ruta == "/v3/config/global/patch":
            acceso = {k: cuerpo[k] for k in ("authMethod", "authInternalUsers", "hlsAlwaysRemux") if k in cuerpo}
            generales = ["reglas de acceso"] if mtx._normalizar(general) != mtx._normalizar(acceso) else []
            generales += sorted(k for k, v in cuerpo.items() if k not in acceso and not mtx._coincide(general.get(k), v))
        else:
            cambiados.append((ruta, f"le pediría {metodo}", True))      # algo que esta comprobación no conoce
    mirando = sorted(p for p in mtx.muxers_hls() if p)
    sin_hls = bool(mirando) and any(k in REINICIAN_HLS for k in generales or [])

    # ---- decirlo
    al_aire = len(conserva) + len(relanza) + len(para)
    dicho = [f"Procesos al aire: {al_aire}"]
    dicho += [f"   se conserva      {nombre(t)} · proceso {pid}" for t, pid in conserva]
    dicho += [f"   SE RELANZARÍA    {nombre(t)} · proceso {pid}: esta versión calcula para él otro comando"
              for t, pid in relanza]
    dicho += [f"   SE PARARÍA       {nombre(t)} · proceso {pid}: {motivo}" for t, pid, motivo in para]
    if sin_proceso:
        dicho.append(f"Sin proceso ahora: {len(sin_proceso)} (no se corta nada: esta versión los lanza cuando tengan entrada)")
        dicho += [f"   {nombre(t)}" for t in sin_proceso]
    if reposo:
        dicho.append("La estación está en reposo (Recorder apagado): no debe correr ninguna salida")
    dicho.append("MediaMTX")
    cortados = [x for x in cambiados if x[2]]
    if not nuevos and not cambiados:
        dicho.append("   paths: no le pediría crear, cambiar ni borrar ninguno")
    dicho += [f"   path {p}: lo crearía (no corta nada)" for p in nuevos]
    dicho += [f"   path {p}: {que}" + (" Y TIENE SEÑAL O LECTORES: SE CORTARÍA" if corta else " (ahora sin señal ni lectores)")
              for p, que, corta in cambiados]
    if generales is None:
        dicho.append("   configuración general: no le pediría cambiar nada")
    elif not generales:
        dicho.append("   configuración general: le repetiría la que ya tiene (no cambia nada)")
    else:
        dicho.append("   configuración general: le pediría cambiar " + ", ".join(generales))
        dicho += [f"      {k}: {EFECTOS[k]}" for k in generales if k in EFECTOS]
        otras = [k for k in generales if k not in EFECTOS]
        if otras:
            dicho.append(f"      {', '.join(otras)}: sin probar qué reinicia en MediaMTX al cambiar")
    if not mirando:
        dicho.append("   HLS: nadie mirando ahora")
    else:
        dicho.append(f"   HLS: {len(mirando)} con alguien mirando ahora ({', '.join(mirando)})"
                     + (": SE QUEDARÍA UNOS SEGUNDOS SIN IMAGEN" if sin_hls else ""))
    cortes = []
    if relanza or para:
        cortes.append(f"{len(relanza) + len(para)} procesos al aire")
    if cortados:
        cortes.append(f"{len(cortados)} señales de MediaMTX")
    if sin_hls:
        cortes.append("el HLS de quien está mirando, unos segundos")
    dicho.append(f"Resumen: {al_aire} procesos al aire; se conservan {len(conserva)}, se relanzarían {len(relanza)}, "
                 f"se pararían {len(para)}." + (" SE CORTARÍA: " + ", ".join(cortes) + "." if cortes else " No se cortaría nada."))
    return dicho, len(cortes)


class NoSePuede(Exception):
    pass


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    base = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MSXS_BASE", "/home/mediasat/streamer"))
    if not base.is_absolute() or not (base / "streamer.db").is_file() or not (CODIGO / "supervisor.py").is_file():
        print(f"!! falta la base del servicio en {base} o el código de esta versión en {CODIGO}")
        return 2

    def version(carpeta):
        try:
            return (carpeta / "VERSION").read_text().strip()
        except OSError:
            return "?"
    print(f"== Qué haría la v{version(AQUI)} al arrancar en esta estación (instalada: v{version(base)}). No se cambia nada.")
    try:
        dicho, cortes = revisar(base)
    except NoSePuede as err:
        print(f"!! no se pudo comprobar: {err}")
        return 2
    except Exception as err:                # sin el texto del error: puede llevar direcciones o claves
        donde = traceback.extract_tb(err.__traceback__)[-1]
        print(f"!! no se pudo comprobar: {type(err).__name__} en {pathlib.Path(donde.filename).name}:{donde.lineno}")
        return 2
    finally:                                # la copia de la base lleva claves: no sobrevive a la comprobación
        for resto in ("streamer.db", "streamer.db-wal", "streamer.db-shm"):
            (AQUI / resto).unlink(missing_ok=True)
    print("\n".join(dicho))
    return 1 if cortes else 0


if __name__ == "__main__":
    sys.exit(main())
