# -*- coding: utf-8 -*-
"""
Crea un proyecto nuevo de Camtasia a partir de una plantilla, reemplazando:
  - los banners de tiempos (sin tiempos, 400m, 800m, ..., ganador)
  - el cintillo (gif / mp4)
  - el video de la carrera
Los banners se colocan cuando la gráfica de TIEMPO del video muestra cada parcial.
El banner del tiempo final no se usa: después del penúltimo parcial entra el ganador.
Soporta Camtasia 8.6 (.camproj, XML) y Camtasia 2026 (.tscproj, JSON).
La plantilla NO se modifica: siempre se escribe un proyecto nuevo.

Uso:
  python camtasia_carrera.py --plantilla PLANTILLA [--salida CARPETA] carpeta_carrera [video.mp4]
"""
import argparse
import copy
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from fractions import Fraction

IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm"}
GANADOR = 1000

avisos = []


def aviso(msg):
    avisos.append(msg)
    print("  [!] " + msg)


# ----------------------------------------------------------------------------
# Clasificación de archivos
# ----------------------------------------------------------------------------

def ext_de(p):
    return os.path.splitext(p)[1].lower()


def es_cintillo(p):
    return "cintillo" in os.path.basename(p).lower()


def es_generado(p):
    """Cosas que produce este script (vista previa del foco, proyectos ya armados): no son la carrera."""
    if "vista previa" in os.path.basename(p).lower() or " - foco" in os.path.basename(p).lower():
        return True
    partes = os.path.abspath(p).lower().split(os.sep)
    return any(parte.endswith((".tscproj", ".camproj")) for parte in partes[:-1])


def rango_banner(nombre):
    """0 = sin tiempos, 1..n = parciales, GANADOR = ganador."""
    n = os.path.splitext(os.path.basename(nombre))[0].lower()
    if "ganador" in n:
        return GANADOR
    m = re.search(r"\((\d+)\)\s*$", n)            # Tricolor-Hipico_..._C09 (3).png
    if m:
        return int(m.group(1))
    m = re.search(r"_a\d+_(\d{1,2})(?:_|$)", n)   # C05_A460_03_1200m.png
    if m:
        return int(m.group(1))
    return 0


def nombre_rango(r):
    return "ganador" if r == GANADOR else ("sin tiempos" if r == 0 else "parcial %d" % r)


# ----------------------------------------------------------------------------
# ffprobe / ffmpeg
# ----------------------------------------------------------------------------

_cache_probe = {}


