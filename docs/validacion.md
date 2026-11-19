# Qué está comprobado y con qué

Que el hub sepa montar una ruta no quiere decir que esté probada. Cada combinación de entrada y
salida tiene uno de tres niveles, que el hub guarda y enseña en cada ruta:

| Nivel | Qué significa | Quién lo anota |
|---|---|---|
| Pendiente de validación | Nadie la ha comprobado todavía. | Es el nivel de partida de todas. |
| Verificada en el banco de pruebas | Pasó señal de verdad por ella, con programas haciendo de cámara y de receptor. | `scripts/aceptacion.py` y `scripts/combinaciones.py`, al pasar. |
| Verificada con equipos reales | Pasó en la estación con la cámara, el receptor o la plataforma de verdad. | Un administrador, diciendo con qué equipo. |

Una prueba de banco posterior nunca rebaja lo verificado con equipos reales. Para retirar un nivel
se vuelve a anotar «pendiente». La tabla completa (120 combinaciones) sale de `GET /api/validacion`.
Los niveles se guardan en cada instalación: una estación recién instalada lo tiene todo pendiente
hasta que las pruebas se pasan en ella.

## Cómo está hoy

Verificadas en el banco de pruebas: las 42 combinaciones que hay entre SRT, RTMP, HLS y WebRTC. Cada
una de las seis formas de entrada se conectó a la vez a las siete formas de salida, con audio y
vídeo comprobados en cada una; es el caso `matriz` de la prueba de las otras formas de conexión
([combinaciones-banco.md](combinaciones-banco.md)). Las seis marcadas con una A son además las de la
prueba de aceptación ([aceptacion-banco.md](aceptacion-banco.md)), con su corte de 60 s y sus
reinicios.

| Entrada | SRT, el hub llama al receptor | SRT, el receptor llama al hub | RTMP, el hub publica en un servidor | RTMP, un cliente lee del hub | HLS, el reproductor pide la lista | WebRTC, un navegador la pide (WHEP) | WebRTC, el hub la reenvía (WHIP) |
|---|---|---|---|---|---|---|---|
| SRT, el codificador llama al hub | Sí | Sí | Sí, A | Sí | Sí, A | Sí, A | Sí |
| SRT, el hub llama al origen | Sí | Sí | Sí | Sí | Sí | Sí | Sí |
| RTMP, el codificador publica en el hub | Sí, A | Sí | Sí | Sí | Sí, A | Sí, A | Sí |
| RTMP, el hub lee del servidor de origen | Sí | Sí | Sí | Sí | Sí | Sí | Sí |
| HLS, el hub lee la lista del origen | Sí | Sí | Sí | Sí | Sí | Sí | Sí |
| WebRTC, el navegador publica en el hub (WHIP) | Sí | Sí | Sí | Sí | Sí | Sí | Sí |

En el banco, el servidor RTMP y la lista HLS que el hub va a buscar los sirve el propio hub por otra
de sus salidas, el codificador SRT a la escucha es un ffmpeg y la entrada WebRTC la publica un
Chromium sin ventana, a 640×360.

La misma prueba de las formas de conexión comprobó en el banco, además:

- SRT con stream ID, con passphrase y con puerto propio, de entrada y de salida.
- RTMPS en los cuatro sentidos: un codificador publica cifrado en el hub, un cliente lee cifrado del
  hub, el hub publica en un servidor y el hub lee de un servidor. Los certificados eran de pruebas,
  firmados por el propio banco.
- Un destino con su resolución y su bitrate (conversión con libx264) y una sola conversión
  compartida por dos destinos con el mismo perfil.
- H.264 con fotogramas B: en copia hacia HLS y convertido hacia WebRTC.
- La selección de pistas de audio por destino, con una fuente de dos pistas.
- El corte de la fuente con tres rutas activas, la política de reconexión con tope de intentos y el
  cambio de la fuente de un destino al aire, con su confirmación y sus permisos.

Verificadas con equipos reales: **ninguna todavía.** La prueba en la HP Z8 con señales reales está
pendiente de que se apruebe el relevo.

Pendientes de validación:

- Todo lo que el banco no puede decir: codificadores, receptores y plataformas de verdad, la red
  de por medio y la carga de la estación (ver [banco-de-pruebas.md](banco-de-pruebas.md)).
- Una fuente HLS durante muchas horas. El hub la lee al ritmo de su propio reloj, con un margen de
  tres segmentos. Si el reloj del origen va algo más despacio que el de la estación, es de esperar
  que ese margen se vaya gastando, y si va más deprisa, que el retardo crezca. En el banco, con
  pruebas de minutos, no se nota: no está medido cuánto tarda en notarse con un origen real ni cómo
  se recupera.
- RTMPS con un certificado de verdad y contra plataformas reales.
- WebRTC y HLS fuera de la red local: HTTPS por delante y, en WebRTC, STUN o TURN.
- Las conversiones de vídeo con NVENC, y con señales 1080i o 1080p59,94.
- MediaMTX no informa de si un destino WHIP recibe lo que se le reenvía: el hub lo enseña como
  «enviando, sin confirmar». En el banco se comprobó mirando el otro servidor.
