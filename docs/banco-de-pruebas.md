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
- Para algunos casos de la segunda prueba, `openssl` (certificados de usar y tirar) y `coturn` (el
  servidor TURN de pruebas). Sin ellos esos casos quedan «sin comprobar» y lo demás corre igual.
  Si el equipo tiene Caddy, el caso `https` lo pone delante en vez de su proxy de pruebas.

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

### La misma prueba, pulsando en el panel

`scripts/aceptacion_panel.js` hace la primera mitad (entrada RTMP y salidas SRT, HLS y WebRTC) sin
tocar la API: abre el panel en un Chromium sin ventana con una sesión de administrador y pulsa lo
que pulsaría un operador. Da de alta la fuente en Fuentes y toma de ahí la dirección para el
codificador, da de alta los tres destinos en Destinos, conecta las tres rutas en la Matriz de rutas
leyendo lo que el panel dice que copiará y convertirá, y sigue en Monitoreo el corte del receptor
SRT y su vuelta. Lo que comprueba fuera del panel es lo que de verdad llega: el vídeo y el audio que
guarda el receptor SRT y lo que reproducen las páginas HLS y WebRTC en el navegador.

```
MSXS_SESION=admin node scripts/aceptacion_panel.js --capturas capturas-panel
```

Tarda algo menos de dos minutos con el corte de 60 s (`--corte 20` lo acorta). Con `--capturas`
deja una captura del panel en cada paso. Borra lo que crea, cuyo nombre empieza por «Aceptación del
panel», también si se la interrumpe; y si una pasada anterior se quedó a medias (un corte de luz),
al empezar borra lo que dejó. No mide la carga ni anota validaciones: eso lo hace
`scripts/aceptacion.py`. Conviene pasarla con el hub sin otras rutas en marcha: en un equipo
pequeño, el navegador sin ventana se queda sin procesador si el hub ya está cargado y la prueba
falla sin que el hub tenga la culpa.

## Las demás formas de conexión

`scripts/combinaciones.py` complementa a la prueba de aceptación con lo que esta no toca. Maneja el
hub igual, por la API del panel, y hace pasar señal de verdad por cada forma de conexión. Donde
hace falta «otro equipo» que sirva o reciba la señal (otro hub, un servidor RTMP, una lista HLS, un
servidor WHIP, una plataforma RTMPS) lo hace el propio hub por otra de sus salidas, o un segundo
MediaMTX que el guion levanta para la ocasión. Son trece casos:

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
| `https` | HLS y WebRTC con HTTPS por delante: un proxy con certificado en el mismo equipo (un Caddy de verdad si el equipo lo tiene; si no, `tests/banco/https_delante.py` haciendo de Caddy) y el hub entregando esas direcciones. Un lector y un navegador reproducen, otro navegador publica por WHIP, quien entra por el proxy sin token recibe un 401, también si dice venir del propio equipo, y «Probar la conexión» lo confirma. Después, con dos proxies mal puestos (uno que no anota de dónde viene cada petición y otro que deja pasar lo que la petición diga de sí misma), se entra sin clave y «Probar la conexión» lo dice. |
| `ice` | WebRTC con una dirección anunciada y un servidor TURN (un coturn que el guion levanta). Cambiar esos ajustes con alguien mirando exige decir qué se corta, quien miraba vuelve solo y la salida SRT de la misma fuente no se corta. Un navegador que solo puede salir por el TURN reproduce y publica. |

En el banco:

```
MSXS_SESION=admin python3 scripts/combinaciones.py --registros .banco --sesion-operador operador
```

Tarda unos diecisiete minutos. Deja en `combinaciones-<fecha>/` su `informe.md` y su `datos.json`,
anota en el hub como «verificadas en el banco de pruebas» las combinaciones por las que pasó señal,
y sale con 0, 1 o 3 igual que la prueba de aceptación. `--casos rtmps,fuente` pasa solo algunos. El
último informe del banco está en [combinaciones-banco.md](combinaciones-banco.md).

