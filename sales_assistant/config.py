"""Central configuration.

Everything that varies by deployment lives here and is overridable through
environment variables, so no secret or tunable is hardcoded in business logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_ROOT / "data"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """Runtime settings, resolved from the environment with sensible defaults."""

    # --- Company identity (used in emails / quotations) ---
    company_name: str = field(default_factory=lambda: _env("COMPANY_NAME", "North Nepal Travel & Trek"))
    company_email: str = field(default_factory=lambda: _env("COMPANY_EMAIL", "partners@northnepaltrek.com"))
    company_website: str = field(default_factory=lambda: _env("COMPANY_WEBSITE", "https://www.northnepaltrek.com"))
    consultant_name: str = field(default_factory=lambda: _env("CONSULTANT_NAME", "The North Nepal Team"))

    # --- LLM (Anthropic Claude) ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-opus-4-8"))
    llm_max_tokens: int = field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "16000") or 16000))

    # --- Pricing ---
    currency: str = field(default_factory=lambda: _env("CURRENCY", "USD"))
    default_markup_pct: float = field(default_factory=lambda: _env_float("DEFAULT_MARKUP_PCT", 25.0))
    pricing_xlsx_path: str = field(default_factory=lambda: _env("PRICING_XLSX_PATH"))

    # --- Knowledge / RAG ---
    knowledge_dir: str = field(default_factory=lambda: _env("KNOWLEDGE_DIR", str(DATA_DIR / "knowledge")))
    min_retrieval_score: float = field(default_factory=lambda: _env_float("MIN_RETRIEVAL_SCORE", 0.05))

    # --- Research ---
    website_research_enabled: bool = field(default_factory=lambda: _env_bool("WEBSITE_RESEARCH_ENABLED", True))
    web_research_enabled: bool = field(default_factory=lambda: _env_bool("WEB_RESEARCH_ENABLED", False))
    trusted_web_domains: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            d.strip()
            for d in _env(
                "TRUSTED_WEB_DOMAINS",
                "himalayanrescue.org.np,welcomenepal.com,immigration.gov.np,nepal.travel",
            ).split(",")
            if d.strip()
        )
    )

    # --- Storage (logs, learning, conversation history) ---
    store_dir: str = field(default_factory=lambda: _env("STORE_DIR", str(DATA_DIR / "store")))

    def ensure_dirs(self) -> None:
        for p in (self.knowledge_dir, self.store_dir):
            Path(p).mkdir(parents=True, exist_ok=True)

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
