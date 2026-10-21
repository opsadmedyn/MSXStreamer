"""Media Syntaxis Streamer - base de datos: flujos, salidas y su estado.

El panel escribe aquí lo que se quiere (flujos y salidas activos) y el supervisor lo cumple.
Así, si el panel se reinicia, las salidas siguen al aire.
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
"""


def conectar():
    BASE.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(RUTA, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(ESQUEMA)
    migrar(c)
    return c


def migrar(c):
    """Columnas añadidas después de crear la base (CREATE TABLE IF NOT EXISTS no las añade)."""
    columnas = {r["name"] for r in c.execute("PRAGMA table_info(salidas)")}
    if "fuente" not in columnas:          # fase 2: 'limpia' (en copia) o 'compuesta'
        c.execute("ALTER TABLE salidas ADD COLUMN fuente TEXT NOT NULL DEFAULT 'limpia'")
    columnas = {r["name"] for r in c.execute("PRAGMA table_info(flujos)")}
    for col in ("hub", "feed", "etiqueta"):   # 0.3.5, entrada 'hub': IP del hub, path del feed, «hub1 · 1BN INT»
        if col not in columnas:
            c.execute(f"ALTER TABLE flujos ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    if "directo" not in columnas:             # 0.3.8: 1080i del hub, los destinos leen la entrada sin MediaMTX
        c.execute("ALTER TABLE flujos ADD COLUMN directo INTEGER NOT NULL DEFAULT 0")
        # los guardados con la 0.3.5-0.3.7 que leen del repetidor del hub: el repetidor solo reparte
        # feeds entrelazados («play/<feed>»), así que van directos
        c.execute("UPDATE flujos SET directo=1 WHERE entrada='hub' AND url LIKE '%streamid=play/%'")
    if "respaldos" not in columnas:           # JSON: el mismo feed en los otros hubs y, al final, el origen
        c.execute("ALTER TABLE flujos ADD COLUMN respaldos TEXT NOT NULL DEFAULT '[]'")


def filas(c, sql, *args):
    return [dict(r) for r in c.execute(sql, args)]


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


def fuentes(f):
    """Direcciones de la entrada de un flujo 'hub', en orden: la del hub elegido y sus respaldos."""
    try:
        respaldos = [u for u in json.loads(f.get("respaldos") or "[]") if isinstance(u, str) and u]
    except ValueError:
        respaldos = []
    return [f["url"], *respaldos] if f.get("url") else respaldos
