"""Media Syntaxis Streamer - carga del equipo y de cada proceso del hub.

De /proc sale cuánta CPU y memoria gasta cada tarea (el lanzador y su ffmpeg comparten grupo de
procesos, así que se suman), el uso total del equipo y la memoria libre; de nvidia-smi, si la hay,
el uso de la tarjeta y de su codificador. Sirve al panel (Monitoreo) y a la prueba de aceptación,
que documenta la carga observada con cada ruta en marcha.

El porcentaje de CPU es de un núcleo: 100 es un núcleo entero y un ffmpeg con varios hilos puede
pasar de ahí. Se calcula entre dos lecturas, así que la primera no lo trae.
"""

import os
import pathlib
import platform
import shutil
import socket
import subprocess
import time

TICS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGINA = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
PROC = pathlib.Path("/proc")
GPU_CADA = 5.0                  # segundos que vale una lectura de nvidia-smi
CONSULTAS_GPU = (
    "name,utilization.gpu,utilization.encoder,utilization.decoder,memory.used,memory.total,encoder.stats.sessionCount",
    "name,utilization.gpu,memory.used,memory.total",       # controladores que no dan el codificador aparte
)


def _de_proceso(pid):
    """(grupo, segundos de CPU, bytes de memoria residente) de un proceso, o None si ya no existe."""
    try:
        campos = (PROC / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        if campos[0] == "Z":
            return None
        # tras el nombre: estado(0) ppid(1) pgrp(2) … utime(11) stime(12) … rss(21)
        return int(campos[2]), (int(campos[11]) + int(campos[12])) / TICS, int(campos[21]) * PAGINA
    except (OSError, IndexError, ValueError):
        return None


def por_grupo():
    """{grupo de procesos: (segundos de CPU, bytes, número de procesos)} de todo lo que corre."""
    grupos = {}
    for d in PROC.iterdir():
        if not d.name.isdigit():
            continue
        p = _de_proceso(d.name)
        if p:
            cpu, mem, n = grupos.get(p[0], (0.0, 0, 0))
            grupos[p[0]] = (cpu + p[1], mem + p[2], n + 1)
    return grupos


def _cpu_total():
    """(tics ocupados, tics totales) del equipo desde que arrancó."""
    try:
        campos = [int(x) for x in (PROC / "stat").read_text().splitlines()[0].split()[1:]]
    except (OSError, ValueError, IndexError):
        return None
    ocioso = campos[3] + (campos[4] if len(campos) > 4 else 0)        # idle + iowait
    return sum(campos[:8]) - ocioso, sum(campos[:8])


def _memoria():
    datos = {}
    try:
        for linea in (PROC / "meminfo").read_text().splitlines():
            clave, _, resto = linea.partition(":")
            datos[clave] = int(resto.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return {"total_mb": datos.get("MemTotal"), "disponible_mb": datos.get("MemAvailable")}


def pid_de(programa, carpetas):
    """El proceso con ese nombre que se lanzó desde una de esas carpetas (la primera que acierte), o
    None. Sirve para medir el MediaMTX de este hub y no el de otra instalación del mismo equipo."""
    candidatos = []
    for d in PROC.iterdir():
        if not d.name.isdigit():
            continue
        try:
            if (d / "comm").read_text().strip() != programa:
                continue
            candidatos.append((int(d.name), (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")))
        except OSError:
            continue
    for carpeta in carpetas:
        for pid, orden in candidatos:
            if f"{str(carpeta).rstrip('/')}/" in orden:
                return pid
    return None


_equipo = None


def equipo():
    """Qué equipo es (nombre, procesador, sistema): la carga que se documenta dice de qué máquina es."""
    global _equipo
    if _equipo is None:
        cpu = ""
        try:
            for linea in (PROC / "cpuinfo").read_text().splitlines():
                if linea.lower().startswith("model name"):
                    cpu = " ".join(linea.partition(":")[2].split())
                    break
        except OSError:
            pass
        _equipo = {"nombre": socket.gethostname(), "cpu": cpu or platform.processor() or platform.machine(),
                   "sistema": f"{platform.system()} {platform.release()}"}
    return _equipo


def leer_gpu(salida):
    """De la salida de nvidia-smi (csv sin cabecera ni unidades) a una lista de tarjetas."""
    tarjetas = []
    for linea in salida.strip().splitlines():
        v = [x.strip() for x in linea.split(",")]

        def num(i):
            try:
                return float(v[i])
            except (ValueError, IndexError):
                return None
        if len(v) >= 7:
            tarjetas.append({"nombre": v[0], "uso_pct": num(1), "codificador_pct": num(2), "decodificador_pct": num(3),
                             "memoria_mb": num(4), "memoria_total_mb": num(5), "sesiones_codificador": num(6)})
        elif len(v) >= 4:
            tarjetas.append({"nombre": v[0], "uso_pct": num(1), "codificador_pct": None, "decodificador_pct": None,
                             "memoria_mb": num(2), "memoria_total_mb": num(3), "sesiones_codificador": None})
    return tarjetas


class Medidor:
    """Guarda la lectura anterior para dar porcentajes. Uno por proceso que mide (panel o prueba)."""

    def __init__(self):
        self.antes = None               # (instante, {grupo: segundos de CPU}, (tics ocupados, tics totales))
        self.gpu = (0.0, None)
        self.consulta_gpu = 0

    def tarjetas(self):
        ahora = time.monotonic()
        if ahora - self.gpu[0] < GPU_CADA:
            return self.gpu[1]
        smi, tarjetas = shutil.which("nvidia-smi"), None
        while smi and self.consulta_gpu < len(CONSULTAS_GPU):
            try:
                r = subprocess.run([smi, f"--query-gpu={CONSULTAS_GPU[self.consulta_gpu]}", "--format=csv,noheader,nounits"],
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=4)
            except (OSError, subprocess.TimeoutExpired):
                break
            if r.returncode == 0:
                tarjetas = leer_gpu(r.stdout.decode(errors="replace"))
                break
            self.consulta_gpu += 1      # esa consulta no la entiende este controlador: la siguiente
        self.gpu = (ahora, tarjetas)
        return tarjetas

    def medir(self, tareas=None):
        """Carga del equipo y de cada tarea. tareas: {nombre: pid del lanzador (que encabeza su grupo)}."""
        ahora, grupos, total = time.monotonic(), por_grupo(), _cpu_total()
        antes, self.antes = self.antes, (ahora, {g: v[0] for g, v in grupos.items()}, total)
        dt = ahora - antes[0] if antes else 0
        por_tarea = {}
        for nombre, pid in (tareas or {}).items():
            g = grupos.get(pid)
            if not g:
                continue
            cpu = None
            if antes and dt >= 0.5 and pid in antes[1]:
                cpu = round(max(0.0, g[0] - antes[1][pid]) / dt * 100, 1)
            por_tarea[nombre] = {"cpu_pct": cpu, "memoria_mb": round(g[1] / 1048576, 1), "procesos": g[2]}
        uso = None
        if antes and antes[2] and total and total[1] > antes[2][1]:
            uso = round((total[0] - antes[2][0]) / (total[1] - antes[2][1]) * 100, 1)
        try:
            carga = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            carga = None
        return {"t": time.time(), "equipo": equipo(), "nucleos": os.cpu_count(), "cpu_pct": uso, "carga": carga,
                "memoria": _memoria(), "gpu": self.tarjetas(), "tareas": por_tarea}
