# Quick Resume Prompts

Last updated: 2026-02-14  
Purpose: Start a new session with minimal tokens and enough context.

## 1) Scalping Minimal

```text
Use skill: scalping-autotrade-copilot.
Read only:
1) C:/Users/ADMIN/.codex/skills/local/scalping-autotrade-copilot/SKILL.md
2) docs/memory/scalping-next-session-handover.md
Do not scan whole repo.

Task: <exact issue>
Expected: <expected behavior>
Error snippet: <1-3 lines>

Implement fix, run targeted checks, then report changed files.
```

## 2) TOMIC Minimal

```text
Use skill: tomic-runtime-copilot.
Read only:
1) C:/Users/ADMIN/.codex/skills/local/tomic-runtime-copilot/SKILL.md
2) docs/memory/scalping-next-session-handover.md
Do not scan whole repo.

Task: <exact issue>
Expected: <expected behavior>
Log snippet: <1-3 lines>

Implement fix and run targeted validation only.
```

## 3) Safe Upstream Merge Only

```text
Follow docs/design/safe-upstream-merge-runbook.md only.
Run scripts/safe-merge-upstream.ps1 with safe defaults.
Do not perform destructive git commands.
```

## 4) Micro Follow-Up Prompt

```text
Continue from last change only.
Read only modified files from previous commit.
Do not re-scan architecture docs unless needed.
```
