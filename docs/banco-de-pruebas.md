# Banco de pruebas del hub de señales

El banco es una instalación completa del hub (MediaMTX, supervisor y panel) dentro de una carpeta,
con un Recorder de mentira que da los tres roles. Sirve para probar de extremo a extremo, con
señales de verdad viajando por SRT, RTMP, HLS y WebRTC, sin tocar ninguna estación.

Lo que se comprueba aquí es de **banco**: la cámara y los receptores son programas (ffmpeg y un
Chromium sin ventana). Que una ruta pase en el banco no la convierte en «verificada con equipos
reales»; eso se anota aparte (ver [validacion.md](validacion.md)).

## Qué hace falta

- Linux con `ffmpeg` y `ffprobe` compilados con libsrt.
- Python 3.10 o posterior con lo de `requirements.lock`:
  `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock`
- Para WebRTC y para ver el HLS en un navegador: Node con Playwright y su Chromium
  (`npm i playwright && npx playwright install chromium`, o el runtime del compositor de una
  estación, ver más abajo). Sin navegador la prueba corre igual y deja WebRTC «sin comprobar».
- Salida a internet la primera vez: descarga MediaMTX v1.21.1 y comprueba su suma SHA-256.

## Arrancarlo

```
PYTHON=.venv/bin/python tests/banco/banco.sh arrancar     # MediaMTX, Recorder de mentira, supervisor y panel
tests/banco/banco.sh estado
tests/banco/banco.sh parar
```

Todo queda en `.banco/` (git lo ignora): la base de datos, los registros y el binario de MediaMTX.
El panel escucha solo en `127.0.0.1:8095` y se entra con la cookie `msr_sesion` de valor `admin`,
`manager` u `operador`:

```
curl -H 'Cookie: msr_sesion=admin' http://127.0.0.1:8095/api/matriz
```

Otras dos órdenes existen para probar reinicios con rutas activas:

- `tests/banco/banco.sh supervisor` reinicia el supervisor y el panel y deja en marcha MediaMTX y
  los ffmpeg de las rutas, que es lo que hace un despliegue normal. Ninguna señal debe cortarse.
- `tests/banco/banco.sh reiniciar` lo mata todo y lo vuelve a levantar, como un reinicio del equipo.

## La prueba de aceptación

`scripts/aceptacion.py` maneja el hub por la API del panel con una sesión de administrador y hace
la prueba principal dos veces: con una entrada RTMP y salidas SRT, HLS y WebRTC, y con una entrada
SRT y salidas RTMP, HLS y WebRTC. En cada una:

1. Crea la fuente y los tres destinos, y publica una señal de prueba (H.264 1280×720 a 30 fps con
   un tono de 1 kHz en AAC).
2. Pide el plan de cada ruta antes de activarla: qué se copia, qué se convierte y por qué.
3. Activa las tres rutas a la vez.
4. Comprueba audio y vídeo en las tres salidas: decodifica lo que recibió el receptor SRT o RTMP,
   decodifica el HLS con ffmpeg y mide en el navegador lo que reproducen de verdad la página HLS y
   la página WebRTC (tiempo reproducido, fotogramas decodificados, audio con energía).
5. Corta el receptor sin avisar durante 60 s, comprueba que HLS y WebRTC siguen reproduciendo y que
   el hub enseña la ruta cortada como «reconectando», devuelve el receptor y mide cuánto tarda la
   salida en recuperarse sola.
6. Apunta la carga del equipo y de cada proceso del hub con las tres salidas en marcha.
7. Busca en los registros del hub y en lo que devuelve la API las claves que usó.
8. Si se le pide, reinicia el hub con las rutas activas y comprueba que vuelven sin duplicados.
9. Anota en el hub las combinaciones que pasaron como «verificadas en el banco de pruebas».

En el banco:

```
MSXS_SESION=admin python3 scripts/aceptacion.py --base .banco/base --registros .banco \
    --reinicio-suave "tests/banco/banco.sh supervisor" --reinicio "tests/banco/banco.sh reiniciar"
```

Tarda unos siete minutos. Deja en `aceptacion-<fecha>/` el `informe.md` para leer y `datos.json`
con todas las medidas. Sale con 0 si todo pasa, 1 si algo falla y 3 si algo quedó sin comprobar.
El último informe del banco está en [aceptacion-banco.md](aceptacion-banco.md).

