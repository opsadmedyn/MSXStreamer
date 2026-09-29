#!/usr/bin/env bash
# Media Syntaxis Streamer - lanza el compositor de una composición (lo llama el supervisor).
#   componer.sh -- <comando ffmpeg...>
# Baja la prioridad y se convierte en el ffmpeg que compone (exec). Las capas ya no van aquí: las
# dibuja un proceso aparte (compositor/capas.sh) que deja la última imagen de cada una en un archivo.
set -euo pipefail
# Por debajo del Recorder y de las salidas limpias en CPU y disco, y la primera en caer si falta
# memoria. Solo se baja la prioridad (no hace falta root); si algo falla, se sigue igual.
renice -n 10 -p $$ >/dev/null 2>&1 || true
ionice -c 2 -n 7 -p $$ >/dev/null 2>&1 || true
{ echo 500 > /proc/$$/oom_score_adj; } 2>/dev/null || true
[ "${1:-}" = "--" ] && shift
exec "$@"
