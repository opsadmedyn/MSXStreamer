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
#   RTMP_PUERTO  puerto RTMP del hub en el banco (por defecto 1945, para no chocar con un 1935 ocupado)
# NO lo arranques en una estación con el Streamer en marcha: usa sus mismos puertos (8095, 8888,
# 8890, 9997). Para probar al lado de una versión en producción, ver docs/instalacion.md.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
BANCO="${MSXS_BANCO:-$RAIZ/.banco}"
PYTHON="${PYTHON:-python3}"
RTMP_PUERTO="${RTMP_PUERTO:-1945}"
MTX_VERSION=v1.21.1
MTX_SHA256=653abc672a3e693f8d3b2717752492fdcfb8072291ec108d03d3dd857411b0ee    # mediamtx_v1.21.1_linux_amd64.tar.gz
BASE="$BANCO/base"
export MSXS_BASE="$BASE"

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
  # la configuración de producción, con el búfer UDP que permita este equipo (en producción, 8 MB)
  local tope; tope="$(sysctl -n net.core.rmem_max 2>/dev/null || echo 212992)"
  if [ "$tope" -ge 8388608 ]; then cp "$RAIZ/config/mediamtx.yml" "$BANCO/mediamtx.yml"
  else sed "s/^udpReadBufferSize:.*/udpReadBufferSize: $tope/" "$RAIZ/config/mediamtx.yml" >"$BANCO/mediamtx.yml"; fi
  if [ ! -f "$BASE/config.json" ]; then
    cat >"$BASE/config.json" <<JSON
{
  "host_publico": "127.0.0.1",
  "ffmpeg": "$(command -v ffmpeg)",
  "ffprobe": "$(command -v ffprobe)",
  "mediamtx_api": "http://127.0.0.1:9997",
  "mediamtx_srt": "srt://127.0.0.1:8890",
  "hls_puerto": 8888,
  "rtmp_puerto": $RTMP_PUERTO,
  "recorder_api": "http://127.0.0.1:18081",
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
  esperar 10 curl -fs http://127.0.0.1:9997/v3/paths/list || { echo "!! MediaMTX no arranca: ver $BANCO/mediamtx.log"; exit 1; }
  lanzar recorder "$BANCO" "$PYTHON" "$RAIZ/tests/banco/recorder_falso.py" 18081
  lanzar supervisor "$RAIZ/streamer" "$PYTHON" supervisor.py
  lanzar panel "$RAIZ/streamer" "$PYTHON" -m uvicorn servidor:app --host 127.0.0.1 --port 8095
  esperar 15 curl -fs http://127.0.0.1:8095/api/version || { echo "!! el panel no arranca: ver $BANCO/panel.log"; exit 1; }
  estado
}

parar() {
  for nombre in panel supervisor recorder mediamtx; do
    if vivo "$nombre"; then kill "$(cat "$BANCO/$nombre.pid")" 2>/dev/null || true; fi
    rm -f "$BANCO/$nombre.pid"
  done
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
}

case "${1:-}" in
  arrancar) arrancar ;;
  parar) parar; estado ;;
  reiniciar) parar; arrancar ;;
  supervisor)
    for nombre in panel supervisor; do
      if vivo "$nombre"; then kill "$(cat "$BANCO/$nombre.pid")" 2>/dev/null || true; fi
      rm -f "$BANCO/$nombre.pid"
    done
    sleep 1; arrancar ;;
  estado) estado ;;
  *) sed -n '2,22p' "$0"; exit 2 ;;
esac
