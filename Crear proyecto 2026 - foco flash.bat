@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Igual que "Crear proyecto 2026 - foco automatico", pero en version express:
  echo    - calcula el foco mucho mas rapido
  echo    - deja pocos movimientos de camara: paneos largos en vez de micro-zooms
  echo    - el cintillo aparece solo mientras la pantalla esta dividida en dos
  echo  No crea la vista previa del foco: para eso usa el bat "foco automatico".
  echo  La primera vez que analiza una carrera tarda uno o dos minutos; despues es instantaneo.
  echo  Arrastra sobre este archivo la carpeta de la carrera, por ejemplo Carreras\461-2026
  echo.
  pause
  exit /b
)
python "%~dp0camtasia_carrera.py" --plantilla "%~dp0plantillas\plantilla-2026.tscproj" --foco-auto --flash %*
echo.
pause
