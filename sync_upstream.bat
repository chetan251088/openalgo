@echo off
setlocal enabledelayedexpansion

set "BRANCH=main"
set "UPSTREAM_URL=https://github.com/marketcalls/openalgo.git"
set "IMPORTANT_FILES=mock_replay.html auto_trading_window.html scripts\historify_replay_server.py blueprints\mock_replay.py TRADE_LOGGING_ANALYTICS.md"

cd /d "%~dp0" || exit /b 1

echo ========================================
echo  OpenAlgo Upstream Sync
 echo ========================================
echo  Branch  : %BRANCH%
echo ========================================
echo.
echo IMPORTANT: This script will help resolve conflicts safely.
echo - Auto-built files (frontend/dist, static/css) will use upstream version
echo - Your custom work will be preserved
echo.
echo NOTE: For best results, run this from Windows CMD (not Git Bash)
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo ERROR: Not inside a git repository.
  pause
  exit /b 1
)

REM Ensure upstream remote exists
 git remote get-url upstream >nul 2>&1
if errorlevel 1 (
  echo Adding upstream remote...
  git remote add upstream %UPSTREAM_URL% || exit /b 1
)

REM Abort if working tree is dirty
for /f "delims=" %%i in ('git status --porcelain') do (
  echo ERROR: Working tree is not clean. Commit or stash changes first.
  echo.
  git status -sb
  pause
  exit /b 1
)

REM Fetch upstream
 echo Fetching upstream...
 git fetch upstream || exit /b 1

REM Create backup branch
set "BACKUP=backup/%BRANCH%-before-upstream-sync"
for /f "delims=" %%b in ('git branch --list "!BACKUP!"') do (
  if not "%%b"=="" set "BACKUP=backup/%BRANCH%-before-upstream-sync-%RANDOM%"
)

 echo Creating backup branch: !BACKUP!
 git checkout %BRANCH% >nul 2>&1
if errorlevel 1 (
  echo ERROR: Could not checkout %BRANCH%.
  pause
  exit /b 1
)

git branch !BACKUP! || exit /b 1

REM Rebase onto upstream with interactive conflict handling
 echo Rebasing %BRANCH% onto upstream/%BRANCH%...
 git rebase upstream/%BRANCH%
if errorlevel 1 (
  echo.
  echo ========================================
  echo  REBASE CONFLICTS DETECTED
  echo ========================================
  echo.
  echo Starting smart conflict resolution...
  call :resolve_conflicts
  if errorlevel 1 (
    echo.
    echo Manual intervention required.
    echo Review conflicts and run:
    echo   git add ^<files^>
    echo   git rebase --continue
    echo.
    echo Or abort with:
    echo   git rebase --abort
    echo.
    echo Or restore from backup:
    echo   git rebase --abort
    echo   git reset --hard !BACKUP!
    pause
    exit /b 1
  )
)

REM Push to origin
 echo.
 echo Pushing to origin with --force-with-lease...
 git push origin %BRANCH% --force-with-lease || exit /b 1

 echo.
 echo ========================================
 echo  Verifying Important Files
 echo ========================================
 set "MISSING_FILES="
 for %%f in (%IMPORTANT_FILES%) do (
   if not exist "%%f" (
     echo WARNING: %%f is missing!
     set "MISSING_FILES=1"
   ) else (
     echo OK: %%f
   )
 )
 
 if defined MISSING_FILES (
   echo.
   echo ========================================
   echo  WARNING: Some files are missing!
   echo ========================================
   echo.
   echo Files may have been lost during sync.
   echo To restore from backup, run:
   echo   git checkout !BACKUP! -- .
   echo   git add .
   echo   git commit -m "chore: restore files after sync"
   echo.
   pause
 )
 

:resolve_conflicts
REM Smart conflict resolution
echo.
echo Analyzing conflicts...
set "CONFLICT_COUNT=0"
set "RESOLVED_COUNT=0"

REM Check current commit message
for /f "delims=" %%m in ('git log -1 --format^=%%s REBASE_HEAD 2^>nul') do set "COMMIT_MSG=%%m"
echo Conflicted commit: !COMMIT_MSG!

REM Check if this is an auto-build commit (safe to skip)
echo !COMMIT_MSG! | findstr /i /c:"auto-build" /c:"chore: rebuild" /c:"[skip ci]" >nul
if not errorlevel 1 (
  echo This is an AUTO-BUILD commit - SKIPPING to avoid losing your work
  git rebase --skip
  if not errorlevel 1 (
    set "RESOLVED_COUNT=1"
    goto :check_more_conflicts
  )
  goto :resolve_failed
)

REM This is a user commit - resolve conflicts properly
echo This is a USER commit - preserving your work

REM For each conflicted file, decide strategy
for /f "usebackq delims=" %%f in (`git diff --name-only --diff-filter^=U 2^>nul`) do (
  set "CONFLICT_COUNT=1"
  set "FILE=%%f"
  
  REM Auto-built files: take upstream version
  echo !FILE! | findstr /i /c:"frontend/dist/" /c:"static/css/main.css" >nul
  if not errorlevel 1 (
    echo   Taking UPSTREAM version: !FILE!
    git checkout --theirs -- "!FILE!" 2>nul
    if not errorlevel 1 (
      git add -- "!FILE!"
      set "RESOLVED_COUNT=1"
    )
  ) else (
    REM User files: keep your version
    echo   Keeping YOUR version: !FILE!
    git checkout --ours -- "!FILE!" 2>nul
    if not errorlevel 1 (
      git add -- "!FILE!"
      set "RESOLVED_COUNT=1"
    )
  )
)

REM Handle deleted files in dist
for /f "usebackq delims=" %%f in (`git diff --name-only --diff-filter^=D 2^>nul`) do (
  set "FILE=%%f"
  echo !FILE! | findstr /i /c:"frontend/dist/" >nul
  if not errorlevel 1 (
    echo   Removing deleted: !FILE!
    git rm -f "!FILE!" 2>nul
    set "RESOLVED_COUNT=1"
  )
)

if not defined CONFLICT_COUNT (
  echo No conflicts found in this commit.
  exit /b 0
)

if not defined RESOLVED_COUNT (
  echo Could not auto-resolve conflicts.
  exit /b 1
)

REM Try to continue rebase
echo Continuing rebase...
git rebase --continue
if errorlevel 1 goto :resolve_failed

:check_more_conflicts
REM Check if there are more conflicts
git status | findstr /c:"rebase in progress" >nul
if not errorlevel 1 (
  echo More commits to process...
  goto :resolve_conflicts
)

echo All conflicts resolved!
exit /b 0

:resolve_failed
exit /b 1

 echo.
 echo ? Sync complete.
 pause
