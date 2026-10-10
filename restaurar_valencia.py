# -*- coding: utf-8 -*-
"""
Restauracion y Enhancement Profesional (Broadcast Pro HD 1440x1080 4:3) para carreras del
Hipodromo Nacional de Valencia.

Corrige los problemas tipicos de la senal de Valencia:
  1. Relacion de aspecto estirada (16:9 -> 4:3 real 1440x1080 SAR 1:1):
     - Devuelve la proporcion natural a los caballos, jinetes y al arco de llegada.
  2. Desenfoque optico y suavizado por escalado de origen:
     - Deconvolucion multi-escala (Unsharp 11x11 de micro-contraste + Unsharp 5x5 + CAS 0.68).
  3. Rango dinamico comprimido (imagen opaca, falta de brillo y contraste):
     - Curva en S de grado Broadcast (negros profundos, medios tonos soleados y blancos limpios).
  4. Dominante fria azulada/grisacea en la arena y el grama:
     - Calibracion de temperatura 5600K Daylight (arena dorada natural y grama verde esmeralda).
  5. Limpieza de ruido de fondo en el audio (afftdn).

Uso:
  python restaurar_valencia.py <carpeta_o_video> [--reemplazar]
"""
import argparse
import os
import subprocess
import sys

VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm"}

VF_VALENCIA_PRO = (
    "scale=1440:1080:flags=lanczos+accurate_rnd+full_chroma_int,setsar=1:1,"
    "hqdn3d=1.6:1.2:2.2:1.6,"
    "colorbalance=rs=-0.01:gs=0.0:bs=-0.01:rm=0.026:gm=0.030:bm=-0.052:rh=0.018:gh=0.020:bh=-0.038,"
    "curves=all=0/0 0.06/0.015 0.25/0.23 0.50/0.58 0.75/0.84 0.94/0.985 1/1,"
    "vibrance=intensity=0.38:rbal=0.95:gbal=1.08:bbal=0.80,"
    "unsharp=11:11:0.85:7:7:0.15,"
    "unsharp=5:5:0.45:3:3:0.0,"
    "cas=0.68"
)

AF_VALENCIA_PRO = "afftdn=nf=-24"


def encontrar_videos(ruta):
    if os.path.isfile(ruta):
        return [ruta]
    videos = []
    for nombre in sorted(os.listdir(ruta)):
        ext = os.path.splitext(nombre)[1].lower()
        if ext in VID_EXT and "enhanced" not in nombre.lower() and "vista previa" not in nombre.lower():
            videos.append(os.path.join(ruta, nombre))
    return videos


def procesar_video(video_in, reemplazar=False):
    carpeta, nombre = os.path.split(video_in)
    base, ext = os.path.splitext(nombre)
    video_out = os.path.join(carpeta, f"{base} - Enhanced Pro HD.mp4")

    print("\n============================================================")
    print(f" Procesando (Valencia Broadcast Pro HD 4:3): {nombre}")
    print(f" Salida: {os.path.basename(video_out)} (1440x1080 4:3)")
    print("============================================================\n")

    cmd = [
        "ffmpeg", "-y", "-i", video_in,
        "-vf", VF_VALENCIA_PRO,
        "-af", AF_VALENCIA_PRO,
        "-aspect", "4:3",
        "-c:v", "libx264", "-crf", "17", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "192k",
        video_out
    ]

    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"  [!] Error al procesar {nombre}")
        return False

    if reemplazar:
        backup = os.path.join(carpeta, f"{base} - Original{ext}")
        if not os.path.exists(backup):
            os.replace(video_in, backup)
        os.replace(video_out, video_in)
        print(f"  [OK] Video original respaldado en: {os.path.basename(backup)}")
        print(f"  [OK] Video mejorado reemplazo a: {nombre}")
    else:
        print(f"  [OK] Video mejorado listo en: {video_out}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Restauracion y Enhancement Pro HD 4:3 para carreras de Valencia.")
    ap.add_argument("rutas", nargs="+", help="Carpeta(s) o archivo(s) de video de Valencia")
    ap.add_argument("--reemplazar", action="store_true",
                    help="Renombra el original como ' - Original.mp4' y deja el Enhanced con el nombre principal")
    args = ap.parse_args()

    for ruta in args.rutas:
        videos = encontrar_videos(ruta)
        if not videos:
            print(f"  [!] No se encontraron videos originales en: {ruta}")
            continue
        for v in videos:
            procesar_video(v, reemplazar=args.reemplazar)


if __name__ == "__main__":
    main()
