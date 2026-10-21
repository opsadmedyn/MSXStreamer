# Media Syntaxis Streamer

Hub SRT de la suite Media Syntaxis: recibe feeds, los enruta y los distribuye a varios destinos.

**Fase 1:** entradas desde un canal de MSX Recorder del mismo equipo o por SRT pull; salidas SRT
(caller o listener), RTMP y HLS en copia (sin recodificar).

**Fase 2 (0.2.0):** composición por flujo: el vídeo reencuadrado en un lienzo 1920×1080 (preajustes
y posición libre, con guías de zonas seguras) y hasta dos capas HTML5 con transparencia, encima o
debajo del vídeo. Cada destino elige señal limpia (en copia) o compuesta.

## Cómo funciona

```
 MSX Recorder ──UDP local──┐                     ┌── ffmpeg ──▶ destino SRT
                           ├──▶ MediaMTX ──read──┼── ffmpeg ──▶ destino RTMP
 origen SRT (pull) ────────┘       │             └── HLS (solo con destino HLS activo)
                                   ▲
 panel web ──▶ SQLite ◀── supervisor (declara paths y mantiene los ffmpeg)
```

- **MediaMTX** (`msxs-mediamtx`) recibe las entradas y las reparte. Cada flujo es un path.
- **Supervisor** (`msxs-supervisor`) compara cada 2 s la base de datos con lo que corre: declara
  los paths en MediaMTX y mantiene un ffmpeg por salida SRT/RTMP, relanzándolo con espera
  creciente si cae. Los ffmpeg sobreviven a un reinicio del supervisor y este los vuelve a adoptar.
- **Panel** (`msxs-web`, puerto 8095) solo escribe en la base de datos. Reiniciarlo no corta nada.

### Entrada desde MSX Recorder

El grabador del canal reenvía por UDP local (`udp://127.0.0.1:<puerto>`) exactamente lo que graba.
Se activa por canal en `canales.json` del Recorder con `"reenvio_udp": 20001` (canal1 → 20001,
canal2 → 20002…) y reiniciando ese grabador. Es UDP a propósito: si el Streamer está parado, el
Recorder no se entera y sigue grabando igual.

### HLS bajo demanda

El HLS no se publica por defecto. Se activa por flujo añadiendo un destino **HLS** en el panel, y
la tabla muestra entonces la dirección `.m3u8` para copiarla. Mientras ese destino esté activo (y
el flujo también), `http://<equipo>:8888/<path>/` responde desde la red; al detenerlo o borrarlo,
deja de responder a las lecturas nuevas. La **vista previa** del panel no lo abre a la red (hasta
la 0.3.2 lo abría una hora, sin sesión): desde la 0.3.4 va a través del propio panel (ver abajo).

Las reglas viven en MediaMTX (`authMethod: internal`): desde el propio equipo se permite todo (las
salidas leen de ahí) y, desde la red, solo la lectura de esos flujos. El supervisor las ajusta por
la API en cada vuelta, en caliente y sin cortar a nadie, así que **no dependen del panel**: con el
panel parado, las salidas se siguen conectando. Como `hlsAlwaysRemux` está apagado, el HLS solo se
genera mientras alguien lo mira.

### Composición (fase 2)

Desde la 0.4.0, por cada flujo con la composición activa el supervisor mantiene dos procesos:

- **Compositor** (`componer.sh` → ffmpeg): reencuadra y superpone por CPU (el ffmpeg del Z8 no trae
  filtros CUDA), codifica con NVENC y publica en MediaMTX en `comp_<flujo>`. Cada capa entra como un
  archivo `capaN.yuv` (yuva420p 1920×1080, alfa recto) que relee en cada fotograma (`-f image2 -loop 1`),
  con `shortest=1` en todas las superposiciones.
- **Capas** (`compositor/capas.sh` → `capas.mjs`, solo si hay capas): un Chromium sin pantalla con una
  página por capa. Cada imagen nueva de la página (screencast PNG, alfa premultiplicado) se convierte
  en un hilo aparte (`compositor/convertir.mjs`: alfa recto, BT.709 rango limitado) y se escribe entera
  en un temporal que se renombra sobre `capaN.yuv`, en `/run/msxs/<flujo>/` (en memoria;
  `/dev/shm/msxs` si no existe). No se usa un ffmpeg en tubería para esto: retiene 2–3 imágenes antes
  de soltar la primera, y una página quieta no llegaría nunca al aire.

