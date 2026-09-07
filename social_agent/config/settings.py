"""Central configuration for the North Nepal Social Engagement Agent.

Two layers of configuration exist and they serve different purposes:

* **Environment settings** (this module) hold secrets and deployment-level
  values. They are read once at process start and are never sent to the
  browser. ``OPENAI_API_KEY`` and ``APIFY_API_TOKEN`` live *only* here.
* **Runtime settings** (``agent_settings`` table, see
  :mod:`social_agent.database.repositories`) hold the operational switches an
  operator flips from the dashboard — dry-run, approval mode, thresholds.
  Environment values seed that row the first time the database is created.

Nothing in ``social_agent`` reads ``os.environ`` outside this module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PACKAGE_ROOT / "data"
PROMPTS_DIR = PACKAGE_ROOT / "ai" / "prompts"
MOCK_DIR = DATA_DIR / "mock"


# --------------------------------------------------------------------------
# .env loading (no third-party dependency)
# --------------------------------------------------------------------------

def load_dotenv(path: str | Path | None = None, override: bool = False) -> dict[str, str]:
    """Load ``KEY=value`` pairs from a .env file into ``os.environ``.

    Existing environment variables win unless ``override`` is set, so a real
    deployment secret is never shadowed by a checked-out file. Returns the
    mapping that was parsed (useful for tests and diagnostics).
    """
    env_path = Path(path) if path else REPO_ROOT / ".env"
    parsed: dict[str, str] = {}
    if not env_path.is_file():
        return parsed
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_opt_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Settings groups
# --------------------------------------------------------------------------


@dataclass
class OpenAISettings:
    """OpenAI is the only runtime AI provider in this application."""

    api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-5"))
    base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    # "responses" (current agent-oriented API) or "chat_completions" (legacy).
    api_style: str = field(default_factory=lambda: _env("OPENAI_API_STYLE", "responses").lower())
    organization: str = field(default_factory=lambda: _env("OPENAI_ORGANIZATION"))
    timeout_seconds: int = field(default_factory=lambda: _env_int("OPENAI_TIMEOUT_SECONDS", 45))
    max_retries: int = field(default_factory=lambda: _env_int("OPENAI_MAX_RETRIES", 2))
    max_output_tokens: int = field(default_factory=lambda: _env_int("OPENAI_MAX_OUTPUT_TOKENS", 900))
    # Left unset by default: several current models reject an explicit
    # temperature. Only sent when the operator sets it deliberately.
    temperature: float | None = field(default_factory=lambda: _env_opt_float("OPENAI_TEMPERATURE"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass
class ApifySettings:
    """Apify is the social-media I/O layer. Actor IDs are configurable.

    No actor behaviour is assumed beyond Apify's documented REST API
    (``POST /v2/acts/{actor}/runs`` → poll ``/v2/actor-runs/{id}`` → read
    ``/v2/datasets/{id}/items``). Per-actor input shapes are supplied as JSON
    templates so a different actor can be swapped in without code changes.
    """

    api_token: str = field(default_factory=lambda: _env("APIFY_API_TOKEN"))
    base_url: str = field(default_factory=lambda: _env("APIFY_BASE_URL", "https://api.apify.com/v2"))
    timeout_seconds: int = field(default_factory=lambda: _env_int("APIFY_TIMEOUT_SECONDS", 600))
    poll_interval_seconds: int = field(default_factory=lambda: _env_int("APIFY_POLL_INTERVAL_SECONDS", 5))
    request_timeout_seconds: int = field(default_factory=lambda: _env_int("APIFY_REQUEST_TIMEOUT_SECONDS", 120))
    max_retries: int = field(default_factory=lambda: _env_int("APIFY_MAX_RETRIES", 2))

    @property
    def configured(self) -> bool:
        return bool(self.api_token)


@dataclass
class PlatformSettings:
    """Per-platform enablement and the account/page the agent watches."""

    facebook_enabled: bool = field(default_factory=lambda: _env_bool("FACEBOOK_ENABLED", True))
    facebook_page_url: str = field(default_factory=lambda: _env("FACEBOOK_PAGE_URL"))
    facebook_account_id: str = field(default_factory=lambda: _env("FACEBOOK_ACCOUNT_ID", "north_nepal_facebook"))

    instagram_enabled: bool = field(default_factory=lambda: _env_bool("INSTAGRAM_ENABLED", True))
    instagram_profile_url: str = field(default_factory=lambda: _env("INSTAGRAM_PROFILE_URL"))
    instagram_account_id: str = field(default_factory=lambda: _env("INSTAGRAM_ACCOUNT_ID", "north_nepal_instagram"))

    # When true the adapters read the bundled fixture data instead of calling
    # Apify. This is what makes an end-to-end dry run possible with no
    # credentials and no real accounts.
    use_mock_data: bool = field(default_factory=lambda: _env_bool("USE_MOCK_SOCIAL_DATA", False))


@dataclass
class DashboardSettings:
    host: str = field(default_factory=lambda: _env("DASHBOARD_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("DASHBOARD_PORT", 8080))
    username: str = field(default_factory=lambda: _env("DASHBOARD_USERNAME", "admin"))
    password: str = field(default_factory=lambda: _env("DASHBOARD_PASSWORD"))
    auth_enabled: bool = field(default_factory=lambda: _env_bool("DASHBOARD_AUTH_ENABLED", True))


@dataclass
class AgentDefaults:
    """Seed values for the ``agent_settings`` row on first run.

    V1 ships safe: dry-run on, human approval on, auto-reply off.
    """

    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", True))
    human_approval_required: bool = field(default_factory=lambda: _env_bool("HUMAN_APPROVAL_REQUIRED", True))
    auto_reply_enabled: bool = field(default_factory=lambda: _env_bool("AUTO_REPLY_ENABLED", False))
    minimum_confidence: float = field(default_factory=lambda: _env_float("AUTO_REPLY_MIN_CONFIDENCE", 0.90))
    max_reply_length: int = field(default_factory=lambda: _env_int("MAX_REPLY_LENGTH", 240))
    polling_interval_seconds: int = field(default_factory=lambda: _env_int("POLLING_INTERVAL_SECONDS", 300))
    max_comments_per_run: int = field(default_factory=lambda: _env_int("MAX_COMMENTS_PER_RUN", 50))
    max_posts_per_run: int = field(default_factory=lambda: _env_int("MAX_POSTS_PER_RUN", 10))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-5"))
    facebook_comments_actor: str = field(
        default_factory=lambda: _env("APIFY_FACEBOOK_COMMENTS_ACTOR", "apify/facebook-comments-scraper")
    )
    facebook_posts_actor: str = field(
        default_factory=lambda: _env("APIFY_FACEBOOK_POSTS_ACTOR", "apify/facebook-posts-scraper")
    )
    facebook_reply_actor: str = field(default_factory=lambda: _env("APIFY_FACEBOOK_REPLY_ACTOR"))
    instagram_comments_actor: str = field(
        default_factory=lambda: _env("APIFY_INSTAGRAM_COMMENTS_ACTOR", "apify/instagram-comment-scraper")
    )
    instagram_posts_actor: str = field(
        default_factory=lambda: _env("APIFY_INSTAGRAM_POSTS_ACTOR", "apify/instagram-post-scraper")
    )
    instagram_reply_actor: str = field(default_factory=lambda: _env("APIFY_INSTAGRAM_REPLY_ACTOR"))

    # Spend controls. The budget is a hard stop on tokens consumed in a rolling
    # 24 hours; 0 disables it. Prices are per 1,000,000 tokens and default to 0,
    # in which case the dashboard reports cost as "not configured" rather than
    # inventing a rate that may be out of date.
    high_engagement_threshold: int = field(
        default_factory=lambda: _env_int("HIGH_ENGAGEMENT_THRESHOLD", 10)
    )
    daily_token_budget: int = field(default_factory=lambda: _env_int("DAILY_TOKEN_BUDGET", 0))
    input_cost_per_million: float = field(
        default_factory=lambda: _env_float("OPENAI_INPUT_COST_PER_MILLION", 0.0)
    )
    output_cost_per_million: float = field(
        default_factory=lambda: _env_float("OPENAI_OUTPUT_COST_PER_MILLION", 0.0)
    )


@dataclass
class Settings:
    """Everything the process needs, resolved once from the environment."""

    openai: OpenAISettings = field(default_factory=OpenAISettings)
    apify: ApifySettings = field(default_factory=ApifySettings)
    platforms: PlatformSettings = field(default_factory=PlatformSettings)
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)
    defaults: AgentDefaults = field(default_factory=AgentDefaults)

    company_name: str = field(default_factory=lambda: _env("COMPANY_NAME", "North Nepal Travel & Trek"))
    database_path: str = field(default_factory=lambda: _env("DATABASE_PATH", str(DATA_DIR / "social_agent.db")))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())
    log_format: str = field(default_factory=lambda: _env("LOG_FORMAT", "json").lower())
    system_prompt_path: str = field(
        default_factory=lambda: _env("SYSTEM_PROMPT_PATH", str(PROMPTS_DIR / "system_prompt.md"))
    )

    def ensure_dirs(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict:
        """A dashboard-safe view. Secrets are reported as booleans only."""
        return {
            "company_name": self.company_name,
            "openai": {
                "model": self.openai.model,
                "base_url": self.openai.base_url,
                "api_style": self.openai.api_style,
                "api_key_configured": self.openai.configured,
                "timeout_seconds": self.openai.timeout_seconds,
                "max_output_tokens": self.openai.max_output_tokens,
            },
            "apify": {
                "base_url": self.apify.base_url,
                "api_token_configured": self.apify.configured,
                "timeout_seconds": self.apify.timeout_seconds,
                "poll_interval_seconds": self.apify.poll_interval_seconds,
            },
            "platforms": {
                "facebook_enabled": self.platforms.facebook_enabled,
                "instagram_enabled": self.platforms.instagram_enabled,
                "use_mock_data": self.platforms.use_mock_data,
            },
            "database_path": self.database_path,
            "log_level": self.log_level,
        }


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """Return the process-wide settings, loading ``.env`` on first use."""
    global _settings
    if _settings is None or refresh:
        load_dotenv()
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def reset_settings() -> None:
    """Drop the cached settings (used by tests that patch the environment)."""
    global _settings
    _settings = None
