#!/usr/bin/env python3
"""Sube un respaldo a Google Drive: "MSX Respaldos/<estación>/<archivo>" en Mi unidad de la cuenta
del sistema (el mismo token.json que usan el Recorder y el Transcoder). Las carpetas se crean la
primera vez y sus IDs se guardan en /home/mediasat/respaldos/drive.json. No se acumulan: el
semanal es un único archivo que se reemplaza (con revisiones) y de los de cada versión se guardan
los 3 últimos por programa; el resto va a la papelera de Drive (ver sobrantes()).
    respaldo_drive.py <archivo.tar.gz> <motivo>
Se ejecuta con el Python del Recorder, que ya tiene las bibliotecas de Google."""

import json
import pathlib
import re
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TOKEN = pathlib.Path("/home/mediasat/transcoder/credentials/token.json")
ESTADO = pathlib.Path("/home/mediasat/respaldos/drive.json")
RAIZ = "MSX Respaldos"
CARPETA = "application/vnd.google-apps.folder"


def drive():
    creds = Credentials.from_authorized_user_file(str(TOKEN))
    if not creds.valid:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def carpeta(d, nombre, padre):
    """Busca la carpeta por nombre dentro de `padre` (o en la raíz) y la crea si no existe."""
    q = [f"name = '{nombre}'", f"mimeType = '{CARPETA}'", "trashed = false",
         f"'{padre}' in parents" if padre else "'root' in parents"]
    r = d.files().list(q=" and ".join(q), fields="files(id)", pageSize=1).execute().get("files", [])
    if r:
        return r[0]["id"]
    meta = {"name": nombre, "mimeType": CARPETA, **({"parents": [padre]} if padre else {})}
    return d.files().create(body=meta, fields="id").execute()["id"]


def carpeta_estacion(d, estacion):
    try:
        ids = json.loads(ESTADO.read_text())
    except (OSError, ValueError):
        ids = {}
    if ids.get("estacion") != estacion or not ids.get("carpeta"):
        raiz = carpeta(d, RAIZ, None)
        ids = {"raiz": raiz, "estacion": estacion, "carpeta": carpeta(d, estacion, raiz)}
        ESTADO.write_text(json.dumps(ids, indent=2))
    return ids["carpeta"]


def estacion():
    try:
        return json.loads(pathlib.Path("/home/mediasat/recorder/config.json").read_text())["estacion"]
    except (OSError, ValueError, KeyError):
        import socket
        return socket.gethostname()


GUARDAR = 3   # respaldos de despliegue que se conservan por programa (recorder, streamer, otros)
FECHADO = re.compile(r"^msx-\d{8}-\d{6}-(.+)\.tar\.gz$")


def fijo(est):
    return f"msx-{est}-semanal.tar.gz"


def respaldos(d, destino):
    """Los respaldos de la carpeta de la estación (solo los marcados msx_respaldo)."""
    q = (f"'{destino}' in parents and trashed = false"
         " and appProperties has { key='msx_respaldo' and value='1' }")
    todos, tok = [], None
    while True:
        r = d.files().list(q=q, fields="nextPageToken,files(id,name,createdTime)",
                           pageSize=1000, pageToken=tok).execute()
        todos += r.get("files", [])
        tok = r.get("nextPageToken")
        if not tok:
            return todos


def sobrantes(archivos, est):
    """Los que van a la papelera: los semanales antiguos con fecha en el nombre (el semanal es ahora
    un único archivo fijo con revisiones) y, de los de despliegue, todo salvo los GUARDAR más
    recientes de cada programa. El fijo y los nombres que no siguen el patrón no se tocan."""
    grupos, fuera = {}, []
    for f in archivos:
        m = FECHADO.match(f["name"])
        if f["name"] == fijo(est) or not m:
            continue
        etiqueta = m.group(1)
        if etiqueta == "semanal":
            fuera.append(f)
            continue
        prog = next((p for p in ("recorder", "streamer") if etiqueta.startswith(p)), "otros")
        grupos.setdefault(prog, []).append(f)
    for lista in grupos.values():
        lista.sort(key=lambda f: f["createdTime"], reverse=True)
        fuera += lista[GUARDAR:]
    return fuera


def main():
    archivo, motivo = pathlib.Path(sys.argv[1]), sys.argv[2]
    d = drive()
    est = estacion()
    destino = carpeta_estacion(d, est)
    etiqueta = re.sub(r"[^a-z0-9.]+", "-", motivo.split(" · ")[0].lower()).strip("-")[:40]
    media = MediaFileUpload(str(archivo), mimetype="application/gzip", resumable=True)
    props = {"msx_respaldo": "1", "motivo": motivo[:100]}
    if etiqueta == "semanal":
        # un solo archivo que se reemplaza cada semana; Drive guarda unos 30 días las revisiones previas
        nombre = fijo(est)
        previo = [f for f in respaldos(d, destino) if f["name"] == nombre]
        meta = {"description": motivo, "appProperties": props}
        if previo:
            f = d.files().update(fileId=previo[0]["id"], body=meta, media_body=media,
                                 fields="id,size,webViewLink").execute()
        else:
            f = d.files().create(body={"name": nombre, "parents": [destino], **meta}, media_body=media,
                                 fields="id,size,webViewLink").execute()
    else:
        nombre = archivo.name.replace(".tar.gz", f"-{etiqueta}.tar.gz") if etiqueta else archivo.name
        meta = {"name": nombre, "parents": [destino], "description": motivo, "appProperties": props}
        f = d.files().create(body=meta, media_body=media, fields="id,size,webViewLink").execute()
    print(f"subido a Drive: {RAIZ}/{est}/{nombre} ({int(f['size']) // 1024} KB) {f['webViewLink']}")
    # solo tras subir bien; a la papelera (30 días), nunca borrado definitivo
    fuera = sobrantes(respaldos(d, destino), est)
    for x in fuera:
        d.files().update(fileId=x["id"], body={"trashed": True}).execute()
    if fuera:
        print(f"a la papelera de Drive {len(fuera)} respaldos: " + ", ".join(x["name"] for x in fuera))


if __name__ == "__main__":
    main()
