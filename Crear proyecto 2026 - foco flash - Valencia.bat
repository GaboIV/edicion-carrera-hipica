@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo.
  echo  Version de Valencia del foco flash.
  echo    - valida el formato antes de empezar: si el video viene estirado a 16:9 lo devuelve
  echo      a 4:3 con el restaurador "Restaurar video Valencia Pro"; si ya esta en 4:3 no lo toca
  echo    - las cintas de arriba van al tope y miden 1080x240, para tapar las graficas del canal
  echo    - el cintillo de 1080x100 se coloca sobre la franja que divide la pantalla
  echo    - no hay tabla de posiciones, asi que no se recorta nada del video
  echo  Arrastra sobre esta carpeta la carpeta de la carrera, por ejemplo Carreras\Valencia\203 - 2026
  echo.
  pause
  exit /b
)
python "%~dp0camtasia_carrera.py" --plantilla "%~dp0plantillas\plantilla-2026-valencia.tscproj" --pista valencia --foco-auto --flash %*
echo.
pause
