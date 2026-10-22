# -*- coding: utf-8 -*-
"""
Calcula el foco (paneo horizontal) del video vertical de una carrera.
Corre en el Python de .python-vision (3.12 + YOLO); camtasia_carrera.py lo llama con --foco-auto.

En el proyecto vertical el video va a escala 1.7778 (alto completo), así que se ve una ventana
de ~607 px del ancho original: el foco es solo la x del centro de esa ventana en cada momento.

Reglas:
  - previa (antes de la partida): centrar lo que se vea
  - partida: seguir al más adelantado; si uno queda muy atrás, ir a verlo un momento y volver
  - carrera: la cabeza del puntero cerca del borde delantero, para ver a los que vienen detrás;
    si viene solo, centrado
  - recta final (desde el penúltimo parcial): encuadrar la lucha si la hay, si no, centrar al puntero
  - cámara de frente o de atrás: centrar el pelotón
  - pantalla dividida: se decide solo con la imagen de arriba
  - después de la meta: centrar al caballo más grande en pantalla (el ganador en primer plano)

Perfil flash, recta final y meta (se detectan en el video, ver analizar_recta()):
  - la recta empieza donde empieza la toma larga que termina en la meta (la baranda se va acostando
    sin saltos hasta quedar horizontal); si no se encuentra, en el penúltimo parcial
  - en la recta, siempre el puntero, con la cabeza cerca del borde delantero para que entre la mayor
    parte posible del pelotón detrás; si el segundo viene muy lejos, el puntero se va centrando
  - en la meta (poste a franjas rojas y blancas, en el medio del arco de llegada): apenas el puntero
    entra en la ventana centrada en el poste, el foco queda fijo ahí hasta que la cámara se va

El sentido de carrera sale de los jinetes: van agachados hacia la cabeza del caballo, así que el
jinete queda corrido hacia adelante dentro de la caja del caballo.
Los cambios de cámara de la transmisión son casi siempre fundidos: un "corte" (salto sin animación)
se hace solo donde la imagen cambia Y el encuadre ideal cambia mucho.

Uso:
  foco_carrera.py VIDEO --salida foco.json [--parciales 26.9,48.7,73.0,92.4] [--partida 5.0] [--preview foco.mp4]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
MODELO = "yolo11s.pt"
IMGSZ = 1280
CONF = 0.25
PERSONA, CABALLO = 0, 17          # clases de COCO
VERSION = 3                       # cambia si cambia lo que se guarda en el análisis

# El perfil "flash" mira menos cuadros y con menos resolución: sigue a los caballos casi igual
# pero tarda una fracción. Además deja pocos movimientos de cámara (ver reducir()).
PERFILES = {
    "normal": {"modelo": MODELO, "imgsz": IMGSZ, "paso_deteccion": 6, "escala": 1.0, "lote": 4},
    "flash": {"modelo": MODELO, "imgsz": 960, "paso_deteccion": 12, "escala": 0.5, "lote": 8},
}

W, H = 1920, 1080                 # el análisis se hace a alto 1080 y ancho proporcional al video (1920 o 1440)
ESCALA = 1920 / 1080              # escala del video en el proyecto vertical
ESCALA_LEJOS = 2.858166           # escala en tomas panorámicas lejanas de la curva
ZOOM_LEJOS = ESCALA_LEJOS / ESCALA
VENTANA = 1080 / ESCALA           # ancho visible del original (~607 px)
MITAD = VENTANA / 2
PASO_ANALISIS = 3                 # cuadros: histograma y pantalla dividida (~10/s)
PASO_DETECCION = PERFILES["normal"]["paso_deteccion"]   # cuadros: caballos y jinetes (~5/s)

# Cada hipódromo transmite distinto: cambia dónde está la franja que separa la pantalla dividida
# (y de qué color es) y dónde pone sus gráficas. Todo lo demás del análisis es igual.
#   banda: filas donde va la franja; se mide qué porción de esas filas es negra.
#   corte: en pantalla dividida, la fila donde termina la imagen de arriba.
#   graficas: rectángulos (x1, y1, x2, y2) con gráficas: ahí no hay caballos.
PISTAS = {
    "rinconada": {"banda": (578, 622), "corte": 565,
                  "graficas": [(0, 0, 790, 165), (1470, 0, 1920, 165), (0, 840, 620, 1080)]},
    "valencia": {"banda": (626, 660), "corte": 620,
                 "graficas": [(0, 0, 1920, 205)]},
}
PISTA = "rinconada"

MARGEN = 0.10 * VENTANA           # aire delante de la cabeza del puntero
SOLO = 0.75 * VENTANA             # ventaja a partir de la cual el puntero "viene solo"
LUCHA = 0.45 * VENTANA            # distancia entre cabezas que cuenta como lucha
SALTO = 0.35 * VENTANA            # cambio de encuadre que justifica un corte en un cambio de cámara
FRONTAL = 0.9                     # alto/ancho de los caballos a partir del cual la cámara es de frente o de atrás
PARTIDA = 8.0                     # segundos de partida, desde que sale la gráfica de tiempos
VISITA = 1.3                      # segundos mirando al que partió mal
VEL_MAX = 650.0                   # px/s del original: velocidad máxima del paneo
TOLERANCIA = 25.0                 # px: simplificación de keyframes

# Perfil flash: menos movimientos de cámara (keyframes más separados)
TOL_FLASH = 70.0                  # px: lo que se puede quitar sin que se note
TOL_MAX_FLASH = 130.0             # px: error máximo tolerado al forzar menos movimientos
SEG_MIN_FLASH = 6.0               # s: separación mínima entre keyframes
MEDIANA_FLASH = 15                # muestras: suavizado más ancho (~6 s)
TOL_META = 12.0                   # px: en la meta el foco sigue al poste casi exacto (queda fijo en el arco)

# Recta final y meta (perfil flash): una pasada liviana extra sobre el final del video
VERSION_RECTA = 3                 # cambia si cambia lo que guarda analizar_recta()
RECTA_FPS = 2.5                   # muestras por segundo de la pasada
RECTA_ESCALA = 0.5                # la pasada se hace a media resolución
RECTA_ANTES = 25.0                # s antes del penúltimo parcial desde donde se busca la recta
RECTA_MAX = 35.0                  # s: la toma de la recta no se estira más atrás que esto desde la meta
RECTA_MIN = 5.5                   # s: si la toma detectada es más corta, la recta empieza en el penúltimo parcial
RIEL_MAX = 22.0                   # °: inclinación máxima de la baranda en la toma de la recta
RIEL_SALTO = 12.0                 # °: cambio brusco de inclinación entre dos muestras que indica otra toma
POSTE_SALTO = 0.9 * VENTANA       # px: lo más que se mueve el poste entre dos muestras (la cámara panea rápido)
POSTE_QUIETO = 0.2 * VENTANA      # px: cuánto se aparta el poste de donde quedó la cámara sin dejar la meta
POSTE_MIN = 4                     # muestras con el poste quieto para aceptar que es la meta
META_HUECO = 1.2                  # s: el poste puede perderse este tiempo (caballos delante) sin dejar la meta
DIV_MIN = 3.0                     # s: pantalla dividida más corta que esto se ignora
DIV_HUECO = 2.0                   # s: dos tramos separados por menos que esto se unen
DIV_MARGEN = 0.3                  # s: el cintillo entra un poco antes y sale un poco después


# ----------------------------------------------------------------------------
# Lectura del video: un solo recorrido
# ----------------------------------------------------------------------------

def dimensiones_video(video):
    """(ancho a alto 1080 respetando el aspecto del video, fps, total de cuadros)."""
    c = cv2.VideoCapture(video)
    w = c.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920
    h = c.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080
    fps = c.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
    c.release()
    ancho = int(round(w * H / float(h) / 2.0)) * 2
    return ancho, fps, total


def identidad(video):
    """Datos del archivo: lo que hace que un análisis guardado siga sirviendo."""
    st = os.stat(video)
    return {"nombre": os.path.basename(video), "bytes": st.st_size, "mtime": int(st.st_mtime)}


def huella(video):
    """Huella del formato viejo del cache (análisis hecho con el perfil normal)."""
    i = identidad(video)
    return hashlib.md5(("%s|%d|%d|%s|%d|%d" % (i["nombre"], i["bytes"], i["mtime"],
                                               MODELO, IMGSZ, VERSION)).encode()).hexdigest()


def leer_cuadros(video, escala):
    """Cuadros BGR para el análisis, del tamaño que pida el perfil.

    Con escala 1 los lee OpenCV, cuadro por cuadro, del video original. El perfil flash usa
    media escala y se los pide a ffmpeg, que decodifica en varios hilos: es bastante más rápido
    y el modelo ve los caballos igual de grandes (YOLO reescala la entrada a imgsz de todos modos)."""
    if escala >= 1.0:
        cap = cv2.VideoCapture(video)
        try:
            while True:
                ok, f = cap.read()
                if not ok:
                    break
                if f.shape[1] != W or f.shape[0] != H:
                    f = cv2.resize(f, (W, H))
                yield f
        finally:
            cap.release()
        return
    wa, ha = int(round(W * escala)), int(round(H * escala))
    proceso = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", video, "-vf", "scale=%d:%d" % (wa, ha),
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    tam = wa * ha * 3
    try:
        while True:
            b = proceso.stdout.read(tam)
            while 0 < len(b) < tam:                     # el pipe puede cortar el cuadro
                resto = proceso.stdout.read(tam - len(b))
                if not resto:
                    break
                b += resto
            if len(b) < tam:
                break
            yield np.frombuffer(b, np.uint8).reshape(ha, wa, 3)
    finally:
        proceso.stdout.close()
        proceso.kill()
        proceso.wait()


def analizar(video, perfil=None, fps=None, total=0, pista=PISTA):
    from ultralytics import YOLO
    perfil = perfil or PERFILES["normal"]
    banda_filas = PISTAS[pista]["banda"]
    esc = float(perfil.get("escala", 1.0))
    paso = max(int(round(4 * esc)), 1)                  # muestreo de histograma y de la franja
    modelo = YOLO(os.path.join(AQUI, "modelos", perfil["modelo"]))
    if fps is None or not total:
        _, fps_v, total = dimensiones_video(video)
        fps = fps or fps_v
    previo = previo_hist = None
    mad, muestras, detecciones = [], [], []
    lote, pendientes, avisado = [], [], [0]

    def detectar():
        """Pasa el lote de cuadros junto: en CPU es bastante más rápido que cuadro por cuadro."""
        if not lote:
            return
        resultados = modelo.predict(lote, imgsz=perfil["imgsz"], conf=CONF,
                                    classes=[PERSONA, CABALLO], verbose=False)
        for (_, tt), r in zip(pendientes, resultados):
            cajas = [[round(x1 / esc), round(y1 / esc), round(x2 / esc), round(y2 / esc), round(c, 2), int(k)]
                     for (x1, y1, x2, y2), c, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(),
                                                       r.boxes.cls.tolist())]
            detecciones.append({"t": round(tt, 3), "cajas": cajas})
        del lote[:]
        del pendientes[:]

    i = 0
    for f in leer_cuadros(video, esc):
        gris = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        mini = cv2.resize(gris, (64, 36), interpolation=cv2.INTER_AREA).astype(np.int16)
        mad.append(0.0 if previo is None else round(float(np.abs(mini - previo).mean()), 1))
        previo = mini
        t = i / fps
        if i % PASO_ANALISIS == 0:
            hist = cv2.calcHist([gris[::paso, ::paso]], [0], None, [32], [0, 256])
            cv2.normalize(hist, hist)
            corr = 1.0 if previo_hist is None else cv2.compareHist(previo_hist, hist, cv2.HISTCMP_CORREL)
            previo_hist = hist
            banda = float((gris[int(banda_filas[0] * esc):int(banda_filas[1] * esc):max(int(2 * esc), 1),
                                ::paso] < 45).mean())
            muestras.append({"t": round(t, 3), "hist": round(corr, 3), "banda": round(banda, 3)})
        if i % perfil["paso_deteccion"] == 0:
            lote.append(f if f.flags.writeable else f.copy())
            pendientes.append((i, t))
            if len(lote) >= perfil.get("lote", 4):
                detectar()
                if len(detecciones) - avisado[0] >= 100:
                    avisado[0] = len(detecciones)
                    print("  analizando video... %s" % ("%d%%" % (100 * i // total) if total > 0
                                                        else "%.0f s" % t), flush=True)
        i += 1
    detectar()
    return {"fps": fps, "duracion": i / fps, "ancho": W, "mad": mad, "muestras": muestras, "detecciones": detecciones}


def sirve(d, video, perfil, pista=PISTA):
    """¿El análisis guardado alcanza para el perfil que se pide? (mismo modelo, igual o mejor calidad)"""
    if d.get("pista", PISTA) != pista or d.get("ancho", 1920) != W or d.get("version", 2) != VERSION:
        return False
    if d.get("perfil"):
        if d.get("identidad") != identidad(video):
            return False
        guardado = d["perfil"]
    elif d.get("huella") == huella(video):      # cache del formato viejo (siempre perfil normal)
        guardado = PERFILES["normal"]
    else:
        return False
    return (guardado.get("modelo") == perfil["modelo"]
            and guardado.get("imgsz", 0) >= perfil["imgsz"]
            and guardado.get("paso_deteccion", 999) <= perfil["paso_deteccion"])


def cargar_o_analizar(video, perfil=None, pista=PISTA):
    global W
    perfil = perfil or PERFILES["normal"]
    W, fps, total = dimensiones_video(video)
    cache = os.path.splitext(video)[0] + ".foco-cache.json"
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            d = json.load(fh)
        if sirve(d, video, perfil, pista):
            print("  usando el análisis guardado (%s)" % os.path.basename(cache))
            return d
    print("  analizando el video (la primera vez tarda unos minutos)...")
    d = analizar(video, perfil, fps, total, pista)
    d["identidad"] = identidad(video)
    d["perfil"] = dict(perfil)
    d["pista"] = pista
    d["version"] = VERSION
    d["huella"] = huella(video)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return d


# ----------------------------------------------------------------------------
# Recta final y meta: pasada liviana sobre el final del video (perfil flash)
# ----------------------------------------------------------------------------
#
# La recta se transmite con una sola toma larga desde la tribuna: la cámara sigue a los caballos y
# la baranda blanca, que en la curva se ve inclinada (~16°), se va acostando hasta quedar horizontal
# en la meta. Esa toma termina en la meta: la cámara se queda quieta con el arco de llegada en
# cuadro y, en el medio del arco, el poste de llegada a franjas rojas y blancas (La Rinconada) o el
# arco verde-azulado en herradura con letras blancas (Valencia).

def segmentos(v):
    """(valor, inicio, largo) de cada tramo de valores iguales de un vector."""
    cambios = np.flatnonzero(np.diff(v)) + 1
    ini = np.r_[0, cambios]
    return zip(v[ini], ini, np.diff(np.r_[ini, len(v)]))


def detectar_arco_valencia(f):
    """x (en coordenadas de ancho W) del arco de llegada de Valencia (herradura verde-azulada con
    letras blancas y cartel central LLEGADA), o [] si no está en cuadro."""
    alto, ancho = f.shape[:2]
    hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
    hsv[:int(0.14 * alto), :int(0.24 * ancho)] = 0      # logo arriba a la izquierda
    hsv[:int(0.20 * alto), int(0.58 * ancho):] = 0      # gráfica arriba a la derecha
    zona = hsv[int(0.06 * alto):int(0.48 * alto)]
    h, s, v = zona[..., 0], zona[..., 1], zona[..., 2]
    teal = ((h >= 75) & (h <= 98) & (s >= 85) & (v >= 50) & (v <= 190)).astype(np.uint8)
    blanco = ((s <= 60) & (v >= 185)).astype(np.uint8)
    vec = cv2.dilate(teal, np.ones((5, 5), np.uint8))
    letras = blanco & vec
    col_teal = teal.sum(axis=0)
    col_letras = letras.sum(axis=0)
    umbral_t = max(int(0.06 * alto), 15)
    cols = np.flatnonzero((col_teal >= umbral_t) & (col_letras >= 2))
    if len(cols) < int(0.06 * ancho):
        return []
    x_min, x_max = int(np.percentile(cols, 5)), int(np.percentile(cols, 95))
    span = x_max - x_min
    if not (0.08 * ancho <= span <= 0.30 * ancho):
        return []
    en_arco = (cols >= x_min) & (cols <= x_max)
    if en_arco.sum() < int(0.05 * ancho):
        return []
    cx = 0.5 * (x_min + x_max) * W / ancho
    return [round(float(cx), 1)]


def postes_meta(f, pista=PISTA):
    """x (en coordenadas de ancho W) de los postes a franjas rojas y blancas del cuadro: columnas
    angostas donde se alternan rojo y blanco al menos 4 veces, con franjas parejas (las de las puntas
    pueden ser largas: el poste suele terminar en un tramo blanco)."""
    if pista == "valencia":
        return detectar_arco_valencia(f)
    alto, ancho = f.shape[:2]
    hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # con poca luz (carreras al atardecer) el blanco se ve gris: el umbral sigue el brillo del cuadro
    claro = max(150.0, min(180.0, 0.85 * float(np.percentile(v[::4, ::4], 95))))
    lab = np.zeros((alto, ancho), np.int8)
    lab[((h <= 8) | (h >= 168)) & (s >= 100) & (v >= 60)] = 1          # rojo
    lab[(s <= 55) & (v >= claro)] = 2                                   # blanco
    for gx1, gy1, gx2, gy2 in PISTAS[pista]["graficas"]:
        lab[int(gy1 * alto / H):int(gy2 * alto / H), int(gx1 * ancho / W):int(gx2 * ancho / W)] = 0
    lab = lab[int(0.12 * alto):int(0.80 * alto)]
    min_f, max_f = max(int(6 * alto / H), 2), int(60 * alto / H)        # largo de una franja
    candidatas = np.flatnonzero(((lab == 1).sum(0) >= min_f * 2) & ((lab == 2).sum(0) >= min_f * 2))
    cols = []
    for x in candidatas:
        mejor = cuenta = 0
        fin = previo = None
        for val, i, largo in segmentos(lab[:, x]):
            if largo < 2:                                               # ruido: no corta la secuencia
                continue
            if val == 0 or largo < min_f:
                cuenta, fin, previo = 0, None, None
                continue
            pegado = fin is not None and i - fin <= 3
            cuenta = cuenta + 1 if pegado and val != previo else 1
            mejor = max(mejor, cuenta)
            fin, previo = i + largo, val
            if largo > max_f:                                           # punta larga: cierra y abre
                cuenta = 1
        if mejor >= 4:
            cols.append(int(x))
    grupos = []
    for x in cols:
        if grupos and x - grupos[-1][-1] <= 2:
            grupos[-1].append(x)
        else:
            grupos.append([x])
    return [round(float(np.mean(g)) * W / ancho, 1) for g in grupos if len(g) * W / ancho <= 20]


def inclinacion_riel(f):
    """Inclinación (grados) de la baranda blanca más larga del cuadro, o None si no se ve."""
    alto, ancho = f.shape[:2]
    hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
    blanco = ((hsv[..., 1] < 45) & (hsv[..., 2] > 190)).astype(np.uint8) * 255
    blanco[:int(0.20 * alto)] = 0                                       # gráfica de tiempos
    lineas = cv2.HoughLinesP(blanco, 1, np.pi / 360, 80, minLineLength=ancho // 4, maxLineGap=12)
    if lineas is None:
        return None
    lineas = lineas.reshape(-1, 4)
    largos = np.hypot(lineas[:, 2] - lineas[:, 0], lineas[:, 3] - lineas[:, 1])
    angulos = np.degrees(np.arctan2(lineas[:, 3] - lineas[:, 1], lineas[:, 2] - lineas[:, 0]))
    return round(float(np.median(angulos[largos > 0.6 * largos.max()])), 1)


def analizar_recta(video, desde, hasta, pista=PISTA):
    """Muestras [t, inclinación de la baranda, [x de los postes/arco]] entre desde y hasta."""
    wa, ha = int(round(W * RECTA_ESCALA)), int(round(H * RECTA_ESCALA))
    proceso = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", "%.3f" % desde, "-t", "%.3f" % (hasta - desde), "-i", video,
         "-vf", "fps=%g,scale=%d:%d" % (RECTA_FPS, wa, ha), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    tam, muestras = wa * ha * 3, []
    try:
        while True:
            b = proceso.stdout.read(tam)
            while 0 < len(b) < tam:
                resto = proceso.stdout.read(tam - len(b))
                if not resto:
                    break
                b += resto
            if len(b) < tam:
                break
            f = np.frombuffer(b, np.uint8).reshape(ha, wa, 3)
            muestras.append([round(desde + len(muestras) / RECTA_FPS, 2), inclinacion_riel(f), postes_meta(f, pista)])
    finally:
        proceso.stdout.close()
        proceso.kill()
        proceso.wait()
    return muestras


def cargar_o_analizar_recta(video, d, desde, pista=PISTA):
    """La pasada de la recta se guarda junto al análisis del video (en el mismo cache)."""
    r = d.get("recta")
    if (r and r.get("version") == VERSION_RECTA and r.get("pista", PISTA) == pista
            and r.get("ancho", 1920) == W and r.get("desde", 1e9) <= desde + 0.5):
        return r["muestras"]
    print("  buscando la recta final y la meta...", flush=True)
    d["recta"] = {"version": VERSION_RECTA, "pista": pista, "ancho": W, "desde": round(desde, 2),
                  "muestras": analizar_recta(video, desde, d["duracion"], pista)}
    with open(os.path.splitext(video)[0] + ".foco-cache.json", "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return d["recta"]["muestras"]


def detectar_meta(muestras, desde=0.0, cortes=()):
    """La toma de la meta: el primer poste/arco que se queda quieto en cuadro después de `desde`.

    Devuelve {"x": donde quedó el poste, "pista": [[t, x], ...] del poste mientras está la meta en
    cuadro (incluido cuando entra mientras la cámara todavía panea), "sentido": +1/-1 si se vio entrar
    el poste (entra por el lado hacia el que corren), "inicio_toma": t de la toma de la recta} o None."""
    pistas = []                                  # cada pista: el mismo poste en muestras seguidas
    for t, _, xs in muestras:
        if t < desde:
            continue
        usados = set()
        for p in pistas:
            if t - p[-1][0] > META_HUECO or not xs:
                continue
            x = min(xs, key=lambda x: abs(x - p[-1][1]))
            if abs(x - p[-1][1]) <= POSTE_SALTO and x not in usados:
                p.append([t, x])
                usados.add(x)
        pistas += [[[t, x]] for x in xs if x not in usados]
    riel = {t: a for t, a, _ in muestras}
    for p in pistas:                             # en orden: la primera meta que aparece
        if len(p) < POSTE_MIN:
            continue
        quieto = float(np.median([x for _, x in p[len(p) // 2:]]))
        cerca = [i for i, (_, x) in enumerate(p) if abs(x - quieto) <= POSTE_QUIETO]
        if len(cerca) < POSTE_MIN:
            continue
        # en la meta la cámara mira la recta de costado: baranda horizontal. Los postes de
        # distancia (también a franjas) se ven en la curva, con la baranda inclinada.
        angulos = [riel[p[i][0]] for i in cerca if riel.get(p[i][0]) is not None]
        if not angulos or abs(np.median(angulos)) > 8:
            continue
        # la meta dura mientras el poste no se aleja de donde quedó (después la cámara se va con
        # el ganador); antes, el poste entrando en cuadro también cuenta
        pista = p[:cerca[-1] + 1]
        entra = pista[0][1] - quieto
        meta = {"x": round(quieto, 1), "pista": pista,
                "sentido": int(np.sign(entra)) if abs(entra) > 0.25 * VENTANA else 0}
        meta["inicio_toma"] = inicio_toma_recta(muestras, pista[0][0], cortes)
        return meta
    return None


def inicio_toma_recta(muestras, t_meta, cortes=()):
    """Hacia atrás desde la meta, mientras siga la misma toma: la baranda inclinada menos de RIEL_MAX,
    sin saltos bruscos de inclinación y sin cruzar un corte de cámara."""
    tope = max((c for c in cortes if c < t_meta - 1.0), default=0.0)
    previas = [(t, a) for t, a, _ in muestras if tope <= t <= t_meta]
    inicio, ultimo, perdidas = t_meta, None, 0
    for t, a in reversed(previas):
        if t_meta - t > RECTA_MAX:
            break
        if a is None or abs(a) > RIEL_MAX or (ultimo is not None and abs(a - ultimo) > RIEL_SALTO):
            perdidas += 1
            if perdidas > 2:
                break
            continue
        inicio, ultimo, perdidas = t, a, 0
    return round(tope, 2) if tope and abs(inicio - tope) < 1.5 else inicio


def x_poste(meta, t):
    """x del poste/arco en el instante t: fijo en meta['x'] mientras la cámara ya está en la meta."""
    p = meta["pista"]
    if t < p[0][0] or t > p[-1][0]:
        return None
    ts = [q[0] for q in p]
    xp = float(np.interp(t, ts, [q[1] for q in p]))
    return meta["x"] if abs(xp - meta["x"]) <= POSTE_QUIETO else xp


# ----------------------------------------------------------------------------
# Qué se ve en cada momento
# ----------------------------------------------------------------------------

def mediana_movil(v, k):
    v = np.asarray(v, float)
    r = k // 2
    return np.array([np.median(v[max(0, i - r):i + r + 1]) for i in range(len(v))])


def pantalla_dividida(muestras):
    t = np.array([m["t"] for m in muestras])
    b = mediana_movil([m["banda"] for m in muestras], 11) > 0.4
    return t, b


def intervalos_dividida(muestras, minimo=DIV_MIN, hueco=DIV_HUECO, margen=DIV_MARGEN):
    """[[inicio, fin]] en segundos de los tramos con la pantalla dividida (la franja negra con
    "SEXTA CARRERA ... HIPÓDROMO"). Ahí abajo se ve al narrador: el cintillo va justo en esos tramos."""
    if not muestras:
        return []
    t, b = pantalla_dividida(muestras)
    tramos, ini = [], None
    for i in range(len(b)):
        if b[i] and ini is None:
            ini = float(t[i])
        elif not b[i] and ini is not None:
            tramos.append([ini, float(t[i])])
            ini = None
    if ini is not None:
        tramos.append([ini, float(t[-1])])
    unidos = []
    for a, f in tramos:                       # dos tramos casi pegados son uno solo
        if unidos and a - unidos[-1][1] < hueco:
            unidos[-1][1] = f
        else:
            unidos.append([a, f])
    return [[round(max(a - margen, 0.0), 2), round(f + margen, 2)]
            for a, f in unidos if f - a >= minimo]


def candidatos_corte(d):
    """Momentos en que la imagen cambia de golpe: corte, fundido o pantalla dividida que entra/sale."""
    fps = d["fps"]
    mad = np.array(d["mad"])
    c = []
    for i in range(1, len(mad)):
        local = np.r_[mad[max(0, i - 15):i], mad[i + 1:i + 16]]
        if mad[i] > 10 and mad[i] > 3 * max(np.median(local), 1.0):
            c.append(i / fps)
    c += [m["t"] for m in d["muestras"] if m["hist"] < 0.92]
    t, b = pantalla_dividida(d["muestras"])
    c += [float(t[i]) for i in range(1, len(b)) if b[i] != b[i - 1]]
    juntos = []
    for x in sorted(c):
        if not juntos or x - juntos[-1] > 0.6:
            juntos.append(x)
    return juntos


def separar(cajas, dividida, pista=PISTA):
    caballos, jinetes = [], []
    corte = PISTAS[pista]["corte"]
    graficas = PISTAS[pista]["graficas"]
    for x1, y1, x2, y2, conf, k in cajas:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if y2 - y1 < 14:
            continue
        if dividida and cy > corte:
            continue
        if any(a <= cx <= c and b <= cy <= e for a, b, c, e in graficas):
            continue
        (caballos if k == CABALLO else jinetes).append((x1, y1, x2, y2))
    if caballos:
        enteros = [c for c in caballos if not ((c[0] <= 10 or c[2] >= W - 10) and (c[2] - c[0]) < 220)]
        if enteros:
            caballos = enteros
    if len(caballos) >= 4:
        altos = np.median([c[3] - c[1] for c in caballos])
        caballos = [c for c in caballos if (c[3] - c[1]) >= 0.55 * altos]
    return caballos, jinetes


def orientacion(caballos, jinetes):
    """Mediana de cuánto está corrido el jinete hacia un lado de su caballo (+ derecha, - izquierda).
    Se ignoran caballos cortados por el borde del cuadro y jinetes en la superposición de dos caballos."""
    corr = []
    enteros = [c for c in caballos if c[0] > 15 and c[2] < W - 15]
    for x1, y1, x2, y2 in enteros:
        suyos = [j for j in jinetes
                 if x1 < (j[0] + j[2]) / 2 < x2
                 and j[3] < y1 + 0.8 * (y2 - y1)
                 and sum(1 for c in caballos if c[0] < (j[0] + j[2]) / 2 < c[2]) == 1]
        if suyos:
            j = max(suyos, key=lambda j: (j[2] - j[0]) * (j[3] - j[1]))
            corr.append(((j[0] + j[2]) / 2 - (x1 + x2) / 2) / (x2 - x1))
    return float(np.median(corr)) if corr else None


# ----------------------------------------------------------------------------
# Sin gráfica de tiempos (Valencia): se estiman los parciales por distancia
# ----------------------------------------------------------------------------

def tramo_carrera(d, pista=PISTA, desde=1.0):
    """(inicio, fin) de la carrera estimados con los caballos que se detectan.

    Los caballos aparecen cuando se abre el partidor (antes están tapados por la manga) y en la
    llegada pasan por delante de la cámara y se pierden unos segundos, hasta que vuelven a
    detectarse ya al trote: ese hueco es la meta."""
    td, bd = pantalla_dividida(d["muestras"])
    dividida = lambda t: bool(bd[min(np.searchsorted(td, t), len(bd) - 1)])
    pasos = []
    for x in d["detecciones"]:
        if x["t"] < desde:
            continue
        cab, _ = separar(x["cajas"], dividida(x["t"]), pista)
        pasos.append((x["t"], len(cab)))
    inicio = next((t for t, n in pasos if n >= 3), None)
    fin, i = None, 0
    while i < len(pasos):
        if pasos[i][1]:
            i += 1
            continue
        j = i
        while j < len(pasos) and not pasos[j][1]:
            j += 1
        previo = pasos[i - 1] if i else None
        vuelve = pasos[j] if j < len(pasos) else None
        if (previo and previo[1] and vuelve
                and previo[0] >= d["duracion"] * 0.55
                and not dividida(previo[0])
                and vuelve[0] - previo[0] >= 1.5                # el hueco en la meta dura ~1.5 s o más
                and vuelve[0] - pasos[j - 1][0] <= 15.0):       # y después vuelven a verse
            fin = previo[0]
        i = j
    if fin is None:
        con_caballos = [t for t, n in pasos if n]
        fin = con_caballos[-1] if con_caballos else None
    return inicio, fin


def parciales_por_distancia(d, distancias, pista=PISTA, fin_forzado=None):
    """Tiempos estimados de cada parcial, repartiendo la distancia de la carrera entre la partida y
    la meta. Es lo que se usa donde el canal no muestra los tiempos (Valencia)."""
    if not distancias:
        return []
    inicio, fin = tramo_carrera(d, pista)
    if fin_forzado is not None:
        fin = fin_forzado
    if inicio is None or fin is None or fin <= inicio:
        return []
    total = float(max(distancias))
    return [round(inicio + (dist / total) * (fin - inicio), 2) for dist in distancias]


# ----------------------------------------------------------------------------
# Reglas de encuadre
# ----------------------------------------------------------------------------

def encuadre(caballos, sentido, fase, frontal, zoom=1.0):
    """x del centro de la ventana para un instante, o None si no hay caballos."""
    if not caballos:
        return None, "sin caballos"
    centro_grupo = float(np.median([(c[0] + c[2]) / 2 for c in caballos]))
    if fase == "despues":
        area = lambda c: (c[2] - c[0]) * (c[3] - c[1])
        grandes = sorted(caballos, key=area, reverse=True)
        if len(grandes) == 1 or area(grandes[0]) > 1.5 * area(grandes[1]):   # el ganador en primer plano
            return (grandes[0][0] + grandes[0][2]) / 2, "ganador"
        return centro_grupo, "pelotón"
    if sentido == 0:
        return centro_grupo, "pelotón"
    cabeza = lambda c: c[2] if sentido > 0 else c[0]
    cola = lambda c: c[0] if sentido > 0 else c[2]
    orden = sorted(caballos, key=lambda c: sentido * cabeza(c), reverse=True)
    lider = orden[0]
    if zoom > 1.01:
        mitad_z, margen_z = MITAD / zoom, MARGEN / zoom
        return cabeza(lider) - sentido * (mitad_z - margen_z), "puntero lejos"
    centro_lider = (lider[0] + lider[2]) / 2
    if lider[2] - lider[0] > 0.9 * VENTANA:            # no entra con aire delante: centrarlo
        return centro_lider, "primer plano"
    ventaja = sentido * (cabeza(lider) - cabeza(orden[1])) if len(orden) > 1 else 1e9
    if frontal and (fase == "previa" or ventaja < LUCHA):
        # cámara de frente o de atrás y los caballos van juntos: se centra el grupo, la dirección no
        # dice quién va adelante. Si uno se despegó, se lo sigue: en la recta final es lo que importa.
        return centro_grupo, "pelotón"
    if fase == "final":
        if ventaja < LUCHA:
            a, b = cabeza(lider), cola(orden[1])
            if abs(a - b) < VENTANA - 2 * MARGEN:
                return (a + b) / 2, "lucha"
            return cabeza(lider) - sentido * (MITAD - MARGEN), "lucha (puntero)"
        return centro_lider, "puntero solo"
    if ventaja > SOLO:
        return centro_lider, "puntero solo"
    return cabeza(lider) - sentido * (MITAD - MARGEN), "puntero"


def encuadre_recta(caballos, sentido, frontal):
    """Recta final (flash): siempre el puntero, con su cabeza cerca del borde delantero para que en
    el resto de la ventana entren los que vienen detrás. Si el segundo está tan lejos que no entra
    igual, el encuadre se va corriendo de a poco hasta centrar al puntero (sin saltos)."""
    if not caballos:
        return None, "sin caballos"
    centro_grupo = float(np.median([(c[0] + c[2]) / 2 for c in caballos]))
    if sentido == 0:
        return centro_grupo, "pelotón"
    cabeza = lambda c: c[2] if sentido > 0 else c[0]
    orden = sorted(caballos, key=lambda c: sentido * cabeza(c), reverse=True)
    lider = orden[0]
    centro_lider = (lider[0] + lider[2]) / 2
    if lider[2] - lider[0] > 0.9 * VENTANA:
        return centro_lider, "primer plano"
    if len(orden) == 1:
        return centro_lider, "puntero solo"
    hueco = sentido * (cabeza(lider) - cabeza(orden[1]))
    if frontal and hueco < LUCHA:                # de frente y juntos: no se sabe quién va adelante
        return centro_grupo, "pelotón"
    adelante = cabeza(lider) - sentido * (MITAD - MARGEN)
    entra = VENTANA - 2 * MARGEN                 # con el puntero adelante, el segundo entra hasta acá
    if hueco <= entra:
        return adelante, "puntero y pelotón"
    k = min((hueco - entra) / (0.5 * VENTANA), 1.0)
    return adelante + k * (centro_lider - adelante), "puntero"


def en_la_meta(caballos, sentido, xp):
    """¿Ya se puede dejar el foco fijo en el poste? Sí cuando el puntero entra entero en la ventana
    centrada en el poste (o ya pasó); antes se lo sigue para no perderlo de vista."""
    if not caballos or sentido == 0:
        return True
    cabeza = lambda c: c[2] if sentido > 0 else c[0]
    lider = max(caballos, key=lambda c: sentido * cabeza(c))
    cola = lider[0] if sentido > 0 else lider[2]
    return sentido * (cola - xp) >= -(MITAD - 0.05 * VENTANA)


def rezagado(caballos, sentido):
    """x de un caballo claramente separado detrás del grupo, o None."""
    if sentido == 0 or len(caballos) < 4:
        return None
    pos = sorted(caballos, key=lambda c: sentido * (c[0] + c[2]) / 2)
    ancho = np.median([c[2] - c[0] for c in caballos])
    ultimo, penultimo = pos[0], pos[1]
    hueco = sentido * ((penultimo[0] + penultimo[2]) - (ultimo[0] + ultimo[2])) / 2
    return (ultimo[0] + ultimo[2]) / 2 if hueco > 1.8 * ancho else None


def calcular(d, parciales, partida=0.0, flash=False, recta=None, pista=PISTA, sin_recta=False):
    """recta: muestras de analizar_recta(); con ellas (perfil flash) la recta final empieza donde
    empieza la toma que termina en la meta, y en la meta el foco queda fijo en el poste/arco de llegada.
    sin_recta: en la recta final no se mueve el foco (queda fijo, lo ajusta el editor a mano);
    después de la meta vuelve el foco automático."""
    global W
    W = d.get("ancho", W)
    dur = d["duracion"]
    td, bd = pantalla_dividida(d["muestras"])
    dividida = lambda t: bool(bd[min(np.searchsorted(td, t), len(bd) - 1)])
    cand_cortes = candidatos_corte(d)
    inicio_final = parciales[-2] if len(parciales) >= 2 else dur * 0.7
    meta = parciales[-1] - 1.5 if parciales else dur * 0.75
    notas = []
    linea = detectar_meta(recta, min(inicio_final, dur * 0.65), cand_cortes) if recta else None
    if linea:
        llega = linea["pista"][0][0]
        en_corte = any(abs(linea["inicio_toma"] - c) < 0.5 for c in cand_cortes)
        inicio_final = (linea["inicio_toma"] if (en_corte or llega - linea["inicio_toma"] >= RECTA_MIN)
                        else min(linea["inicio_toma"], inicio_final))
        meta = linea["pista"][-1][0]             # después de la toma de la meta: el ganador
        notas.append("Recta final desde %.1f s; meta (arco/poste de llegada) en cuadro de %.1f a %.1f s."
                     % (inicio_final, llega, meta))
    elif recta:
        notas.append("No se encontró el poste de llegada: la recta final empieza en el penúltimo parcial.")

    # 1) qué hay en cada instante
    instantes = []
    for x in d["detecciones"]:
        div = dividida(x["t"])
        cab, jin = separar(x["cajas"], div, pista)
        alto = float(np.median([(c[3] - c[1]) / max(c[2] - c[0], 1) for c in cab])) if cab else None
        alto_px = float(np.median([c[3] - c[1] for c in cab])) if cab else None
        cy_med = float(np.median([(c[1] + c[3]) / 2 for c in cab])) if cab else None
        ancho_grupo = (max(c[2] for c in cab) - min(c[0] for c in cab)) if len(cab) >= 2 else 0.0
        lejos = (not div) and len(cab) >= 3 and alto_px is not None and alto_px < 108 and cy_med is not None and cy_med > 620
        instantes.append({"t": x["t"], "caballos": cab, "orient": orientacion(cab, jin),
                          "alto": alto, "alto_px": alto_px, "ancho_grupo": ancho_grupo, "lejos": lejos})

    lejos_arr = mediana_movil([1.0 if x["lejos"] else 0.0 for x in instantes], 7) > 0.5 if instantes else []
    for x, lj in zip(instantes, lejos_arr):
        x["lejos"] = bool(flash and lj and x["t"] < inicio_final)
        x["zoom"] = 1.0

    # 2) sentido de carrera y cámara de frente, mirando ±2.0 s alrededor
    ts = np.array([x["t"] for x in instantes])
    sentido_previo = 0
    for x in instantes:
        cerca = [y for y in instantes if abs(y["t"] - x["t"]) <= 2.0]
        o = [y["orient"] for y in cerca if y["orient"] is not None]
        if o and abs(np.median(o)) >= 0.03:
            sentido_previo = 1 if np.median(o) > 0 else -1
        x["sentido"] = sentido_previo
        a = [y["alto"] for y in cerca if y["alto"] is not None and abs(y["t"] - x["t"]) <= 1.0]
        altos_px = [y["alto_px"] for y in cerca if y["alto_px"] is not None and abs(y["t"] - x["t"]) <= 1.0]
        anchos_g = [y["ancho_grupo"] for y in cerca if abs(y["t"] - x["t"]) <= 1.0]
        es_panoramica = (bool(altos_px) and np.median(altos_px) < 120) or (bool(anchos_g) and np.median(anchos_g) > 0.75 * VENTANA)
        x["frontal"] = bool(a) and float(np.median(a)) > FRONTAL and not es_panoramica
    if linea:
        # en la recta todos corren hacia la meta: un solo sentido para toda la toma. Lo da el poste
        # (entra por el lado hacia el que corren); si no se lo vio entrar, los jinetes de toda la recta
        en_recta = [x for x in instantes if inicio_final <= x["t"] <= meta]
        o = [x["orient"] for x in en_recta if x["orient"] is not None]
        s = linea["sentido"] or (int(np.sign(np.median(o))) if o and abs(np.median(o)) >= 0.03 else 0)
        for x in en_recta:
            x["sentido"] = s or x["sentido"]
            # la toma de la recta mira en diagonal y los caballos se ven altos, como de frente; con
            # el sentido ya sabido, el más adelantado en pantalla es el puntero
            x["frontal"] = x["frontal"] and not s

    # 3) encuadre ideal en cada instante
    serie = []
    fijo = False                                 # ya se llegó a la meta: el foco queda en el poste
    for x in instantes:
        t = x["t"]
        fase = "despues" if t > meta else ("final" if t >= inicio_final else
                                           ("previa" if t < partida - 1 else
                                            ("partida" if t < partida + PARTIDA else "carrera")))
        if linea and (fase == "final" or (fijo and not x["caballos"])):
            cx, motivo = encuadre_recta(x["caballos"], x["sentido"], x["frontal"])
            xp = x_poste(linea, t) if t <= meta else (linea["x"] if fijo else None)
            if xp is not None and (fijo or en_la_meta(x["caballos"], x["sentido"], xp)):
                cx, motivo, fase, fijo = xp, "meta", "meta", True
        else:
            fijo = False
            cx, motivo = encuadre(x["caballos"], x["sentido"], fase, x["frontal"] or fase == "previa")
        serie.append([t, cx, motivo, fase, x["lejos"]])
    seguidas = 0
    for p, x in zip(serie, instantes):          # partida: alguien quedó muy atrás 3 muestras seguidas
        if p[3] != "partida":
            continue
        xr = rezagado(x["caballos"], x["sentido"])
        seguidas = seguidas + 1 if xr is not None else 0
        if seguidas == 3:
            for q in serie:
                if p[0] - 0.4 <= q[0] <= p[0] - 0.4 + VISITA:
                    q[1], q[2] = xr, "partió mal"
            notas.append("Partida: un caballo quedó atrás a los %.1f s; se lo enfoca %.1f s." % (p[0], VISITA))
            break
    ultimo = next((p[1] for p in serie if p[1] is not None), W / 2)
    for p in serie:                              # sin caballos: se mantiene el encuadre
        ultimo = p[1] = p[1] if p[1] is not None else ultimo

    # 4) cortes: cambio de imagen + cambio grande del encuadre ideal
    xs = np.array([p[1] for p in serie])
    cortes = []
    for c in cand_cortes:
        antes = xs[(ts >= c - 1.2) & (ts < c - 0.1)]
        despues = xs[(ts > c + 0.1) & (ts <= c + 1.2)]
        if len(antes) and len(despues) and abs(np.median(despues) - np.median(antes)) > SALTO:
            cortes.append(c)

    # 5) suavizado por toma y keyframes
    bordes = [0.0] + cortes + [dur + 1]
    keyframes = []
    for a, b in zip(bordes, bordes[1:]):
        tramo = [p for p in serie if a <= p[0] < b]
        if not tramo:
            continue
        kf = suavizar(tramo, a, flash)
        if a > 0:
            kf[0][2] = "corte"
        keyframes += kf
    en_meta = [p[0] for p in serie if p[2] == "meta"]

    # 6) sin recta final: entre el comienzo de la recta y la meta el foco queda fijo, para que el
    # editor lo arme a mano. Después de la meta vuelve el foco automático (con un corte, como la
    # transmisión, que cambia de cámara al cruzar).
    sin_foco = None
    if sin_recta and meta - inicio_final >= 1.0:
        antes = [k for k in keyframes if k[0] < inicio_final - 0.05]
        despues = [k for k in keyframes if k[0] > meta + 0.05]
        x_fijo = antes[-1][1] if antes else (despues[0][1] if despues else W / 2.0)
        keyframes = (antes
                     + [[round(inicio_final, 3), round(x_fijo, 1), "sin foco (recta final)"],
                        [round(meta, 3), round(x_fijo, 1), "sin foco (recta final)"]]
                     + despues)
        if despues:
            keyframes[len(antes) + 2][2] = "corte"
        sin_foco = [round(inicio_final, 2), round(meta, 2)]
        notas.append("Recta final de %.1f a %.1f s: sin foco automático (queda fijo en x=%.0f para "
                     "que lo ajustes a mano); después de la meta sigue solo."
                     % (inicio_final, meta, x_fijo))

    return {"keyframes": keyframes, "cortes": [round(c, 2) for c in cortes], "notas": notas,
            "sin_foco": sin_foco,
            "recta": {"inicio": round(inicio_final, 2), "poste": linea["x"] if linea else None,
                      "foco_fijo": [round(en_meta[0], 2), round(en_meta[-1], 2)] if en_meta else None},
            "serie": [[round(p[0], 2), round(p[1], 1), p[2]] for p in serie]}


# ----------------------------------------------------------------------------
# Suavizado y keyframes
# ----------------------------------------------------------------------------

def limitar(x):
    return float(min(max(x, MITAD), W - MITAD))


def rdp(puntos, tol):
    if len(puntos) < 3:
        return puntos
    (t0, x0), (t1, x1) = puntos[0], puntos[-1]
    peor, k = 0.0, 0
    for i in range(1, len(puntos) - 1):
        t, x = puntos[i]
        esperado = x0 + (x1 - x0) * (t - t0) / ((t1 - t0) or 1)
        if abs(x - esperado) > peor:
            peor, k = abs(x - esperado), i
    if peor <= tol:
        return [puntos[0], puntos[-1]]
    return rdp(puntos[:k + 1], tol)[:-1] + rdp(puntos[k:], tol)


def suavizar(tramo, a, flash=False):
    """Keyframes [t, x, motivo] de una toma: sin temblores y con velocidad de paneo limitada."""
    ts = [p[0] for p in tramo]
    xs = [p[1] for p in tramo]
    visita = [p[2] == "partió mal" for p in tramo]
    fijo = [p[2] == "meta" for p in tramo]
    fases = [p[3] for p in tramo]
    lejos = [bool(p[4]) if len(p) > 4 else False for p in tramo]
    tipo_tramo = [("meta" if fijo[i] else ("recta" if fases[i] == "final" else ("lejos" if lejos[i] else "normal")))
                  for i in range(len(tramo))]
    # la mediana se aplica por separado antes de la meta, en la recta, en la meta y después
    m = []
    for grupo in tramos_iguales(tipo_tramo):
        tp = tipo_tramo[grupo[0]]
        k_med = 5 if tp in ("meta", "recta") else (7 if tp == "lejos" else (MEDIANA_FLASH if flash else 9))
        m += list(mediana_movil([xs[i] for i in grupo], k_med))
    m = [xs[i] if visita[i] else m[i] for i in range(len(m))]
    for orden in (range(1, len(m)), range(len(m) - 2, -1, -1)):   # hacia adelante y hacia atrás
        for i in orden:
            j = i - 1 if orden.step == 1 else i + 1
            entra = fijo[max(i, j)] and not fijo[min(i, j)]               # llegada a la meta: rápido
            rapido = 3 if visita[i] or visita[j] else (2 if entra else 1)
            lim = VEL_MAX * rapido * abs(ts[i] - ts[j])
            m[i] = m[j] + max(-lim, min(lim, m[i] - m[j]))
    puntos = [(round(t, 3), round(limitar(x), 1)) for t, x in zip(ts, m)]
    puntos[0] = (round(a, 3), puntos[0][1])
    motivo = {round(p[0], 3): p[2] for p in tramo}
    if not flash:
        return [[t, x, motivo.get(t, "")] for t, x in rdp(puntos, TOLERANCIA)]
    # flash: pocos movimientos de cámara, largos y parejos. En la recta final se sigue de cerca al
    # puntero y pelotón; en la meta el foco queda fijo en el arco/poste de llegada.
    kf = []
    for grupo in tramos_iguales(tipo_tramo):
        tp = tipo_tramo[grupo[0]]
        fin = grupo[-1] + 1                                      # el primero del tramo siguiente
        if tp == "meta":                                         # en la meta: quieto en el poste
            pedazo = decimar(puntos[grupo[0]:fin], TOL_META, 2) + puntos[fin:fin + 1]
        elif tp == "recta":
            pedazo = puntos[grupo[0]:fin + 1]
            objetivo = max(2, int(round((pedazo[-1][0] - pedazo[0][0]) / 2.2)) + 1)
            pedazo = decimar(decimar(pedazo, 30.0, len(pedazo)), 60.0, objetivo)
        elif tp == "lejos":
            pedazo = puntos[grupo[0]:fin + 1]
            objetivo = max(2, int(round((pedazo[-1][0] - pedazo[0][0]) / 4.5)) + 1)
            pedazo = decimar(decimar(pedazo, 45.0, len(pedazo)), 85.0, objetivo)
        else:
            pedazo = puntos[grupo[0]:fin + 1]
            objetivo = max(2, int(round((pedazo[-1][0] - pedazo[0][0]) / SEG_MIN_FLASH)) + 1)
            pedazo = decimar(decimar(pedazo, TOL_FLASH, len(pedazo)), TOL_MAX_FLASH, objetivo)
        kf += pedazo if not kf else pedazo[1:] if kf[-1] == pedazo[0] else pedazo
    return [[t, x, motivo.get(t, "")] for t, x in kf]


def tramos_iguales(v):
    """Índices agrupados en tramos seguidos con el mismo valor: [[0, 1, 2], [3, 4], ...]."""
    grupos = []
    for i, x in enumerate(v):
        if grupos and v[grupos[-1][0]] == x:
            grupos[-1].append(i)
        else:
            grupos.append([i])
    return grupos


def decimar(puntos, tol, objetivo):
    """Saca de a un punto mientras el error que agrega no pase de tol (o hasta llegar a objetivo)."""
    p = list(puntos)
    while len(p) > max(objetivo, 2):
        peor, k = None, -1
        for i in range(1, len(p) - 1):
            (t0, x0), (t1, x1), (t2, x2) = p[i - 1], p[i], p[i + 1]
            esperado = x0 + (x2 - x0) * (t1 - t0) / ((t2 - t0) or 1e-6)
            err = abs(x1 - esperado)
            if peor is None or err < peor:
                peor, k = err, i
        if k < 0 or peor > tol:
            break
        del p[k]
    return p


# ----------------------------------------------------------------------------
# Vista previa vertical (para revisar sin abrir Camtasia)
# ----------------------------------------------------------------------------

def x_en(keyframes, t):
    """x del centro en el instante t: lineal entre keyframes; en un corte salta sin animar."""
    prev = None
    for k in keyframes:
        if k[0] > t:
            if prev is None:
                return k[1]
            if k[2] == "corte":
                return prev[1]
            return prev[1] + (k[1] - prev[1]) * (t - prev[0]) / max(k[0] - prev[0], 1e-6)
        prev = k
    return prev[1]


def vista_previa(video, keyframes, salida):
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    ancho = int(round(VENTANA)) // 2 * 2
    out = cv2.VideoWriter(salida, cv2.VideoWriter_fourcc(*"mp4v"), fps, (ancho, H))
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if f.shape[1] != W or f.shape[0] != H:
            f = cv2.resize(f, (W, H))
        izq = int(round(x_en(keyframes, i / fps) - ancho / 2))
        izq = min(max(izq, 0), W - ancho)
        out.write(np.ascontiguousarray(f[:, izq:izq + ancho]))
        i += 1
    out.release()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Foco automático (paneo) para el video vertical de una carrera.")
    ap.add_argument("video")
    ap.add_argument("--salida", required=True, help="json con los keyframes")
    ap.add_argument("--parciales", default="", help="segundos en que aparece cada parcial, separados por coma")
    ap.add_argument("--partida", type=float, default=0.0, help="segundo en que sale la gráfica de tiempos")
    ap.add_argument("--preview", help="mp4 vertical para revisar el foco")
    ap.add_argument("--flash", action="store_true",
                    help="perfil rápido: menos cuadros analizados y muchos menos movimientos de cámara")
    ap.add_argument("--pista", default=PISTA, choices=sorted(PISTAS),
                    help="hipódromo: define la franja de pantalla dividida y las zonas con gráficas")
    ap.add_argument("--distancias", default="",
                    help="distancias de los banners (400,800,1200,1300): si el canal no muestra los "
                         "tiempos, los parciales se estiman repartiendo la distancia de la carrera")
    ap.add_argument("--sin-recta-final", action="store_true",
                    help="en la recta final el foco no se mueve (queda fijo para ajustarlo a mano); "
                         "después de la meta sigue automático")
    a = ap.parse_args()
    parciales = [float(x) for x in a.parciales.split(",") if x.strip()]
    distancias = [int(x) for x in a.distancias.split(",") if x.strip()]
    perfil = PERFILES["flash" if a.flash else "normal"]

    d = cargar_o_analizar(a.video, perfil, a.pista)
    partida, estimados = a.partida, False
    inicio_carr, fin_carr = tramo_carrera(d, a.pista)
    if not parciales and distancias:
        parciales = parciales_por_distancia(d, distancias, a.pista)
        if parciales:
            estimados = True
            partida = max(partida, inicio_carr or 0.0)
    recta = None
    if a.flash:
        penultimo = min(parciales[-2], d["duracion"] * 0.75) if len(parciales) >= 2 else d["duracion"] * 0.7
        recta = cargar_o_analizar_recta(a.video, d, max(penultimo - RECTA_ANTES, partida, 0.0), a.pista)
        if estimados and recta:
            linea_m = detectar_meta(recta, d["duracion"] * 0.60, candidatos_corte(d))
            if linea_m and (fin_carr is None or fin_carr > linea_m["pista"][-1][0] + 1.0 or fin_carr < linea_m["pista"][0][0]):
                fin_carr = round(min(linea_m["pista"][0][0] + 2.0, linea_m["pista"][-1][0]), 2)
                parciales = parciales_por_distancia(d, distancias, a.pista, fin_forzado=fin_carr)
    if estimados and parciales:
        print("  el canal no muestra los tiempos en pantalla; parciales estimados por distancia")
        print("  (carrera de %.1f a %.1f s): %s"
              % (inicio_carr, fin_carr, ", ".join("%.1f s" % t for t in parciales)))
    r = calcular(d, parciales, partida, a.flash, recta, a.pista, a.sin_recta_final)
    r["ancho"] = W
    r["ventana"] = VENTANA
    r["escala"] = ESCALA
    r["flash"] = bool(a.flash)
    r["dividida"] = intervalos_dividida(d["muestras"])
    r["parciales"] = parciales
    r["carrera"] = [inicio_carr, fin_carr]
    r["parciales_estimados"] = estimados
    with open(a.salida, "w", encoding="utf-8") as fh:
        json.dump(r, fh, ensure_ascii=False, indent=1)
    print("  foco: %d keyframes, %d cortes de cámara (%s)"
          % (len(r["keyframes"]), len(r["cortes"]), ", ".join("%.1f s" % c for c in r["cortes"])))
    print("  pantalla dividida: " + (", ".join("%.1f-%.1f s" % (x, y) for x, y in r["dividida"])
                                     or "no hubo"))
    for n in r["notas"]:
        print("  " + n)
    if a.preview:
        vista_previa(a.video, r["keyframes"], a.preview)
        print("  vista previa: %s" % a.preview)


if __name__ == "__main__":
    main()
