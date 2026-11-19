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

## El panel

Se abre en `https://<dominio del Recorder>/streamer/`, con la sesión del Recorder. Tiene la misma
cabecera que el resto de la suite y seis secciones:

| Sección | Para qué |
|---|---|
| Fuentes | Dar de alta cada señal que entra y ver qué trae: vista previa, resolución, códec, bitrate, pistas de audio con sus medidores. El formulario pregunta por separado quién inicia la conexión y en qué dirección viaja la señal |
| Destinos | Dos listas: los destinos a los que el hub se conecta y las direcciones que el hub entrega para que otro equipo las abra. Cada uno con sus credenciales, su perfil de salida, su audio y su reconexión, y con «Probar conexión» |
| Matriz de rutas | Fuentes en filas y destinos en columnas. Al elegir un cruce dice qué pistas copiará y cuáles convertirá, y por qué, antes de activar. Si el destino ya está al aire con otra fuente, la nombra y pide confirmar el cambio |
| Monitoreo | El estado de cada entrada y de cada ruta, con la evidencia que lo respalda y la hora en que se comprobó, y la carga de la estación con el consumo de cada proceso |
| Adaptadores | MediaMTX, FFmpeg, y lo que les falta a NDI y a Zixi para habilitarse. Abajo, la tabla de qué formas de conexión están verificadas con equipos reales, cuáles en el banco y cuáles pendientes |
| Ajustes | WebRTC fuera de la red local, los puertos del hub, los perfiles de conversión, qué puede hacer cada rol y el registro de cambios |

Los flujos de la 0.3.9, con su composición, siguen en «Flujos y composición», a la derecha de las
secciones. Cada flujo aparece además en Fuentes como una fuente más, y se puede llevar a cualquier
destino desde la matriz.

Lo que un rol no puede hacer sale apagado o no sale, y el panel dice por qué. Las claves van ocultas:
quien tiene el permiso las ve un minuto con «Mostrar claves», y cada consulta queda en el registro
de cambios.

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
| `rtmps_puerto`, `rtmps_cert`, `rtmps_clave` | RTMPS del hub: su puerto y los archivos PEM del certificado y de su clave. Sin los tres, el hub atiende solo RTMP sin cifrar (ver «RTMPS») | `1946`, `/home/mediasat/streamer/tls/hub.crt`, `/home/mediasat/streamer/tls/hub.key` |
| `webrtc_puerto`, `webrtc_udp` | Señalización WebRTC (TCP) y medios (UDP) | `8889`, `8189` |
| `hls_url`, `webrtc_url` | Dirección pública HTTPS de HLS y de WebRTC, si hay un proxy delante (ver más abajo) | `https://hls.ejemplo.com` |
| `panel_puerto` | Puerto del panel (solo para avisar si una fuente lo pide) | `8095` |
| `renderer_url` | Dirección del MSX Renderer, si esta estación lo tiene: pone su botón en la cabecera del panel, junto a los del Recorder. Vacía, el botón no sale | `https://renderer.ejemplo.com` |
| `adaptadores` | NDI y Zixi: `{"ndi": {"activo": false, "libndi": ""}, "zixi": {"activo": false, "api": ""}}` | |
| `recorder_canales`, `recorder_config`, `composicion_max`, `hub_tokens` | Los de la 0.3.9, sin cambios | |

Los puertos y direcciones públicas también se cambian desde Ajustes (`PUT /api/ajustes/red`), que
manda sobre `config.json`. El hub no pide a MediaMTX un puerto que tenga otro programa: deja ese
servidor apagado y lo dice en el estado de las fuentes y destinos afectados. Cambiar desde Ajustes
el puerto de un servidor reinicia ese servidor y corta lo que esté al aire por él: con rutas al aire
el hub responde con la lista de las que se cortan y pide confirmarla (ver «Quién puede cambiar qué»).

