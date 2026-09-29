#!/usr/bin/env bash
# Despliega MSX Streamer en una estación.
#   scripts/desplegar.sh <alias ssh> [--instalar | --mediamtx]
# --instalar: primera vez. Comprueba que los puertos estén libres, descarga MediaMTX, crea el
#   entorno de Python, crea config.json desde el ejemplo e instala los servicios.
# Sin opciones: copia el código, pone la unidad del panel (msxs-web, solo en 127.0.0.1) y reinicia
#   el panel y el supervisor. Las salidas siguen al aire.
# --mediamtx: además sustituye mediamtx.yml y las unidades systemd por las de esta versión (la
#   anterior queda como mediamtx.yml.antes-<versión>) y reinicia MediaMTX: las salidas se cortan
#   unos segundos y se reconectan solas.
# Todo vive en /home/mediasat/streamer; no toca nada del Recorder.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:?uso: desplegar.sh <alias ssh> [--instalar | --mediamtx]}"; shift || true
INSTALAR=0; [ "${1:-}" = "--instalar" ] && INSTALAR=1
MEDIAMTX=0; [ "${1:-}" = "--mediamtx" ] && MEDIAMTX=1
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
COPYFILE_DISABLE=1 tar cz --no-xattrs streamer/*.py streamer/componer.sh streamer/compositor streamer/static VERSION config systemd \
  | $SSH "$COMO tar xz -C $DEST/releases/v$VERSION"
# rsync sin --delete: el login.html de la clave del panel (hasta la 0.3.0) se quita aparte
$SSH "$COMO bash -c 'rsync -a $DEST/releases/v$VERSION/streamer/ $DEST/streamer/ && rm -f $DEST/streamer/static/login.html && cp $DEST/releases/v$VERSION/VERSION $DEST/ && cd $DEST/streamer && python3 -m py_compile *.py'"

# carpeta en memoria para las imágenes de las capas de la composición (0.4.0). Solo la crea: no
# reinicia nada ni toca otros servicios. Sin ella, las capas van a /dev/shm/msxs.
$SSH "echo 'd /run/msxs 0750 mediasat mediasat -' | sudo tee /etc/tmpfiles.d/msxs.conf >/dev/null && sudo systemd-tmpfiles --create /etc/tmpfiles.d/msxs.conf"

if [ "$INSTALAR" = 1 ]; then
  echo "== $HOST: configuración y servicios"
  $SSH "$COMO bash -s" <<REMOTO
set -euo pipefail
cd $DEST
[ -f mediamtx.yml ] || cp releases/v$VERSION/config/mediamtx.yml mediamtx.yml
if [ ! -f config.json ]; then
  cp releases/v$VERSION/config/config.ejemplo.json config.json    # revisar host_publico
fi
REMOTO
  # búfer UDP grande para el reenvío del Recorder (ver udpReadBufferSize en mediamtx.yml)
  $SSH "echo 'net.core.rmem_max=16777216' | sudo tee /etc/sysctl.d/60-msx-streamer.conf >/dev/null && sudo sysctl -q -p /etc/sysctl.d/60-msx-streamer.conf"
  # el comodín lo expande root: el usuario de SSH no puede leer /home/mediasat
  $SSH "sudo sh -c 'cp $DEST/releases/v$VERSION/systemd/msxs-*.service /etc/systemd/system/' && sudo systemctl daemon-reload \
    && sudo systemctl enable --now msxs-mediamtx msxs-supervisor msxs-web"
else
  echo "== $HOST: reiniciando panel y supervisor (las salidas no se cortan)"
  # la unidad del panel va siempre (escucha solo en 127.0.0.1: se entra por el Caddy del Recorder,
  # solo Tailscale); cambiarla solo afecta al panel, que se reinicia igualmente
  $SSH "sudo cp $DEST/releases/v$VERSION/systemd/msxs-web.service /etc/systemd/system/ && sudo systemctl daemon-reload"
  $SSH "sudo systemctl restart msxs-supervisor msxs-web"
  if [ "$MEDIAMTX" = 1 ]; then
    echo "== $HOST: configuración de MediaMTX y unidades de v$VERSION (las salidas se cortan unos segundos)"
    $SSH "$COMO bash -c 'cd $DEST && cp -p mediamtx.yml mediamtx.yml.antes-v$VERSION && cp releases/v$VERSION/config/mediamtx.yml mediamtx.yml.tmp && mv mediamtx.yml.tmp mediamtx.yml'"
    $SSH "sudo sh -c 'cp $DEST/releases/v$VERSION/systemd/msxs-*.service /etc/systemd/system/' && sudo systemctl daemon-reload \
      && sudo systemctl restart msxs-mediamtx"
  fi
fi

sleep 4
ESTADO=$($SSH "systemctl is-active msxs-mediamtx msxs-supervisor msxs-web | tr '\n' ' '")
VER=$($SSH "curl -s http://127.0.0.1:8095/api/version")
echo "== $HOST: servicios [$ESTADO] versión $VER · panel en https://<dominio del Recorder>/streamer/ (solo Tailscale)"
ESCUCHA=$($SSH "ss -Hltn 'sport = :8095' | awk '{print \$4}' | sort -u | xargs")
if [ "$ESCUCHA" != "127.0.0.1:8095" ]; then
  echo "!! el panel escucha en [$ESCUCHA] y debe ser solo 127.0.0.1:8095: revisar /etc/systemd/system/msxs-web.service"
fi

# respaldo de esta versión a Google Drive (si la estación lo tiene instalado: scripts/instalar-respaldos.sh del Streamer)
if $SSH "sudo test -x /home/mediasat/respaldos/bin/respaldar.sh"; then
  $SSH "sudo /home/mediasat/respaldos/bin/respaldar.sh --drive 'streamer-v$VERSION'" || echo "!! el respaldo a Drive falló; la versión quedó instalada igualmente"
fi
