#!/usr/bin/env python3
"""Media Syntaxis Streamer - lanzador: corre un ffmpeg y guarda lo que escribe, sin claves.

    correr.py <registro> <comando...>

El supervisor no lanza ffmpeg a pelo: lanza este programa, que escribe en el registro la línea de
arranque con las claves tapadas, arranca el comando y copia al registro lo que este escriba por su
salida de errores, línea a línea y también sin claves (ffmpeg repite la URL de destino, con la clave
de emisión, cuando no puede conectar). Las claves a tapar llegan en MSXS_SECRETOS (una lista JSON),
que no se pasa al comando.

Vive y muere con su ffmpeg: comparten grupo de procesos (el supervisor los para con una sola señal),
sale con el mismo código, y si este programa muere por su cuenta el núcleo avisa a ffmpeg para que
termine (así no queda un ffmpeg emitiendo sin que nadie lo vigile, que acabaría duplicado).
"""

import ctypes
import json
import os
import select
import signal
import subprocess
import sys
import time

import secretos

LINEA_MAX = 4000
PR_SET_PDEATHSIG = 1


def _morir_con_el_padre():
    try:
        ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:                       # sin prctl (no es Linux): se sigue igual
        pass


def main(argv):
    if len(argv) < 3:
        print("uso: correr.py <registro> <comando...>", file=sys.stderr)
        return 2
    registro, cmd = argv[1], argv[2:]
    try:
        ocultos = [s for s in json.loads(os.environ.pop("MSXS_SECRETOS", "[]")) if isinstance(s, str) and s]
    except ValueError:
        ocultos = []
    with open(registro, "ab", buffering=0) as salida:
        salida.write(f"\n== {time.strftime('%F %T')} {secretos.ocultar_comando(cmd, ocultos)}\n".encode())
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, preexec_fn=_morir_con_el_padre)
        except OSError as err:
            salida.write(f"No se pudo lanzar: {err.strerror or err}\n".encode())
            return 127

        def parar(*_):
            try:
                p.terminate()
            except OSError:
                pass
        signal.signal(signal.SIGTERM, parar)
        signal.signal(signal.SIGINT, parar)

        # Se lee mientras el comando viva. Si termina y otro proceso de su grupo sigue con la salida
        # abierta (las capturas de una composición), no se espera a ese: se guarda lo que quede y
        # se sale, para que el supervisor vea la caída.
        os.set_blocking(p.stderr.fileno(), False)
        resto, abierto = b"", True
        while abierto:
            terminado = p.poll() is not None
            if not terminado:
                try:
                    select.select([p.stderr], [], [], 0.5)
                except InterruptedError:
                    continue
            while True:
                try:
                    trozo = os.read(p.stderr.fileno(), 65536)
                except BlockingIOError:
                    break
                except InterruptedError:
                    continue
                except OSError:
                    abierto = False
                    break
                if not trozo:
                    abierto = False
                    break
                *lineas, resto = (resto + trozo).replace(b"\r", b"\n").split(b"\n")
                if len(resto) > LINEA_MAX:  # una línea sin fin: se corta para no guardarla en memoria
                    lineas.append(resto)
                    resto = b""
                for linea in lineas:
                    if linea.strip():
                        texto = secretos.ocultar(linea[:LINEA_MAX].decode(errors="replace"), ocultos)
                        salida.write(texto.encode() + b"\n")
            if terminado:
                break
        if resto.strip():
            salida.write(secretos.ocultar(resto[:LINEA_MAX].decode(errors="replace"), ocultos).encode() + b"\n")
        codigo = p.wait()
    return codigo if codigo >= 0 else 128 - codigo


if __name__ == "__main__":
    sys.exit(main(sys.argv))
