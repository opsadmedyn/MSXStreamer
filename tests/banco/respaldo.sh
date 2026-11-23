#!/usr/bin/env bash
# Prueba del respaldo a Google Drive y de su cifrado, sin estación y sin Drive:
#   sudo tests/banco/respaldo.sh
# Ejecuta scripts/respaldar.sh, scripts/respaldo_drive.py y scripts/instalar-respaldos.sh tal cual,
# dentro de un espacio de montaje propio en el que /home es un disco en memoria vacío. Ahí monta una
# estación de mentira (/home/mediasat, con una clave reconocible en streamer.db), un Drive que es una
# carpeta (las bibliotecas de Google se cambian por unas de pega que guardan lo «subido» y lo que
# Drive anotaría de cada archivo) y un ssh que solo apunta lo que se le pide. Nada de eso existe
# fuera de la prueba ni queda al terminar, y el /home de verdad no se toca.
# Hace falta Linux, root, unshare, openssl y python3. Tarda unos 15 s.
set -uo pipefail
SECRETO="CLAVE-DE-EMISION-DE-PRUEBA-7f3a"
ABRIR="openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256"      # la orden de docs/instalacion.md

if [ -z "${MSX_PRUEBA_RESPALDO:-}" ]; then      # fuera: copia lo que se prueba y vuelve a entrar, ya aislada
  [ "$(uname -s)" = Linux ] || { echo "esta prueba solo corre en Linux"; exit 2; }
  [ "$(id -u)" = 0 ] || { echo "hace falta root: sudo $0"; exit 2; }
  for o in unshare openssl python3; do command -v "$o" > /dev/null || { echo "falta $o"; exit 2; }; done
  YO="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  cd "$(dirname "$YO")/../.."
  COPIA=$(mktemp -d /tmp/msx-prueba-respaldo.XXXXXX)      # fuera de /home, que dentro de la prueba queda tapado
  trap 'rm -rf "$COPIA"' EXIT
  mkdir -p "$COPIA/scripts" "$COPIA/systemd"
  cp scripts/respaldar.sh scripts/respaldo_drive.py scripts/instalar-respaldos.sh "$COPIA/scripts/"
  cp systemd/msx-respaldo.service systemd/msx-respaldo.timer "$COPIA/systemd/"
  cp docs/instalacion.md "$YO" "$COPIA/"
  MSX_PRUEBA_RESPALDO="$COPIA" unshare -m --propagation private bash "$COPIA/$(basename "$YO")"
  exit $?
fi

# dentro: solo sigue si de verdad está en un espacio de montaje propio y /home quedó vacío
COPIA="$MSX_PRUEBA_RESPALDO"
[ "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)" ] || { echo "no está aislada: la prueba no sigue"; exit 2; }
mount -t tmpfs tmpfs /home || { echo "no se pudo tapar /home: la prueba no sigue"; exit 2; }
[ "$(stat -f -c %T /home)" = tmpfs ] && [ -z "$(ls -A /home)" ] || { echo "/home no quedó vacío: la prueba no sigue"; exit 2; }

M=/home/mediasat; D=$M/drive-falso; C=$M/respaldos/clave-respaldo; P=$M/ssh-falso/pedido.txt
R=$M/respaldos/bin/respaldar.sh
mkdir -p $M/streamer/streamer $M/recorder/venv/bin $M/respaldos/bin $D $M/ssh-falso $M/sin-openssl \
         $M/pega/google/auth/transport $M/pega/google/oauth2 $M/pega/googleapiclient
echo '{"rtmp_puerto": 1945}' > $M/streamer/config.json; echo "0.4.0-alfa" > $M/streamer/VERSION
echo '{"estacion": "banco"}' > $M/recorder/config.json; echo "9.9.9" > $M/recorder/VERSION
python3 - $M/streamer/streamer.db "$SECRETO" <<'FIN'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("create table destinos(id text, clave text)")
c.execute("insert into destinos values (?, ?)", ("youtube", sys.argv[2]))
c.commit()
FIN
install -m 750 "$COPIA/scripts/respaldar.sh" "$COPIA/scripts/respaldo_drive.py" $M/respaldos/bin/

# el Drive de mentira: lo que respaldo_drive.py importa de Google, hecho con una carpeta
touch $M/pega/google/__init__.py $M/pega/google/auth/__init__.py $M/pega/google/auth/transport/__init__.py \
      $M/pega/google/oauth2/__init__.py $M/pega/googleapiclient/__init__.py
echo "class Request: pass" > $M/pega/google/auth/transport/requests.py
cat > $M/pega/google/oauth2/credentials.py <<'FIN'
class Credentials:
    valid = True

    @classmethod
    def from_authorized_user_file(cls, archivo):
        return cls()
