# Qué está comprobado y con qué

Que el hub sepa montar una ruta no quiere decir que esté probada. Cada combinación de entrada y
salida tiene uno de tres niveles, que el hub guarda y enseña en cada ruta:

| Nivel | Qué significa | Quién lo anota |
|---|---|---|
| Pendiente de validación | Nadie la ha comprobado todavía. | Es el nivel de partida de todas. |
| Verificada en el banco de pruebas | Pasó la prueba de aceptación con programas haciendo de cámara y de receptor. | `scripts/aceptacion.py`, al pasar. |
| Verificada con equipos reales | Pasó en la estación con la cámara, el receptor o la plataforma de verdad. | Un administrador, diciendo con qué equipo. |

Una prueba de banco posterior nunca rebaja lo verificado con equipos reales. Para retirar un nivel
se vuelve a anotar «pendiente». La tabla completa (120 combinaciones) sale de `GET /api/validacion`.

## Cómo está hoy

Verificadas en el banco de pruebas (ver [aceptacion-banco.md](aceptacion-banco.md)):

| Entrada | Salida |
|---|---|
| RTMP, el codificador publica en el hub | SRT, el hub llama al receptor |
| RTMP, el codificador publica en el hub | HLS, el reproductor pide la lista al hub |
| RTMP, el codificador publica en el hub | WebRTC, el navegador pide la señal al hub (WHEP) |
| SRT, el codificador llama al hub | RTMP, el hub publica en el servidor del destino |
| SRT, el codificador llama al hub | HLS, el reproductor pide la lista al hub |
| SRT, el codificador llama al hub | WebRTC, el navegador pide la señal al hub (WHEP) |

Verificadas con equipos reales: **ninguna todavía.** La prueba en la HP Z8 con señales reales está
pendiente de que se apruebe el relevo.

Pendientes de validación, aunque el motor las monta y tienen pruebas unitarias:

- SRT con el hub llamando al origen, y SRT con el receptor llamando al hub (con stream ID o con
  puerto propio).
- RTMP con el hub leyendo de un servidor de origen, y RTMP entregado por el hub a un cliente.
- RTMPS, de entrada y de salida. Depende de que el ffmpeg de la estación tenga TLS y, para recibir
  RTMPS, de dar un certificado a MediaMTX.
- HLS de entrada (leer un `.m3u8` con sus variantes y pistas de audio).
- WebRTC de entrada por WHIP y el reenvío a un destino WHIP. MediaMTX no informa de si el destino
  WHIP recibe: el hub lo enseña como «enviando, sin confirmar».
- WebRTC y HLS fuera de la red local: HTTPS por delante y, en WebRTC, STUN o TURN.
- Conversiones de vídeo (otra resolución o bitrate, y H.264 con fotogramas B hacia WebRTC), con
  libx264 y con NVENC.
- Los flujos de la versión anterior (Hub channel, Recorder, SRT) usados como fuente de una ruta.
- NDI y Zixi, de entrada y de salida (ver más abajo).

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
