# Safe Upstream Sync Workflow

**Last Updated:** March 16, 2026

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

### Category 3: GENERATED OUTPUT / LOCAL ARTIFACTS

These need different handling:

- `frontend/dist/**` - generated output, but **tracked in this repo**
- `static/css/main.css` - generated output if present
- `__pycache__/` - Python bytecode, never commit
- `.env.dhan`, `.env.kotak`, `.env.zerodha` - broker credentials, never commit
- `*.pyc` - compiled files, never commit
- `node_modules/` - dependency cache, never commit

**Merge Strategy for `frontend/dist/**`:** do not hand-merge hashed assets. Resolve source code first, then rebuild and stage the rebuilt output:
```powershell
cd frontend
npm run build
cd ..
git add -A frontend/dist
```

---

### Category 4: UPSTREAM ONLY - Always Theirs

These files are usually upstream-owned, but this list is no longer absolute. **Review before using `--theirs`**:

- Core utilities that you have not customized
- Broker integrations you have not patched locally
- Frontend infrastructure files you have not extended locally
- Upstream-only docs / installer files where you have no custom behavior

**Do not blindly use `--theirs`** on these files anymore:
- `services/place_order_service.py`
- `services/options_multiorder_service.py`
- `utils/logging.py`
- `websocket_proxy/server.py`
- `restx_api/schemas.py`
- `app.py`

These now carry real local behavior and need manual review during upstream merges.

---

## Safe Sync Workflow (Used on 2026-03-15)

This is the exact workflow used to merge `upstream/main` into local `main` safely while preserving local scalping, unified trading, TOMIC, and intelligence work.

### Step 1: Preflight and Inspect Divergence
```powershell
git status --short --branch
git remote -v
git branch -vv

git fetch upstream

git rev-list --left-right --count main...upstream/main
git log --oneline --decorate --no-merges main..upstream/main
git log --oneline --decorate --no-merges upstream/main..main
```

Why:
- Confirms local `main` is in the state you expect before touching history.
- Shows whether this is a small sync or a true multi-branch integration.
- Lets you see upstream-only vs local-only commits before conflict resolution.

### Step 2: Create a Safety Backup Branch
```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
git branch "backup/main-pre-upstream-merge-$stamp" main
```

Do this every time. If the merge goes sideways, you want a named branch pointing at the pre-merge state.

### Step 3: Start the Merge Without Auto-Committing
```powershell
git merge --no-commit --no-ff upstream/main
```

Why this matters:
- `--no-commit` leaves conflict state in the worktree so you can resolve carefully.
- `--no-ff` guarantees a merge commit, which keeps the sync point explicit.
- This is much safer for this repo than doing a blind `git merge upstream/main -m ...`.

### Step 4: Resolve Source Conflicts First

Before touching generated files, find the real code conflicts:

```powershell
git diff --name-only --diff-filter=U
rg -n "^(<<<<<<<|=======|>>>>>>>)" app.py restx_api/schemas.py services/options_multiorder_service.py services/place_order_service.py utils/logging.py websocket_proxy/server.py
```

These were the important source conflict decisions in the March 2026 merge:

#### `app.py`
- Keep local blueprint/runtime wiring.
- Keep local `subscribers.register_all()` bootstrap.
- Keep local intelligence/TOMIC/unified work.
- Also take upstream `custom_straddle_bp` import and registration.

#### `restx_api/schemas.py`
- Keep local schema validation logic including `validates_schema`.
- Also take upstream quantity normalization for non-crypto exchanges:
  - `post_load`
  - `CRYPTO_EXCHANGES`
  - `_coerce_quantity_to_int(...)`

#### `services/options_multiorder_service.py`
- Keep local direct-symbol options-leg support.
- Keep local `get_option_exchange(...)`.
- Keep local payload handling around resolved exchange / option type.
- Also take upstream event bus imports:
  - `AnalyzerErrorEvent`
  - `MultiOrderCompletedEvent`

#### `services/place_order_service.py`
- Keep local hardening for broker adapters that return `res=None` or omit `status`.
- Keep local `response_status` normalization.
- Also keep upstream event-bus behavior:
  - `OrderPlacedEvent`
  - `OrderFailedEvent`
- Result: local transport safety + upstream event publishing.

#### `utils/logging.py`
- Keep local recursive sensitive-value sanitizer `_sanitize_value(...)`.
- Keep upstream type hints / docstring improvements on `filter(...)`.

#### `websocket_proxy/server.py`
- Keep local `_resolve_market_data_metadata(...)` payload-aware routing.
- Do not replace it with the simpler upstream topic-only parser.
- This matters for broker compatibility and multi-broker routing.

### Step 5: Treat `frontend/dist` as Generated but Tracked

Important correction to older guidance:

- `frontend/dist/` is tracked in this repo.
- Do not hand-merge hashed bundle filenames.
- Do not assume `frontend/dist` is gitignored.
- Resolve source first, then rebuild `dist` from the merged frontend source tree.

Useful checks:
```powershell
git diff --name-only --diff-filter=U -- frontend/dist
```

What worked:

1. Try to keep local versions for dist files that actually exist on our side.
2. Remove upstream-only stale hash files instead of trying to preserve both graphs.
3. Rebuild from source.
4. Stage the rebuilt `frontend/dist`.

For a large hashed-file conflict set, this PowerShell loop is practical:

```powershell
$paths = git diff --name-only --diff-filter=U -- frontend/dist
foreach ($path in $paths) {
    git checkout --ours -- "$path" 2>$null
    $stillUnmerged = git diff --name-only --diff-filter=U -- "$path"
    if ($stillUnmerged) {
        if (Test-Path $path) {
            Remove-Item $path -Force -ErrorAction SilentlyContinue
        }
        git rm -f --ignore-unmatch -- "$path" 2>$null | Out-Null
    } else {
        git add -- "$path"
    }
}
```

