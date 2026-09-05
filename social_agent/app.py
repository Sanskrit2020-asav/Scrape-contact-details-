"""Composition root.

One place where the object graph is assembled, so the CLI, the dashboard and the
tests all build the same application in the same way — and so a test can swap
any single dependency (the OpenAI transport, the Apify client, the adapters)
without touching the code under test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ai import AIDecisionService
from .apify import ApifyClient
from .config import Settings, get_settings
from .database import Database, Repositories, get_database
from .database.models import AgentSettings
from .knowledge import KnowledgeService
from .observability import configure_logging, get_logger
from .social import build_adapters
from .social.base import SocialPlatformAdapter

log = get_logger(__name__)


@dataclass
class Application:
    """Everything wired together."""

    settings: Settings
    db: Database
    repos: Repositories
    knowledge: KnowledgeService
    ai: AIDecisionService
    adapters: dict[str, SocialPlatformAdapter]
    agent: Any  # SocialEngagementAgent (imported lazily to avoid a cycle)

    @property
    def agent_settings(self) -> AgentSettings:
        return self.repos.settings.get()

    def health(self) -> dict[str, Any]:
        """Everything an operator needs to see whether this thing can run."""
        agent_settings = self.agent_settings
        return {
            "company": self.settings.company_name,
            "ai": {
                "provider": "openai",
                "configured": self.ai.configured,
                "model": agent_settings.openai_model,
                "api_style": self.settings.openai.api_style,
            },
            "apify": {
                "configured": self.settings.apify.configured,
                "using_mock_data": self.settings.platforms.use_mock_data,
            },
            "mode": {
                "dry_run": agent_settings.dry_run,
                "human_approval_required": agent_settings.human_approval_required,
                "auto_reply_enabled": agent_settings.auto_reply_enabled,
                "minimum_confidence": agent_settings.minimum_confidence,
            },
            "platforms": {name: adapter.health() for name, adapter in self.adapters.items()},
        }


def build_application(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    ai_service: AIDecisionService | None = None,
    apify_client: ApifyClient | None = None,
    adapters: dict[str, SocialPlatformAdapter] | None = None,
    seed_knowledge: bool = True,
    configure_logs: bool = True,
) -> Application:
    """Assemble the application. Every dependency is overridable for tests."""
    from .agent import SocialEngagementAgent  # local import breaks the cycle

    settings = settings or get_settings()
    if configure_logs:
        configure_logging(settings.log_level, settings.log_format)

    database = db or get_database(settings.database_path)
    # Idempotent: already-applied migrations are skipped. Running it here means
    # an injected database (tests, a second process) is always schema-current.
    database.migrate()
    repos = Repositories(database)
    agent_settings = repos.settings.bootstrap(settings.defaults)

    knowledge = KnowledgeService(repos.knowledge)
    if seed_knowledge:
        knowledge.seed_if_empty()

    ai = ai_service or AIDecisionService(settings)
    resolved_adapters = adapters if adapters is not None else build_adapters(
        agent_settings, settings, apify_client=apify_client
    )

    agent = SocialEngagementAgent(repos, resolved_adapters, ai, knowledge, settings)

    log.info(
        "application ready",
        extra={
            "dry_run": agent_settings.dry_run,
            "human_approval_required": agent_settings.human_approval_required,
            "auto_reply_enabled": agent_settings.auto_reply_enabled,
            "platforms": sorted(resolved_adapters),
            "openai_configured": ai.configured,
        },
    )
    return Application(
        settings=settings,
        db=database,
        repos=repos,
        knowledge=knowledge,
        ai=ai,
        adapters=resolved_adapters,
        agent=agent,
    )
