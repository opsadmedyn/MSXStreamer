#!/usr/bin/env bash
# Ensayo del relevo de la 0.3.9 por esta versión, con salidas al aire y sin estación:
#   sudo PYTHON=.venv/bin/python tests/banco/relevo.sh
# Monta una estación de mentira (/home/mediasat/streamer, con sus unidades de systemd) en la que
# corre la 0.3.9 tal como está en el repositorio, con MediaMTX y con salidas al aire de todas las
# clases que tiene: un flujo que MediaMTX va a buscar por SRT, uno de un canal del Recorder (UDP),
# uno de hub y uno 1080i directo, con destinos SRT (llamando y escuchando, con passphrase y
# streamid), RTMP y HLS. Después hace el relevo como dice docs/instalacion.md, con
# scripts/desplegar.sh tal cual, y comprueba que cada salida sigue siendo el mismo proceso, que a
# cada receptor le siguió llegando señal sin huecos, que MediaMTX no se reinició ni soltó ninguna
# entrada, y cuánto estuvo parado el HLS. Con la versión nueva para y vuelve a iniciar una salida
# de las de antes. Luego vuelve a la 0.3.9 con el desplegar.sh de la 0.3.9, repite el relevo y
# vuelve otra vez por el atajo de la guía, comprobando lo mismo en cada paso. Al final despliega
# una versión nueva estropeada a propósito, que no arranca: lo que está al aire tiene que seguir
# igual, desplegar.sh tiene que decirlo y de ahí se tiene que poder volver a la 0.3.9.
#
# Todo ocurre en espacios propios de montaje, de red y de procesos: /home y /etc/systemd/system son
# discos en memoria, los puertos son solo de la prueba y al terminar no queda ningún proceso. ssh,
# sudo y systemctl son de mentira (relevo_ayuda.py): la «estación» es este equipo dentro de la
# prueba, y el systemd de mentira para y arranca cada unidad como diga su archivo (el supervisor,
# con KillMode=process).
#
# Hace falta Linux, root, unshare, ip, ss, rsync, curl, git, ffmpeg con libsrt y libx264 (fuera de
# /home), el MediaMTX del banco (lo descarga tests/banco/banco.sh arrancar; o MEDIAMTX=<binario>) y
# un Python 3.10 o posterior con fastapi y uvicorn (PYTHON). Tarda unos 3 minutos.
#   CONSERVAR=<carpeta>   deja ahí lo que dijo cada paso, las medidas y los registros
set -uo pipefail
ANTERIOR="${ANTERIOR:-7094db734ca31634c479a454f7190c11986ec987}"      # la 0.3.9