**El navegador ya no marca el ritmo del vídeo:** si una página se cuelga o el proceso de las capas
cae, la capa se queda en su última imagen y el vídeo sigue. **Sin corte:** mostrar u ocultar una capa,
cambiar su dirección o recargarla (botón «Recargar») lo aplica el proceso de las capas en caliente
(`capas.json`, que escribe el supervisor); la página nueva sustituye a la anterior al llegar su primera
imagen. **Con corte de 1–2 s** en la señal compuesta, de momento: encuadre, fondo, calidad, añadir o
quitar una capa, y pasarla de encima a debajo (irán en vivo con el ffmpeg con zmq, paso B).

Vigilancia de las páginas: latido cada 1 s y sustitución tras 5 s sin respuesta o si la página cae;
renovación diaria o al pasar de ~400 MB de memoria JS; si no carga, sigue la anterior y se reintenta
de 2 a 30 s. El supervisor relanza el proceso de las capas si su latido (`frame=` del `.prog`) no
avanza en 15 s. Las salidas limpias y el Recorder no dependen de nada de esto.

Runtime (Node y Chromium): `scripts/instalar-compositor.sh <estación>`, una vez. Capa de ejemplo:
`http://127.0.0.1:8095/static/capa-ejemplo.html` (L-bar con reloj, para el preajuste "L derecha").

### Salvaguardas de la composición (0.3.2)

Una composición nunca debe afectar a la señal limpia ni al Recorder:

- **Sin entrada, termina.** Si se corta la señal la composición termina en
  vez de emitir una imagen congelada, y el supervisor la relanza cuando la entrada vuelve. Si sigue
  viva pero su vídeo lleva 15 s sin avanzar (`frame=` del `.prog`), el supervisor también la relanza.
- **Como mucho 3 a la vez** (`composicion_max` en `config.json`, 3 si no está). El panel no deja
  activar una cuarta; si la base tiene más (un flujo que se inicia, una edición a mano), el
  supervisor arranca solo 3, primero las que ya corren, y las demás esperan con «Límite de…».
- **Prioridad baja:** el compositor va con nice 10, ionice best-effort 7 y `oom_score_adj` 500; el
  proceso de las capas (Chromium y sus conversores), con nice 15, ionice idle y `oom_score_adj` 800:
  si falta CPU, disco o memoria, ceden y caen antes que lo demás.
- **Datos dañados:** una composición que no se puede construir queda como «Composición no válida»
  y el supervisor sigue vigilando y relanzando las demás salidas.
- **Disco:** los `.prog` (más de 1 MB en disco) y los `.log` (más de 5 MB) se recortan también
  mientras el proceso corre.

## Despliegue

```
scripts/desplegar.sh z8 --instalar   # primera vez: MediaMTX, entorno, config y servicios
scripts/desplegar.sh z8              # actualizaciones: las salidas no se cortan
scripts/desplegar.sh z8 --mediamtx   # además, mediamtx.yml y unidades nuevas (corte de segundos)
```

Todo queda en `/home/mediasat/streamer`. Puertos: panel 8095, HLS 8888, SRT 8890 (UDP), API de
MediaMTX 9997 (solo local). La configuración de la máquina está en `config.json`
(ver `config/config.ejemplo.json`).

### 0.4.0 (rama de la fase 2, sin instalar)

Capas como "última imagen" (el contrato del diseño de la fase 2), con el ffmpeg de siempre:

- **El navegador ya no marca el ritmo del vídeo.** Una página colgada o un Chromium caído dejan la
  capa en su última imagen; el vídeo compuesto sigue.
- **Transparencias correctas:** se quita el alfa premultiplicado del screencast (antes las barras
  semitransparentes salían más oscuras) y el compositor ya no decodifica PNG en cada fotograma.
- **Sin corte:** mostrar, ocultar, cambiar la dirección o recargar una capa (botón nuevo «Recargar»).
- **Vigilancia de páginas:** latido, página caída, renovación diaria y por memoria; latido del
  proceso de las capas al supervisor.
- `shortest=1` en todas las superposiciones (con prueba en `tests/`), salida etiquetada BT.709.
- `desplegar.sh` crea `/run/msxs` con systemd-tmpfiles (solo crea la carpeta, no reinicia nada).

Al instalarla, las composiciones activas se relanzan una vez (cambia su comando); las salidas limpias
no se tocan. Pruebas: `python3 -m unittest discover tests`.

### 0.3.4

