@echo off
:: Simple upstream sync script
:: 1. Backup current state
:: 2. Fetch upstream
:: 3. Merge with conflict markers
:: 4. Show any conflicts for manual resolution

echo === OpenAlgo Upstream Sync ===
echo.

:: Create timestamp backup
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set backup_branch=backup-%datetime:~0,8%-%datetime:~8,4%

echo Creating backup branch: %backup_branch%
git branch %backup_branch%

echo.
echo Fetching upstream...
git fetch upstream

echo.
echo Merging upstream/main...
git merge upstream/main --no-commit

:: Check for conflicts
git diff --name-only --diff-filter=U > conflict_files.tmp
for /f %%A in ('type conflict_files.tmp ^| find /c /v ""') do set conflict_count=%%A
del conflict_files.tmp

if %conflict_count% GTR 0 (
    echo.
    echo === CONFLICTS DETECTED ===
    echo Please resolve the following files manually:
    git diff --name-only --diff-filter=U
    echo.
    echo After resolving, run: git add . ^&^& git commit -m "merge: sync upstream"
) else (
    echo.
    echo No conflicts! Committing...
    git commit -m "merge: sync with upstream"
    echo.
    echo Done! Push with: git push origin main
)

echo.
echo Backup saved to branch: %backup_branch%
pause
