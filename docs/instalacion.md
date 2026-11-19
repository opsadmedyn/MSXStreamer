# Instalación del Streamer con el hub de señales

El hub de señales es la versión 0.4 del Streamer: hereda sus flujos, la composición y las entradas
Hub channel y Recorder, y añade fuentes, destinos y rutas entre ellos. Se instala igual que el
Streamer, en `/home/mediasat/streamer`, con tres servicios de systemd:

| Servicio | Qué es | Proceso |
|---|---|---|
| `msxs-mediamtx` | MediaMTX: recibe las fuentes y sirve SRT, RTMP, HLS y WebRTC | `bin/mediamtx mediamtx.yml` |
| `msxs-supervisor` | Lanza y vigila los ffmpeg de envíos y conversiones, y declara los paths en MediaMTX | `/usr/bin/python3 supervisor.py` (solo biblioteca estándar) |
| `msxs-web` | Panel y API, solo en `127.0.0.1:8095`; se entra por el Caddy del Recorder (solo Tailscale) | `venv/bin/uvicorn servidor:app` |

```
/home/mediasat/streamer/
  bin/mediamtx          MediaMTX v1.21.1
  mediamtx.yml          su configuración (el hub enciende RTMP y WebRTC por la API cuando hacen falta)
  config.json           la de esta máquina (ver más abajo)
  streamer.db           fuentes, destinos, rutas, ajustes y claves; solo la lee mediasat (0600)
  streamer/             el código
  venv/                 Python del panel (FastAPI y uvicorn, versiones de requirements.lock)
  logs/  run/           registros de cada ffmpeg y su progreso
  releases/v<versión>/  cada versión desplegada, tal como llegó
```

## Qué hace falta

- Linux x86-64 con systemd y Python 3.10 o posterior con `venv` (la EZC80 tiene 3.10.12).
- El usuario `mediasat`, y `sudo` para quien instala.
- `ffmpeg` y `ffprobe` con libsrt, libx264 y libopus; con una tarjeta NVIDIA, además `h264_nvenc`,
  que el hub usa sola si funciona. En la EZC80 `ffmpeg` está en `/usr/local/bin` (FFmpeg 7.1); hay
  que comprobar que `ffprobe` esté a su lado o decir dónde está en `config.json`. Para comprobarlo:

  ```
  ffmpeg -hide_banner -protocols | grep -w srt
  ffmpeg -hide_banner -encoders | grep -E 'libx264|libopus|h264_nvenc'
  ```

- `curl` y `rsync` en la estación, y salida a GitHub la primera vez (descarga de MediaMTX).
- Para la prueba de aceptación: Node con Playwright y su Chromium. En la EZC80 ya están, en el
  runtime del compositor (`/home/mediasat/streamer/runtime`).

## Instalar en una estación nueva

Desde una copia del repositorio, con un alias de SSH a la estación:

```
scripts/desplegar.sh <alias ssh> --instalar
```

Comprueba que los puertos 8095, 8888, 8890 y 9997 estén libres, descarga MediaMTX v1.21.1 y verifica
su SHA-256, crea el entorno de Python con las versiones de `requirements.lock`, copia el código,
crea `mediamtx.yml` y `config.json` desde los ejemplos si no existen, sube el búfer UDP del sistema
(`net.core.rmem_max`, que MediaMTX necesita) e instala y arranca los tres servicios.

Después hay que revisar `config.json` (como mínimo `host_publico` y los puertos de la tabla de
abajo) y reiniciar el panel y el supervisor: `sudo systemctl restart msxs-supervisor msxs-web`.

### Lo mismo, a mano

