@echo off
REM AUTO-FIX SYNC - Protects ALL your custom work
REM Strategy: Take THEIRS only for auto-built files, OURS for everything else

cd /d "%~dp0"

echo ========================================
echo  AUTO-FIX: Resolving Sync Conflicts
echo ========================================
echo.
echo Strategy:
echo - Auto-built files (dist, CSS) = UPSTREAM version
echo - ALL other files = YOUR version (protected)
echo.

REM Step 1: Check if we're in a rebase
git status | findstr /c:"rebase in progress" >nul
if errorlevel 1 (
    echo No rebase in progress. Starting fresh sync...
    goto fresh_sync
)

echo Rebase in progress detected. Auto-fixing...
echo.

:fix_loop
REM Check if still in rebase
git status | findstr /c:"rebase in progress" >nul
if errorlevel 1 goto rebase_done

REM Get current commit being applied
for /f "delims=" %%m in ('git log -1 --format^=%%s REBASE_HEAD 2^>nul') do set "COMMIT_MSG=%%m"
echo Processing: !COMMIT_MSG!

REM Check if it's an auto-build commit
echo !COMMIT_MSG! | findstr /i /c:"auto-build" /c:"[skip ci]" /c:"frontend dist" >nul
if not errorlevel 1 (
    echo   → Auto-build commit, SKIPPING (safe to skip)
    git rebase --skip
    goto fix_loop
)

REM Regular commit with conflicts - resolve intelligently
echo   → Regular commit, resolving conflicts...

REM Get list of conflicted files
for /f "usebackq delims=" %%f in (`git diff --name-only --diff-filter^=U 2^>nul`) do (
    set "FILE=%%f"
    
    REM Check if it's an auto-built file (take THEIRS)
    echo !FILE! | findstr /i /c:"frontend/dist/" /c:"frontend\\dist\\" /c:"static/css/main.css" /c:"static\\css\\main.css" >nul
    if not errorlevel 1 (
        echo   → Taking UPSTREAM: !FILE! (auto-built)
        git checkout --theirs "!FILE!" 2>nul
        git add "!FILE!" 2>nul
    ) else (
        REM All other files - take YOURS (your custom work)
        echo   → Keeping YOURS: !FILE! (custom work)
        git checkout --ours "!FILE!" 2>nul
        git add "!FILE!" 2>nul
    )
)

REM Handle deleted files in frontend/dist (auto-built, safe to remove)
for /f "usebackq delims=" %%f in (`git diff --name-only --diff-filter^=D 2^>nul`) do (
    set "FILE=%%f"
    echo !FILE! | findstr /i /c:"frontend/dist/" /c:"frontend\\dist\\" >nul
    if not errorlevel 1 (
        echo   → Removing deleted: !FILE! (auto-built)
        git rm -f "!FILE!" 2>nul
    )
)

REM Continue rebase
git -c core.editor=true rebase --continue 2>nul
if errorlevel 1 (
    echo   → More conflicts detected, continuing loop...
    goto fix_loop
)

goto fix_loop

:rebase_done
echo.
echo ========================================
echo  Rebase Complete!
echo ========================================
echo.

REM Push to origin
echo Pushing to origin...
git push origin main --force-with-lease

REM Restore env files if backed up
if exist .env.dhan.backup (
    echo Restoring .env files...
    move /Y .env.dhan.backup .env.dhan >nul
    move /Y .env.kotak.backup .env.kotak >nul
)

REM Restore stashed changes
git stash list | findstr "temp env files" >nul
if not errorlevel 1 (
    echo Restoring stashed changes...
    git stash pop
)

echo.
echo ========================================
echo  Verifying Your Files
echo ========================================
echo.
echo Checking important files exist...
if exist mock_replay.html (echo ✓ mock_replay.html) else (echo ✗ mock_replay.html MISSING!)
if exist auto_trading_window.html (echo ✓ auto_trading_window.html) else (echo ✗ auto_trading_window.html MISSING!)
if exist scalping_interface.html (echo ✓ scalping_interface.html) else (echo ✗ scalping_interface.html MISSING!)
if exist chart_window.html (echo ✓ chart_window.html) else (echo ✗ chart_window.html MISSING!)
if exist scripts\historify_replay_server.py (echo ✓ replay server) else (echo ✗ replay server MISSING!)
if exist TRADE_LOGGING_ANALYTICS.md (echo ✓ analytics doc) else (echo ✗ analytics doc MISSING!)
if exist SNIPER_SCALPER_PRESET_PLAN.md (echo ✓ preset plan) else (echo ✗ preset plan MISSING!)
if exist blueprints\mock_replay.py (echo ✓ mock replay blueprint) else (echo ✗ mock replay blueprint MISSING!)

echo.
echo If any files are missing, recover from backup:
git branch | findstr "backup/main-before-upstream-sync"
echo.
echo To recover: git checkout [backup-branch] -- .
echo.

echo ========================================
echo  ALL DONE! ✓
echo ========================================
pause
exit /b 0

:fresh_sync
echo No active rebase. Please run sync_upstream.bat first.
pause
exit /b 1