Para el caso `rtmps` hace falta `openssl`: el guion crea con él el certificado de su plataforma de
pruebas, y `banco.sh` el del propio banco (firmado por él mismo), con el que el hub atiende RTMPS en
el puerto 1946. Sin certificado en el hub, esa parte queda «sin comprobar».

El caso `https` necesita también `openssl`, para el certificado de sus proxies de pruebas, y que el
equipo tenga una dirección de red además de `127.0.0.1`: los clientes de la prueba entran por ella
para que MediaMTX los vea llegar de fuera. Si el equipo tiene Caddy, el proxy principal del caso es
un Caddy que el guion lanza aparte del que haya en el sistema: con su configuración, sus puertos y
su almacén dentro de la carpeta de resultados, sin puerto de administración y sin instalar su
certificado raíz en ningún sitio. El informe dice con qué versión se hizo. Sin Caddy, o si el que
hay no arranca con esa configuración, hace su papel el proxy de pruebas y el informe lo dice. Los
dos proxies mal puestos son siempre el de pruebas, con `--sin-origen` y con `--solo-real-ip`. Una
estación con el Recorder ya tiene Caddy, y no hay que tocarlo. En otro equipo se instala con
`sudo apt-get install caddy`; en Ubuntu el paquete deja además un servicio habilitado en el puerto
80, que la prueba no usa y que en un equipo de pruebas se apaga con
`sudo systemctl disable --now caddy`.

El caso `ice` necesita `coturn` (`sudo apt-get install coturn` deja el programa `turnserver`; el
guion lanza el suyo, en `127.0.0.1` y con puertos propios). En Ubuntu el paquete deja además un
servicio habilitado en el puerto 3478, que la prueba no usa: `sudo systemctl disable --now coturn`
lo apaga. Los dos casos cambian ajustes del hub (las direcciones públicas y los de WebRTC) y los
dejan como estaban; en un hub que ya tiene los suyos no los tocan y quedan «sin comprobar».

## En una estación, sin tocar lo que está en producción

El banco cabe al lado del Streamer en marcha si se le desplazan los puertos. Con `DESPLAZA=100` usa
8195 (panel), 10097 (API de MediaMTX), 8990/UDP (SRT), 8988 (HLS), 2045 (RTMP), 2046 (RTMPS) y 8989
con 8289/UDP (WebRTC), y su propia carpeta. No comparte nada con la instalación de `/home/mediasat/streamer`: ni
la base de datos, ni MediaMTX, ni los servicios de systemd.

Es la forma de medir la carga en la HP Z8 antes del relevo, y `scripts/medir-carga.sh` lo hace de
una vez (la pasada del 19 de noviembre de 2026 está en [aceptacion-z8.md](aceptacion-z8.md)). En la
estación, como `mediasat`, desde una copia del repositorio (nunca desde la carpeta
del Streamer):

```
scripts/medir-carga.sh --solo-mirar      # no arranca nada: dice cómo está la estación y si se puede
scripts/medir-carga.sh --lugar "HP Z8 (EZC80), banco al lado de producción"
```

El guion tarda unos 7 minutos y hace esto, en orden:

1. Anota la estación (procesador, memoria, tarjeta gráfica, carga) y cómo está el Streamer en
   producción: su versión, el estado de sus tres servicios y los identificadores de sus procesos de
   rutas. No escribe sus líneas de órdenes, que llevan claves.
2. Se niega a seguir si ya hay un banco en marcha, si alguno de los puertos del banco está ocupado,
   si quedan menos de 2 GB libres o si la carga media del último minuto pasa del 60 % de los
   núcleos (`CARGA_MAXIMA` lo cambia).
