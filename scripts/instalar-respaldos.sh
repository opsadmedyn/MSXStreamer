#!/usr/bin/env bash
# Instala en una estación el respaldo a Google Drive y lo prueba con un primer respaldo:
#   scripts/instalar-respaldos.sh <alias ssh>
# Deja respaldar.sh y respaldo_drive.py en /home/mediasat/respaldos/bin y activa
# msx-respaldo.timer (domingos 04:30). Además, desplegar.sh del Streamer y del Recorder lo lanzan
# tras cada versión. Destino: "MSX Respaldos/<estación>/" en Mi unidad de la cuenta del sistema.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:?uso: instalar-respaldos.sh <alias ssh>}"
BIN=/home/mediasat/respaldos/bin
ssh "$HOST" "sudo mkdir -p $BIN && sudo chmod 700 /home/mediasat/respaldos"
COPYFILE_DISABLE=1 tar cz --no-xattrs scripts/respaldar.sh scripts/respaldo_drive.py systemd/msx-respaldo.service systemd/msx-respaldo.timer \
  | ssh "$HOST" "T=\$(mktemp -d) && sudo tar xz -C \$T && sudo install -m 750 \$T/scripts/respaldar.sh \$T/scripts/respaldo_drive.py $BIN/ \
      && sudo install -m 644 \$T/systemd/msx-respaldo.service \$T/systemd/msx-respaldo.timer /etc/systemd/system/ \
      && sudo rm -rf \$T && sudo systemctl daemon-reload && sudo systemctl enable --now msx-respaldo.timer"
echo "== $HOST: primer respaldo a Drive"
ssh "$HOST" "sudo $BIN/respaldar.sh --drive instalacion"
ssh "$HOST" "systemctl list-timers --no-pager msx-respaldo.timer | head -2"
