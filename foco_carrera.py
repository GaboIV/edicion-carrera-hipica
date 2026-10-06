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
import sys

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
MODELO = "yolo11s.pt"
IMGSZ = 1280
CONF = 0.25
PERSONA, CABALLO = 0, 17          # clases de COCO
VERSION = 2                       # cambia si cambia lo que se guarda en el análisis

W, H = 1920, 1080                 # el análisis se hace en coordenadas de un cuadro 1920x1080
ESCALA = 1920 / 1080              # escala del video en el proyecto vertical
VENTANA = 1080 / ESCALA           # ancho visible del original (~607 px)
MITAD = VENTANA / 2
PASO_ANALISIS = 3                 # cuadros: histograma y pantalla dividida (~10/s)
PASO_DETECCION = 6                # cuadros: caballos y jinetes (~5/s)

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


# ----------------------------------------------------------------------------
# Lectura del video: un solo recorrido
# ----------------------------------------------------------------------------

def huella(video):
    st = os.stat(video)
    return hashlib.md5(("%s|%d|%d|%s|%d|%d" % (os.path.basename(video), st.st_size, int(st.st_mtime),
                                                MODELO, IMGSZ, VERSION)).encode()).hexdigest()


def analizar(video):
    from ultralytics import YOLO
    modelo = YOLO(os.path.join(AQUI, "modelos", MODELO))
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    previo = previo_hist = None
    mad, muestras, detecciones = [], [], []
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if f.shape[1] != W:
            f = cv2.resize(f, (W, H))
        gris = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        mini = cv2.resize(gris, (64, 36), interpolation=cv2.INTER_AREA).astype(np.int16)
        mad.append(0.0 if previo is None else round(float(np.abs(mini - previo).mean()), 1))
        previo = mini
        t = i / fps
        if i % PASO_ANALISIS == 0:
            hist = cv2.calcHist([gris[::4, ::4]], [0], None, [32], [0, 256])
            cv2.normalize(hist, hist)
            corr = 1.0 if previo_hist is None else cv2.compareHist(previo_hist, hist, cv2.HISTCMP_CORREL)
            previo_hist = hist
            banda = float((gris[Y_BANDA[0]:Y_BANDA[1]:2, ::4] < 45).mean())
            muestras.append({"t": round(t, 3), "hist": round(corr, 3), "banda": round(banda, 3)})
        if i % PASO_DETECCION == 0:
            r = modelo.predict(f, imgsz=IMGSZ, conf=CONF, classes=[PERSONA, CABALLO], verbose=False)[0]
            cajas = [[round(x1), round(y1), round(x2), round(y2), round(c, 2), int(k)]
                     for (x1, y1, x2, y2), c, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(),
                                                       r.boxes.cls.tolist())]
            detecciones.append({"t": round(t, 3), "cajas": cajas})
            if len(detecciones) % 100 == 0:
                print("  analizando video... %d%%" % (100 * i // max(total, 1)), flush=True)
        i += 1
    return {"fps": fps, "duracion": i / fps, "mad": mad, "muestras": muestras, "detecciones": detecciones}


def cargar_o_analizar(video):
    cache = os.path.splitext(video)[0] + ".foco-cache.json"
    h = huella(video)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("huella") == h:
            print("  usando el análisis guardado (%s)" % os.path.basename(cache))
            return d
    print("  analizando el video (la primera vez tarda unos minutos)...")
    d = analizar(video)
    d["huella"] = h
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
    if frontal or sentido == 0:
        return float(np.median([(c[0] + c[2]) / 2 for c in caballos])), "pelotón"
    cabeza = lambda c: c[2] if sentido > 0 else c[0]
    cola = lambda c: c[0] if sentido > 0 else c[2]
    orden = sorted(caballos, key=lambda c: sentido * cabeza(c), reverse=True)
    lider = orden[0]
    centro_lider = (lider[0] + lider[2]) / 2
    if lider[2] - lider[0] > 0.9 * VENTANA:            # no entra con aire delante: centrarlo
        return centro_lider, "primer plano"
    ventaja = sentido * (cabeza(lider) - cabeza(orden[1])) if len(orden) > 1 else 1e9
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


def calcular(d, parciales, partida=0.0):
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
        kf = suavizar(tramo, a)
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


def suavizar(tramo, a):
    """Keyframes [t, x, motivo] de una toma: sin temblores y con velocidad de paneo limitada."""
    ts = [p[0] for p in tramo]
    xs = [p[1] for p in tramo]
    visita = [p[2] == "partió mal" for p in tramo]
    m = list(mediana_movil(xs, 9))
    m = [xs[i] if visita[i] else m[i] for i in range(len(m))]
    for orden in (range(1, len(m)), range(len(m) - 2, -1, -1)):   # hacia adelante y hacia atrás
        for i in orden:
            j = i - 1 if orden.step == 1 else i + 1
            lim = VEL_MAX * (3 if visita[i] or visita[j] else 1) * abs(ts[i] - ts[j])
            m[i] = m[j] + max(-lim, min(lim, m[i] - m[j]))
    puntos = [(round(t, 3), round(limitar(x), 1)) for t, x in zip(ts, m)]
    puntos[0] = (round(a, 3), puntos[0][1])
    motivo = {round(p[0], 3): p[2] for p in tramo}
    return [[t, x, motivo.get(t, "")] for t, x in rdp(puntos, TOLERANCIA)]


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
    a = ap.parse_args()
    parciales = [float(x) for x in a.parciales.split(",") if x.strip()]

    d = cargar_o_analizar(a.video)
    r = calcular(d, parciales, a.partida)
    r["ventana"] = VENTANA
    r["escala"] = ESCALA
    with open(a.salida, "w", encoding="utf-8") as fh:
        json.dump(r, fh, ensure_ascii=False, indent=1)
    print("  foco: %d keyframes, %d cortes de cámara (%s)"
          % (len(r["keyframes"]), len(r["cortes"]), ", ".join("%.1f s" % c for c in r["cortes"])))
    for n in r["notas"]:
        print("  " + n)
    if a.preview:
        vista_previa(a.video, r["keyframes"], a.preview)
        print("  vista previa: %s" % a.preview)


if __name__ == "__main__":
    main()