3. Arranca el banco en `/home/mediasat/hub-banco` con lo que la estación ya tiene: el Python y el
   MediaMTX del Streamer (una copia del binario, si es de la misma versión), el mismo ffmpeg, y el
   Node y el Chromium del compositor como navegador de la prueba. No instala ni descarga nada, salvo
   MediaMTX si la estación tuviera otra versión.
4. Pasa la prueba de aceptación completa con señales de prueba, midiendo la carga en cada fase.
5. Para el banco, comprueba que no queda ninguno de sus procesos ni de sus puertos, y vuelve a
   mirar el Streamer en producción: los servicios y los procesos tienen que ser los mismos que
   antes. Lo deja escrito en `produccion.txt`, junto al `informe.md` y al `datos.json` de la prueba.

Sale con lo que salga la prueba (0 pasa, 1 falla, 3 algo quedó sin comprobar), con 2 si no llegó a
arrancar y con 130 si se interrumpió. Para pararlo antes de tiempo basta un `kill` al guion: cierra
la prueba (su cámara, sus receptores y su navegador, y lo que creó en el hub), para el banco y lo
cuenta; tarda unos 15 segundos.

La señal de prueba y los reproductores corren en la misma máquina, así que el uso total de CPU que
sale en el informe los incluye; las filas de cada proceso del hub y la de MediaMTX no. La prueba no
usa la tarjeta gráfica: sus rutas van en copia, y solo se convierte el audio para WebRTC.

Si el Chromium del compositor no decodificara H.264, el guion lo dice al mirar y pasa la prueba sin
navegador (esas comprobaciones quedan «sin comprobar»); `CHROMIUM=/usr/bin/google-chrome` hace que
use otro navegador.

Por dentro son los pasos de siempre, con los puertos desplazados, y se pueden dar a mano:

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

Las demás formas de conexión se pasan igual, antes de parar el banco. Esta prueba sí convierte
vídeo (con la tarjeta gráfica, si la hay) y cambia y repone ajustes del banco, así que conviene
pasarla con la estación tranquila:

```
MSXS_SESION=admin python3 scripts/combinaciones.py --hub http://127.0.0.1:8195 \
    --registros $MSXS_BANCO --sesion-operador operador --lugar "HP Z8 (EZC80), banco al lado de producción" \
    --puerto-srt 9331 --puerto-rtmp 1970
```

Los receptores y cámaras de esa prueba toman puertos libres a partir de los que se le indican.

## El respaldo a Drive

