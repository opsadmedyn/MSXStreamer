#!/usr/bin/env bash
# Media Syntaxis Streamer - lanza una composición (la llama el supervisor).
#   componer.sh <fifo1> <url1> [<fifo2> <url2>] -- <comando ffmpeg...>
# Arranca una captura de Chromium por capa, cada una escribiendo en su FIFO, y después se
# convierte en el ffmpeg que compone (exec). Todo va en el mismo grupo de procesos: el supervisor
# lo para entero con una sola señal, y si ffmpeg cae, las capturas se quedan sin lector y terminan.
set -euo pipefail
RUNTIME=/home/mediasat/streamer/runtime
export PATH=$RUNTIME/node/bin:$PATH PLAYWRIGHT_BROWSERS_PATH=$RUNTIME/browsers MSXS_RUNTIME=$RUNTIME
AQUI=$(cd "$(dirname "$0")" && pwd)
while [ "${1:-}" != "--" ]; do
  FIFO="$1"; URL="$2"; shift 2
  rm -f "$FIFO"; mkfifo -m 600 "$FIFO"
  node "$AQUI/compositor/captura.mjs" "$URL" > "$FIFO" &
done
shift
exec "$@"