Incluye la 0.3.3 (vista previa por el panel y foto en el lienzo), que llegó a GitHub pero no se
instaló, y le añade el lienzo en directo.

- **Vista previa por el panel.** «Abrir» (vista previa), «Ver el resultado» (composición) y «ver»
  (destino HLS) ya no llevan a `http://<host_publico>:8888/…`, que por Tailscale no llega: van por
  el propio panel, `https://<dominio del Recorder>/streamer/hls/<path>/`, con la misma sesión
  (cualquier rol). El panel lo pide a MediaMTX en 127.0.0.1 y solo deja ver paths de flujos que
  existen o de su composición. Va en asíncrono, sin ocupar los hilos del resto del panel: como
  mucho 24 peticiones a la vez (las demás reciben un 503 y el reproductor reintenta). Las que van
  bien y esos 503 no se anotan en el registro de accesos; los rechazos (sin sesión, sin módulo,
  path que no es de un flujo), sí. La dirección `.m3u8` que se copia para reproductores externos
  sigue siendo la del 8888, igual que antes.
- **La vista previa ya no abre el 8888.** «Abrir» y «Ver el resultado» ya no publican el flujo (y
  su composición) una hora en `http://<equipo>:8888/…` sin sesión, ni hacen esperar a la pestaña
  nueva hasta que el supervisor lo aplica: solo comprueban que se puede ver y dan el enlace del
  panel. Por el 8888 solo se sirven los flujos con un destino HLS activo. La vista previa por el
  panel no caduca: dura mientras la pestaña esté abierta y la sesión valga.
- **Lienzo en directo.** En el paso Composición, el recuadro del vídeo muestra la entrada limpia en
  directo con las capas encima o debajo («Edición»), y el selector «Resultado» pasa el lienzo entero
  a la señal compuesta real (`comp_<flujo>`) si la composición está activa y en marcha (tras
  «Guardar composición» se reconecta sola). Es un vídeo por el panel (HLS de baja latencia) por cada
  pestaña con el lienzo a la vista: la tasa entera de la entrada (o de la compuesta) más unos
  200 kb/s de listas de LL-HLS (unas 20 peticiones/s), por Tailscale y por el panel. Se corta al
  salir del paso, del flujo o de la pestaña, a los 2 s de sacar el lienzo de la pantalla con el
  scroll y tras 10 min sin tocar la página («En pausa por inactividad»: sigue al pulsar en el lienzo
  o al mover el ratón). Si el navegador no deja arrancar el vídeo solo, no pide nada hasta que se
  pulsa en el lienzo. «Lectores» del flujo cuenta también estas sesiones HLS del panel (lienzo y
  vista previa), y MediaMTX las mantiene hasta 1 min después de cerrarlas.
- **Foto de la entrada en el lienzo.** Queda de reserva: se ve mientras arranca el vídeo, si falla o
  si el navegador no lo reproduce, renovada cada 5 s y con los mismos cortes que el vídeo; con el
  vídeo en marcha no se piden fotos. La saca el `ffmpeg` de `config.json`: un fotograma a 640 px,
  desentrelazado si llega entrelazado, con `nice 19`, un hilo y como mucho 8 s (después se mata).
  Guarda la última 4 s por flujo, saca una sola a la vez por flujo y como mucho 2 en total. Con el
  flujo parado o sin señal no lanza ffmpeg y queda el recuadro «VÍDEO». Cada foto es un lector SRT
  de 1 a 2 s en MediaMTX (tres líneas en su registro); en el del panel no se anotan las que van bien.

Se despliega con `scripts/desplegar.sh <estación>`, sin `--mediamtx`: copia `streamer/` y reinicia
solo `msxs-web` y `msxs-supervisor`. No cambian `supervisor.py`, `composicion.py`, `mtx.py`,
`componer.sh`, MediaMTX ni las unidades: las salidas y las composiciones siguen al aire y no se
relanzan, porque sus comandos de ffmpeg son los mismos. Caddy no cambia: `/streamer/hls/…` entra
por el mismo `handle_path` que el resto del panel. Una vista previa abierta antes del despliegue
sigue en el 8888 hasta su hora: el supervisor la quita entonces, como siempre.

Vuelta atrás: `git checkout v0.3.2 && scripts/desplegar.sh <estación>` (sin `--mediamtx`). No hay
cambios en la base ni en `config.json`.

### 0.3.2

