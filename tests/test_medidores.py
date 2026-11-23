"""Medidores de audio: el comando por pista y la lectura de lo que escribe astats."""

import medidores

SALIDA = """frame:0    pts:0       pts_time:0
lavfi.astats.1.Peak_level=-21.067829
lavfi.astats.1.RMS_level=-24.793768
lavfi.astats.2.Peak_level=-inf
lavfi.astats.2.RMS_level=-inf
frame:1    pts:4784    pts_time:0.0996667
lavfi.astats.1.Peak_level=-3.5
"""


def test_lee_pico_y_rms_por_canal_y_el_silencio():
    canales = {}
    assert medidores.leer(SALIDA, canales)
    assert canales == {1: {"pico": -3.5, "rms": -24.8}, 2: {"pico": -120.0, "rms": -120.0}}
    assert not medidores.leer("frame:2    pts:9600    pts_time:0.2\n", canales)
    assert medidores._nivel("nan") == -120.0 and medidores._nivel("-200.5") == -120.0 and medidores._nivel("0.000000") == 0.0


def test_un_grafo_por_pista_cada_uno_a_su_descriptor():
    cmd = medidores.comando("estudio-a-f621", [7, 9])
    grafo = cmd[cmd.index("-filter_complex") + 1]
    assert grafo.count("astats") == 2 and "[0:a:0]" in grafo and "[0:a:1]" in grafo
    assert "file=/dev/fd/7:direct=1[m0]" in grafo and "file=/dev/fd/9:direct=1[m1]" in grafo
    assert "-vn" in cmd and cmd[-3:] == ["-f", "null", "-"] and cmd.count("-map") == 2
    assert "streamid=read:estudio-a-f621" in cmd[cmd.index("-i") + 1]
