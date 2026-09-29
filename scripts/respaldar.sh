#!/usr/bin/env bash
# Respaldo de la configuración y el estado de MSX Streamer y MSX Recorder en una estación.
# Solo lee: no para ni reinicia nada. Guarda una copia en la estación y la envía por la salida
# estándar para tener otra en el Mac:
#   mkdir -p ~/Documents/ClaudeCode/respaldos
#   ssh z8 'sudo bash -s' < scripts/respaldar.sh > ~/Documents/ClaudeCode/respaldos/z8-$(date +%Y%m%d-%H%M).tar.gz
# El código ya está en GitHub (etiquetas vX.Y.Z); aquí va lo que no está en el repositorio:
# configuración, bases de datos, unidades systemd y ajustes del sistema. Lleva claves (panel,
# destinos): guárdalo solo en sitios de confianza y nunca en el repositorio.
#
# Con --drive "<motivo>" (así lo lanzan desplegar.sh tras cada versión y el timer semanal
# msx-respaldo.timer, desde /home/mediasat/respaldos/bin), en vez de sacarlo por la salida estándar
# lo sube a Google Drive: "MSX Respaldos/<estación>/" en Mi unidad de la cuenta del sistema.
set -euo pipefail
DRIVE=""; [ "${1:-}" = "--drive" ] && DRIVE="${2:-manual}"
FECHA=$(date +%Y%m%d-%H%M%S)
DIR=/home/mediasat/respaldos
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
R="$TMP/msx-$FECHA"
mkdir -p "$R"

copiar() { for f in "$@"; do [ -e "$f" ] && cp -a --parents "$f" "$R" 2>/dev/null || true; done; }
sqlite_copia() {   # copia coherente aunque el servicio esté escribiendo
  [ -f "$1" ] || return 0
  mkdir -p "$R$(dirname "$1")"
  python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" "$1" "$R$1"
}

copiar /home/mediasat/streamer/config.json /home/mediasat/streamer/mediamtx.yml /home/mediasat/streamer/VERSION \
       /home/mediasat/streamer/mediamtx.yml.antes-*
sqlite_copia /home/mediasat/streamer/streamer.db
copiar /home/mediasat/recorder/canales.json /home/mediasat/recorder/config.json /home/mediasat/recorder/VERSION \
       /home/mediasat/recorder/canales.json.respaldo-* /home/mediasat/recorder/canales.json.antes-*
sqlite_copia /home/mediasat/recorder/usuarios.db
sqlite_copia /home/mediasat/recorder/shows.db
sqlite_copia /home/mediasat/recorder/indice.db
copiar /home/mediasat/recorder/destinos.json /home/mediasat/recorder/qc.json
# el código instalado, para que cada respaldo sirva por sí solo (sin entornos ni binarios)
tar c -C / --exclude=venv --exclude=__pycache__ --exclude=cache --exclude='*.db*' --exclude='._*' \
    home/mediasat/recorder home/mediasat/streamer/streamer 2>/dev/null | tar x -C "$R" || true
copiar /etc/systemd/system/msxs-*.service /etc/systemd/system/msr-*.service /etc/systemd/system/caddy.service.d \
       /etc/sysctl.d/60-msx-streamer.conf /etc/caddy/Caddyfile /etc/sudoers.d/90-msr-mediasat

{
  echo "Respaldo MSX · $(hostname) · $(date '+%F %T %Z')"
  echo "Streamer $(cat /home/mediasat/streamer/VERSION 2>/dev/null) · Recorder $(cat /home/mediasat/recorder/VERSION 2>/dev/null)"
  echo
  systemctl list-units --no-legend --plain 'msxs-*' 'msr-*' 2>/dev/null | awk '{print $1, $3, $4}' || true
  echo
  echo "net.core.rmem_max=$(sysctl -n net.core.rmem_max 2>/dev/null || true)"
  df -h / /srv/grabaciones 2>/dev/null | tail -n +2 || true
} > "$R/LEEME.txt"

mkdir -p "$DIR"; chmod 700 "$DIR"
tar czf "$DIR/msx-$FECHA.tar.gz" -C "$TMP" "msx-$FECHA"
chmod 600 "$DIR/msx-$FECHA.tar.gz"
ls -1t "$DIR"/msx-*.tar.gz | tail -n +31 | xargs -r rm -f       # se conservan los 30 últimos
echo "respaldo guardado en $(hostname):$DIR/msx-$FECHA.tar.gz ($(du -h "$DIR/msx-$FECHA.tar.gz" | cut -f1))" >&2
if [ -n "$DRIVE" ]; then
  /home/mediasat/recorder/venv/bin/python -W ignore::FutureWarning /home/mediasat/respaldos/bin/respaldo_drive.py \
    "$DIR/msx-$FECHA.tar.gz" "$DRIVE · Streamer $(cat /home/mediasat/streamer/VERSION 2>/dev/null) · Recorder $(cat /home/mediasat/recorder/VERSION 2>/dev/null)" >&2
else
  cat "$DIR/msx-$FECHA.tar.gz"
fi
