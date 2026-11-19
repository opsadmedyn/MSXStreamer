#!/usr/bin/env bash
# Mide la carga del hub de señales en una estación que tiene el Streamer en producción, sin
# tocarlo: levanta el banco de pruebas al lado (puertos desplazados y carpeta propia), pasa la
# prueba de aceptación con señales de prueba y lo vuelve a parar.
#
# Se ejecuta EN la estación, como mediasat, desde una copia del repositorio (nunca desde la
# carpeta del Streamer en producción):
#     scripts/medir-carga.sh --lugar "HP Z8 (EZC80), banco al lado de producción"
#     scripts/medir-carga.sh --solo-mirar     no arranca nada: dice cómo está la estación y si se puede
#
# Lo que no hace: no despliega nada, no reinicia ni para ningún servicio y no usa ningún puerto
# del Streamer. De su carpeta solo lee: su Python, su MediaMTX, el navegador del compositor y qué
# ffmpeg usa. Antes de arrancar comprueba que los puertos del banco están libres y que la estación
# no está cargada; al acabar para el banco, pase lo que pase, y comprueba que los servicios y los
# procesos del Streamer son los mismos que antes.
#
# Variables:
#   MSXS_BANCO    carpeta del banco (por defecto /home/mediasat/hub-banco)
#   DESPLAZA      lo que se suma a los puertos del Streamer (por defecto 100; nunca 0)
#   PRODUCCION    carpeta del Streamer en producción (por defecto /home/mediasat/streamer)
#   CARGA_MAXIMA  no arranca si la carga media del último minuto pasa de este tanto por ciento de
#                 los núcleos (por defecto 60)
#   PYTHON, NODE, CHROMIUM   para usar otros que los de la estación (ver docs/banco-de-pruebas.md)
# Sale con lo que salga la prueba de aceptación (0 pasa, 1 falla, 3 algo quedó sin comprobar), con
# 2 si no llegó a arrancar o con 130 si se interrumpió (un kill al guion basta: cierra la prueba,
# para el banco y lo cuenta).
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"
PRODUCCION="${PRODUCCION:-/home/mediasat/streamer}"
export MSXS_BANCO="${MSXS_BANCO:-/home/mediasat/hub-banco}"
export DESPLAZA="${DESPLAZA:-100}"
CARGA_MAXIMA="${CARGA_MAXIMA:-60}"
LUGAR="$(hostname), banco al lado de producción"
SOLO_MIRAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --lugar) LUGAR="${2:?--lugar necesita un texto}"; shift 2 ;;
    --solo-mirar) SOLO_MIRAR=1; shift ;;
    *) sed -n '2,27p' "$0"; exit 2 ;;
  esac
done

no() { echo "!! $*" >&2; exit 2; }

