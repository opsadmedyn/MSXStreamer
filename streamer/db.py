"""Media Syntaxis Streamer - base de datos: lo que se quiere y su estado.

El panel escribe aquí lo que se quiere (flujos y salidas activos; fuentes, destinos y rutas del
hub) y el supervisor lo cumple. Así, si el panel se reinicia, las salidas siguen al aire, y tras
reiniciar el equipo el supervisor vuelve a levantar exactamente lo que estaba habilitado.
"""

import json
import os
import pathlib
import sqlite3

BASE = pathlib.Path(os.environ.get("MSXS_BASE", "/home/mediasat/streamer"))
RUTA = BASE / "streamer.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS flujos (
    id          TEXT PRIMARY KEY,          -- también es el nombre del path en MediaMTX
    nombre      TEXT NOT NULL,
    entrada     TEXT NOT NULL,             -- 'hub' | 'pull' | 'recorder' (solo flujos anteriores a la 0.3.5)
    url         TEXT NOT NULL DEFAULT '',  -- pull: srt://host:puerto; hub: la lectura del feed en el hub elegido
    streamid    TEXT NOT NULL DEFAULT '',
    passphrase  TEXT NOT NULL DEFAULT '',
    latencia_ms INTEGER NOT NULL DEFAULT 400,
    canal       TEXT NOT NULL DEFAULT '',  -- recorder: id del canal
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS salidas (
    id          TEXT PRIMARY KEY,
    flujo       TEXT NOT NULL REFERENCES flujos(id) ON DELETE CASCADE,
    nombre      TEXT NOT NULL,
    tipo        TEXT NOT NULL,             -- 'srt' | 'rtmp' | 'hls'
    url         TEXT NOT NULL DEFAULT '',  -- srt://… | rtmp://… (hls: vacío, lo sirve MediaMTX)
    modo        TEXT NOT NULL DEFAULT 'caller',  -- srt: caller | listener
    streamid    TEXT NOT NULL DEFAULT '',
    passphrase  TEXT NOT NULL DEFAULT '',
    latencia_ms INTEGER NOT NULL DEFAULT 300,
    clave       TEXT NOT NULL DEFAULT '',  -- rtmp: clave de emisión
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS estado_salidas (
    salida      TEXT PRIMARY KEY,
    pid         INTEGER,
    desde       REAL,                      -- inicio del proceso actual
    reinicios   INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    kbps        REAL,
    firma       TEXT NOT NULL DEFAULT '',  -- huella del comando: si cambia la salida, se relanza
    visto       REAL                       -- última vez que el supervisor lo revisó
);
CREATE TABLE IF NOT EXISTS vistas_previas (   -- vista previa HLS abierta desde el panel
    flujo       TEXT PRIMARY KEY REFERENCES flujos(id) ON DELETE CASCADE,
    hasta       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS composiciones (  -- fase 2: una por flujo; publica en el path "comp_<flujo>"
    flujo       TEXT PRIMARY KEY REFERENCES flujos(id) ON DELETE CASCADE,
    activa      INTEGER NOT NULL DEFAULT 0,
    x           INTEGER NOT NULL DEFAULT 0,      -- vídeo dentro del lienzo 1920x1080 (16:9)
    y           INTEGER NOT NULL DEFAULT 0,
    ancho       INTEGER NOT NULL DEFAULT 1920,   -- el alto sale del ancho (16:9)
    fondo       TEXT NOT NULL DEFAULT '#000000',
    capas       TEXT NOT NULL DEFAULT '[]',      -- JSON: [{"url", "activa", "encima"}], hasta 2
    kbps        INTEGER NOT NULL DEFAULT 8000
);
CREATE TABLE IF NOT EXISTS entradas_activas (  -- hub: qué dirección de la entrada usa ahora el supervisor
    flujo       TEXT PRIMARY KEY REFERENCES flujos(id) ON DELETE CASCADE,
    indice      INTEGER NOT NULL DEFAULT 0,      -- 0: la del hub elegido; 1…: los respaldos, en orden
    firma       TEXT NOT NULL DEFAULT '',        -- huella de las direcciones: si se editan, vuelve a 0
    desde       REAL
);
CREATE TABLE IF NOT EXISTS paths_mtx (       -- paths de MediaMTX creados por el supervisor
    nombre      TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS eventos (
    t           REAL NOT NULL DEFAULT (strftime('%s','now')),
    flujo       TEXT, salida TEXT,
    texto       TEXT NOT NULL
);

-- ------------------------------------------------------------ hub de señales (matriz.py)
CREATE TABLE IF NOT EXISTS fuentes (
    id          TEXT PRIMARY KEY,          -- también es su path en MediaMTX
    nombre      TEXT NOT NULL,
    protocolo   TEXT NOT NULL,             -- 'srt' | 'rtmp' | 'hls' | 'webrtc' | 'ndi' | 'zixi'
    inicia      TEXT NOT NULL,             -- quién inicia la conexión: 'origen' (llama al hub) | 'hub'
    url         TEXT NOT NULL DEFAULT '',  -- inicia el hub: srt://host:puerto, rtmp(s)://…, https://….m3u8
    streamid    TEXT NOT NULL DEFAULT '',  -- srt, inicia el hub: el que pide el origen
    passphrase  TEXT NOT NULL DEFAULT '',  -- srt
    latencia_ms INTEGER NOT NULL DEFAULT 400,
    clave       TEXT NOT NULL DEFAULT '',  -- inicia el origen: con la que publica en el hub; rtmp del hub: la del origen
    opciones    TEXT NOT NULL DEFAULT '{}',-- JSON: {"puerto"} srt con puerto propio, {"variante"} hls, {"nombre"} ndi…
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS destinos (
    id          TEXT PRIMARY KEY,          -- si el hub entrega una dirección, también es su path en MediaMTX
    nombre      TEXT NOT NULL,
    protocolo   TEXT NOT NULL,
    inicia      TEXT NOT NULL,             -- 'hub' (el hub se conecta al destino) | 'receptor' (el hub entrega una dirección)
    url         TEXT NOT NULL DEFAULT '',  -- inicia el hub: srt://host:puerto, rtmp(s)://servidor/app, https://…/whip
    streamid    TEXT NOT NULL DEFAULT '',
    passphrase  TEXT NOT NULL DEFAULT '',
    latencia_ms INTEGER NOT NULL DEFAULT 300,
    clave       TEXT NOT NULL DEFAULT '',  -- inicia el hub: clave de emisión o token; receptor: con la que lee
    perfil      TEXT NOT NULL DEFAULT 'original',  -- el que propone la matriz al conectar una fuente
    audio       TEXT NOT NULL DEFAULT 'todas',     -- 'todas' | número de pista (desde 1)
    reconexion  TEXT NOT NULL DEFAULT '{}',        -- JSON: {"cada_s", "max_intentos"}
    opciones    TEXT NOT NULL DEFAULT '{}',
    creado      REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS rutas (         -- un destino recibe una sola fuente a la vez
    destino     TEXT PRIMARY KEY REFERENCES destinos(id) ON DELETE CASCADE,
    fuente      TEXT NOT NULL,             -- id de una fuente o path de un flujo heredado (ver matriz.heredadas)
    perfil      TEXT NOT NULL DEFAULT 'original',
    audio       TEXT NOT NULL DEFAULT 'todas',
    desde       REAL NOT NULL DEFAULT (strftime('%s','now')),
    por         TEXT NOT NULL DEFAULT ''   -- quién la conectó
);
CREATE TABLE IF NOT EXISTS perfiles (      -- además de los fijos (plan.PERFILES_FIJOS)
    id          TEXT PRIMARY KEY,
    nombre      TEXT NOT NULL,
    video       TEXT NOT NULL DEFAULT 'null',   -- JSON: {"alto", "kbps"} o null (el vídeo no se toca)
    audio       TEXT NOT NULL DEFAULT 'null'    -- JSON: {"kbps"} o null
);
CREATE TABLE IF NOT EXISTS inspecciones (  -- lo que trae cada fuente, visto por el supervisor
    fuente      TEXT PRIMARY KEY,          -- id de una fuente o path de un flujo heredado
    t           REAL NOT NULL,
    huella      TEXT NOT NULL DEFAULT '',  -- de la señal inspeccionada: si cambia, se vuelve a mirar
    pistas      TEXT NOT NULL DEFAULT 'null',   -- JSON como lo espera plan.planear
    error       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS senales (       -- cuándo hubo señal en cada path del hub
    path        TEXT PRIMARY KEY,
    lista       INTEGER NOT NULL DEFAULT 0,
    desde       REAL,                      -- desde cuándo está así
    ultima      REAL                       -- última vez que se vio con señal
);
CREATE TABLE IF NOT EXISTS ajustes (
    clave       TEXT PRIMARY KEY,
    valor       TEXT NOT NULL              -- JSON
);
CREATE TABLE IF NOT EXISTS cambios (       -- quién cambió qué en el hub y cuándo. Nunca lleva claves
    t           REAL NOT NULL DEFAULT (strftime('%s','now')),
    quien       TEXT NOT NULL DEFAULT '',  -- email del usuario, o 'El hub' si fue el supervisor
    rol         TEXT NOT NULL DEFAULT '',
    accion      TEXT NOT NULL,             -- «Activó una ruta», «Cambió la fuente al aire»…
    objeto      TEXT NOT NULL DEFAULT '',  -- «Estudio A → YouTube»
    detalle     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ordenes (       -- lo que el panel pide al supervisor que haga en su próxima vuelta
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    t           REAL NOT NULL DEFAULT (strftime('%s','now')),
    orden       TEXT NOT NULL,             -- 'reintentar'
    tarea       TEXT NOT NULL              -- id de la tarea (ruta_<destino>, ent_<fuente>…)
);
"""


def conectar():
    BASE.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(RUTA, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(ESQUEMA)
    migrar(c)
    _solo_el_usuario()
    return c


def _columna(c, tabla, nombre, definicion):
    """Añade la columna si falta y dice si la añadió. Tras un despliegue el panel y el supervisor
    arrancan a la vez y los dos pasan por aquí: si el otro la añadió entre mirar y añadir, SQLite
    responde que ya existe, y eso no es un fallo."""
    if nombre in {r["name"] for r in c.execute(f"PRAGMA table_info({tabla})")}:
        return False
    try:
        c.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")
    except sqlite3.OperationalError as err:
        if "duplicate column name" not in str(err):
            raise
        return False
    return True


def migrar(c):
    """Columnas añadidas después de crear la base (CREATE TABLE IF NOT EXISTS no las añade)."""
    _columna(c, "salidas", "fuente", "TEXT NOT NULL DEFAULT 'limpia'")     # fase 2: 'limpia' (en copia) o 'compuesta'
    for col in ("hub", "feed", "etiqueta"):   # 0.3.5, entrada 'hub': IP del hub, path del feed, «hub1 · 1BN INT»
        _columna(c, "flujos", col, "TEXT NOT NULL DEFAULT ''")
    if _columna(c, "flujos", "directo", "INTEGER NOT NULL DEFAULT 0"):     # 0.3.8: 1080i del hub, sin MediaMTX
        # los guardados con la 0.3.5-0.3.7 que leen del repetidor del hub: el repetidor solo reparte
        # feeds entrelazados («play/<feed>»), así que van directos
        c.execute("UPDATE flujos SET directo=1 WHERE entrada='hub' AND url LIKE '%streamid=play/%'")
    _columna(c, "flujos", "respaldos", "TEXT NOT NULL DEFAULT '[]'")       # JSON: el mismo feed en los otros hubs y, al final, el origen
    # seguidos sin llegar a enviar: los cuenta la política de reconexión
    _columna(c, "estado_salidas", "intentos", "INTEGER NOT NULL DEFAULT 0")


_permisos_vistos = False


def _solo_el_usuario():
    """La base guarda claves (passphrases, claves de emisión): solo la lee el usuario del servicio.
    Se comprueba una vez por proceso; los archivos -wal y -shm heredan los permisos de la base."""
    global _permisos_vistos
    if _permisos_vistos or not RUTA.exists():
        return
    _permisos_vistos = True
    for ruta in (RUTA, RUTA.with_name(RUTA.name + "-wal"), RUTA.with_name(RUTA.name + "-shm")):
        try:
            if ruta.stat().st_mode & 0o077:
                ruta.chmod(0o600)
        except OSError:
            pass


def filas(c, sql, *args):
    return [dict(r) for r in c.execute(sql, args)]


def ajuste(c, clave, defecto=None):
    """Valor guardado de un ajuste del hub (JSON), o `defecto`."""
    r = c.execute("SELECT valor FROM ajustes WHERE clave=?", (clave,)).fetchone()
    try:
        return json.loads(r["valor"]) if r else defecto
    except ValueError:
        return defecto


def guardar_ajuste(c, clave, valor):
    c.execute("INSERT INTO ajustes(clave, valor) VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
              (clave, json.dumps(valor, ensure_ascii=False)))


def cambio(c, quien, rol, accion, objeto="", detalle=""):
    """Anota un cambio hecho en el hub. Se guardan dos años."""
    c.execute("INSERT INTO cambios(quien, rol, accion, objeto, detalle) VALUES (?,?,?,?,?)",
              (quien, rol, accion, objeto, detalle))
    c.execute("DELETE FROM cambios WHERE t < strftime('%s','now') - 730*86400")


def evento(c, texto, flujo=None, salida=None):
    c.execute("INSERT INTO eventos(flujo, salida, texto) VALUES (?,?,?)", (flujo, salida, texto))
    c.execute("DELETE FROM eventos WHERE t < strftime('%s','now') - 30*86400")


def config():
    """config.json de la máquina (puertos, host público, tope de composiciones)."""
    try:
        return json.loads((BASE / "config.json").read_text())
    except (OSError, ValueError):
        return {}


def canales_recorder():
    """Canales de MSX Recorder en este equipo. `reenvio_udp` es el puerto local por el que el
    grabador reenvía el feed mientras graba (vacío: el canal no reenvía). `url`: su origen
    principal, que se usa si el canal no reenvía y es SRT (normalmente la lectura de un hub)."""
    ruta = pathlib.Path(config().get("recorder_canales", "/home/mediasat/recorder/canales.json"))
    try:
        return [{"id": c["id"], "nombre": c.get("nombre") or c["id"],
                 "reenvio_udp": int(c["reenvio_udp"]) if c.get("reenvio_udp") else None,
                 "url": (c.get("url") or "").strip()}
                for c in json.loads(ruta.read_text()) if c.get("id")]
    except (OSError, ValueError, TypeError, KeyError):
        return []


def reposo_recorder():
    """Estado del modo en reposo del Recorder de este equipo («Apagar servicio» en su System Monitor):
    el contenido de su reposo.json, que existe solo mientras la estación está en reposo, o None."""
    ruta = pathlib.Path(config().get("recorder_reposo", "/home/mediasat/recorder/reposo.json"))
    try:
        datos = json.loads(ruta.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        datos = {}                          # a medio escribir o ilegible: sigue en reposo
    return datos if isinstance(datos, dict) else {}


def fuentes(f):
    """Direcciones de la entrada de un flujo 'hub', en orden: la del hub elegido y sus respaldos."""
    try:
        respaldos = [u for u in json.loads(f.get("respaldos") or "[]") if isinstance(u, str) and u]
    except ValueError:
        respaldos = []
    return [f["url"], *respaldos] if f.get("url") else respaldos