FIN
cat > $M/pega/googleapiclient/http.py <<'FIN'
class MediaFileUpload:
    def __init__(self, archivo, mimetype=None, resumable=False):
        self.archivo, self.mimetype = archivo, mimetype
FIN
cat > $M/pega/googleapiclient/discovery.py <<'FIN'
"""Un Drive que es una carpeta: guarda lo que se «sube» y apunta lo que Drive anotaría de cada archivo."""
import json
import pathlib
import shutil

DRIVE = pathlib.Path("/home/mediasat/drive-falso")


class _Orden:
    def __init__(self, hacer):
        self.execute = hacer


class _Archivos:
    def list(self, **_):
        return _Orden(lambda: {"files": []})

    def create(self, body, media_body=None, fields=None):
        def crear():
            if media_body is None:
                return {"id": "carpeta-" + body["name"]}
            for k, v in body.get("appProperties", {}).items():      # el límite de Drive por propiedad
                assert len((k + v).encode()) <= 124, f"la propiedad {k} pasa de 124 bytes"
            shutil.copyfile(media_body.archivo, DRIVE / body["name"])
            with open(DRIVE / "subidas.jsonl", "a") as f:
                f.write(json.dumps({"nombre": body["name"], "tipo": media_body.mimetype, "carpeta": body["parents"][0],
                                    "descripcion": body["description"]}, ensure_ascii=False) + "\n")
            return {"id": "archivo", "size": str((DRIVE / body["name"]).stat().st_size),
                    "webViewLink": "https://drive.de.mentira/" + body["name"]}
        return _Orden(crear)


class _Drive:
    def files(self):
        return _Archivos()


def build(*_, **__):
    return _Drive()
FIN
cat > $M/recorder/venv/bin/python <<'FIN'
#!/bin/sh
# hace de Python del Recorder: el del sistema, con las bibliotecas de pega en vez de las de Google
PYTHONPATH=/home/mediasat/pega exec python3 -S "$@"
FIN
cat > $M/ssh-falso/ssh <<'FIN'
#!/bin/sh
# ssh de mentira: apunta lo que se le pide a la estación y solo ejecuta, aquí mismo, el guion de respaldo
shift; echo "$*" >> /home/mediasat/ssh-falso/pedido.txt
case "$*" in
  "sudo /home/mediasat/respaldos/bin/respaldar.sh "*) exec sh -c "${*#sudo }" ;;
  *) cat > /dev/null; exit 0 ;;
esac
FIN
printf '#!/bin/sh\nexit 1\n' > $M/sin-openssl/openssl      # un openssl que falla
chmod 755 $M/recorder/venv/bin/python $M/ssh-falso/ssh $M/sin-openssl/openssl

