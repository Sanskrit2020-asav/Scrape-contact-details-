"""Builds the set of enabled platform adapters.

Adding Messenger, WhatsApp or Google Business Profile later means writing an
adapter and registering it here — the AI service, guardrails and pipeline are
untouched (spec §32).
"""
from __future__ import annotations

from ..apify import ActorRegistry, ApifyClient
from ..config import Settings, get_settings
from ..database.models import AgentSettings
from ..observability import get_logger
from .base import SocialPlatformAdapter
from .facebook import FacebookAdapter
from .instagram import InstagramAdapter
from .mock import MockAdapter

log = get_logger(__name__)

#: Channels planned but deliberately not implemented in V1.
FUTURE_PLATFORMS: tuple[str, ...] = ("messenger", "whatsapp", "google_business_profile")


def build_adapters(
    agent_settings: AgentSettings,
    settings: Settings | None = None,
    *,
    apify_client: ApifyClient | None = None,
    force_mock: bool | None = None,
) -> dict[str, SocialPlatformAdapter]:
    """Return ``{platform: adapter}`` for every enabled channel."""
    settings = settings or get_settings()
    use_mock = settings.platforms.use_mock_data if force_mock is None else force_mock
    registry = ActorRegistry(agent_settings)
    client = apify_client or ApifyClient(settings.apify)
    adapters: dict[str, SocialPlatformAdapter] = {}

    if agent_settings.facebook_enabled and settings.platforms.facebook_enabled:
        adapters["facebook"] = (
            MockAdapter("facebook", account_id=settings.platforms.facebook_account_id)
            if use_mock
            else FacebookAdapter(
                client=client,
                registry=registry,
                page_url=settings.platforms.facebook_page_url,
                account_id=settings.platforms.facebook_account_id,
                account_username=settings.company_name,
            )
        )

    if agent_settings.instagram_enabled and settings.platforms.instagram_enabled:
        adapters["instagram"] = (
            MockAdapter("instagram", account_id=settings.platforms.instagram_account_id)
            if use_mock
            else InstagramAdapter(
                client=client,
                registry=registry,
                profile_url=settings.platforms.instagram_profile_url,
                account_id=settings.platforms.instagram_account_id,
                account_username=settings.platforms.instagram_account_id,
            )
        )

    log.info(
        "adapters built",
        extra={"platforms": sorted(adapters), "mode": "mock" if use_mock else "apify"},
    )
    return adapters
