@echo off
REM Start the Historify replay WebSocket server (port 8770).
REM Run this first, then run_mock.bat, then open http://127.0.0.1:5001/mock-replay
REM Uses .env.mock or .env.dhan so Historify DB path matches the app.

setlocal
cd /d "%~dp0" || exit /b 1

if exist .env.mock (set ENV_FILE=.env.mock) else (set ENV_FILE=.env.dhan)
if not exist %ENV_FILE% set ENV_FILE=.env

echo ========================================
echo  Historify Replay Server (port 8770)
echo  Env: %ENV_FILE%
echo ========================================
echo.

set UV_HTTP_TIMEOUT=300
uv run --env-file %ENV_FILE% python scripts/historify_replay_server.py

pause
