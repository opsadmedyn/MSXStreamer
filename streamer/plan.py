"""Media Syntaxis Streamer - plan de una ruta: qué se copia, qué se convierte y por qué.

Una ruta lleva una fuente a un destino. Antes de activarla, el operador ve este plan, pista a pista:
  - si el destino acepta el códec tal como llega, la pista se copia y solo cambia el transporte;
  - si solo el audio no vale, se convierte solo el audio y el vídeo se copia;
  - si hay que tocar el vídeo (otro perfil, un códec que el destino no admite, fotogramas B hacia
    WebRTC), se vuelve a codificar.

El resultado de convertir es una «señal derivada» de la fuente, identificada por una firma que solo
depende de lo que sale (medidas, bitrate, pistas y códecs), no del destino. Dos rutas de la misma
fuente con la misma firma comparten una sola conversión.

Aquí no se lanza nada: son funciones puras, y las pruebas (tests/test_plan.py) cubren los casos que
el hub debe saber representar.
"""

import hashlib

import protocolos as P

PERFILES_FIJOS = (
    {"id": "original", "nombre": "Original", "video": None, "audio": None},
    {"id": "1080p-6", "nombre": "1080p · 6 Mb/s", "video": {"alto": 1080, "kbps": 6000}, "audio": {"kbps": 160}},
    {"id": "720p-3", "nombre": "720p · 3 Mb/s", "video": {"alto": 720, "kbps": 3000}, "audio": {"kbps": 128}},
)
KBPS_AUDIO = {"opus": 128, "aac": 160}
# Bitrate de vídeo cuando se convierte sin perfil (misma resolución que la fuente): por alto de imagen.
KBPS_POR_ALTO = ((2160, 16000), (1440, 9000), (1080, 6000), (720, 3000), (540, 2000), (480, 1500), (0, 800))


def _decimal(x, cifras=2):
    """Número con coma decimal y sin ceros de más: 59.94 -> «59,94», 30.0 -> «30»."""
    return f"{x:.{cifras}f}".rstrip("0").rstrip(".").replace(".", ",")


def _canales(n):
    return {1: "mono", 2: "estéreo", 6: "5.1", 8: "7.1"}.get(n, f"{n} canales") if n else ""


def texto_video(v):
    """«H.264 · 1920×1080 · 59,94 fps», con lo que se sepa."""
    partes = [P.nombre_codec(v.get("codec"))]
    if v.get("ancho") and v.get("alto"):
        partes.append(f"{v['ancho']}×{v['alto']}")
    if v.get("fps"):
        partes.append(f"{_decimal(v['fps'])} fps")
    return " · ".join(partes)


def texto_audio(a):
    """«AAC · estéreo · 48 kHz», con lo que se sepa."""
    partes = [P.nombre_codec(a.get("codec"))]
    if a.get("canales"):
        partes.append(_canales(a["canales"]))
    if a.get("hz"):
        partes.append(f"{_decimal(a['hz'] / 1000, 1)} kHz")
    return " · ".join(partes)


def nombre_pista(i, a):
    """«Pista 2 · Inglés»: el número siempre, y el nombre o el idioma si la fuente lo trae."""
    extra = a.get("nombre") or a.get("idioma") or ""
    return f"Pista {i + 1}" + (f" · {extra}" if extra else "")


def _lista(numeros):
    """«2», «1 y 2», «1, 2 y 3»."""
    numeros = [str(n) for n in numeros]
    return numeros[0] if len(numeros) == 1 else ", ".join(numeros[:-1]) + " y " + numeros[-1]


def _pistas_de_audio(numeros):
    return f"la pista {numeros[0]} de audio" if len(numeros) == 1 else f"las pistas {_lista(numeros)} de audio"


def kbps_por_alto(alto):
    for minimo, kbps in KBPS_POR_ALTO:
        if (alto or 0) >= minimo:
            return kbps
    return KBPS_POR_ALTO[-1][1]


