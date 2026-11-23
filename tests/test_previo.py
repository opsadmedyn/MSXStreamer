"""scripts/previo.py: antes de desplegar dice qué haría esta versión con lo que la estación tiene
al aire, y no toca nada. Aquí corre contra una carpeta de servicio de mentira, con procesos que
hacen de salidas y un MediaMTX de mentira que apunta todo lo que se le pide.
(tests/banco/relevo.sh lo pasa además por la estación de ensayo, antes de cada relevo.)"""

import http.server
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import threading

import pytest

import db
import mtx
import supervisor

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CLAVES = ("frase-de-paso-de-la-entrada", "frase-de-paso-de-la-salida", "clave-de-emision-secreta", "198.51.100.7")


class MediaMTXFalso(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.server.pedidos.append(("GET", self.path))
        r = self.server.respuestas.get(self.path.split("?")[0])
        if r is None:
            self.send_error(404)
            return
        datos = json.dumps(r).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_POST(self):                      # si previo.py llegara a pedir un cambio, quedaría apuntado
        self.server.pedidos.append((self.command, self.path))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_PATCH = do_DELETE = do_PUT = do_POST


@pytest.fixture
def estacion(tmp_path, monkeypatch):
    """Una carpeta de servicio con su base y su config.json, un MediaMTX de mentira y, aparte, la
    carpeta temporal a la que previo.sh lleva previo.py y el código."""
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MediaMTXFalso)
    servidor.pedidos, servidor.respuestas = [], {}
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = tmp_path / "servicio"
    (base / "run").mkdir(parents=True)
    (base / "logs").mkdir()
    (base / "VERSION").write_text("0.3.9\n")
    (base / "config.json").write_text(json.dumps({
        "ffmpeg": "/usr/local/bin/ffmpeg", "mediamtx_api": f"http://127.0.0.1:{servidor.server_address[1]}",
        "recorder_canales": str(tmp_path / "sin-canales.json"), "recorder_reposo": str(tmp_path / "sin-reposo.json")}))
    c = sqlite3.connect(base / "streamer.db", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(db.ESQUEMA)
    db.migrar(c)
    c.execute("""INSERT INTO flujos(id, nombre, entrada, url, passphrase, latencia_ms)
                 VALUES ('f1', 'Canal Uno', 'pull', 'srt://198.51.100.7:9000', 'frase-de-paso-de-la-entrada', 200)""")
    previo = tmp_path / "previo"
    (previo / "streamer").mkdir(parents=True)
    for f in (RAIZ / "streamer").glob("*.py"):
        shutil.copy(f, previo / "streamer")
    shutil.copy(RAIZ / "scripts" / "previo.py", previo)
    shutil.copy(RAIZ / "VERSION", previo)
    monkeypatch.setattr(supervisor, "RUN", base / "run")         # para calcular aquí las firmas como allí
    monkeypatch.setattr(supervisor, "FFMPEG", "/usr/local/bin/ffmpeg")
    procesos = []

    class Estacion:
        pass
    e = Estacion()
    e.base, e.c, e.servidor = base, c, servidor

    def salida(sid, nombre, tipo="srt", activo=1, corre=True, firma=None):
        """Una salida del flujo f1, con su proceso (o sin él) y la firma que la base tiene guardada."""
        url = "srt://198.51.100.7:9100" if tipo == "srt" else "rtmp://198.51.100.7/live"
        c.execute("""INSERT INTO salidas(id, flujo, nombre, tipo, url, passphrase, clave, activo)
                     VALUES (?, 'f1', ?, ?, ?, 'frase-de-paso-de-la-salida', 'clave-de-emision-secreta', ?)""",
                  (sid, nombre, tipo, url, activo))
        fila = dict(c.execute("SELECT * FROM salidas WHERE id=?", (sid,)).fetchone())
        pid = proceso(sid) if corre else None
        c.execute("INSERT INTO estado_salidas(salida, pid, firma) VALUES (?,?,?)",
                  (sid, pid, firma or supervisor.firma(supervisor.comando(fila, "f1"))))
        return pid

    def proceso(tarea):
        """Hace de ffmpeg de una tarea: lleva su .prog en la orden, que es por lo que se le reconoce."""
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", str(base / "run" / f"{tarea}.prog")])
        procesos.append(p)
        return p.pid

    def mediamtx(paths=None, general=None, senales=None, hls=()):
        f1 = dict(c.execute("SELECT * FROM flujos WHERE id='f1'").fetchone())
        paths = {"f1": mtx.conf_de_flujo(f1, [], 0)} if paths is None else paths
        general = {**mtx.acceso_deseado(set()), "rtmp": False, "webrtc": False} if general is None else general
        senales = {"f1": {"ready": True, "readers": [{"type": "srtConn", "id": "x"}]}} if senales is None else senales
        lista = lambda items: {"pageCount": 1, "itemCount": len(items), "items": items}
        servidor.respuestas.update({
            "/v3/config/paths/list": lista([{"name": n, **conf} for n, conf in paths.items()]),
            "/v3/paths/list": lista([{"name": n, **info} for n, info in senales.items()]),
            "/v3/config/global/get": general,
            "/v3/hlsmuxers/list": lista([{"path": p, "created": "", "lastRequest": "", "bytesSent": 1} for p in hls])})

    def previo_py():
        """Ejecuta previo.py como en la estación. Devuelve (salida del proceso, lo que dijo) y
        comprueba lo que vale siempre: no pide ningún cambio, no toca la carpeta del servicio y
        no escribe direcciones ni claves."""
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        antes = {f.relative_to(base).as_posix(): f.read_bytes() for f in sorted(base.rglob("*")) if f.is_file()}
        r = subprocess.run([sys.executable, "-B", str(previo / "previo.py"), str(base)], capture_output=True, text=True,
                           timeout=60, env={"PATH": os.environ["PATH"], "no_proxy": "*"})
        despues = {f.relative_to(base).as_posix(): f.read_bytes() for f in sorted(base.rglob("*")) if f.is_file()}
        assert r.stderr == "", r.stderr
        assert despues == antes, "previo.py tocó la carpeta del servicio"
        assert {m for m, _ in servidor.pedidos} == {"GET"}, servidor.pedidos
        assert not any(k in r.stdout for k in CLAVES) and "://" not in r.stdout, r.stdout
        assert not list(previo.glob("streamer.db*")), "previo.py dejó su copia de la base"
        return r.returncode, r.stdout

    e.salida, e.proceso, e.mediamtx, e.previo_py = salida, proceso, mediamtx, previo_py
    yield e
    for p in procesos:
        p.kill()
        p.wait()
    servidor.shutdown()
    servidor.server_close()


def test_si_esta_version_calcula_lo_mismo_dice_que_conserva_cada_proceso_y_que_no_corta_nada(estacion):
    pid = estacion.salida("s-uno", "A Castr")
    estacion.salida("s-dos", "A YouTube", tipo="rtmp")
    estacion.salida("s-reintenta", "Sin destino", corre=False)
    estacion.mediamtx()
    codigo, dicho = estacion.previo_py()
    assert codigo == 0, dicho
    assert "al arrancar en esta estación (instalada: v0.3.9). No se cambia nada." in dicho
    assert f"   se conserva      salida «A Castr» (srt) del flujo «Canal Uno» · proceso {pid}\n" in dicho
    assert "se conserva      salida «A YouTube» (rtmp) del flujo «Canal Uno»" in dicho
    assert "Sin proceso ahora: 1 " in dicho and "   salida «Sin destino» (srt) del flujo «Canal Uno»\n" in dicho
    assert "   paths: no le pediría crear, cambiar ni borrar ninguno\n" in dicho
    assert "   configuración general: no le pediría cambiar nada\n" in dicho
    assert "   HLS: nadie mirando ahora\n" in dicho
    assert dicho.endswith("Resumen: 2 procesos al aire; se conservan 2, se relanzarían 0, se pararían 0. No se cortaría nada.\n")


def test_avisa_de_cada_proceso_que_relanzaria_o_pararia(estacion):
    estacion.salida("s-igual", "Igual")
    otra = estacion.salida("s-otra", "Con otro comando", firma="0123456789abcdef")
    apagada = estacion.salida("s-apagada", "Desactivada", tipo="rtmp", activo=0)
    suelto = estacion.proceso("sin-dueno")
    estacion.mediamtx()
    codigo, dicho = estacion.previo_py()
    assert codigo == 1, dicho
    assert f"   SE RELANZARÍA    salida «Con otro comando» (srt) del flujo «Canal Uno» · proceso {otra}: " in dicho
    assert f"   SE PARARÍA       salida «Desactivada» (rtmp) del flujo «Canal Uno» · proceso {apagada}: ya no lo pide nadie" in dicho
    assert f"   SE PARARÍA       tarea sin-dueno · proceso {suelto}: corre sin que la base lo conozca ni nadie lo pida\n" in dicho
    assert dicho.endswith("Resumen: 4 procesos al aire; se conservan 1, se relanzarían 1, se pararían 2. "
                          "SE CORTARÍA: 3 procesos al aire.\n")


def test_avisa_de_lo_que_le_pediria_cambiar_a_mediamtx_y_no_se_lo_pide(estacion):
    estacion.salida("s-uno", "A Castr")
    estacion.c.execute("INSERT INTO paths_mtx(nombre) VALUES ('f1'), ('de-antes')")
    sin_proxies = {k: v for k, v in mtx.acceso_deseado(set()).items() if "TrustedProxies" not in k}
    estacion.mediamtx(paths={"f1": {"source": "srt://198.51.100.7:9000?otra=cosa", "sourceOnDemand": False},
                             "de-antes": {"source": "publisher"}},
                      general={**sin_proxies, "rtmp": False, "webrtc": False}, hls=["f1"])
    codigo, dicho = estacion.previo_py()
    assert codigo == 1, dicho
    assert "   path f1: le cambiaría source Y TIENE SEÑAL O LECTORES: SE CORTARÍA\n" in dicho
    assert "   path de-antes: lo borraría (ahora sin señal ni lectores)\n" in dicho
    assert "   configuración general: le pediría cambiar hlsTrustedProxies, webrtcTrustedProxies\n" in dicho
    assert "   HLS: 1 con alguien mirando ahora (f1): SE QUEDARÍA UNOS SEGUNDOS SIN IMAGEN\n" in dicho
    assert dicho.endswith("Resumen: 1 procesos al aire; se conservan 1, se relanzarían 0, se pararían 0. "
                          "SE CORTARÍA: 1 señales de MediaMTX, el HLS de quien está mirando, unos segundos.\n")


def test_con_alguien_mirando_por_hls_no_avisa_si_mediamtx_ya_tiene_las_listas_de_proxies(estacion):
    estacion.salida("s-uno", "A Castr")
    estacion.mediamtx(hls=["f1"])
    codigo, dicho = estacion.previo_py()
    assert codigo == 0, dicho
    assert "   HLS: 1 con alguien mirando ahora (f1)\n" in dicho
    assert dicho.endswith("No se cortaría nada.\n")


def test_si_mediamtx_no_responde_o_falta_la_base_dice_que_no_pudo_comprobarlo(estacion, tmp_path):
    estacion.salida("s-uno", "A Castr")
    codigo, dicho = estacion.previo_py()                # el MediaMTX de mentira aún no sabe responder: 404
    assert codigo == 2 and "!! no se pudo comprobar: MediaMTX no responde" in dicho, dicho
    r = subprocess.run([sys.executable, "-B", str(tmp_path / "previo" / "previo.py"), str(tmp_path / "no-existe")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "!! falta la base del servicio" in r.stdout, r.stdout
    assert not (tmp_path / "no-existe").exists()
