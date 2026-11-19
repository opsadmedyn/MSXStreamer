"""El guion que mide la carga en una estación se niega a arrancar donde podría tocar la producción.

Solo se prueban las negativas, que ocurren antes de mirar la estación: lo demás (arrancar el banco
al lado de otro, pasar la prueba, interrumpirla) se ensaya con el banco, ver docs/banco-de-pruebas.md.
"""
import os
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GUION = RAIZ / "scripts" / "medir-carga.sh"


def _correr(tmp_path, *args, **entorno):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHON", "NODE", "CHROMIUM", "DESPLAZA")}
    env.update({"MSXS_BANCO": str(tmp_path / "banco"), "PRODUCCION": str(tmp_path / "produccion")})
    env.update(entorno)
    return subprocess.run(["bash", str(GUION), *args], env=env, capture_output=True, text=True, timeout=60)


def test_no_acepta_los_puertos_de_produccion(tmp_path):
    r = _correr(tmp_path, "--solo-mirar", DESPLAZA="0")
    assert r.returncode == 2
    assert "DESPLAZA no puede ser 0" in r.stderr
    assert not (tmp_path / "banco").exists()


def test_el_desplazamiento_tiene_que_ser_un_numero(tmp_path):
    r = _correr(tmp_path, "--solo-mirar", DESPLAZA="cien")
    assert r.returncode == 2
    assert "tiene que ser un número" in r.stderr


def test_no_se_ejecuta_desde_la_carpeta_de_produccion(tmp_path):
    r = _correr(tmp_path, "--solo-mirar", PRODUCCION=str(RAIZ))
    assert r.returncode == 2
    assert "no se ejecuta desde la carpeta del Streamer" in r.stderr


def test_el_banco_no_va_dentro_de_produccion(tmp_path):
    prod = tmp_path / "produccion"
    r = _correr(tmp_path, "--solo-mirar", MSXS_BANCO=str(prod / "hub-banco"))
    assert r.returncode == 2
    assert "no puede estar dentro" in r.stderr
    assert not prod.exists()


def test_el_banco_no_contiene_a_produccion(tmp_path):
    r = _correr(tmp_path, "--solo-mirar", MSXS_BANCO=str(tmp_path), PRODUCCION=str(tmp_path / "streamer"))
    assert r.returncode == 2
    assert "no puede contener" in r.stderr


def test_una_opcion_desconocida_ensena_el_uso_y_no_arranca(tmp_path):
    r = _correr(tmp_path, "--arrancar-ya")
    assert r.returncode == 2
    assert "scripts/medir-carga.sh --solo-mirar" in r.stdout
    assert not (tmp_path / "banco").exists()
