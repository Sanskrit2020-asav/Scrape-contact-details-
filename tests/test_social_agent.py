"""Tests for the North Nepal Social Engagement Agent.

Covers the twenty scenarios in the build spec (§29). No network, no API key and
no real accounts: the OpenAI and Apify HTTP transports are replaced with test
doubles, so the real client, validator, guardrails, routing and publisher code
all execute unchanged.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from social_agent.agent.guardrails import (  # noqa: E402
    apply_guardrails,
    mentions_current_incident,
    trim_to_length,
)
from social_agent.agent.policy import auto_reply_blockers, route  # noqa: E402
from social_agent.agent.variation import is_repetitive, similarity  # noqa: E402
from social_agent.ai import build_context  # noqa: E402
from social_agent.ai.openai_client import (  # noqa: E402
    OpenAIClient,
    OpenAIError,
    OpenAIRateLimitError,
    OpenAITimeoutError,
)
from social_agent.ai.schema import (  # noqa: E402
    AgentDecision,
    DecisionValidationError,
    extract_json,
    validate_decision,
)
from social_agent.apify import ApifyActorError, ApifyClient, ApifyTimeoutError  # noqa: E402
from social_agent.config import ApifySettings, OpenAISettings, Settings  # noqa: E402
from social_agent.database import Database, Repositories  # noqa: E402
from social_agent.database.models import Comment, CommentStatus, Post  # noqa: E402
from social_agent.social import MockAdapter  # noqa: E402
from social_agent.social.base import (  # noqa: E402
    FetchedComment,
    FetchedPost,
    PlatformNotConfiguredError,
    ReplyResult,
    SocialPlatformAdapter,
)
from social_agent.testing import (  # noqa: E402
    QueuedApifyTransport,
    QueuedOpenAITransport,
    ScriptedOpenAITransport,
    apify_run_started,
    build_test_application,
    decision,
    responses_payload,
)


# ----------------------------------------------------------------- fixtures


@pytest.fixture()
def app():
    """A full application on an in-memory database with a scripted AI."""
    return build_test_application()


@pytest.fixture()
def repos():
    db = Database(":memory:")
    db.migrate()
    return Repositories(db)


def _run(app, platforms=None):
    return app.agent.run_cycle(platforms=platforms)


def _comment_by_text(app, fragment: str):
    for comment in app.repos.comments.list(limit=100):
        if fragment.lower() in comment.comment_text.lower():
            return comment
    raise AssertionError(f"no comment containing {fragment!r}")


def _decide(app, text: str, platform: str = "facebook"):
    """Push one ad-hoc comment through the real pipeline."""
    post = app.repos.posts.upsert(
        Post(platform=platform, platform_post_id="p_test", caption="Test post",
             topic="Everest Base Camp", content_type="photo")
    )
    stored = app.repos.comments.insert_if_new(
        Comment(platform=platform, platform_comment_id=f"c_{abs(hash(text))}",
                platform_post_id="p_test", post_id=post.id,
                author_name="Tester", comment_text=text)
    )
    assert stored is not None
    return app.agent.process_comment(stored, app.repos.settings.get())


# ------------------------------------------------------- 1. simple compliment


def test_simple_compliment_gets_a_short_reply(app):
    result = _decide(app, "Beautiful!")
    assert result["action"] == "reply"
    assert result["intent"] == "compliment"
    assert result["reply"]
    assert len(result["reply"]) <= app.repos.settings.get().max_reply_length
    # Ships safe: approval is required, so it queues rather than publishing.
    assert result["status"] == CommentStatus.PENDING_APPROVAL.value


# ------------------------------------------------------- 2. emoji-only comment


def test_emoji_only_comment_is_handled(app):
    result = _decide(app, "😍", platform="instagram")
    assert result["action"] == "reply"
    assert result["reply"]


# ---------------------------------------------------------- 3. travel interest


def test_travel_interest_is_treated_as_a_lead(app):
    result = _decide(app, "I want to come to Nepal next year.")
    assert result["intent"] == "lead"
    assert result["action"] == "reply"
    # Warm, not salesy.
    assert "BOOK NOW" not in (result["reply"] or "").upper()


# ------------------------------------------------------- 4. difficulty question


def test_trek_difficulty_question_is_answered(app):
    result = _decide(app, "How difficult is Manaslu?")
    assert result["intent"] == "trekking_difficulty"
    assert result["action"] == "reply"


# ------------------------------------------------------------ 5. price inquiry


def test_price_inquiry_does_not_quote_a_price(app):
    result = _decide(app, "How much does this trek cost?")
    assert result["intent"] == "price_inquiry"
    reply = result["reply"] or ""
    assert "$" not in reply and "USD" not in reply.upper()
    assert "message" in reply.lower()


def test_invented_price_is_blocked_by_guardrails():
    """A model that does quote a price never gets published."""
    priced = AgentDecision(
        action="reply", intent="price_inquiry", confidence=0.99,
        reply="The Manaslu trek is $1,450 per person.", needs_human=False,
        reason="", risk_level="low",
    )
    result = apply_guardrails(priced, comment_text="How much is Manaslu?")
    assert result.decision.action == "escalate"
    assert result.decision.reply is None
    assert result.blocked
    assert any("figure" in v for v in result.violations)


# ---------------------------------------------------------- 6. permit question


def test_permit_question_is_kept_general(app):
    result = _decide(app, "Do I need a special permit for Manaslu?")
    assert result["intent"] == "permit_question"
    assert result["action"] == "reply"


# -------------------------------------------------------------------- 7. spam


def test_spam_is_ignored(app):
    result = _decide(app, "CHEAP FOLLOWERS AND LIKES!! DM me now www.buy-fast.example")
    assert result["action"] == "ignore"
    assert result["status"] == CommentStatus.IGNORED.value
    assert result["reply"] is None


# --------------------------------------------------------------- 8. complaint


def test_complaint_is_escalated_never_argued_with(app):
    result = _decide(app, "You scammed me.")
    assert result["action"] == "escalate"
    assert result["status"] == CommentStatus.ESCALATED.value
    assert result["reply"] is None


def test_complaint_escalates_even_if_the_model_wants_to_reply():
    defensive = AgentDecision(
        action="reply", intent="complaint", confidence=0.95,
        reply="That is not true, our guides are excellent.", needs_human=False,
        reason="", risk_level="low",
    )
    result = apply_guardrails(defensive, comment_text="You scammed me and I want a refund")
    assert result.decision.action == "escalate"
    assert result.decision.needs_human is True
    assert result.decision.reply is None


# ---------------------------------------------------------- 9. refund request


def test_refund_request_is_escalated(app):
    result = _decide(app, "I want a refund for my cancelled trek.")
    assert result["action"] == "escalate"
    assert result["reply"] is None


# --------------------------------------------------------- 10. safety question


def test_general_safety_question_is_not_falsely_escalated():
    """A general question about altitude is answerable; it is not an incident."""
    assert mentions_current_incident("How dangerous is altitude sickness generally?") is None
    assert mentions_current_incident("How difficult is Manaslu?") is None


# ------------------------------------------------- 11. current disaster question


@pytest.mark.parametrize(
    "text",
    [
        "Is the trail open right now?",
        "Was anyone hurt in the landslide?",
        "Are flights to Lukla running?",
        "Is the road to Besisahar blocked?",
        "Is it safe now?",
    ],
)
def test_current_conditions_are_always_escalated(text):
    assert mentions_current_incident(text) is not None
    reassuring = AgentDecision(
        action="reply", intent="safety", confidence=0.99,
        reply="Everything is completely fine, come on over!", needs_human=False,
        reason="", risk_level="low",
    )
    result = apply_guardrails(reassuring, comment_text=text)
    assert result.decision.action == "escalate"
    assert result.decision.risk_level == "high"
    assert result.decision.needs_human is True
    assert result.decision.reply is None


def test_disaster_comment_through_the_pipeline_is_escalated(app):
    result = _decide(app, "Is the trail open right now?", platform="instagram")
    assert result["action"] == "escalate"
    assert result["risk_level"] == "high"
    assert result["reply"] is None


# -------------------------------------------------------- 12. duplicate comment


def test_the_same_comment_is_never_processed_twice(app):
    first = _run(app)
    assert first.comments_new == 13
    assert first.comments_duplicate == 0
    calls_after_first = len(app.ai.client.transport.requests)

    second = _run(app)
    assert second.comments_new == 0
    assert second.comments_duplicate == 13
    assert second.processed == 0
    # No second round of OpenAI calls for comments already seen.
    assert len(app.ai.client.transport.requests) == calls_after_first
    assert app.repos.comments.total() == 13


def test_a_posted_reply_cannot_be_duplicated(app):
    _run(app)
    comment = _comment_by_text(app, "Beautiful!")
    first = app.agent.approve_reply(comment.id)
    assert first["ok"]
    second = app.agent.approve_reply(comment.id)
    assert second["ok"] is False
    assert "already" in (second.get("error") or "").lower()


def test_database_refuses_a_second_posted_reply(repos):
    from social_agent.database.models import Reply

    comment = repos.comments.insert_if_new(
        Comment(platform="facebook", platform_comment_id="dup1", comment_text="hi")
    )
    a = repos.replies.create(Reply(comment_id=comment.id, reply_text="one"))
    b = repos.replies.create(Reply(comment_id=comment.id, reply_text="two"))
    assert repos.replies.mark_posted(a.id, "r1", dry_run=False) is True
    # The partial unique index stops the second one.
    assert repos.replies.mark_posted(b.id, "r2", dry_run=False) is False
    assert repos.replies.has_posted_reply(comment.id) is True


# ------------------------------------------------------------ 13. low confidence


def test_low_confidence_never_auto_replies():
    from social_agent.database.models import AgentSettings

    settings = AgentSettings(
        dry_run=False, human_approval_required=False,
        auto_reply_enabled=True, minimum_confidence=0.90,
    )
    low = AgentDecision(
        action="reply", intent="compliment", confidence=0.72, reply="Thanks!",
        needs_human=False, reason="", risk_level="low",
    )
    blockers = auto_reply_blockers(low, settings)
    assert any("confidence" in b for b in blockers)
    assert route(low, settings).status == CommentStatus.PENDING_APPROVAL.value
    assert route(low, settings).publish_now is False


def test_auto_reply_requires_every_condition():
    from social_agent.database.models import AgentSettings

    settings = AgentSettings(
        dry_run=False, human_approval_required=False,
        auto_reply_enabled=True, minimum_confidence=0.90,
    )
    good = AgentDecision(
        action="reply", intent="compliment", confidence=0.95, reply="Lovely spot.",
        needs_human=False, reason="", risk_level="low",
    )
    assert auto_reply_blockers(good, settings) == []
    assert route(good, settings).publish_now is True

    # Each condition, removed one at a time, must block it.
    assert auto_reply_blockers(good, AgentSettings(**{**settings.to_dict(), "dry_run": True}))
    assert auto_reply_blockers(good, AgentSettings(**{**settings.to_dict(), "auto_reply_enabled": False}))
    assert auto_reply_blockers(good, AgentSettings(**{**settings.to_dict(), "human_approval_required": True}))
    assert auto_reply_blockers(good, settings, duplicate_ok=False)
    for field, value in (("needs_human", True), ("risk_level", "medium"), ("action", "escalate")):
        assert auto_reply_blockers(
            AgentDecision(**{**good.to_dict(), field: value}), settings
        )


# ------------------------------------------------- 14. invalid OpenAI response


def test_malformed_json_is_rejected_and_retried_then_marked_for_review():
    settings = Settings()
    settings.openai.api_key = "sk-test"
    transport = QueuedOpenAITransport(queue=[
        responses_payload({"nonsense": True}),
        responses_payload({"still": "wrong"}),
    ])
    from social_agent.ai import AIDecisionService

    service = AIDecisionService(
        settings, client=OpenAIClient(settings.openai, transport=transport, sleep=lambda _: None)
    )
    result = service.decide(build_context(
        platform="facebook",
        comment=Comment(platform="facebook", platform_comment_id="x", comment_text="Beautiful!"),
    ))
    assert result.valid is False
    assert result.attempts == 2                      # one retry, then it gives up
    assert result.decision.action == "escalate"      # never publishes
    assert result.decision.needs_human is True
    assert result.decision.reply is None


def test_a_retry_can_recover_from_one_bad_response():
    settings = Settings()
    settings.openai.api_key = "sk-test"
    transport = QueuedOpenAITransport(queue=[
        responses_payload({"garbage": 1}),
        responses_payload(decision(reply="It really is lovely up there.")),
    ])
    from social_agent.ai import AIDecisionService

    service = AIDecisionService(
        settings, client=OpenAIClient(settings.openai, transport=transport, sleep=lambda _: None)
    )
    result = service.decide(build_context(
        platform="facebook",
        comment=Comment(platform="facebook", platform_comment_id="x", comment_text="Beautiful!"),
    ))
    assert result.valid is True
    assert result.attempts == 2
    assert result.decision.action == "reply"


@pytest.mark.parametrize("payload", [
    {"action": "maybe", "intent": "x", "confidence": 0.5, "reply": "hi",
     "needs_human": False, "reason": "", "risk_level": "low"},
    {"action": "reply", "intent": "x", "confidence": 5.0, "reply": "hi",
     "needs_human": False, "reason": "", "risk_level": "low"},
    {"action": "reply", "intent": "x", "confidence": 0.5, "reply": None,
     "needs_human": False, "reason": "", "risk_level": "low"},
    {"action": "reply", "intent": "x", "confidence": 0.5, "reply": "hi",
     "needs_human": "yes", "reason": "", "risk_level": "low"},
    {"action": "reply", "intent": "x", "confidence": 0.5, "reply": "hi",
     "needs_human": False, "reason": "", "risk_level": "nuclear"},
])
def test_schema_validation_rejects_bad_payloads(payload):
    with pytest.raises(DecisionValidationError):
        validate_decision(payload)


def test_json_is_extracted_from_a_fenced_response():
    parsed = extract_json('```json\n{"action": "ignore"}\n```')
    assert parsed == {"action": "ignore"}


# ------------------------------------------------------------ 15. Apify failure


def test_apify_actor_failure_is_reported_not_swallowed():
    client = ApifyClient(
        ApifySettings(api_token="t", poll_interval_seconds=0),
        transport=QueuedApifyTransport(queue=[
            apify_run_started(status="RUNNING"),
            {"data": {"id": "run_1", "defaultDatasetId": "ds_1", "status": "FAILED"}},
        ]),
        sleep=lambda _: None,
    )
    with pytest.raises(ApifyActorError):
        client.run_actor("apify/whatever", {})


def test_apify_timeout_is_raised():
    ticks = iter([0, 0, 1_000_000, 2_000_000])
    client = ApifyClient(
        ApifySettings(api_token="t", timeout_seconds=5, poll_interval_seconds=0),
        transport=QueuedApifyTransport(queue=[
            apify_run_started(status="RUNNING"),
            {"data": {"id": "run_1", "status": "RUNNING"}},
        ]),
        sleep=lambda _: None,
        clock=lambda: next(ticks),
    )
    with pytest.raises(ApifyTimeoutError):
        client.run_actor("apify/whatever", {})


def test_a_failing_platform_does_not_stop_the_cycle():
    """Instagram still gets processed when Facebook's fetch blows up."""
    class BrokenAdapter(SocialPlatformAdapter):
        platform = "facebook"

        def fetch_posts(self, limit=10):
            raise PlatformNotConfiguredError("no actor configured")

        def fetch_comments(self, posts, limit=50): return []
        def fetch_comment_context(self, comment): raise NotImplementedError
        def post_reply(self, comment, reply_text): raise NotImplementedError
        def check_existing_reply(self, comment): return False

    app = build_test_application(adapters={
        "facebook": BrokenAdapter(),
        "instagram": MockAdapter("instagram"),
    })
    report = _run(app)
    assert any("facebook" in e for e in report.errors)
    assert report.comments_new == 6                     # Instagram fixtures
    assert report.processed == 6