El panel y el supervisor leen `config.json` al arrancar. Después de cambiarlo hay que reiniciar los
dos, `sudo systemctl restart msxs-supervisor msxs-web`, que no corta ninguna señal: MediaMTX y los
ffmpeg de las rutas siguen en marcha y el supervisor los vuelve a adoptar.

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
completo las rutas volvieron solas en unos 8 s (RTMP), 10 s (HLS) y 20 s (WebRTC, tras recargar la
página del reproductor), sin duplicados ([aceptacion-banco.md](aceptacion-banco.md)).

## Puertos

| Puerto | Quién lo usa | Debe llegar desde |
|---|---|---|
| 8095/tcp | Panel y API | Solo `127.0.0.1` (el Caddy del Recorder, por Tailscale) |
| 9997/tcp | API de MediaMTX | Solo `127.0.0.1` |
| 8890/udp | SRT del hub (fuentes que llaman y receptores que llaman, por stream ID) | Los equipos que envían o reciben SRT |
| Los que se elijan, UDP | SRT con puerto propio de una fuente o destino | Ese equipo |
| `rtmp_puerto`/tcp | RTMP del hub (fuentes que publican, clientes que leen) | Los codificadores y lectores RTMP |
| `rtmps_puerto`/tcp | Lo mismo, cifrado (solo si se ha configurado RTMPS) | Los codificadores y lectores RTMPS |
| 8888/tcp | HLS | Los reproductores (o solo el proxy HTTPS) |
| 8889/tcp | WebRTC: WHIP y WHEP | Los navegadores (o solo el proxy HTTPS) |
| 8189/udp | WebRTC: los medios | Los navegadores, salvo que todo vaya por TURN |

Los envíos que hace el hub (SRT o RTMP hacia un receptor) salen hacia el puerto del receptor y no
necesitan nada abierto en la estación. Para ver qué está ocupado antes de instalar:

```
ss -Hltnu | grep -E ':(8095|9997|8890|8888|8889|8189|1945)\b'
```

## RTMPS

**Emitir** por RTMPS hacia una plataforma no necesita nada en la estación: basta escribir
`rtmps://…` en la dirección del destino y que el ffmpeg tenga TLS (`ffmpeg -protocols | grep rtmps`).
ffmpeg cifra la emisión pero no comprueba el certificado del servidor. «Probar la conexión» sí lo
comprueba: si no es válido (caducado, de otro nombre, firmado por el propio servidor) lo dice, y no
da el destino por caído, porque la emisión funcionaría igual.

Para que el hub **reciba** RTMPS de un codificador y lo **entregue** a un cliente, MediaMTX necesita
un certificado:

1. Un certificado y su clave en PEM, para el nombre que va en `host_publico`, que el usuario del
   servicio (`mediasat`) pueda leer. Vale el mismo que usa el proxy HTTPS de la estación, o una
   copia que se actualice cuando se renueve.
2. En `config.json`, `rtmps_puerto` (uno libre, por ejemplo 1946), `rtmps_cert` y `rtmps_clave`, y
   reiniciar el supervisor y el panel (`sudo systemctl restart msxs-supervisor msxs-web`).
3. Desde entonces cada fuente y cada destino RTMP que el hub entrega tiene dos direcciones, la de
   siempre y `rtmps://…`, con la misma clave. El otro equipo usa la que quiera.

Cómo se comporta, medido en el banco con MediaMTX 1.21.1 y señales en marcha:

- El hub comprueba el certificado antes de pedírselo a MediaMTX, porque con uno que no puede cargar
  MediaMTX se cierra entero. Si falta, no se puede leer, no está en PEM, su clave está cifrada con
  contraseña o no es la suya, el hub enciende RTMP sin cifrar y Ajustes dice el motivo. Comprueba
  que carga, no que esté vigente ni a qué nombre está: eso lo verá el codificador al conectar.
- En cuanto el certificado vale, el hub enciende RTMPS solo (1,5 s en el banco). Encenderlo o
  apagarlo reinicia el servidor RTMP: quien publica o lee por RTMP se corta y vuelve a conectar.
  En el banco un lector RTMP estuvo 3,3 s sin señal, y la señal de una cámara RTMP tardó 8,5 s en
  volver a su receptor. Lo demás no se entera: una fuente SRT siguió llegando sin ninguna parada a
  su receptor SRT, al HLS y al WebRTC de un navegador. Conviene hacerlo sin señales RTMP al aire.
