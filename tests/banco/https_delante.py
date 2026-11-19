#!/usr/bin/env python3
"""Banco de pruebas: un proxy inverso HTTPS delante de un servidor que solo habla HTTP.

    https_delante.py [--sin-origen] <certificado> <clave> <dirección> <puerto>=<puerto de destino> [...]

Hace en el banco el papel del proxy con certificado que una estación pone delante de HLS y de WebRTC
(el Caddy del Recorder, por ejemplo): atiende con TLS en cada <puerto> de <dirección> y pasa cada
petición, ya en claro, al <puerto de destino> de 127.0.0.1. No reescribe rutas, que es lo que se le
pide a ese proxy cuando HLS y WebRTC tienen cada uno su nombre.

Como hace Caddy, anota en X-Forwarded-For la dirección de quien conecta y descarta la que traiga la
petición. MediaMTX recibe todo lo del proxy desde 127.0.0.1 y juzga por esa cabecera de quién es:
sin ella lo tomaría por algo del propio equipo, que entra sin clave. Con --sin-origen no la anota,
como un proxy al que no se le ha dicho (nginx no la pone solo).

Solo para pruebas: el certificado es de usar y tirar, quien conecta no puede comprobarlo, y de HTTP
entiende lo justo (peticiones con Content-Length o sin cuerpo).
"""

import asyncio
import ssl
import sys


def _cerrar(escritor):
    try:
        escritor.close()
    except OSError:
        pass


async def _peticiones(lector, e_destino, cliente, anotar):
    """Del cliente al servidor, petición a petición: la misma, con la dirección de origen anotada."""
    try:
        while True:
            try:
                cabeza = await lector.readuntil(b"\r\n\r\n")
            except asyncio.IncompleteReadError:
                break                                   # el cliente cerró
            lineas, cuerpo = cabeza[:-4].split(b"\r\n"), 0
            pasan = [lineas[0]]
            for linea in lineas[1:]:
                nombre, _, valor = linea.partition(b":")
                nombre = nombre.strip().lower()
                if nombre == b"x-forwarded-for":
                    continue                            # lo que diga quien conecta no vale
                if nombre == b"transfer-encoding":
                    raise ValueError("este proxy de pruebas no pasa cuerpos en trozos")
                if nombre == b"content-length":
                    cuerpo = int(valor.strip() or 0)
                pasan.append(linea)
            if anotar:
                pasan.append(b"X-Forwarded-For: " + cliente.encode())
            e_destino.write(b"\r\n".join(pasan) + b"\r\n\r\n")
            while cuerpo > 0:
                trozo = await lector.read(min(65536, cuerpo))
                if not trozo:
                    return
                e_destino.write(trozo)
                cuerpo -= len(trozo)
            await e_destino.drain()
    except (OSError, ValueError, asyncio.LimitOverrunError):
        pass
    finally:
        _cerrar(e_destino)


async def _respuestas(l_destino, escritor):
    """Del servidor al cliente, tal cual."""
    try:
        while True:
            datos = await l_destino.read(65536)
            if not datos:
                break
            escritor.write(datos)
            await escritor.drain()
    except OSError:
        pass
    finally:
        _cerrar(escritor)


async def _atender(lector, escritor, destino, anotar):
    try:
        l_destino, e_destino = await asyncio.open_connection("127.0.0.1", destino)
    except OSError:
        _cerrar(escritor)
        return
    cliente = (escritor.get_extra_info("peername") or ("",))[0]
    await asyncio.gather(_peticiones(lector, e_destino, cliente, anotar), _respuestas(l_destino, escritor))


async def servir(contexto, direccion, pares, anotar):
    servidores = [await asyncio.start_server(lambda l, e, d=destino: _atender(l, e, d, anotar), direccion, puerto,
                                             ssl=contexto, limit=1 << 20) for puerto, destino in pares]
    print(f"escuchando en {direccion}: " + " ".join(f"{p}->{d}" for p, d in pares)
          + ("" if anotar else " (sin anotar la dirección de origen)"), flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servidores))


def main(argv):
    anotar = "--sin-origen" not in argv
    argv = [a for a in argv if a != "--sin-origen"]
    if len(argv) < 5:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(argv[1], argv[2])
    pares = [tuple(int(x) for x in par.split("=", 1)) for par in argv[4:]]
    try:
        asyncio.run(servir(contexto, argv[3], pares, anotar))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
