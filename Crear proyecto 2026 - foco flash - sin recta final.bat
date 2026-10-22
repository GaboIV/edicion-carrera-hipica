@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Foco flash, pero la recta final te la deja a vos:
  echo    - antes de la recta sigue automatico, con pocos movimientos de camara
  echo    - la recta final queda con el foco fijo, para que la armes a mano en Camtasia
  echo    - despues de cruzar la meta vuelve el foco automatico, con el ganador
  echo  Podes arrastrar una carpeta de carrera O una carpeta con varias carreras adentro:
  echo  el script entra en cada subcarpeta y hace el proyecto de cada una.
  echo  Ejemplo: Carreras\La Rinconada\Reunion 40
  echo.
  pause
  exit /b
)
python "%~dp0camtasia_carrera.py" --plantilla "%~dp0plantillas\plantilla-2026.tscproj" --foco-auto --flash --sin-recta-final %*
echo.
pause
