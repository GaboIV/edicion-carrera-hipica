@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Igual que "Crear proyecto 2026", pero borra los movimientos de foco
  echo  de la plantilla para que empieces el foco desde cero.
  echo  Arrastra sobre este archivo la carpeta de la carrera, por ejemplo Carreras\461-2026
  echo.
  pause
  exit /b
)
python "%~dp0camtasia_carrera.py" --plantilla "%~dp0plantillas\plantilla-2026.tscproj" --limpiar-foco %*
echo.
pause
