#!/usr/bin/env python3
"""Media Syntaxis Streamer - espera de una fuente HLS: no empezar a leerla sin margen.

    espera_hls.py <variante> <segmentos> <comando de ffmpeg...>

El hub lee una lista HLS en vivo unos segmentos por detrás del directo, como un reproductor
(comandos.HLS_DESDE): ese margen es lo que deja que la señal salga continua aunque los segmentos
aparezcan de golpe. Pero una lista que acaba de nacer tiene un solo segmento: pasa cuando el origen
arranca después de configurar el hub y cuando el origen crea la lista al pedírsela alguien, como
hace MediaMTX (que además la rellena con huecos por delante). ffmpeg empezaría por ese segmento,
sin margen, y ya no lo recupera: lo que entrega el hub sale entonces a golpes de un segmento (en el
banco, parones de más de 1 s en cada lector y un navegador viéndolo por WebRTC a 26 fotogramas por
segundo en vez de 30).

Este programa mira la lista antes de leerla. Si ya tiene los segmentos que hacen falta, o está
cerrada, se convierte en el ffmpeg sin esperar. Si tiene menos, espera a que los tenga, con un tope,
y va diciendo por su salida de errores qué espera: el lanzador (correr.py) lo guarda en el registro
y el supervisor lo enseña como evidencia del estado de la fuente. Si la lista no se puede leer desde
aquí, no espera nada: ffmpeg lo intenta y dice por qué falla, como siempre.

Al terminar se convierte en el ffmpeg (exec): mismo PID, mismo grupo de procesos y el mismo aviso
del núcleo si muere el lanzador. La dirección de la lista es lo que sigue a "-i" en el comando; aquí
no se escribe en ningún sitio, porque puede llevar una clave.
"""

import os
import sys
import time
import urllib.parse
import urllib.request

ESPERA = "Esperando a que la lista HLS"     # así empieza lo que dice mientras espera (supervisor.py y estados.py)
TOPE = 45.0                                 # nunca se espera más que esto, tarde lo que tarde el origen
MARGEN = 5.0                                # sobre lo que deberían tardar en aparecer los segmentos que faltan
# De cada lectura de la lista: lo mismo que se le da a ffmpeg (-rw_timeout en comandos.entrada). Un
# origen que crea la lista al pedírsela no contesta la primera vez hasta tener un segmento entero
# (MediaMTX, en el banco, 4 s con un fotograma clave cada 2 s); cortando antes, ffmpeg la abriría
# por su cuenta con la lista recién nacida, que es justo lo que se quiere evitar
PLAZO = 15.0
SIN_DURACION = 2.0                          # segundos por segmento si la lista no lo dice


def variantes(texto):
    """Direcciones de las variantes de una lista maestra, en el orden en que las declara (el mismo en
    que ffmpeg numera sus programas y el panel las ofrece)."""
    direcciones, pendiente = [], False
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("#EXT-X-STREAM-INF:"):
            pendiente = True
        elif linea and not linea.startswith("#") and pendiente:
            direcciones.append(linea)
            pendiente = False
    return direcciones


def medios(texto):
    """(segmentos, segundos de cada uno, cerrada) de una lista de medios. Cuentan los segmentos del
    final de la lista que se pueden pedir, que es de donde se empieza a leer: no los huecos
    (#EXT-X-GAP) ni lo que haya antes de uno. MediaMTX rellena con huecos una lista recién nacida
    hasta sus siete entradas, y ffmpeg, que no conoce esa marca, los pide y se los salta uno a uno.
    Tampoco cuentan los trozos de baja latencia (#EXT-X-PART): ffmpeg lee segmentos enteros."""
    segmentos, declarada, mayor, con_duracion, hueco = 0, 0.0, 0.0, False, False
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("#EXTINF:"):
            con_duracion = True
            try:
                mayor = max(mayor, float(linea[8:].split(",")[0]))
            except ValueError:
                pass
        elif linea.startswith("#EXT-X-GAP"):
            hueco = True
        elif linea.startswith("#EXT-X-TARGETDURATION:"):
            try:
                declarada = float(linea.split(":", 1)[1])
            except ValueError:
                pass
        elif linea and not linea.startswith("#"):       # la dirección de un segmento: cierra lo dicho de él
            if con_duracion:
                segmentos = 0 if hueco else segmentos + 1
            con_duracion = hueco = False
    return segmentos, declarada or mayor or SIN_DURACION, "#EXT-X-ENDLIST" in texto


def leer(url):
    """(texto, dirección final tras las redirecciones) de una lista. Si no se puede leer o no es una
    lista HLS, lanza OSError o ValueError."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("no es http")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "msx-hub"}), timeout=PLAZO) as r:
        texto = r.read(1_000_000).decode(errors="replace")
        if "#EXTM3U" not in texto[:200]:
            raise ValueError("no es una lista HLS")
        return texto, r.geturl()


def _segmentos(n):
    return "1 segmento" if n == 1 else f"{n} segmentos"


def esperar(url, variante, hacen_falta, decir, abrir=leer, ahora=time.monotonic, dormir=time.sleep):
    """Espera, con tope, a que la lista de medios de `url` (o de su variante, si es maestra) tenga
    `hacen_falta` segmentos. Lo que no puede resolver (no se lee, no es una lista, no tiene esa
    variante) lo lanza, y quien llama arranca ffmpeg sin más."""
    texto, final = abrir(url)
    if "#EXT-X-STREAM-INF" in texto:
        url = urllib.parse.urljoin(final, variantes(texto)[variante])
        texto, _ = abrir(url)
    inicio, limite, dicho = ahora(), None, None
    while True:
        n, dura, cerrada = medios(texto)
        if cerrada or n >= hacen_falta:
            break
        if limite is None:                  # lo que deberían tardar los que faltan, y un margen
            limite = inicio + min(TOPE, (hacen_falta - n) * dura + MARGEN)
        if ahora() >= limite:
            decir(f"La lista HLS del origen sigue con {_segmentos(n)} tras {ahora() - inicio:.0f} s de espera: "
                  f"el hub empieza a leerla sin el margen de {hacen_falta}")
            return
        if n != dicho:
            decir(f"{ESPERA} del origen tenga {hacen_falta} segmentos para leerla con margen: tiene {n}")
            dicho = n
        dormir(min(1.0, max(0.25, dura / 4)))
        texto, _ = abrir(url)
    if dicho is not None:
        decir(f"La lista HLS del origen ya tiene {_segmentos(n)}: el hub empieza a leerla")


def _decir(texto):
    print(texto, file=sys.stderr, flush=True)


def main(argv):
    if len(argv) < 4:
        print("uso: espera_hls.py <variante> <segmentos> <comando...>", file=sys.stderr)
        return 2
    cmd = argv[3:]
    try:
        esperar(cmd[cmd.index("-i") + 1], int(argv[1]), int(argv[2]), _decir)
    except Exception:                       # no se pudo mirar la lista: que lo intente ffmpeg, que dirá por qué
        pass
    try:
        os.execvp(cmd[0], cmd)
    except OSError as err:
        _decir(f"No se pudo lanzar: {err.strerror or err}")
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv))