```
sudo -u mediasat bash -c '
  set -e
  D=/home/mediasat/streamer
  mkdir -p $D/bin $D/logs $D/run $D/streamer
  cd /tmp
  F=mediamtx_v1.21.1_linux_amd64.tar.gz
  curl -fsSLO https://github.com/bluenviron/mediamtx/releases/download/v1.21.1/$F
  curl -fsSLO https://github.com/bluenviron/mediamtx/releases/download/v1.21.1/checksums.sha256
  grep "[ *]$F\$" checksums.sha256 | sha256sum -c -
  tar xzf $F mediamtx && install -m 755 mediamtx $D/bin/mediamtx && rm -f $F checksums.sha256 mediamtx
'
# desde la copia del repositorio, copiada a la estación (aquí en /tmp/msxstreamer):
sudo -u mediasat bash -c '
  set -e
  D=/home/mediasat/streamer R=/tmp/msxstreamer
  rsync -a $R/streamer/ $D/streamer/ && cp $R/VERSION $D/
  [ -f $D/mediamtx.yml ] || cp $R/config/mediamtx.yml $D/
  [ -f $D/config.json ]  || cp $R/config/config.ejemplo.json $D/config.json
  python3 -m venv $D/venv && $D/venv/bin/pip install -q -r $R/requirements.lock
'
echo 'net.core.rmem_max=16777216' | sudo tee /etc/sysctl.d/60-msx-streamer.conf
sudo sysctl -p /etc/sysctl.d/60-msx-streamer.conf
sudo cp /tmp/msxstreamer/systemd/msxs-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now msxs-mediamtx msxs-supervisor msxs-web
curl -s http://127.0.0.1:8095/api/version
```

## config.json

| Clave | Para qué | Ejemplo |
|---|---|---|
| `host_publico` | Nombre o IP con que otros equipos llegan al hub; va en las direcciones que entrega | `recorder2.mediasyntaxis.com` |
| `ffmpeg`, `ffprobe` | Dónde están (si falta `ffprobe`, se busca junto a `ffmpeg`) | `/usr/local/bin/ffmpeg` |
| `codificador` | `auto` (NVENC si funciona, si no libx264), `h264_nvenc` o `libx264` | `auto` |
| `mediamtx_api` | API de MediaMTX; solo local | `http://127.0.0.1:9997` |
| `mediamtx_srt` | Dónde escucha el SRT de MediaMTX (su puerto es el SRT del hub) | `srt://127.0.0.1:8890` |
| `hls_puerto` | Puerto HLS de MediaMTX | `8888` |
| `rtmp_puerto` | Puerto RTMP del hub. **En la EZC80 no puede ser 1935**: lo usa Restreamer | `1945` |
| `webrtc_puerto`, `webrtc_udp` | Señalización WebRTC (TCP) y medios (UDP) | `8889`, `8189` |
| `hls_url`, `webrtc_url` | Dirección pública HTTPS de HLS y de WebRTC, si hay un proxy delante (ver más abajo) | `https://hls.ejemplo.com` |
| `panel_puerto` | Puerto del panel (solo para avisar si una fuente lo pide) | `8095` |
| `adaptadores` | NDI y Zixi: `{"ndi": {"activo": false, "libndi": ""}, "zixi": {"activo": false, "api": ""}}` | |
| `recorder_canales`, `recorder_config`, `composicion_max`, `hub_tokens` | Los de la 0.3.9, sin cambios | |

Los puertos y direcciones públicas también se cambian desde Ajustes (`PUT /api/ajustes/red`), que
manda sobre `config.json`. El hub no pide a MediaMTX un puerto que tenga otro programa: deja ese
servidor apagado y lo dice en el estado de las fuentes y destinos afectados.

## Arranque automático

Los tres servicios quedan habilitados en systemd (`systemctl enable`) y se reinician solos si caen
(`Restart=always`). Al encender la estación:

1. Arranca MediaMTX con su `mediamtx.yml`.
2. El supervisor lee la base de datos, vuelve a declarar en MediaMTX los paths de las fuentes y
   destinos, enciende RTMP y WebRTC si alguna ruta los usa, y relanza los envíos y conversiones de
   las rutas que estaban habilitadas en cuanto su fuente vuelve a tener señal.
