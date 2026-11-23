# Prueba de aceptación del hub de señales · banco de pruebas (contenedor de desarrollo, no la HP Z8)

> Informe que escribe `scripts/aceptacion.py`, de la pasada completa en el banco de pruebas con el código de
> esta rama (2026-11-19). No es la HP Z8: la carga de la estación y las pruebas con equipos reales
> están pendientes (ver [banco-de-pruebas.md](banco-de-pruebas.md) y [validacion.md](validacion.md)).

- **Cuándo:** 2026-11-19 16:37 UTC
- **Dónde corrió el hub:** banco de pruebas (contenedor de desarrollo, no la HP Z8) · vm · Intel(R) Xeon(R) Processor @ 2.10GHz · 4 núcleos · Linux 6.18.44-fc-v653
- **Hub:** Streamer 0.4.0-alfa · MediaMTX v1.21.1 · FFmpeg 6.1.1-3ubuntu5 · conversiones de vídeo con libx264
- **Señal de prueba:** H.264 1280×720 a 30 fps y 2.5 Mb/s (sin fotogramas B) con un tono de 1000 Hz en AAC estéreo a 48 kHz, generada con ffmpeg.
- **Con qué se comprobó:** la cámara y los receptores son programas (ffmpeg y un Chromium sin ventana). Es una verificación de banco de pruebas, no con equipos reales.
- **Cómo se manejó el hub:** por la API del panel, con una sesión de administrador (http://127.0.0.1:8095).

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
| Todas | Las tres rutas se activan a la vez desde la API del panel | Pasa | las tres peticiones, en 0.03 s |
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 9.3 s de activar |
| SRT | Llega señal tras activar | Pasa | a los 9.3 s |
| HLS | Llega señal tras activar | Pasa | a los 13.8 s |
| WebRTC | Llega señal tras activar | Pasa | a los 11.8 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 240 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 29.2 s de 29.2 s; mayor parada 0.0 s; 1280×720; decodificó 9.2 MB de vídeo y 477 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 876 fotogramas en 29.2 s (29.99 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 29.2 s de audio audio/opus en 29.2 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 14 segmentos nuevos en 29.0 s; mayor espera 1.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | SRT: enviando (El proceso de envío manda 2 711 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 747 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 729 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| SRT | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 7.0 s del corte pasa a «reconectando»: El hub está conectando con el destino |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 58.4 s de 58.4 s; mayor parada 0.0 s; 1280×720; decodificó 18.3 MB de vídeo y 954 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1752 fotogramas en 58.4 s (30.01 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 58.4 s de audio audio/opus en 58.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 29 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| SRT | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 1.7 s y el hub marca «enviando» a los 7.0 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 18.1 s de 18.1 s; mayor parada 0.0 s; 1280×720; decodificó 5.7 MB de vídeo y 296 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 543 fotogramas en 18.1 s (29.97 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 18.1 s de audio audio/opus en 18.1 s; mayor silencio 0.0 s |
| SRT | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1467 fotogramas en 48.9 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 551 fotogramas en 18.4 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor SRT

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 16:38:43 | El receptor SRT desaparece sin despedirse |
| El hub lo enseña | +7.0 s | La ruta pasa a «reconectando»: El hub está conectando con el destino. Evidencia: El proceso de envío está en marcha y aún no manda datos; Intentos seguidos sin conseguirlo: 2 |
| Vuelve el receptor | 16:39:43 | A los 60 s del corte |
| Vuelve la señal al receptor | +1.7 s | El hub reintenta cada 2 s. En total el receptor estuvo 61.8 s sin recibir |
| El hub marca «enviando» | +7.0 s | |

Estados que fue enseñando el hub en cada ruta:

- SRT: 16:38:40 enviando → 16:38:50 reconectando → 16:39:50 enviando
- HLS: 16:38:40 enviando
- WebRTC: 16:38:40 enviando

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 29 % de media, 31.4 % de máximo (de 4 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 2.1 de media, 2.3 de máximo.
- Memoria: 14999 MB disponibles de 16087 MB.
- Sin tarjeta NVIDIA a la vista (nvidia-smi no responde): las conversiones de vídeo irían por CPU.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 18.9 % de media, 20.5 % de máximo | 78.2 MB de media, 84.7 MB de máximo |
| Conversión de «Aceptación · fuente RTMP» | 4.8 % de media, 5.5 % de máximo | 69.5 MB de media, 69.5 MB de máximo |
| Envío a «Aceptación · receptor SRT» | 2.6 % de media, 3 % de máximo | 68.5 MB de media, 68.5 MB de máximo |

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
| Todas | Las tres rutas se activan a la vez desde la API del panel | Pasa | las tres peticiones, en 0.03 s |
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 9.0 s de activar |
| RTMP | Llega señal tras activar | Pasa | a los 9.0 s |
| HLS | Llega señal tras activar | Pasa | a los 13.8 s |
| WebRTC | Llega señal tras activar | Pasa | a los 11.5 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 240 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 29.2 s de 29.2 s; mayor parada 0.0 s; 1280×720; decodificó 9.2 MB de vídeo y 477 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 877 fotogramas en 29.2 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 29.2 s de audio audio/opus en 29.2 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 15 segmentos nuevos en 29.1 s; mayor espera 2.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | RTMP: enviando (El proceso de envío manda 2 589 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 870 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 749 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| RTMP | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 2.7 s del corte pasa a «reconectando»: El hub está conectando con el destino |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 58.4 s de 58.4 s; mayor parada 0.0 s; 1280×720; decodificó 18.3 MB de vídeo y 954 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1753 fotogramas en 58.4 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 58.4 s de audio audio/opus en 58.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 29 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| RTMP | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 4.1 s y el hub marca «enviando» a los 8.7 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 20.1 s de 20.1 s; mayor parada 0.0 s; 1280×720; decodificó 6.3 MB de vídeo y 329 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 604 fotogramas en 20.1 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 20.1 s de audio audio/opus en 20.1 s; mayor silencio 0.0 s |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 4.6 s |
| Todas | Reiniciar los servicios del hub no corta ninguna salida | Pasa | RTMP: 0.0 s sin señal · HLS: 0.0 s sin señal · WebRTC: 0.0 s sin señal; los 2 ffmpeg de las rutas son los mismos procesos de antes; el supervisor anotó al arrancar que 2 seguían en marcha y que lanzó 0 |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Envío a «Aceptación · servidor RTMP», Conversión de «Aceptación · fuente SRT»); un solo ffmpeg por tarea |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 11.2 s |
| Todas | Tras el reinicio, las rutas que estaban activas vuelven solas | Pasa | RTMP: 8.0 s sin señal · HLS: 10.1 s sin señal · WebRTC: 20.2 s sin señal; ahora: RTMP enviando, HLS enviando, WebRTC enviando; páginas de los reproductores recargadas 1 vez |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Envío a «Aceptación · servidor RTMP», Conversión de «Aceptación · fuente SRT»); un solo ffmpeg por tarea |
| RTMP | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1480 fotogramas en 49.3 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMP | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 1141 fotogramas en 38.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor RTMP

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 16:41:11 | El receptor RTMP desaparece sin despedirse |
| El hub lo enseña | +2.7 s | La ruta pasa a «reconectando»: El hub está conectando con el destino. Evidencia: El proceso de envío está en marcha y aún no manda datos; Intentos seguidos sin conseguirlo: 4 |
| Vuelve el receptor | 16:42:11 | A los 60 s del corte |
| Vuelve la señal al receptor | +4.1 s | El hub reintenta cada 2 s. En total el receptor estuvo 64.2 s sin recibir |
| El hub marca «enviando» | +8.7 s | |

Estados que fue enseñando el hub en cada ruta:

- RTMP: 16:41:09 enviando → 16:41:14 reconectando → 16:42:20 enviando
- HLS: 16:41:09 enviando
- WebRTC: 16:41:09 enviando

### Reinicio de los servicios del hub con las rutas activas

- Orden: `tests/banco/banco.sh supervisor`. El panel volvió a responder a los 4.6 s.
- Tiempo sin señal en cada salida: RTMP 0.0 s, HLS 0.0 s, WebRTC 0.0 s.
- En su primera vuelta tras arrancar, el supervisor encontró 2 procesos en marcha (los conserva) y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 2 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 0 veces.

### Reinicio completo con las rutas activas

- Orden: `tests/banco/banco.sh reiniciar`. El panel volvió a responder a los 11.2 s.
- Tiempo sin señal en cada salida: RTMP 8.0 s, HLS 10.1 s, WebRTC 20.2 s.
- En su primera vuelta tras arrancar, el supervisor encontró 0 procesos en marcha y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 0 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 2 veces y las páginas de los reproductores se recargaron 1 vez.

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 29 % de media, 31.8 % de máximo (de 4 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 1.9 de media, 2.1 de máximo.
- Memoria: 15012 MB disponibles de 16087 MB.
- Sin tarjeta NVIDIA a la vista (nvidia-smi no responde): las conversiones de vídeo irían por CPU.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 19.8 % de media, 22 % de máximo | 78 MB de media, 84 MB de máximo |
| Conversión de «Aceptación · fuente SRT» | 4.1 % de media, 4.6 % de máximo | 70.2 MB de media, 70.2 MB de máximo |
| Envío a «Aceptación · servidor RTMP» | 2 % de media, 2.5 % de máximo | 67.2 MB de media, 67.2 MB de máximo |

HLS y WebRTC no llevan proceso propio: los sirve MediaMTX, y su consumo es el de la fila de MediaMTX.

## Las claves

La prueba usó 6 claves (la de publicación de cada fuente, la passphrase SRT, la clave RTMP y la de lectura de WebRTC) y las buscó después:

- Registros mirados: .banco (769 archivos)
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
