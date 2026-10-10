@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Restauracion y Enhancement Profesional 4:3 para carreras de Valencia:
  echo    - Reformatea la relacion de aspecto de 16:9 estirado a 4:3 real - 1440x1080
  echo    - Corrige el desenfoque con deconvolucion optica multi-escala
  echo    - Recupera brillo, contraste y negros profundos con curva en S de produccion
  echo    - Elimina el tono frio/grisaceo en la arena y aviva el verde de la grama
  echo    - Estandariza la salida a 1440x1080 Full HD BT.709 y limpia el audio
  echo.
  echo  Arrastra sobre este archivo la carpeta o el video de la carrera, por ejemplo:
  echo    Carreras\Valencia\2026 - 204
  echo.
  pause
  exit /b
)
python "%~dp0restaurar_valencia.py" %*
echo.
pause
