# Safe Upstream Merge Runbook

Last updated: 2026-02-14  
Scope: Safe `upstream/main -> main` merge without losing local work

## 1) When To Use This

Use this runbook whenever you ask to:

1. fetch from upstream
2. merge upstream changes
3. keep local changes safe during conflicts

## 2) Non-Negotiable Safety Rules

1. Start only from a clean git working tree.
2. Create and push a timestamped backup branch before merge.
3. Resolve source conflicts first; do not hand-edit hashed `frontend/dist` bundle conflicts.
4. Never use destructive reset commands unless explicitly requested.

## 3) Preferred One-Command Flow

Use the helper script:

```powershell
pwsh -File .\scripts\safe-merge-upstream.ps1 -Branch main -BuildFrontendDist
```

Useful options:

1. `-NoPush`: merge locally but do not push yet.
2. `-PreferOursOnRemainingConflicts`: if you intentionally want local side for unresolved conflicts.
3. `-BuildFrontendDist`: rebuild and commit refreshed `frontend/dist` after merge.

## 4) What The Script Guarantees

1. Validates repo state (clean tree, no active merge/rebase, correct branch).
2. Ensures `upstream` remote is present and points to `https://github.com/marketcalls/openalgo.git`.
3. Fetches both `origin` and `upstream`.
4. Creates + pushes backup branch:
   - `backup/main-pre-upstream-YYYYMMDD-HHMMSS`
5. Merges `upstream/main` with conflict handling.
6. Auto-prefers local side for known generated conflicts:
   - `frontend/dist/*`
   - `static/css/main.css`
7. Optionally rebuilds `frontend/dist` and commits generated outputs.
8. Pushes `main` unless `-NoPush` is used.

## 5) Manual Conflict Policy (If Script Stops)

1. Resolve real code conflicts in source files (`frontend/src/**`, backend python, docs).
2. For generated bundle conflicts, keep local side and rebuild:
   - `git checkout --ours -- frontend/dist`
   - `cd frontend && npm run build`
   - `git add -A frontend/dist`
3. Complete merge commit:
   - `git commit`
4. Push:
   - `git push origin main`

## 6) Recovery Plan

If merge result is wrong:

1. Inspect backup branch created by script.
2. Restore or cherry-pick from backup branch.
3. Re-run merge with adjusted conflict strategy.

## 7) Quick Invocation Reminder

When you say:  
`fetch upstream and merge safely`  
the assistant should follow this runbook and use `scripts/safe-merge-upstream.ps1` by default.
