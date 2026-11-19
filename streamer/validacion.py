"""Media Syntaxis Streamer - qué combinaciones de entrada y salida están comprobadas, y con qué.

Que el hub sepa montar una ruta no quiere decir que esté probada. Cada combinación (cómo entra la
señal y cómo sale) tiene uno de tres niveles, y el panel lo enseña en cada ruta:

  pendiente     nadie la ha comprobado todavía;
  laboratorio   comprobada en el banco de pruebas, con programas haciendo de cámara y de receptor;
  real          comprobada en este equipo con los equipos de verdad (la cámara, el receptor, la
                plataforma): la anota un administrador, con qué equipo y cuándo.

Nada sale comprobado de fábrica: los niveles se anotan al pasar la prueba de aceptación
(scripts/aceptacion.py, que anota «laboratorio») o a mano desde Ajustes. Se guardan en la base, en
el ajuste «validacion».
"""

import time

import db
import protocolos as P

PENDIENTE, LABORATORIO, REAL = "pendiente", "laboratorio", "real"
NIVELES = {PENDIENTE: "Pendiente de validación", LABORATORIO: "Verificada en el banco de pruebas",
           REAL: "Verificada con equipos reales"}
# Entradas de los flujos de la versión anterior, que el hub enruta como una fuente más.
FLUJOS = {"hub": "Flujo de un Hub channel", "recorder": "Flujo del Recorder", "pull": "Flujo SRT (el Streamer llama)"}


def lado_fuente(f):
    """«rtmp:origen», «srt:hub»… o «flujo:hub» si es un flujo heredado."""
    h = f.get("heredada")
    return f"flujo:{h['entrada']}" if h else f"{f['protocolo']}:{f['inicia']}"


def lado_destino(d):
    return f"{d['protocolo']}:{d['inicia']}"


def clave(f, d):
    return f"{lado_fuente(f)}>{lado_destino(d)}"


def entradas():
    """[(lado, texto)] de todas las formas de entrar al hub."""
    r = [(f"{p}:{m}", f"{P.NOMBRES[p]} · {P.quien_inicia('fuente', p, m)}") for p in P.PROTOCOLOS for m in P.ENTRADAS[p]]
    return r + [(f"flujo:{e}", texto) for e, texto in FLUJOS.items()]


def salidas():
    return [(f"{p}:{m}", f"{P.NOMBRES[p]} · {P.quien_inicia('destino', p, m)}") for p in P.PROTOCOLOS for m in P.SALIDAS[p]]


def valida(lado_f, lado_d):
    return lado_f in dict(entradas()) and lado_d in dict(salidas())


def leer(c):
    guardado = db.ajuste(c, "validacion") or {}
    return guardado if isinstance(guardado, dict) else {}


def _vista(anotado):
    nivel = (anotado or {}).get("nivel")
    nivel = nivel if nivel in (LABORATORIO, REAL) else PENDIENTE
    return {"nivel": nivel, "nombre": NIVELES[nivel], "t": (anotado or {}).get("t"),
            "quien": (anotado or {}).get("quien") or "", "nota": (anotado or {}).get("nota") or ""}


def de(registro, f, d):
    """Nivel de la combinación de una fuente con un destino. `registro`: lo que devuelve leer()."""
    return _vista(registro.get(clave(f, d)))


def tabla(c):
    """Todas las combinaciones, con su nivel: para Ajustes y para el informe de la prueba."""
    registro, r = leer(c), []
    for lf, tf in entradas():
        for ld, td in salidas():
            adaptador = sorted({x.split(":")[0] for x in (lf, ld)} & set(P.CON_ADAPTADOR))
            r.append({"fuente": lf, "destino": ld, "fuente_texto": tf, "destino_texto": td,
                      "adaptador": [P.NOMBRES[a] for a in adaptador], **_vista(registro.get(f"{lf}>{ld}"))})
    return r


def anotar(c, lado_f, lado_d, nivel, quien="", nota=""):
    """Anota el nivel de una combinación. Lo comprobado con equipos reales no lo rebaja una prueba
    de banco posterior: para quitarlo hay que anotar «pendiente»."""
    if not valida(lado_f, lado_d) or nivel not in NIVELES:
        raise ValueError("combinación o nivel desconocidos")
    registro, k = leer(c), f"{lado_f}>{lado_d}"
    if nivel == PENDIENTE:
        registro.pop(k, None)
    elif nivel == LABORATORIO and (registro.get(k) or {}).get("nivel") == REAL:
        return False
    else:
        registro[k] = {"nivel": nivel, "t": time.time(), "quien": quien, "nota": nota[:500]}
    db.guardar_ajuste(c, "validacion", registro)
    return True
