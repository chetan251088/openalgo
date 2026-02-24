@echo off
echo ========================================
echo  Starting OpenAlgo - DHAN INSTANCE
echo  Port: 5001 (http://127.0.0.1:5001)
echo  WebSocket: 8766
echo ========================================
echo.

REM Point app to Dhan environment file directly (no file copying needed)
set DOTENV_FILE=.env.dhan

echo Configuration: .env.dhan
echo.
echo Starting Flask application...
echo.

REM Increase timeout for slow networks (5 minutes)
set UV_HTTP_TIMEOUT=300

REM Run without auto-sync to avoid Windows file-lock issues while other instances are running.
REM If dependencies changed, run: uv sync
uv run --no-sync --env-file .env.dhan app.py

echo.
echo Application stopped.
pause
