# Las demás formas de conexión del hub · banco de pruebas (contenedor de desarrollo, no la HP Z8)

> Informe que escribe `scripts/combinaciones.py`, de la pasada completa en el banco de pruebas con el código de
> esta rama (2026-11-19). No es la HP Z8: las pruebas con equipos reales están pendientes (ver
> [banco-de-pruebas.md](banco-de-pruebas.md) y [validacion.md](validacion.md)).

- **Cuándo:** 2026-11-19 10:25 UTC
- **Dónde corrió el hub:** banco de pruebas (contenedor de desarrollo, no la HP Z8) · vm · Intel(R) Xeon(R) Processor @ 2.10GHz · 4 núcleos
- **Hub:** Streamer 0.4.0-alfa · MediaMTX v1.21.1 · FFmpeg 6.1.1-3ubuntu5 · conversiones de vídeo con libx264
- **Con qué se comprobó:** la cámara y los receptores son programas (ffmpeg y un Chromium sin ventana). Donde hace falta «otro equipo» que sirva o reciba la señal (otro hub, un servidor RTMP, una lista HLS, un servidor WHIP) lo hace este mismo hub por otra de sus salidas o entradas; la plataforma RTMPS es un segundo MediaMTX con un certificado de pruebas. Es una verificación de banco de pruebas, no con equipos reales.
- **Cómo se manejó el hub:** por la API del panel, con una sesión de administrador (http://127.0.0.1:8095).

Complementa a la prueba de aceptación ([aceptacion-banco.md](aceptacion-banco.md)), que cubre la entrada RTMP y la entrada SRT con sus salidas SRT, RTMP, HLS y WebRTC, el corte de 60 s y los reinicios.

## Resultado

| Qué | Resultado |
|---|---|
| Lo que el hub entrega para que otro equipo lo abra (SRT y RTMP) | Pasa |
| Otras formas de recibir la fuente: el hub va a buscarla (SRT, RTMP, HLS) o la espera en un puerto propio | Pasa |
| RTMPS: el hub recibe y entrega cifrado, publica en una plataforma y lee de un servidor | Pasa |
| WebRTC: WHIP de entrada y reenvío WHIP | Pasa |
| Un destino con su resolución y su bitrate, y una conversión compartida | Pasa |
| H.264 con fotogramas B hacia WebRTC y hacia HLS | Pasa |
| Selección de pistas de audio por destino | Pasa |
| Corte de la fuente durante 20 s con tres rutas activas | Pasa |
| Política de reconexión con tope de intentos | Pasa |
| Cambiar la fuente de un destino al aire | Pasa |
| Las claves de la prueba no aparecen en los registros ni en lo que devuelve la API | Pasa |

## Lo que el hub entrega para que otro equipo lo abra (SRT y RTMP)

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| SRT con stream ID y passphrase: sin nadie conectado, el hub lo enseña como «esperando receptor» | Pasa | MediaMTX tiene la señal en el path del destino; MediaMTX no tiene receptores en el path del destino |
| SRT con stream ID y passphrase: el equipo que llama al hub recibe audio y vídeo | Pasa | vídeo H264 1280×720, 407 fotogramas en 15.2 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT con stream ID y passphrase: mientras lee, el hub lo enseña como «enviando» y dice qué lo respalda | Pasa | «enviando»: Receptores conectados: 1 (127.0.0.1); Salen 2 578 kb/s; MediaMTX tiene la señal en el path del destino |
| SRT con puerto propio: sin nadie conectado, el hub lo enseña como «esperando receptor» | Pasa | El proceso de envío escucha en el puerto del destino |
| SRT con puerto propio: el equipo que llama al hub recibe audio y vídeo | Pasa | vídeo H264 1280×720, 452 fotogramas en 15.5 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT con puerto propio: mientras lee, el hub lo enseña como «enviando» y dice qué lo respalda | Pasa | «enviando»: El proceso de envío manda 2 718 kb/s; Reconexiones desde que se activó: 0 |
| RTMP: sin nadie conectado, el hub lo enseña como «esperando receptor» | Pasa | MediaMTX tiene la señal en el path del destino; MediaMTX no tiene receptores en el path del destino |
| RTMP: el equipo que llama al hub recibe audio y vídeo | Pasa | vídeo H264 1280×720, 349 fotogramas en 12.9 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMP: mientras lee, el hub lo enseña como «enviando» y dice qué lo respalda | Pasa | «enviando»: Receptores conectados: 1 (127.0.0.1); Salen 2 551 kb/s; MediaMTX tiene la señal en el path del destino |

## Otras formas de recibir la fuente: el hub va a buscarla (SRT, RTMP, HLS) o la espera en un puerto propio

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| SRT, el hub llama a un codificador a la escucha: el hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| SRT, el hub llama a un codificador a la escucha → SRT: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 457 fotogramas en 15.4 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT, el hub llama a otro hub con stream ID y passphrase: el hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| SRT, el hub llama a otro hub con stream ID y passphrase → HLS: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 300 fotogramas en 10.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMP, el hub lee de un servidor: el hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| RTMP, el hub lee de un servidor → RTMP: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 350 fotogramas en 12.3 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| HLS, el hub lee una lista .m3u8: el hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| HLS, el hub lee una lista .m3u8 → SRT: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 458 fotogramas en 15.4 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| SRT, el codificador llama a un puerto propio del hub: el hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| SRT, el codificador llama a un puerto propio del hub → RTMP: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 346 fotogramas en 11.8 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

## RTMPS: el hub recibe y entrega cifrado, publica en una plataforma y lee de un servidor

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| RTMPS de entrada: «Probar la conexión» de la fuente confirma que el hub atiende RTMPS | Pasa | MediaMTX (RTMPS) escucha en el puerto 1946 |
| RTMPS, el codificador publica cifrado en el hub: el hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| RTMPS, el codificador publica cifrado en el hub → SRT: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 464 fotogramas en 15.6 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMPS de entrega: quien lee del hub por RTMPS recibe audio y vídeo | Pasa | vídeo H264 1280×720, 353 fotogramas en 13.3 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMPS de entrega: mientras lee, el hub lo enseña como «enviando» y sabe que el lector va por RTMPS | Pasa | «enviando»: Receptores conectados: 1 (127.0.0.1); Salen 2 584 kb/s; MediaMTX tiene la señal en el path del destino; lectores por RTMPS |
| RTMPS: «Probar la conexión» llega hasta el saludo RTMP y avisa de que el certificado de pruebas no es de fiar | Pasa | El certificado del servidor no es válido (self-signed certificate). El hub emite igualmente y cifrado, pero sin garantía de que el servidor sea quien dice ser |
| RTMPS de salida: lo que el hub publica en la plataforma llega con audio y vídeo | Pasa | vídeo H264 1280×720, 352 fotogramas en 11.8 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMPS de salida: el hub lo enseña como «enviando» y dice qué lo respalda | Pasa | «enviando»: El proceso de envío manda 2 590 kb/s; Reconexiones desde que se activó: 0 |
| RTMPS de entrada, el hub lee de un servidor: el hub recibe la fuente y sabe qué pistas trae | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| RTMPS de entrada, el hub lee de un servidor → SRT: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 455 fotogramas en 15.8 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

## WebRTC: WHIP de entrada y reenvío WHIP

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| WHIP: «Probar la conexión» dice que el hub ya escucha en la dirección que entrega | Pasa | a los 0.2 s de crear la fuente: MediaMTX (WebRTC) escucha en el puerto 8889 |
| WHIP: el hub recibe lo que publica un navegador y sabe qué pistas trae | Pasa | H.264 · 320×180 · 20 fps; Opus · mono · 48 kHz |
| WHIP desde un navegador → SRT: el plan dice qué copia y qué convierte | Pasa | vídeo: copia; audio: copia |
| WHIP desde un navegador → SRT: llegan audio y vídeo | Pasa | vídeo H264 640×360, 299 fotogramas en 15.4 s; audio OPUS 1 canales 48000 Hz, volumen medio -24.1 dB |
| WHIP desde un navegador → RTMP: el plan dice qué copia y qué convierte | Pasa | vídeo: copia; audio: convierte (RTMP no admite Opus: se convierte a AAC.) |
| WHIP desde un navegador → RTMP: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 242 fotogramas en 13.4 s; audio AAC 1 canales 48000 Hz, volumen medio -24.1 dB |
| WHIP: cuando el navegador sube la resolución, el hub lo refleja | Pasa | al inspeccionarla era H.264 · 320×180 · 20 fps; el navegador envía ahora 1280×720 y el hub enseña H.264 · 1280×720 · 20 fps |
| Reenvío WHIP: el plan copia el vídeo y convierte el audio a Opus | Pasa | vídeo: copia; audio: convierte (WebRTC no admite AAC: se convierte a Opus.) |
| Reenvío WHIP: el otro servidor recibe la señal | Pasa | H.264 · 1280×720 · 30 fps; Opus · estéreo · 48 kHz. El hub lo enseña como «enviando»: MediaMTX reenvía la señal al destino por WHIP |
| WHIP → WHEP: un navegador reproduce audio y vídeo de lo que entró por WHIP | Pasa | 271 fotogramas en 9.1 s (29.9 fps), mayor parada 0.0 s; 1280×720 video/H264; audio audio/opus que suena, mayor silencio 0.0 s |

## Un destino con su resolución y su bitrate, y una conversión compartida

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| Antes de activar, el plan dice que el vídeo se convierte a 640×360 y por qué | Pasa | H.264 · 1280×720 · 30 fps → H.264 · 640×360 · 0,8 Mb/s · sin fotogramas B. El perfil «Aceptación · 360p · 800 kb/s» fija la resolución y el bitrate. Un codificador de vídeo a 640×360. |
| El plan del segundo destino avisa de que usa la conversión del primero | Pasa | Usa la conversión que ya alimenta a «Aceptación · destino SRT a 360p»: no añade carga. |
| SRT: llega a 640×360 y con el bitrate del perfil | Pasa | vídeo H264 640×360, 578 fotogramas en 19.9 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB; 1017 kb/s en total (el perfil pide 800 de vídeo y 96 de audio; la fuente trae 2500) |
| RTMP: llega a 640×360 y con el bitrate del perfil | Pasa | vídeo H264 640×360, 736 fotogramas en 25.2 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB; 1019 kb/s en total (el perfil pide 800 de vídeo y 96 de audio; la fuente trae 2500) |
| Los dos destinos comparten una sola conversión | Pasa | 1 conversión nueva para dos destinos |

## H.264 con fotogramas B hacia WebRTC y hacia HLS

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| El hub recibe la fuente con fotogramas B | Pasa | H.264 · 1280×720 · 30 fps |
| Hacia HLS el plan copia el vídeo: HLS admite fotogramas B | Pasa | vídeo: copia; audio: copia |
| Hacia WebRTC el plan convierte el vídeo y dice que es por los fotogramas B | Pasa | WebRTC no admite fotogramas B: el vídeo se vuelve a codificar sin ellos. Un codificador de vídeo a 1280×720. |
| HLS: llegan audio y vídeo, en copia | Pasa | vídeo H264 1280×720, 360 fotogramas en 12.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| WebRTC: un navegador reproduce audio y vídeo de la señal convertida | Pasa | 331 fotogramas en 11.1 s (30.0 fps), mayor parada 0.0 s; 1280×720 video/H264; audio audio/opus que suena, mayor silencio 0.0 s |

## Selección de pistas de audio por destino

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| El hub ve las dos pistas de audio de la fuente | Pasa | AAC · estéreo · 48 kHz; AAC · estéreo · 48 kHz |
| El plan del destino con la pista 2 dice cuál lleva y cuál se queda fuera, y por qué | Pasa | Se copian el vídeo y la pista 2 de audio: solo cambia el transporte. La pista 1 de audio no se envía. Pista 1: omite (El destino usa solo la pista 2.) Pista 2: copia (SRT acepta AAC tal como llega.) |
| Para HLS, que es el reproductor quien viene a por la señal, el hub prepara una señal con esa sola pista | Pasa | Se copian el vídeo y la pista 2 de audio: solo cambia el transporte. La pista 1 de audio no se envía. Solo se reparten las pistas: carga mínima. |
| «Todas»: llegan audio y vídeo | Pasa | vídeo H264 1280×720, 434 fotogramas en 15.6 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| «Todas»: llegan las dos pistas, cada una con su sonido | Pasa | volumen medio de cada pista: -24.1 dB, -36.1 dB (la segunda se emite 12 dB más baja) |
| «Pista 2» por SRT: llega solo la segunda pista | Pasa | vídeo H264 1280×720, 777 fotogramas en 27.1 s; audio AAC 2 canales 48000 Hz, volumen medio -36.1 dB; 1 pista de audio, a -36.1 dB (la primera va a -24.1 dB y la segunda a -36.1 dB) |
| «Pista 2» por HLS: llega solo la segunda pista | Pasa | vídeo H264 1280×720, 300 fotogramas en 10.0 s; audio AAC 2 canales 48000 Hz, volumen medio -36.1 dB; 1 pista de audio, a -36.1 dB (la primera va a -24.1 dB y la segunda a -36.1 dB) |

## Corte de la fuente durante 20 s con tres rutas activas

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| El hub recibe la fuente | Pasa | H.264 · 1280×720 · 30 fps; AAC · estéreo · 48 kHz |
| Las tres rutas de la fuente están en marcha (SRT en copia, RTMP con conversión y HLS) | Pasa | SRT en copia: «enviando»; RTMP con conversión a 360p: «enviando»; HLS: «esperando_receptor» |
| Al cortarse la fuente, el hub la enseña a ella y a sus tres rutas como «esperando fuente», y dice en qué se basa | Pasa | a los 0.7 s del corte: La fuente no tiene señal; Fuente: esperando fuente. Nadie está publicando en esta fuente |
| Con la fuente cortada no quedan procesos de envío ni de conversión dando vueltas | Pasa | 6 procesos antes del corte, 0 a los 20 s |
| Al volver la fuente, las tres rutas se recuperan solas | Pasa | a los 9.9 s de volver el codificador, tras 20 s de corte |
| SRT: tras la vuelta llegan audio y vídeo | Pasa | vídeo H264 1280×720, 460 fotogramas en 16.1 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| RTMP: tras la vuelta llega a 640×360, con la conversión otra vez en marcha | Pasa | vídeo H264 640×360, 502 fotogramas en 18.2 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB; con el perfil a 360p |
| HLS: tras la vuelta llegan audio y vídeo | Pasa | vídeo H264 1280×720, 360 fotogramas en 12.0 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |
| Tras la vuelta hay los mismos procesos que antes del corte: nada duplicado | Pasa | 6 antes del corte y 6 después |

## Política de reconexión con tope de intentos

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| Con el receptor apagado, el hub lo enseña como «reconectando» mientras lo intenta, y dice en qué se basa | Pasa | El hub está conectando con el destino; El proceso de envío está en marcha y aún no manda datos |
| Agotados los 3 intentos, el hub deja de intentarlo y lo enseña como «error», con el motivo | Pasa | a los 20 s: El hub dejó de intentarlo: pulsa Reintentar cuando el destino esté listo; Último intento: Se cortó la conexión. ffmpeg dijo: Error opening output files: Input/output error; Intentos seguidos sin conseguirlo: 3 |
| Aunque el receptor se encienda, el hub no vuelve a intentarlo por su cuenta | Pasa | 6 s después sigue en «error» y al receptor le han llegado 0 bytes |
| Al pedir «reintentar», la salida arranca y llegan audio y vídeo | Pasa | vídeo H264 1280×720, 418 fotogramas en 15.7 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB |

## Cambiar la fuente de un destino al aire

| Qué se comprobó | Resultado | Medida |
|---|---|---|
| El hub recibe la segunda cámara | Pasa | H.264 · 640×360 · 30 fps |
| El destino está al aire con la cámara principal | Pasa | «enviando»: El proceso de envío manda 2 306 kb/s; Reconexiones desde que se activó: 0 |
| Cambiar la fuente de un destino al aire sin confirmar no hace nada, y el hub dice cuál está al aire | Pasa | «Aceptación · destino al aire» ya recibe «Aceptación · cámara principal (SRT)» (enviando). Confirma el cambio para sustituirla. |
| Un operador no puede cambiar la fuente de un destino al aire | Pasa | Tu rol (operador) no puede modificar una ruta al aire: «Aceptación · destino al aire» está al aire con «Aceptación · cámara principal (SRT)» |
| Confirmando cuál se sustituye, el destino pasa a la segunda cámara | Pasa | vídeo H264 640×360, 376 fotogramas en 13.4 s; audio AAC 2 canales 48000 Hz, volumen medio -24.1 dB; la segunda cámara emite a 640×360 |
| El cambio queda en el registro, con quién lo hizo | Pasa | Cambió la fuente al aire: Aceptación · destino al aire (de «Aceptación · cámara principal (SRT)» a «Aceptación · segunda cámara (RTMP, 640×360)»), por admin@banco.invalid |

## Las claves

La prueba usó 35 claves y passphrases y las buscó después:

- Registros mirados: .banco (233 archivos)
- En los registros mirados no aparece ninguna.
- En las respuestas de la API (matriz, carga y registro de cambios): no aparece ninguna.

## Qué queda anotado como comprobado

| Entrada | Salida | Nivel |
|---|---|---|
| SRT (el codificador llama al hub) | SRT (el receptor llama al hub) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | RTMP (un cliente lee del hub) | Verificada en el banco de pruebas |
| SRT (el hub llama) | SRT (el hub llama) | Verificada en el banco de pruebas |
| SRT (el hub llama) | HLS (el reproductor pide la lista) | Verificada en el banco de pruebas |
| RTMP (el hub llama) | RTMP (el hub llama) | Verificada en el banco de pruebas |
| HLS (el hub lee la lista) | SRT (el hub llama) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | RTMP (el hub llama) | Verificada en el banco de pruebas |
| RTMP (el codificador publica en el hub) | SRT (el hub llama) | Verificada en el banco de pruebas |
| RTMP (el codificador publica en el hub) | RTMP (un cliente lee del hub) | Verificada en el banco de pruebas |
| RTMP (el hub llama) | SRT (el hub llama) | Verificada en el banco de pruebas |
| WebRTC (el origen publica por WHIP) | SRT (el hub llama) | Verificada en el banco de pruebas |
| WebRTC (el origen publica por WHIP) | RTMP (el hub llama) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | WebRTC (el hub reenvía por WHIP) | Verificada en el banco de pruebas |
| WebRTC (el origen publica por WHIP) | WebRTC (el navegador pide la señal, WHEP) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | SRT (el hub llama) | Verificada en el banco de pruebas |
| SRT (el codificador llama al hub) | HLS (el reproductor pide la lista) | Verificada en el banco de pruebas |

«Verificada en el banco de pruebas» quiere decir con programas haciendo de cámara y de receptor. Ninguna combinación queda como «verificada con equipos reales» hasta que un administrador lo anote tras probarla con ellos.