- Los flujos de la versión anterior (Hub channel, Recorder, SRT) usados como fuente de una ruta.
- NDI y Zixi, de entrada y de salida (ver más abajo).

Dos límites conocidos de la entrada por HLS:

- El hub la lee tres segmentos por detrás del directo, para que salga a ritmo constante hacia SRT,
  RTMP y WebRTC. Con segmentos de 2 s son unos 6 s de retardo añadidos a los que ya trae la lista.
- Si la lista acaba de nacer, la fuente espera a que tenga esos tres segmentos antes de empezar a
  leerla, y lo dice en su estado. En el banco, con segmentos de 2 s, son unos 4 s.

Dos límites conocidos de la entrada por WebRTC:

- El vídeo tiene que llegar en H.264. VP8, VP9 y AV1 no caben en el enlace interno del hub: el plan
  lo dice y esa pista no sale. OBS publica en H.264; a un navegador hay que pedírselo.
- Un navegador cambia la resolución sobre la marcha según su ancho de banda. El hub lo sigue cuando
  el tamaño nuevo lleva 10 s estable, y las conversiones entregan siempre el tamaño de su perfil.

## Prueba con equipos reales

Se hace en la estación, con la ruta tal como va a trabajar. Para cada combinación que se quiera dar
por buena:

1. Crear la fuente y el destino con los equipos de verdad: la cámara o el codificador, y el
   decodificador, la plataforma o el reproductor que la va a recibir.
2. Antes de activar, leer el plan: qué pistas se copian, cuáles se convierten y por qué. Comprobar
   que coincide con lo que se espera de esa señal (1080i, varios audios, fotogramas B).
3. Activar la ruta y comprobar en el destino imagen y sonido, no solo que «conecta»: movimiento
   continuo, sincronía de labios y el audio correcto en cada pista.
4. Dejarla al menos 30 minutos y mirar en Monitoreo el bitrate, las reconexiones y, en SRT, las
   pérdidas y el RTT.
5. Cortar el receptor 60 segundos (apagarlo o desconectarle la red) y comprobar que el hub lo enseña
   como «reconectando», que las demás salidas de esa fuente siguen, y que al volver se recupera solo.
6. Cortar la fuente 60 segundos y comprobar que las rutas pasan a «esperando fuente» y vuelven.
7. Apuntar la carga del equipo con la ruta en marcha (`GET /api/carga`, o Monitoreo).
8. Anotarla como verificada, diciendo con qué equipo:

```
curl -X PUT http://127.0.0.1:8095/api/validacion -H 'Content-Type: application/json' \
     -H "Cookie: msr_sesion=$MSXS_SESION" \
     -d '{"fuente": "srt:origen", "destino": "rtmp:hub", "nivel": "real",
          "nota": "Codificador Haivision Makito X hacia YouTube, 2026-11-25, 45 min"}'
```

El lado de la fuente se escribe `protocolo:origen` (el origen llama al hub) o `protocolo:hub` (el
hub va a buscarla), y `flujo:hub`, `flujo:recorder` o `flujo:pull` para los flujos heredados. El
lado del destino, `protocolo:hub` (el hub se conecta) o `protocolo:receptor` (el hub entrega una
dirección). Cuando la interfaz esté construida se anotará desde Ajustes.

## NDI y Zixi

Ni MediaMTX ni FFmpeg hablan NDI o Zixi. Cada uno entra por un adaptador que depende de un
componente con licencia propia, que no se puede distribuir con el Streamer:

| Adaptador | Componente que hace falta | Cómo se conecta al hub |
|---|---|---|
| NDI | SDK de NDI para Linux (`libndi.so`, de Vizrt NDI AB), que se descarga de ndi.video aceptando su licencia, y GStreamer con el plugin `ndi` (de gst-plugins-rs). Para descubrir fuentes en la red local, avahi (mDNS) o un servidor de descubrimiento NDI. | Un puente convierte entre NDI y MPEG-TS y lo intercambia con MediaMTX por SRT local. |
| Zixi | Zixi Broadcaster u otro componente de Zixi con su licencia. | El hub intercambia MPEG-TS con ese componente por una dirección local; es él quien recibe o entrega por Zixi, con los modos y límites de su licencia. |

Hoy el hub detecta si cada componente está instalado y dice exactamente qué falta
(`GET /api/adaptadores`); las fuentes y destinos NDI o Zixi se pueden crear y quedan en espera con
ese requisito a la vista, sin afectar al resto. El paso de señal de cada adaptador se termina y se
valida con el componente instalado y un equipo real delante. Hasta entonces ninguna ruta NDI o Zixi
figura como verificada, ni en el banco ni con equipos reales.

Para avanzar hace falta, de cada uno: el SDK o el componente instalado en la estación (o el acceso
para instalarlo), su licencia y un equipo que emita o reciba con el que probar.
