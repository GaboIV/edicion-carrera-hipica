@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo Arrastra una carrera o una carpeta con varias carreras sobre este archivo.
  echo Foco-sol-6.1-flash usa el seguimiento visual sin analizar la narracion.
  echo El audio original se conserva en el proyecto.
  pause
  exit /b 1
)
"%~dp0.python-vision\cpython-3.12.15-windows-x86_64-none\python.exe" "%~dp0foco-sol-6.1-flash.py" %*
set RESULTADO=%errorlevel%
echo.
pause
exit /b %RESULTADO%
