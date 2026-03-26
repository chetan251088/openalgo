"""
Provider-agnostic LLM analyst for Market Pulse.
Generates plain-English market commentary from scores and data.
"""

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# ── In-memory cache for analyst output ──────────────────────────
_analyst_cache: dict[str, str] = {}
_analyst_cache_ts: dict[str, float] = {}
_analyst_refreshing: set[str] = set()
_analyst_lock = threading.Lock()
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
        self.api_key = os.getenv("LLM_API_KEY")
        self.base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "gpt-4o")

    def generate(self, prompt: str, system_prompt: str) -> str:
        import requests

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1024,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("No choices returned from OpenAI-compatible provider")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "".join(text_parts)
        raise ValueError("No text content returned from OpenAI-compatible provider")


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
    "nvidia": OpenAIProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def _get_provider() -> LLMProvider | None:
    provider_name = os.getenv("LLM_PROVIDER", "").lower()
    if not provider_name:
        return None

    if provider_name not in {"ollama"} and not os.getenv("LLM_API_KEY"):
        logger.warning("LLM_API_KEY is required for provider: %s", provider_name)
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
7. Distinguish intraday breadth from structural breadth. If A/D is strong but % above moving averages is weak, call breadth mixed rather than contradictory.
8. If institutional flow data is stale, say it is stale and do not lean on it.
9. Treat breadth metrics as coming from the Nifty 50 constituent basket unless explicitly stated otherwise.
10. In day mode, separate the structural backdrop from the live session tape. A downtrend backdrop can coexist with a bullish intraday thrust, and vice versa.
11. Do not contradict the supplied rule impacts or option structures.

Speak like a Bloomberg terminal analyst — direct, professional, data-driven. No hedging or disclaimers."""


def _build_prompt(pulse_data: dict[str, Any]) -> str:
    """Build the analyst prompt from market pulse data."""
    scores = pulse_data.get("scores", {})
    lines = [
        f"Decision: {pulse_data.get('decision', 'N/A')}",
        f"Base Quality Decision: {pulse_data.get('quality_decision', 'N/A')}",
        f"Market Quality Score: {pulse_data.get('market_quality_score', 'N/A')}/100",
        f"Execution Window Score: {pulse_data.get('execution_window_score', 'N/A')}/100",
        f"Mode: {pulse_data.get('mode', 'swing')}",
        f"Regime: {pulse_data.get('regime', 'N/A')}",
        f"Execution Regime: {pulse_data.get('execution_regime', pulse_data.get('regime', 'N/A'))}",
        f"Directional Bias: {(pulse_data.get('directional_bias') or {}).get('bias', 'N/A')}",
        "Breadth Universe: Nifty 50 constituents",
        (
            "Sector Heatmap Default: "
            f"{'1d live tape' if pulse_data.get('mode') == 'day' else '5d swing context'}"
        ),
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

    institutional = pulse_data.get("institutional_flows") or {}
    freshness = institutional.get("freshness") or {}
    latest = institutional.get("latest") or {}
    if institutional:
        lines.append("")
        lines.append("Institutional Flows:")
        lines.append(
            "  "
            f"Freshness: {'stale' if freshness.get('is_stale') else 'fresh'}"
            f", latest={freshness.get('latest_trading_date', latest.get('date', 'N/A'))}"
            f", lag_business_days={freshness.get('lag_business_days', 0)}"
        )
        lines.append(
            "  "
            f"Headline bias: {latest.get('headline_bias', 'neutral')}, "
            f"cash: {latest.get('cash_bias', 'neutral')}, "
            f"derivatives: {latest.get('derivatives_bias', 'neutral')}"
        )

    lines.append("")
    lines.append("Generate a concise terminal-style analysis explaining this market environment.")

    return "\n".join(lines)


# ── Main Entry Point ────────────────────────────────────────────

def generate_analysis(
    pulse_data: dict[str, Any],
    mode: str = "swing",
    force: bool = False,
) -> str:
    """Return cached AI commentary; refresh in background if stale."""
    if not force and _is_analyst_cache_valid(mode):
        return _analyst_cache[mode]

    if force:
        return _refresh_analysis_sync(pulse_data, mode)

    stale = _analyst_cache.get(mode)
    _refresh_analysis_background(pulse_data, mode)
    return stale if stale else _fallback_analysis(pulse_data)


def _refresh_analysis_background(pulse_data: dict[str, Any], mode: str) -> None:
    """Start a background refresh for this mode if one is not already running."""
    with _analyst_lock:
        if mode in _analyst_refreshing:
            return
        _analyst_refreshing.add(mode)

    def _runner():
        try:
            _refresh_analysis_sync(pulse_data, mode)
        finally:
            with _analyst_lock:
                _analyst_refreshing.discard(mode)

    threading.Thread(target=_runner, daemon=True).start()


def _refresh_analysis_sync(pulse_data: dict[str, Any], mode: str) -> str:
    """Synchronously generate LLM analysis and update the per-mode cache."""
    provider = _get_provider()
    if provider is None:
        return _fallback_analysis(pulse_data)

    try:
        prompt = _build_prompt(pulse_data)
        analysis = provider.generate(prompt, SYSTEM_PROMPT)
        with _analyst_lock:
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
