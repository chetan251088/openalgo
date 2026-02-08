@echo off
REM Start OpenAlgo with MOCK WebSocket (replay server on 8770).
REM Use this for after-hours testing: Mock Replay UI and mock trades go to db/mock_trading.db.
REM
REM Step 1: In another terminal run  run_replay_server.bat  first.
REM Step 2: Run this script, then open  http://127.0.0.1:5001/mock-replay

setlocal
cd /d "%~dp0" || exit /b 1

set BASE_ENV=.env.dhan
if not exist .env.dhan (
  echo .env.dhan not found. Using .env.kotak instead.
  set BASE_ENV=.env.kotak
)

REM Create .env.mock from base env with WEBSOCKET_URL pointing to mock replay server
if not exist .env.mock (
  echo Creating .env.mock from %BASE_ENV% with WEBSOCKET_URL=ws://127.0.0.1:8770
  copy /Y "%BASE_ENV%" .env.mock > nul
  powershell -NoProfile -Command "(Get-Content .env.mock) -replace 'WEBSOCKET_URL\s*=.*', 'WEBSOCKET_URL = ''ws://127.0.0.1:8770''' | Set-Content .env.mock"
)

echo ========================================
echo  OpenAlgo - MOCK MODE (replay WS 8770)
echo  Port: 5001  http://127.0.0.1:5001
echo  Mock Replay: http://127.0.0.1:5001/mock-replay
echo ========================================
echo.
echo Start the replay server first in another terminal: run_replay_server.bat
echo.
set UV_HTTP_TIMEOUT=300
uv run --env-file .env.mock app.py
echo.
pause