# ------------------------------------------------------------ 16. OpenAI timeout


def test_openai_timeout_escalates_and_publishes_nothing():
    settings = Settings()
    settings.openai.api_key = "sk-test"
    settings.openai.max_retries = 1
    transport = QueuedOpenAITransport(queue=[OpenAITimeoutError(), OpenAITimeoutError()])
    from social_agent.ai import AIDecisionService

    service = AIDecisionService(
        settings, client=OpenAIClient(settings.openai, transport=transport, sleep=lambda _: None)
    )
    result = service.decide(build_context(
        platform="facebook",
        comment=Comment(platform="facebook", platform_comment_id="x", comment_text="Beautiful!"),
    ))
    assert result.valid is False
    assert result.decision.action == "escalate"
    assert result.decision.reply is None
    assert len(transport.requests) == 2                # retried once, then gave up


def test_openai_rate_limit_is_retried_then_succeeds():
    settings = Settings()
    settings.openai.api_key = "sk-test"
    transport = QueuedOpenAITransport(queue=[
        OpenAIRateLimitError(retry_after=0.01),
        responses_payload(decision(reply="Lovely up there.")),
    ])
    client = OpenAIClient(settings.openai, transport=transport, sleep=lambda _: None)
    completion = client.complete_json(
        instructions="s", user_content="u", json_schema={"type": "object"}, schema_name="n"
    )
    assert completion.attempts == 2
    assert json.loads(completion.text)["action"] == "reply"