3. Antes de lanzar nada busca los ffmpeg que sigan en marcha y los adopta, así que nunca hay dos
   procesos ni dos publicaciones por la misma ruta. Lo que hizo al arrancar queda en
   `GET /api/matriz` → `sistema.arranque` (cuántos siguieron, cuántos lanzó).

Reiniciar solo el supervisor y el panel (lo que hace un despliegue normal) no corta ninguna salida:
`KillMode=process` deja vivos los ffmpeg y el supervisor los adopta al volver. En el banco de
pruebas, con tres salidas activas: 0 s sin señal al reiniciar los servicios, y tras un reinicio
completo las rutas volvieron solas en unos 9 s (RTMP y HLS) y 19 s (WebRTC, tras recargar la página
del reproductor), sin duplicados ([aceptacion-banco.md](aceptacion-banco.md)).

## Puertos

| Puerto | Quién lo usa | Debe llegar desde |
|---|---|---|
| 8095/tcp | Panel y API | Solo `127.0.0.1` (el Caddy del Recorder, por Tailscale) |
| 9997/tcp | API de MediaMTX | Solo `127.0.0.1` |
| 8890/udp | SRT del hub (fuentes que llaman y receptores que llaman, por stream ID) | Los equipos que envían o reciben SRT |
| Los que se elijan, UDP | SRT con puerto propio de una fuente o destino | Ese equipo |
| `rtmp_puerto`/tcp | RTMP del hub (fuentes que publican, clientes que leen) | Los codificadores y lectores RTMP |
| 8888/tcp | HLS | Los reproductores (o solo el proxy HTTPS) |
| 8889/tcp | WebRTC: WHIP y WHEP | Los navegadores (o solo el proxy HTTPS) |
| 8189/udp | WebRTC: los medios | Los navegadores, salvo que todo vaya por TURN |

Los envíos que hace el hub (SRT o RTMP hacia un receptor) salen hacia el puerto del receptor y no
necesitan nada abierto en la estación. Para ver qué está ocupado antes de instalar:

```
ss -Hltnu | grep -E ':(8095|9997|8890|8888|8889|8189|1945)\b'
```

## HLS y WebRTC fuera de la red local

Pendiente de validar en la red de la EZC80. Lo previsto:

- **HTTPS por delante.** Un proxy inverso con certificado (el Caddy del Recorder u otro) sirve HLS
  y WebRTC con nombres propios, y el hub entrega esas direcciones si se le dicen en `hls_url` y
  `webrtc_url`. Con Caddy, por ejemplo:

  ```
  hls.ejemplo.com {
      reverse_proxy 127.0.0.1:8888
  }
  webrtc.ejemplo.com {
      reverse_proxy 127.0.0.1:8889
  }
  ```

  Con un nombre propio para cada uno no hay que reescribir rutas en el proxy.
- **WebRTC necesita además que lleguen los medios.** La señalización va por HTTPS, pero los medios
  van por UDP al puerto `webrtc_udp`. Si la estación está detrás de NAT hay que anunciar su IP
  pública y abrir ese puerto, o usar un servidor TURN. Las dos cosas se dicen en Ajustes
  (`PUT /api/ajustes/webrtc` con `hosts` y la lista `ice` de servidores STUN y TURN, cada uno con su
  usuario y credencial). Las credenciales de TURN se guardan como las demás claves.

## Relevo de la 0.3.9 en la EZC80

Se hace cuando estén aprobados el mockup y el relevo. La base de datos y `mediamtx.yml` de la 0.3.9
sirven tal cual: el hub solo añade tablas y una columna con valor por defecto, y `config/mediamtx.yml`
y las unidades de systemd no cambian desde la 0.3.9, así que no hace falta `--mediamtx` y MediaMTX no
se reinicia.

