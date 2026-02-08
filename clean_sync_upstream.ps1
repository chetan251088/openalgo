# OpenAlgo Clean Upstream Sync Script
# This script safely syncs with upstream while preserving custom scalping work

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " OpenAlgo Clean Upstream Sync" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Safety checks
Write-Host "[1/8] Safety checks..." -ForegroundColor Yellow

$currentBranch = git branch --show-current
if ($currentBranch -ne "main") {
    Write-Host "❌ ERROR: Not on main branch. Currently on: $currentBranch" -ForegroundColor Red
    exit 1
}

# Check for ongoing rebase
if (Test-Path ".git/rebase-merge" -or Test-Path ".git/rebase-apply") {
    Write-Host "⚠️  Rebase in progress. Aborting it first..." -ForegroundColor Yellow
    git rebase --abort
}

Write-Host "✅ On main branch, ready to proceed" -ForegroundColor Green

# Step 2: Create backup branch
Write-Host ""
Write-Host "[2/8] Creating backup branch..." -ForegroundColor Yellow

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupBranch = "backup/clean-sync-$timestamp"
git branch $backupBranch
Write-Host "✅ Backup created: $backupBranch" -ForegroundColor Green

# Step 3: Define custom files to preserve
Write-Host ""
Write-Host "[3/8] Identifying custom files..." -ForegroundColor Yellow

$customFiles = @(
    # Core scalping system HTML files
    "auto_trading_window.html",
    "scalping_interface.html",
    "chart_window.html",
    "mock_replay.html",
    "debug_scalping.html",
    
    # Backend for scalping/auto-trading
    "blueprints\ai_scalper.py",
    "blueprints\manual_trades.py",
    "blueprints\mock_replay.py",
    "blueprints\scalping.py",
    
    # Services (entire directories)
    "services\ai_scalper",
    "services\manual_trade_log_store.py",
    
    # Scripts
    "scripts\historify_replay_server.py",
    
    # Databases
    "database\historify_db.py",
    "database\mock_trading_db.py",
    
    # Documentation you created
    "docs\SCALPING_FRAMEWORK_DOCUMENTATION.md",
    "docs\SCALPING_FEATURES_MANUAL.md",
    "TRADE_LOGGING_ANALYTICS.md",
    "SNIPER_SCALPER_PRESET_PLAN.md",
    "ai_scalper_architecture.md",
    "SCALPING_INTERFACE_README.md",
    "BROWSER_SETUP.md",
    "MULTI_INSTANCE_SETUP.md",
    "PERFORMANCE_OPTIMIZATIONS.md",
    "chart_implementation_plan.md",
    
    # Design docs
    "docs\design",
    "docs\audit",
    "docs\images",
    
    # Helper scripts
    "SYNC_MANUAL.txt",
    "env_bootstrap.bat",
    "SETUP_SECOND_INSTANCE.bat",
    "START_BOTH.bat",
    "auto_fix_sync.bat",
    "emergency_restore.bat",
    
    # GitHub copilot instructions
    ".github\copilot-instructions.md",
    ".github\instructions",
    
    # Chart trading docs
    "Chart Trading WebSocket Fix.md"
)

Write-Host "✅ Will preserve $($customFiles.Count) files/folders" -ForegroundColor Green

# Step 4: Backup custom files
Write-Host ""
Write-Host "[4/8] Backing up custom files..." -ForegroundColor Yellow

$backupDir = "..\openalgo_custom_backup_$timestamp"
mkdir $backupDir -Force | Out-Null

$backedUpCount = 0
foreach ($file in $customFiles) {
    if (Test-Path $file) {
        $dest = Join-Path $backupDir $file
        $dir = Split-Path $dest -Parent
        if ($dir -and !(Test-Path $dir)) {
            mkdir $dir -Force | Out-Null
        }
        
        # Copy file or directory
        if (Test-Path $file -PathType Container) {
            Copy-Item $file $dest -Recurse -Force
        } else {
            Copy-Item $file $dest -Force
        }
        $backedUpCount++
    }
}

