"""
Provider-agnostic LLM analyst for Market Pulse.
Generates plain-English market commentary from scores and data.
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# ── In-memory cache for analyst output ──────────────────────────
# Cache is keyed by mode (swing/day) so switching modes doesn't serve stale text.
# Design spec says analyst refreshes independently on a 5-min cycle; the caller
# should return the cached value immediately and NOT block on LLM if cache exists.
_analyst_cache: dict[str, str] = {}        # mode → text
_analyst_cache_ts: dict[str, float] = {}   # mode → timestamp
_ANALYST_TTL = int(os.getenv("ANALYST_REFRESH_SECONDS", "300"))  # 5 min


def _is_analyst_cache_valid(mode: str) -> bool:
    ts = _analyst_cache_ts.get(mode, 0)
    return (time.time() - ts) < _ANALYST_TTL and mode in _analyst_cache


# ── LLM Provider Interface ──────────────────────────────────────

class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system_prompt: str) -> str:
        ...


class ClaudeProvider(LLMProvider):
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.getenv("LLM_API_KEY"))
        self.model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    def generate(self, prompt: str, system_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


class OpenAIProvider(LLMProvider):
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL") or None,
        )
        self.model = os.getenv("LLM_MODEL", "gpt-4o")

    def generate(self, prompt: str, system_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content


class GeminiProvider(LLMProvider):
    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("LLM_API_KEY"))
        self.model = genai.GenerativeModel(os.getenv("LLM_MODEL", "gemini-pro"))

    def generate(self, prompt: str, system_prompt: str) -> str:
        response = self.model.generate_content(f"{system_prompt}\n\n{prompt}")
        return response.text


class OllamaProvider(LLMProvider):
    def __init__(self):
        self.base_url = os.getenv("LLM_BASE_URL", "http://localhost:11434")
        self.model = os.getenv("LLM_MODEL", "llama3")

    def generate(self, prompt: str, system_prompt: str) -> str:
        import requests
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "system": system_prompt,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60,
        )
        return resp.json().get("response", "")


# ── Provider Factory ────────────────────────────────────────────

_PROVIDERS = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def _get_provider() -> LLMProvider | None:
    provider_name = os.getenv("LLM_PROVIDER", "").lower()
    if not provider_name or not os.getenv("LLM_API_KEY"):
        return None
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        logger.warning("Unknown LLM provider: %s", provider_name)
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning("Failed to init LLM provider %s: %s", provider_name, e)
        return None


# ── System Prompt ───────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Indian equity market analyst providing concise, actionable commentary for a swing/day trader. Your analysis must:

1. Reference Indian benchmarks: Nifty 50, Bank Nifty, India VIX, USDINR, sector indices
2. Tie back to the Market Quality Score and Execution Window Score
3. Explain the Decision (YES/CAUTION/NO) in plain English
4. Highlight which scoring categories are driving the decision
5. Note any risks or opportunities in the current environment
6. Be concise: 3-5 sentences max

Speak like a Bloomberg terminal analyst — direct, professional, data-driven. No hedging or disclaimers."""


def _build_prompt(pulse_data: dict[str, Any]) -> str:
    """Build the analyst prompt from market pulse data."""
    scores = pulse_data.get("scores", {})
    lines = [
        f"Decision: {pulse_data.get('decision', 'N/A')}",
        f"Market Quality Score: {pulse_data.get('market_quality_score', 'N/A')}/100",
        f"Execution Window Score: {pulse_data.get('execution_window_score', 'N/A')}/100",
        f"Mode: {pulse_data.get('mode', 'swing')}",
        f"Regime: {pulse_data.get('regime', 'N/A')}",
        "",
        "Category Scores:",
    ]
    for cat, info in scores.items():
        if isinstance(info, dict):
            lines.append(f"  {cat}: {info.get('score', 'N/A')}/100 ({info.get('direction', '')})")
        else:
            lines.append(f"  {cat}: {info}/100")

    # Add key rules firing
    all_rules = []
    for cat, info in scores.items():
        if isinstance(info, dict):
            for r in info.get("rules", []):
                all_rules.append(f"  [{cat}] {r.get('rule', '')}: {r.get('detail', '')} ({r.get('impact', '')})")
    if all_rules:
        lines.append("")
        lines.append("Key Rules Firing:")
        lines.extend(all_rules[:15])

    # Add sector summary
    sectors = pulse_data.get("sectors_summary", [])
    if sectors:
        lines.append("")
        lines.append("Sector Performance (5d):")
        for s in sectors[:6]:
            lines.append(f"  {s.get('name', '')}: {s.get('return_5d', 0):+.1f}%")

    lines.append("")
    lines.append("Generate a concise terminal-style analysis explaining this market environment.")

    return "\n".join(lines)


