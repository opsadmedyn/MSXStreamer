#!/usr/bin/env bash
# Media Syntaxis Streamer - lanza el proceso de las capas HTML5 de una composición (lo llama el
# supervisor): Chromium sin pantalla con la prioridad más baja y el primero en caer si falta memoria.
#   capas.sh <carpeta de capas> <capas.json> <latido>
set -euo pipefail
RUNTIME=${MSXS_RUNTIME:-/home/mediasat/streamer/runtime}
export PATH=$RUNTIME/node/bin:$PATH PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-$RUNTIME/browsers} MSXS_RUNTIME=$RUNTIME
AQUI=$(cd "$(dirname "$0")" && pwd)
renice -n 15 -p $$ >/dev/null 2>&1 || true
ionice -c 3 -p $$ >/dev/null 2>&1 || true
{ echo 800 > /proc/$$/oom_score_adj; } 2>/dev/null || true
exec node "$AQUI/capas.mjs" "$@"