def test_a_client_error_is_not_retried():
    settings = Settings()
    settings.openai.api_key = "sk-test"
    transport = QueuedOpenAITransport(queue=[OpenAIError("bad request", status=400)])
    client = OpenAIClient(settings.openai, transport=transport, sleep=lambda _: None)
    with pytest.raises(OpenAIError):
        client.complete_json(
            instructions="s", user_content="u", json_schema={"type": "object"}, schema_name="n"
        )
    assert len(transport.requests) == 1


# ------------------------------------------ 17/18. Facebook & Instagram posting


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_a_posting_failure_is_recorded_and_not_silently_retried(platform):
    class FailingAdapter(MockAdapter):
        def post_reply(self, comment, reply_text):
            return ReplyResult(success=False, error="platform rejected the reply")

    app = build_test_application(adapters={platform: FailingAdapter(platform)})
    app.repos.settings.update(dry_run=False)
    _run(app, platforms=[platform])

    pending = app.repos.comments.list(status=CommentStatus.PENDING_APPROVAL.value, limit=1)
    assert pending, "expected something waiting for approval"
    result = app.agent.approve_reply(pending[0].id)

    assert result["ok"] is False
    assert "rejected" in (result.get("error") or result.get("reason") or "")
    # Nothing was recorded as posted, so a human retry cannot duplicate.
    assert app.repos.replies.has_posted_reply(pending[0].id) is False
    assert app.repos.comments.get(pending[0].id).status == CommentStatus.FAILED.value


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_publishing_without_a_reply_actor_fails_loudly(platform):
    """No actor is invented: the adapter says exactly what is missing."""
    from social_agent.apify import ActorRegistry
    from social_agent.database.models import AgentSettings
    from social_agent.social.apify_adapter import ApifySocialAdapter

    adapter = ApifySocialAdapter(
        platform,
        client=ApifyClient(ApifySettings(api_token="t")),
        registry=ActorRegistry(AgentSettings()),
        page_urls=["https://example.com/page"],
    )
    with pytest.raises(PlatformNotConfiguredError) as exc:
        adapter.post_reply(
            FetchedComment(platform=platform, platform_comment_id="c1"), "hello"
        )
    assert "REPLY_ACTOR" in str(exc.value)
    assert adapter.health()["can_publish"] is False