def medidas(v, alto_objetivo=None):
    """Ancho y alto de salida, pares, con la proporción de la fuente y sin ampliar nunca la imagen."""
    ancho, alto = v.get("ancho") or 0, v.get("alto") or 0
    if not ancho or not alto:                       # sin medidas conocidas: 16:9
        alto = alto_objetivo or 1080
        return round(alto * 16 / 9 / 2) * 2, alto // 2 * 2
    if not alto_objetivo or alto_objetivo >= alto:
        return ancho // 2 * 2, alto // 2 * 2
    return round(ancho * alto_objetivo / alto / 2) * 2, alto_objetivo // 2 * 2


def firma_de(video, audio):
    """Huella de una señal derivada: lo que sale, no a dónde va."""
    if video is None:
        v = "-"
    elif video == "copia":
        v = "copia"
    else:
        v = f"{video['codec']}:{video['ancho']}x{video['alto']}:{video['kbps']}"
    a = ",".join(f"{t['origen']}:{t['codec']}" + ("" if t["codec"] == "copia" else f":{t['kbps']}:{t['canales']}")
                 for t in audio)
    return hashlib.sha1(f"v={v};a={a}".encode()).hexdigest()[:10]


def _provisional(texto):
    return {"posible": True, "provisional": True, "video": None, "audio": [], "senal": None,
            "mapa_audio": [], "entera": True, "avisos": [texto], "resumen": texto, "carga": ""}


def _imposible(texto, video=None, audio=()):
    """La ruta no puede funcionar tal como llega la fuente: no se lanza nada y el panel dice por qué."""
    return {"posible": False, "provisional": False, "video": video, "audio": list(audio), "senal": None,
            "mapa_audio": [], "entera": True, "avisos": [texto], "resumen": texto, "carga": ""}


def _no_pasa(codec, que):
    return f"El hub no puede llevar {que} {P.nombre_codec(codec)} de la fuente a los destinos"