1. Respaldar: `sudo /home/mediasat/respaldos/bin/respaldar.sh` (ver el aviso sobre claves más abajo).
2. Elegir el puerto RTMP del hub (no 1935) y comprobar que él, 8889/tcp y 8189/udp están libres;
   añadir `rtmp_puerto` a `/home/mediasat/streamer/config.json`.
3. Probar antes al lado de producción, con el banco desplazado (`DESPLAZA=100`): mide la carga en la
   propia estación sin tocar nada de lo que está al aire. Ver [banco-de-pruebas.md](banco-de-pruebas.md).
4. Desplegar sin opciones, que copia el código y reinicia solo el panel y el supervisor (las salidas
   siguen al aire):

   ```
   scripts/desplegar.sh <alias ssh>
   ```

5. Comprobar: `curl -s http://127.0.0.1:8095/api/version` en la estación, el panel abre, los flujos
   de la 0.3.9 siguen como estaban y `journalctl -u msxs-supervisor -n 50` no tiene errores.

**Volver a la 0.3.9.** Primero desactivar desde el panel las rutas del hub (la 0.3.9 no las conoce
y dejaría sus ffmpeg sin vigilar). Después, desde el repositorio, `git checkout claude/streamer-reposo`
y `scripts/desplegar.sh <alias ssh>`; o en la estación, si existe `releases/v0.3.9`:

```
sudo -u mediasat bash -c 'cd /home/mediasat/streamer && rsync -a releases/v0.3.9/streamer/ streamer/ && cp releases/v0.3.9/VERSION .'
sudo systemctl restart msxs-supervisor msxs-web
```

Las tablas nuevas se quedan en la base sin estorbar, para cuando se vuelva a la 0.4.

## Claves y registros

- Las claves (de publicación de cada fuente, passphrases SRT, claves RTMP, tokens WebRTC,
  credenciales de TURN y de los destinos) se guardan solo en `streamer.db`, que únicamente puede leer
  `mediasat`.
- No salen en los registros: el lanzador de cada ffmpeg anota su orden con las claves cambiadas por
  `***` y filtra igual todo lo que ffmpeg escribe.
- La API las devuelve como `***`. Las claves que da el hub (la de publicación de una fuente, el
  token de lectura de un destino WebRTC) solo las ve quien tiene el permiso «Ver claves»; las de un
  equipo ajeno (la passphrase o la clave de emisión de un destino) se escriben y no vuelven nunca.
- La prueba de aceptación lo comprueba buscando las suyas en los registros y en la API.
- **Aviso:** el respaldo a Google Drive (`scripts/respaldar.sh`) copia `streamer.db` y `config.json`
  tal cual, con las claves legibles. Con el hub, eso incluye las claves de los destinos (por ejemplo
  la de emisión de YouTube). Está por decidir si se cifra el respaldo o se dejan fuera las claves.

## Quién puede cambiar qué

Los roles son los del Recorder. Por defecto:

| Permiso | operador | manager | admin |
|---|---|---|---|
| Ver fuentes, destinos y monitoreo | Sí | Sí | Sí |
| Conectar una ruta a un destino libre | Sí | Sí | Sí |
| Modificar una ruta al aire | | Sí | Sí |
| Crear y editar fuentes y destinos | | Sí | Sí |
| Ver claves, passphrases y tokens | | | Sí |
| Cambiar ajustes y adaptadores | | | Sí |

Un administrador lo cambia en Ajustes (`PUT /api/ajustes/permisos`); el administrador conserva
siempre todos. Cada cambio en el hub queda en el registro de cambios con quién lo hizo
(`GET /api/cambios`), sin claves.

## Comprobar la instalación

- Pruebas del motor, en una copia del repositorio (no hace falta la estación):
  `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock pytest httpx && .venv/bin/python -m pytest tests`
- De extremo a extremo, con señales de verdad: el banco y la prueba de aceptación, ver
  [banco-de-pruebas.md](banco-de-pruebas.md).
- Qué combinaciones están comprobadas y cómo anotar las que se prueben con equipos reales:
  [validacion.md](validacion.md).
