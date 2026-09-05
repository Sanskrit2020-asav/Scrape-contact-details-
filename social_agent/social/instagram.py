"""Instagram adapter."""
from __future__ import annotations

from ..apify import ActorRegistry, ApifyClient
from .apify_adapter import ApifySocialAdapter


class InstagramAdapter(ApifySocialAdapter):
    platform = "instagram"

    def __init__(
        self,
        *,
        client: ApifyClient,
        registry: ActorRegistry,
        profile_url: str = "",
        account_id: str = "",
        account_username: str = "",
    ):
        super().__init__(
            "instagram",
            client=client,
            registry=registry,
            page_urls=[profile_url] if profile_url else [],
            account_id=account_id,
            account_username=account_username,
        )
