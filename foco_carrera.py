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
VERSION = 2                       # cambia si cambia lo que se guarda en el análisis

# El perfil "flash" mira menos cuadros y con menos resolución: sigue a los caballos casi igual
# pero tarda una fracción. Además deja pocos movimientos de cámara (ver reducir()).
PERFILES = {
    "normal": {"modelo": MODELO, "imgsz": IMGSZ, "paso_deteccion": 6, "escala": 1.0, "lote": 4},
    "flash": {"modelo": MODELO, "imgsz": 960, "paso_deteccion": 12, "escala": 0.5, "lote": 8},
}

W, H = 1920, 1080                 # el análisis se hace en coordenadas de un cuadro 1920x1080
ESCALA = 1920 / 1080              # escala del video en el proyecto vertical
VENTANA = 1080 / ESCALA           # ancho visible del original (~607 px)
MITAD = VENTANA / 2
PASO_ANALISIS = 3                 # cuadros: histograma y pantalla dividida (~10/s)
PASO_DETECCION = PERFILES["normal"]["paso_deteccion"]   # cuadros: caballos y jinetes (~5/s)

Y_BANDA = (578, 622)              # franja negra "SEXTA CARRERA ... INH" de la pantalla dividida
Y_CORTE_DIVIDIDA = 565            # en pantalla dividida solo cuenta la imagen de arriba
ZONAS_GRAFICAS = [(0, 0, 790, 165), (1470, 0, 1920, 165), (0, 840, 620, 1080)]  # tiempo, velocidad, orden

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
DIV_MIN = 3.0                     # s: pantalla dividida más corta que esto se ignora
DIV_HUECO = 2.0                   # s: dos tramos separados por menos que esto se unen
DIV_MARGEN = 0.3                  # s: el cintillo entra un poco antes y sale un poco después


# ----------------------------------------------------------------------------
# Lectura del video: un solo recorrido
# ----------------------------------------------------------------------------

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
                if f.shape[1] != W:
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


