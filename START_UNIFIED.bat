@echo off
REM ============================================================
REM  Unified Trading System Startup
REM  Starts MiroFish + Sector Rotation Map + OpenAlgo (3 broker instances)
REM  Run this to use the full intelligence stack (predictions + rotation + fundamentals)
REM ============================================================
echo.
echo ========================================
echo  Unified Trading System Startup
echo ========================================
echo.
echo This will open SIX terminal windows:
echo   1. MiroFish (port 5003) - AI predictions
echo   2. MiroFish Frontend (port 3000) - graph UI
echo   3. Sector Rotation Map (port 8000) - RRG allocation
echo   4. OpenAlgo Kotak (port 5000)
echo   5. OpenAlgo Dhan (port 5001)
echo   6. OpenAlgo Zerodha (port 5002) - authoritative for TOMIC/Intelligence
echo.
echo Prerequisites:
echo   - Add to .env.zerodha: MIROFISH_URL, SECTOR_ROTATION_URL, INTELLIGENCE_API_KEY
echo   - MiroFish at D:\mirofish\MiroFish
echo   - Sector Rotation Map at D:\sector-rotation\sector-rotation-map
echo.
pause

REM --- 1. MiroFish ---
echo Starting MiroFish...
start "MiroFish (Port 5003)" cmd /k "cd /d D:\mirofish\MiroFish\backend && uv run python run.py"
timeout /t 5 /nobreak > nul

REM --- 2. MiroFish Frontend ---
echo Starting MiroFish Frontend...
start "MiroFish Frontend (Port 3000)" cmd /k "cd /d D:\mirofish\MiroFish && npm run frontend"
timeout /t 5 /nobreak > nul

REM --- 3. Sector Rotation Map ---
echo Starting Sector Rotation Map...
start "Sector Rotation Map (Port 8000)" cmd /k "cd /d D:\sector-rotation\sector-rotation-map && uv run python api_server.py"
timeout /t 5 /nobreak > nul

REM --- 4-6. OpenAlgo (reuse START_BOTH logic) ---
set UV_HTTP_TIMEOUT=300

echo Starting Kotak instance...
start "OpenAlgo - KOTAK (Port 5000)" cmd /k run_kotak.bat
timeout /t 10 /nobreak > nul

echo Starting Dhan instance...
start "OpenAlgo - DHAN (Port 5001)" cmd /k run_dhan.bat
timeout /t 10 /nobreak > nul

echo Starting Zerodha instance...
start "OpenAlgo - ZERODHA (Port 5002)" cmd /k run_zerodha.bat

echo.
echo ========================================
echo  All services started!
echo ========================================
echo.
echo MiroFish UI:  http://127.0.0.1:3000
echo MiroFish:     http://127.0.0.1:5003
echo Sector Rot:   http://127.0.0.1:8000
echo Kotak:        http://127.0.0.1:5000
echo Dhan:         http://127.0.0.1:5001
echo Zerodha:      http://127.0.0.1:5002 (Command Center, Options Selling)
echo.
echo Close this window or press any key...
pause > nul
