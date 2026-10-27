@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo          NEXAFARMA STORE - INICIO
echo ========================================

if not exist "app.py" (
  echo ERROR: No se encontro app.py en %CD%.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno virtual de NexaFarma...
  python -m venv .venv
  if errorlevel 1 (
    echo ERROR: No fue posible crear el entorno virtual.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: No se pudieron instalar las dependencias.
  echo Comprueba que Python y tu conexion a Internet esten disponibles.
  pause
  exit /b 1
)

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Se creo .env desde .env.example.
  echo Edita .env para configurar Gmail API, datos de la tienda, Wompi y WhatsApp.
)

echo.
echo Iniciando servidor NexaFarma...
start "NexaFarma Server" /min cmd /c "call "%~dp0.venv\Scripts\activate.bat" && cd /d "%~dp0" && python app.py"

set "READY=0"
for /l %%i in (1,1,20) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/salud -TimeoutSec 1; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    set "READY=1"
    goto :ready
  )
  timeout /t 1 /nobreak >nul
)

:ready
if "%READY%"=="1" (
  echo Servidor listo en http://127.0.0.1:5000
) else (
  echo AVISO: El servidor tardo mas de lo esperado en responder.
  echo Revisa la ventana "NexaFarma Server" para ver el error.
)
start "" "http://127.0.0.1:5000/"
echo.
echo Puedes cerrar esta ventana; el servidor quedara en la ventana minimizada "NexaFarma Server".
echo.
pause
endlocal
