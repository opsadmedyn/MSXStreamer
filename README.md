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
deja de responder a las lecturas nuevas. La **vista previa** del panel abre el HLS de ese flujo
durante una hora.

Las reglas viven en MediaMTX (`authMethod: internal`): desde el propio equipo se permite todo (las
salidas leen de ahí) y, desde la red, solo la lectura de esos flujos. El supervisor las ajusta por
la API en cada vuelta, en caliente y sin cortar a nadie, así que **no dependen del panel**: con el
panel parado, las salidas se siguen conectando. Como `hlsAlwaysRemux` está apagado, el HLS solo se
genera mientras alguien lo mira.

### Composición (fase 2)

El supervisor mantiene, por cada flujo con la composición activa, un proceso `componer.sh`: una
captura de Chromium sin pantalla por capa (`compositor/captura.mjs`, PNG con alfa a 29,97 fps por un
FIFO) y un ffmpeg que reencuadra y superpone por CPU (el ffmpeg del Z8 no trae filtros CUDA), codifica
con NVENC y publica en MediaMTX en `comp_<flujo>`. Las salidas con fuente "compuesta" leen de ahí; la
señal limpia sigue igual para las demás. Todo va en un grupo de procesos: se para o relanza entero.
Runtime (Node y Chromium): `scripts/instalar-compositor.sh <estación>`, una vez. Capa de ejemplo:
`http://127.0.0.1:8095/static/capa-ejemplo.html` (L-bar con reloj, para el preajuste "L derecha").

### Salvaguardas de la composición (0.3.2)

Una composición nunca debe afectar a la señal limpia ni al Recorder:

- **Sin entrada, termina.** Si se corta la señal (o muere una captura), la composición termina en
  vez de emitir una imagen congelada, y el supervisor la relanza cuando la entrada vuelve. Si sigue
  viva pero su vídeo lleva 15 s sin avanzar (`frame=` del `.prog`), el supervisor también la relanza.
- **Como mucho 3 a la vez** (`composicion_max` en `config.json`, 3 si no está). El panel no deja
  activar una cuarta; si la base tiene más (un flujo que se inicia, una edición a mano), el
  supervisor arranca solo 3, primero las que ya corren, y las demás esperan con «Límite de…».
- **Prioridad baja:** `componer.sh` se pone nice 10, ionice best-effort 7 y `oom_score_adj` 500, y
  lo heredan Chromium y ffmpeg: si falta CPU, disco o memoria, cede y cae antes que lo demás.
- **Datos dañados:** una composición que no se puede construir queda como «Composición no válida»
  y el supervisor sigue vigilando y relanzando las demás salidas.
- **Disco:** los `.prog` (más de 1 MB en disco) y los `.log` (más de 5 MB) se recortan también
  mientras el proceso corre; si una composición no llega a abrir la entrada, el supervisor mata las
  capturas que se quedaron esperando su FIFO.

## Despliegue

```
scripts/desplegar.sh z8 --instalar   # primera vez: MediaMTX, entorno, config y servicios
scripts/desplegar.sh z8              # actualizaciones: las salidas no se cortan
scripts/desplegar.sh z8 --mediamtx   # además, mediamtx.yml y unidades nuevas (corte de segundos)
```

Todo queda en `/home/mediasat/streamer`. Puertos: panel 8095, HLS 8888, SRT 8890 (UDP), API de
MediaMTX 9997 (solo local). La configuración de la máquina está en `config.json`
(ver `config/config.ejemplo.json`).

### 0.3.2

La 0.3.1 desplegada (solo Tailscale, sin clave del panel) más los arreglos revisados: las
salvaguardas de la composición (arriba) y los del acceso (abajo). Se despliega entera con
`scripts/desplegar.sh <estación>`, sin `--mediamtx`: copia todo `streamer/` y reinicia solo
`msxs-supervisor` y `msxs-web`; MediaMTX sigue igual. No basta copiar `servidor.py` y reiniciar
`msxs-web`: `servidor.py` importa `supervisor.py`, que necesita `composicion.maximo` del nuevo
`composicion.py` (con el de la 0.3.1 el panel no arranca y `/streamer/` queda fuera), y las
salvaguardas del supervisor solo se aplican al reiniciarlo.

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
aparecen para nadie.

`host_publico` (config.json) es el nombre con el que se construyen los enlaces HLS y de vista previa
(`http://<host_publico>:8888/…`): el dominio del Recorder, que resuelve a su IP de Tailscale.
