@echo off
echo ========================================
echo  Starting OpenAlgo - ZERODHA INSTANCE
echo  Port: 5002 (http://127.0.0.1:5002)
echo  WebSocket: 8767
echo ========================================
echo.

REM Point app to Zerodha environment file directly (no file copying needed)
set DOTENV_FILE=.env.zerodha

echo Configuration: .env.zerodha
echo.
echo Starting Flask application...
echo.

REM Increase timeout for slow networks (5 minutes)
set UV_HTTP_TIMEOUT=300

REM Run without auto-sync to avoid Windows file-lock issues while other instances are running.
REM If dependencies changed, run: uv sync
uv run --no-sync --env-file .env.zerodha app.py

echo.
echo Application stopped.
pause
