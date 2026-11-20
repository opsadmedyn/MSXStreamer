"""La carga del equipo y de cada tarea, leída de /proc."""

import os
import subprocess
import sys
import time

import carga


def test_carga_del_equipo_y_de_una_tarea_que_gasta_cpu():
    # una tarea como las del hub: encabeza su grupo y tiene un hijo, que es quien trabaja
    codigo = "import subprocess,sys; subprocess.run([sys.executable,'-c','while True: pass'])"
    p = subprocess.Popen([sys.executable, "-c", codigo], start_new_session=True)
    try:
        m = carga.Medidor()
        primera = m.medir({"ruta_x": p.pid, "no-existe": 2 ** 22 - 3})
        assert primera["tareas"]["ruta_x"]["cpu_pct"] is None and "no-existe" not in primera["tareas"]
        time.sleep(1.2)
        segunda = m.medir({"ruta_x": p.pid})
        t = segunda["tareas"]["ruta_x"]
        assert t["procesos"] == 2 and t["memoria_mb"] > 1 and 60 < t["cpu_pct"] < 140       # un núcleo entero, más o menos
        assert segunda["nucleos"] == os.cpu_count() and 0 < segunda["cpu_pct"] <= 100
        assert segunda["memoria"]["total_mb"] > segunda["memoria"]["disponible_mb"] > 0
    finally:
        os.killpg(p.pid, 9)
        p.wait()


def test_lectura_de_nvidia_smi():
    completa = "NVIDIA GeForce RTX 5060 Ti, 12, 34, 0, 1810, 16311, 3\n"
    (t,) = carga.leer_gpu(completa)
    assert t == {"nombre": "NVIDIA GeForce RTX 5060 Ti", "uso_pct": 12.0, "codificador_pct": 34.0, "decodificador_pct": 0.0,
                 "memoria_mb": 1810.0, "memoria_total_mb": 16311.0, "sesiones_codificador": 3.0}
    (t,) = carga.leer_gpu("Quadro P2000, [N/A], 512, 5120\n")
    assert t["uso_pct"] is None and t["memoria_mb"] == 512.0 and t["codificador_pct"] is None
    assert carga.leer_gpu("") == []
