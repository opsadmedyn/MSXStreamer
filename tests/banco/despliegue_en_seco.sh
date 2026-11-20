#!/usr/bin/env bash
# Ensayo en seco de scripts/desplegar.sh sin opciones: qué dice y cómo termina según lo que le
# responda la estación.
#   tests/banco/despliegue_en_seco.sh
# No se conecta a nada: el ssh es de mentira, responde como una estación y apunta lo que se le
# pide. Hace falta bash, python3 y tar. Tarda un minuto, casi todo en las esperas del guion.
set -uo pipefail
cd "$(dirname "$0")/../.."
V=$(cat VERSION); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cat > "$T/ssh" <<'FIN'
#!/bin/bash
shift
c="$*"
echo "$c" >> "$SECO_PEDIDO"
case "$c" in
  *"curl -s http://127.0.0.1:8095/api/version"*) [ -n "$SECO_VERSION" ] && echo "\"$SECO_VERSION\"" || exit 7 ;;
  *"systemctl is-active"*) printf '%s' "$SECO_SERVICIOS" ;;
  *"ss -Hltn 'sport = :8095'"*) echo "$SECO_ESCUCHA" ;;
  *"sudo test -x /home/mediasat/respaldos/bin/respaldar.sh"*) [ "$SECO_RESPALDO" != no ] ;;
  *"sudo grep -q -- --crear-clave"*) [ "$SECO_RESPALDO" != anterior ] ;;
  *"respaldar.sh --drive"*) [ "$SECO_RESPALDO" != falla ] ;;
  *"tar xz -C"*) cat > /dev/null ;;
esac
FIN
chmod 755 "$T/ssh"

fallos=0
caso() {   # caso <nombre> <versión que da el panel> <servicios> <dónde escucha> <respaldo: no|nuevo|anterior|falla>
           #      <salida esperada> <respaldos que debe lanzar> <debe decir> [no debe decir]
  local nombre=$1 dicho s lanzados bien=1
  export SECO_PEDIDO="$T/pedido" SECO_VERSION=$2 SECO_SERVICIOS=$3 SECO_ESCUCHA=$4 SECO_RESPALDO=$5
  : > "$SECO_PEDIDO"
  dicho=$(PATH="$T:$PATH" scripts/desplegar.sh estacion 2>&1 < /dev/null); s=$?
  lanzados=$(grep -c "respaldar.sh --drive" "$SECO_PEDIDO")
  [ "$s" = "$6" ] && [ "$lanzados" = "$7" ] || bien=0
  echo "$dicho" | grep -q -- "$8" || bien=0
  [ -z "${9:-}" ] || ! echo "$dicho" | grep -q -- "$9" || bien=0
  if [ $bien = 1 ]; then echo "   bien: $nombre"; return; fi
  echo "   FALLA: $nombre (salida $s, respaldos lanzados: $lanzados). Dijo:"; echo "$dicho" | tail -6 | sed 's/^/      /'
  fallos=$((fallos + 1))
}
EN_MARCHA="servicios \[active active active \] versión \"$V\""
NO_QUEDO="^!! estacion no quedó con la v$V en marcha"
caso "sin respaldo instalado: termina bien, sin avisos, y no lanza ninguno" \
  "$V" "active active active " 127.0.0.1:8095 no 0 0 "$EN_MARCHA" "^!!"
caso "con el guion de respaldo de esta versión: termina bien, sin avisos, y lanza el respaldo" \
  "$V" "active active active " 127.0.0.1:8095 nuevo 0 1 "$EN_MARCHA" "^!!"
caso "con un guion de respaldo anterior: avisa de que sube a Drive sin cifrar y de cómo ponerlo al día" \
  "$V" "active active active " 127.0.0.1:8095 anterior 0 1 "^!! el guion de respaldo de estacion es anterior a esta versión y sube a Drive sin cifrar"
caso "el respaldo falla: lo dice, y la versión queda instalada" \
  "$V" "active active active " 127.0.0.1:8095 falla 0 1 "^!! el respaldo a Drive falló"
caso "el panel escucha fuera de 127.0.0.1: lo avisa" \
  "$V" "active active active " "0.0.0.0:8095" no 0 0 "^!! el panel escucha en \[0.0.0.0:8095\]"
caso "el panel no responde: termina con error, lo dice y no lanza el respaldo" \
  "" "active activating activating " 127.0.0.1:8095 nuevo 1 0 "$NO_QUEDO"
caso "responde la versión anterior: termina con error y lo dice" \
  "0.3.9" "active active active " 127.0.0.1:8095 nuevo 1 0 "$NO_QUEDO"
caso "el panel responde pero el supervisor no está en marcha: termina con error y lo dice" \
  "$V" "active activating active " 127.0.0.1:8095 nuevo 1 0 "$NO_QUEDO"

echo
if [ $fallos -eq 0 ]; then echo "TODO BIEN"; exit 0; fi
echo "FALLOS: $fallos"; exit 1