# ---------------------------------------------------- 19. repetitive replies


def test_repetition_is_detected():
    previous = ["Thank you! 😊", "Glad you like it!", "Thank you so much!"]
    assert is_repetitive("Thank you very much! 😊", previous)[0] is True
    assert is_repetitive("Glad you like it 😊", previous)[0] is True
    assert is_repetitive("Those Himalayan views are something else.", previous)[0] is False
    assert similarity("Thank you!", "Thank you!") == 1.0


def test_a_repetitive_reply_is_flagged_for_a_human():
    repeat = AgentDecision(
        action="reply", intent="compliment", confidence=0.98, reply="Thank you so much!",
        needs_human=False, reason="", risk_level="low",
    )
    result = apply_guardrails(
        repeat, comment_text="Beautiful!", previous_replies=["Thank you! 😊", "Thanks so much!"]
    )
    assert result.decision.needs_human is True
    assert result.decision.confidence <= 0.6
    assert any("similar" in v for v in result.violations)


def test_previous_replies_are_sent_to_the_model(app):
    _run(app)
    comment = _comment_by_text(app, "Beautiful!")
    app.agent.approve_reply(comment.id)
    recent = app.repos.replies.recent_reply_texts(platform="facebook")
    assert recent, "approved replies should be available as context"

    app.ai.client.transport.requests.clear()
    _decide(app, "Amazing views ❤️")
    sent = app.ai.client.transport.requests[-1]["payload"]
    body = json.dumps(sent)
    assert "previous_replies" in body
    assert recent[0][:20] in body


