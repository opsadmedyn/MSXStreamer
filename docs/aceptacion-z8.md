# Prueba de aceptación del hub de señales · HP Z8 (EZC80), banco al lado de producción

> Pasada del 19 de noviembre de 2026 en la propia estación: `msxserver2`, la HP Z8 que en pantalla se
> llama EZC80. La lanzó `scripts/medir-carga.sh` desde una copia del repositorio en el commit
> `992d91f`, con el hub en un banco de puertos desplazados (`/home/mediasat/hub-banco`) al lado del
> Streamer 0.3.9 que está en producción, que no se tocó. Las señales fueron de prueba: programas
> haciendo de cámara y de receptores. La prueba con señales y equipos reales sigue pendiente
> ([validacion.md](validacion.md)).
>
> Las secciones «Resumen» y «La estación y la producción, antes y después» se añadieron después. Lo
> demás es, sin cambios, el `informe.md` que escribió `scripts/aceptacion.py` en la estación
> (SHA-256 `11f2c70ca0acc28a156bc1045b3f70716d1cc94e407c3531209b5606cd4ac132`).

- **Cuándo:** 2026-11-19 16:20 EST
- **Dónde corrió el hub:** HP Z8 (EZC80), banco al lado de producción · msxserver2 · Intel(R) Xeon(R) Silver 4214 CPU @ 2.20GHz · 48 núcleos · Linux 5.15.0-202-generic
- **Hub:** Streamer 0.4.0-alfa · MediaMTX v1.21.1 · FFmpeg 7.1 · conversiones de vídeo con h264_nvenc
- **Señal de prueba:** H.264 1280×720 a 30 fps y 2.5 Mb/s (sin fotogramas B) con un tono de 1000 Hz en AAC estéreo a 48 kHz, generada con ffmpeg.
- **Con qué se comprobó:** la cámara y los receptores son programas (ffmpeg y un Chromium sin ventana). Es una verificación de banco de pruebas, no con equipos reales.
- **Cómo se manejó el hub:** por la API del panel, con una sesión de administrador (http://127.0.0.1:8195).

## Resumen

La prueba de aceptación pasa en la estación, en sus dos mitades y a la primera. Tardó 6 minutos.

### Resultados por ruta

| Ruta | Qué hace el hub | Audio y vídeo en el receptor | Con el receptor SRT o RTMP cortado 60 s |
|---|---|---|---|
| RTMP → SRT | Copia el vídeo y el audio | Sí: H.264 1280×720 y AAC estéreo, a los 9,1 s de activar | Es la salida cortada. El hub la enseña como «reconectando» a los 7,5 s y se recupera sola: el receptor vuelve a tener señal a los 3,2 s de regresar |
| RTMP → HLS | Copia el vídeo y el audio | Sí, en un navegador y con ffmpeg, a los 12,1 s | Sigue: el navegador reprodujo 59,8 s de 59,8 s, sin paradas |
| RTMP → WebRTC | Copia el vídeo y convierte el audio a Opus | Sí, en un navegador, a los 13,1 s | Sigue: 1793 fotogramas en 59,8 s, audio sin silencios y ningún paquete perdido |
| SRT → RTMP | Copia el vídeo y el audio | Sí: H.264 1280×720 y AAC estéreo, a los 6,6 s de activar | Es la salida cortada. El hub la enseña como «reconectando» a los 1,5 s y se recupera sola: el receptor vuelve a tener señal a los 2,5 s de regresar |
| SRT → HLS | Copia el vídeo y el audio | Sí, en un navegador y con ffmpeg, a los 10,6 s | Sigue: el navegador reprodujo 59,7 s de 59,8 s, sin paradas |
| SRT → WebRTC | Copia el vídeo y convierte el audio a Opus | Sí, en un navegador, a los 9,6 s | Sigue: 1793 fotogramas en 59,8 s, audio sin silencios y ningún paquete perdido |

Con las rutas de la segunda mitad activas se reiniciaron además los servicios del hub del banco.
Reiniciar el panel y el supervisor no cortó ninguna salida. Tras un reinicio completo, con MediaMTX
incluido, las tres rutas volvieron solas y sin publicaciones duplicadas, después de 8,5 s (RTMP),
11,1 s (HLS) y 18,3 s (WebRTC) sin señal. No es un reinicio de la Z8: ese queda para el relevo.

Las seis claves de la prueba no aparecen en los registros ni en lo que devuelve la API.

### Carga observada en la estación

Con una fuente de 1280×720 a 30 fps y 2,5 Mb/s y sus tres salidas en marcha (medias de 30 s; el
100 % de un proceso es un núcleo de los 48 de la estación):

| | Entrada RTMP | Entrada SRT |
|---|---|---|
| MediaMTX | 37,4 % de un núcleo (máximo 41,3 %), 73 MB | 39,3 % de un núcleo (máximo 42,1 %), 78 MB |
| Conversión del audio a Opus para WebRTC | 9,1 % (máximo 9,6 %), 37 MB | 8,5 % (máximo 9 %), 37 MB |
| Envío por SRT o por RTMP | 3,7 % (máximo 4 %), 35 MB | 3,9 % (máximo 4,5 %), 34 MB |
| Los tres procesos del hub, en total | 50 % de un núcleo y 145 MB | 52 % de un núcleo y 149 MB |
| CPU de todo el equipo | 7,8 % de media, 9,6 % de máximo | 7,5 % de media, 9,9 % de máximo |
| Memoria disponible | 88 702 MB de 95 338 MB | 88 676 MB de 95 338 MB |
| Tarjeta (RTX 5060 Ti) | 0 % | 0 % |

- Los procesos del hub usan en total alrededor de medio núcleo de los 48 de la estación, y unos
  150 MB de memoria.
- La cifra de todo el equipo incluye además lo que la prueba pone en la misma máquina (la cámara,
  que codifica H.264 por CPU, los receptores y los dos reproductores de un Chromium sin ventana) y
  lo demás que corre en la estación. Antes de arrancar, la carga media de la estación era de 1,7:
  el 3 % de los núcleos.
- El Streamer de producción no tenía ninguna tarea en marcha ni al empezar ni al terminar la
  prueba: la carga medida no se suma a la de unas salidas al aire.
- La tarjeta no trabajó: ninguna ruta de la prueba convierte vídeo. El hub encontró `h264_nvenc` y
  lo usará en las conversiones de vídeo, pero esa carga no está medida.

### Lo que esta medida no dice

- Lo que cuesta convertir vídeo con NVENC (un destino con perfil de 720p o de 1080p), ni cuántas
  conversiones a la vez admite la tarjeta.
- La carga con varias fuentes a la vez y con las señales de verdad de la estación (1080i y 1080p a
  59,94, con varios audios y más bitrate).
- El comportamiento con la red y con equipos reales de por medio: todo viajó dentro de la estación.

## Resultado

| Prueba | Resultado |
|---|---|
| Entrada RTMP con salidas SRT, HLS y WebRTC | Pasa |
| Entrada SRT con salidas RTMP, HLS y WebRTC | Pasa |
| Las claves de la prueba no aparecen en los registros ni en lo que devuelve la API | Pasa |

## Entrada RTMP con salidas SRT, HLS y WebRTC

La fuente: H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz. Quién inicia la conexión: el codificador publica en el hub. Dirección de la señal: hacia el hub.

### Antes de activar: qué se copia y qué se convierte

| Salida | Vídeo | Audio | Carga prevista |
|---|---|---|---|
| SRT | Se copia: H.264 · 1280×720 · 30 fps. SRT acepta H.264 tal como llega. | Se copia: AAC · estéreo · 48 kHz. SRT acepta AAC tal como llega. | Sin conversión. |
| HLS | Se copia: H.264 · 1280×720 · 30 fps. HLS acepta H.264 tal como llega. | Se copia: AAC · estéreo · 48 kHz. HLS acepta AAC tal como llega. | Sin conversión. |
| WebRTC | Se copia: H.264 · 1280×720 · 30 fps. WebRTC acepta H.264 tal como llega. | Se convierte: AAC · estéreo · 48 kHz → Opus · estéreo · 128 kb/s. WebRTC no admite AAC: se convierte a Opus. | Solo audio: carga mínima. |

### Comprobaciones

| Salida | Qué se comprobó | Resultado | Medida |
|---|---|---|---|
| Todas | El hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| Todas | Antes de activar, el hub dice qué copia y qué convierte en cada ruta, y por qué | Pasa | SRT: Se copia todo: solo cambia el transporte. · HLS: Se copia todo: solo cambia el transporte. · WebRTC: Se copia el vídeo y se convierte el audio. |
| Todas | Las tres rutas se activan a la vez desde la API del panel | Pasa | las tres peticiones, en 0.07 s |
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 9.1 s de activar |
| SRT | Llega señal tras activar | Pasa | a los 9.1 s |
| HLS | Llega señal tras activar | Pasa | a los 12.1 s |
| WebRTC | Llega señal tras activar | Pasa | a los 13.1 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 240 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 28.4 s de 28.4 s; mayor parada 0.0 s; 1280×720; decodificó 9.0 MB de vídeo y 464 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 853 fotogramas en 28.4 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 28.4 s de audio audio/opus en 28.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 14 segmentos nuevos en 29.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | SRT: enviando (El proceso de envío manda 2 738 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 706 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 718 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| SRT | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 7.5 s del corte pasa a «reconectando»: El hub está conectando con el destino |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 59.8 s de 59.8 s; mayor parada 0.0 s; 1280×720; decodificó 18.7 MB de vídeo y 976 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1793 fotogramas en 59.8 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 59.8 s de audio audio/opus en 59.8 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 29 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| SRT | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 3.2 s y el hub marca «enviando» a los 5.5 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 16.2 s de 16.2 s; mayor parada 0.0 s; 1280×720; decodificó 5.1 MB de vídeo y 264 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 486 fotogramas en 16.2 s (30.02 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 16.2 s de audio audio/opus en 16.2 s; mayor silencio 0.0 s |
| SRT | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1482 fotogramas en 49.4 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 651 fotogramas en 21.7 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor SRT

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 16:21:44 | El receptor SRT desaparece sin despedirse |
| El hub lo enseña | +7.5 s | La ruta pasa a «reconectando»: El hub está conectando con el destino. Evidencia: El proceso de envío está en marcha y aún no manda datos; Intentos seguidos sin conseguirlo: 2 |
| Vuelve el receptor | 16:22:44 | A los 60 s del corte |
| Vuelve la señal al receptor | +3.2 s | El hub reintenta cada 2 s. En total el receptor estuvo 63.3 s sin recibir |
| El hub marca «enviando» | +5.5 s | |

Estados que fue enseñando el hub en cada ruta:

- SRT: 16:21:42 enviando → 16:21:52 reconectando → 16:22:50 enviando
- HLS: 16:21:42 enviando
- WebRTC: 16:21:42 enviando

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 7.8 % de media, 9.6 % de máximo (de 48 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 2.1 de media, 2.2 de máximo.
- Memoria: 88702 MB disponibles de 95338 MB.
- Tarjeta NVIDIA GeForce RTX 5060 Ti: uso 0 % de media, 0 % de máximo; codificador 0 % de media, 0 % de máximo.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 37.4 % de media, 41.3 % de máximo | 73.1 MB de media, 81.1 MB de máximo |
| Conversión de «Aceptación · fuente RTMP» | 9.1 % de media, 9.6 % de máximo | 36.8 MB de media, 37 MB de máximo |
| Envío a «Aceptación · receptor SRT» | 3.7 % de media, 4 % de máximo | 35 MB de media, 35 MB de máximo |

HLS y WebRTC no llevan proceso propio: los sirve MediaMTX, y su consumo es el de la fila de MediaMTX.

## Entrada SRT con salidas RTMP, HLS y WebRTC

La fuente: H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz. Quién inicia la conexión: el codificador llama al hub. Dirección de la señal: hacia el hub.

### Antes de activar: qué se copia y qué se convierte

| Salida | Vídeo | Audio | Carga prevista |
|---|---|---|---|
| RTMP | Se copia: H.264 · 1280×720 · 30 fps. RTMP acepta H.264 tal como llega. | Se copia: AAC · estéreo · 48 kHz. RTMP acepta AAC tal como llega. | Sin conversión. |
| HLS | Se copia: H.264 · 1280×720 · 30 fps. HLS acepta H.264 tal como llega. | Se copia: AAC · estéreo · 48 kHz. HLS acepta AAC tal como llega. | Sin conversión. |
| WebRTC | Se copia: H.264 · 1280×720 · 30 fps. WebRTC acepta H.264 tal como llega. | Se convierte: AAC · estéreo · 48 kHz → Opus · estéreo · 128 kb/s. WebRTC no admite AAC: se convierte a Opus. | Solo audio: carga mínima. |

### Comprobaciones

| Salida | Qué se comprobó | Resultado | Medida |
|---|---|---|---|
| Todas | El hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| Todas | Antes de activar, el hub dice qué copia y qué convierte en cada ruta, y por qué | Pasa | RTMP: Se copia todo: solo cambia el transporte. · HLS: Se copia todo: solo cambia el transporte. · WebRTC: Se copia el vídeo y se convierte el audio. |
| Todas | Las tres rutas se activan a la vez desde la API del panel | Pasa | las tres peticiones, en 0.05 s |
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 6.6 s de activar |
| RTMP | Llega señal tras activar | Pasa | a los 6.6 s |
| HLS | Llega señal tras activar | Pasa | a los 10.6 s |
| WebRTC | Llega señal tras activar | Pasa | a los 9.6 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 239 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 28.4 s de 28.4 s; mayor parada 0.0 s; 1280×720; decodificó 9.0 MB de vídeo y 463 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 851 fotogramas en 28.4 s (29.99 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 28.4 s de audio audio/opus en 28.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 15 segmentos nuevos en 29.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | RTMP: enviando (El proceso de envío manda 2 631 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 557 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 704 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| RTMP | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 1.5 s del corte pasa a «reconectando»: El destino no responde y el hub reintenta |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 59.7 s de 59.8 s; mayor parada 0.0 s; 1280×720; decodificó 18.7 MB de vídeo y 975 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1793 fotogramas en 59.8 s (30.01 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 59.8 s de audio audio/opus en 59.8 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 30 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| RTMP | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 2.5 s y el hub marca «enviando» a los 5.5 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 16.2 s de 16.2 s; mayor parada 0.0 s; 1280×720; decodificó 5.1 MB de vídeo y 264 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 486 fotogramas en 16.2 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 16.2 s de audio audio/opus en 16.2 s; mayor silencio 0.0 s |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 1.4 s |
| Todas | Reiniciar los servicios del hub no corta ninguna salida | Pasa | RTMP: 0.0 s sin señal · HLS: 0.0 s sin señal · WebRTC: 0.0 s sin señal; los 2 ffmpeg de las rutas son los mismos procesos de antes; el supervisor anotó al arrancar que 2 seguían en marcha y que lanzó 0 |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Envío a «Aceptación · servidor RTMP», Conversión de «Aceptación · fuente SRT»); un solo ffmpeg por tarea |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 3.8 s |
| Todas | Tras el reinicio, las rutas que estaban activas vuelven solas | Pasa | RTMP: 8.5 s sin señal · HLS: 11.1 s sin señal · WebRTC: 18.3 s sin señal; ahora: RTMP enviando, HLS enviando, WebRTC enviando; páginas de los reproductores recargadas 1 vez |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Envío a «Aceptación · servidor RTMP», Conversión de «Aceptación · fuente SRT»); un solo ffmpeg por tarea |
| RTMP | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1403 fotogramas en 46.8 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMP | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 833 fotogramas en 27.8 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor RTMP

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 16:24:17 | El receptor RTMP desaparece sin despedirse |
| El hub lo enseña | +1.5 s | La ruta pasa a «reconectando»: El destino no responde y el hub reintenta. Evidencia: Último intento: El otro equipo rechaza la conexión. ffmpeg dijo: Error opening output files: Connection refused; Intentos seguidos sin conseguirlo: 5 |
| Vuelve el receptor | 16:25:17 | A los 60 s del corte |
| Vuelve la señal al receptor | +2.5 s | El hub reintenta cada 2 s. En total el receptor estuvo 62.6 s sin recibir |
| El hub marca «enviando» | +5.5 s | |

Estados que fue enseñando el hub en cada ruta:

- RTMP: 16:24:15 enviando → 16:24:19 reconectando → 16:25:23 enviando
- HLS: 16:24:15 enviando
- WebRTC: 16:24:15 enviando

### Reinicio de los servicios del hub con las rutas activas

- Orden: `tests/banco/banco.sh supervisor`. El panel volvió a responder a los 1.4 s.
- Tiempo sin señal en cada salida: RTMP 0.0 s, HLS 0.0 s, WebRTC 0.0 s.
- En su primera vuelta tras arrancar, el supervisor encontró 2 procesos en marcha (los conserva) y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 2 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 0 veces.

### Reinicio completo con las rutas activas

- Orden: `tests/banco/banco.sh reiniciar`. El panel volvió a responder a los 3.8 s.
- Tiempo sin señal en cada salida: RTMP 8.5 s, HLS 11.1 s, WebRTC 18.3 s.
- En su primera vuelta tras arrancar, el supervisor encontró 0 procesos en marcha y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 0 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 2 veces y las páginas de los reproductores se recargaron 1 vez.

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 7.5 % de media, 9.9 % de máximo (de 48 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 3.1 de media, 3.6 de máximo.
- Memoria: 88676 MB disponibles de 95338 MB.
- Tarjeta NVIDIA GeForce RTX 5060 Ti: uso 0 % de media, 0 % de máximo; codificador 0 % de media, 0 % de máximo.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 39.3 % de media, 42.1 % de máximo | 77.5 MB de media, 85.1 MB de máximo |
| Conversión de «Aceptación · fuente SRT» | 8.5 % de media, 9 % de máximo | 37.3 MB de media, 37.6 MB de máximo |
| Envío a «Aceptación · servidor RTMP» | 3.9 % de media, 4.5 % de máximo | 34 MB de media, 34 MB de máximo |

HLS y WebRTC no llevan proceso propio: los sirve MediaMTX, y su consumo es el de la fila de MediaMTX.

## Las claves

La prueba usó 6 claves (la de publicación de cada fuente, la passphrase SRT, la clave RTMP y la de lectura de WebRTC) y las buscó después:

- Registros mirados: /home/mediasat/hub-banco (35 archivos)
- En los registros mirados no aparece ninguna.
- En las respuestas de la API (matriz, carga y registro de cambios): no aparece ninguna; salen como «***».

## Qué queda anotado como comprobado

| Entrada | Salida | Nivel |
|---|---|---|
| RTMP (el codificador llama al hub) | SRT (el hub llama al receptor) | Verificada en el banco de pruebas |
| RTMP (el codificador llama al hub) | HLS (el receptor pide la señal al hub) | Verificada en el banco de pruebas |
| RTMP (el codificador llama al hub) | WebRTC (el receptor pide la señal al hub) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | RTMP (el hub llama al receptor) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | HLS (el receptor pide la señal al hub) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | WebRTC (el receptor pide la señal al hub) | Verificada en el banco de pruebas |

«Verificada en el banco de pruebas» quiere decir con programas haciendo de cámara y de receptor. Ninguna combinación queda como «verificada con equipos reales» hasta que un administrador lo anote tras probarla con ellos.

## La estación y la producción, antes y después

`scripts/medir-carga.sh` mira la estación antes de arrancar el banco y vuelve a mirarla al terminar.
Esto es lo que escribió, sin las líneas de la prueba:

```
== estación: msxserver2, 2026-11-19 16:20:39 EST
   sistema: Ubuntu 22.04.5 LTS, núcleo 5.15.0-202-generic
   procesador: Intel(R) Xeon(R) Silver 4214 CPU @ 2.20GHz, 48 núcleos
   memoria: 89223 MB disponibles de 95338 MB
   tarjeta (nombre, uso, memoria usada, memoria total, sesiones de codificación): NVIDIA GeForce RTX 5060 Ti, 0 %, 37 MiB, 16311 MiB, 0
   carga media: 1.69 (1 min), 1.33 (5 min), 1.23 (15 min): el 3 % de los núcleos
== Streamer en producción (/home/mediasat/streamer)
   versión: 0.3.9
   MediaMTX: v1.21.1
   servicio msxs-mediamtx: active, proceso 4670, desde Sun 2026-11-08 16:27:08 EST
   servicio msxs-supervisor: active, proceso 4672, desde Sun 2026-11-08 16:27:08 EST
   servicio msxs-web: active, proceso 4675, desde Sun 2026-11-08 16:27:08 EST
   tareas del Streamer en marcha (PID): 
   Caddy del equipo: v2.11.4 h1:XKxkMTgNSizEvKG6QHue6cAsFOteU2qA61w2tKkCWi0=
== lo que usará el banco
   ffmpeg: /usr/local/bin/ffmpeg (ffmpeg version 7.1)
   Python: /home/mediasat/streamer/venv/bin/python (Python 3.10.12)
   MediaMTX: una copia del de producción (v1.21.1)
   navegador: /home/mediasat/streamer/runtime/node/bin/node con su Chromium; reproduce H.264, AAC y Opus
== comprobaciones antes de arrancar
   puertos libres: 8195 10097 8990 8988 8989 8289 18181 2045 2046 9311 1960
   disco: 127 GB libres en /home/mediasat
   carga: el 3 % de los núcleos (tope 60)
== arrancando el banco en /home/mediasat/hub-banco (panel en 127.0.0.1:8195)
   mediamtx: en marcha (pid 2776495)
   recorder: en marcha (pid 2776506)
   supervisor: en marcha (pid 2776507)
   panel: en marcha (pid 2776508)
   procesos de rutas: 0
   panel: http://127.0.0.1:8195 (cookie msr_sesion=admin)
== prueba de aceptación (unos 6 minutos)
== parando el banco
   del banco no queda ningún proceso ni ningún puerto abierto
== Streamer en producción, después
   servicio msxs-mediamtx: active, proceso 4670, desde Sun 2026-11-08 16:27:08 EST
   servicio msxs-supervisor: active, proceso 4672, desde Sun 2026-11-08 16:27:08 EST
   servicio msxs-web: active, proceso 4675, desde Sun 2026-11-08 16:27:08 EST
   tareas del Streamer en marcha (PID): 
   los servicios y las tareas del Streamer son los mismos que antes de la prueba
== la prueba de aceptación PASA
   informe: /home/mediasat/hub-banco/aceptacion-20261119-162041/informe.md
   medidas: /home/mediasat/hub-banco/aceptacion-20261119-162041/datos.json
```

- Los tres servicios del Streamer 0.3.9 (`msxs-mediamtx`, `msxs-supervisor` y `msxs-web`) son los
  mismos procesos antes y después (4670, 4672 y 4675, en marcha desde el 8 de noviembre): nada los
  reinició. De `/home/mediasat/streamer` el guion solo toma cosas: lee la versión, copia MediaMTX y
  usa el Python de su entorno y el Node con Chromium del compositor. No toca Docker, Caddy, el
  cortafuegos ni ningún servicio.
- Del banco no quedó ningún proceso ni ningún puerto abierto. En la estación quedan su carpeta,
  `/home/mediasat/hub-banco` (55 MB: la base de datos de la prueba, los registros y el informe con
  sus medidas, en `aceptacion-20261119-162041/`), y la copia del repositorio desde la que se lanzó,
  `/home/mediasat/hub-banco-repo` (1,6 MB). Nada de producción las usa: se pueden borrar cuando se
  quiera.
- Se llegó a la estación por Tailscale, como `msxserver2`. El alias `z8` del Mac desde el que se
  lanzó la prueba apunta a 192.168.0.177, que no respondió.
- El Caddy de la estación es el 2.11.4. La prueba no pasa por él: el banco se manejó por
  `127.0.0.1`.
- Las medidas de cada lectura están en `datos.json`, junto al informe (SHA-256
  `fde580186be571bb024af298f9bcadbac42b8466fb7acdfe534fa2b99580605e`). En el Mac desde el que se
  lanzó la prueba hay copia de los dos y de la salida del guion, en la carpeta `hub-banco-ezc80`.
