# Safe Upstream Sync Workflow

**Last Updated:** February 8, 2026

This guide ensures clean upstream syncs without losing custom work.

---

## Your Custom Files (Complete List)

### Category 1: 100% YOURS - Always Keep (No Conflicts)

These files are entirely your custom work. **Always use `--ours`**:

#### HTML Applications (Standalone)
- `auto_trading_window.html` - Main auto-trading interface (~12,800 lines)
- `scalping_interface.html` - Manual scalping dashboard
- `chart_window.html` - Chart trading window
- `mock_replay.html` - Mock replay interface
- `debug_scalping.html` - Debug tools

**Merge Strategy:** `git checkout --ours <file>` - upstream never touches these

#### Backend Services
- `services/ai_scalper/__init__.py`
- `services/ai_scalper/advisor.py`
- `services/ai_scalper/agent.py`
- `services/ai_scalper/config.py`
- `services/ai_scalper/execution_engine.py`
- `services/ai_scalper/feature_cache.py`
- `services/ai_scalper/learning.py`
- `services/ai_scalper/log_store.py`
- `services/ai_scalper/manager.py`
- `services/ai_scalper/model_tuner.py`
- `services/ai_scalper/model_tuner_scheduler.py`
- `services/ai_scalper/playbooks.py`
- `services/ai_scalper/risk_engine.py`
- `services/manual_trade_log_store.py`
- `services/mock_replay/__init__.py`
- `services/mock_replay/replay_engine.py`
- `services/mock_replay/synthetic_options.py`

**Merge Strategy:** `git checkout --ours services/ai_scalper/` - entire folder is yours

#### Blueprints
- `blueprints/ai_scalper.py` - AI scalper routes
- `blueprints/manual_trades.py` - Manual trade routes
- `blueprints/mock_replay.py` - Mock replay routes
- `blueprints/scalping.py` - Scalping routes

**Merge Strategy:** `git checkout --ours blueprints/ai_scalper.py` etc.

#### Scripts
- `scripts/historify_replay_server.py` - Mock replay WebSocket server
- `scripts/mock_websocket_server.py` - Mock WebSocket utilities

**Merge Strategy:** `git checkout --ours scripts/historify_replay_server.py`

#### Database Modules
- `database/historify_db.py` - Historical data queries
- `database/mock_trading_db.py` - Mock trading storage

**Merge Strategy:** `git checkout --ours database/historify_db.py`

#### Documentation
- `docs/SCALPING_FRAMEWORK_DOCUMENTATION.md` - Complete framework docs
- `docs/SCALPING_FEATURES_MANUAL.md` - Features manual
- `docs/design/auto-trade-log-feedback-and-conditions.md`
- `docs/design/mock-websocket-historify-replay.md`
- `docs/design/mock-websocket-testing.md`
- `docs/audit/auto-trade-log-feedback-2026-02-06.md`
- `docs/images/depth-scout.svg`
- `docs/images/scalp-radar.svg`
- `docs/images/signal-stack.svg`
- `TRADE_LOGGING_ANALYTICS.md` - Analytics queries
- `SNIPER_SCALPER_PRESET_PLAN.md` - Preset specifications
- `ai_scalper_architecture.md` - Architecture overview
- `SCALPING_INTERFACE_README.md` - Interface guide
- `BROWSER_SETUP.md` - Browser configuration
- `MULTI_INSTANCE_SETUP.md` - Multi-instance guide
- `PERFORMANCE_OPTIMIZATIONS.md` - Performance guide
- `chart_implementation_plan.md` - Chart trading plan
- `Chart Trading WebSocket Fix.md` - WebSocket fixes

**Merge Strategy:** `git checkout --ours docs/` - all your docs

#### Helper Scripts
- `run_mock.bat` - Run mock mode
- `run_replay_server.bat` - Run replay server
- `run_dhan.bat` - Run with Dhan
- `run_kotak.bat` - Run with Kotak
- `run_zerodha.bat` - Run with Zerodha
- `SETUP_SECOND_INSTANCE.bat` - Second instance setup
- `START_BOTH.bat` - Start multiple instances
- `env_bootstrap.bat` - Environment setup
- `SYNC_MANUAL.txt` - Manual sync reference
- `clean_sync_upstream.ps1` - Automated sync script
- `validate_merge.py` - Import validation
- `UPSTREAM_SYNC_GUIDE.md` - This guide