# --------------------------------------------------------- 20. approval flow


def test_human_approval_flow_end_to_end(app):
    _run(app)
    comment = _comment_by_text(app, "Beautiful!")
    assert comment.status == CommentStatus.PENDING_APPROVAL.value

    reply = app.repos.replies.latest_for_comment(comment.id)
    assert reply is not None and reply.approved is False and reply.posted is False

    result = app.agent.approve_reply(comment.id, approved_by="mohan")
    assert result["ok"] is True
    assert result["dry_run"] is True                  # dry run is on by default

    refreshed = app.repos.replies.latest_for_comment(comment.id)
    assert refreshed.approved is True
    assert refreshed.approved_by == "mohan"
    assert refreshed.posted is True and refreshed.dry_run is True
    assert app.repos.comments.get(comment.id).status == CommentStatus.DRY_RUN_REPLIED.value


def test_edit_and_reply_uses_the_edited_text(app):
    _run(app)
    comment = _comment_by_text(app, "Beautiful!")
    app.agent.approve_reply(comment.id, text="Glad it landed — that was a good morning up there.")
    reply = app.repos.replies.latest_for_comment(comment.id)
    assert reply.final_reply_text == "Glad it landed — that was a good morning up there."
    assert reply.reply_text != reply.final_reply_text     # the original is kept


