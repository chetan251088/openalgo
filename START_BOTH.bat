@echo off
echo ========================================
echo  Starting ALL OpenAlgo Instances
echo ========================================
echo.
echo This will open THREE terminal windows:
echo   1. Kotak instance on Port 5000
echo   2. Dhan instance on Port 5001
echo   3. Zerodha instance on Port 5002
echo.
echo Make sure you've updated .env.dhan with Dhan credentials!
echo.
echo Note: First run may take longer to download packages...
echo.
pause

REM Set increased timeout for package downloads
set UV_HTTP_TIMEOUT=300

echo Starting Kotak instance in new window...
start "OpenAlgo - KOTAK (Port 5000)" cmd /k run_kotak.bat

echo Waiting 10 seconds before starting Dhan instance...
timeout /t 10 /nobreak > nul

echo Starting Dhan instance in new window...
start "OpenAlgo - DHAN (Port 5001)" cmd /k run_dhan.bat

echo Waiting 10 seconds before starting Zerodha instance...
timeout /t 10 /nobreak > nul

echo Starting Zerodha instance in new window...
start "OpenAlgo - ZERODHA (Port 5002)" cmd /k run_zerodha.bat

echo.
echo ========================================
echo  All instances started!
echo ========================================
echo.
echo Kotak:   http://127.0.0.1:5000 (WebSocket: 8765)
echo Dhan:    http://127.0.0.1:5001 (WebSocket: 8766)
echo Zerodha: http://127.0.0.1:5002 (WebSocket: 8767)
echo.
echo Close this window or press any key...
pause > nul
