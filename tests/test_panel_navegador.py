"""El panel en un navegador de verdad: lo que solo falla pulsando.

Levanta el panel de esta versión con una base vacía y el Recorder de mentira del banco (sin MediaMTX
ni supervisor, que aquí no hacen falta) y lanza los guiones de tests/navegador/ con Node y
Playwright. Sin Node, sin Playwright o sin su Chromium, la prueba se salta.
"""

import json
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SIN_NAVEGADOR = 77          # así terminan los guiones cuando no tienen con qué probar


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def panel(tmp_path):
    """El panel en marcha, en 127.0.0.1 y con su propia base: devuelve su dirección."""
    pytest.importorskip("uvicorn")
    base = tmp_path / "base"
    base.mkdir()
    recorder, puerto = _puerto_libre(), _puerto_libre()
    (base / "config.json").write_text(json.dumps({
        "host_publico": "127.0.0.1",
        "mediamtx_api": f"http://127.0.0.1:{_puerto_libre()}",        # nadie escucha: como MediaMTX parado
        "recorder_api": f"http://127.0.0.1:{recorder}",
        "recorder_canales": str(tmp_path / "sin-recorder" / "canales.json"),
        "recorder_reposo": str(tmp_path / "sin-recorder" / "reposo.json"),
        "recorder_config": str(tmp_path / "sin-recorder" / "config.json"),
    }))
    registro = open(tmp_path / "panel.log", "w")
    procesos = [
        subprocess.Popen([sys.executable, str(RAIZ / "tests" / "banco" / "recorder_falso.py"), str(recorder)],
                         stdout=registro, stderr=subprocess.STDOUT),
        subprocess.Popen([sys.executable, "-m", "uvicorn", "servidor:app", "--host", "127.0.0.1", "--port", str(puerto)],
                         cwd=RAIZ / "streamer", env={"PATH": "/usr/bin:/bin", "MSXS_BASE": str(base)},
                         stdout=registro, stderr=subprocess.STDOUT),
    ]
    url = f"http://127.0.0.1:{puerto}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{url}/api/version", timeout=1).read()
                break
            except OSError:
                assert all(p.poll() is None for p in procesos), (tmp_path / "panel.log").read_text()[-2000:]
                time.sleep(0.2)
        else:
            pytest.fail("el panel no llegó a responder:\n" + (tmp_path / "panel.log").read_text()[-2000:])
        yield url
    finally:
        for p in procesos:
            p.terminate()
        for p in procesos:
            p.wait(timeout=10)
        registro.close()


def _navegador(guion, url):
    node = shutil.which("node")
    if not node:
        pytest.skip("sin Node no se puede abrir el panel en un navegador")
    r = subprocess.run([node, str(RAIZ / "tests" / "navegador" / guion), url], capture_output=True, text=True, timeout=180)
    if r.returncode == SIN_NAVEGADOR:
        pytest.skip("sin Playwright o sin su Chromium: " + r.stdout.strip()[-200:])
    return r


def test_borrar_una_salida_o_un_flujo_los_quita_de_la_pantalla_y_la_seccion_sigue_al_dia(panel):
    r = _navegador("borrar.js", panel)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "todo bien" in r.stdout
