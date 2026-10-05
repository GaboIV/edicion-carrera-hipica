# -*- coding: utf-8 -*-
"""
Crea un proyecto nuevo de Camtasia a partir de una plantilla, reemplazando:
  - los banners de tiempos (sin tiempos, 400m, 800m, ..., ganador)
  - el cintillo (gif / mp4)
  - el video de la carrera
Soporta Camtasia 8.6 (.camproj, XML) y Camtasia 2026 (.tscproj, JSON).
La plantilla NO se modifica: siempre se escribe un proyecto nuevo.

Uso:
  python camtasia_carrera.py --plantilla PLANTILLA [--salida CARPETA] carpeta_carrera [video.mp4]
"""
import argparse
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
        self.video = video
        self.usados = set()

    def elegir(self, src_viejo, en_linea=True):
        """en_linea=False: la fuente solo está en el contenedor de medios, no en la línea de tiempo."""
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
                menores = [k for k in self.banners if k < r]
                if menores:
                    nuevo = self.banners[max(menores)]
                    if en_linea:
                        aviso("La plantilla tiene '%s' pero esta carrera no; se puso '%s' (repetido). "
                              "Borra ese clip si sobra." % (nombre_rango(r), os.path.basename(nuevo)))
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
                     if ext_de(f) in VID_EXT and not es_cintillo(f)]
    return cand


def buscar_video(rutas, carpetas, numero):
    """Video de la carrera: el que se pasó, o el de la carpeta de la carrera, o el que tenga C<numero> en Descargas."""
    for r in rutas:
        if os.path.isfile(r) and ext_de(r) in VID_EXT and not es_cintillo(r):
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
                     if ext_de(f) in VID_EXT and not es_cintillo(f)]
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


def procesar_2026(plantilla, nuevos, salida_dir, nombre, limpiar_foco):
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

    en_linea = set()   # ids de fuentes que aparecen en la línea de tiempo

    def buscar_src(o):
        if isinstance(o, dict):
            if isinstance(o.get("src"), int):
                en_linea.add(o["src"])
            for v in o.values():
                buscar_src(v)
        elif isinstance(o, list):
            for v in o:
                buscar_src(v)
    buscar_src(p["timeline"])

    fuentes = {}   # id -> datos
    carrera = None
    print("\nReemplazos:")
    for s in p["sourceBin"]:
        viejo = s["src"]
        nuevo = nuevos.elegir(viejo, s["id"] in en_linea)
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
                 "carrera": nuevo == nuevos.video}
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
        if limpiar_foco and fuente["carrera"] and x.get("_type") == "VMFile":
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

    def cuadro(u):
        return int(round(u / FRAME)) * FRAME

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

    for tr in p["timeline"]["sceneTrack"]["scenes"][0]["csml"]["tracks"]:
        recorrer(tr["medias"], True)
        con_carrera = any(fuentes.get(src_de(m), {}).get("carrera") for m in tr["medias"])
        if carrera and OLD_END != NEW_END and not con_carrera and tr["medias"]:
            repartir(tr["medias"])
    if carrera and OLD_END != NEW_END:
        print("Banners y cintillo reubicados en proporción (x%.2f); ajústalos a los parciales reales."
              % (NEW_END / OLD_END))
    if carrera and NEW_END < OLD_END and not limpiar_foco:
        aviso("El video nuevo es más corto: los focos de la plantilla después de %.1f s ya no aplican."
              % (NEW_END / ER))

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
    en_linea = {e.get("src") for e in root.iter() if e.tag != "Source" and e.get("src")}
    for src in root.iter("Source"):
        sid, viejo = src.get("id"), src.get("src")
        nuevo = nuevos.elegir(viejo, sid in en_linea)
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
                 "carrera": nuevo == nuevos.video, "base": os.path.basename(nuevo)}
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
        if carrera and delta and not con_carrera:
            repartir()

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

    if carrera and delta:
        print("Banners y cintillo reubicados en proporción (x%.2f); ajústalos a los parciales reales."
              % (NEW_END / OLD_END))
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
        salida = procesar_2026(plantilla, nuevos, a.salida, nombre, a.limpiar_foco)
    elif plantilla.lower().endswith(".camproj"):
        if a.limpiar_foco:
            aviso("--limpiar-foco solo funciona con Camtasia 2026; en 8.6 se conservan los focos.")
        salida = procesar_86(plantilla, nuevos, a.salida, nombre)
    else:
        sys.exit("La plantilla debe ser .camproj o .tscproj")
    nuevos.avisar_sobrantes()

    print("\nListo: %s" % salida)
    if avisos:
        print("\nRevisa %d aviso(s) marcados con [!]." % len(avisos))


if __name__ == "__main__":
    main()