- Renovar el certificado en los mismos archivos no corta nada: MediaMTX lo recarga solo.
- Un RTMPS que ya está en marcha no se apaga porque lo que se le pide deje de valer. Si el
  certificado del disco se estropea, o si `config.json` pasa a pedir otro certificado u otro puerto
  que no valen, sigue como estaba, con el certificado que tenía cargado, y Ajustes avisa de que al
  reiniciarse MediaMTX no volverá hasta corregirlo.
- Cambiar solo el puerto de RTMPS reinicia RTMPS y nada más: no corta a quien publica por RTMP sin
  cifrar.

## HLS y WebRTC fuera de la red local

Comprobado en el banco de pruebas, con un Caddy y un servidor TURN (coturn) en el mismo equipo que
el hub (casos `https` e `ice` de [combinaciones-banco.md](combinaciones-banco.md)). Pendiente en la
red de la EZC80: el proxy de la estación con sus nombres y su certificado de verdad, y un NAT real
entre el hub y quien mira.

### HTTPS por delante

Un proxy inverso con certificado (el Caddy del Recorder u otro) sirve HLS y WebRTC con nombres
propios, y el hub entrega esas direcciones si se le dicen en `hls_url` y `webrtc_url`. Con Caddy:

```
hls.ejemplo.com {
    reverse_proxy 127.0.0.1:8888
}
webrtc.ejemplo.com {
    reverse_proxy 127.0.0.1:8889
}
```

Con un nombre propio para cada uno no hay que reescribir rutas en el proxy. En el banco, con un
Caddy 2.6.2 puesto así delante (con un puerto para cada uno en vez de un nombre, y un certificado
de pruebas), un lector y un navegador reproducen la lista HLS, un navegador reproduce la página
WebRTC que entrega el hub, otro publica por WHIP con su token, y Monitoreo enseña la dirección de
quien mira, no la del proxy.

**El proxy tiene que decirle al hub de dónde viene cada petición.** Un proxy en la misma estación
llega a MediaMTX desde `127.0.0.1`, que para MediaMTX es el propio hub y entra sin clave. Lo
encontró la prueba: por un proxy así, cualquiera leía por HLS cualquier señal y miraba o publicaba
por WebRTC sin token. Desde esta versión el hub le pide a MediaMTX que juzgue lo que le llega de
`127.0.0.1` por la dirección que el proxy anota en la cabecera `X-Forwarded-For`. Lo que no la trae
(el panel y los ffmpeg del hub) sigue siendo del hub, y quien llama a MediaMTX directamente desde la
red no gana nada poniéndola él.

- Caddy la anota solo, con la dirección de quien conecta, y descarta la que traiga la petición: no
  hay que hacer nada. Medido en el banco con Caddy 2.6.2: quien entra sin token recibe un `401` por
  HLS y por WebRTC, también si manda él una `X-Forwarded-For: 127.0.0.1`. El Caddy de la EZC80 es
  el 2.11.4 (mirado el 19 de noviembre de 2026) y con él no está medido; lo dice allí «Probar la
  conexión».
- nginx no la anota si no se le dice. En cada `location` que lleve al hub hace falta
  `proxy_set_header X-Forwarded-For $remote_addr;` (también vale `$proxy_add_x_forwarded_for`).
  **Con `X-Real-IP` sola no basta**: nginx deja pasar la `X-Forwarded-For` que traiga la petición,
  y MediaMTX mira esa antes. Medido en el banco con nginx 1.24: con cualquiera de esas dos líneas
  responde `401` a quien entra sin clave, mande la cabecera que mande; con `X-Real-IP` sola
  responde `401` a quien pide sin más y deja entrar a quien manda `X-Forwarded-For: 127.0.0.1`; y
  sin ninguna entra cualquiera.
