#!/usr/bin/env bash
# Banco de pruebas del hub de señales: una instancia completa y aislada (MediaMTX, supervisor y
# panel) en una carpeta, para probar de extremo a extremo sin tocar ninguna estación.
#
#   tests/banco/banco.sh arrancar     descarga MediaMTX la primera vez y lo levanta todo
#   tests/banco/banco.sh parar        lo para todo (también los ffmpeg de las rutas)
#   tests/banco/banco.sh reiniciar    como reiniciar el equipo: lo mata todo y lo vuelve a levantar
#   tests/banco/banco.sh supervisor   como desplegar una versión: reinicia el supervisor y el panel y
#                                     deja en marcha MediaMTX y los ffmpeg de las rutas (deben seguir)
#   tests/banco/banco.sh estado       qué está en marcha
#
# El panel queda en http://127.0.0.1:8095 y solo ahí (127.0.0.1). Se entra con una cookie
# «msr_sesion» de valor admin, manager u operador (ver recorder_falso.py):
#     curl -H 'Cookie: msr_sesion=admin' http://127.0.0.1:8095/api/matriz
# Hace falta ffmpeg (con libsrt) y Python 3.10 o posterior con lo de requirements.lock. Variables:
#   MSXS_BANCO   carpeta de trabajo (por defecto .banco, en la raíz del repositorio; git la ignora)
#   PYTHON       el Python con fastapi y uvicorn (por defecto python3)
#   DESPLAZA     número que se suma a todos los puertos (por defecto 0). Con DESPLAZA=100 el banco
#                usa 8195 (panel), 10097 (API de MediaMTX), 8990 (SRT), 8988 (HLS), 2045 (RTMP),
#                2046 (RTMPS), 8989 y 8289/UDP (WebRTC): así cabe en una estación con el Streamer
#                en producción sin tocarlo (ver docs/banco-de-pruebas.md)
#   RTMP_PUERTO  puerto RTMP del hub en el banco (por defecto 1945 + DESPLAZA, lejos del 1935)
#   RTMPS_PUERTO puerto RTMPS (por defecto 1946 + DESPLAZA). Si hay openssl, el banco se hace un
#                certificado de pruebas, firmado por él mismo, y el hub atiende también RTMPS
# Sin DESPLAZA usa los puertos del Streamer (8095, 8888, 8890, 9997): NO lo arranques así en una
# estación que lo tenga en marcha.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
BANCO="${MSXS_BANCO:-$RAIZ/.banco}"
PYTHON="${PYTHON:-python3}"
D="${DESPLAZA:-0}"
PANEL=$((8095 + D)); MTX_API=$((9997 + D)); SRT=$((8890 + D)); HLS=$((8888 + D))
WEBRTC=$((8889 + D)); WEBRTC_UDP=$((8189 + D)); RECORDER=$((18081 + D))
RTMP_PUERTO="${RTMP_PUERTO:-$((1945 + D))}"
RTMPS_PUERTO="${RTMPS_PUERTO:-$((1946 + D))}"
MTX_VERSION=v1.21.1
MTX_SHA256=653abc672a3e693f8d3b2717752492fdcfb8072291ec108d03d3dd857411b0ee    # mediamtx_v1.21.1_linux_amd64.tar.gz
BASE="$BANCO/base"
export MSXS_BASE="$BASE"
# Con este equipo se habla sin pasar por el proxy de salida que pueda tener el entorno: «Probar la
# conexión» pregunta a la dirección pública del hub, que en el banco es este mismo equipo
PROPIA="$("$PYTHON" -c 'import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("192.0.2.1", 9))
print(s.getsockname()[0])' 2>/dev/null || true)"
if [ -n "$PROPIA" ]; then
  export no_proxy="${no_proxy:+$no_proxy,}$PROPIA" NO_PROXY="${NO_PROXY:+$NO_PROXY,}$PROPIA"
fi

vivo() { [ -f "$BANCO/$1.pid" ] && kill -0 "$(cat "$BANCO/$1.pid")" 2>/dev/null; }

lanzar() {      # lanzar <nombre> <directorio> <comando...>
  local nombre="$1" dir="$2"; shift 2
  if vivo "$nombre"; then return; fi
  (cd "$dir" && exec setsid "$@" >>"$BANCO/$nombre.log" 2>&1 </dev/null) &
  echo $! >"$BANCO/$nombre.pid"
}

esperar() {     # esperar <segundos> <comando que debe acabar bien>
  local hasta=$((SECONDS + $1)); shift
  until "$@" >/dev/null 2>&1; do
    [ $SECONDS -lt $hasta ] || return 1
    sleep 0.3
  done
}

