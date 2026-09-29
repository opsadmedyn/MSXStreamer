#!/usr/bin/env bash
# Diagnóstico de calidad de MSX Streamer. Solo lee: no cambia ni reinicia nada. Desde el Mac:
#   ssh z8 'sudo bash -s' < scripts/diagnostico.sh            (canal1)
#   ssh z8 'sudo bash -s -- canal2' < scripts/diagnostico.sh  (otro canal)
# Sirve para ver en qué tramo se pierden paquetes cuando un destino llega con artefactos:
#   origen -> grabador (trozos en disco) -> UDP local -> MediaMTX -> ffmpeg de salida -> destino
CANAL="${1:-canal1}"
B=/home/mediasat/streamer
FF=/usr/local/bin/ffmpeg
PATH_MTX="rec_$CANAL"

echo "== servicios"
systemctl is-active msxs-mediamtx msxs-supervisor msxs-web "msr-grabador@$CANAL" | paste -sd' '
echo "carga: $(cut -d' ' -f1-3 /proc/loadavg) · rmem_default=$(sysctl -n net.core.rmem_default) rmem_max=$(sysctl -n net.core.rmem_max)"

PUERTO=$(python3 -c "
import json,sys
c=[c for c in json.load(open('/home/mediasat/recorder/canales.json')) if c['id']=='$CANAL']
print(c[0].get('reenvio_udp','') if c else '')")
echo "reenvío UDP de $CANAL: ${PUERTO:-no configurado}"

echo "== 1. UDP local (grabador -> MediaMTX): paquetes descartados por el receptor en 10 s"
if [ -n "$PUERTO" ]; then
  dropped() { ss -Huanm "sport = :$PUERTO" | grep -o 'd[0-9]*)' | tr -dc 0-9; }
  ss -Huanm "sport = :$PUERTO" | grep -o 'skmem:([^)]*)'
  D1=$(dropped); sleep 10; D2=$(dropped)
  echo "descartados en 10 s: $(( ${D2:-0} - ${D1:-0} ))  (0 = el tramo local está limpio)"
fi

echo "== 2. señal dentro de MediaMTX ($PATH_MTX): errores al decodificar 20 s"
timeout 30 nice -n 10 $FF -hide_banner -v error -i "srt://127.0.0.1:8890?streamid=read:$PATH_MTX" -t 20 -f null - 2>&1 \
  | tee /tmp/msxs-diag.txt | head -5
echo "líneas de error: $(wc -l < /tmp/msxs-diag.txt)"

echo "== 3. trozos grabados en disco (lo que llega del origen): errores en los últimos 5"
find "/srv/grabaciones/$CANAL" -maxdepth 1 -name '*.ts' -mmin -2 -printf '%T@ %p\n' | sort -n | tail -6 | head -5 \
  | while read -r _ f; do echo "$(basename "$f"): $(nice -n 10 $FF -hide_banner -v error -i "$f" -f null - 2>&1 | wc -l) errores"; done

echo "== 4. MediaMTX, últimos 15 min (lectores lentos, pérdidas, errores)"
journalctl -u msxs-mediamtx --since "-15 min" --no-pager -o cat | grep -iE 'slow|discard|lost|error|decode|timeout|closed' | tail -12

echo "== 5. salidas: configuración y estado"
python3 - "$B/streamer.db" "$B/run" <<'EOF'
import sqlite3, sys, pathlib
c = sqlite3.connect(sys.argv[1])
for sid, nombre, tipo, url, modo, lat, rein, err in c.execute(
        "SELECT s.id, s.nombre, s.tipo, s.url, s.modo, s.latencia_ms, e.reinicios, e.error "
        "FROM salidas s LEFT JOIN estado_salidas e ON e.salida = s.id WHERE s.activo = 1"):
    prog = pathlib.Path(sys.argv[2], f"{sid}.prog")
    ult = {}
    if prog.exists():
        for linea in prog.read_text().splitlines()[-20:]:
            k, _, v = linea.partition("=")
            ult[k] = v
    print(f"{nombre} [{tipo}] {url.split('?')[0]} modo={modo} latencia={lat} ms reinicios={rein} "
          f"velocidad={ult.get('speed', '?')} bitrate={ult.get('bitrate', '?')} error={err or '-'}")
EOF
for f in "$B"/logs/*.log; do [ -s "$f" ] && { echo "-- $(basename "$f"), últimas líneas:"; tail -5 "$f"; }; done

echo "== 6. grabador de $CANAL, últimos 15 min"
journalctl -u "msr-grabador@$CANAL" --since "-15 min" --no-pager -o cat | tail -8
