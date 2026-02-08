@echo off
echo ========================================
echo  Starting OpenAlgo - KOTAK INSTANCE
echo  Port: 5000 (http://127.0.0.1:5000)
echo  WebSocket: 8765
echo ========================================
echo.

REM Point app to Kotak environment file directly (no file copying needed)
set DOTENV_FILE=.env.kotak

echo Configuration: .env.kotak
echo.
echo Starting Flask application...
echo.

REM Increase timeout for slow networks (5 minutes)
set UV_HTTP_TIMEOUT=300

REM Run the application using uv (--env-file tells uv to load .env.kotak instead of .env)
uv run --env-file .env.kotak app.py

echo.
echo Application stopped.
pause