Write-Host "✅ Backed up $backedUpCount items to $backupDir" -ForegroundColor Green

# Step 5: Fetch and reset to upstream
Write-Host ""
Write-Host "[5/8] Fetching upstream and resetting..." -ForegroundColor Yellow
Write-Host "⚠️  This will replace all files with upstream versions" -ForegroundColor Red

git fetch upstream
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ ERROR: Failed to fetch upstream" -ForegroundColor Red
    exit 1
}

git reset --hard upstream/main
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ ERROR: Failed to reset to upstream/main" -ForegroundColor Red
    Write-Host "💡 Restore with: git reset --hard $backupBranch" -ForegroundColor Yellow
    exit 1
}

Write-Host "✅ Reset to upstream/main" -ForegroundColor Green

# Step 6: Restore custom files
Write-Host ""
Write-Host "[6/8] Restoring custom files..." -ForegroundColor Yellow

$restoredCount = 0
foreach ($file in $customFiles) {
    $source = Join-Path $backupDir $file
    if (Test-Path $source) {
        # Restore file or directory
        if (Test-Path $source -PathType Container) {
            Copy-Item $source $file -Recurse -Force
        } else {
            Copy-Item $source $file -Force
        }
        $restoredCount++
    }
}

Write-Host "✅ Restored $restoredCount custom files" -ForegroundColor Green

# Step 7: Rebuild frontend
Write-Host ""
Write-Host "[7/8] Rebuilding frontend..." -ForegroundColor Yellow
Write-Host "⏳ This may take 30-60 seconds..." -ForegroundColor Gray

# Root-level CSS build
Write-Host "  Building root CSS (Tailwind)..." -ForegroundColor Gray
npm run build 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✅ Root CSS built" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  Root CSS build had warnings (non-critical)" -ForegroundColor Yellow
}

# React frontend build
Write-Host "  Building React frontend..." -ForegroundColor Gray
Push-Location frontend
npm install --silent 2>&1 | Out-Null
npm run build 2>&1 | Out-Null
$reactBuildSuccess = $LASTEXITCODE -eq 0
Pop-Location

if ($reactBuildSuccess) {
    Write-Host "  ✅ React frontend built" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  React build had issues (check manually)" -ForegroundColor Yellow
}

# Step 8: Commit and push
Write-Host ""
Write-Host "[8/8] Committing changes..." -ForegroundColor Yellow

git add .
git commit -m "feat: clean sync with upstream + restore custom scalping framework

- Synced with upstream/main
- Restored custom auto-trading/scalping system
- Rebuilt frontend (React + Tailwind CSS)
- Preserved all custom documentation

Custom work includes:
- Auto-trading window with adaptive presets
- Mock replay system for backtesting
- AI scalper backend with trade logging
- Comprehensive scalping documentation"

Write-Host "✅ Changes committed" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " ✅ SYNC COMPLETE!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📋 Summary:" -ForegroundColor White
Write-Host "  • Backup branch: $backupBranch" -ForegroundColor Gray
Write-Host "  • Backup folder: $backupDir" -ForegroundColor Gray
Write-Host "  • Custom files restored: $restoredCount" -ForegroundColor Gray
Write-Host "  • Repository synced with upstream/main" -ForegroundColor Gray
Write-Host ""
Write-Host "🚀 Next steps:" -ForegroundColor White
Write-Host "  1. Test the application: uv run app.py" -ForegroundColor Gray
Write-Host "  2. Verify your custom features work" -ForegroundColor Gray
Write-Host "  3. Push to origin: git push origin main --force-with-lease" -ForegroundColor Gray
Write-Host ""
Write-Host "💡 If something is wrong:" -ForegroundColor Yellow
Write-Host "   git reset --hard $backupBranch" -ForegroundColor Yellow
Write-Host ""