# ---------------------------------------------------------------- dónde y con qué puertos
case "$DESPLAZA" in ''|*[!0-9]*) no "DESPLAZA tiene que ser un número (por ejemplo 100)" ;; esac
[ "$DESPLAZA" -ge 1 ] || no "DESPLAZA no puede ser 0: el banco usaría los puertos del Streamer"
PRODUCCION="$(readlink -m "$PRODUCCION")"
MSXS_BANCO="$(readlink -m "$MSXS_BANCO")"
case "$RAIZ/" in "$PRODUCCION"/*) no "esto no se ejecuta desde la carpeta del Streamer ($PRODUCCION): usa una copia del repositorio" ;; esac
case "$MSXS_BANCO/" in "$PRODUCCION"/*) no "la carpeta del banco no puede estar dentro de la del Streamer ($PRODUCCION)" ;; esac
case "$PRODUCCION/" in "$MSXS_BANCO"/*) no "la carpeta del banco no puede contener la del Streamer ($PRODUCCION)" ;; esac
D="$DESPLAZA"
PANEL=$((8095 + D))
PUERTO_SRT=$((9211 + D)); PUERTO_RTMP=$((1860 + D))        # los receptores de la prueba (9311 y 1960 con 100)
# los mismos que tests/banco/banco.sh, más los dos receptores de la prueba
PUERTOS="$PANEL $((9997 + D)) $((8890 + D)) $((8888 + D)) $((8889 + D)) $((8189 + D)) $((18081 + D)) $((1945 + D)) $((1946 + D)) $PUERTO_SRT $PUERTO_RTMP"

# ---------------------------------------------------------------- lo que hay en la estación
uno() { "$@" 2>/dev/null | head -1 || true; }

vivos_del_banco() {
  # PID de lo que haya en marcha del banco: sus cuatro servicios (por sus archivos .pid) y los
  # ffmpeg de sus rutas (llevan la carpeta del banco en su línea de órdenes)
  local nombre pid
  for nombre in mediamtx recorder supervisor panel; do
    pid="$(cat "$MSXS_BANCO/$nombre.pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then echo "$pid"; fi
  done
  pgrep -f "$MSXS_BANCO/base/run/" 2>/dev/null || true
}

puertos_ocupados() {      # de los puertos del banco, los que alguien tiene abiertos
  ss -Hltnu | awk '{print $5}' | sed 's/.*://' | sort -un | grep -Fxf <(printf '%s\n' $PUERTOS) | tr '\n' ' ' || true
}

foto_produccion() {
  # Servicios del Streamer (estado, proceso principal y desde cuándo) y los PID de sus tareas. Solo
  # los PID: sus líneas de órdenes llevan direcciones con claves, y no se imprimen.
  if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    for unidad in msxs-mediamtx msxs-supervisor msxs-web; do
      echo "servicio $unidad: $(systemctl is-active "$unidad" 2>/dev/null || true)," \
           "proceso $(systemctl show -p MainPID --value "$unidad" 2>/dev/null || true)," \
           "desde $(systemctl show -p ActiveEnterTimestamp --value "$unidad" 2>/dev/null || true)"
    done
  fi
  echo "tareas del Streamer en marcha (PID): $(pgrep -f "$PRODUCCION/run/" 2>/dev/null | sort -n | tr '\n' ' ')"
}

echo "== estación: $(hostname), $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "   sistema: $( (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || true), núcleo $(uname -r)"
NUCLEOS="$(nproc)"
echo "   procesador: $(awk -F': *' '/^model name/ {print $2; exit}' /proc/cpuinfo), $NUCLEOS núcleos"
echo "   memoria: $(awk '/^MemTotal/ {t=$2} /^MemAvailable/ {d=$2} END {printf "%d MB disponibles de %d MB", d/1024, t/1024}' /proc/meminfo)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,encoder.stats.sessionCount --format=csv,noheader 2>/dev/null \
    | sed 's/^/   tarjeta (nombre, uso, memoria usada, memoria total, sesiones de codificación): /' \
    || echo "   tarjeta: nvidia-smi no responde"
else
  echo "   tarjeta: sin nvidia-smi"
fi
read -r CARGA1 CARGA5 CARGA15 _ </proc/loadavg
CARGA_PCT="$(awk -v c="$CARGA1" -v n="$NUCLEOS" 'BEGIN { printf "%d", c * 100 / n }')"
echo "   carga media: $CARGA1 (1 min), $CARGA5 (5 min), $CARGA15 (15 min): el $CARGA_PCT % de los núcleos"

echo "== Streamer en producción ($PRODUCCION)"
if [ -d "$PRODUCCION" ]; then
  echo "   versión: $(cat "$PRODUCCION/VERSION" 2>/dev/null || echo desconocida)"
  [ -x "$PRODUCCION/bin/mediamtx" ] && echo "   MediaMTX: $(uno "$PRODUCCION/bin/mediamtx" --version)"
else
  echo "   no hay carpeta $PRODUCCION: aquí no parece haber un Streamer en producción"
fi
ANTES="$(foto_produccion)"
echo "$ANTES" | sed 's/^/   /'
command -v caddy >/dev/null 2>&1 && echo "   Caddy del equipo: $(uno caddy version)"

echo "== lo que usará el banco"
# el mismo ffmpeg que el Streamer, si dice cuál
if [ -r "$PRODUCCION/config.json" ]; then
  FF="$(python3 -c 'import json, sys
try:
    print(json.load(open(sys.argv[1])).get("ffmpeg") or "")
except Exception:
    print("")' "$PRODUCCION/config.json")"
  if [ -n "$FF" ] && [ -x "$FF" ] && [ "${FF#/}" != "$FF" ]; then PATH="$(dirname "$FF"):$PATH"; export PATH; fi
fi
command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1 || no "faltan ffmpeg y ffprobe"
echo "   ffmpeg: $(command -v ffmpeg) ($(ffmpeg -hide_banner -version | head -1 | cut -d' ' -f1-3))"
grep -qx ' *srt' <<<"$(ffmpeg -hide_banner -protocols 2>/dev/null || true)" || no "ese ffmpeg no trae libsrt"
if [ -z "${PYTHON:-}" ]; then
  if [ -x "$PRODUCCION/venv/bin/python" ]; then PYTHON="$PRODUCCION/venv/bin/python"; else PYTHON=python3; fi
fi
export PYTHON
"$PYTHON" -c 'import fastapi, uvicorn' 2>/dev/null || no "$PYTHON no tiene fastapi y uvicorn (ver requirements.lock)"
echo "   Python: $PYTHON ($("$PYTHON" -V 2>&1))"
MTX_PROPIO=""
if [ -x "$MSXS_BANCO/bin/mediamtx" ]; then
  echo "   MediaMTX: el que ya tiene el banco ($(uno "$MSXS_BANCO/bin/mediamtx" --version))"
elif [ -x "$PRODUCCION/bin/mediamtx" ] && [ "$(uno "$PRODUCCION/bin/mediamtx" --version)" = "v1.21.1" ]; then
  MTX_PROPIO="$PRODUCCION/bin/mediamtx"
  echo "   MediaMTX: una copia del de producción (v1.21.1)"
else
  echo "   MediaMTX: el banco descargará la v1.21.1 y comprobará su suma"
fi
# el navegador de la prueba: el del compositor de la estación, si no se dice otro
if [ -z "${NODE:-}" ] && [ -x "$PRODUCCION/runtime/node/bin/node" ]; then
  export NODE="$PRODUCCION/runtime/node/bin/node"
  export NODE_PATH="${NODE_PATH:-$PRODUCCION/runtime/node_modules}"
  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$PRODUCCION/runtime/browsers}"
fi
NAVEGADOR=()
PUEDE="$("${NODE:-node}" scripts/aceptacion_navegador.js --capacidades 2>&1 | tail -1 || true)"
if [ "$(printf '%s' "$PUEDE" | python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
    print("si" if all(d.get(k) for k in ("mse_h264", "mse_aac", "rtc_h264", "rtc_opus")) else "no")
except Exception:
    print("no")')" = si ]; then
  echo "   navegador: ${NODE:-node} con su Chromium; reproduce H.264, AAC y Opus"
else
  NAVEGADOR=(--sin-navegador)
  echo "   navegador: NO hay uno que sirva (${PUEDE:0:160}): WebRTC quedará sin comprobar"
fi

echo "== comprobaciones antes de arrancar"
command -v ss >/dev/null 2>&1 || no "falta ss (iproute2) para mirar los puertos"
[ -z "$(vivos_del_banco)" ] || no "ya hay un banco en marcha en $MSXS_BANCO: páralo antes (MSXS_BANCO=$MSXS_BANCO DESPLAZA=$DESPLAZA tests/banco/banco.sh parar)"
OCUPADOS="$(puertos_ocupados)"
[ -z "$OCUPADOS" ] || no "puertos del banco ocupados en este equipo: $OCUPADOS(prueba con otro DESPLAZA)"
echo "   puertos libres: $PUERTOS"
LIBRE_KB="$(df -Pk "$(dirname "$MSXS_BANCO")" | awk 'NR==2 {print $4}')"
[ "$LIBRE_KB" -ge 2097152 ] || no "quedan menos de 2 GB libres donde iría el banco ($(dirname "$MSXS_BANCO"))"
echo "   disco: $((LIBRE_KB / 1048576)) GB libres en $(dirname "$MSXS_BANCO")"
[ "$CARGA_PCT" -le "$CARGA_MAXIMA" ] || no "la estación está cargada (el $CARGA_PCT % de los núcleos en el último minuto; el tope es $CARGA_MAXIMA): no se arranca"
echo "   carga: el $CARGA_PCT % de los núcleos (tope $CARGA_MAXIMA)"

if [ "$SOLO_MIRAR" = 1 ]; then
  echo "== se puede: no se ha arrancado nada (--solo-mirar)"
  exit 0
fi

# ---------------------------------------------------------------- el banco y la prueba
mkdir -p "$MSXS_BANCO/bin"
[ -z "$MTX_PROPIO" ] || install -m 755 "$MTX_PROPIO" "$MSXS_BANCO/bin/mediamtx"
CARPETA="$MSXS_BANCO/aceptacion-$(date +%Y%m%d-%H%M%S)"
parar_banco() { tests/banco/banco.sh parar >/dev/null 2>&1 || true; }
trap parar_banco EXIT
trap 'exit 130' INT TERM HUP

echo "== arrancando el banco en $MSXS_BANCO (panel en 127.0.0.1:$PANEL)"
tests/banco/banco.sh arrancar

echo "== prueba de aceptación (unos 6 minutos)"
# La prueba corre aparte y aquí se la espera: así una señal a este guion (un kill, o el cierre de la sesión
# desde la que corre) llega en el acto a la prueba, que para su cámara, sus receptores y su navegador y borra
# del hub lo que creó; después el guion sigue, para el banco y lo cuenta. Con la prueba en primer plano, bash
# no atendería la señal hasta que la prueba terminase.
set +e
CORTADA=0
MSXS_SESION=admin "$PYTHON" scripts/aceptacion.py --hub "http://127.0.0.1:$PANEL" \
    --base "$MSXS_BANCO/base" --registros "$MSXS_BANCO" --lugar "$LUGAR" --carpeta "$CARPETA" \
    --puerto-srt "$PUERTO_SRT" --puerto-rtmp "$PUERTO_RTMP" \
    --reinicio-suave "tests/banco/banco.sh supervisor" --reinicio "tests/banco/banco.sh reiniciar" \
    "${NAVEGADOR[@]}" &
PRUEBA=$!
trap 'CORTADA=1; echo "== interrumpido: cerrando la prueba"; kill -TERM "$PRUEBA" 2>/dev/null' INT TERM HUP
wait "$PRUEBA"; CODIGO=$?
while kill -0 "$PRUEBA" 2>/dev/null; do wait "$PRUEBA"; CODIGO=$?; done
trap 'exit 130' INT TERM HUP
set -e

echo "== parando el banco"
ERAN="$(vivos_del_banco | tr '\n' ' ')"
parar_banco
trap - EXIT
sleep 1
QUEDAN=""
for pid in $ERAN; do if kill -0 "$pid" 2>/dev/null; then QUEDAN="$QUEDAN$pid "; fi; done
QUEDAN="$QUEDAN$(pgrep -f "$MSXS_BANCO/base/run/" 2>/dev/null | tr '\n' ' ' || true)"
OCUPADOS="$(puertos_ocupados)"
if [ -n "$QUEDAN$OCUPADOS" ]; then
  LIMPIO="OJO: del banco quedan procesos (PID: ${QUEDAN:-ninguno}) o puertos abiertos (${OCUPADOS:-ninguno}): revisar con «tests/banco/banco.sh estado»"
else
  LIMPIO="del banco no queda ningún proceso ni ningún puerto abierto"
fi
echo "   $LIMPIO"

echo "== Streamer en producción, después"
DESPUES="$(foto_produccion)"
echo "$DESPUES" | sed 's/^/   /'
if [ "$ANTES" = "$DESPUES" ]; then
  MISMO="los servicios y las tareas del Streamer son los mismos que antes de la prueba"
else
  MISMO="OJO: los servicios o las tareas del Streamer NO son los mismos que antes de la prueba (puede ser cosa suya: mirar sus eventos)"
fi
echo "   $MISMO"
{
  echo "Estación: $(hostname), $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "Antes de la prueba:"; echo "$ANTES" | sed 's/^/  /'
  echo "Después de la prueba:"; echo "$DESPUES" | sed 's/^/  /'
  echo "$MISMO"
  echo "$LIMPIO"
} >"$CARPETA/produccion.txt" 2>/dev/null || true

if [ "$CORTADA" = 1 ]; then
  echo "== la prueba se interrumpió antes de terminar: no vale como resultado"
  exit 130
fi
case "$CODIGO" in
  0) echo "== la prueba de aceptación PASA" ;;
  3) echo "== la prueba de aceptación pasa, con algo sin comprobar" ;;
  *) echo "== la prueba de aceptación NO pasa (salida $CODIGO)" ;;
esac
echo "   informe: $CARPETA/informe.md"
echo "   medidas: $CARPETA/datos.json"
exit "$CODIGO"