Then rebuild:

```powershell
cd frontend
npm run build
cd ..
git add -A frontend/dist
```

### Step 6: Sync Python Environment
```powershell
uv sync
```

Use `uv sync` after the merge because upstream may have changed:
- `pyproject.toml`
- `uv.lock`
- transitive versions like `PyJWT`, `tornado`, etc.

### Step 7: Do a Compile-Only Sanity Check

On Windows, `py_compile` can fail if `__pycache__` or temporary `.pyc` outputs are locked. A compile-only check avoids that:

```powershell
@'
from pathlib import Path
files = [
    "app.py",
    "restx_api/schemas.py",
    "services/options_multiorder_service.py",
    "services/place_order_service.py",
    "utils/logging.py",
    "websocket_proxy/server.py",
]
for file in files:
    source = Path(file).read_text(encoding="utf-8")
    compile(source, file, "exec")
print("compile-ok")
'@ | .venv\Scripts\python.exe -
```

### Step 8: Commit the Merge
```powershell
git commit -m "Merge upstream/main into main"
```

### Step 9: Push
```powershell
git push origin main
```

---

## Conflict Handling Cheat Sheet

### If `app.py` Conflicts
- Do not blindly take `--ours` or `--theirs`.
- Start from local app wiring.
- Re-introduce any truly new upstream imports / blueprint registrations manually.
- In the March 2026 merge, `custom_straddle_bp` was the upstream addition worth keeping.

### If `services/place_order_service.py` Conflicts
- Keep local transport hardening.
- Keep upstream event publishing.
- Do not regress back to assuming every broker adapter returns `res.status`.

### If `websocket_proxy/server.py` Conflicts
- Prefer local payload-aware metadata resolution.
- Upstream's simpler topic parser may drop broker-specific or payload-derived metadata.

### If `frontend/dist/**` Conflicts
- Do not hand-edit hashed files.
- Do not keep both old and new hash sets.
- Use the merged `frontend/src/**` tree as truth.
- Rebuild and stage `frontend/dist`.

### If `uv sync` Fails on Windows Cache Permissions
- Retry with elevated permissions.
- This happened during the March 2026 merge because `uv` cache access was blocked in the default sandbox.

---

## Frontend Special Handling

For this repo:

- `frontend/src/**` is the source of truth.
- `frontend/dist/**` is tracked output that must be regenerated after major merges.
- `frontend/dist/**` should not be hand-merged.
- Rebuild after conflict resolution and then stage the rebuilt output.

Recommended flow:

```powershell
cd frontend
npm run build
cd ..
git add -A frontend/dist
```

---

## Quick Reference Commands

### Preflight
```powershell
git status --short --branch
git remote -v
git branch -vv
git fetch upstream
git rev-list --left-right --count main...upstream/main
```

### Backup
```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
git branch "backup/main-pre-upstream-merge-$stamp" main
```

### Merge Without Auto-Commit
```powershell
git merge --no-commit --no-ff upstream/main
```

### Inspect Conflicts
```powershell
git diff --name-only --diff-filter=U
rg -n "^(<<<<<<<|=======|>>>>>>>)" app.py restx_api/schemas.py services/options_multiorder_service.py services/place_order_service.py utils/logging.py websocket_proxy/server.py
```

### Rebuild and Sync
```powershell
cd frontend
npm run build
cd ..

uv sync
```

### Finalize
```powershell
git commit -m "Merge upstream/main into main"
git push origin main
```

---

## What If Something Goes Wrong?

### Abort the Merge Before Commit
```powershell
git merge --abort
```

### Restore to the Backup Branch
```powershell
git branch | Select-String "backup/main-pre-upstream-merge"
git reset --hard backup/main-pre-upstream-merge-YYYYMMDD-HHMMSS
```

Do this only if you explicitly want to discard the in-progress merge state.

---

## Tips for Clean Merges

✅ **DO:**
- Create a backup branch first
- Inspect divergence before merging
- Use `git merge --no-commit --no-ff upstream/main`
- Resolve source conflicts before touching `frontend/dist`
- Rebuild frontend after merge
- Run `uv sync` after dependency changes
- Run a compile-only sanity check on conflict-resolved Python files

❌ **DON'T:**
- Don't use `git rebase upstream/main`
- Don't do a blind auto-commit merge when the branches have diverged heavily
- Don't hand-merge hashed `frontend/dist` artifacts
- Don't assume `frontend/dist` is untracked in this repo
- Don't skip the backup branch

---

## Success Checklist

After merge, verify:
- [ ] `git diff --name-only --diff-filter=U` returns nothing
- [ ] `npm run build` passes in `frontend/`
- [ ] `uv sync` completes
- [ ] conflict-resolved Python files compile successfully
- [ ] `git status --short --branch` is clean except expected ahead/behind relation
- [ ] `/scalping-unified` loads
- [ ] websocket market data still flows
- [ ] custom straddle / tools pages still load

---

## File Conflict Quick Decision Tree

```text
Is the conflict in source code?
├─ YES → Resolve manually from both sides
└─ NO → Continue

Is it in frontend/dist with hashed filenames?
├─ YES → Do not hand-merge; rebuild dist from source
└─ NO → Continue

Is it one of your custom workflow files?
├─ YES → Start from OURS, then add any real upstream improvements manually
└─ NO → Compare both sides before choosing
```

---

**Remember:** The safest pattern for this repo is backup branch -> fetch -> inspect divergence -> `merge --no-commit` -> resolve source carefully -> rebuild `frontend/dist` -> `uv sync` -> compile check -> commit -> push.