def analizar(video, perfil=None, fps=None, total=0):
    from ultralytics import YOLO
    perfil = perfil or PERFILES["normal"]
    esc = float(perfil.get("escala", 1.0))
    paso = max(int(round(4 * esc)), 1)                  # muestreo de histograma y de la franja
    modelo = YOLO(os.path.join(AQUI, "modelos", perfil["modelo"]))
    if fps is None or not total:
        c = cv2.VideoCapture(video)
        fps = fps or c.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
        c.release()
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
            banda = float((gris[int(Y_BANDA[0] * esc):int(Y_BANDA[1] * esc):max(int(2 * esc), 1),
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
    return {"fps": fps, "duracion": i / fps, "mad": mad, "muestras": muestras, "detecciones": detecciones}


def sirve(d, video, perfil):
    """¿El análisis guardado alcanza para el perfil que se pide? (mismo modelo, igual o mejor calidad)"""
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


def cargar_o_analizar(video, perfil=None):
    perfil = perfil or PERFILES["normal"]
    cache = os.path.splitext(video)[0] + ".foco-cache.json"
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            d = json.load(fh)
        if sirve(d, video, perfil):
            print("  usando el análisis guardado (%s)" % os.path.basename(cache))
            return d
    print("  analizando el video (la primera vez tarda unos minutos)...")
    c = cv2.VideoCapture(video)
    fps = c.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
    c.release()
    d = analizar(video, perfil, fps, total)
    d["identidad"] = identidad(video)
    d["perfil"] = dict(perfil)
    d["huella"] = huella(video)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return d


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


def separar(cajas, dividida):
    caballos, jinetes = [], []
    for x1, y1, x2, y2, conf, k in cajas:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if y2 - y1 < 14:
            continue
        if dividida and cy > Y_CORTE_DIVIDIDA:
            continue
        if any(a <= cx <= c and b <= cy <= e for a, b, c, e in ZONAS_GRAFICAS):
            continue
        (caballos if k == CABALLO else jinetes).append((x1, y1, x2, y2))
    return caballos, jinetes


def orientacion(caballos, jinetes):
    """Mediana de cuánto está corrido el jinete hacia un lado de su caballo (+ derecha, - izquierda)."""
    corr = []
    for x1, y1, x2, y2 in caballos:
        suyos = [j for j in jinetes if x1 < (j[0] + j[2]) / 2 < x2 and j[3] < y1 + 0.8 * (y2 - y1)]
        if suyos:
            j = max(suyos, key=lambda j: (j[2] - j[0]) * (j[3] - j[1]))
            corr.append(((j[0] + j[2]) / 2 - (x1 + x2) / 2) / (x2 - x1))
    return float(np.median(corr)) if corr else None


# ----------------------------------------------------------------------------
# Reglas de encuadre
# ----------------------------------------------------------------------------

def encuadre(caballos, sentido, fase, frontal):
    """x del centro de la ventana para un instante, o None si no hay caballos."""
    if not caballos:
        return None, "sin caballos"
    if fase == "despues":
        area = lambda c: (c[2] - c[0]) * (c[3] - c[1])
        grandes = sorted(caballos, key=area, reverse=True)
        if len(grandes) == 1 or area(grandes[0]) > 2 * area(grandes[1]):   # el ganador en primer plano
            return (grandes[0][0] + grandes[0][2]) / 2, "ganador"
        fase = "final"                                                   # todavía se ven varios
    centro_grupo = float(np.median([(c[0] + c[2]) / 2 for c in caballos]))
    if sentido == 0:
        return centro_grupo, "pelotón"
    cabeza = lambda c: c[2] if sentido > 0 else c[0]
    cola = lambda c: c[0] if sentido > 0 else c[2]
    orden = sorted(caballos, key=lambda c: sentido * cabeza(c), reverse=True)
    lider = orden[0]
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


def rezagado(caballos, sentido):
    """x de un caballo claramente separado detrás del grupo, o None."""
    if sentido == 0 or len(caballos) < 4:
        return None
    pos = sorted(caballos, key=lambda c: sentido * (c[0] + c[2]) / 2)
    ancho = np.median([c[2] - c[0] for c in caballos])
    ultimo, penultimo = pos[0], pos[1]
    hueco = sentido * ((penultimo[0] + penultimo[2]) - (ultimo[0] + ultimo[2])) / 2
    return (ultimo[0] + ultimo[2]) / 2 if hueco > 1.8 * ancho else None


def calcular(d, parciales, partida=0.0, flash=False):
    dur = d["duracion"]
    td, bd = pantalla_dividida(d["muestras"])
    dividida = lambda t: bool(bd[min(np.searchsorted(td, t), len(bd) - 1)])
    inicio_final = parciales[-2] if len(parciales) >= 2 else dur * 0.7
    meta = parciales[-1] - 1.5 if parciales else dur * 0.75

    # 1) qué hay en cada instante
    instantes = []
    for x in d["detecciones"]:
        cab, jin = separar(x["cajas"], dividida(x["t"]))
        alto = float(np.median([(c[3] - c[1]) / max(c[2] - c[0], 1) for c in cab])) if cab else None
        instantes.append({"t": x["t"], "caballos": cab, "orient": orientacion(cab, jin), "alto": alto})

    # 2) sentido de carrera y cámara de frente, mirando ±1.5 s alrededor
    ts = np.array([x["t"] for x in instantes])
    sentido_previo = 0
    for x in instantes:
        cerca = [y for y in instantes if abs(y["t"] - x["t"]) <= 1.5]
        o = [y["orient"] for y in cerca if y["orient"] is not None]
        if o and abs(np.median(o)) >= 0.03:
            sentido_previo = 1 if np.median(o) > 0 else -1
        x["sentido"] = sentido_previo
        a = [y["alto"] for y in cerca if y["alto"] is not None and abs(y["t"] - x["t"]) <= 1.0]
        x["frontal"] = bool(a) and float(np.median(a)) > FRONTAL

    # 3) encuadre ideal en cada instante
    serie = []
    for x in instantes:
        t = x["t"]
        fase = "despues" if t >= meta else ("final" if t >= inicio_final else
                                            ("previa" if t < partida - 1 else
                                             ("partida" if t < partida + PARTIDA else "carrera")))
        cx, motivo = encuadre(x["caballos"], x["sentido"], fase, x["frontal"] or fase == "previa")
        serie.append([t, cx, motivo, fase])
    notas = []
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
    for c in candidatos_corte(d):
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
    return {"keyframes": keyframes, "cortes": [round(c, 2) for c in cortes], "notas": notas,
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
    m = list(mediana_movil(xs, MEDIANA_FLASH if flash else 9))
    m = [xs[i] if visita[i] else m[i] for i in range(len(m))]
    for orden in (range(1, len(m)), range(len(m) - 2, -1, -1)):   # hacia adelante y hacia atrás
        for i in orden:
            j = i - 1 if orden.step == 1 else i + 1
            lim = VEL_MAX * (3 if visita[i] or visita[j] else 1) * abs(ts[i] - ts[j])
            m[i] = m[j] + max(-lim, min(lim, m[i] - m[j]))
    puntos = [(round(t, 3), round(limitar(x), 1)) for t, x in zip(ts, m)]
    puntos[0] = (round(a, 3), puntos[0][1])
    motivo = {round(p[0], 3): p[2] for p in tramo}
    if not flash:
        return [[t, x, motivo.get(t, "")] for t, x in rdp(puntos, TOLERANCIA)]
    # flash: pocos movimientos de cámara, largos y parejos. Primero se saca lo que sobra sin que
    # se note (TOL_FLASH) y después se sigue sacando hasta dejar uno cada SEG_MIN_FLASH segundos,
    # siempre que el encuadre no se aleje más de TOL_MAX_FLASH del ideal.
    objetivo = max(2, int(round((ts[-1] - ts[0]) / SEG_MIN_FLASH)) + 1)
    kf = decimar(puntos, TOL_FLASH, len(puntos))
    kf = decimar(kf, TOL_MAX_FLASH, objetivo)
    return [[t, x, motivo.get(t, "")] for t, x in kf]


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
        if f.shape[1] != W:
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
    a = ap.parse_args()
    parciales = [float(x) for x in a.parciales.split(",") if x.strip()]
    perfil = PERFILES["flash" if a.flash else "normal"]

    d = cargar_o_analizar(a.video, perfil)
    r = calcular(d, parciales, a.partida, a.flash)
    r["ventana"] = VENTANA
    r["escala"] = ESCALA
    r["dividida"] = intervalos_dividida(d["muestras"])
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
