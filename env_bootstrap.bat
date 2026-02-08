@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0" || exit /b 1

echo ========================================
echo  OpenAlgo Env Bootstrap (Kotak/Dhan/Zerodha)
echo ========================================
echo.

if not exist .sample.env (
  echo ERROR: .sample.env not found in this folder.
  pause
  exit /b 1
)

call :make_env_kotak
call :make_env_dhan
call :make_env_zerodha

echo.
echo ? Done.
pause
exit /b 0

:make_env_kotak
set "OUT=.env.kotak"
if exist "%OUT%" (
  echo %OUT% already exists - skipping
  goto :eof
)
copy /Y .sample.env "%OUT%" >nul
powershell -NoProfile -Command "
  $p=\"%OUT%\"; 
  $c=Get-Content $p; 
  $out=foreach($l in $c){$t=$l.Trim(); 
    if($t -like \"BROKER_API_KEY =*\"){\"BROKER_API_KEY = 'YQRCT'\"; continue}; 
    if($t -like \"BROKER_API_SECRET =*\"){\"BROKER_API_SECRET = '029d65cf-677f-409d-be1e-ab5270d30fb3'\"; continue}; 
    if($t -like \"REDIRECT_URL =*\"){\"REDIRECT_URL = 'http://127.0.0.1:5000/kotak/callback'\"; continue}; 
    if($t -like \"DATABASE_URL =*\"){\"DATABASE_URL = 'sqlite:///db/openalgo_kotak.db'\"; continue}; 
    if($t -like \"LATENCY_DATABASE_URL =*\"){\"LATENCY_DATABASE_URL = 'sqlite:///db/latency_kotak.db'\"; continue}; 
    if($t -like \"LOGS_DATABASE_URL =*\"){\"LOGS_DATABASE_URL = 'sqlite:///db/logs_kotak.db'\"; continue}; 
    if($t -like \"SANDBOX_DATABASE_URL =*\"){\"SANDBOX_DATABASE_URL = 'sqlite:///db/sandbox_kotak.db'\"; continue}; 
    if($t -like \"HISTORIFY_DATABASE_URL =*\"){\"HISTORIFY_DATABASE_URL = 'db/historify.duckdb'\"; continue}; 
    if($t -like \"HOST_SERVER =*\"){\"HOST_SERVER = 'http://127.0.0.1:5000'\"; continue}; 
    if($t -like \"FLASK_PORT='*\"){\"FLASK_PORT='5000'\"; continue}; 
    if($t -like \"WEBSOCKET_PORT='*\"){\"WEBSOCKET_PORT='8765'\"; continue}; 
    if($t -like \"WEBSOCKET_URL='*\"){\"WEBSOCKET_URL='ws://127.0.0.1:8765'\"; continue}; 
    if($t -like \"ZMQ_PORT='*\"){\"ZMQ_PORT='5555'\"; continue}; 
    if($t -like \"CORS_ALLOWED_ORIGINS =*\"){\"CORS_ALLOWED_ORIGINS = 'http://127.0.0.1:5000'\"; continue}; 
    if($t -like \"SESSION_COOKIE_NAME =*\"){\"SESSION_COOKIE_NAME = 'session_kotak'\"; continue}; 
    if($t -like \"CSRF_COOKIE_NAME =*\"){\"CSRF_COOKIE_NAME = 'csrf_token_kotak'\"; continue}; 
    $l}; 
  $out | Set-Content $p
"

echo Created %OUT%

:make_env_dhan
set "OUT=.env.dhan"
if exist "%OUT%" (
  echo %OUT% already exists - skipping
  goto :eof
)
copy /Y .sample.env "%OUT%" >nul
powershell -NoProfile -Command "
  $p=\"%OUT%\"; 
  $c=Get-Content $p; 
  $out=foreach($l in $c){$t=$l.Trim(); 
    if($t -like \"BROKER_API_KEY =*\"){\"BROKER_API_KEY = '1000351204:::7a710879'\"; continue}; 
    if($t -like \"BROKER_API_SECRET =*\"){\"BROKER_API_SECRET = '2091d664-2bae-4f1a-ade9-a098f0b2273a'\"; continue}; 
    if($t -like \"REDIRECT_URL =*\"){\"REDIRECT_URL = 'http://127.0.0.1:5001/dhan/callback'\"; continue}; 
    if($t -like \"DATABASE_URL =*\"){\"DATABASE_URL = 'sqlite:///db/openalgo_dhan.db'\"; continue}; 
    if($t -like \"LATENCY_DATABASE_URL =*\"){\"LATENCY_DATABASE_URL = 'sqlite:///db/latency_dhan.db'\"; continue}; 
    if($t -like \"LOGS_DATABASE_URL =*\"){\"LOGS_DATABASE_URL = 'sqlite:///db/logs_dhan.db'\"; continue}; 
    if($t -like \"SANDBOX_DATABASE_URL =*\"){\"SANDBOX_DATABASE_URL = 'sqlite:///db/sandbox_dhan.db'\"; continue}; 
    if($t -like \"HISTORIFY_DATABASE_URL =*\"){\"HISTORIFY_DATABASE_URL = 'db/historify.duckdb'\"; continue}; 
    if($t -like \"HOST_SERVER =*\"){\"HOST_SERVER = 'http://127.0.0.1:5001'\"; continue}; 
    if($t -like \"FLASK_PORT='*\"){\"FLASK_PORT='5001'\"; continue}; 
    if($t -like \"WEBSOCKET_PORT='*\"){\"WEBSOCKET_PORT='8766'\"; continue}; 
    if($t -like \"WEBSOCKET_URL='*\"){\"WEBSOCKET_URL='ws://127.0.0.1:8766'\"; continue}; 
    if($t -like \"ZMQ_PORT='*\"){\"ZMQ_PORT='5556'\"; continue}; 
    if($t -like \"CORS_ALLOWED_ORIGINS =*\"){\"CORS_ALLOWED_ORIGINS = 'http://127.0.0.1:5001'\"; continue}; 
    if($t -like \"SESSION_COOKIE_NAME =*\"){\"SESSION_COOKIE_NAME = 'session_dhan'\"; continue}; 
    if($t -like \"CSRF_COOKIE_NAME =*\"){\"CSRF_COOKIE_NAME = 'csrf_token_dhan'\"; continue}; 
    $l}; 
  $out | Set-Content $p
"

echo Created %OUT%

:make_env_zerodha
set "OUT=.env.zerodha"
if exist "%OUT%" (
  echo %OUT% already exists - skipping
  goto :eof
)
copy /Y .sample.env "%OUT%" >nul
powershell -NoProfile -Command "
  $p=\"%OUT%\"; 
  $c=Get-Content $p; 
  $out=foreach($l in $c){$t=$l.Trim(); 
    if($t -like \"BROKER_API_KEY =*\"){\"BROKER_API_KEY = 'aja2puur7fptl9cz'\"; continue}; 
    if($t -like \"BROKER_API_SECRET =*\"){\"BROKER_API_SECRET = 'ri6ctp52zp3oue1c7p428duirqqza6cs'\"; continue}; 
    if($t -like \"REDIRECT_URL =*\"){\"REDIRECT_URL = 'http://127.0.0.1:5002/zerodha/callback'\"; continue}; 
    if($t -like \"DATABASE_URL =*\"){\"DATABASE_URL = 'sqlite:///db/openalgo_zerodha.db'\"; continue}; 
    if($t -like \"LATENCY_DATABASE_URL =*\"){\"LATENCY_DATABASE_URL = 'sqlite:///db/latency_zerodha.db'\"; continue}; 
    if($t -like \"LOGS_DATABASE_URL =*\"){\"LOGS_DATABASE_URL = 'sqlite:///db/logs_zerodha.db'\"; continue}; 
    if($t -like \"SANDBOX_DATABASE_URL =*\"){\"SANDBOX_DATABASE_URL = 'sqlite:///db/sandbox_zerodha.db'\"; continue}; 
    if($t -like \"HISTORIFY_DATABASE_URL =*\"){\"HISTORIFY_DATABASE_URL = 'db/historify.duckdb'\"; continue}; 
    if($t -like \"HOST_SERVER =*\"){\"HOST_SERVER = 'http://127.0.0.1:5002'\"; continue}; 
    if($t -like \"FLASK_PORT='*\"){\"FLASK_PORT='5002'\"; continue}; 
    if($t -like \"WEBSOCKET_PORT='*\"){\"WEBSOCKET_PORT='8767'\"; continue}; 
    if($t -like \"WEBSOCKET_URL='*\"){\"WEBSOCKET_URL='ws://127.0.0.1:8767'\"; continue}; 
    if($t -like \"ZMQ_PORT='*\"){\"ZMQ_PORT='5557'\"; continue}; 
    if($t -like \"CORS_ALLOWED_ORIGINS =*\"){\"CORS_ALLOWED_ORIGINS = 'http://127.0.0.1:5002'\"; continue}; 
    if($t -like \"CSP_STYLE_SRC =*\"){\"CSP_STYLE_SRC = '\'self\' \'unsafe-inline\' https://fonts.googleapis.com'\"; continue}; 
    if($t -like \"CSP_FONT_SRC =*\"){\"CSP_FONT_SRC = '\'self\' https://fonts.gstatic.com'\"; continue}; 
    if($t -like \"SESSION_COOKIE_NAME =*\"){\"SESSION_COOKIE_NAME = 'session_zerodha'\"; continue}; 
    if($t -like \"CSRF_COOKIE_NAME =*\"){\"CSRF_COOKIE_NAME = 'csrf_token_zerodha'\"; continue}; 
    $l}; 
  $out | Set-Content $p
"

echo Created %OUT%

:eof
