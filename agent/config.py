from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_CONTEXT_MODEL = "qwen3.7-plus"
DEFAULT_CONTEXT_TIMEOUT_SECONDS = 12.0
DEFAULT_KNOWLEDGE_TIMEOUT_SECONDS = 15.0
DEFAULT_RISK_MODEL = "cardiac-rupture-qwen38"
DEFAULT_RISK_API_KEY = "cardiac-rupture-local-key"
DEFAULT_RISK_URLS = (
    "http://127.0.0.1:8000/v1",
    "http://127.0.0.1:8001/v1",
)


@dataclass(frozen=True)
class AgentSettings:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_iterations: int = 6
    timeout_seconds: float = 60.0

    @property
    def is_configured(self) -> bool:
        normalized = self.api_key.strip().lower()
        placeholder_terms = ("替换", "your", "placeholder", "changeme", "example")
        return bool(
            len(normalized) >= 16
            and not any(term in normalized for term in placeholder_terms)
        )


@dataclass(frozen=True)
class RiskModelSettings:
    api_key: str
    urls: tuple[str, ...] = DEFAULT_RISK_URLS
    model: str = DEFAULT_RISK_MODEL
    timeout_seconds: float = 120.0
    max_tokens: int = 1024

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip() and self.urls and self.model.strip())


def _streamlit_secret(name: str) -> str:
    try:
        import streamlit as st

        value = st.secrets.get(name, "")
        return str(value).strip() if value else ""
    except Exception:
        return ""


def _setting(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or _streamlit_secret(name) or default


def get_agent_settings() -> AgentSettings:
    """Load settings without ever hard-coding or logging the API key."""
    api_key = _setting("DASHSCOPE_API_KEY") or _setting("BAILIAN_API_KEY")
    return AgentSettings(
        api_key=api_key,
        base_url=_setting("BAILIAN_COMPATIBLE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=_setting("BAILIAN_MODEL", DEFAULT_MODEL),
    )


def get_context_model() -> str:
    """Model used only to compress already-grounded clinical facts."""
    return _setting("BAILIAN_CONTEXT_MODEL", DEFAULT_CONTEXT_MODEL)


def get_context_timeout_seconds() -> float:
    """Keep optional clinical-text compression within a short fallback window."""
    raw = _setting(
        "CLINICAL_CONTEXT_TIMEOUT_SECONDS",
        str(DEFAULT_CONTEXT_TIMEOUT_SECONDS),
    )
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        configured = DEFAULT_CONTEXT_TIMEOUT_SECONDS
    return min(15.0, max(10.0, configured))


def get_knowledge_timeout_seconds() -> float:
    """Bound optional knowledge clarification so it cannot block the UI."""
    raw = _setting(
        "KNOWLEDGE_TIMEOUT_SECONDS",
        str(DEFAULT_KNOWLEDGE_TIMEOUT_SECONDS),
    )
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        configured = DEFAULT_KNOWLEDGE_TIMEOUT_SECONDS
    return min(20.0, max(10.0, configured))


def context_compression_enabled() -> bool:
    value = _setting("CLINICAL_CONTEXT_LLM_ENABLED", "1").lower()
    return value not in {"0", "false", "no", "off"}


def get_risk_model_settings() -> RiskModelSettings:
    """Load the local cardiac-rupture model without logging its credential."""
    configured_urls = _setting("CARDIAC_RISK_URLS")
    urls = tuple(
        item.strip().rstrip("/")
        for item in configured_urls.split(",")
        if item.strip()
    ) or DEFAULT_RISK_URLS
    raw_max_tokens = _setting("CARDIAC_RISK_MAX_TOKENS", "1024")
    try:
        max_tokens = min(2048, max(256, int(raw_max_tokens)))
    except ValueError:
        max_tokens = 1024
    return RiskModelSettings(
        api_key=_setting("CARDIAC_RISK_API_KEY", DEFAULT_RISK_API_KEY),
        urls=urls,
        model=_setting("CARDIAC_RISK_MODEL", DEFAULT_RISK_MODEL),
        max_tokens=max_tokens,
    )
