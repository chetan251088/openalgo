@echo off
REM Simple Sync Script - Handles CSS conflicts automatically

cd /d "%~dp0"

echo ========================================
echo  OpenAlgo Simple Sync
echo ========================================
echo.

REM Check for uncommitted changes
for /f "delims=" %%i in ('git status --porcelain') do (
  echo ERROR: Uncommitted changes detected. Commit first!
  git status -sb
  pause
  exit /b 1
)

REM Create backup
set "BACKUP=backup/main-before-sync-%RANDOM%"
echo Creating backup: %BACKUP%
git branch %BACKUP%

REM Fetch upstream
echo Fetching upstream...
git fetch upstream

REM Delete CSS (auto-built anyway)
echo Removing auto-built CSS...
git rm static/css/main.css 2>nul

REM Rebase
echo Rebasing...
git rebase upstream/main

if errorlevel 1 (
  echo.
  echo Conflicts detected. Running auto-skip for auto-build commits...
  
  :skip_loop
  REM Check if we're still in rebase
  git status | findstr /c:"rebase in progress" >nul
  if errorlevel 1 goto rebase_done
  
  REM Get commit message
  for /f "delims=" %%m in ('git log -1 --format^=%%s REBASE_HEAD 2^>nul') do set "MSG=%%m"
  
  REM Check if it's an auto-build commit
  echo !MSG! | findstr /i /c:"auto-build" /c:"[skip ci]" /c:"frontend dist" >nul
  if not errorlevel 1 (
    echo Skipping auto-build commit: !MSG!
    git rebase --skip
    goto skip_loop
  )
  
  REM Not auto-build, try taking theirs for CSS
  git checkout --theirs static/css/main.css 2>nul
  git add static/css/main.css 2>nul
  git rebase --continue
  goto skip_loop
)

:rebase_done
echo.
echo Rebase complete!

REM Push
echo Pushing to origin...
git push origin main --force-with-lease

REM Rebuild CSS
echo.
echo Rebuilding CSS...
call npm run build

echo.
echo ========================================
echo  Sync Complete!
echo  Backup: %BACKUP%
echo ========================================
pause
