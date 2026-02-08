@echo off
REM EMERGENCY RESTORE - Abort rebase and restore from backup

cd /d "%~dp0"

echo ========================================
echo  EMERGENCY RESTORE
echo ========================================
echo.

REM 1. Abort any active rebase
echo Aborting rebase...
git rebase --abort 2>nul

REM 2. Find the most recent backup branch
echo.
echo Finding backup branch...
for /f "delims=" %%b in ('git branch ^| findstr "backup/main-before-upstream-sync-26134"') do set "BACKUP=%%b"
if "!BACKUP!"=="" (
    for /f "delims=" %%b in ('git branch ^| findstr "backup/main-before-upstream-sync" ^| sort /r') do (
        set "BACKUP=%%b"
        goto found
    )
)
:found
set BACKUP=!BACKUP:~2!
echo Found backup: !BACKUP!

REM 3. Reset to backup branch
echo.
echo Restoring from backup...
git reset --hard !BACKUP!

REM 4. Verify files
echo.
echo ========================================
echo  Verifying Your Files
echo ========================================
if exist auto_trading_window.html (echo [OK] auto_trading_window.html) else (echo [MISSING] auto_trading_window.html)
if exist mock_replay.html (echo [OK] mock_replay.html) else (echo [MISSING] mock_replay.html)
if exist scalping_interface.html (echo [OK] scalping_interface.html) else (echo [MISSING] scalping_interface.html)
if exist chart_window.html (echo [OK] chart_window.html) else (echo [MISSING] chart_window.html)
if exist scripts\historify_replay_server.py (echo [OK] scripts\historify_replay_server.py) else (echo [MISSING] scripts\historify_replay_server.py)
if exist blueprints\mock_replay.py (echo [OK] blueprints\mock_replay.py) else (echo [MISSING] blueprints\mock_replay.py)

echo.
echo ========================================
echo  RESTORED! You are back to before sync.
echo ========================================
echo.
echo Your branch: !BACKUP!
echo All your files should be back now.
echo.
pause