def test_operator_can_ignore_and_escalate(app):
    _run(app)
    a = _comment_by_text(app, "Which mountain")
    b = _comment_by_text(app, "I visited Nepal")
    assert app.agent.ignore_comment(a.id)["ok"] is True
    assert app.agent.escalate_comment(b.id, note="pass to sales")["ok"] is True
    assert app.repos.comments.get(a.id).status == CommentStatus.IGNORED.value
    assert app.repos.comments.get(b.id).status == CommentStatus.ESCALATED.value


def test_dry_run_publishes_nothing_to_the_platform(app):
    adapter = app.adapters["facebook"]
    _run(app)
    for comment in app.repos.comments.list(status=CommentStatus.PENDING_APPROVAL.value, limit=50):
        app.agent.approve_reply(comment.id)
    assert adapter.published == [], "dry run must not reach the adapter"


def test_turning_dry_run_off_publishes(app):
    _run(app)
    app.repos.settings.update(dry_run=False)
    comment = _comment_by_text(app, "Beautiful!")
    result = app.agent.approve_reply(comment.id)
    assert result["published"] is True
    assert app.repos.comments.get(comment.id).status == CommentStatus.REPLIED.value
    assert any(p["comment_id"] == comment.platform_comment_id
               for p in app.adapters["facebook"].published)


