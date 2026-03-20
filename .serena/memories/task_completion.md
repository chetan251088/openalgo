# Task Completion Checklist

After implementing a task, perform these steps:

## 1. Code Quality
- Verify syntax and imports are correct
- Ensure docstrings follow Google style
- Check type hints are present where applicable
- No commented-out code or debug prints

## 2. Testing (if applicable)
```bash
uv run pytest test/ -v
```

## 3. Git Commit
```bash
git add <modified_files>
git commit -m "feat(module): description

Optional longer description explaining the change."
```

## 4. For Task 8 specifically
- No TDD required (plan has no test step)
- File created: `services/market_pulse_analyst.py`
- Content: ~230 lines, provider-agnostic LLM analyst
- Supports: Claude, OpenAI, Gemini, Ollama
- Features: Caching (5-min TTL), fallback analysis, non-blocking API
- Commit message: "feat(market-pulse): add provider-agnostic LLM analyst layer"

## 5. Verification
- File exists at correct path
- Imports resolve (no ModuleNotFoundError when imported)
- Commit shows in git log