**Merge Strategy:** `git checkout --ours *.bat` - all helper scripts

#### React Pages (Your Analytics)
- `frontend/src/pages/AutoTradeAnalytics.tsx` - Auto-trade analytics page
- `frontend/src/pages/AutoTradeModelTuning.tsx` - Model tuning page
- `frontend/src/pages/ManualTradeAnalytics.tsx` - Manual trade analytics page

**Merge Strategy:** `git checkout --ours frontend/src/pages/AutoTrade*.tsx`

#### React API Files (Your Functions)
- `frontend/src/api/ai-scalper.ts` - AI scalper API functions
- `frontend/src/api/manual-trades.ts` - Manual trades API functions
- `frontend/src/types/ai-scalper.ts` - Type definitions

**Merge Strategy:** `git checkout --ours frontend/src/api/ai-scalper.ts`

---

### Category 2: MIXED - Needs Manual Merge

These files have BOTH your changes AND upstream changes. **Requires careful handling:**

#### `app.py` - Blueprint Registrations
**Your Changes:**
```python
# Lines ~62-65: Your blueprint imports
from blueprints.ai_scalper import ai_scalper_bp
from blueprints.mock_replay import mock_replay_bp
from blueprints.manual_trades import manual_trades_bp
from blueprints.scalping import scalping_bp

# Lines ~200+: Your blueprint registrations
app.register_blueprint(ai_scalper_bp, url_prefix='/ai_scalper')
app.register_blueprint(mock_replay_bp, url_prefix='/mock_replay')
app.register_blueprint(manual_trades_bp, url_prefix='/manual_trades')
app.register_blueprint(scalping_bp, url_prefix='/scalping')
```

**Merge Strategy:**
1. Use YOURS: `git checkout --ours app.py`
2. If upstream added critical app-level changes, manually review with:
   ```powershell
   git show upstream/main:app.py > app.py.upstream
   code --diff app.py app.py.upstream
   ```
3. Add any critical upstream changes (rare)

#### `database/user_db.py` - Custom Functions
**Your Changes:**
```python
# Line ~142: Your custom function
def get_email_by_username(username):
    """Get email address for a username (for AI scalper notifications)"""
    user = User.query.filter_by(username=username).first()
    return user.email if user else None
```

**Merge Strategy:**
1. Use YOURS: `git checkout --ours database/user_db.py`
2. Run `validate_merge.py` to check for missing functions
3. If upstream removed functions you need, add them back manually

#### `frontend/src/App.tsx` - Route Registrations
**Your Changes:**
```tsx
// Lines ~103-105: Your lazy imports
const AutoTradeAnalytics = lazy(() => import('@/pages/AutoTradeAnalytics'))
const AutoTradeModelTuning = lazy(() => import('@/pages/AutoTradeModelTuning'))
const ManualTradeAnalytics = lazy(() => import('@/pages/ManualTradeAnalytics'))

// Lines ~207-209: Your routes
<Route path="/auto-trade/analytics" element={<AutoTradeAnalytics />} />
<Route path="/auto-trade/model-tuning" element={<AutoTradeModelTuning />} />
<Route path="/manual-trades/analytics" element={<ManualTradeAnalytics />} />
```

**Merge Strategy:**
1. Use YOURS: `git checkout --ours frontend/src/App.tsx`
2. Upstream routes are additive - yours won't conflict
3. Rebuild frontend to get all upstream pages too

#### `frontend/src/pages/Tools.tsx` - Tool Cards
**Your Changes:**
```tsx
// Lines ~61-77: Your tool cards
{
  title: 'Auto-Trade Analytics',
  description: 'AI Scalper performance analytics...',
  href: '/auto-trade/analytics',
  color: 'bg-purple-500',
},
{
  title: 'Auto-Trade Model Tuning',
  description: 'AI-powered strategy optimization...',
  href: '/auto-trade/model-tuning',
  color: 'bg-fuchsia-500',
},
{
  title: 'Manual Trade Analytics',
  description: 'Manual scalping performance...',
  href: '/manual-trades/analytics',
  color: 'bg-pink-500',
},
```