preparar() {
  mkdir -p "$BASE" "$BANCO/bin"
  if [ ! -x "$BANCO/bin/mediamtx" ]; then
    echo "== descargando MediaMTX $MTX_VERSION"
    local f="mediamtx_${MTX_VERSION}_linux_amd64.tar.gz" tmp
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/$f" "https://github.com/bluenviron/mediamtx/releases/download/$MTX_VERSION/$f"
    echo "$MTX_SHA256  $tmp/$f" | sha256sum -c - >/dev/null
    tar xzf "$tmp/$f" -C "$tmp" mediamtx && install -m 755 "$tmp/mediamtx" "$BANCO/bin/mediamtx"
    rm -rf "$tmp"
  fi
  # la configuración de producción, con el búfer UDP que permita este equipo (en producción, 8 MB) y
  # los puertos del banco. MediaMTX vigila su archivo y, si cambia estando en marcha, recarga y
  # corta todas las señales: solo se reescribe cuando de verdad es distinto
  local tope nuevo; tope="$(sysctl -n net.core.rmem_max 2>/dev/null || echo 212992)"
  [ "$tope" -lt 8388608 ] || tope=8388608
  nuevo="$(mktemp)"
  sed -e "s/^udpReadBufferSize:.*/udpReadBufferSize: $tope/" -e "s/^apiAddress:.*/apiAddress: 127.0.0.1:$MTX_API/" \
      -e "s/^srtAddress:.*/srtAddress: :$SRT/" -e "s/^hlsAddress:.*/hlsAddress: :$HLS/" "$RAIZ/config/mediamtx.yml" >"$nuevo"
  if cmp -s "$nuevo" "$BANCO/mediamtx.yml"; then rm -f "$nuevo"; else mv "$nuevo" "$BANCO/mediamtx.yml"; fi
  # RTMPS necesita un certificado: uno de pruebas, firmado por el propio banco (quien se conecte
  # no lo podrá comprobar, y así lo dirá). Sin openssl, el banco se queda sin RTMPS
  local tls=""
  if [ ! -f "$BANCO/tls/hub.crt" ] && command -v openssl >/dev/null; then
    mkdir -p "$BANCO/tls"
    openssl req -x509 -newkey rsa:2048 -nodes -keyout "$BANCO/tls/hub.key" -out "$BANCO/tls/hub.crt" -days 30 \
      -subj "/CN=banco-de-pruebas" -addext "subjectAltName=IP:127.0.0.1,DNS:localhost" >/dev/null 2>&1 || rm -rf "$BANCO/tls"
  fi
  if [ -f "$BANCO/tls/hub.crt" ]; then
    tls="\"rtmps_puerto\": $RTMPS_PUERTO, \"rtmps_cert\": \"$BANCO/tls/hub.crt\", \"rtmps_clave\": \"$BANCO/tls/hub.key\","
  fi
  if [ ! -f "$BASE/config.json" ]; then
    cat >"$BASE/config.json" <<JSON
{
  "host_publico": "127.0.0.1",
  $tls
  "ffmpeg": "$(command -v ffmpeg)",
  "ffprobe": "$(command -v ffprobe)",
  "mediamtx_api": "http://127.0.0.1:$MTX_API",
  "mediamtx_srt": "srt://127.0.0.1:$SRT",
  "hls_puerto": $HLS,
  "rtmp_puerto": $RTMP_PUERTO,
  "webrtc_puerto": $WEBRTC,
  "webrtc_udp": $WEBRTC_UDP,
  "panel_puerto": $PANEL,
  "recorder_api": "http://127.0.0.1:$RECORDER",
  "recorder_canales": "$BASE/sin-recorder/canales.json",
  "recorder_reposo": "$BASE/sin-recorder/reposo.json",
  "recorder_config": "$BASE/sin-recorder/config.json"
}
JSON
  fi
}

arrancar() {
  preparar
  lanzar mediamtx "$BANCO" "$BANCO/bin/mediamtx" "$BANCO/mediamtx.yml"
  esperar 10 curl -fs "http://127.0.0.1:$MTX_API/v3/paths/list" || { echo "!! MediaMTX no arranca: ver $BANCO/mediamtx.log"; exit 1; }
  lanzar recorder "$BANCO" "$PYTHON" "$RAIZ/tests/banco/recorder_falso.py" "$RECORDER"
  lanzar supervisor "$RAIZ/streamer" "$PYTHON" supervisor.py
  lanzar panel "$RAIZ/streamer" "$PYTHON" -m uvicorn servidor:app --host 127.0.0.1 --port "$PANEL"
  esperar 15 curl -fs "http://127.0.0.1:$PANEL/api/version" || { echo "!! el panel no arranca: ver $BANCO/panel.log"; exit 1; }
  estado
}

detener() {     # detener <nombre>: lo para y espera a que se haya ido (el supervisor acaba antes su vuelta)
  local pid
  if vivo "$1"; then
    pid="$(cat "$BANCO/$1.pid")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  fi
  rm -f "$BANCO/$1.pid"
}

parar() {
  for nombre in panel supervisor recorder mediamtx; do detener "$nombre"; done
  # los ffmpeg de las rutas (y sus lanzadores) llevan la carpeta del banco en su línea de órdenes
  pkill -f "$BASE/run/" 2>/dev/null || true
  pkill -f "$BASE/logs/" 2>/dev/null || true
  sleep 1
  pkill -9 -f "$BASE/run/" 2>/dev/null || true
}

estado() {
  for nombre in mediamtx recorder supervisor panel; do
    if vivo "$nombre"; then echo "   $nombre: en marcha (pid $(cat "$BANCO/$nombre.pid"))"; else echo "   $nombre: parado"; fi
  done
  echo "   procesos de rutas: $(pgrep -fc "$BASE/run/.*\.prog" 2>/dev/null || true)"
  echo "   panel: http://127.0.0.1:$PANEL (cookie msr_sesion=admin)"
}

case "${1:-}" in
  arrancar) arrancar ;;
  parar) parar; estado ;;
  reiniciar) parar; arrancar ;;
  supervisor)
    for nombre in panel supervisor; do detener "$nombre"; done
    arrancar ;;
  estado) estado ;;
  *) sed -n '2,27p' "$0"; exit 2 ;;
esac
