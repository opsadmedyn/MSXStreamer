# Prueba de aceptación del hub de señales · banco de pruebas (contenedor de desarrollo, no la HP Z8)

> Informe que escribe `scripts/aceptacion.py`, de la pasada completa en el banco de pruebas con el código de
> esta rama (2026-11-19). No es la HP Z8: la carga de la estación y las pruebas con equipos reales
> están pendientes (ver [banco-de-pruebas.md](banco-de-pruebas.md) y [validacion.md](validacion.md)).

- **Cuándo:** 2026-11-19 08:59 UTC
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
| Todas | Las tres rutas se activan a la vez desde la API del panel | Pasa | las tres peticiones, en 0.04 s |
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 10.3 s de activar |
| SRT | Llega señal tras activar | Pasa | a los 10.3 s |
| HLS | Llega señal tras activar | Pasa | a los 15.1 s |
| WebRTC | Llega señal tras activar | Pasa | a los 12.8 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 240 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 29.2 s de 29.2 s; mayor parada 0.0 s; 1280×720; decodificó 9.2 MB de vídeo y 476 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 876 fotogramas en 29.2 s (30.02 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 29.2 s de audio audio/opus en 29.2 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 14 segmentos nuevos en 29.0 s; mayor espera 2.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | SRT: enviando (El proceso de envío manda 2 765 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 596 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 717 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| SRT | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 6.7 s del corte pasa a «reconectando»: El hub está conectando con el destino |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 59.4 s de 59.4 s; mayor parada 0.0 s; 1280×720; decodificó 18.5 MB de vídeo y 969 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1780 fotogramas en 59.4 s (29.99 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 59.4 s de audio audio/opus en 59.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 29 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| SRT | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 1.8 s y el hub marca «enviando» a los 4.8 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 15.1 s de 15.1 s; mayor parada 0.0 s; 1280×720; decodificó 4.7 MB de vídeo y 247 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 453 fotogramas en 15.1 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 15.1 s de audio audio/opus en 15.1 s; mayor silencio 0.0 s |
| SRT | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1536 fotogramas en 51.5 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 612 fotogramas en 20.7 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor SRT

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 09:00:37 | El receptor SRT desaparece sin despedirse |
| El hub lo enseña | +6.7 s | La ruta pasa a «reconectando»: El hub está conectando con el destino. Evidencia: El proceso de envío está en marcha y aún no manda datos; Intentos seguidos sin conseguirlo: 1 |
| Vuelve el receptor | 09:01:37 | A los 60 s del corte |
| Vuelve la señal al receptor | +1.8 s | El hub reintenta cada 2 s. En total el receptor estuvo 61.5 s sin recibir |
| El hub marca «enviando» | +4.8 s | |

Estados que fue enseñando el hub en cada ruta:

- SRT: 09:00:35 enviando → 09:00:44 reconectando → 09:01:42 enviando
- HLS: 09:00:35 enviando
- WebRTC: 09:00:35 enviando

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 26.7 % de media, 30.3 % de máximo (de 4 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 0.7 de media, 0.9 de máximo.
- Memoria: 15004 MB disponibles de 16087 MB.
- Sin tarjeta NVIDIA a la vista (nvidia-smi no responde): las conversiones de vídeo irían por CPU.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 18.7 % de media, 20.5 % de máximo | 73.2 MB de media, 80.4 MB de máximo |
| Conversión de «Aceptación · fuente RTMP» | 4.5 % de media, 5 % de máximo | 70.8 MB de media, 70.8 MB de máximo |
| Envío a «Aceptación · receptor SRT» | 2.6 % de media, 3.5 % de máximo | 69.9 MB de media, 69.9 MB de máximo |

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
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 10.3 s de activar |
| RTMP | Llega señal tras activar | Pasa | a los 10.3 s |
| HLS | Llega señal tras activar | Pasa | a los 14.8 s |
| WebRTC | Llega señal tras activar | Pasa | a los 13.8 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 240 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 29.2 s de 29.2 s; mayor parada 0.0 s; 1280×720; decodificó 9.2 MB de vídeo y 477 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 876 fotogramas en 29.2 s (30.01 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 29.2 s de audio audio/opus en 29.2 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 15 segmentos nuevos en 29.0 s; mayor espera 2.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | RTMP: enviando (El proceso de envío manda 2 633 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 526 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 622 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| RTMP | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 2.9 s del corte pasa a «reconectando»: El hub está conectando con el destino |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 58.3 s de 58.4 s; mayor parada 0.0 s; 1280×720; decodificó 18.2 MB de vídeo y 953 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1750 fotogramas en 58.4 s (29.99 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 58.3 s de audio audio/opus en 58.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 30 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| RTMP | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 1.9 s y el hub marca «enviando» a los 5.2 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 16.1 s de 16.1 s; mayor parada 0.0 s; 1280×720; decodificó 5.0 MB de vídeo y 263 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 483 fotogramas en 16.1 s (29.94 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 16.1 s de audio audio/opus en 16.1 s; mayor silencio 0.0 s |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 1.8 s |
| Todas | Reiniciar los servicios del hub no corta ninguna salida | Pasa | RTMP: 0.0 s sin señal · HLS: 0.0 s sin señal · WebRTC: 0.0 s sin señal; los 2 ffmpeg de las rutas son los mismos procesos de antes; el supervisor anotó al arrancar que 2 seguían en marcha y que lanzó 0 |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Envío a «Aceptación · servidor RTMP», Conversión de «Aceptación · fuente SRT»); un solo ffmpeg por tarea |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 2.1 s |
| Todas | Tras el reinicio, las rutas que estaban activas vuelven solas | Pasa | RTMP: 8.5 s sin señal · HLS: 9.1 s sin señal · WebRTC: 19.2 s sin señal; ahora: RTMP enviando, HLS enviando, WebRTC enviando; páginas de los reproductores recargadas 1 vez |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Envío a «Aceptación · servidor RTMP», Conversión de «Aceptación · fuente SRT»); un solo ffmpeg por tarea |
| RTMP | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1527 fotogramas en 51.5 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMP | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 901 fotogramas en 30.7 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor RTMP

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 09:03:01 | El receptor RTMP desaparece sin despedirse |
| El hub lo enseña | +2.9 s | La ruta pasa a «reconectando»: El hub está conectando con el destino. Evidencia: El proceso de envío está en marcha y aún no manda datos; Intentos seguidos sin conseguirlo: 3 |
| Vuelve el receptor | 09:04:01 | A los 60 s del corte |
| Vuelve la señal al receptor | +1.9 s | El hub reintenta cada 2 s. En total el receptor estuvo 61.5 s sin recibir |
| El hub marca «enviando» | +5.2 s | |

Estados que fue enseñando el hub en cada ruta:

- RTMP: 09:02:59 enviando → 09:03:04 reconectando → 09:04:06 enviando
- HLS: 09:02:59 enviando
- WebRTC: 09:02:59 enviando

### Reinicio de los servicios del hub con las rutas activas

- Orden: `tests/banco/banco.sh supervisor`. El panel volvió a responder a los 1.8 s.
- Tiempo sin señal en cada salida: RTMP 0.0 s, HLS 0.0 s, WebRTC 0.0 s.
- En su primera vuelta tras arrancar, el supervisor encontró 2 procesos en marcha (los conserva) y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 2 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 0 veces.

### Reinicio completo con las rutas activas

- Orden: `tests/banco/banco.sh reiniciar`. El panel volvió a responder a los 2.1 s.
- Tiempo sin señal en cada salida: RTMP 8.5 s, HLS 9.1 s, WebRTC 19.2 s.
- En su primera vuelta tras arrancar, el supervisor encontró 0 procesos en marcha y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 0 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 1 vez y las páginas de los reproductores se recargaron 1 vez.

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 29.3 % de media, 31.9 % de máximo (de 4 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 1.4 de media, 1.7 de máximo.
- Memoria: 14991 MB disponibles de 16087 MB.
- Sin tarjeta NVIDIA a la vista (nvidia-smi no responde): las conversiones de vídeo irían por CPU.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 19.9 % de media, 21.5 % de máximo | 75.7 MB de media, 82.5 MB de máximo |
| Conversión de «Aceptación · fuente SRT» | 4 % de media, 4.5 % de máximo | 70.9 MB de media, 70.9 MB de máximo |
| Envío a «Aceptación · servidor RTMP» | 2 % de media, 2.5 % de máximo | 68.9 MB de media, 68.9 MB de máximo |

HLS y WebRTC no llevan proceso propio: los sirve MediaMTX, y su consumo es el de la fila de MediaMTX.

## Las claves

La prueba usó 6 claves (la de publicación de cada fuente, la passphrase SRT, la clave RTMP y la de lectura de WebRTC) y las buscó después:

- Registros mirados: .banco (31 archivos)
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
