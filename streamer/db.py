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
    entrada     TEXT NOT NULL,             -- 'pull' | 'recorder'
    url         TEXT NOT NULL DEFAULT '',  -- pull: srt://host:puerto
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
    return c


def filas(c, sql, *args):
    return [dict(r) for r in c.execute(sql, args)]


def evento(c, texto, flujo=None, salida=None):
    c.execute("INSERT INTO eventos(flujo, salida, texto) VALUES (?,?,?)", (flujo, salida, texto))
    c.execute("DELETE FROM eventos WHERE t < strftime('%s','now') - 30*86400")


def config():
    """config.json de la máquina (puertos, clave del panel, host público)."""
    try:
        return json.loads((BASE / "config.json").read_text())
    except (OSError, ValueError):
        return {}


def canales_recorder():
    """Canales de MSX Recorder en este equipo. `reenvio_udp` es el puerto local por el que el
    grabador reenvía el feed mientras graba (vacío: el canal no reenvía)."""
    ruta = pathlib.Path(config().get("recorder_canales", "/home/mediasat/recorder/canales.json"))
    try:
        return [{"id": c["id"], "nombre": c.get("nombre") or c["id"],
                 "reenvio_udp": int(c["reenvio_udp"]) if c.get("reenvio_udp") else None}
                for c in json.loads(ruta.read_text()) if c.get("id")]
    except (OSError, ValueError, TypeError, KeyError):
        return []