# ------------------------------------------------------- supporting behaviour


def test_knowledge_marked_human_only_never_reaches_the_model(app):
    from social_agent.database.models import KnowledgeItem

    app.repos.knowledge.create(KnowledgeItem(
        title="Internal margin sheet", content="SECRET-MARGIN-42", category="policy",
        human_only=True,
    ))
    active = app.knowledge.active_items()
    assert all("SECRET-MARGIN-42" not in i.content for i in active)

    _decide(app, "How much does this cost?")
    body = json.dumps(app.ai.client.transport.requests[-1]["payload"])
    assert "SECRET-MARGIN-42" not in body


def test_expired_knowledge_is_not_offered(app):
    from social_agent.database.models import KnowledgeItem

    app.repos.knowledge.create(KnowledgeItem(
        title="Old monsoon notice", content="EXPIRED-NOTICE", category="notice",
        valid_from="2020-01-01", valid_until="2020-12-31",
    ))
    assert all("EXPIRED-NOTICE" not in i.content for i in app.knowledge.active_items())


def test_secrets_are_never_returned_by_the_api(app):
    from social_agent.dashboard.api import settings_get

    app.settings.openai.api_key = "sk-super-secret"
    app.settings.apify.api_token = "apify_super_secret"
    body = json.dumps(settings_get(app, {}, {}))
    assert "sk-super-secret" not in body
    assert "apify_super_secret" not in body
    assert '"api_key_configured": true' in body.replace("True", "true")


def test_settings_update_rejects_unknown_fields(app):
    from social_agent.dashboard.api import settings_update

    result = settings_update(app, {}, {"minimum_confidence": 0.75, "openai_api_key": "sk-evil"})
    assert result["ok"] is True
    assert "openai_api_key" in result["rejected"]
    assert app.repos.settings.get().minimum_confidence == 0.75


def test_reply_length_is_trimmed_on_a_sentence_boundary():
    text = "First sentence here. Second sentence here. Third sentence goes on and on."
    trimmed = trim_to_length(text, 45)
    assert len(trimmed) <= 45
    assert trimmed.endswith(".")


def test_replies_are_capped_at_three_sentences():
    long = AgentDecision(
        action="reply", intent="travel_question", confidence=0.9,
        reply="One. Two. Three. Four. Five.", needs_human=False, reason="", risk_level="low",
    )
    result = apply_guardrails(long, comment_text="Tell me about it", max_reply_length=240)
    assert result.decision.reply.count(".") <= 3


def test_banned_corporate_phrasing_is_flagged():
    corporate = AgentDecision(
        action="reply", intent="compliment", confidence=0.99,
        reply="Thank you for reaching out to us.", needs_human=False, reason="", risk_level="low",
    )
    result = apply_guardrails(corporate, comment_text="Beautiful!")
    assert result.decision.needs_human is True
    assert any("banned phrase" in v for v in result.violations)


def test_comments_without_a_stable_id_are_dropped():
    from social_agent.social.normalize import normalize_comment

    assert normalize_comment({"text": "no id here"}, "facebook") is None