**Merge Strategy:**
1. Use YOURS: `git checkout --ours frontend/src/pages/Tools.tsx`
2. Upstream may add new tools - yours are additive
3. No conflicts expected

#### Broker Files - Custom Fixes
**Your Changes:**
- `broker/dhan/api/order_api.py` - Custom order handling fixes
- `broker/dhan/mapping/order_data.py` - Custom order mapping
- `broker/kotak/api/order_api.py` - Custom order handling fixes
- `broker/kotak/mapping/order_data.py` - Custom order mapping

**Merge Strategy:**
1. **Check if your changes are bug fixes or custom features:**
   ```powershell
   git diff upstream/main...main broker/dhan/api/order_api.py
   ```
2. **If bug fixes:** Use YOURS, then submit PR to upstream
   ```powershell
   git checkout --ours broker/dhan/api/order_api.py
   ```
3. **If custom features:** Use YOURS and document why
4. **If unsure:** Use THEIRS and re-apply your fixes manually after testing

**Note:** Broker files are actively maintained by upstream. Your changes may break when they update broker APIs. Consider:
- Documenting your changes in comments
- Submitting bug fixes upstream via PR
- Keeping custom changes minimal

---

### Category 3: NEVER COMMIT - Build Artifacts

These should stay in `.gitignore`:

- `frontend/dist/**` - React build output (rebuilt every time)
- `static/css/main.css` - Tailwind build output (rebuilt every time)
- `__pycache__/` - Python bytecode
- `.env.dhan`, `.env.kotak`, `.env.zerodha` - Broker credentials
- `*.pyc` - Python compiled files
- `node_modules/` - NPM dependencies

**Merge Strategy:** If conflicts appear, **delete and rebuild**:
```powershell
git rm frontend/dist/assets/*conflicting*.js
cd frontend && npm run build && cd ..
```

---

### Category 4: UPSTREAM ONLY - Always Theirs

These files you never modify. **Always use `--theirs`**:

- Core utilities: `utils/*.py` (except custom ones)
- Core services: `services/telegram_alert_service.py`, `services/place_order_service.py`
- Broker integrations: `broker/*/api/*.py` (unless you added custom fixes)
- Frontend infrastructure: `frontend/src/components/**`, `frontend/src/lib/**`
- All files in `frontend/src/pages/` EXCEPT your 3 analytics pages

**Merge Strategy:** `git checkout --theirs <file>`

---

## Safe Sync Workflow (3 Steps)

### Step 1: Backup and Fetch
```powershell
# Create backup
git branch backup/sync-$(Get-Date -Format "yyyyMMdd-HHmm")

# Fetch upstream
git fetch upstream

# Check what's new
git log --oneline main..upstream/main
```

### Step 2: Merge (NOT Rebase)
```powershell
# Simple merge - keeps your work, adds upstream
git merge upstream/main -m "merge: sync with upstream"

# If conflicts appear, handle them (see below)
```

### Step 3: Validate and Rebuild
```powershell
# Validate imports
uv run python validate_merge.py

# Fix any import errors it finds

# Rebuild frontend
cd frontend
npm install
npm run build
cd ..

# Test the app
uv run app.py

# Commit and push
git add .
git commit -m "chore: rebuild after upstream merge"
git push origin main
```

---

## Handling Conflicts

### If `app.py` Conflicts:
```powershell
# Use YOURS (has your blueprints)
git checkout --ours app.py

# Then manually add any NEW blueprints from upstream if needed
# (Usually none - upstream doesn't add blueprints often)
```

### If `frontend/src/App.tsx` Conflicts:
```powershell
# Use OURS (has your routes)
git checkout --ours frontend/src/App.tsx

# Rebuild will work fine
```

### If `frontend/dist/**` Conflicts:
```powershell
# DELETE all conflicting build files
git rm frontend/dist/assets/*conflicted*.js

# Accept upstream's dist (will be replaced anyway)
git checkout --theirs frontend/dist/

# Then rebuild fresh
cd frontend && npm run build && cd ..
```

