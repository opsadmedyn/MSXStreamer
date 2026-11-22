#!/usr/bin/env bash
# Antes de desplegar: qué haría esta versión con lo que la estación tiene al aire. No cambia nada.
#   scripts/previo.sh <alias ssh>
# Lleva el código de esta versión a una carpeta temporal de la estación (no a la del servicio),
# ejecuta allí scripts/previo.py como el usuario del servicio y borra la carpeta. Termina con 0 si
# desplegar no cortaría nada de lo que está al aire, con 1 si cortaría algo y con 2 si no se pudo
# comprobar. Qué mira y cómo lo averigua: scripts/previo.py.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:?uso: previo.sh <alias ssh>}"
SSH="ssh $HOST"
COMO="sudo -u mediasat"
python3 -m py_compile streamer/*.py scripts/previo.py
T=$($SSH "$COMO mktemp -d /tmp/msxs-previo.XXXXXX")
case "$T" in
  /tmp/msxs-previo.??????) ;;
  *) echo "!! $HOST no dio una carpeta temporal: [$T]"; exit 2 ;;
esac
# la carpeta lleva una copia de la base, con sus claves: se borra pase lo que pase
trap '$SSH "$COMO rm -rf $T" || echo "!! no se pudo borrar $T en $HOST: bórrala, lleva una copia de la base"' EXIT
COPYFILE_DISABLE=1 tar cz --no-xattrs streamer/*.py VERSION -C scripts previo.py | $SSH "$COMO tar xz -C $T"
$SSH "$COMO /usr/bin/python3 -B $T/previo.py"
