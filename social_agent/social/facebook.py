"""Facebook adapter."""
from __future__ import annotations

from ..apify import ActorRegistry, ApifyClient
from .apify_adapter import ApifySocialAdapter


class FacebookAdapter(ApifySocialAdapter):
    platform = "facebook"

    def __init__(
        self,
        *,
        client: ApifyClient,
        registry: ActorRegistry,
        page_url: str = "",
        account_id: str = "",
        account_username: str = "",
    ):
        super().__init__(
            "facebook",
            client=client,
            registry=registry,
            page_urls=[page_url] if page_url else [],
            account_id=account_id,
            account_username=account_username,
        )
