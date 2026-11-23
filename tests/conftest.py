"""Pruebas del Streamer. Los módulos de streamer/ se importan como en producción (sueltos, con
streamer/ como directorio de trabajo) y la base de datos va a una carpeta temporal por sesión."""

import os
import pathlib
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "streamer"))
os.environ.setdefault("MSXS_BASE", tempfile.mkdtemp(prefix="msxs-pruebas-"))