`scripts/respaldar.sh` no es parte del hub, pero lleva sus claves a Google Drive (ver
[instalacion.md](instalacion.md#el-respaldo-a-google-drive-y-sus-claves)). Su prueba no necesita el
banco ni una estación:

```
sudo tests/banco/respaldo.sh
```

Ejecuta tal cual el guion de respaldo, el que sube a Drive y el instalador, dentro de un espacio de
montaje propio en el que `/home` es un disco en memoria vacío. Ahí monta una estación de mentira con
una clave reconocible en `streamer.db`, un Drive que es una carpeta y un `ssh` que solo apunta lo
que se le pide. Nada de eso queda al terminar y el `/home` de verdad no se toca. Necesita Linux,
root, `unshare`, `openssl` y `python3`, y tarda unos 15 s.

Comprueba que a Drive solo llega el archivo cifrado, en el que no se lee ni la clave ni el nombre
de la base, y que se abre con la orden de `instalacion.md` y da el mismo archivo que la copia de la
estación; que `instalar-respaldos.sh` crea la contraseña antes del primer respaldo y que repetirlo
no la cambia; y que sin contraseña, con una vacía o corta, o si el cifrado falla, el respaldo
termina con error y a Drive no sube nada, mientras la copia de la estación se hace igual. El guion
que sube a Drive rechaza además cualquier archivo sin cifrar. Si el Python del equipo tiene la
biblioteca `cryptography`, abre también el archivo sin `openssl`. El último caso lo lanza como el
temporizador semanal: la orden de `msx-respaldo.service`, con el entorno vacío y sin terminal.

No dice nada del Drive de verdad (la cuenta, sus permisos, sus límites) ni de la estación. Eso se
comprobó aparte en la EZC80, al instalarlo: ver
[instalacion.md](instalacion.md#el-respaldo-a-google-drive-y-sus-claves).

## Lo que el banco no puede decir

- La carga de la HP Z8 con sus señales de verdad (1080i, 1080p59,94, varios audios) y con NVENC:
  el banco de desarrollo no tiene tarjeta y codifica por CPU. En la estación está medida la prueba
  de aceptación, que no convierte vídeo ([aceptacion-z8.md](aceptacion-z8.md)).
- El comportamiento con la red de por medio: pérdidas, latencia, cortafuegos y NAT. En el banco
  todo viaja dentro del mismo equipo. El proxy HTTPS y el servidor TURN de las pruebas también
  están en él: comprueban que el hub habla bien con ellos y que no deja entrar sin clave, no que
  WebRTC atraviese un NAT de verdad ni que el proxy, los nombres y el certificado de una estación
  estén bien puestos.
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
  mientras lo confirma. Con eso, al regresar el codificador la señal vuelve a los receptores a los
  3 s por una ruta en copia y entre los 6 y los 8 s por una ruta con conversión, que antes eran
  12 s. El hub la marca como «enviando» unos 4 s después, cuando tiene dos medidas de lo que sale.
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
- Un proxy inverso en el mismo equipo llega a MediaMTX desde `127.0.0.1`, que para MediaMTX es el
  propio hub. Por un proxy así, sin credenciales, se leía por HLS cualquier señal y se miraba y se
  publicaba por WebRTC, y la respuesta traía además la credencial del servidor TURN. Con
  `hlsTrustedProxies` y `webrtcTrustedProxies` puestos a `127.0.0.1` y `::1`, MediaMTX juzga lo que
  le llega de ahí por la dirección anotada en `X-Forwarded-For`: de fuera y sin credenciales, 401;
  sin la cabecera (el panel, los ffmpeg del hub), local como antes; y quien llama directamente
  desde la red no puede hacerse pasar por local poniéndola. El supervisor lo pide al arrancar
  (`streamer/mtx.py`). Un proxy que no anota esa cabecera lo deja todo abierto igual: por uno así,
  un navegador sin token vio una salida WebRTC protegida (30 fps, con audio) y publicó en una fuente
  WHIP protegida. Por uno que la anota, los dos intentos recibieron un 401.
- De lo que le llega de un proxy de confianza, MediaMTX toma como origen la última dirección de
  `X-Forwarded-For` que no sea de un proxy de confianza (varias líneas de esa cabecera valen como
  una sola lista). Si la cabecera no viene, o lo último que trae no es una dirección, toma la de
  `X-Real-IP`, y si tampoco viene, la del propio proxy, que es `127.0.0.1`. `Forwarded` no la mira.
  Por eso un proxy que anota solo `X-Real-IP` y deja pasar la `X-Forwarded-For` que traiga la
  petición responde 401 a quien pide sin más y abre a quien la manda con `127.0.0.1`: por uno así,
  un lector de ffmpeg sin clave leyó por HLS una fuente (239 fotogramas en 8 s, con audio) y un
  navegador sin token vio una salida WebRTC protegida (233 fotogramas en 7,8 s), que Monitoreo
  enseñaba como un receptor de `127.0.0.1`. Publicar así no se probó con señal: la petición WHIP
  pasa la puerta (406 a una oferta de pruebas, en vez de 401).
- Con un Caddy 2.6.2 de verdad delante, quien entra sin token recibe un 401 por HLS y por WebRTC,
  también mandando `X-Forwarded-For: 127.0.0.1`: Caddy descarta la que trae la petición y pone la
  de quien conecta. Con un nginx 1.24 de verdad: sin decirle nada entra cualquiera; con
  `proxy_set_header X-Forwarded-For $remote_addr;` o con `$proxy_add_x_forwarded_for`, 401 mande lo
  que mande la petición; con `X-Real-IP` sola, 401 a quien pide sin más y dentro quien manda
  `X-Forwarded-For: 127.0.0.1`. «Probar la conexión» acertó en todos esos montajes. Con nginx solo
  se miró quién entra: no se reprodujo nada a través de él.
- Pedírselo a un MediaMTX que no lo tiene reinicia sus servidores HLS y WebRTC, una vez: un
  navegador que miraba por HLS estuvo unos 10 s sin imagen, un lector HLS de ffmpeg se cortó y un
  lector SRT no notó nada (35 s seguidos, con 33 ms como mayor hueco). Después MediaMTX devuelve las
  listas tal como el hub las pide y no se vuelven a tocar.
- A quien pide sin credenciales desde fuera la página de una señal declarada, MediaMTX responde 401,
  por HLS y por WebRTC; a este equipo le da la página (200), y pedirla no arranca nada. Para una
  señal que no conoce responde 500 a cualquiera. Es lo que usa «Probar la conexión» para ver si por
  la dirección pública se entra sin clave: pide la página de una fuente y, si en vez de un 401
  recibe la misma página que MediaMTX le da a él, lo dice. La pide dos veces, una sin más y otra
  con `X-Forwarded-For: 127.0.0.1`. La lista (`index.m3u8`) no sirve para esto: MediaMTX responde
  primero con una redirección (302) a cualquiera, y el 401 o la lista llegan al seguirla.
- Cambiar las direcciones que anuncia WebRTC o sus servidores ICE reinicia el servidor WebRTC de
  MediaMTX. Quien miraba se queda sin imagen un segundo después; su página da la conexión por
  cerrada a los 16 s y vuelve a conectar sola: entre 18 y 20 s sin imagen. La salida SRT de la misma
  fuente no se corta. Reiniciar el supervisor con esos ajustes puestos no corta nada.
- Con un servidor TURN (coturn), un navegador obligado a salir solo por él reproduce la salida
  WebRTC a 30 fps con audio y publica por WHIP. MediaMTX entrega al navegador los servidores ICE con
  su credencial: si es una contraseña fija, llega tal cual a cada navegador autorizado; con secreto
  compartido (usuario `AUTH_SECRET`) MediaMTX fabrica un usuario temporal y el secreto no sale.
- Si el servidor TURN configurado no responde, WebRTC sigue funcionando por conexión directa, pero
  cada navegador nuevo tarda entre 9,5 y 10,8 s en empezar a ver, en vez de entre 1,5 y 2,5 s. Con
  un nombre que no existe, entre 2,5 y 3,5 s.
- La página HLS de MediaMTX empieza a reproducir en un navegador casi siempre en torno a medio
  segundo, y a veces tarda entre 4 y 7 s: 3 de 32 arranques medidos (2 de 16 sin proxy, 1 de 8 por
  Caddy, ninguno de 8 por el proxy de pruebas). En ese rato el reproductor ya ha saltado al directo
  y su reloj no avanza. Después de empezar no se paró en ninguno. Las dos pruebas dan la
  reproducción por empezada cuando el reproductor reproduce de verdad, y dicen aparte lo que tardó:
  antes contaban ese rato como una parada, y una pasada falló por eso.
- De una señal de ritmo irregular, como la que publica un navegador por WHIP, ffprobe no siempre
  declara bien los fotogramas por segundo: casi siempre 20, que es lo que hay, a veces nada, y una
  vez 60. Con 60, una conversión de vídeo de esa fuente pondría un fotograma clave cada 6 s en vez
  de cada 2 s. Los paquetes de vídeo que llegan en ese rato sí dan siempre 20 por segundo, así que
  el hub se queda con esa cuenta cuando ffprobe no declara nada o declara bastante más de lo que
  llega (`streamer/inspeccion.py`).
