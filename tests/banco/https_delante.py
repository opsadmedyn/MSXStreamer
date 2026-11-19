#!/usr/bin/env python3
"""Banco de pruebas: un proxy inverso HTTPS delante de un servidor que solo habla HTTP.

    https_delante.py [--sin-origen | --solo-real-ip] <certificado> <clave> <dirección> <puerto>=<puerto de destino> [...]

Hace en el banco el papel del proxy con certificado que una estación pone delante de HLS y de WebRTC
(el Caddy del Recorder, por ejemplo): atiende con TLS en cada <puerto> de <dirección> y pasa cada
petición, ya en claro, al <puerto de destino> de 127.0.0.1. No reescribe rutas, que es lo que se le
pide a ese proxy cuando HLS y WebRTC tienen cada uno su nombre.

MediaMTX recibe todo lo del proxy desde 127.0.0.1 y juzga de quién es cada petición por lo que el
proxy anote: sin eso la tomaría por algo del propio equipo, que entra sin clave. Sirve sobre todo
para hacer de proxy mal puesto, que es lo que el hub tiene que saber ver:

  (sin opciones)   como Caddy: anota en X-Forwarded-For la dirección de quien conecta y descarta la
                   que traiga la petición.
  --sin-origen     no anota nada, como un proxy al que no se le ha dicho (nginx no lo hace solo).
  --solo-real-ip   anota la dirección de quien conecta en X-Real-IP y deja pasar la X-Forwarded-For
                   que traiga la petición, como un nginx al que solo se le puso
                   `proxy_set_header X-Real-IP $remote_addr;`. MediaMTX mira antes X-Forwarded-For:
                   quien la mande con 127.0.0.1 pasa por ser el propio equipo.

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


ANOTA = {"": "x-forwarded-for", "--sin-origen": None, "--solo-real-ip": "x-real-ip"}       # la cabecera que pone cada modo


async def _peticiones(lector, e_destino, cliente, anotar):
    """Del cliente al servidor, petición a petición: la misma, con la dirección de origen anotada en la
    cabecera `anotar` (None: en ninguna). De las que traiga la petición solo pasa X-Forwarded-For, y
    solo si no es la que se anota."""
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
                if nombre == b"x-real-ip" or (nombre == b"x-forwarded-for" and anotar != "x-real-ip"):
                    continue                            # lo que diga quien conecta no vale
                if nombre == b"transfer-encoding":
                    raise ValueError("este proxy de pruebas no pasa cuerpos en trozos")
                if nombre == b"content-length":
                    cuerpo = int(valor.strip() or 0)
                pasan.append(linea)
            if anotar:
                pasan.append(f"{anotar}: {cliente}".encode())
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
          + (f" (la dirección de origen, en {anotar})" if anotar else " (sin anotar la dirección de origen)"), flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servidores))


def main(argv):
    modos = [a for a in argv[1:] if a.startswith("--")]
    argv = [a for a in argv if not a.startswith("--")]
    if len(argv) < 5 or len(modos) > 1 or (modos and modos[0] not in ANOTA):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    anotar = ANOTA[modos[0] if modos else ""]
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