- Lo que queda abierto con un proxy mal puesto, medido en el banco. Por uno que no anota nada, la
  lista HLS de una fuente se lee sin clave, y un navegador sin token ve una salida WebRTC protegida
  y publica en una fuente WHIP protegida. Por uno que deja pasar la cabecera que trae la petición,
  mandándola con `127.0.0.1`, un lector leyó por HLS una fuente y un navegador sin token vio una
  salida WebRTC protegida; en Monitoreo aparecía como un receptor de `127.0.0.1`.
- El hub no puede impedirlo desde dentro, pero lo comprueba: «Probar la conexión» de una fuente o
  un destino HLS o WebRTC pide por la dirección pública, sin clave, la página de una fuente. La
  pide dos veces, una sin más y otra diciendo venir de la propia estación, y dice si entra de
  alguna de las dos formas. Pedirla no abre ninguna señal.
- Si el hub no llega a su propia dirección pública (pasa detrás de algunos routers) o el nombre
  apunta a la propia estación, «Probar la conexión» dice que desde ahí no se puede comprobar.
  Entonces se mira desde otro equipo, con el identificador de cualquier fuente del hub:

  ```
  curl -s -o /dev/null -w '%{http_code}\n' https://hls.ejemplo.com/<id de la fuente>/
  curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-For: 127.0.0.1' https://hls.ejemplo.com/<id de la fuente>/
  curl -s -o /dev/null -w '%{http_code}\n' https://webrtc.ejemplo.com/<id de la fuente>/
  curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-For: 127.0.0.1' https://webrtc.ejemplo.com/<id de la fuente>/
  ```

  Las cuatro tienen que responder `401`. Un `200` quiere decir que se entra sin clave. Se pide la
  página (la dirección acabada en `/`) y no la lista `index.m3u8`, a la que MediaMTX responde
  primero con una redirección, a cualquiera.
- Vale lo mismo para cualquier programa de la estación que reenvíe conexiones de fuera hacia los
  puertos del hub (un túnel, un proxy TCP): MediaMTX las ve llegar de `127.0.0.1` y no les pide
  clave. SRT y RTMP no tienen dónde anotar el origen, así que delante de ellos no se pone nada así.
- Con el proxy en otra máquina MediaMTX ve la dirección del proxy, que no es la suya, y pide clave
  como a cualquiera. Monitoreo enseñará entonces la dirección del proxy y no la de quien mira. Este
  montaje no se ha probado en el banco.

### WebRTC: que lleguen los medios

La señalización va por HTTPS, pero los medios van por UDP al puerto `webrtc_udp`. Si la estación
está detrás de NAT hay que anunciar su IP pública y abrir ese puerto, o usar un servidor TURN. Las
dos cosas se dicen en Ajustes (`PUT /api/ajustes/webrtc`, con `hosts` y la lista `ice` de servidores
STUN y TURN, cada uno con su usuario y credencial). Las credenciales de TURN se guardan como las
demás claves.

Medido en el banco con MediaMTX 1.21.1 y un coturn de pruebas:

- La dirección que se pone en `hosts` aparece entre las que el hub ofrece al navegador.
- Un navegador que solo puede salir por el servidor TURN reproduce la salida WebRTC (30 fps, con
  audio) y publica por WHIP. Vale con usuario y contraseña fijos y con secreto compartido.
- **Mejor con secreto compartido.** Con usuario y contraseña fijos, esa contraseña llega tal cual a
  cada navegador autorizado a mirar. Con secreto compartido (en coturn, `use-auth-secret`; en el
  hub, usuario `AUTH_SECRET` y el secreto como credencial) cada navegador recibe un usuario
  temporal y el secreto no sale del hub.
