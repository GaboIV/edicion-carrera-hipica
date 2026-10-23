# -*- coding: utf-8 -*-
"""Entrada única para una carrera, varias carpetas o una reunión completa."""
from pathlib import Path
import sys
import camtasia_carrera

if __name__ == "__main__":
    args=sys.argv[1:]
    if not any(arg=="--plantilla" or arg.startswith("--plantilla=") for arg in args):
        args=["--plantilla",str(Path(__file__).parent/"plantillas"/"plantilla-2026.tscproj")]+args
    sys.argv=[sys.argv[0],"--sol-6-1"]+args
    camtasia_carrera.main()
