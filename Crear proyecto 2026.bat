@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Arrastra sobre este archivo la carpeta de la carrera, por ejemplo Carreras\461-2026
  echo  El proyecto se crea dentro de esa misma carpeta.
  echo.
  pause
  exit /b
)
python "%~dp0camtasia_carrera.py" --plantilla "%~dp0plantillas\plantilla-2026.tscproj" %*
echo.
pause
