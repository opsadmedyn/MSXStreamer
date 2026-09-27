#!/usr/bin/env python3
"""Sube un respaldo a Google Drive: "MSX Respaldos/<estación>/<archivo>" en Mi unidad de la cuenta
del sistema (el mismo token.json que usan el Recorder y el Transcoder). Las carpetas se crean la
primera vez y sus IDs se guardan en /home/mediasat/respaldos/drive.json.
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


def main():
    archivo, motivo = pathlib.Path(sys.argv[1]), sys.argv[2]
    d = drive()
    destino = carpeta_estacion(d, estacion())
    etiqueta = re.sub(r"[^a-z0-9.]+", "-", motivo.split(" · ")[0].lower()).strip("-")[:40]
    nombre = archivo.name.replace(".tar.gz", f"-{etiqueta}.tar.gz") if etiqueta else archivo.name
    meta = {"name": nombre, "parents": [destino], "description": motivo,
            "appProperties": {"msx_respaldo": "1", "motivo": motivo[:100]}}
    media = MediaFileUpload(str(archivo), mimetype="application/gzip", resumable=True)
    f = d.files().create(body=meta, media_body=media, fields="id,size,webViewLink").execute()
    print(f"subido a Drive: {RAIZ}/{estacion()}/{nombre} ({int(f['size']) // 1024} KB) {f['webViewLink']}")


if __name__ == "__main__":
    main()
