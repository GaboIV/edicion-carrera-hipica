@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Valencia, foco flash y recta final a mano:
  echo    - valida el formato: si el video viene estirado a 16:9 lo devuelve a 4:3 con el
  echo      restaurador "Restaurar video Valencia Pro"; si ya esta en 4:3 no lo toca
  echo    - las cintas de arriba van al tope y miden 1080x240, para tapar las graficas del canal
  echo    - el cintillo de 1080x100 se coloca sobre la franja que divide la pantalla
  echo    - la recta final queda con el foco fijo para que la armes a mano y, al cruzar la meta,
  echo      vuelve el foco automatico
  echo  Podes arrastrar una carpeta de carrera O una carpeta con varias carreras adentro.
  echo  Ejemplo: Carreras\Valencia\Reunion 25
  echo.
  pause
  exit /b
)
python "%~dp0camtasia_carrera.py" --plantilla "%~dp0plantillas\plantilla-2026-valencia.tscproj" --pista valencia --foco-auto --flash --sin-recta-final %*
echo.
pause
