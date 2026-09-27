#!/usr/bin/env bash
# Instala en una estación el runtime del compositor (fase 2): Node y Chromium sin pantalla con
# playwright-core, para capturar las capas HTML5 con canal alfa. Todo queda en
# /home/mediasat/streamer/runtime (no toca el sistema ni lo que ya está al aire).
#   scripts/instalar-compositor.sh <alias ssh>
# Versiones fijas, las mismas con las que el PoC aguantó 19 h estable en el Z8.
set -euo pipefail
HOST="${1:?uso: instalar-compositor.sh <alias ssh>}"
NODE_VERSION="v22.23.3"
PLAYWRIGHT_VERSION="1.63.0"
DEST=/home/mediasat/streamer/runtime

ssh "$HOST" "sudo -u mediasat bash -s" <<REMOTO
set -euo pipefail
mkdir -p $DEST && cd $DEST
if [ ! -x node/bin/node ] || [ "\$(node/bin/node -v)" != "$NODE_VERSION" ]; then
  echo "== Node $NODE_VERSION"
  T=\$(mktemp -d); cd \$T
  F=node-$NODE_VERSION-linux-x64.tar.xz
  curl -fsSLO https://nodejs.org/dist/$NODE_VERSION/\$F
  curl -fsSLO https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt
  grep " \$F\$" SHASUMS256.txt | sha256sum -c -
  tar xJf \$F && rm -rf $DEST/node && mv node-$NODE_VERSION-linux-x64 $DEST/node
  cd $DEST && rm -rf \$T
fi
export PATH=$DEST/node/bin:\$PATH PLAYWRIGHT_BROWSERS_PATH=$DEST/browsers
[ -f package.json ] || echo '{"name":"msxs-compositor","private":true}' > package.json
echo "== playwright-core $PLAYWRIGHT_VERSION y Chromium sin pantalla"
npm install --silent --no-audit --no-fund --save-exact playwright-core@$PLAYWRIGHT_VERSION
npx --yes playwright-core@$PLAYWRIGHT_VERSION install chromium-headless-shell 2>&1 | tail -2
CHROME=\$(ls -d $DEST/browsers/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell | tail -1)
echo "== comprobación"
node -v
"\$CHROME" --version
FALTAN=\$(ldd "\$CHROME" | grep -c "not found" || true)
[ "\$FALTAN" = 0 ] || { echo "!! faltan bibliotecas del sistema para Chromium:"; ldd "\$CHROME" | grep "not found"; exit 1; }
echo "== runtime listo en $DEST"
REMOTO