La 0.3.1 desplegada (solo Tailscale, sin clave del panel) más los arreglos revisados: las
salvaguardas de la composición (arriba) y los del acceso (abajo). Se despliega entera con
`scripts/desplegar.sh <estación>`, sin `--mediamtx`: copia todo `streamer/` y reinicia solo
`msxs-supervisor` y `msxs-web`; MediaMTX sigue igual. No basta copiar `servidor.py` y reiniciar
`msxs-web`: `servidor.py` importa `supervisor.py`, que necesita `composicion.maximo` del nuevo
`composicion.py` (con el de la 0.3.1 el panel no arranca y `/streamer/` queda fuera), y las
salvaguardas del supervisor solo se aplican al reiniciarlo.

Desde la 0.3.2, el despliegue normal pone también la unidad `msxs-web` de la versión (antes solo lo
hacían `--instalar` y `--mediamtx`, que reinicia MediaMTX y corta las salidas): el panel escucha
solo en `127.0.0.1:8095`. Si en el Z8 seguía en `0.0.0.0`, desde ese momento `http://<IP local>:8095`
deja de responder y solo se entra por `https://<dominio del Recorder>/streamer/`. Al terminar, el
script comprueba con `ss` que el 8095 solo escucha en 127.0.0.1 y, si no, avisa con `!!` (a mano:
`ssh z8 "ss -Hltn 'sport = :8095'"` debe mostrar solo `127.0.0.1:8095`). También borra el
`static/login.html` de la clave del panel que quedó de la 0.3.0 (rsync no borra nada).

Vuelta atrás: `git checkout v0.3.1 && scripts/desplegar.sh <estación>` (sin `--mediamtx`). No hay
cambios en la base ni en la unidad `msxs-web`, y la 0.3.1 ignora `composicion_max`; una composición
con alguna capa activa se relanza una vez.

Efecto al desplegar: las salidas limpias SRT y RTMP no se relanzan, porque su comando de ffmpeg no
cambia (el HLS lo sirve MediaMTX, que no se reinicia). Una composición con alguna capa activa cambia
de comando (`eof_action=endall`), así que se relanza una vez: la señal compuesta y sus destinos se
cortan unos 20 s. Una sin capas activas no cambia de comando y sigue corriendo; la prioridad baja de
`componer.sh` le llega en su próximo relanzamiento. Si hubiera más de 3 composiciones activas, siguen
3 (primero las que ya corren y, entre ellas, las de los flujos más antiguos) y las demás, con sus
destinos compuestos, se paran con «Límite de…». Si en el mismo día se despliega el Recorder 1.5.3,
primero el Streamer: así su panel no manda al login mientras el Recorder arranca.

## Acceso (0.3.1)

Solo por Tailscale y solo con los usuarios del MSX Recorder del mismo equipo. El panel escucha en
`127.0.0.1:8095` y lo publica el Caddy del Recorder en `https://<dominio del Recorder>/streamer/`
(mismo dominio, así que comparte la cookie de sesión del Recorder). Hace falta «streamer» en la
columna G «Módulos» de la hoja de usuarios. El panel pregunta por la sesión a
`http://127.0.0.1:8081/api/auth/yo` (caché de 10 s); sin sesión, manda al login del Recorder, que
devuelve aquí al entrar. Admin: todo. Operador: ver, arrancar/parar destinos y vista previa. El
registro anota quién hizo cada cambio. No hay clave propia ni acceso por la red local.

Además, desde la 0.3.2: si el Recorder no responde o contesta 5xx (el 503 del Recorder 1.5.3 recién
reiniciado, antes de leer la hoja), vale su última respuesta; un 401 (sesión cerrada) o cualquier
otro 4xx deja fuera. Con un Recorder antiguo cuya respuesta no trae los módulos (1.4.1) solo entran
los admins. Una petición que cambia algo y viene de otra página (su `Origin` no es este panel) se
rechaza con 403. Los operadores no ven el streamid de entradas y destinos, ni lo que va en las URL
tras `?` o antes de `@`, ni la clave RTMP (salen como `***`); en los errores y en el registro no
aparecen para nadie, tampoco los de un destino o flujo ya borrado o editado: toda URL SRT o RTMP
sale ahí como la ve un operador.

`host_publico` (config.json) es el nombre con el que se construyen las direcciones HLS de los
destinos (`http://<host_publico>:8888/…`, para reproductores externos): el dominio del Recorder, que
resuelve a su IP de Tailscale. Desde la 0.3.4 la vista previa ya no lo usa: va por el panel.
