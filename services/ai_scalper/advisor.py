from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Dict, Optional

import requests

from utils.logging import get_logger

from .config import AdvisorConfig

logger = get_logger(__name__)


@dataclass
class AdvisorUpdate:
    changes: Dict[str, Any]
    notes: str = ""


class AdvisorClient:
    def __init__(self, config: AdvisorConfig) -> None:
        self.config = config

    def get_update(self, context: Dict[str, Any]) -> Optional[AdvisorUpdate]:
        return None


class NoOpAdvisor(AdvisorClient):
    def get_update(self, context: Dict[str, Any]) -> Optional[AdvisorUpdate]:
        return None


class HttpAdvisor(AdvisorClient):
    def get_update(self, context: Dict[str, Any]) -> Optional[AdvisorUpdate]:
        if not self.config.url:
            return None
        try:
            resp = requests.post(self.config.url, json=context, timeout=self.config.timeout_s)
            if not resp.ok:
                logger.warning("Advisor http error: %s", resp.text)
                return None
            payload = resp.json()
            return _parse_advisor_payload(payload)
        except Exception as exc:
            logger.debug("Advisor http exception: %s", exc)
            return None


class OpenAIAdvisor(AdvisorClient):
    def get_update(self, context: Dict[str, Any]) -> Optional[AdvisorUpdate]:
        api_key = os.getenv("OPENAI_API_KEY")
        model = self.config.model or os.getenv("OPENAI_ADVISOR_MODEL")
        if not api_key or not model:
            return None
        base = self.config.base_url or os.getenv("OPENAI_BASE_URL")
        url = _combine_base(base, "/v1/chat/completions") if base else "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a low-latency scalping advisor. Reply ONLY with JSON: {\"changes\":{...},\"notes\":\"...\"}.",
                },
                {
                    "role": "user",
                    "content": f"Context:\n{json.dumps(context, separators=(',', ':'))}",
                },
            ],
            "temperature": 0.1,
            "max_tokens": 200,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.config.timeout_s)
            if not resp.ok:
                logger.debug("OpenAI advisor error: %s", resp.text)
                return None
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return _parse_advisor_text(content)
        except Exception as exc:
            logger.debug("OpenAI advisor exception: %s", exc)
            return None


class AnthropicAdvisor(AdvisorClient):
    def get_update(self, context: Dict[str, Any]) -> Optional[AdvisorUpdate]:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        model = self.config.model or os.getenv("ANTHROPIC_ADVISOR_MODEL")
        if not api_key or not model:
            return None
        base = self.config.base_url or os.getenv("ANTHROPIC_BASE_URL")
        url = _combine_base(base, "/v1/messages") if base else "https://api.anthropic.com/v1/messages"
        payload = {
            "model": model,
            "max_tokens": 200,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "user",
                    "content": f"Reply ONLY with JSON: {{\"changes\":{{...}},\"notes\":\"...\"}}.\nContext:\n{json.dumps(context, separators=(',', ':'))}",
                }
            ],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self.config.anthropic_version,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.config.timeout_s)
            if not resp.ok:
                logger.debug("Anthropic advisor error: %s", resp.text)
                return None
            data = resp.json()
            content_blocks = data.get("content") or []
            text = ""
            if content_blocks and isinstance(content_blocks, list):
                text = content_blocks[0].get("text", "")
            return _parse_advisor_text(text)
        except Exception as exc:
            logger.debug("Anthropic advisor exception: %s", exc)
            return None


class OllamaAdvisor(AdvisorClient):
    def get_update(self, context: Dict[str, Any]) -> Optional[AdvisorUpdate]:
        model = self.config.model or os.getenv("OLLAMA_ADVISOR_MODEL")
        if not model:
            return None
        base = self.config.base_url or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        url = _combine_base(base, "/api/chat")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"Reply ONLY with JSON: {{\"changes\":{{...}},\"notes\":\"...\"}}.\nContext:\n{json.dumps(context, separators=(',', ':'))}",
                }
            ],
            "stream": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.config.timeout_s)
            if not resp.ok:
                logger.debug("Ollama advisor error: %s", resp.text)
                return None
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            return _parse_advisor_text(content)
        except Exception as exc:
            logger.debug("Ollama advisor exception: %s", exc)
            return None


def _parse_advisor_text(text: str) -> Optional[AdvisorUpdate]:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    return _parse_advisor_payload(payload)


def _parse_advisor_payload(payload: Dict[str, Any]) -> Optional[AdvisorUpdate]:
    changes = payload.get("changes") or {}
    notes = payload.get("notes") or ""
    if not isinstance(changes, dict):
        return None
    return AdvisorUpdate(changes=changes, notes=notes)


def _combine_base(base: str | None, path: str) -> str:
    if not base:
        return path
    base = base.rstrip("/")
    if base.endswith(path):
        return base
    if path.startswith("/v1/") and base.endswith("/v1"):
        return f"{base}{path[len('/v1'):]}"
    if path.startswith("/api/") and base.endswith("/api"):
        return f"{base}{path[len('/api'):]}"
    return f"{base}{path}"


def build_advisor(config: AdvisorConfig) -> AdvisorClient:
    if not config.enabled or config.provider == "none":
        return NoOpAdvisor(config)
    if config.provider == "http":
        return HttpAdvisor(config)
    if config.provider == "openai":
        return OpenAIAdvisor(config)
    if config.provider == "anthropic":
        return AnthropicAdvisor(config)
    if config.provider == "ollama":
        return OllamaAdvisor(config)
    return NoOpAdvisor(config)