Opciones útiles: `--pruebas rtmp` o `--pruebas srt` para una sola, `--corte 20` para un corte más
corto mientras se depura, `--fotogramas-b` para que la cámara de pruebas emita con fotogramas B
(obliga a convertir el vídeo para WebRTC, que es la ruta que más carga), `--sin-navegador`,
`--no-anotar` y `--conservar`. `python3 scripts/aceptacion.py -h` las lista todas.

## En una estación, sin tocar lo que está en producción

El banco cabe al lado del Streamer en marcha si se le desplazan los puertos. Con `DESPLAZA=100` usa
8195 (panel), 10097 (API de MediaMTX), 8990/UDP (SRT), 8988 (HLS), 2045 (RTMP) y 8989 con 8289/UDP
(WebRTC), y su propia carpeta. No comparte nada con la instalación de `/home/mediasat/streamer`: ni
la base de datos, ni MediaMTX, ni los servicios de systemd.

Es la forma de medir la carga en la HP Z8 antes del relevo. En la estación, como `mediasat`, desde
una copia del repositorio:

```
export MSXS_BANCO=/home/mediasat/hub-banco DESPLAZA=100
export PYTHON=/home/mediasat/streamer/venv/bin/python        # el entorno del Streamer ya tiene fastapi y uvicorn
tests/banco/banco.sh arrancar

# el Node y el Chromium del compositor sirven de navegador para la prueba
export NODE=/home/mediasat/streamer/runtime/node/bin/node
export NODE_PATH=/home/mediasat/streamer/runtime/node_modules
export PLAYWRIGHT_BROWSERS_PATH=/home/mediasat/streamer/runtime/browsers

MSXS_SESION=admin python3 scripts/aceptacion.py --hub http://127.0.0.1:8195 \
    --base $MSXS_BANCO/base --registros $MSXS_BANCO --lugar "HP Z8 (EZC80), banco al lado de producción" \
    --puerto-srt 9311 --puerto-rtmp 1960 \
    --reinicio-suave "tests/banco/banco.sh supervisor" --reinicio "tests/banco/banco.sh reiniciar"

tests/banco/banco.sh parar
```

Antes de arrancar conviene comprobar que esos puertos están libres en la estación
(`ss -Hltnu | grep -E ':(8195|10097|8990|8988|2045|8989|8289|9311|1960)\b'` no debe dar nada). La
señal de prueba y los reproductores corren en la misma máquina, así que el uso total de CPU que
sale en el informe los incluye; las filas de cada proceso del hub y la de MediaMTX no.

Si el Chromium del compositor no decodificara H.264, las comprobaciones del navegador fallarían
diciéndolo; `CHROMIUM=/usr/bin/google-chrome` hace que la prueba use otro navegador.

## Lo que el banco no puede decir

- La carga de la HP Z8 con sus señales de verdad (1080i, 1080p59,94, varios audios) y con NVENC:
  el banco de desarrollo no tiene tarjeta y codifica por CPU.
- El comportamiento con la red de por medio: pérdidas, latencia, cortafuegos, NAT y, en WebRTC,
  servidores STUN o TURN. En el banco todo viaja por `127.0.0.1`.
- La compatibilidad con cada codificador, receptor o plataforma concretos (un decodificador SRT de
  un fabricante, YouTube, un reproductor de televisión).
- NDI y Zixi: necesitan su SDK o su componente con licencia y un equipo real delante.

## Lo que quedó anotado al construir el motor

Con MediaMTX 1.21.1 en el banco, y recogido en el código donde se usa:

- Qué códecs acepta cada salida tal como llegan (`streamer/protocolos.py`): por ejemplo, WebRTC no
  acepta H.264 con fotogramas B (MediaMTX cierra la sesión) ni audio AAC, que se convierte a Opus.
- Encender por la API el servidor RTMP o el WebRTC de MediaMTX no corta las conexiones SRT en
  curso. Si el puerto pedido lo tiene otro programa, MediaMTX se cierra entero al aplicarlo: por eso
  el hub comprueba antes que el puerto esté libre y, si no lo está, no lo pide y dice por qué.
- MediaMTX vigila su `mediamtx.yml`: si el archivo cambia con el servicio en marcha, recarga y
  corta todas las señales hasta que el supervisor vuelve a declarar los paths. Por eso
  `desplegar.sh` solo lo toca con `--mediamtx`.
- Reiniciar el supervisor y el panel no corta ninguna salida: los ffmpeg siguen y el supervisor los
  vuelve a adoptar. La prueba de aceptación lo mide.