- **Cambiar `hosts` o `ice` corta WebRTC.** MediaMTX reinicia su servidor WebRTC, y con él se
  cierran sus sesiones. Quien miraba se queda sin imagen hasta que su página vuelve a conectar sola
  (entre 18 y 20 s en el banco). Con quien publica por WHIP no se midió: volverá si su codificador
  reconecta solo. Lo que va por SRT no se corta. Por eso, con rutas WebRTC al aire, el hub responde
  con la lista de las que se cortan y pide confirmarla (ver «Quién puede cambiar qué»).
- **Un servidor TURN que no responde retrasa cada conexión.** WebRTC sigue funcionando por conexión
  directa, pero cada navegador nuevo tarda unos 10 s en empezar en vez de 2. Si el servidor TURN se
  retira, hay que quitarlo también de Ajustes.
- Reiniciar el supervisor con un servidor TURN configurado no corta a quien mira.

Al servidor TURN tienen que llegar tanto el hub como quien mira. El paquete `coturn` de Ubuntu
queda habilitado al instalarlo y arranca solo en el puerto 3478: si se instala solo para una prueba,
`sudo systemctl disable --now coturn`.

## Relevo de la 0.3.9 en la EZC80

Se hace cuando esté aprobado el relevo. La base de datos y `mediamtx.yml` de la 0.3.9
sirven tal cual: el hub solo añade tablas y una columna con valor por defecto, y `config/mediamtx.yml`
y las unidades de systemd no cambian desde la 0.3.9, así que no hace falta `--mediamtx` y MediaMTX no
se reinicia.

1. Respaldar: `sudo /home/mediasat/respaldos/bin/respaldar.sh` (ver el aviso sobre claves más abajo).
2. Elegir el puerto RTMP del hub (no 1935) y comprobar que él, 8889/tcp y 8189/udp están libres;
   añadir `rtmp_puerto` a `/home/mediasat/streamer/config.json`.
3. Probar antes al lado de producción, con el banco desplazado (`DESPLAZA=100`): mide la carga en la
   propia estación sin tocar nada de lo que está al aire. Ver [banco-de-pruebas.md](banco-de-pruebas.md).
   Hecho el 19 de noviembre de 2026, con señales de prueba: [aceptacion-z8.md](aceptacion-z8.md).
4. Desplegar sin opciones, que copia el código y reinicia solo el panel y el supervisor:

   ```
   scripts/desplegar.sh <alias ssh>
   ```

   Las salidas siguen al aire, con una excepción la primera vez que arranca esta versión: el
   supervisor le pide a MediaMTX que juzgue por `X-Forwarded-For` lo que le llega de un proxy de la
   estación (ver «HTTPS por delante»), y MediaMTX reinicia una vez sus servidores HLS y WebRTC. En
   el banco, un navegador que miraba por HLS estuvo unos 10 s sin imagen y un lector HLS hecho con
   ffmpeg se cortó; un lector SRT no notó nada. Conviene hacer el relevo sin nadie mirando por HLS.
   En los arranques siguientes no pasa.

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

Hay ajustes que cortan lo que está al aire: el puerto de un servidor (`PUT /api/ajustes/red`) y las
direcciones anunciadas o los servidores ICE de WebRTC (`PUT /api/ajustes/webrtc`). Con rutas al aire
por ese protocolo hacen falta los dos permisos, el de ajustes y el de modificar una ruta al aire. El
hub responde primero con un 409 que trae las rutas que se cortan (`al_aire`) y sus destinos
(`corta`), y el cambio se confirma repitiéndolo con esa lista en `corta`, igual que al cambiar la
fuente de un destino al aire. En el registro de cambios queda cuántas rutas cortó.

## Comprobar la instalación

- Pruebas del motor, en una copia del repositorio (no hace falta la estación):
  `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock pytest httpx && .venv/bin/python -m pytest tests`
- De extremo a extremo, con señales de verdad: el banco y la prueba de aceptación, por la API
  (`scripts/aceptacion.py`) y pulsando en el panel (`scripts/aceptacion_panel.js`), ver
  [banco-de-pruebas.md](banco-de-pruebas.md).
- Qué combinaciones están comprobadas y cómo anotar las que se prueben con equipos reales:
  [validacion.md](validacion.md).
