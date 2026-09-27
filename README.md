# Media Syntaxis Streamer

Hub SRT de la suite Media Syntaxis: recibe feeds, los enruta y los distribuye a varios destinos.

**Fase 1 (esta versión):** entradas desde un canal de MSX Recorder del mismo equipo o por SRT pull;
salidas SRT (caller o listener), RTMP y HLS, todas en copia (sin recodificar). La composición con
reencuadre y capas HTML5 llega en la fase 2.

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
el flujo también), `http://<equipo>:8888/<path>/` responde; al detenerlo o borrarlo, deja de
responder a las lecturas nuevas. MediaMTX consulta cada lectura al panel (`authMethod: http` →
`/api/mtx/auth`, solo desde el propio equipo), y como `hlsAlwaysRemux` está apagado, el HLS solo se
genera mientras alguien lo mira. La vista previa del panel usa un token temporal (1 h) y funciona
aunque no haya destino HLS. Si el panel está parado no se abren lecturas nuevas (las salidas ya
conectadas siguen); el supervisor reintenta solo.

## Despliegue

```
scripts/desplegar.sh z8 --instalar   # primera vez: MediaMTX, entorno, config y servicios
scripts/desplegar.sh z8              # actualizaciones: las salidas no se cortan
scripts/desplegar.sh z8 --mediamtx   # además, mediamtx.yml y unidades nuevas (corte de segundos)
```

Todo queda en `/home/mediasat/streamer`. Puertos: panel 8095, HLS 8888, SRT 8890 (UDP), API de
MediaMTX 9997 (solo local). La configuración de la máquina está en `config.json`
(ver `config/config.ejemplo.json`); `clave_panel` protege el panel.
