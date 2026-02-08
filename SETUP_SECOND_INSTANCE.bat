@echo off
echo ========================================
echo  OpenAlgo Dual-Broker Setup Guide
echo ========================================
echo.
echo This guide will help you set up both Kotak and Dhan instances
echo to run simultaneously for performance comparison.
echo.
echo ========================================
echo  FILES CREATED:
echo ========================================
echo.
echo 1. .env.kotak         - Kotak broker configuration (Port 5000)
echo 2. .env.dhan          - Dhan broker configuration (Port 5001)
echo 3. run_kotak.bat      - Launcher for Kotak instance
echo 4. run_dhan.bat       - Launcher for Dhan instance
echo.
echo ========================================
echo  SETUP STEPS:
echo ========================================
echo.
echo STEP 1: Update Dhan Credentials
echo --------------------------------
echo Edit .env.dhan and update these values:
echo   BROKER_API_KEY = 'YOUR_DHAN_CLIENT_ID'
echo   BROKER_API_SECRET = 'YOUR_DHAN_ACCESS_TOKEN'
echo.
echo To get Dhan credentials:
echo   1. Login to Dhan web platform
echo   2. Go to Settings ^> API Management
echo   3. Create/Copy your Client ID and Access Token
echo.
echo STEP 2: Run Both Instances
echo --------------------------------
echo Open TWO separate terminal/command prompt windows:
echo.
echo   Terminal 1: Double-click run_kotak.bat
echo              (or run: run_kotak.bat)
echo.
echo   Terminal 2: Double-click run_dhan.bat
echo              (or run: run_dhan.bat)
echo.
echo STEP 3: Access Both Instances
echo --------------------------------
echo   Kotak Instance:  http://127.0.0.1:5000
echo                    WebSocket: ws://127.0.0.1:8765
echo.
echo   Dhan Instance:   http://127.0.0.1:5001
echo                    WebSocket: ws://127.0.0.1:8766
echo.
echo ========================================
echo  IMPORTANT NOTES:
echo ========================================
echo.
echo - Each instance uses SEPARATE databases (complete isolation)
echo - Each instance uses SEPARATE ports (no conflicts)
echo - Each instance uses SEPARATE cookies (independent sessions)
echo - You'll need to login separately to each instance
echo - You can test the same strategy on both brokers simultaneously
echo.
echo ========================================
echo  TROUBLESHOOTING:
echo ========================================
echo.
echo If port 5000 or 5001 is already in use:
echo   1. Close the other instance first
echo   2. Or change FLASK_PORT in the .env file
echo.
echo If WebSocket issues occur:
echo   1. Make sure both WebSocket ports (8765, 8766) are free
echo   2. Check firewall settings
echo.
echo ========================================
echo  PERFORMANCE COMPARISON:
echo ========================================
echo.
echo To compare broker performance:
echo   1. Open chart_window.html on both instances
echo   2. Use the same symbol/contract
echo   3. Place orders simultaneously
echo   4. Compare:
echo      - Order placement speed
echo      - Order line appearance time
echo      - Cancel operation speed
echo      - WebSocket data latency
echo.
echo ========================================
echo.
echo Setup guide complete!
echo.
echo Next step: Edit .env.dhan with your Dhan credentials
echo.
pause