### If `database/user_db.py` or other core files conflict:
```powershell
# Check what changed
git diff upstream/main...main database/user_db.py

# If upstream REMOVED a function you use:
# 1. Use YOURS: git checkout --ours database/user_db.py
# 2. Run validate_merge.py to find any new issues
# 3. Add missing functions manually if needed
```

---

## Frontend Special Handling

**NEVER commit `frontend/dist/`** - it's build output.

After any merge:
1. Get upstream's `frontend/src/` (except your custom pages)
2. Keep your custom pages: `AutoTradeAnalytics.tsx`, `AutoTradeModelTuning.tsx`, `ManualTradeAnalytics.tsx`
3. Rebuild: `cd frontend && npm run build`

Your custom API files that need preserving:
- `frontend/src/api/ai-scalper.ts` - Has your API functions
- `frontend/src/api/manual-trades.ts` - Has your API functions
- `frontend/src/types/ai-scalper.ts` - Has your types

---

## Quick Reference Commands

### Before Sync
```powershell
# Backup
git branch backup/sync-$(Get-Date -Format "yyyyMMdd-HHmm")
git fetch upstream
```

### Merge
```powershell
# Simple merge
git merge upstream/main -m "merge: sync with upstream"
```

### Fix Conflicts (if any)
```powershell
# Use YOUR version for custom files
git checkout --ours app.py
git checkout --ours frontend/src/App.tsx

# Use THEIR version for frontend build
git checkout --theirs frontend/dist/

# Remove conflicting build files
git status --short | Select-String "^UU.*frontend/dist" | ForEach-Object {
    $file = $_.Line.Substring(3).Trim()
    git rm $file
}

# Stage and continue
git add .
git commit -m "merge: resolve conflicts"
```

### Validate
```powershell
uv run python validate_merge.py
```

### Rebuild Frontend
```powershell
cd frontend
npm install
npm run build
cd ..
git add frontend/dist/
git commit -m "chore: rebuild frontend"
```

### Push
```powershell
git push origin main
```

---

## What If Something Goes Wrong?

### Abort Merge
```powershell
git merge --abort
```

### Restore from Backup
```powershell
# Find your backup
git branch | Select-String "backup/sync"

# Reset to backup
git reset --hard backup/sync-YYYYMMDD-HHMM
```

### Nuclear Option - Start Fresh
```powershell
# Reset to your last known good state
git reset --hard origin/main

# Or reset to backup and force push
git reset --hard backup/sync-YYYYMMDD-HHMM
git push origin main --force
```

---

## Tips for Clean Merges

✅ **DO:**
- Merge frequently (weekly) - smaller changes = easier merges
- Always create backup branch first
- Use `git merge` (NOT `git rebase`)
- Run validation script after merge
- Test the app before pushing

❌ **DON'T:**
- Never use `git rebase upstream/main` - causes commit replay mess
- Don't skip frontend rebuild after merge
- Don't commit `frontend/dist/` manually (it's gitignored for a reason)
- Don't use `git checkout --ours` on pure upstream files
- Don't panic - backups are there!

---

## When to Ask for Help

🆘 If you see:
- Import errors after validation that you can't fix
- App won't start after merge
- More than 10 conflicting files
- Lost files that aren't in any backup

→ **Stop, backup your current state, and ask before proceeding**

---

## Success Checklist

After merge, verify:
- [ ] `uv run python validate_merge.py` passes
- [ ] `uv run app.py` starts without errors
- [ ] `/tools` shows all 12 tools including your analytics
- [ ] `/auto-trade/analytics` loads
- [ ] `/manual-trades/analytics` loads
- [ ] Auto trading window still works
- [ ] Mock replay still works

---

## File Conflict Quick Decision Tree

```
Is file in frontend/dist/?
├─ YES → Delete it, rebuild will recreate
└─ NO → Continue

Is file one of YOUR custom files?
├─ YES → Use YOURS (git checkout --ours)
└─ NO → Continue

Is file app.py, App.tsx, or Tools.tsx?
├─ YES → Use YOURS, manually merge later if needed
└─ NO → Use THEIRS (git checkout --theirs)
```

---

**Remember:** Merges are safer than rebases. Your custom work is in separate files that rarely conflict. When in doubt, use YOUR version and rebuild!
