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

## Las demás formas de conexión

`scripts/combinaciones.py` complementa a la prueba de aceptación con lo que esta no toca. Maneja el
hub igual, por la API del panel, y hace pasar señal de verdad por cada forma de conexión. Donde
hace falta «otro equipo» que sirva o reciba la señal (otro hub, un servidor RTMP, una lista HLS, un
servidor WHIP, una plataforma RTMPS) lo hace el propio hub por otra de sus salidas, o un segundo
MediaMTX que el guion levanta para la ocasión. Son once casos:

| Caso | Qué comprueba |
|---|---|
| `entregas` | Lo que el hub entrega para que otro equipo lo abra: SRT con stream ID y passphrase, SRT con puerto propio y RTMP. Sin nadie conectado debe decir «esperando receptor», y con alguien leyendo, «enviando». |
| `llamadas` | El hub va a buscar la fuente: llama por SRT a un codificador a la escucha y a otro hub (con stream ID y passphrase), lee de un servidor RTMP y lee una lista HLS. Y un codificador SRT que llama a un puerto propio del hub, con passphrase. |
| `rtmps` | RTMPS en los cuatro sentidos: un codificador publica cifrado en el hub, un cliente lee cifrado del hub, el hub publica en una plataforma y el hub lee de un servidor. |
| `webrtc` | Un navegador publica por WHIP (H.264 y Opus). Sale por SRT en copia y por RTMP con el audio convertido a AAC, el hub sigue la subida de resolución del navegador, MediaMTX reenvía la señal por WHIP a otro servidor y otro navegador la reproduce por WHEP. |
| `perfiles` | Un destino con su resolución y su bitrate, y dos destinos con el mismo perfil compartiendo una sola conversión. |
| `fotogramas` | H.264 con fotogramas B: en copia hacia HLS y convertido hacia WebRTC, que no los admite. |
| `audio` | Una fuente con dos pistas de audio: un destino con todas y otros solo con la segunda. Las pistas se distinguen por su volumen. |
| `fuente` | Corte de la fuente durante 20 s con tres rutas activas (copia, conversión y HLS): las tres pasan a «esperando fuente», no queda ningún proceso dando vueltas y vuelven solas, sin duplicados. |
| `reconexion` | Política de reconexión con tope de intentos: «reconectando» mientras lo intenta, «error» al agotarlos, y nada más hasta que alguien pulsa «Reintentar». |
| `cambio` | Cambiar la fuente de un destino al aire: el hub dice cuál está al aire y pide confirmación, un operador no puede hacerlo y el cambio queda en el registro con quién lo hizo. |
| `matriz` | Las 42 combinaciones entre SRT, RTMP, HLS y WebRTC: cada una de las seis formas de entrada conectada a la vez a las siete formas de salida, con audio y vídeo comprobados en cada una. Antes de activar se lee el plan de las siete. |

En el banco:

```
MSXS_SESION=admin python3 scripts/combinaciones.py --registros .banco --sesion-operador operador
```

Tarda unos quince minutos. Deja en `combinaciones-<fecha>/` su `informe.md` y su `datos.json`, anota
en el hub como «verificadas en el banco de pruebas» las combinaciones por las que pasó señal, y
sale con 0, 1 o 3 igual que la prueba de aceptación. `--casos rtmps,fuente` pasa solo algunos. El
último informe del banco está en [combinaciones-banco.md](combinaciones-banco.md).

Para el caso `rtmps` hace falta `openssl`: el guion crea con él el certificado de su plataforma de
pruebas, y `banco.sh` el del propio banco (firmado por él mismo), con el que el hub atiende RTMPS en
el puerto 1946. Sin certificado en el hub, esa parte queda «sin comprobar».

## En una estación, sin tocar lo que está en producción

El banco cabe al lado del Streamer en marcha si se le desplazan los puertos. Con `DESPLAZA=100` usa
8195 (panel), 10097 (API de MediaMTX), 8990/UDP (SRT), 8988 (HLS), 2045 (RTMP), 2046 (RTMPS) y 8989
con 8289/UDP (WebRTC), y su propia carpeta. No comparte nada con la instalación de `/home/mediasat/streamer`: ni
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
(`ss -Hltnu | grep -E ':(8195|10097|8990|8988|2045|2046|8989|8289|9311|1960)\b'` no debe dar nada). La
señal de prueba y los reproductores corren en la misma máquina, así que el uso total de CPU que
sale en el informe los incluye; las filas de cada proceso del hub y la de MediaMTX no.

Si el Chromium del compositor no decodificara H.264, las comprobaciones del navegador fallarían
diciéndolo; `CHROMIUM=/usr/bin/google-chrome` hace que la prueba use otro navegador.

Las demás formas de conexión se pasan igual, antes de parar el banco:

```
MSXS_SESION=admin python3 scripts/combinaciones.py --hub http://127.0.0.1:8195 \
    --registros $MSXS_BANCO --sesion-operador operador --lugar "HP Z8 (EZC80), banco al lado de producción" \
    --puerto-srt 9331 --puerto-rtmp 1970
```

