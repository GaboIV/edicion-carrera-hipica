@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Igual que "Crear proyecto 2026", pero calcula el foco siguiendo a los caballos.
  echo  La primera vez analiza el video completo: tarda unos minutos.
  echo  Tambien crea "... - vista previa del foco.mp4" para revisarlo sin abrir Camtasia.
  echo  Para una version rapida y con menos movimientos de camara usa "foco flash".
  echo  Arrastra sobre este archivo la carpeta de la carrera, por ejemplo Carreras\461-2026
  echo.
  pause
  exit /b
)
python "%~dp0camtasia_carrera.py" --plantilla "%~dp0plantillas\plantilla-2026.tscproj" --foco-auto --vista-previa %*
echo.
pause