if [ -z "${MSX_PRUEBA_RELEVO:-}" ]; then      # fuera: copia lo que se prueba y vuelve a entrar, ya aislada
  [ "$(uname -s)" = Linux ] || { echo "esta prueba solo corre en Linux"; exit 2; }
  [ "$(id -u)" = 0 ] || { echo "hace falta root: sudo $0"; exit 2; }
  for o in unshare ip ss rsync curl git ffmpeg setsid; do command -v "$o" > /dev/null || { echo "falta $o"; exit 2; }; done
  case "$(command -v ffmpeg)" in /home/*) echo "ffmpeg está bajo /home, que dentro de la prueba queda tapado"; exit 2 ;; esac
  YO="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  PY="$(command -v "${PYTHON:-python3}")" || { echo "no encuentro ${PYTHON:-python3}"; exit 2; }
  PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")"
  cd "$(dirname "$YO")/../.."
  "$PY" -c 'import sys, fastapi, uvicorn; assert sys.version_info >= (3, 10)' 2> /dev/null \
    || { echo "$PY no es un Python 3.10 o posterior con fastapi y uvicorn (ver requirements.lock): PYTHON=<otro>"; exit 2; }
  MTX="${MEDIAMTX:-${MSXS_BANCO:-$PWD/.banco}/bin/mediamtx}"
  [ -x "$MTX" ] || { echo "falta MediaMTX en $MTX: lo descarga tests/banco/banco.sh arrancar"; exit 2; }
  git cat-file -e "$ANTERIOR^{commit}" 2> /dev/null || { echo "este repositorio no tiene el commit de la 0.3.9 ($ANTERIOR)"; exit 2; }
  COPIA=$(mktemp -d /tmp/msx-prueba-relevo.XXXXXX)      # fuera de /home, que dentro de la prueba queda tapado
  trap 'rm -rf "$COPIA"' EXIT
  mkdir -p "$COPIA/nueva" "$COPIA/anterior" "$COPIA/bin" "$COPIA/informe"
  git archive "$ANTERIOR" | tar x -C "$COPIA/anterior"
  tar c --exclude=__pycache__ streamer scripts VERSION config systemd requirements.lock | tar x -C "$COPIA/nueva"
  cp "$MTX" "$COPIA/bin/mediamtx"
  cp "$YO" tests/banco/relevo_ayuda.py tests/banco/recorder_falso.py docs/instalacion.md "$COPIA/"
  # lo que ese Python tenga bajo /home (un entorno virtual, por ejemplo) se deja a la vista dentro
  VER=$("$PY" -c 'import sys; print(sys.prefix); print(sys.base_prefix)' | sort -u | grep '^/home/' | tr '\n' ':')
  MSX_PRUEBA_RELEVO="$COPIA" MSX_PY="$PY" MSX_VER="$VER" \
    unshare -m -n -p -f --mount-proc --propagation private bash "$COPIA/$(basename "$YO")"
  s=$?
  if [ -n "${CONSERVAR:-}" ]; then
    mkdir -p "$CONSERVAR" && cp -a "$COPIA/informe/." "$CONSERVAR/" && echo "lo que dijo cada paso queda en $CONSERVAR"
  fi
  exit $s
fi

# dentro: solo sigue si de verdad está en espacios propios y lo que va a tocar quedó tapado
COPIA="$MSX_PRUEBA_RELEVO"; PY="$MSX_PY"; AYUDA="$COPIA/relevo_ayuda.py"
[ "$$" = 1 ] || { echo "no está aislada (procesos): la prueba no sigue"; exit 2; }
[ "$(ip -o link | wc -l)" = 1 ] || { echo "no está aislada (red): la prueba no sigue"; exit 2; }
ip link set lo up || { echo "no se pudo levantar la red de la prueba"; exit 2; }
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
n=0
for d in ${MSX_VER//:/ }; do                  # solo lectura, y antes de tapar /home
  n=$((n + 1)); mkdir -p "$COPIA/ver/$n"
  mount --bind "$d" "$COPIA/ver/$n" && mount -o remount,bind,ro "$COPIA/ver/$n" || { echo "no se pudo conservar $d"; exit 2; }
done
mount -t tmpfs tmpfs /home || { echo "no se pudo tapar /home: la prueba no sigue"; exit 2; }
[ "$(stat -f -c %T /home)" = tmpfs ] && [ -z "$(ls -A /home)" ] || { echo "/home no quedó vacío: la prueba no sigue"; exit 2; }
mount -t tmpfs tmpfs /etc/systemd/system || { echo "no se pudo tapar /etc/systemd/system: la prueba no sigue"; exit 2; }
[ -z "$(ls -A /etc/systemd/system)" ] || { echo "/etc/systemd/system no quedó vacío: la prueba no sigue"; exit 2; }
n=0
for d in ${MSX_VER//:/ }; do
  n=$((n + 1)); mkdir -p "$d" && mount --bind "$COPIA/ver/$n" "$d" || { echo "no se pudo dejar a la vista $d"; exit 2; }
done
"$PY" -c 'import fastapi, uvicorn' || { echo "dentro de la prueba, $PY ya no encuentra fastapi y uvicorn"; exit 2; }

M=/home/mediasat; E=$M/streamer; I=$COPIA/informe; MED=$I/medidas
export MSX_SD=$I/systemd MSX_PY
NUEVA=$(cat "$COPIA/nueva/VERSION"); VIEJA=$(cat "$COPIA/anterior/VERSION")
mkdir -p $E/bin $E/logs $E/run $E/venv/bin $M/recorder "$MSX_SD" "$MED" "$COPIA/falsos"

cat > "$COPIA/falsos/ssh" <<'FIN'
#!/bin/sh
# ssh de mentira: la «estación» es este mismo equipo, dentro de la prueba
shift
exec bash -c "$*"
FIN
cat > "$COPIA/falsos/sudo" <<'FIN'
#!/bin/sh
# sudo de mentira: la prueba ya es root, y el usuario mediasat no existe aquí
[ "$1" = "-u" ] && shift 2
exec "$@"
FIN
printf '#!/bin/sh\nexec "$MSX_PY" "%s" systemctl "$@"\n' "$AYUDA" > "$COPIA/falsos/systemctl"
chmod 755 "$COPIA"/falsos/*
export PATH="$COPIA/falsos:$PATH"

fallos=0
bien() { echo "   bien: $*"; }
mal() { echo "   FALLA: $*"; fallos=$((fallos + 1)); }
api() {         # api <método> <ruta> [json]: el panel, con una sesión de administrador
  curl -s --noproxy '*' -X "$1" -H 'Cookie: msr_sesion=admin' -H 'Content-Type: application/json' ${3:+-d "$3"} "http://127.0.0.1:8095$2"
}
ident() { "$PY" -c 'import json, sys; print(json.load(sys.stdin)["id"])'; }
esperar() {     # esperar <segundos> <comando que debe acabar bien>
  local hasta=$((SECONDS + $1)); shift
  until "$@" > /dev/null 2>&1; do [ $SECONDS -lt $hasta ] || return 1; sleep 0.3; done
}
foto() { "$PY" "$AYUDA" foto "$I/$1.json"; }
desplegar() {   # desplegar <nueva|anterior|rota> <nombre del paso>: el desplegar.sh de esa versión, tal cual
  (cd "$COPIA/$1" && scripts/desplegar.sh estacion) > "$I/$2.txt" 2>&1 < /dev/null
  echo $? > "$I/$2.salida"
}
SENAL=(-re -f lavfi -i "testsrc2=size=640x360:rate=30" -f lavfi -i "sine=frequency=1000:sample_rate=48000"
       -c:v libx264 -preset ultrafast -tune zerolatency -g 30 -b:v 800k -pix_fmt yuv420p -c:a aac -b:a 96k -f mpegts)
codificador() { # codificador <nombre> <a dónde>: una cámara de pruebas. Si quien la lee se desconecta, termina
  setsid ffmpeg -hide_banner -nostdin -loglevel error "${SENAL[@]}" "$2" > "$MED/$1.log" 2>&1 < /dev/null &
  echo $! > "$MED/$1.pid"
}
receptor() {    # receptor <nombre> <opciones de entrada de ffmpeg…>: lo que recibe pasa por el medidor
  local n=$1; shift
  MEDIDA="$MED/$n.medida" LOG="$MED/$n.log" AYUDA="$AYUDA" setsid bash -c \
    'ffmpeg -hide_banner -nostdin -loglevel error "$@" -map 0 -c copy -flush_packets 1 -f mpegts - 2>> "$LOG" | "$MSX_PY" "$AYUDA" medidor "$MEDIDA"' \
    _ "$@" < /dev/null > /dev/null 2>&1 &
}
RECEPTORES="srt-llama srt-escucha rtmp canal hub directo"
conectadas() {  # todas las salidas con proceso, enviando según el panel
  api GET /api/flujos | "$PY" -c '
import json, sys
s = [x["estado"] for f in json.load(sys.stdin) for x in f["salidas"] if x["tipo"] != "hls"]
sys.exit(0 if len(s) == 6 and all(e == "conectada" for e in s) else 1)'
}
llega() {       # llega <receptor>: su medidor ha apuntado algo en los últimos 3 s
  [ -n "$(awk -v t="$(date +%s)" '$1 >= t - 3 && $2 > 0' "$MED/$1.medida" 2> /dev/null)" ]
}
llegan() { local r; for r in $RECEPTORES; do llega "$r" || return 1; done; }
tamano() { stat -c %s "$1" 2> /dev/null || echo 0; }
hls_avanza() {  # la lista HLS ha avanzado en los últimos segundos
  local t p; t=$(date +%s); p=$("$PY" "$AYUDA" parada "$MED/hls.medida" $((t - 6)) "$t")
  [ "$p" != inf ] && [ "${p%.*}" -lt 4 ]
}
quejas() {      # lo que el supervisor y el panel han escrito de malo desde que se les pidió arrancar
  "$PY" "$AYUDA" quejas "$MSX_SD/msxs-supervisor.log" "$MSX_SD/msxs-web.log"
}
# ms sin que a un receptor le llegue nada: más que eso cuenta como corte. Estas señales llegan a
# ráfagas, una cada cuarto de segundo más o menos (el audio viaja agrupado): por eso junto al mayor
# hueco de cada paso se da el que había con todo en reposo, medido antes del primer relevo
HUECO_MAX=500
REPOSO=0
en_reposo() {   # el mayor hueco de los últimos 12 s, sin que nadie toque nada
  local r hueco t; t=$(date +%s.%N)
  for r in $RECEPTORES; do
    hueco=$("$PY" "$AYUDA" hueco "$MED/$r.medida" "$(awk -v t="$t" 'BEGIN { printf "%.3f", t - 12 }')" "$t")
    [ "$hueco" != inf ] && [ "$hueco" -gt "$REPOSO" ] && REPOSO=$hueco
  done
}
al_aire() {     # al_aire <paso> <T0> <T1> [--solo-al-aire]: lo que un despliegue no debe tocar
  local paso=$1 t0=$2 t1=$3 r hueco peor=0 cortados="" sueltas="" dif
  dif=$("$PY" "$AYUDA" comparar "$I/$paso-antes.json" "$I/$paso-despues.json" ${4:-})
  [ -z "$dif" ] && bien "las 6 salidas son los mismos procesos, MediaMTX no se reinició y sus 3 entradas no se soltaron" \
    || mal "algo cambió: $(echo "$dif" | tr '\n' ';')"
  for r in $RECEPTORES; do
    hueco=$("$PY" "$AYUDA" hueco "$MED/$r.medida" "$t0" "$t1")
    if [ "$hueco" = inf ] || [ "$hueco" -gt $HUECO_MAX ]; then cortados="$cortados $r ($hueco ms)"; fi
    [ "$hueco" != inf ] && [ "$hueco" -gt "$peor" ] && peor=$hueco
  done
  [ -z "$cortados" ] && bien "a los 6 receptores les siguió llegando señal: el mayor hueco fue de $peor ms (en reposo, de $REPOSO ms)" \
    || mal "receptores con corte (en reposo, el mayor hueco era de $REPOSO ms):$cortados"
  for r in pull canal hub directo; do kill -0 "$(cat "$MED/cod-$r.pid")" 2> /dev/null || sueltas="$sueltas $r"; done
  [ -z "$sueltas" ] && bien "las 4 cámaras de prueba siguen conectadas: nadie las soltó" || mal "se soltó alguna cámara:$sueltas"
}
comprobar() {   # comprobar <paso> <versión que debe quedar> <T0> <T1>
  local paso=$1 version=$2 q
  [ "$(cat "$I/$paso.salida")" = 0 ] && grep -q "servicios \[active active active \] versión \"$version\"" "$I/$paso.txt" \
    && bien "desplegar.sh termina bien y deja los tres servicios en marcha con la $version" \
    || mal "desplegar.sh no deja los tres servicios en marcha con la $version: $(tail -3 "$I/$paso.txt" | tr '\n' ' ')"
  al_aire "$paso" "$3" "$4"
  q=$(quejas)
  [ -z "$q" ] && bien "el supervisor no escribió nada en su registro y el panel no anotó ningún error" \
    || mal "el supervisor o el panel se quejan: $(echo "$q" | head -4 | cut -c1-220 | tr '\n' ';')"
}
previo() {      # previo <nombre>: scripts/previo.sh de la versión nueva, con fotos de antes y de después
  foto "$1-antes"; (cd $E && find . | sort) > "$I/$1-archivos-antes.txt"
  (cd "$COPIA/nueva" && scripts/previo.sh estacion) > "$I/$1.txt" 2>&1 < /dev/null
  echo $? > "$I/$1.salida"
  foto "$1-despues"; (cd $E && find . | sort) > "$I/$1-archivos-despues.txt"
}
intacto() {     # intacto <nombre>: previo.sh no tocó nada ni escribió lo que no debe
  local dif
  dif=$("$PY" "$AYUDA" comparar "$I/$1-antes.json" "$I/$1-despues.json" --solo-al-aire)
  dif="$dif$("$PY" - "$I/$1-antes.json" "$I/$1-despues.json" <<'FIN'
import json, sys
a, d = (json.load(open(x)) for x in sys.argv[1:3])
for que in ("eventos", "mediamtx", "servicios", "version"):
    if a[que] != d[que]:
        print(f"cambió: {que}")
FIN
)"
  cmp -s "$I/$1-archivos-antes.txt" "$I/$1-archivos-despues.txt" || dif="$dif; hay archivos de más o de menos en la carpeta del servicio"
  [ -z "$(ls -d /tmp/msxs-previo.* 2> /dev/null)" ] || dif="$dif; dejó su carpeta temporal"
  grep -q "frase-de-paso-de-prueba-1\|clave-del-streamid\|clave-de-emision-de-prueba\|://" "$I/$1.txt" && dif="$dif; escribe direcciones o claves"
  [ -z "$dif" ] && bien "previo.sh no tocó nada: mismos procesos y señales, MediaMTX y servicios como estaban, ni un evento ni un archivo nuevo, y no escribe direcciones ni claves" \
    || mal "previo.sh tocó algo: $(echo "$dif" | tr '\n' ';')"
}
paso() {        # paso <nombre> <nueva|anterior|rota|atajo> [espera]: foto, despliegue, espera y foto
  foto "$1-antes"; T0=$(date +%s.%N)
  if [ "$2" = atajo ]; then       # «Volver a la 0.3.9» en la estación, como lo da docs/instalacion.md
    { sudo -u mediasat bash -c "cd $E && rsync -a releases/v$VIEJA/streamer/ streamer/ && cp releases/v$VIEJA/VERSION ."
      sudo systemctl restart msxs-supervisor msxs-web; sleep 4
      echo "== estacion: servicios [$(systemctl is-active msxs-mediamtx msxs-supervisor msxs-web | tr '\n' ' ')] versión $(curl -s --noproxy '*' http://127.0.0.1:8095/api/version)"
    } > "$I/$1.txt" 2>&1
    echo 0 > "$I/$1.salida"
  else
    desplegar "$2" "$1"
  fi
  sleep "${3:-16}"; T1=$(date +%s.%N); foto "$1-despues"       # el HLS tarda unos 10 s en volver la primera vez
}

echo "0 · la estación de mentira, con la $VIEJA instalada"
cp "$COPIA/bin/mediamtx" $E/bin/mediamtx
printf '#!/bin/sh\nexec "%s" -m uvicorn "$@"\n' "$PY" > $E/venv/bin/uvicorn
printf '#!/bin/sh\nexec "%s" "$@"\n' "$PY" > $E/venv/bin/python
chmod 755 $E/venv/bin/*
TOPE=$(sysctl -n net.core.rmem_max 2> /dev/null || echo 212992); [ "$TOPE" -lt 8388608 ] || TOPE=8388608
sed "s/^udpReadBufferSize:.*/udpReadBufferSize: $TOPE/" "$COPIA/anterior/config/mediamtx.yml" > $E/mediamtx.yml
cat > $E/config.json <<FIN
{
  "host_publico": "127.0.0.1",
  "ffmpeg": "$(command -v ffmpeg)",
  "recorder_canales": "/home/mediasat/recorder/canales.json",
  "mediamtx_api": "http://127.0.0.1:9997",
  "mediamtx_srt": "srt://127.0.0.1:8890",
  "hls_puerto": 8888,
  "composicion_max": 3,
  "recorder_config": "/home/mediasat/recorder/config.json",
  "hub_tokens": {}
}
FIN
echo '[{"id": "canal1", "nombre": "Canal 1", "reenvio_udp": 9301, "url": ""}]' > $M/recorder/canales.json
echo '{}' > $M/recorder/config.json
cp "$COPIA"/anterior/systemd/msxs-*.service /etc/systemd/system/
setsid "$PY" "$COPIA/recorder_falso.py" 8081 > "$I/recorder.log" 2>&1 < /dev/null &
systemctl start msxs-mediamtx
esperar 15 curl -fs --noproxy '*' http://127.0.0.1:9997/v3/paths/list || { echo "MediaMTX no arranca:"; cat "$MSX_SD/msxs-mediamtx.log"; exit 2; }
desplegar anterior 0-instalacion
esperar 20 curl -fs --noproxy '*' http://127.0.0.1:8095/api/version
[ "$(curl -s --noproxy '*' http://127.0.0.1:8095/api/version)" = "\"$VIEJA\"" ] \
  && bien "el desplegar.sh de la $VIEJA la deja en marcha: $(tail -1 "$I/0-instalacion.txt" | cut -c1-90)" \
  || { echo "la $VIEJA no arranca en la estación de mentira:"; cat "$I/0-instalacion.txt"; tail -20 "$MSX_SD"/*.log; exit 2; }

echo "1 · salidas al aire con la $VIEJA: 4 flujos, 6 destinos con proceso y uno HLS"
receptor srt-llama -i "srt://0.0.0.0:9201?mode=listener&passphrase=frase-de-paso-de-prueba-1"
receptor rtmp -listen 1 -i "rtmp://127.0.0.1:9203/live/clave-de-emision-de-prueba"
receptor canal -i "srt://0.0.0.0:9204?mode=listener"
receptor hub -i "srt://0.0.0.0:9205?mode=listener"
receptor directo -i "srt://0.0.0.0:9206?mode=listener"
codificador cod-pull "srt://0.0.0.0:9101?mode=listener&pkt_size=1316"
codificador cod-canal "udp://127.0.0.1:9301?pkt_size=1316"
codificador cod-hub "srt://0.0.0.0:9102?mode=listener&pkt_size=1316"
codificador cod-directo "srt://0.0.0.0:9104?mode=listener&pkt_size=1316"
F_PULL=$(api POST /api/flujos '{"nombre":"Pull","entrada":"pull","url":"srt://127.0.0.1:9101","latencia_ms":200}' | ident)
S_LLAMA=$(api POST "/api/flujos/$F_PULL/salidas" '{"nombre":"SRT llama","tipo":"srt","url":"srt://127.0.0.1:9201","modo":"caller","streamid":"#!::r=prueba,password=clave-del-streamid","passphrase":"frase-de-paso-de-prueba-1","latencia_ms":200}' | ident)
S_ESCUCHA=$(api POST "/api/flujos/$F_PULL/salidas" '{"nombre":"SRT escucha","tipo":"srt","url":"srt://0.0.0.0:9202","modo":"listener","latencia_ms":200}' | ident)
api POST "/api/flujos/$F_PULL/salidas" '{"nombre":"RTMP","tipo":"rtmp","url":"rtmp://127.0.0.1:9203/live","clave":"clave-de-emision-de-prueba"}' > /dev/null
api POST "/api/flujos/$F_PULL/salidas" '{"nombre":"HLS","tipo":"hls"}' > /dev/null
F_CANAL=$(api POST /api/flujos '{"nombre":"Canal 1","entrada":"recorder","canal":"canal1"}' | ident)
api POST "/api/flujos/$F_CANAL/salidas" '{"nombre":"Canal a SRT","tipo":"srt","url":"srt://127.0.0.1:9204","latencia_ms":200}' > /dev/null
# los flujos de hub los resuelve el panel con la lista de feeds del Recorder: aquí se escriben como él los guarda
"$PY" - <<'FIN'
import sqlite3
c = sqlite3.connect("/home/mediasat/streamer/streamer.db", timeout=10, isolation_level=None)
alta = """INSERT INTO flujos(id, nombre, entrada, url, latencia_ms, hub, feed, etiqueta, respaldos, directo)
          VALUES (?, ?, 'hub', ?, 200, '127.0.0.1', 'prueba', ?, ?, ?)"""
c.execute(alta, ("hub-prueba", "Hub", "srt://127.0.0.1:9102?streamid=read:prueba", "hub1 · PRUEBA",
                 '["srt://127.0.0.1:9109?streamid=read:prueba"]', 0))
c.execute(alta, ("directo-prueba", "Directo 1080i", "srt://127.0.0.1:9104?streamid=play/prueba", "hub1 · PRUEBA 1080i", "[]", 1))
FIN
api POST /api/flujos/hub-prueba/salidas '{"nombre":"Hub a SRT","tipo":"srt","url":"srt://127.0.0.1:9205","latencia_ms":200}' > /dev/null
api POST /api/flujos/directo-prueba/salidas '{"nombre":"Directo a SRT","tipo":"srt","url":"srt://127.0.0.1:9206","latencia_ms":200}' > /dev/null
esperar 30 sh -c "ss -Hlun 'sport = :9202' | grep -q ." && receptor srt-escucha -i "srt://127.0.0.1:9202?mode=caller"
setsid "$PY" "$AYUDA" hls "http://127.0.0.1:8888/$F_PULL/index.m3u8" "$MED/hls.medida" > /dev/null 2>&1 < /dev/null &
if esperar 90 conectadas && esperar 30 llegan; then
  bien "las 6 salidas envían y a los 6 receptores les llega señal"
else
  echo "las salidas no llegan a estar al aire con la $VIEJA: la prueba no sigue"; api GET /api/flujos | cut -c1-3000
  tail -5 "$MSX_SD"/*.log "$MED"/*.log $E/logs/*.log 2> /dev/null | cut -c1-300; exit 2
fi
esperar 30 hls_avanza && bien "el HLS del flujo se puede leer" || mal "el HLS del flujo no llega a servirse"
sleep 13; en_reposo
[ "$REPOSO" -gt 0 ] && [ "$REPOSO" -le $HUECO_MAX ] && bien "con todo en reposo, el mayor hueco entre dos llegadas a un receptor es de $REPOSO ms" \
  || mal "en reposo ya hay huecos de $REPOSO ms: la medida no sirve"

echo "2 · el relevo, como en docs/instalacion.md: previo.sh, rtmp_puerto en config.json y desplegar.sh sin opciones"
previo 1-previo
[ "$(cat "$I/1-previo.salida")" = 1 ] && grep -q "^Resumen: 6 procesos al aire; se conservan 6, se relanzarían 0, se pararían 0\. SE CORTARÍA: el HLS de quien está mirando, unos segundos\.$" "$I/1-previo.txt" \
  && bien "previo.sh lo anuncia: la versión nueva conservaría los 6 procesos y solo cortaría unos segundos el HLS de quien mira" \
  || mal "previo.sh no anuncia eso (salida $(cat "$I/1-previo.salida")): $(grep "Resumen\|RELANZ\|PARAR\|CORTAR\|!!" "$I/1-previo.txt" | head -4 | tr '\n' ';')"
grep -q "paths: no le pediría crear, cambiar ni borrar ninguno" "$I/1-previo.txt" \
  && grep -q "configuración general: le pediría cambiar hlsTrustedProxies, webrtcTrustedProxies$" "$I/1-previo.txt" \
  && bien "y que a MediaMTX solo le pediría las dos listas de proxies de confianza" \
  || mal "previo.sh anuncia otros cambios en MediaMTX: $(sed -n '/^MediaMTX/,/^Resumen/p' "$I/1-previo.txt" | tr '\n' ';')"
intacto 1-previo
"$PY" - <<'FIN'
import json, os, pathlib
p = pathlib.Path("/home/mediasat/streamer/config.json")
c = json.loads(p.read_text())
c["rtmp_puerto"] = 1945
nuevo = p.with_name("config.json.nuevo")
nuevo.write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
os.chmod(nuevo, p.stat().st_mode & 0o777)
os.replace(nuevo, p)
FIN
paso 1-relevo nueva
comprobar 1-relevo "$NUEVA" "$T0" "$T1"
ARRANQUE=$("$PY" -c '
import json, sys
d = json.load(open(sys.argv[1])); t0 = float(sys.argv[2]) - 1
print(" | ".join(e["texto"] for e in d["eventos"] if e["t"] >= t0))' "$I/1-relevo-despues.json" "$T0")
echo "$ARRANQUE" | grep -q "arranque: 6 procesos seguían en marcha y se conservan, 0 lanzados" \
  && bien "el supervisor nuevo lo dice así: 6 procesos seguían en marcha y se conservan, 0 lanzados" \
  || mal "el supervisor nuevo no conservó los 6 procesos: $ARRANQUE"
echo "$ARRANQUE" | grep -q "relanzada\|detenid\|sin dueño\|declarado" && mal "paró, relanzó o volvió a declarar algo: $ARRANQUE" \
  || bien "no paró ni relanzó nada, ni volvió a declarar ninguna entrada en MediaMTX"
"$PY" - "$I/1-relevo-despues.json" <<'FIN' && bien "MediaMTX sigue sin RTMP ni WebRTC (nadie los usa todavía) y ya juzga por X-Forwarded-For" || mal "MediaMTX no quedó como se esperaba"
import json, sys
m = json.load(open(sys.argv[1]))["mediamtx"]
assert m["rtmp"] is False and m["webrtc"] is False and m["hls"] is True and m["srt"] is True, m
assert m["hlsTrustedProxies"] and m["webrtcTrustedProxies"], m
FIN
PARADA=$("$PY" "$AYUDA" parada "$MED/hls.medida" "$T0" "$T1")
[ "$PARADA" != inf ] && [ "${PARADA%.*}" -lt 30 ] && esperar 20 hls_avanza \
  && bien "el HLS estuvo $PARADA s sin avanzar, una vez, y volvió solo" || mal "el HLS no volvió (parada: $PARADA s)"
api GET /api/matriz | "$PY" -c 'import json, sys; d = json.load(sys.stdin); assert isinstance(d, dict) and "detail" not in d' 2> /dev/null \
  && [ "$(api GET /api/flujos | "$PY" -c 'import json, sys; print(len(json.load(sys.stdin)))')" = 4 ] \
  && bien "el panel nuevo responde: el hub, y los 4 flujos de antes con sus salidas" || mal "el panel nuevo no responde como debe"
[ "$(stat -c %a $E/streamer.db)" = 600 ] && bien "la base queda con permisos 600" || mal "la base quedó con permisos $(stat -c %a $E/streamer.db)"

echo "3 · con la versión nueva: parar y volver a iniciar una salida de las de antes"
PID_ANTES=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["salidas"][sys.argv[2]]["pid"])' "$I/1-relevo-despues.json" "$S_LLAMA")
api POST "/api/salidas/$S_LLAMA/detener" > /dev/null
esperar 15 sh -c "! kill -0 $PID_ANTES 2> /dev/null" && bien "la para: el proceso que venía de la $VIEJA termina" || mal "no paró el proceso $PID_ANTES"
sleep 2; receptor srt-llama -i "srt://0.0.0.0:9201?mode=listener&passphrase=frase-de-paso-de-prueba-1"
REG=$(tamano "$E/logs/$S_LLAMA.log")
api POST "/api/salidas/$S_LLAMA/iniciar" > /dev/null
esperar 60 conectadas && esperar 30 llega srt-llama && bien "la vuelve a iniciar y al receptor le llega señal otra vez" || mal "no volvió a enviar"
tail -c +$((REG + 1)) "$E/logs/$S_LLAMA.log" > "$I/3-registro-nuevo.txt"
grep -q "\*\*\*" "$I/3-registro-nuevo.txt" && ! grep -q "frase-de-paso-de-prueba-1\|clave-del-streamid" "$I/3-registro-nuevo.txt" \
  && bien "lo que la versión nueva anota al lanzarla ya no lleva la passphrase ni el streamid" || mal "el registro nuevo lleva claves o no anota el arranque"
sleep 5

echo "4 · volver a la $VIEJA, con el desplegar.sh de la $VIEJA"
paso 4-vuelta anterior
comprobar 4-vuelta "$VIEJA" "$T0" "$T1"

echo "5 · el relevo otra vez: ya no toca el HLS"
previo 5-previo
[ "$(cat "$I/5-previo.salida")" = 0 ] && grep -q "^Resumen: 6 procesos al aire; se conservan 6, se relanzarían 0, se pararían 0\. No se cortaría nada\.$" "$I/5-previo.txt" \
  && bien "previo.sh lo anuncia: esta vez no se cortaría nada" \
  || mal "previo.sh no anuncia eso (salida $(cat "$I/5-previo.salida")): $(grep "Resumen\|RELANZ\|PARAR\|CORTAR\|!!" "$I/5-previo.txt" | head -4 | tr '\n' ';')"
intacto 5-previo
paso 5-relevo nueva
comprobar 5-relevo "$NUEVA" "$T0" "$T1"
PARADA=$("$PY" "$AYUDA" parada "$MED/hls.medida" "$T0" "$T1")
[ "$PARADA" != inf ] && [ "${PARADA%.*}" -lt 6 ] && bien "esta vez el HLS no se paró (mayor rato sin avanzar: $PARADA s)" \
  || mal "el HLS se paró otra vez: $PARADA s"

echo "6 · volver a la $VIEJA por el atajo de la guía, en la propia estación"
grep -q "rsync -a releases/v$VIEJA/streamer/ streamer/ && cp releases/v$VIEJA/VERSION ." "$COPIA/instalacion.md" \
  && grep -q "sudo systemctl restart msxs-supervisor msxs-web" "$COPIA/instalacion.md" \
  && bien "es la orden que da docs/instalacion.md" || mal "docs/instalacion.md da otra orden para volver"
paso 6-atajo atajo
comprobar 6-atajo "$VIEJA" "$T0" "$T1"

echo "7 · si la versión nueva no llegara a arrancar: lo que está al aire sigue y desplegar.sh lo dice"
cp -a "$COPIA/nueva" "$COPIA/rota"
sed -i '1i raise RuntimeError("estropeado a propósito: es el ensayo de un relevo que sale mal")' \
  "$COPIA/rota/streamer/supervisor.py" "$COPIA/rota/streamer/servidor.py"
paso 7-rota rota 6
[ "$(cat "$I/7-rota.salida")" != 0 ] && grep -q "^!! estacion no quedó con la v$NUEVA en marcha" "$I/7-rota.txt" \
  && bien "desplegar.sh termina con error y dice que la $NUEVA no quedó en marcha" \
  || mal "desplegar.sh no avisa de que la versión no arrancó (salida $(cat "$I/7-rota.salida")): $(tail -2 "$I/7-rota.txt" | tr '\n' ' ')"
al_aire 7-rota "$T0" "$T1" --solo-al-aire

echo "8 · y de ahí se vuelve a la $VIEJA, con el desplegar.sh de la $VIEJA"
paso 8-vuelta anterior
comprobar 8-vuelta "$VIEJA" "$T0" "$T1"

echo
if [ $fallos -eq 0 ]; then echo "TODO BIEN"; exit 0; fi
echo "FALLOS: $fallos. Lo que dijo cada paso:"
for f in "$I"/*.txt; do echo "--- $(basename "$f")"; cat "$f"; done
for f in "$MSX_SD"/*.log; do echo "--- $(basename "$f") (final)"; tail -40 "$f"; done
exit 1