Los receptores y cámaras de esa prueba toman puertos libres a partir de los que se le indican.

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
- El servidor WebRTC de MediaMTX tarda entre 0,3 y 2 s en atender desde que se crea la primera
  fuente o destino WebRTC. Mientras tanto «Probar la conexión» dice que se está encendiendo.
- Un navegador que publica por WHIP empieza en 320×180 y sube por escalones hasta 1280×720 en unos
  17 s, sin volver a conectar. El hub vuelve a inspeccionar la fuente cuando el tamaño lleva 10 s
  quieto, y toda conversión de vídeo fija el tamaño de salida para que el codificador no reciba
  una imagen que cambia.
- ffmpeg tardaba 5,4 s en abrir una señal del propio hub porque espera por si hay más programas en
  el MPEG-TS. Lo que el hub lee de sí mismo trae siempre uno, y con `-scan_all_pmts 0` son 1,3 s:
  es lo que tarda menos una ruta en arrancar y cada reintento hacia un receptor caído.
- Cuando un codificador se cae y vuelve, el hub tiene que volver a mirar la señal (unos 5 s). Si
  para MediaMTX trae lo mismo que antes (códecs, tamaño, canales), el hub conserva lo que sabía
  mientras lo confirma. Con eso una ruta con conversión volvió a entregar a los 6 s de regresar el
  codificador, en vez de a los 12 s.
- ffmpeg cifra hacia un servidor RTMPS pero no comprueba su certificado. «Probar la conexión» sí lo
  comprueba y avisa si no es válido, sin dar el destino por caído.
- RTMPS en MediaMTX: con un certificado que no puede cargar, MediaMTX se cierra entero (el hub lo
  comprueba antes de pedírselo). Encenderlo o apagarlo reinicia el servidor RTMP y corta a quien
  publica o lee por RTMP, pero no a lo demás: con una fuente SRT entregada por SRT, HLS y WebRTC no
  hubo ninguna parada. Cambiarle el puerto reinicia solo RTMPS. Una vez en marcha, recarga solo el
  certificado cuando cambian sus archivos y, si lo nuevo no vale, sigue con el anterior. Por eso el
  hub no apaga un RTMPS en marcha cuando lo que se le pide deja de valer: lo deja como está y avisa.
- MediaMTX 1.21 trae un servidor nuevo, MoQ, que viene encendido: abre los puertos 8892 y 8893 y
  deja un certificado suyo (`auto.crt` y `auto.key`) en la carpeta desde la que se lanza.
  `mediamtx.yml` lo apaga con `moq: no`, y también el MediaMTX que hace de plataforma RTMPS en las
  pruebas.
- Una fuente HLS que el hub va a buscar salía a golpes: ffmpeg empezaba por el último segmento de
  la lista, sin colchón, y cada segmento le llegaba tarde y lo soltaba de una vez. Midiendo lo que
  sale cada 100 ms, el 35 % del tiempo no salía nada, con parones de 0,9 s, y un navegador veía esa
  fuente por WebRTC a 24 fps. Empezando tres segmentos por detrás del directo, como hace un
  reproductor, sale continuo. Cuesta dos segmentos más de retardo en una fuente que ya trae varios.
- Eso no bastaba con una lista recién nacida, que no tiene tres segmentos que dejar por delante.
  Pasa cuando el origen arranca después que el hub y con los servidores que crean la lista cuando
  alguien la pide, como MediaMTX. MediaMTX, además, la sirve desde el principio con sus siete
  entradas, rellenas por delante con huecos (`#EXT-X-GAP`) que ffmpeg no conoce: los pide, recibe un
  404 y se los salta. El margen que falta al empezar ya no se recupera: a un lector del hub los
  fotogramas de esa fuente le llegaban con parones de hasta 1,6 s (los de una cámara SRT, con 80 ms
  como mucho) y un navegador la veía por WebRTC a 26 fps. Ahora el proceso de entrada mira la lista
  antes de leerla (`streamer/espera_hls.py`): si al final tiene menos de tres segmentos de verdad,
  espera a que los tenga, con un tope, y la fuente dice en su estado qué está esperando. Con eso los
  fotogramas llegan cada 30 ms, con 51 ms como mucho. En el banco son unos 4 s más hasta que la
  fuente empieza a recibirse, solo cuando la lista es nueva.
- En lo que un cliente lee del hub por RTMP, el audio empieza hasta 2 s antes que el vídeo: MediaMTX
  manda el audio enseguida y el vídeo desde el siguiente fotograma clave. Las pruebas miden los
  fotogramas sobre lo que dura el vídeo, no sobre toda la captura.
- El receptor de las pruebas también es un ffmpeg y, por lo mismo que el hub, tardaba 5 s en
  empezar a guardar lo que le llegaba por SRT. La prueba de aceptación apuntaba ese retraso suyo
  como tiempo de recuperación de la salida: 7 s cuando eran 1,4 s. Los receptores y lectores de
  las pruebas abren ahora con `-scan_all_pmts 0`, y el momento en que el hub marca «enviando» se
  mide aparte del momento en que vuelve la señal.
- El reenvío WHIP de MediaMTX hacia otro MediaMTX funciona, y lo que entra por WHIP se reproduce
  por WHEP sin convertir nada.