def test_the_agent_uses_openai_and_never_anthropic():
    """Guards the core architectural constraint of the build."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "social_agent"
    banned = ("anthropic", "claude", "gemini", "kimi")
    offenders = []
    for path in root.rglob("*.py"):
        lowered = path.read_text(encoding="utf-8").lower()
        for term in banned:
            if term in lowered:
                offenders.append(f"{path.name}: {term}")
    assert not offenders, f"non-OpenAI provider referenced: {offenders}"


def test_openai_request_uses_strict_structured_output():
    settings = Settings()
    settings.openai.api_key = "sk-test"
    transport = QueuedOpenAITransport(queue=[responses_payload(decision())])
    client = OpenAIClient(settings.openai, transport=transport, sleep=lambda _: None)
    client.complete_json(
        instructions="sys", user_content="hi",
        json_schema={"type": "object"}, schema_name="north_nepal",
    )
    payload = transport.requests[0]["payload"]
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert transport.requests[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_dashboard_requires_a_password():
    from social_agent.config import DashboardSettings
    from social_agent.dashboard.auth import AuthNotConfiguredError, BasicAuth

    with pytest.raises(AuthNotConfiguredError):
        BasicAuth(DashboardSettings(password="", auth_enabled=True))

    auth = BasicAuth(DashboardSettings(username="admin", password="hunter2", auth_enabled=True))
    import base64

    good = base64.b64encode(b"admin:hunter2").decode()
    bad = base64.b64encode(b"admin:wrong").decode()
    assert auth.check(f"Basic {good}").ok is True
    assert auth.check(f"Basic {bad}").ok is False
    assert auth.check(None).ok is False


# ------------------------------------------------- token accounting & budget


def test_every_ai_call_is_recorded_including_failures(app):
    _run(app)
    totals = app.repos.usage.totals()
    assert totals["calls"] == 13                    # one per comment
    assert totals["total_tokens"] > 0
    assert totals["input_tokens"] > 0 and totals["output_tokens"] > 0


def test_a_failed_call_still_counts_against_spend():
    """A retry costs money whether or not its output was usable."""
    from social_agent.ai import AIDecisionService, OpenAIClient

    settings = Settings()
    settings.openai.api_key = "sk-test"
    transport = QueuedOpenAITransport(queue=[
        responses_payload({"garbage": 1}),
        responses_payload({"garbage": 2}),
    ])
    app = build_test_application(transport=transport, adapters={"facebook": MockAdapter("facebook")})
    _decide(app, "Beautiful!")

    totals = app.repos.usage.totals()
    assert totals["calls"] == 1
    assert totals["failed_calls"] == 1              # marked unusable, still billed


def test_cost_is_not_estimated_without_configured_rates(app):
    from social_agent.dashboard.api import overview

    _run(app)
    usage = overview(app, {}, {})["usage"]
    # No rates set: report null rather than a made-up figure.
    assert usage["rates_configured"] is False
    assert usage["estimated_cost_usd"] is None

    app.repos.settings.update(input_cost_per_million=1.25, output_cost_per_million=10.0)
    usage = overview(app, {}, {})["usage"]
    assert usage["rates_configured"] is True
    assert usage["estimated_cost_usd"] > 0


def test_cost_maths():
    from social_agent.database.models import AgentSettings

    db = Database(":memory:")
    db.migrate()
    repos = Repositories(db)
    settings = AgentSettings(input_cost_per_million=2.0, output_cost_per_million=10.0)
    cost = repos.usage.estimated_cost(
        {"input_tokens": 1_000_000, "output_tokens": 500_000}, settings
    )
    assert cost == 7.0                              # 1M*2 + 0.5M*10


def test_budget_cap_stops_processing(app):
    _run(app)
    used = app.repos.usage.totals()["total_tokens"]
    assert used > 0

    app.repos.settings.update(daily_token_budget=used // 2)
    calls_before = app.repos.usage.totals()["calls"]

    # New comments arrive, but the cap must stop them being processed.
    post = app.repos.posts.upsert(
        Post(platform="facebook", platform_post_id="p_new", caption="x")
    )
    app.repos.comments.insert_if_new(
        Comment(platform="facebook", platform_comment_id="c_over_budget",
                platform_post_id="p_new", post_id=post.id, comment_text="Beautiful!")
    )
    report = app.agent.run_cycle()

    assert report.budget_stopped is True
    assert report.processed == 0
    assert app.repos.usage.totals()["calls"] == calls_before   # no new spend
    assert any("budget" in e for e in report.errors)


def test_zero_budget_means_no_cap(app):
    assert app.repos.settings.get().daily_token_budget == 0
    report = _run(app)
    assert report.budget_stopped is False
    assert report.processed == 13


def test_budget_is_reported_in_health(app):
    _run(app)
    app.repos.settings.update(daily_token_budget=1_000_000)
    health = app.health()
    assert health["usage"]["calls"] == 13
    assert health["usage"]["budget"]["enabled"] is True
    assert health["usage"]["budget"]["exceeded"] is False