def planear(pistas, protocolo, perfil=None, audio="todas", empuja=True):
    """Plan de una ruta hacia un destino de `protocolo`.

    pistas: lo que trae la fuente ({"video": {...} | None, "audio": [{...}]}), o None si aún no se
        ha inspeccionado.
    perfil: uno de los perfiles (None: «Original»).
    audio: "todas" o el número de pista (desde 1) que quiere el destino.
    empuja: el hub se conecta al destino (su propio proceso elige las pistas al enviar). Si es el
        receptor quien viene a por la señal, elegir pistas ya exige una señal derivada.
    """
    perfil = perfil or PERFILES_FIJOS[0]
    nombre = P.NOMBRES.get(protocolo, protocolo)
    if protocolo in P.CON_ADAPTADOR:
        texto = f"La entrega la hace el adaptador {nombre}, que recibe la señal del hub tal como llega."
        return {"posible": True, "provisional": False, "adaptador": protocolo, "video": None, "audio": [],
                "senal": {"firma": "", "video": "copia", "audio": []}, "mapa_audio": [], "entera": True,
                "avisos": [], "resumen": texto, "carga": ""}
    if protocolo not in P.VIDEO_ACEPTA:
        raise ValueError(f"protocolo desconocido: {protocolo}")
    if pistas is None:
        return _provisional("La fuente todavía no se ha inspeccionado. El plan se calcula en cuanto llegue señal.")

    avisos = []
    v = pistas.get("video")
    audios = pistas.get("audio") or []
    if not v and not audios:
        return _imposible("La fuente no trae vídeo ni audio.")
    if v and v.get("codec") not in P.ENLACE_VIDEO:
        texto = _no_pasa(v.get("codec"), "vídeo") + ". Configura el origen para que envíe H.264."
        return _imposible(texto, {"accion": "imposible", "de": texto_video(v), "a": None, "motivo": texto})
    # número de cada pista de audio en el enlace interno, que solo lleva las que caben en él
    enlace = {}
    for i, a in enumerate(audios):
        if a.get("codec") in P.ENLACE_AUDIO:
            enlace[i] = len(enlace)
    if audios and not enlace and not v:
        return _imposible(_no_pasa(audios[0].get("codec"), "audio") + ". Configura el origen para que envíe AAC u Opus.")

    # ---- vídeo
    plan_v, senal_v = None, None
    if v:
        c = v.get("codec")
        de = texto_video(v)
        convertir = None                            # motivo, si hay que convertir
        if perfil.get("video"):
            convertir = f"El perfil «{perfil['nombre']}» fija la resolución y el bitrate."
        elif c not in P.VIDEO_ACEPTA[protocolo]:
            convertir = f"{nombre} no admite {P.nombre_codec(c)}: se convierte a H.264."
        elif protocolo in P.SIN_FOTOGRAMAS_B and c == "h264":
            if v.get("b") is None:
                return _provisional("Falta saber si el vídeo trae fotogramas B. Se comprueba al inspeccionar la señal.")
            if v["b"]:
                convertir = f"{nombre} no admite fotogramas B: el vídeo se vuelve a codificar sin ellos."
        if convertir:
            objetivo = (perfil.get("video") or {}).get("alto")
            ancho, alto = medidas(v, objetivo)
            kbps = (perfil.get("video") or {}).get("kbps") or kbps_por_alto(alto)
            senal_v = {"codec": "h264", "ancho": ancho, "alto": alto, "kbps": kbps}
            plan_v = {"accion": "convierte", "de": de, "motivo": convertir,
                      "a": f"H.264 · {ancho}×{alto} · {_decimal(kbps / 1000, 1)} Mb/s · sin fotogramas B"}
            if objetivo and objetivo > (v.get("alto") or objetivo):
                avisos.append(f"La fuente tiene menos resolución que el perfil: se mantiene en {ancho}×{alto}.")
        else:
            senal_v = "copia"
            plan_v = {"accion": "copia", "de": de, "a": None,
                      "motivo": f"{nombre} acepta {P.nombre_codec(c)} tal como llega."}
        if v.get("entrelazado"):
            avisos.append("La fuente es entrelazada: los navegadores la muestran con rayas si no se convierte.")
    else:
        avisos.append("La fuente no trae vídeo.")

    # ---- audio: qué pistas van y cómo
    varias = P.VARIAS_PISTAS[protocolo]
    primera = min(enlace) if enlace else None       # la primera pista que el hub puede llevar
    if audio == "todas" and varias:
        elegidas = list(enlace)
    else:
        i = primera if audio == "todas" else int(audio) - 1
        if i is not None and i not in enlace and enlace:
            falta = "La fuente no tiene pista" if not 0 <= i < len(audios) else "El hub no puede llevar la pista"
            avisos.append(f"{falta} {i + 1}: se envía la pista {primera + 1}.")
            i = primera
        elegidas = [i] if i in enlace else []
    plan_a, senal_a = [], []
    for i, a in enumerate(audios):
        fila = {"pista": i + 1, "nombre": nombre_pista(i, a), "de": texto_audio(a), "a": None}
        if i not in enlace:
            fila.update(accion="omite", motivo=_no_pasa(a.get("codec"), "audio") + ": esta pista no sale.")
            avisos.append(f"La pista {i + 1} ({P.nombre_codec(a.get('codec'))}) no llega al destino: "
                          "el hub no puede transportar ese audio. Pide al origen AAC u Opus.")
            plan_a.append(fila)
            continue
        if i not in elegidas:
            fila.update(accion="omite", motivo=(f"El destino usa solo la pista {elegidas[0] + 1}." if varias and elegidas
                                                else f"{nombre} lleva una sola pista de audio."))
            plan_a.append(fila)
            continue
        c, canales = a.get("codec"), a.get("canales") or 2
        tope = P.CANALES_MAX.get(protocolo)
        if c in P.AUDIO_ACEPTA[protocolo] and not (tope and canales > tope):
            fila.update(accion="copia", motivo=f"{nombre} acepta {P.nombre_codec(c)} tal como llega.")
            senal_a.append({"origen": enlace[i], "codec": "copia"})
        else:
            a_codec = P.AUDIO_PREFERIDO[protocolo]
            kbps = (perfil.get("audio") or {}).get("kbps") or KBPS_AUDIO[a_codec]
            salen = min(canales, tope) if tope else canales
            if c in P.AUDIO_ACEPTA[protocolo]:
                motivo = f"{nombre} lleva el audio en {_canales(salen)}: se mezcla y se convierte."
            else:
                motivo = f"{nombre} no admite {P.nombre_codec(c)}: se convierte a {P.nombre_codec(a_codec)}."
            fila.update(accion="convierte", motivo=motivo,
                        a=f"{P.nombre_codec(a_codec)} · {_canales(salen)} · {kbps} kb/s")
            senal_a.append({"origen": enlace[i], "codec": a_codec, "kbps": kbps, "canales": salen})
        plan_a.append(fila)
    if not audios:
        avisos.append("La fuente no trae audio.")

    # ---- la señal que lee la ruta
    van = [f["pista"] for f in plan_a if f["accion"] != "omite"]
    no_van = [f["pista"] for f in plan_a if f["accion"] == "omite"]
    convierte_v = isinstance(senal_v, dict)
    convierte_a = any(t["codec"] != "copia" for t in senal_a)
    entera = [t["origen"] for t in senal_a] == list(range(len(enlace)))    # van todas las que lleva el enlace
    if not convierte_v and not convierte_a and (entera or empuja):
        # nada que convertir: la fuente tal cual (y, si hay que elegir pistas, las elige quien envía)
        senal = {"firma": "", "video": senal_v, "audio": senal_a}
        mapa = [t["origen"] for t in senal_a]
    else:
        senal = {"firma": firma_de(senal_v, senal_a), "video": senal_v, "audio": senal_a}
        mapa = list(range(len(senal_a)))            # la derivada ya trae solo lo que hace falta

    if convierte_v and convierte_a:
        resumen = "Se convierten el vídeo y el audio."
    elif convierte_v:
        resumen = "Se convierte el vídeo" + (" y el audio se copia." if senal_a else ".")
    elif convierte_a:
        resumen = ("Se copia el vídeo y se convierte el audio." if v else "Se convierte el audio.")
    elif not no_van:
        resumen = "Se copia todo: solo cambia el transporte."
    else:                                           # «todo» sería mentira: se dice qué es lo que se copia
        partes = (["el vídeo"] if v else []) + ([_pistas_de_audio(van)] if van else [])
        resumen = f"Se copia{'n' if len(partes) > 1 or len(van) > 1 else ''} {' y '.join(partes)}: solo cambia el transporte."
    if no_van:
        resumen += f" {_pistas_de_audio(no_van).capitalize()} no se envía{'n' if len(no_van) > 1 else ''}."
    if convierte_v:
        carga = f"Un codificador de vídeo a {senal_v['ancho']}×{senal_v['alto']}."
    elif convierte_a:
        carga = "Solo audio: carga mínima."
    elif senal["firma"]:
        carga = "Solo se reparten las pistas: carga mínima."
    else:
        carga = "Sin conversión."
    return {"posible": True, "provisional": False, "video": plan_v, "audio": plan_a, "senal": senal,
            "mapa_audio": mapa, "entera": entera, "avisos": avisos, "resumen": resumen, "carga": carga}


def perfil_por_id(perfiles, pid):
    """El perfil pedido o, si ya no existe, «Original» (una ruta nunca se queda sin perfil)."""
    for p in perfiles:
        if p["id"] == pid:
            return p
    return PERFILES_FIJOS[0]
