# Prueba de aceptación del hub de señales · banco de pruebas (contenedor de desarrollo, no la HP Z8)

> Informe que escribe `scripts/aceptacion.py`, de la pasada completa en el banco de pruebas con el código de
> esta rama (2026-11-19). No es la HP Z8: la carga de la estación y las pruebas con equipos reales
> están pendientes (ver [banco-de-pruebas.md](banco-de-pruebas.md) y [validacion.md](validacion.md)).

- **Cuándo:** 2026-11-19 14:57 UTC
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
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 6.3 s de activar |
| SRT | Llega señal tras activar | Pasa | a los 6.3 s |
| HLS | Llega señal tras activar | Pasa | a los 11.1 s |
| WebRTC | Llega señal tras activar | Pasa | a los 8.8 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 240 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 29.2 s de 29.2 s; mayor parada 0.0 s; 1280×720; decodificó 9.2 MB de vídeo y 477 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 877 fotogramas en 29.2 s (30.0 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 29.2 s de audio audio/opus en 29.2 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 14 segmentos nuevos en 29.0 s; mayor espera 1.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | SRT: enviando (El proceso de envío manda 2 777 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 757 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 754 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| SRT | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 6.7 s del corte pasa a «reconectando»: El hub está conectando con el destino |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 59.4 s de 59.4 s; mayor parada 0.0 s; 1280×720; decodificó 18.6 MB de vídeo y 970 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1783 fotogramas en 59.4 s (30.02 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 59.4 s de audio audio/opus en 59.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 29 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| SRT | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 1.4 s y el hub marca «enviando» a los 4.7 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 15.1 s de 15.1 s; mayor parada 0.0 s; 1280×720; decodificó 4.7 MB de vídeo y 246 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 452 fotogramas en 15.1 s (29.95 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 15.1 s de audio audio/opus en 15.1 s; mayor silencio 0.0 s |
| SRT | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1414 fotogramas en 47.1 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 495 fotogramas en 16.5 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor SRT

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 14:58:38 | El receptor SRT desaparece sin despedirse |
| El hub lo enseña | +6.7 s | La ruta pasa a «reconectando»: El hub está conectando con el destino. Evidencia: El proceso de envío está en marcha y aún no manda datos; Intentos seguidos sin conseguirlo: 2 |
| Vuelve el receptor | 14:59:38 | A los 60 s del corte |
| Vuelve la señal al receptor | +1.4 s | El hub reintenta cada 2 s. En total el receptor estuvo 61.5 s sin recibir |
| El hub marca «enviando» | +4.7 s | |

Estados que fue enseñando el hub en cada ruta:

- SRT: 14:58:36 enviando → 14:58:45 reconectando → 14:59:43 enviando
- HLS: 14:58:36 enviando
- WebRTC: 14:58:36 enviando

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 28.9 % de media, 32.3 % de máximo (de 4 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 0.7 de media, 1 de máximo.
- Memoria: 15019 MB disponibles de 16087 MB.
- Sin tarjeta NVIDIA a la vista (nvidia-smi no responde): las conversiones de vídeo irían por CPU.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 19.3 % de media, 22.5 % de máximo | 76.6 MB de media, 82.8 MB de máximo |
| Conversión de «Aceptación · fuente RTMP» | 4.8 % de media, 5.5 % de máximo | 69.2 MB de media, 69.2 MB de máximo |
| Envío a «Aceptación · receptor SRT» | 2.7 % de media, 3 % de máximo | 68.2 MB de media, 68.2 MB de máximo |

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
| Todas | El hub deja listas las direcciones de HLS y WebRTC y las enseña como «esperando receptor» | Pasa | a los 7.0 s de activar |
| RTMP | Llega señal tras activar | Pasa | a los 9.1 s |
| HLS | Llega señal tras activar | Pasa | a los 14.8 s |
| WebRTC | Llega señal tras activar | Pasa | a los 9.6 s |
| HLS | Ffmpeg decodifica el audio y el vídeo de la lista | Pasa | vídeo H264 1280×720, 239 fotogramas en 8.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS | El navegador reproduce audio y vídeo con las tres salidas en marcha | Pasa | reprodujo 29.2 s de 29.2 s; mayor parada 0.0 s; 1280×720; decodificó 9.3 MB de vídeo y 477 kB de audio |
| WebRTC | El navegador reproduce vídeo con las tres salidas en marcha | Pasa | 877 fotogramas en 29.2 s (30.02 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena con las tres salidas en marcha | Pasa | 29.2 s de audio audio/opus en 29.2 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos con las tres salidas en marcha | Pasa | 15 segmentos nuevos en 29.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| Todas | El hub enseña las tres rutas como «enviando», con la evidencia que lo respalda | Pasa | RTMP: enviando (El proceso de envío manda 2 624 kb/s; Reconexiones desde que se activó: 0) · HLS: enviando (Receptores conectados: 4 (127.0.0.1); Salen 10 425 kb/s; MediaMTX tiene la señal en el path del destino) · WebRTC: enviando (Receptores conectados: 1 (127.0.0.1); Salen 2 593 kb/s; MediaMTX tiene la señal en el path del destino; Lee la señal convertida, que se está publicando) |
| RTMP | El hub detecta que el receptor se fue y lo enseña | Pasa | a los 2.8 s del corte pasa a «reconectando»: El hub está conectando con el destino |
| Todas | Durante el corte, el hub sigue enseñando HLS y WebRTC como «enviando» | Pasa | HLS: enviando · WebRTC: enviando |
| HLS | El navegador reproduce audio y vídeo durante los 60 s de corte | Pasa | reprodujo 58.4 s de 58.4 s; mayor parada 0.0 s; 1280×720; decodificó 18.3 MB de vídeo y 954 kB de audio |
| WebRTC | El navegador reproduce vídeo durante los 60 s de corte | Pasa | 1753 fotogramas en 58.4 s (30.01 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena durante los 60 s de corte | Pasa | 58.4 s de audio audio/opus en 58.4 s; mayor silencio 0.0 s |
| HLS | La lista del hub sigue sacando segmentos durante los 60 s de corte | Pasa | 30 segmentos nuevos en 59.1 s; mayor espera 1.0 s; 0 peticiones fallidas |
| RTMP | La salida se recupera sola al volver el receptor | Pasa | el receptor vuelve a tener señal a los 2.5 s y el hub marca «enviando» a los 6.8 s (reintenta cada 2 s) |
| HLS | El navegador reproduce audio y vídeo mientras la salida cortada se recupera | Pasa | reprodujo 18.1 s de 18.1 s; mayor parada 0.0 s; 1280×720; decodificó 5.7 MB de vídeo y 296 kB de audio |
| WebRTC | El navegador reproduce vídeo mientras la salida cortada se recupera | Pasa | 544 fotogramas en 18.1 s (30.02 fps); mayor parada 0.0 s; 1280×720 video/H264; 0 paquetes perdidos |
| WebRTC | El navegador recibe audio que suena mientras la salida cortada se recupera | Pasa | 18.1 s de audio audio/opus en 18.1 s; mayor silencio 0.0 s |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 5.3 s |
| Todas | Reiniciar los servicios del hub no corta ninguna salida | Pasa | RTMP: 0.0 s sin señal · HLS: 0.0 s sin señal · WebRTC: 0.0 s sin señal; los 2 ffmpeg de las rutas son los mismos procesos de antes; el supervisor anotó al arrancar que 2 seguían en marcha y que lanzó 0 |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Conversión de «Aceptación · fuente SRT», Envío a «Aceptación · servidor RTMP»); un solo ffmpeg por tarea |
| Todas | Tras el reinicio, el panel del hub vuelve a responder | Pasa | a los 11.2 s |
| Todas | Tras el reinicio, las rutas que estaban activas vuelven solas | Pasa | RTMP: 7.5 s sin señal · HLS: 10.1 s sin señal · WebRTC: 20.3 s sin señal; ahora: RTMP enviando, HLS enviando, WebRTC enviando; páginas de los reproductores recargadas 1 vez |
| Todas | Tras el reinicio no hay publicaciones ni procesos duplicados | Pasa | 2 procesos del hub, los mismos que antes (Conversión de «Aceptación · fuente SRT», Envío a «Aceptación · servidor RTMP»); un solo ffmpeg por tarea |
| RTMP | El receptor tiene audio y vídeo antes del corte | Pasa | vídeo H264 1280×720, 1405 fotogramas en 46.8 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMP | El receptor tiene audio y vídeo tras recuperarse | Pasa | vídeo H264 1280×720, 1154 fotogramas en 38.5 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

### El corte del receptor RTMP

| Momento | Hora | Qué pasó |
|---|---|---|
| Corte | 15:00:58 | El receptor RTMP desaparece sin despedirse |
| El hub lo enseña | +2.8 s | La ruta pasa a «reconectando»: El hub está conectando con el destino. Evidencia: El proceso de envío está en marcha y aún no manda datos; Intentos seguidos sin conseguirlo: 4 |
| Vuelve el receptor | 15:01:58 | A los 60 s del corte |
| Vuelve la señal al receptor | +2.5 s | El hub reintenta cada 2 s. En total el receptor estuvo 62.6 s sin recibir |
| El hub marca «enviando» | +6.8 s | |

Estados que fue enseñando el hub en cada ruta:

- RTMP: 15:00:56 enviando → 15:01:01 reconectando → 15:02:05 enviando
- HLS: 15:00:56 enviando
- WebRTC: 15:00:56 enviando

### Reinicio de los servicios del hub con las rutas activas

- Orden: `tests/banco/banco.sh supervisor`. El panel volvió a responder a los 5.3 s.
- Tiempo sin señal en cada salida: RTMP 0.0 s, HLS 0.0 s, WebRTC 0.0 s.
- En su primera vuelta tras arrancar, el supervisor encontró 2 procesos en marcha (los conserva) y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 2 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 0 veces.

### Reinicio completo con las rutas activas

- Orden: `tests/banco/banco.sh reiniciar`. El panel volvió a responder a los 11.2 s.
- Tiempo sin señal en cada salida: RTMP 7.5 s, HLS 10.1 s, WebRTC 20.3 s.
- En su primera vuelta tras arrancar, el supervisor encontró 0 procesos en marcha y lanzó 0; los demás los lanza cuando su fuente vuelve a tener señal. De los ffmpeg de las rutas, 0 de 2 son el mismo proceso de antes.
- La cámara de pruebas tuvo que volver a publicar 2 veces y las páginas de los reproductores se recargaron 1 vez.

### Carga con las tres salidas en marcha (30 s, 15 lecturas)

- Uso de CPU de todo el equipo: 29.1 % de media, 32.8 % de máximo (de 4 núcleos). Incluye la cámara y los reproductores de la prueba cuando corren en la misma máquina.
- Carga media del sistema (1 min): 0.8 de media, 0.9 de máximo.
- Memoria: 15007 MB disponibles de 16087 MB.
- Sin tarjeta NVIDIA a la vista (nvidia-smi no responde): las conversiones de vídeo irían por CPU.

| Proceso del hub | CPU (100 % = un núcleo) | Memoria |
|---|---|---|
| MediaMTX (recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC) | 19.7 % de media, 21 % de máximo | 77.5 MB de media, 83.5 MB de máximo |
| Conversión de «Aceptación · fuente SRT» | 4.2 % de media, 5 % de máximo | 69.5 MB de media, 69.5 MB de máximo |
| Envío a «Aceptación · servidor RTMP» | 2 % de media, 2.5 % de máximo | 67.4 MB de media, 67.4 MB de máximo |

HLS y WebRTC no llevan proceso propio: los sirve MediaMTX, y su consumo es el de la fila de MediaMTX.

## Las claves

La prueba usó 6 claves (la de publicación de cada fuente, la passphrase SRT, la clave RTMP y la de lectura de WebRTC) y las buscó después:

- Registros mirados: .banco (535 archivos)
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
