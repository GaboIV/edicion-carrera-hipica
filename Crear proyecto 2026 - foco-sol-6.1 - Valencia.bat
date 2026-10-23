@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo Arrastra una carrera de Valencia o una carpeta con varias carreras.
  pause
  exit /b 1
)
"%~dp0.python-vision\cpython-3.12.15-windows-x86_64-none\python.exe" "%~dp0foco-sol-6.1.py" --pista valencia %*
set RESULTADO=%errorlevel%
echo.
pause
exit /b %RESULTADO%
