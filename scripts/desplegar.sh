#!/usr/bin/env bash
# Despliega MSX Streamer en una estación.
#   scripts/desplegar.sh <alias ssh> [--instalar]
# --instalar: primera vez. Comprueba que los puertos estén libres, descarga MediaMTX, crea el
#   entorno de Python, genera config.json (con una clave aleatoria) e instala los servicios.
# Sin opciones: copia el código y reinicia el panel y el supervisor. Las salidas siguen al aire.
# Todo vive en /home/mediasat/streamer; no toca nada del Recorder.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:?uso: desplegar.sh <alias ssh> [--instalar]}"; shift || true
INSTALAR=0; [ "${1:-}" = "--instalar" ] && INSTALAR=1
VERSION="$(cat VERSION)"
MTX_VERSION="v1.21.1"
DEST=/home/mediasat/streamer
SSH="ssh $HOST"
COMO="sudo -u mediasat"

echo "== comprobaciones locales (v$VERSION)"
python3 -m py_compile streamer/*.py
node --check streamer/static/app.js 2>/dev/null || echo "   (node no disponible: app.js sin comprobar)"

if [ "$INSTALAR" = 1 ]; then
  echo "== $HOST: puertos"
  OCUPADOS=$($SSH "ss -Hltnu '( sport = :8095 or sport = :8888 or sport = :8890 or sport = :9997 )' | awk '{print \$1, \$5}'")
  if [ -n "$OCUPADOS" ]; then echo "!! puertos ocupados en $HOST:"; echo "$OCUPADOS"; exit 1; fi

  echo "== $HOST: MediaMTX $MTX_VERSION y entorno de Python"
  $SSH "$COMO bash -s" <<REMOTO
set -euo pipefail
mkdir -p $DEST/bin $DEST/logs $DEST/run /tmp/msxs-mtx
cd /tmp/msxs-mtx
F=mediamtx_${MTX_VERSION}_linux_amd64.tar.gz
curl -fsSLO https://github.com/bluenviron/mediamtx/releases/download/${MTX_VERSION}/\$F
curl -fsSLO https://github.com/bluenviron/mediamtx/releases/download/${MTX_VERSION}/checksums.sha256
grep "[ *]\$F\$" checksums.sha256 | sha256sum -c -
tar xzf \$F mediamtx && install -m 755 mediamtx $DEST/bin/mediamtx
cd / && rm -rf /tmp/msxs-mtx
python3 -m venv $DEST/venv && $DEST/venv/bin/pip install -q fastapi uvicorn
REMOTO
fi

echo "== $HOST: copiando v$VERSION"
$SSH "$COMO mkdir -p $DEST/streamer $DEST/releases/v$VERSION"
COPYFILE_DISABLE=1 tar cz --no-xattrs streamer/*.py streamer/static VERSION config systemd \
  | $SSH "$COMO tar xz -C $DEST/releases/v$VERSION"
$SSH "$COMO bash -c 'rsync -a $DEST/releases/v$VERSION/streamer/ $DEST/streamer/ && cp $DEST/releases/v$VERSION/VERSION $DEST/ && cd $DEST/streamer && python3 -m py_compile *.py'"

if [ "$INSTALAR" = 1 ]; then
  echo "== $HOST: configuración y servicios"
  $SSH "$COMO bash -s" <<REMOTO
set -euo pipefail
cd $DEST
[ -f mediamtx.yml ] || cp releases/v$VERSION/config/mediamtx.yml mediamtx.yml
if [ ! -f config.json ]; then
  EJEMPLO=releases/v$VERSION/config/config.ejemplo.json python3 -c '
import json, os, secrets
c = json.load(open(os.environ["EJEMPLO"]))
c["clave_panel"] = secrets.token_urlsafe(12)
json.dump(c, open("config.json", "w"), indent=2)
print("   clave del panel:", c["clave_panel"])'
fi
REMOTO
  # búfer UDP grande para el reenvío del Recorder (ver udpReadBufferSize en mediamtx.yml)
  $SSH "echo 'net.core.rmem_max=16777216' | sudo tee /etc/sysctl.d/60-msx-streamer.conf >/dev/null && sudo sysctl -q -p /etc/sysctl.d/60-msx-streamer.conf"
  # el comodín lo expande root: el usuario de SSH no puede leer /home/mediasat
  $SSH "sudo sh -c 'cp $DEST/releases/v$VERSION/systemd/msxs-*.service /etc/systemd/system/' && sudo systemctl daemon-reload \
    && sudo systemctl enable --now msxs-mediamtx msxs-supervisor msxs-web"
else
  echo "== $HOST: reiniciando panel y supervisor (las salidas no se cortan)"
  $SSH "sudo systemctl restart msxs-supervisor msxs-web"
fi

sleep 4
ESTADO=$($SSH "systemctl is-active msxs-mediamtx msxs-supervisor msxs-web | tr '\n' ' '")
VER=$($SSH "curl -s http://127.0.0.1:8095/api/version")
echo "== $HOST: servicios [$ESTADO] versión $VER · panel en el puerto 8095"