def probe(path):
    if path in _cache_probe:
        return _cache_probe[path]
    cmd = ["ffprobe", "-v", "error"]
    if ext_de(path) == ".gif":
        cmd.append("-count_frames")
    cmd += ["-show_entries",
            "stream=codec_type,width,height,r_frame_rate,nb_frames,nb_read_frames,duration,sample_rate,channels"
            ":format=duration", "-of", "json", path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit("ffprobe no pudo leer %s:\n%s" % (path, r.stderr))
    d = json.loads(r.stdout)
    fmt_dur = float(d.get("format", {}).get("duration") or 0)
    info = {"path": path, "fmt_dur": fmt_dur}
    for s in d.get("streams", []):
        if s["codec_type"] == "video" and "w" not in info:
            frames = s.get("nb_read_frames") or s.get("nb_frames") or 0
            info.update(w=int(s["width"]), h=int(s["height"]),
                        fps=Fraction(s.get("r_frame_rate") or "30/1"),
                        vdur=float(s.get("duration") or fmt_dur),
                        frames=int(frames))
        elif s["codec_type"] == "audio" and "adur" not in info:
            info.update(adur=float(s.get("duration") or fmt_dur),
                        sr=int(s.get("sample_rate") or 44100),
                        ch=int(s.get("channels") or 2))
    mt = os.path.getmtime(path)
    info["lastMod"] = datetime.datetime.fromtimestamp(mt, datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    _cache_probe[path] = info
    return info


def loudness(path):
    """(LUFS integrado, pico lineal) o (None, None)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
                        "-af", "ebur128=peak=sample", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    p = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", r.stderr)
    lufs = float(i[-1]) if i else None
    peak = 10 ** (float(p[-1]) / 20) if p and p[-1] != "-inf" else None
    return lufs, peak


# ----------------------------------------------------------------------------
# Medios nuevos
# ----------------------------------------------------------------------------

def tiene_banners(carpeta):
    return any(ext_de(f) in IMG_EXT and not es_cintillo(f) for f in os.listdir(carpeta)
               if os.path.isfile(os.path.join(carpeta, f)))


def carpeta_con_medios(carpeta):
    """Busca la carpeta con los banners: la misma, o una subcarpeta (Carreras/461-2026/Tricolor-Hipico_.../).
    Se ignoran las carpetas de proyectos .tscproj ya generados."""
    for _ in range(3):
        if tiene_banners(carpeta):
            return carpeta
        subs = [os.path.join(carpeta, f) for f in os.listdir(carpeta)
                if os.path.isdir(os.path.join(carpeta, f)) and not f.lower().endswith(".tscproj")]
        con_banners = [s for s in subs if tiene_banners(s)]
        if len(con_banners) > 1:
            sys.exit("Hay varias carpetas con banners en %s; deja solo una o arrastra la correcta." % carpeta)
        if con_banners:
            return con_banners[0]
        if len(subs) != 1:
            break
        carpeta = subs[0]
    sys.exit("No encontré los banners (imágenes .png) en %s." % carpeta)


class Nuevos:
    def __init__(self, carpeta, video):
        self.carpeta = carpeta
        files = sorted(os.path.join(carpeta, f) for f in os.listdir(carpeta))
        files = [f for f in files if os.path.isfile(f)]
        self.cintillos = [f for f in files if es_cintillo(f) and ext_de(f) in IMG_EXT | VID_EXT]
        self.banners = {}
        for f in files:
            if ext_de(f) in IMG_EXT and not es_cintillo(f):
                r = rango_banner(f)
                if r in self.banners:
                    aviso("Dos banners con el mismo orden (%s): %s y %s. Se usa el primero."
                          % (nombre_rango(r), os.path.basename(self.banners[r]), os.path.basename(f)))
                    continue
                self.banners[r] = f
        # el último parcial es el tiempo final: no se muestra, se salta al ganador
        parciales = [r for r in self.banners if 0 < r < GANADOR]
        self.n_parciales = max(parciales) if parciales else 0
        self.final = self.banners.pop(self.n_parciales) if parciales else None
        self.video = video
        self.usados = set()

    def elegir(self, src_viejo):
        base = os.path.basename(src_viejo)
        ext = ext_de(base)
        if es_cintillo(base):
            # gif con gif, video con video: Camtasia trata distinto cada tipo
            mismo = [c for c in self.cintillos if ext_de(c) == ext] or \
                    [c for c in self.cintillos if (ext_de(c) in VID_EXT) == (ext in VID_EXT)]
            nuevo = mismo[0] if mismo else None
        elif ext in VID_EXT:
            nuevo = self.video
        elif ext in IMG_EXT:
            r = rango_banner(base)
            nuevo = self.banners.get(r)
            if nuevo is None and r != GANADOR:
                # fuente que esta carrera no usa; la pista de banners se arma después según el plan
                menores = [k for k in self.banners if k < r]
                if menores:
                    nuevo = self.banners[max(menores)]
        else:
            nuevo = None
        if nuevo:
            self.usados.add(nuevo)
        return nuevo

    def avisar_sobrantes(self):
        cintillo_usado = any(c in self.usados for c in self.cintillos)
        for f in list(self.banners.values()) + self.cintillos:
            if f not in self.usados and not (cintillo_usado and es_cintillo(f)):
                aviso("Sin lugar en la plantilla (agrégalo a mano si lo necesitas): %s" % os.path.basename(f))


def videos_en(carpetas):
    cand = []
    for d in carpetas:
        if os.path.isdir(d):
            cand += [os.path.join(d, f) for f in os.listdir(d)
                     if ext_de(f) in VID_EXT and not es_cintillo(f) and not es_generado(f)]
    return cand


def buscar_video(rutas, carpetas, numero):
    """Video de la carrera: el que se pasó, o el de la carpeta de la carrera, o el que tenga C<numero> en Descargas.
    Se ignoran las vistas previas y los videos dentro de proyectos ya creados (si no, se analizaría
    la vista previa del foco en vez de la carrera)."""
    for r in rutas:
        if os.path.isfile(r) and ext_de(r) in VID_EXT and not es_cintillo(r) and not es_generado(r):
            return r
    cand = videos_en(carpetas)
    if len(cand) > 1 and numero:
        cand = [c for c in cand if numero in os.path.basename(c)] or cand
    if cand:
        return max(cand, key=os.path.getmtime)
    descargas = os.path.join(os.path.expanduser("~"), "Downloads")
    cand = []
    for d in (os.path.join(descargas, "Video"), descargas):
        if os.path.isdir(d):
            cand += [os.path.join(d, f) for f in os.listdir(d)
                     if ext_de(f) in VID_EXT and not es_cintillo(f) and not es_generado(f)]
    if numero:
        por_numero = [c for c in cand if re.search(r"(^|[^0-9])C?%s([^0-9]|$)" % numero, os.path.basename(c))]
        if por_numero:
            return max(por_numero, key=os.path.getmtime)
    if cand:
        v = max(cand, key=os.path.getmtime)
        aviso("No encontré un video con el número de la carrera; uso el más reciente: %s" % os.path.basename(v))
        return v
    return None


def nombre_proyecto(carpeta, banners):
    numero = None
    for p in banners.values():
        m = re.search(r"_A(\d+)", os.path.basename(p), re.I)
        if m:
            numero = m.group(1)
            break
    m = re.search(r"(20\d\d)-\d\d-\d\d", carpeta)
    anio = m.group(1) if m else str(datetime.date.today().year)
    return numero, ("%s - %s" % (numero, anio) if numero else os.path.basename(carpeta))


def ruta_libre(ruta):
    if not os.path.exists(ruta):
        return ruta
    base, ext = os.path.splitext(ruta)
    i = 2
    while os.path.exists("%s (%d)%s" % (base, i, ext)):
        i += 1
    return "%s (%d)%s" % (base, i, ext)


# ----------------------------------------------------------------------------
# Parciales en el video: la gráfica CARRERA | DISTANCIA | TIEMPO (arriba a la izquierda)
# muestra "400m 23.70", "800m 46.02", ... y el texto cambia en cada parcial.
# ----------------------------------------------------------------------------

ZONA_TIEMPO = (480, 104, 250, 46)   # x, y, ancho, alto del texto del parcial en un cuadro de 1920x1080
FPS_ANALISIS = 10
UMBRAL_CAMBIO = 12                  # diferencia media (0-255) entre cuadros separados 0.3 s
IGNORAR_INICIO = 12.0               # antes de esto la gráfica todavía está entrando
ZONA_ROTULO = (490, 62, 120, 20)    # dentro del rótulo amarillo "TIEMPO": dice si la gráfica está en pantalla
ADELANTO_GANADOR = 1.5              # el ganador entra al cruzar la meta, un poco antes del tiempo final
BLANCO = bytes(1 if v > 200 else 0 for v in range(256))


def cuadros_gris(video, filtro, ancho, alto):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf",
                        "fps=%d,scale=1920:1080,format=gray,%s" % (FPS_ANALISIS, filtro),
                        "-f", "rawvideo", "-"], capture_output=True)
    tam = ancho * alto
    return [r.stdout[i:i + tam] for i in range(0, len(r.stdout) - tam + 1, tam)]


def grafica_en_pantalla(video):
    """Por cada cuadro analizado: ¿se ve el rótulo amarillo "TIEMPO"? (antes de la partida no está)"""
    x, y, w, h = ZONA_ROTULO
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf",
                        "fps=%d,scale=1920:1080,crop=%d:%d:%d:%d,format=rgb24" % (FPS_ANALISIS, w, h, x, y),
                        "-f", "rawvideo", "-"], capture_output=True)
    tam = w * h * 3
    res = []
    for k in range(0, len(r.stdout) - tam + 1, tam):
        f = r.stdout[k:k + tam]
        amarillo = sum(1 for i in range(0, tam, 3) if f[i] > 180 and f[i + 1] > 130 and f[i + 2] < 110)
        res.append(0.35 <= amarillo / (w * h) <= 0.7)
    return res


def detectar_parciales(video, n):
    """(segundos en que la gráfica empieza a mostrar cada parcial —los n, incluido el final— o None,
    segundo en que aparece la gráfica, que es más o menos la partida)"""
    x, y, w, h = ZONA_TIEMPO
    recorte = "crop=%d:%d:%d:%d" % (w, h, x, y)
    bw, bh = w // 5, h // 5
    texto = cuadros_gris(video, recorte, w, h)
    bloques = cuadros_gris(video, recorte + ",scale=%d:%d:flags=area" % (bw, bh), bw, bh)
    visible = grafica_en_pantalla(video)
    N = min(len(texto), len(bloques), len(visible))
    # la gráfica "aparece" cuando se ve 2 s seguidos
    aparicion = next((i / FPS_ANALISIS for i in range(N - 20) if all(visible[i:i + 20])), None)
    eventos = []
    i = 5
    while i < N:
        d = sum(abs(a - b) for a, b in zip(bloques[i], bloques[i - 3])) / (bw * bh)
        # imágenes de la previa y la entrada de la gráfica no cuentan
        con_grafica = aparicion is not None and i / FPS_ANALISIS > aparicion + 1 and             visible[i - 5] and visible[min(i + 15, N - 1)]
        if d > UMBRAL_CAMBIO and con_grafica and i / FPS_ANALISIS >= IGNORAR_INICIO:
            # cuando la animación termina tiene que quedar texto (no la gráfica saliendo de pantalla)
            blancos = texto[min(i + 15, N - 1)].translate(BLANCO).count(1)
            if 200 <= blancos <= 2200:
                eventos.append(i / FPS_ANALISIS)
            i += 15   # una misma animación dura menos de 1.5 s
        i += 1
    print("Parciales en el video: " + (", ".join("%.1f s" % t for t in eventos) or "ninguno"))
    # partida: cuando sale la gráfica o ~24 s antes del primer parcial (400m), lo que pase primero
    partida = min([t for t in (aparicion, eventos[0] - 24.0 if eventos else None) if t is not None] or [0.0])
    print("Partida: ~%.1f s" % max(partida, 0.0))
    return (eventos[:n] if len(eventos) >= n else None), max(partida, 0.0)


def parciales_de(nuevos):
    """detectar_parciales una sola vez por carrera (lo usan los banners, el cintillo y el foco)."""
    datos_carrera(nuevos)
    return nuevos.eventos


# ----------------------------------------------------------------------------
# Lo que se detecta en el video (parciales, partida y pantalla dividida) se guarda al lado del
# video: leerlo cuesta decodificarlo entero un par de veces, y son datos que no cambian.
# ----------------------------------------------------------------------------

VERSION_CARRERA = 1
ZONA_DIV = (0, 578, 1920, 44)       # franja negra "SEXTA CARRERA ... INHIP" de la pantalla dividida
UMBRAL_DIV = 0.4                    # fracción de píxeles negros en la franja
DIV_VENTANA = 11                    # muestras de la mediana (a 10 por segundo: ~1 s)
DIV_MIN = 3.0                       # s: un tramo más corto que esto es un falso positivo
DIV_HUECO = 2.0                     # s: dos tramos separados por menos que esto son uno solo
DIV_MARGEN = 0.3                    # s: el cintillo entra un poco antes y sale un poco después


def cache_carrera(video):
    return os.path.splitext(video)[0] + ".carrera-cache.json"


def identidad_video(video):
    st = os.stat(video)
    return {"nombre": os.path.basename(video), "bytes": st.st_size, "mtime": int(st.st_mtime)}


def guardar_cache(ruta, d):
    try:
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
    except OSError:
        pass          # si la carpeta es de solo lectura se vuelve a calcular la próxima vez


def mediana(v):
    s = sorted(v)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def tramos_de_banda(t, suave):
    """[[inicio, fin]] a partir de la serie (tiempos, ¿hay franja negra?) ya suavizada."""
    tramos, ini = [], None
    for i, hay in enumerate(suave):
        if hay and ini is None:
            ini = t[i]
        elif not hay and ini is not None:
            tramos.append([ini, t[i]])
            ini = None
    if ini is not None:
        tramos.append([ini, t[-1]])
    unidos = []
    for a, f in tramos:
        if unidos and a - unidos[-1][1] < DIV_HUECO:
            unidos[-1][1] = f
        else:
            unidos.append([a, f])
    return [[round(max(a - DIV_MARGEN, 0.0), 2), round(f + DIV_MARGEN, 2)]
            for a, f in unidos if f - a >= DIV_MIN]


def dividida_de_foco_cache(video):
    """Si ya hay un análisis de foco guardado, la pantalla dividida sale de ahí (sin decodificar el video)."""
    ruta = os.path.splitext(video)[0] + ".foco-cache.json"
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if d.get("identidad") != identidad_video(video) and not d.get("huella"):
        return None
    muestras = d.get("muestras") or []
    if len(muestras) < DIV_VENTANA:
        return None
    mitad = DIV_VENTANA // 2
    banda = [m["banda"] for m in muestras]
    suave = [mediana(banda[max(0, i - mitad):i + mitad + 1]) > UMBRAL_DIV for i in range(len(banda))]
    return tramos_de_banda([m["t"] for m in muestras], suave)


def detectar_dividida(video):
    """[[inicio, fin]] en segundos de los tramos con la pantalla dividida en dos (la franja negra
    donde abajo se ve al narrador). Mismo criterio que foco_carrera.intervalos_dividida."""
    x, y, w, h = ZONA_DIV
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf",
                        "fps=%d,scale=1920:1080,crop=%d:%d:%d:%d,format=gray" % (FPS_ANALISIS, w, h, x, y),
                        "-f", "rawvideo", "-"], capture_output=True)
    tam = w * h
    banda = []
    for k in range(0, len(r.stdout) - tam + 1, tam):
        f = r.stdout[k:k + tam]
        negros = total = 0
        for fila in range(0, h, 8):
            base = fila * w
            for col in range(0, w, 16):
                total += 1
                if f[base + col] < 45:
                    negros += 1
        banda.append(negros / max(total, 1))
    n = len(banda)
    mitad = DIV_VENTANA // 2
    suave = [mediana(banda[max(0, i - mitad):i + mitad + 1]) > UMBRAL_DIV for i in range(n)]
    return tramos_de_banda([i / FPS_ANALISIS for i in range(n)], suave)


def datos_carrera(nuevos):
    """Parciales, partida y pantalla dividida del video, calculados una sola vez (con cache)."""
    if hasattr(nuevos, "datos"):
        return nuevos.datos
    ruta = cache_carrera(nuevos.video)
    ident = identidad_video(nuevos.video)
    d = None
    if os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = None
        if d and (d.get("identidad") != ident or d.get("version") != VERSION_CARRERA):
            d = None
    if d is None:
        if nuevos.n_parciales:
            eventos, partida = detectar_parciales(nuevos.video, nuevos.n_parciales)
        else:
            eventos, partida = None, None
        d = {"identidad": ident, "version": VERSION_CARRERA, "parciales": eventos,
             "partida": partida, "dividida": None}
        guardar_cache(ruta, d)
    else:
        print("Parciales guardados: " + (", ".join("%.1f s" % t for t in d["parciales"])
                                         if d.get("parciales") else "ninguno"))
    nuevos.datos = d
    nuevos.eventos, nuevos.partida = d.get("parciales"), d.get("partida")
    return d


def dividida_de(nuevos, foco=None):
    """[[inicio, fin]] de los tramos de pantalla dividida (donde va el cintillo)."""
    if foco and foco.get("dividida") is not None:
        return foco["dividida"]
    d = datos_carrera(nuevos)
    if d.get("dividida") is None:
        tramos = dividida_de_foco_cache(nuevos.video)
        if tramos is None:
            print("Buscando los tramos de pantalla dividida en el video...")
            tramos = detectar_dividida(nuevos.video)
        d["dividida"] = tramos
        guardar_cache(cache_carrera(nuevos.video), d)
    return d["dividida"]


def plan_banners(nuevos, fin_video, inicios_plantilla, k):
    """[(rango, inicio, fin)] en segundos. inicios_plantilla: dónde empiezan los banners en la plantilla;
    k: duración del video nuevo / la de la plantilla (para el respaldo proporcional)."""
    parciales = sorted(r for r in nuevos.banners if 0 < r < GANADOR)
    n = nuevos.n_parciales
    eventos = parciales_de(nuevos)
    tiempos = {0: inicios_plantilla[0] * k}
    if eventos:
        # "sin tiempos" entra con la misma anticipación al primer parcial que en la plantilla
        if len(inicios_plantilla) > 2:
            tiempos[0] = max(eventos[0] - (inicios_plantilla[1] - inicios_plantilla[0]), 0.0)
        tiempos.update((r, eventos[r - 1]) for r in parciales)
        tiempos[GANADOR] = eventos[n - 1] - ADELANTO_GANADOR
    else:
        aviso("No pude leer los %d parciales en la gráfica del video; los banners quedan en tiempos "
              "aproximados, ajústalos a mano." % n)
        a = (inicios_plantilla[1] if len(inicios_plantilla) > 2 else inicios_plantilla[0]) * k
        b = inicios_plantilla[-1] * k
        tiempos.update((r, a + (b - a) * i / max(len(parciales), 1)) for i, r in enumerate(parciales))
        tiempos[GANADOR] = b
    orden = [r for r in sorted(nuevos.banners) if r in tiempos]
    plan = []
    for i, r in enumerate(orden):
        fin = tiempos[orden[i + 1]] if i + 1 < len(orden) else fin_video
        if fin - tiempos[r] < 1:
            aviso("El banner '%s' queda con menos de 1 s (%.1f s); revísalo." % (nombre_rango(r), tiempos[r]))
        plan.append((r, tiempos[r], fin))
    if nuevos.final:
        print("Tiempo final omitido (entra el ganador): %s" % os.path.basename(nuevos.final))
    print("Banners: " + ", ".join("%s %.1f s" % (nombre_rango(r), ini) for r, ini, _ in plan))
    return plan


# ----------------------------------------------------------------------------
# Foco automático: foco_carrera.py corre en un Python 3.12 propio, dentro de .python-vision
# (con YOLO); no depende de nada instalado fuera de esta carpeta
# ----------------------------------------------------------------------------

AQUI = os.path.dirname(os.path.abspath(__file__))
PYTHON_VISION = os.path.join(AQUI, ".python-vision", "cpython-3.12.15-windows-x86_64-none", "python.exe")


def calcular_foco(nuevos, carpeta_salida, nombre, vista_previa, flash=False):
    """Datos del foco: keyframes [segundo del video, x del centro en un cuadro de 1920, motivo] y
    los tramos de pantalla dividida; o None si no se pudo calcular."""
    if not os.path.exists(PYTHON_VISION):
        aviso("No está el Python de visión (.python-vision); el foco queda como en la plantilla.")
        return None
    print("\nFoco automático%s:" % (" (flash)" if flash else ""))
    eventos = parciales_de(nuevos) or []
    salida = os.path.join(carpeta_salida, nombre + " - foco.json")
    cmd = [PYTHON_VISION, os.path.join(AQUI, "foco_carrera.py"), nuevos.video, "--salida", salida,
           "--parciales", ",".join("%.2f" % t for t in eventos)]
    if getattr(nuevos, "partida", None) is not None:
        cmd += ["--partida", "%.2f" % nuevos.partida]
    if flash:
        cmd += ["--flash"]
    if vista_previa:
        cmd += ["--preview", os.path.join(carpeta_salida, nombre + " - vista previa del foco.mp4")]
    sys.stdout.flush()
    if subprocess.run(cmd).returncode:
        aviso("El foco automático falló; el foco queda como en la plantilla.")
        return None
    with open(salida, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# Camtasia 2026 (.tscproj JSON)
# ----------------------------------------------------------------------------

def fps_2026(fps):
    if fps.denominator == 1:
        return fps.numerator
    return "%d/100" % round(float(fps) * 100)


def escalar_param(params, nombre, factor):
    v = params.get(nombre)
    if isinstance(v, dict):
        v["defaultValue"] = v.get("defaultValue", 1.0) * factor
        for k in v.get("keyframes", []):
            k["value"] = k["value"] * factor
    elif isinstance(v, (int, float)):
        params[nombre] = v * factor


def valor_param(params, nombre, defecto=0.0):
    v = params.get(nombre, defecto)
    return v.get("defaultValue", defecto) if isinstance(v, dict) else v


def procesar_2026(plantilla, nuevos, salida_dir, nombre, limpiar_foco, foco=None):
    with open(plantilla, encoding="utf-8-sig") as f:
        p = json.load(f)
    dir_plantilla = os.path.dirname(os.path.abspath(plantilla))
    ER = p["editRate"]
    FPS = int(p.get("videoFormatFrameRate", 30))
    FRAME = ER // FPS
    W, H = p.get("width", 1080), p.get("height", 1920)

    def unidades(segundos):  # duración en unidades de la línea de tiempo, redondeada al cuadro
        return int(math.floor(segundos * FPS + 1e-6)) * FRAME

    bundle = ruta_libre(os.path.join(salida_dir, nombre + ".tscproj"))
    os.makedirs(bundle)
    salida = os.path.join(bundle, os.path.basename(bundle))

    fuentes = {}   # id -> datos
    carrera = None
    print("\nReemplazos:")
    for s in p["sourceBin"]:
        viejo = s["src"]
        nuevo = nuevos.elegir(viejo)
        old_len = None
        for t in s["sourceTracks"]:
            if t["type"] == 0:
                old_len = unidades(t["range"][1] / t["editRate"])
        if not nuevo:
            aviso("Sin reemplazo para: %s (se copia el original si existe)" % viejo)
            orig = viejo if os.path.isabs(viejo) else os.path.join(dir_plantilla, viejo)
            if not os.path.isabs(viejo) and os.path.exists(orig):
                shutil.copy2(orig, bundle)
            continue
        print("  %-60s -> %s" % (os.path.basename(viejo)[:60], os.path.basename(nuevo)))
        destino = os.path.join(bundle, os.path.basename(nuevo))
        if not os.path.exists(destino):
            shutil.copy2(nuevo, destino)
        info = probe(nuevo)
        es_gif = ext_de(nuevo) == ".gif"
        old_w, old_h = s["rect"][2], s["rect"][3]
        base = os.path.basename(nuevo)
        s["src"] = base
        s["rect"] = [0, 0, info["w"], info["h"]]
        s["lastMod"] = info["lastMod"]
        s.setdefault("metadata", {})["timeAdded"] = \
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
        new_len = None
        for t in s["sourceTracks"]:
            t["metaData"] = base + ";"
            if t["type"] == 0:
                t["trackRect"] = [0, 0, info["w"], info["h"]]
                if es_gif:
                    t["range"] = [0, info.get("frames") or t["range"][1]]
                else:
                    t["editRate"] = 1000
                    t["range"] = [0, int(math.ceil(info["vdur"] * 1000))]
                    t["sampleRate"] = fps_2026(info["fps"])
                new_len = unidades(t["range"][1] / t["editRate"])
            elif t["type"] == 1:
                t["trackRect"] = [0, 0, info["w"], info["h"]]
            elif t["type"] == 2:
                if "adur" not in info:
                    aviso("El video nuevo no tiene audio: %s" % base)
                    continue
                t["range"] = [0, int(math.ceil(info["adur"] * 1000))]
                t["sampleRate"] = info["sr"]
                t["numChannels"] = info["ch"]
                lufs, peak = loudness(nuevo)
                if lufs is not None:
                    t["integratedLUFS"] = lufs
                if peak is not None:
                    t["peakLevel"] = peak
        datos = {"base": os.path.splitext(base)[0], "old_len": old_len, "new_len": new_len,
                 "w": info["w"], "h": info["h"],
                 "factor": old_w / info["w"] if old_w != info["w"] else 1.0,
                 "cambio_tam": (old_w, old_h) != (info["w"], info["h"]),
                 "imagen": all(t["type"] == 1 for t in s["sourceTracks"]),
                 # cuánto material hay: un clip nunca puede pedir más que esto (los gif no se repiten solos)
                 "len_fuente": next((unidades(t["range"][1] / t["editRate"]) for t in s["sourceTracks"]
                                     if t["type"] == 0 and t.get("editRate")), None),
                 "carrera": nuevo == nuevos.video, "archivo": nuevo,
                 "banner": nuevo in nuevos.banners.values()}
        fuentes[s["id"]] = datos
        if datos["carrera"]:
            carrera = datos

    OLD_END = carrera["old_len"] if carrera else None
    NEW_END = carrera["new_len"] if carrera else None
    if carrera:
        print("\nDuración del video: plantilla %.2f s -> nuevo %.2f s" % (OLD_END / ER, NEW_END / ER))

    def src_de(m):
        if m.get("_type") == "UnifiedMedia":
            return (m.get("video") or m.get("audio") or {}).get("src")
        return m.get("src")

    def partes(m):
        if m.get("_type") == "UnifiedMedia":
            return [m] + [m[k] for k in ("video", "audio") if k in m]
        return [m]

    def fijar_duracion(m, d, imagen):
        for x in partes(m):
            x["duration"] = d
            if not imagen:
                x["mediaDuration"] = d

    def cuadro(u):
        return int(round(u / FRAME)) * FRAME

    def aplicar_foco(x, fuente):
        """Reemplaza el foco de la plantilla por el paneo calculado: una animación entre cada par
        de keyframes (lineal, para que el paneo no frene en cada uno); en los cortes, salto de 1 cuadro."""
        par = x.setdefault("parameters", {})
        for v in par.values():
            if isinstance(v, dict) and v.get("keyframes"):
                v["defaultValue"] = v["keyframes"][0]["value"]
                del v["keyframes"]
        escala = valor_param(par, "scale0", 1.0) * fuente["w"] / 1920.0
        tr = lambda cx: (960.0 - cx) * escala
        desde = x.get("mediaStart", 0)
        kf = [(cuadro(t * ER - desde), cx, motivo) for t, cx, motivo in foco["keyframes"]]
        kf = [k for k in kf if 0 <= k[0] <= x["duration"]]
        if not kf:
            return
        anims = []
        for (t0, x0, _), (t1, x1, motivo) in zip(kf, kf[1:]):
            if motivo == "corte":
                t0 = t1 - FRAME
            if t1 > t0 and abs(x1 - x0) >= 0.5:
                anims.append({"endTime": t1, "time": t0, "value": tr(x1), "duration": t1 - t0})
        t0 = par.get("translation0")
        if not isinstance(t0, dict):
            t0 = par["translation0"] = {"type": "double", "defaultValue": t0 or 0.0}
        t0["defaultValue"] = tr(kf[0][1])
        t0["interp"] = "linr"
        t0["keyframes"] = anims
        x["animationTracks"] = {"visual": [{"endTime": a["endTime"], "duration": a["duration"]} for a in anims]}
        print("Foco: %d movimientos de cámara en el clip de la carrera." % len(anims))

    def ajustar_visual(x, fuente):
        if x.get("attributes", {}).get("ident"):
            x["attributes"]["ident"] = fuente["base"]
        f = fuente["factor"]
        if f != 1.0 and "parameters" in x:
            escalar_param(x["parameters"], "scale0", f)
            escalar_param(x["parameters"], "scale1", f)
            for c in ("geometryCrop0", "geometryCrop1", "geometryCrop2", "geometryCrop3"):
                escalar_param(x["parameters"], c, 1.0 / f)
        md = x.get("metadata", {})
        if fuente["cambio_tam"] and "default-width" in md and "default-height" in md:
            md["default-height"]["value"] = md["default-width"]["value"] * fuente["h"] / fuente["w"]
        principal = not any(valor_param(x.get("parameters", {}), "geometryCrop%d" % i) for i in range(4))
        if foco and foco.get("keyframes") and fuente["carrera"] and x.get("_type") == "VMFile" and principal:
            aplicar_foco(x, fuente)
        elif limpiar_foco and fuente["carrera"] and x.get("_type") == "VMFile":
            for v in x.get("parameters", {}).values():
                if isinstance(v, dict) and v.get("keyframes"):
                    v["defaultValue"] = v["keyframes"][0]["value"]
                    del v["keyframes"]
            x["animationTracks"] = {}

    def recorrer(medias, nivel_superior):
        for m in medias:
            if m.get("_type") == "Group":
                for tr in m.get("tracks", []):
                    recorrer(tr.get("medias", []), False)
                continue
            fuente = fuentes.get(src_de(m))
            if not fuente:
                continue
            for x in partes(m):
                if x.get("_type") in ("VMFile", "IMFile", "ScreenVMFile"):
                    ajustar_visual(x, fuente)
            if not nivel_superior or m.get("scalar", 1) not in (1, "1"):
                continue
            ini, dur = m["start"], m["duration"]
            # 1) el clip de la carrera que terminaba al final del video viejo, termina al final del nuevo
            if fuente["carrera"] and OLD_END != NEW_END and abs(ini + dur - OLD_END) <= FRAME:
                nueva = NEW_END - ini
                if nueva >= ER:
                    fijar_duracion(m, nueva, fuente["imagen"])
                    dur = nueva
                else:
                    aviso("Un clip empieza después del final del video nuevo (%.1f s); revísalo." % (ini / ER))
            # 2) un clip de video no puede pedir más material del que tiene el archivo nuevo
            if not fuente["imagen"] and fuente["new_len"]:
                disponible = fuente["new_len"] - m.get("mediaStart", 0)
                if disponible <= 0:
                    aviso("Un clip de %s empieza más allá del final del archivo nuevo (%.1f s)."
                          % (fuente["base"], ini / ER))
                elif dur > disponible:
                    fijar_duracion(m, disponible, False)
                    aviso("Se recortó un clip de %s a %.1f s porque el archivo nuevo es más corto."
                          % (fuente["base"], disponible / ER))

    def repartir(medias):
        """Reubica los clips en proporción a la duración del video nuevo; el último sigue terminando al final."""
        k = NEW_END / OLD_END
        fin_viejo = fin_nuevo = 0
        for m in sorted(medias, key=lambda m: m["start"]):
            ini, dur = m["start"], m["duration"]
            nuevo_ini = fin_nuevo + cuadro(max(ini - fin_viejo, 0) * k)
            fuente = fuentes.get(src_de(m))
            es_video = m.get("_type") in ("VMFile", "AMFile", "UnifiedMedia", "ScreenVMFile")
            imagen = fuente["imagen"] if fuente else not es_video
            if m.get("_type") == "Group":
                nueva_dur = dur
            elif abs(ini + dur - OLD_END) <= FRAME:
                nueva_dur = NEW_END - nuevo_ini
            else:
                nueva_dur = cuadro(dur * k)
                if fuente and not imagen and fuente["new_len"]:
                    nueva_dur = min(nueva_dur, fuente["new_len"] - m.get("mediaStart", 0))
                elif not fuente and es_video:
                    nueva_dur = min(nueva_dur, dur)   # video no reemplazado: no pedirle más material
            for x in partes(m):
                x["start"] = nuevo_ini
            if m.get("_type") != "Group":
                fijar_duracion(m, max(nueva_dur, FRAME), imagen)
            fin_viejo, fin_nuevo = ini + dur, nuevo_ini + m["duration"]

    ids = [0]

    def max_id(o):
        if isinstance(o, dict):
            if isinstance(o.get("id"), int):
                ids[0] = max(ids[0], o["id"])
            for v in o.values():
                max_id(v)
        elif isinstance(o, list):
            for v in o:
                max_id(v)
    max_id(p)

    def armar_banners(tr):
        """La pista de banners se arma de nuevo: un clip por banner del plan, en los tiempos del video."""
        clips = sorted(tr["medias"], key=lambda m: m["start"])
        k = NEW_END / OLD_END if carrera else 1
        plan = plan_banners(nuevos, (NEW_END or clips[-1]["start"] + clips[-1]["duration"]) / ER,
                            [m["start"] / ER for m in clips], k)
        src_de_archivo = {}
        for sid, f in fuentes.items():
            src_de_archivo.setdefault(f["archivo"], sid)
        libres, ganador = clips[:-1], clips[-1]   # en la plantilla el ganador es el último
        armados = []
        for rango, ini, fin in plan:
            sid = src_de_archivo.get(nuevos.banners[rango])
            if sid is None:
                aviso("La plantilla no tiene dónde poner '%s'; agrégalo a mano." % nombre_rango(rango))
                continue
            if rango == GANADOR:
                m = ganador
            elif libres:
                m = libres.pop(0)
            else:   # carrera con más parciales que la plantilla
                m = copy.deepcopy(armados[-1] if armados else ganador)
                ids[0] += 1
                m["id"] = ids[0]
            m["src"] = sid
            m.setdefault("attributes", {})["ident"] = fuentes[sid]["base"]
            m["start"] = unidades(ini)
            m["duration"] = max(unidades(fin) - m["start"], FRAME)
            armados.append(m)
        if not armados:
            return
        armados[-1]["duration"] = (NEW_END or armados[-1]["start"] + armados[-1]["duration"]) - armados[-1]["start"]
        trans = tr.get("transitions", [])
        entrada = next((t for t in trans if "leftMedia" not in t), None)
        salida = next((t for t in trans if "rightMedia" not in t), None)
        medio = next((t for t in trans if "leftMedia" in t and "rightMedia" in t), None)
        nuevas = []
        if entrada:
            nuevas.append(dict(copy.deepcopy(entrada), rightMedia=armados[0]["id"]))
        if medio:
            for a, b in zip(armados, armados[1:]):
                nuevas.append(dict(copy.deepcopy(medio), leftMedia=a["id"], rightMedia=b["id"]))
        if salida:
            nuevas.append(dict(copy.deepcopy(salida), leftMedia=armados[-1]["id"]))
        tr["medias"] = armados
        if "transitions" in tr:
            tr["transitions"] = nuevas

    pistas = p["timeline"]["sceneTrack"]["scenes"][0]["csml"]["tracks"]
    es_banners = lambda tr: tr["medias"] and all(
        m.get("_type") == "IMFile" and fuentes.get(m.get("src"), {}).get("banner") for m in tr["medias"])
    inicios = sorted(m["start"] for tr in pistas if es_banners(tr) for m in tr["medias"])
    primer_parcial = inicios[1] if len(inicios) > 2 else None   # en la plantilla

    def anclar(medias):
        """Mueve la pista entera para que quede a la misma distancia del primer parcial que en la plantilla."""
        eventos = parciales_de(nuevos)
        if not eventos or primer_parcial is None:
            return False
        off = cuadro(eventos[0] * ER - primer_parcial)
        if min(m["start"] for m in medias) + off < 0 or                 max(m["start"] + m["duration"] for m in medias) + off > NEW_END:
            return False
        for m in medias:
            nuevo = m["start"] + off
            for x in partes(m):
                x["start"] = nuevo
        return True

    como = None
    cintillos = set(nuevos.cintillos)
    tramos_div = dividida_de(nuevos, foco) if cintillos else []

    def es_cintillo_track(tr):
        return bool(tr["medias"]) and all(
            fuentes.get(src_de(m), {}).get("archivo") in cintillos for m in tr["medias"])

    def armar_cintillo(tr, tramos):
        """Un tramo de cintillo por cada tramo de pantalla dividida: entra cuando la pantalla se parte
        en dos (abajo se ve al narrador) y sale cuando vuelve a pantalla completa.

        El gif no se repite solo, así que un tramo más largo que el gif se cubre con varios clips
        pegados (como hace la plantilla), cada uno con material suficiente."""
        clips = sorted(tr["medias"], key=lambda m: m["start"])
        modelo = clips[0]
        tope = max(fuentes.get(modelo.get("src"), {}).get("len_fuente") or FRAME, FRAME)
        trans = tr.get("transitions") or []
        entrada = next((t for t in trans if "leftMedia" not in t), None)
        salida = next((t for t in trans if "rightMedia" not in t), None)
        armados = []            # [[clips del tramo 1], [clips del tramo 2], ...]
        for ini, fin in tramos:
            ini_u = unidades(desfase + ini)
            fin_u = unidades(min(desfase + fin, fin_carrera))
            if fin_u - ini_u < FRAME:
                continue
            trozos = max(1, int(math.ceil((fin_u - ini_u) / tope)))
            paso = min(cuadro(int(math.ceil((fin_u - ini_u) / trozos))), tope)
            grupo = []
            for k in range(trozos):
                m = copy.deepcopy(modelo)
                if armados or grupo:
                    ids[0] += 1
                    m["id"] = ids[0]
                m["start"] = ini_u + k * paso
                m["duration"] = max(min(paso, fin_u - m["start"]), FRAME)
                m["mediaDuration"] = m["duration"]     # el clip no pide más que el gif
                grupo.append(m)
            armados.append(grupo)
        if not armados:
            return
        tr["medias"] = [m for grupo in armados for m in grupo]
        if "transitions" in tr:
            nuevas = []
            for grupo in armados:       # fundido al entrar y al salir; adentro los clips van pegados
                if entrada:
                    nuevas.append(dict(copy.deepcopy(entrada), rightMedia=grupo[0]["id"]))
                if salida:
                    nuevas.append(dict(copy.deepcopy(salida), leftMedia=grupo[-1]["id"]))
            tr["transitions"] = nuevas
        print("Cintillo: %d tramo(s) de pantalla dividida (%s), %d clip(s) de %.1f s máximo."
              % (len(armados), ", ".join("%.1f-%.1f s" % (a, b) for a, b in tramos),
                 len(tr["medias"]), tope / ER))

    # el cintillo se ubica en la línea de tiempo con el mismo desfase que el video de la carrera
    desfase, fin_carrera = None, None
    for tr in pistas:
        for m in tr["medias"]:
            f = fuentes.get(src_de(m))
            if f and f["carrera"] and m.get("start") is not None:
                off = (m["start"] - m.get("mediaStart", 0)) / ER
                desfase = off if desfase is None else min(desfase, off)
                fin = (m["start"] + m["duration"]) / ER
                fin_carrera = fin if fin_carrera is None else max(fin_carrera, fin)
    desfase = desfase or 0.0
    if fin_carrera is None:
        fin_carrera = float("inf")

    for tr in pistas:
        recorrer(tr["medias"], True)
        con_carrera = any(fuentes.get(src_de(m), {}).get("carrera") for m in tr["medias"])
        if es_banners(tr):
            armar_banners(tr)
        elif es_cintillo_track(tr) and tramos_div:
            armar_cintillo(tr, tramos_div)
        elif carrera and not con_carrera and tr["medias"]:
            if anclar(tr["medias"]):
                como = "con el primer parcial, como en la plantilla"
            elif OLD_END != NEW_END:
                repartir(tr["medias"])
                como = "en proporción (x%.2f)" % (NEW_END / OLD_END)
    if como:
        print("Cintillo reubicado %s." % como)
    if cintillos and not tramos_div:
        aviso("No pude detectar tramos de pantalla dividida: el cintillo queda como en la plantilla.")
    if carrera and NEW_END < OLD_END and not limpiar_foco and not foco:
        aviso("El video nuevo es más corto: los focos de la plantilla después de %.1f s ya no aplican."
              % (NEW_END / ER))

    def revisar_proyecto():
        """Chequeos baratos antes de guardar. El importante: un clip no puede pedir más material del
        que tiene su archivo (Camtasia se niega a abrir el proyecto entero si eso pasa)."""
        problemas, vistos = [], set()

        def duracion(x, donde):
            d = x.get("duration") or 0
            if d <= 0:
                problemas.append("%s: duración 0" % donde)
                return
            f = fuentes.get(x.get("src"))
            if f and f.get("len_fuente"):
                usado = (x.get("mediaStart") or 0) + (x.get("mediaDuration") or d)
                if usado > f["len_fuente"] + FRAME:
                    problemas.append("%s: pide %.1f s y el archivo tiene %.1f s"
                                     % (donde, usado / ER, f["len_fuente"] / ER))

        def recorrer(medias):
            for m in medias:
                if m.get("_type") == "Group":
                    for t in m.get("tracks", []):
                        recorrer(t.get("medias", []))
                    continue
                for x in partes(m):
                    if x.get("id") in vistos:
                        problemas.append("id repetido: %s" % x.get("id"))
                    vistos.add(x.get("id"))
                    if x.get("_type") != "UnifiedMedia":
                        duracion(x, "clip %s (%s)" % (x.get("id"), x.get("_type")))

        for tr in pistas:
            recorrer(tr["medias"])
            en_pista = {m.get("id") for m in tr["medias"] if m.get("_type") != "UnifiedMedia"}
            for x in (tr.get("transitions") or []):
                for lado in ("leftMedia", "rightMedia"):
                    if lado in x and x[lado] not in en_pista:
                        problemas.append("una transición apunta a un clip que no está en su pista (%s)"
                                         % x[lado])
        for pr in problemas:
            aviso(pr)
        return problemas

    revisar_proyecto()
    meta = p.setdefault("metadata", {})
    meta["Title"] = nombre
    meta["AutoSaveFile"] = ""
    with open(salida, "w", encoding="utf-8", newline="\r\n") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
    return salida


# ----------------------------------------------------------------------------
# Camtasia 8.6 (.camproj XML) — se edita el texto para no alterar el formato
# ----------------------------------------------------------------------------

def xml_attr(v):
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def poner_attr(etiqueta, nombre, valor):
    return re.sub(r'\b%s="[^"]*"' % nombre, lambda _: '%s="%s"' % (nombre, valor), etiqueta, count=1)


def multiplicar_vector(bloque, nombre, factores):
    """Multiplica los valores de <VectorParam name=nombre>: factores[i] para la componente i."""
    m = re.search(r'<VectorParam id="\d+"\s+name="%s">(.*?)</VectorParam>' % nombre, bloque, re.S)
    if not m:
        return bloque
    interior = m.group(1)
    comps = list(re.finditer(r"<InterpolatingParam\b[^>]*?(?:/>|>.*?</InterpolatingParam>)", interior, re.S))
    nuevo = interior
    for i in reversed(range(min(len(comps), len(factores)))):
        c = comps[i]
        f = factores[i]
        txt = re.sub(r'\bvalue="([^"]*)"',
                     lambda mm: mm.group(0) if float(mm.group(1)) == 0 else 'value="%r"' % (float(mm.group(1)) * f),
                     c.group(0))
        nuevo = nuevo[:c.start()] + txt + nuevo[c.end():]
    return bloque[:m.start(1)] + nuevo + bloque[m.end(1):]


def frac(s):
    return Fraction(s) if s else Fraction(0)


def armar_banners_86(txt, pista_id, plan, fuentes, nuevos, FPS, fin_video):
    """La pista de banners de 8.6 es [transición] banner [transición] banner ... [transición]:
    cada transición (AudioTransition + ImageTransition) ocupa su propio espacio entre dos banners.
    Se arma de nuevo con un banner por paso del plan; la transición queda centrada en el cambio."""
    m = re.search(r'<GenericTrack id="%s"[^>]*>.*?</GenericTrack>' % pista_id, txt, re.S)
    pista = m.group(0)
    i, j = pista.index("<Medias>") + len("<Medias>"), pista.rindex("</Medias>")
    interior = pista[i:j]
    items = list(re.finditer(r'<(AudioTransition|ImageTransition|IMFile) id="\d+".*?</\1>', interior, re.S))
    secuencia = []   # ["t", [bloques]] o ["c", bloque]
    for it in items:
        if it.group(1) == "IMFile":
            secuencia.append(["c", it.group(0)])
        elif secuencia and secuencia[-1][0] == "t":
            secuencia[-1][1].append(it.group(0))
        else:
            secuencia.append(["t", [it.group(0)]])
    if len(items) < 2 or not re.fullmatch(r"t(ct)+", "".join(s[0] for s in secuencia)):
        aviso("La pista de banners de la plantilla no tiene la forma esperada; quedó como en la plantilla.")
        return txt
    sep = interior[items[0].end():items[1].start()]
    pares = [s[1] for s in secuencia if s[0] == "t"]
    clips = [s[1] for s in secuencia if s[0] == "c"]
    TR = int(re.search(r'duration="(\d+)"', pares[0][0]).group(1))
    h = TR // 2

    ultimo_id = [max(int(x) for x in re.findall(r'\bid="(\d+)"', txt))]

    def renumerar(b):
        def otro(_):
            ultimo_id[0] += 1
            return 'id="%d"' % ultimo_id[0]
        return re.sub(r'\bid="\d+"', otro, b)

    def poner(b, **attrs):
        cab = re.match(r"<[^>]*>", b).group(0)
        cab2 = cab
        for k, v in attrs.items():
            cab2 = poner_attr(cab2, k, str(v))
        return cab2 + b[len(cab):]

    src_de_archivo = {}
    for sid, f in fuentes.items():
        src_de_archivo.setdefault(f["archivo"], sid)
    pasos = []
    for rango, ini, _ in plan:
        sid = src_de_archivo.get(nuevos.banners[rango])
        if sid is None:
            aviso("La plantilla no tiene dónde poner '%s'; agrégalo a mano." % nombre_rango(rango))
        else:
            pasos.append((rango, sid, round(ini * FPS)))
    if not pasos:
        return txt

    libres, ganador = clips[:-1], clips[-1]
    medios = pares[1:-1]
    bloques = []
    for n, (rango, sid, corte) in enumerate(pasos):
        if n == 0:
            ini = max(corte, TR)
            par = pares[0]
        else:
            ini = corte - h + TR
            par = medios.pop(0) if medios else [renumerar(b) for b in (pares[1] if len(pares) > 2 else pares[0])]
        fin = pasos[n + 1][2] - h if n + 1 < len(pasos) else fin_video - TR
        bloques += [poner(b, start=ini - TR) for b in par]
        if rango == GANADOR:
            clip = ganador
        elif libres:
            clip = libres.pop(0)
        else:   # carrera con más parciales que la plantilla
            clip = renumerar(clips[-2] if len(clips) > 1 else ganador)
        bloques.append(poner(clip, start=ini, duration=max(fin - ini, 1), src=sid))
        ultimo_fin = ini + max(fin - ini, 1)
    bloques += [poner(b, start=ultimo_fin) for b in pares[-1]]

    interior2 = interior[:items[0].start()] + sep.join(bloques) + interior[items[-1].end():]
    pista2 = pista[:i] + interior2 + pista[j:]
    return txt[:m.start()] + pista2 + txt[m.end():]


def procesar_86(plantilla, nuevos, salida_dir, nombre):
    with open(plantilla, "rb") as f:
        txt = f.read().decode("utf-8")
    i, j = txt.index("<CSMLData>"), txt.index("</CSMLData>") + len("</CSMLData>")
    root = ET.fromstring(txt[i:j])
    proj = root.find("GoProject/Project")
    FPS = int(Fraction(proj.get("editRate", "30/1")))

    fuentes = {}
    carrera = None
    print("\nReemplazos:")
    for src in root.iter("Source"):
        sid, viejo = src.get("id"), src.get("src")
        nuevo = nuevos.elegir(viejo)
        if not nuevo:
            aviso("Sin reemplazo para: %s (queda igual)" % viejo)
            continue
        print("  %-60s -> %s" % (os.path.basename(viejo)[:60], os.path.basename(nuevo)))
        info = probe(nuevo)
        old_w = int(src.get("rect").strip("()").split(",")[2])
        imagen = all(t.get("type") == "1" for t in src.findall("SourceTrack"))
        old_frames = new_frames = None
        rango = None
        if not imagen:
            r_old = int(src.find("SourceTrack").get("range").strip("()").split(",")[1])
            old_frames = round(r_old * FPS / 1e7)
            new_frames = int(math.ceil(info["fmt_dur"] * FPS - 1e-6))
            rango = round(new_frames * 1e7 / FPS)

        m = re.search(r'<Source id="%s" .*?</Source>' % sid, txt, re.S)
        bloque = m.group(0)
        cab = re.match(r"<Source [^>]*>", bloque).group(0)
        cab2 = poner_attr(cab, "src", xml_attr(nuevo))
        cab2 = poner_attr(cab2, "lastMod", info["lastMod"])
        cab2 = poner_attr(cab2, "rect", "(0,0,%d,%d)" % (info["w"], info["h"]))
        bloque2 = cab2 + bloque[len(cab):]

        def pista(mt):
            t = mt.group(0)
            tipo = re.search(r'type="(\d)"', t).group(1)
            if tipo in ("0", "1"):
                t = poner_attr(t, "trackRect", "(0,0,%d,%d)" % (info["w"], info["h"]))
            if tipo == "0":
                t = poner_attr(t, "range", "(0,%d)" % rango)
                t = poner_attr(t, "sampleRate", "10000000/%d" % round(1e7 / float(info["fps"])))
            if tipo == "2":
                if "adur" not in info:
                    aviso("El video nuevo no tiene audio: %s" % os.path.basename(nuevo))
                else:
                    t = poner_attr(t, "range", "(0,%d)" % rango)
                    t = poner_attr(t, "sampleRate", "%d/1" % info["sr"])
                    t = poner_attr(t, "numChannels", str(info["ch"]))
            return t

        bloque2 = re.sub(r"<SourceTrack [^>]*/>", pista, bloque2)
        txt = txt[:m.start()] + bloque2 + txt[m.end():]
        datos = {"old": old_frames, "new": new_frames, "imagen": imagen,
                 "factor": old_w / info["w"] if old_w != info["w"] else 1.0,
                 "carrera": nuevo == nuevos.video, "base": os.path.basename(nuevo),
                 "archivo": nuevo, "banner": nuevo in nuevos.banners.values()}
        fuentes[sid] = datos
        if datos["carrera"]:
            carrera = datos

    OLD_END = carrera["old"] if carrera else None
    NEW_END = carrera["new"] if carrera else None
    delta = (NEW_END - OLD_END) if carrera else 0
    if carrera:
        print("\nDuración del video: plantilla %.2f s -> nuevo %.2f s" % (OLD_END / FPS, NEW_END / FPS))

    cambios = {}   # (tag, id) -> {attr: valor}
    escalas = []   # (tag, id, factor)
    pista_banners = None
    como = None
    inicios = []
    for track in root.iter("GenericTrack"):
        clips = [c for c in track.iter("IMFile")]
        if len(clips) > 2 and all(fuentes.get(c.get("src"), {}).get("banner") for c in clips):
            inicios = sorted(int(c.get("start")) for c in clips)
    primer_parcial = inicios[1] if inicios else None   # en la plantilla, en cuadros

    for track in root.iter("GenericTrack"):
        medias = track.find("Medias")
        if medias is None:
            continue
        items = []
        for m in medias:
            hijos = [c for c in m if c.get("start") is not None] if m.tag == "UnifiedMedia" else [m]
            for c in hijos:
                if c.get("start") is None:
                    continue
                items.append({"tag": c.tag, "id": c.get("id"), "start": int(c.get("start")),
                              "dur": int(c.get("duration")), "src": c.get("src"),
                              "ms": frac(c.get("mediaStart")), "md": frac(c.get("mediaDuration")),
                              "scalar": c.get("scalar", "1/1"), "trans": c.tag.endswith("Transition")})
        for it in items:
            f = fuentes.get(it["src"])
            if f and f["factor"] != 1.0 and it["tag"] in ("VMFile", "IMFile", "ScreenVMFile"):
                escalas.append((it["tag"], it["id"], f["factor"]))
        clips = [it for it in items if not it["trans"]]
        if clips and all(it["tag"] == "IMFile" and fuentes.get(it["src"], {}).get("banner") for it in clips):
            pista_banners = (track.get("id"), sorted(clips, key=lambda it: it["start"]))
            continue   # se arma de nuevo al final, con los tiempos del video

        def mover_fin(it, nuevo_fin):
            viejo_fin = it["start"] + it["dur"]
            it["dur"] = nuevo_fin - it["start"]
            c = cambios.setdefault((it["tag"], it["id"]), {})
            c["duration"] = str(it["dur"])
            f = fuentes.get(it["src"])
            if f and not f["imagen"] and it["scalar"] == "1/1":
                it["md"] = Fraction(it["dur"])
                c["mediaDuration"] = "%d/1" % it["dur"]
            # la transición que estaba pegada al final del clip se mueve con él
            for t in items:
                if t["trans"] and t["start"] == viejo_fin:
                    t["start"] = nuevo_fin
                    cambios.setdefault((t["tag"], t["id"]), {})["start"] = str(nuevo_fin)

        def colocar(it, ini, dur):
            it["start"], it["dur"] = ini, dur
            c = cambios.setdefault((it["tag"], it["id"]), {})
            c["start"], c["duration"] = str(ini), str(dur)
            f = fuentes.get(it["src"])
            if not it["trans"] and f and not f["imagen"] and it["scalar"] == "1/1":
                it["md"] = Fraction(dur)
                c["mediaDuration"] = "%d/1" % dur

        def repartir():
            """Reubica clips y transiciones en proporción a la duración del video nuevo."""
            k = NEW_END / OLD_END
            grupos = {}
            for it in items:   # AMFile/VMFile o Audio/ImageTransition que van juntos
                grupos.setdefault((it["start"], it["dur"]), []).append(it)
            fin_viejo = fin_nuevo = 0
            nuevos_g = []
            for ini, dur in sorted(grupos):
                g = grupos[(ini, dur)]
                ns = fin_nuevo + round(max(ini - fin_viejo, 0) * k)
                if g[0]["trans"]:
                    nd = dur
                else:
                    nd = round(dur * k)
                    for it in g:
                        f = fuentes.get(it["src"])
                        if f and not f["imagen"] and f["new"]:
                            nd = min(nd, int(f["new"] - it["ms"]))
                        elif not f and it["tag"] != "IMFile":
                            nd = min(nd, dur)   # video no reemplazado: no pedirle más material
                    nd = max(nd, 1)
                nuevos_g.append([g, ns, nd])
                fin_viejo, fin_nuevo = ini + dur, ns + nd
            # si la pista terminaba junto con el video, sigue terminando junto con el nuevo
            if fin_viejo == OLD_END and fin_nuevo != NEW_END:
                dif = NEW_END - fin_nuevo
                clips = [i for i, x in enumerate(nuevos_g) if not x[0][0]["trans"]]
                if clips and nuevos_g[clips[-1]][2] + dif >= 1:
                    nuevos_g[clips[-1]][2] += dif
                    for x in nuevos_g[clips[-1] + 1:]:
                        x[1] += dif
            for g, ns, nd in nuevos_g:
                for it in g:
                    colocar(it, ns, nd)

        con_carrera = any(fuentes.get(it["src"], {}).get("carrera") for it in items)
        if carrera and not con_carrera and items:
            # cintillo: a la misma distancia del primer parcial que en la plantilla, o en proporción
            eventos = parciales_de(nuevos)
            off = round(eventos[0] * FPS) - primer_parcial if eventos and primer_parcial is not None else None
            if off is not None and min(it["start"] for it in items) + off >= 0 and                     max(it["start"] + it["dur"] for it in items) + off <= NEW_END:
                for it in items:
                    colocar(it, it["start"] + off, it["dur"])
                como = "con el primer parcial, como en la plantilla"
            elif delta:
                repartir()
                como = "en proporción (x%.2f)" % (NEW_END / OLD_END)

        # 1) en la pista de la carrera, lo que terminaba al final del video viejo termina al final del nuevo
        elif carrera and delta:
            finales = [t for t in items if t["trans"] and t["start"] + t["dur"] == OLD_END]
            anclas = {OLD_END} | {t["start"] for t in finales}
            for t in finales:
                t["start"] += delta
                cambios.setdefault((t["tag"], t["id"]), {})["start"] = str(t["start"])
            for it in items:
                if it["trans"] or it["start"] + it["dur"] not in anclas:
                    continue
                nuevo_fin = it["start"] + it["dur"] + delta
                if nuevo_fin - it["start"] >= FPS:
                    viejo_fin = it["start"] + it["dur"]
                    it["dur"] = nuevo_fin - it["start"]
                    c = cambios.setdefault((it["tag"], it["id"]), {})
                    c["duration"] = str(it["dur"])
                    f = fuentes.get(it["src"])
                    if f and not f["imagen"] and it["scalar"] == "1/1":
                        it["md"] = Fraction(it["dur"])
                        c["mediaDuration"] = "%d/1" % it["dur"]
                else:
                    aviso("Un clip empieza después del final del video nuevo (cuadro %d); revísalo." % it["start"])

        # 2) los clips de video no pueden pedir más material del que tiene el archivo nuevo
        for it in items:
            f = fuentes.get(it["src"])
            if it["trans"] or not f or f["imagen"] or not f["new"] or it["scalar"] != "1/1":
                continue
            disponible = f["new"] - it["ms"]
            if disponible <= 0:
                aviso("Un clip de %s empieza más allá del final del archivo nuevo (cuadro %d)."
                      % (f["base"], it["start"]))
            elif it["md"] > disponible:
                mover_fin(it, it["start"] + int(disponible))
                aviso("Se recortó un clip de %s a %d cuadros porque el archivo nuevo es más corto."
                      % (f["base"], int(disponible)))

        if carrera and NEW_END:
            for it in items:
                if it["start"] + it["dur"] > NEW_END + 1:
                    aviso("Un elemento (%s, cuadro %d) queda después del final del video nuevo."
                          % (it["tag"], it["start"]))

    for (tag, eid), attrs in cambios.items():
        m = re.search(r'<%s id="%s"[^>]*>' % (tag, eid), txt)
        etiqueta = m.group(0)
        for k, v in attrs.items():
            etiqueta = poner_attr(etiqueta, k, v)
        txt = txt[:m.start()] + etiqueta + txt[m.end():]

    for tag, eid, f in escalas:
        m = re.search(r'<%s id="%s"[^>]*>.*?</%s>' % (tag, eid, tag), txt, re.S)
        b = multiplicar_vector(m.group(0), "scale", [f, f])
        b = multiplicar_vector(b, "geometryCrop", [1 / f] * 4)
        txt = txt[:m.start()] + b + txt[m.end():]

    if pista_banners:
        k = NEW_END / OLD_END if carrera else 1
        clips = pista_banners[1]
        fin = NEW_END or clips[-1]["start"] + clips[-1]["dur"]
        plan = plan_banners(nuevos, fin / FPS, [it["start"] / FPS for it in clips], k)
        txt = armar_banners_86(txt, pista_banners[0], plan, fuentes, nuevos, FPS, fin)

    if como:
        print("Cintillo reubicado %s." % como)
    if carrera and delta < 0:
        aviso("El video nuevo es más corto: los focos (zoom-n-pan) de la plantilla después de %.1f s ya no aplican."
              % (NEW_END / FPS))

    # Cabecera: ID nuevo, sin autoguardado heredado, título y fecha
    txt = re.sub(r"<ProjectID>[^<]*</ProjectID>",
                 lambda _: "<ProjectID>%s</ProjectID>" % str(uuid.uuid4()).upper(), txt, count=1)
    txt = re.sub(r"<AutoSaveFile>[^<]*</AutoSaveFile>", "<AutoSaveFile></AutoSaveFile>", txt, count=1)
    txt = re.sub(r"(<FieldArrayKey>8</FieldArrayKey>\s*<Value>)[^<]*(</Value>)",
                 lambda mm: mm.group(1) + xml_attr(nombre) + mm.group(2), txt, count=1)
    ahora = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    txt = re.sub(r"(<FieldArrayKey>13</FieldArrayKey>\s*<Value>)[^<]*(</Value>)",
                 lambda mm: mm.group(1) + ahora + mm.group(2), txt, count=1)

    ET.fromstring(txt[txt.index("<CSMLData>"):txt.index("</CSMLData>") + len("</CSMLData>")])  # validación
    salida = ruta_libre(os.path.join(salida_dir, nombre + ".camproj"))
    with open(salida, "wb") as f:
        f.write(txt.encode("utf-8"))
    return salida


# ----------------------------------------------------------------------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Proyecto nuevo de Camtasia desde una plantilla.")
    ap.add_argument("--plantilla", required=True, help=".camproj (8.6) o .tscproj (2026)")
    ap.add_argument("--salida", help="carpeta donde se crea el proyecto nuevo (por defecto, la carpeta de la carrera)")
    ap.add_argument("--nombre", help="nombre del proyecto (por defecto '460 - 2026' según los banners)")
    ap.add_argument("--limpiar-foco", action="store_true",
                    help="solo 2026: borra los keyframes de foco del video para empezar desde cero")
    ap.add_argument("--foco-auto", action="store_true",
                    help="solo 2026: calcula el foco (paneo) siguiendo a los caballos en el video")
    ap.add_argument("--flash", action="store_true",
                    help="con --foco-auto: versión rápida, con muchos menos movimientos de cámara")
    ap.add_argument("--vista-previa", action="store_true",
                    help="con --foco-auto: también crea un mp4 vertical para revisar el foco")
    ap.add_argument("rutas", nargs="+", help="carpeta de banners y, opcionalmente, el video de la carrera")
    a = ap.parse_args()

    plantilla = os.path.abspath(a.plantilla)
    if os.path.isdir(plantilla):  # carpeta .tscproj de 2026
        plantilla = os.path.join(plantilla, os.path.basename(plantilla))
    a.rutas = [os.path.abspath(r.strip('"')) for r in a.rutas]
    carpetas = [r for r in a.rutas if os.path.isdir(r)]
    if not carpetas:
        sys.exit("Falta la carpeta de la carrera (o la de banners).")
    carpeta = carpeta_con_medios(carpetas[0])
    # Carreras\461-2026\  (video + subcarpeta de banners): el proyecto se crea ahí mismo
    carpeta_carrera = carpetas[0] if carpeta != carpetas[0] else os.path.dirname(carpeta)
    a.salida = os.path.abspath(a.salida) if a.salida else carpeta_carrera
    print("Banners:  %s" % carpeta)

    nuevos = Nuevos(carpeta, None)
    numero, nombre = nombre_proyecto(carpeta, nuevos.banners)
    nuevos.video = buscar_video(a.rutas, carpetas + [carpeta], numero)
    if not nuevos.video:
        sys.exit("No encontré el video de la carrera. Arrástralo junto con la carpeta.")
    nombre = a.nombre or nombre
    print("Video:    %s" % nuevos.video)
    print("Proyecto: %s" % nombre)
    print("Banners detectados: " + ", ".join(
        "%s=%s" % (nombre_rango(r), os.path.basename(f)) for r, f in sorted(nuevos.banners.items())))

    os.makedirs(a.salida, exist_ok=True)
    if plantilla.lower().endswith(".tscproj"):
        foco = calcular_foco(nuevos, a.salida, nombre, a.vista_previa, a.flash) if a.foco_auto else None
        salida = procesar_2026(plantilla, nuevos, a.salida, nombre, a.limpiar_foco, foco)
    elif plantilla.lower().endswith(".camproj"):
        if a.limpiar_foco or a.foco_auto or a.flash:
            aviso("--limpiar-foco, --foco-auto y --flash solo funcionan con Camtasia 2026; en 8.6 se conservan los focos.")
        salida = procesar_86(plantilla, nuevos, a.salida, nombre)
    else:
        sys.exit("La plantilla debe ser .camproj o .tscproj")
    nuevos.avisar_sobrantes()

    print("\nListo: %s" % salida)
    if avisos:
        print("\nRevisa %d aviso(s) marcados con [!]." % len(avisos))


if __name__ == "__main__":
    main()