fallos=0
bien() { echo "   bien: $*"; }
mal() { echo "   FALLA: $*"; fallos=$((fallos + 1)); }
subido() { python3 -c "import json, sys; print(json.loads(open(sys.argv[1]).read().splitlines()[-1])[sys.argv[2]])" $D/subidas.jsonl "$1" 2> /dev/null; }
vaciar() { rm -f $D/*; sleep 1; }      # el nombre de cada respaldo lleva la hora al segundo
instalar() { (cd "$COPIA" && PATH=$M/ssh-falso:$PATH scripts/instalar-respaldos.sh estacion "$@") < /dev/null; }

echo "1 · sin contraseña de respaldo: a Drive no sube nada, y la copia de la estación se hace igual"
$R --drive prueba > $M/s1.txt 2>&1; s=$?
[ $s -ne 0 ] && [ -z "$(ls $D)" ] && grep -q "no tiene contraseña de respaldo" $M/s1.txt && grep -q -- "--crear-clave" $M/s1.txt \
  && bien "salida $s, dice cómo crearla y en Drive no hay nada" || mal "salida $s, subió algo o no dice cómo crearla"
L=$(ls -1t $M/respaldos/msx-*.tar.gz 2> /dev/null | head -1)
[ -n "$L" ] && [ "$(stat -c %a "$L")" = 600 ] \
  && tar xzOf "$L" --wildcards '*/home/mediasat/streamer/streamer.db' | grep -a "$SECRETO" > /dev/null \
  && bien "en la estación queda $(basename "$L"), con permisos 600 y la base dentro" || mal "no quedó la copia de la estación"
vaciar

echo "2 · crear la contraseña con openssl fallando: da error y no deja ninguna a medias"
PATH=$M/sin-openssl:$PATH $R --crear-clave > $M/s2.txt 2>&1; s=$?
[ $s -ne 0 ] && [ -z "$(ls $C* 2> /dev/null)" ] && bien "salida $s y ningún archivo de contraseña" || mal "salida $s o quedó un archivo"

echo "3 · instalar-respaldos.sh: crea la contraseña antes del primer respaldo, que sube cifrado"
instalar > $M/s3.txt 2>&1; s=$?
[ $s -eq 0 ] && [ "$(grep -n -- "--crear-clave" $P | cut -d: -f1)" -lt "$(grep -n -- "--drive instalacion" $P | cut -d: -f1)" ] 2> /dev/null \
  && bien "termina bien: pide la contraseña y después el primer respaldo" || mal "salida $s o en otro orden"
[ "$(stat -c %a $C 2> /dev/null)" = 600 ] && [ "$(wc -c < $C)" -ge 40 ] \
  && bien "contraseña creada: un archivo con permisos 600 y $(wc -c < $C) caracteres" || mal "la contraseña no quedó bien"
[ -n "$(ls $D/msx-*-instalacion.tar.gz.enc 2> /dev/null)" ] && [ -z "$(ls $D/*.tar.gz 2> /dev/null)" ] \
  && bien "ese primer respaldo sube solo cifrado" || mal "el primer respaldo no subió cifrado"
vaciar

echo "4 · repetir no cambia la contraseña, que es la que abre los respaldos anteriores"
H1=$(sha256sum < $C); $R --crear-clave > $M/s4.txt 2>&1; H2=$(sha256sum < $C)
[ "$H1" = "$H2" ] && grep -q "se conserva" $M/s4.txt && bien "--crear-clave conserva la que hay" || mal "--crear-clave la cambió"
instalar > $M/s4b.txt 2>&1; s=$?; H3=$(sha256sum < $C)
[ $s -eq 0 ] && [ "$H1" = "$H3" ] && [ -n "$(ls $D/msx-*-instalacion.tar.gz.enc 2> /dev/null)" ] \
  && bien "instalar otra vez también la conserva, y su respaldo sube cifrado" || mal "salida $s o el instalador la cambió"
vaciar

echo "5 · con contraseña: a Drive solo sube cifrado, y se abre con la orden de docs/instalacion.md"
$R --drive prueba > $M/s5.txt 2>&1; s=$?
[ $s -eq 0 ] && bien "termina bien" || mal "salida $s"
E=$(ls $D/msx-*-prueba.tar.gz.enc 2> /dev/null | head -1); L=$(ls -1t $M/respaldos/msx-*.tar.gz | head -1)
[ -n "$E" ] && [ -z "$(ls $D/*.tar.gz 2> /dev/null)" ] && [ "$(subido tipo)" = application/octet-stream ] \
  && bien "en Drive queda solo $(basename "$E")" || mal "no subió solo el cifrado"
[ "$(head -c 8 "$E")" = "Salted__" ] && ! grep -a -q "$SECRETO" "$E" && ! grep -a -q "streamer.db" "$E" \
  && bien "dentro no se lee ni la clave ni el nombre de la base" || mal "se lee algo"
$ABRIR -pass file:$C -in "$E" -out $M/abierto.tar.gz && cmp -s $M/abierto.tar.gz "$L" \
  && bien "descifrado, es idéntico a la copia de la estación" || mal "no se abre o no coincide"
mkdir -p $M/restaurado && $ABRIR -pass file:$C -in "$E" | tar xz -C $M/restaurado \
  && [ "$(python3 -c "import glob, sqlite3, sys; print(sqlite3.connect(glob.glob(sys.argv[1])[0]).execute('select clave from destinos').fetchone()[0])" \
          "$M/restaurado/*/home/mediasat/streamer/streamer.db")" = "$SECRETO" ] \
  && bien "restaurado por la tubería documentada: la base trae su clave" || mal "la restauración no da la base"
grep -q -- "$ABRIR" "$COPIA/instalacion.md" && subido descripcion | grep -q -- "cifrado: $ABRIR" \
  && bien "esa orden es la de docs/instalacion.md y la que queda en la descripción del archivo en Drive" || mal "la orden documentada no es esa"
[ "$(stat -c %a "$L")" = 600 ] && tar tzf "$L" > /dev/null \
  && bien "la copia de la estación sigue legible, con permisos 600" || mal "la copia local cambió"
echo "otra contraseña, también larga" > $M/otra
$ABRIR -pass file:$M/otra -in "$E" 2> /dev/null | tar xz -C $M 2> /dev/null && mal "se abre con otra contraseña" || bien "con otra contraseña no se abre"
if python3 -c "import cryptography" 2> /dev/null; then
  python3 - "$E" $C "$L" <<'FIN' && bien "se abre también sin openssl, con PBKDF2-HMAC-SHA256 y AES-256-CBC de la biblioteca cryptography" || mal "el formato no es el documentado"
import hashlib, sys
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
datos = open(sys.argv[1], "rb").read()
clave = open(sys.argv[2], "rb").readline().rstrip(b"\r\n")
assert datos[:8] == b"Salted__"
k = hashlib.pbkdf2_hmac("sha256", clave, datos[8:16], 600000, 48)      # 32 bytes de clave y 16 de vector
d = Cipher(algorithms.AES(k[:32]), modes.CBC(k[32:])).decryptor()
u = padding.PKCS7(128).unpadder()
assert u.update(d.update(datos[16:]) + d.finalize()) + u.finalize() == open(sys.argv[3], "rb").read()
FIN
else
  echo "   (este Python no tiene la biblioteca cryptography: no se comprueba que se abra sin openssl)"
fi
vaciar

echo "6 · con contraseña y el cifrado fallando: da error y no sube nada"
PATH=$M/sin-openssl:$PATH $R --drive prueba > $M/s6.txt 2>&1; s=$?
[ $s -ne 0 ] && [ -z "$(ls $D)" ] && bien "salida $s y en Drive no hay nada" || mal "salida $s o subió algo"
vaciar

echo "7 · una contraseña vacía o corta no vale: da error y no sube nada"
cp -p $C $M/clave-buena; printf '\nuna segunda línea, que openssl no lee\n' > $C
$R --drive prueba > $M/s7.txt 2>&1; s=$?
[ $s -ne 0 ] && [ -z "$(ls $D)" ] && grep -q "menos de 20 caracteres" $M/s7.txt && bien "salida $s, lo dice y en Drive no hay nada" || mal "salida $s o subió algo"
cp -p $M/clave-buena $C; vaciar

echo "8 · sin --drive: sale por la salida estándar, legible, tenga o no contraseña la estación"
$R > $M/s8.tar.gz 2> $M/s8.txt; s=$?
mv $C $M/clave-buena; sleep 1; $R > $M/s8b.tar.gz 2> $M/s8b.txt; s2=$?; mv $M/clave-buena $C
[ $s -eq 0 ] && [ $s2 -eq 0 ] && tar tzf $M/s8.tar.gz > /dev/null 2>&1 && tar tzf $M/s8b.tar.gz > /dev/null 2>&1 && [ -z "$(ls $D)" ] \
  && bien "las dos veces un tar legible, y a Drive no va nada" || mal "salidas $s y $s2, o no es un tar"
vaciar

echo "9 · un motivo largo y con acentos cabe en lo que Drive admite anotar"
$R --drive "ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú ñandú" > $M/s9.txt 2>&1; s=$?
[ $s -eq 0 ] && [ -n "$(ls $D/msx-*.tar.gz.enc 2> /dev/null)" ] && bien "sube, con el nombre $(basename $D/msx-*.enc)" || mal "salida $s: Drive lo habría rechazado"
vaciar

echo "10 · el guion que sube a Drive rechaza un archivo sin cifrar, aunque se le pida a mano"
L=$(ls -1t $M/respaldos/msx-*.tar.gz | head -1)
$M/recorder/venv/bin/python $M/respaldos/bin/respaldo_drive.py "$L" "a mano" > $M/s10.txt 2>&1; s=$?
[ $s -ne 0 ] && [ -z "$(ls $D)" ] && grep -q "no está cifrado" $M/s10.txt && bien "salida $s, lo dice y en Drive no hay nada" || mal "salida $s o lo subió"
vaciar

echo "11 · como lo lanza el temporizador semanal: la orden de msx-respaldo.service, sin entorno ni terminal"
ORDEN=$(sed -n 's/^ExecStart=//p' "$COPIA/systemd/msx-respaldo.service")
# systemd lanza la unidad sin HOME, sin USER y sin terminal: aquí, con el entorno vacío salvo el PATH
(cd / && env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin setsid $ORDEN) < /dev/null > $M/s11.txt 2>&1; s=$?
E=$(ls $D/msx-*-semanal.tar.gz.enc 2> /dev/null | head -1); L=$(ls -1t $M/respaldos/msx-*.tar.gz | head -1)
[ "$ORDEN" = "$R --drive semanal" ] && [ $s -eq 0 ] && [ -n "$E" ] && [ -z "$(ls $D/*.tar.gz 2> /dev/null)" ] \
  && $ABRIR -pass file:$C -in "$E" 2> /dev/null | cmp -s - "$L" \
  && bien "termina bien, sube solo $(basename "$E") y se abre" || mal "salida $s, no subió cifrado o no se abre"

echo
if [ $fallos -eq 0 ]; then echo "TODO BIEN"; exit 0; fi
echo "FALLOS: $fallos. Lo que dijo cada paso:"
for f in $M/s?.txt $M/s??.txt; do echo "--- $(basename "$f")"; cat "$f"; done
exit 1