# ── Main Entry Point ────────────────────────────────────────────

def generate_analysis(pulse_data: dict[str, Any], mode: str = "swing", force: bool = False) -> str:
    """Return cached AI analyst commentary; refresh in background if stale.

    This function is designed to be NON-BLOCKING for the API caller:
    - If cache is valid → return immediately.
    - If cache is stale/missing → return stale cache or fallback NOW,
      then kick off a background thread to refresh the LLM cache.
    - If force=True → block and regenerate (used by manual refresh button).

    Args:
        pulse_data: Full market pulse response data
        mode: 'swing' or 'day' — each mode caches independently
        force: Force synchronous refresh (blocks until LLM responds)

    Returns: Analysis text string. Falls back to rule-based summary on LLM failure.
    """
    global _analyst_cache, _analyst_cache_ts

    # Fast path: cache hit
    if not force and _is_analyst_cache_valid(mode):
        return _analyst_cache[mode]

    # Force refresh: block and regenerate
    if force:
        return _refresh_analysis_sync(pulse_data, mode)

    # Non-blocking path: return stale/fallback immediately, refresh in background
    stale = _analyst_cache.get(mode)

    import threading
    threading.Thread(
        target=_refresh_analysis_sync,
        args=(pulse_data, mode),
        daemon=True,
    ).start()

    return stale if stale else _fallback_analysis(pulse_data)


def _refresh_analysis_sync(pulse_data: dict[str, Any], mode: str) -> str:
    """Synchronously generate LLM analysis and update cache. Thread-safe."""
    global _analyst_cache, _analyst_cache_ts

    provider = _get_provider()
    if provider is None:
        return _fallback_analysis(pulse_data)

    try:
        prompt = _build_prompt(pulse_data)
        analysis = provider.generate(prompt, SYSTEM_PROMPT)
        _analyst_cache[mode] = analysis
        _analyst_cache_ts[mode] = time.time()
        return analysis
    except Exception as e:
        logger.warning("LLM analysis failed for mode=%s: %s", mode, e)
        return _fallback_analysis(pulse_data)


def _fallback_analysis(pulse_data: dict[str, Any]) -> str:
    """Rule-based fallback when LLM is unavailable."""
    decision = pulse_data.get("decision", "CAUTION")
    score = pulse_data.get("market_quality_score", 50)
    regime = pulse_data.get("regime", "chop")
    exec_score = pulse_data.get("execution_window_score", 50)

    if decision == "YES":
        base = f"Market Quality {score}/100 signals a favorable trading environment."
    elif decision == "CAUTION":
        base = f"Market Quality {score}/100 — selective opportunities only."
    else:
        base = f"Market Quality {score}/100 — capital preservation mode."

    regime_text = {
        "uptrend": "Nifty in uptrend regime with positive MA alignment.",
        "downtrend": "Nifty in downtrend — avoid aggressive positioning.",
        "chop": "Choppy, range-bound conditions — patience required.",
    }.get(regime, "Mixed signals across indicators.")

    exec_text = f"Execution Window {exec_score}/100 — "
    if exec_score >= 70:
        exec_text += "breakouts are holding, follow-through is healthy."
    elif exec_score >= 50:
        exec_text += "mixed breakout quality, selectivity advised."
    else:
        exec_text += "breakouts failing frequently, wait for confirmation."

    return f"{base} {regime_text} {exec_text}"
