"""Pruebas del generador de la composición (sin ffmpeg): python3 -m unittest discover tests"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "streamer"))
import composicion  # noqa: E402

RUN, CARPETA = pathlib.Path("/run/x"), pathlib.Path("/run/msxs/f1")


def comp(capas, **k):
    return {"flujo": "f1", "activa": 1, "x": 422, "y": 0, "ancho": 1498, "fondo": "#000000",
            "kbps": 8000, "capas": json.dumps(capas), **k}


def cmd(c):
    return composicion.comando(c, "rec_canal1", "ffmpeg", RUN, "componer.sh", CARPETA)


class Composicion(unittest.TestCase):
    def test_shortest_en_todas_las_capas(self):
        for capas in ([{"url": "http://a", "encima": True}],
                      [{"url": "http://a", "encima": False}, {"url": "http://b", "encima": True}]):
            g = composicion.filtro(comp(capas))
            overlays = [p for p in g.split(";") if "overlay=" in p]
            self.assertTrue(overlays)
            for p in overlays:
                self.assertIn("shortest=1", p, p)

    def test_mostrar_ocultar_y_direccion_no_relanzan(self):
        a = cmd(comp([{"url": "http://a", "activa": True}]))
        b = cmd(comp([{"url": "http://otra", "activa": False, "recarga": 3}]))
        self.assertEqual(a, b)
        self.assertFalse(any("http://" in x for x in a))

    def test_capa_oculta_sigue_siendo_entrada(self):
        c = cmd(comp([{"url": "http://a", "activa": False}]))
        self.assertIn(str(CARPETA / "capa1.yuv"), c)
        self.assertIn("image2", c)

    def test_estado_capas(self):
        e = json.loads(composicion.estado_capas(comp([{"url": "http://a", "activa": False, "recarga": 2}, {"url": ""}])))
        self.assertEqual(e, {"capas": [{"url": "http://a", "visible": False, "recarga": 2}]})

    def test_preparar_capas_transparente(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            composicion.preparar_capas(comp([{"url": "http://a"}, {"url": "http://b"}]), d)
            for i in (1, 2):
                datos = (d / f"capa{i}.yuv").read_bytes()
                self.assertEqual(len(datos), composicion.TAM_CAPA)
                self.assertEqual(datos[-1], 0)            # alfa 0

    def test_sin_capas_sin_entradas(self):
        c = cmd(comp([]))
        self.assertEqual(c.count("-i"), 1)


if __name__ == "__main__":
    unittest.main()
