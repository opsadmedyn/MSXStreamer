#!/usr/bin/env python3
"""Recorder de mentira para el banco de pruebas. NO ES PARA PRODUCCIÓN.

El panel del Streamer no tiene usuarios propios: pregunta al Recorder del equipo quién es quien trae
la cookie de sesión. En el banco no hay Recorder, así que este programa contesta en su lugar: la
cookie «msr_sesion» vale «admin», «manager» u «operador» y ese es el rol. Cualquiera que llegue al
panel del banco puede por tanto entrar como administrador: por eso el banco escucha solo en
127.0.0.1 y este archivo no se copia a ninguna estación (scripts/desplegar.sh no lo lleva).

    recorder_falso.py [puerto]      (por defecto 18081)
"""

import http.server
import json
import sys

ROLES = ("admin", "manager", "operador")


class Recorder(http.server.BaseHTTPRequestHandler):
    def _responder(self, codigo, cuerpo):
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        if self.path != "/api/auth/yo":
            return self._responder(404, {"detail": "No existe"})
        cookies = dict(p.strip().split("=", 1) for p in self.headers.get("Cookie", "").split(";") if "=" in p)
        rol = cookies.get("msr_sesion", "")
        if rol not in ROLES:
            return self._responder(401, {"detail": "Sin sesión"})
        # el operador solo tiene el Streamer; los demás, la suite entera (para ver sus botones)
        modulos = ["streamer"] if rol == "operador" else ["recorder", "streamer", "renderer", "showeditor"]
        self._responder(200, {"email": f"{rol}@banco.invalid", "rol": rol, "modulos": modulos})

    def do_POST(self):
        self._responder(200, {"ok": True})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 18081
    http.server.ThreadingHTTPServer(("127.0.0.1", puerto), Recorder).serve_forever()
