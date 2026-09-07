"""Data access. The only module in the package that writes SQL.

Duplicate protection (spec §21) is implemented here in three layers:

1. ``UNIQUE (platform, platform_comment_id)`` — the same comment is stored once.
2. :meth:`CommentRepository.claim_new` — a comment moves ``new → processing``
   with a conditional UPDATE, so two concurrent cycles cannot both claim it.
3. ``idx_replies_one_posted_per_comment`` — at most one *posted* reply per
   comment, enforced by the database even if application code retries.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from ..observability import get_logger, get_request_id
from .connection import Database
from .models import (
    AgentSettings,
    AuditLog,
    Comment,
    CommentStatus,
    KnowledgeItem,
    Post,
    Reply,
    UsageRecord,
)

log = get_logger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to(cls, row: sqlite3.Row | None, bools: Sequence[str] = ()):
    if row is None:
        return None
    data = {k: row[k] for k in row.keys()}
    for name in bools:
        if name in data:
            data[name] = bool(data[name])
    known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in data.items() if k in known})


class PostRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, post: Post) -> Post:
        """Insert or refresh a post, keyed on (platform, platform_post_id)."""
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO social_posts
                    (platform, platform_post_id, account_id, url, caption,
                     content_type, topic, posted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, platform_post_id) DO UPDATE SET
                    caption      = excluded.caption,
                    url          = excluded.url,
                    content_type = excluded.content_type,
                    topic        = excluded.topic,
                    posted_at    = COALESCE(excluded.posted_at, social_posts.posted_at),
                    updated_at   = datetime('now')
                """,
                (
                    post.platform, post.platform_post_id, post.account_id, post.url,
                    post.caption, post.content_type, post.topic, post.posted_at,
                ),
            )
        found = self.get(post.platform, post.platform_post_id)
        assert found is not None  # the upsert above guarantees a row
        return found

    def get(self, platform: str, platform_post_id: str) -> Post | None:
        row = self.db.query_one(
            "SELECT * FROM social_posts WHERE platform = ? AND platform_post_id = ?",
            (platform, platform_post_id),
        )
        return _row_to(Post, row)

    def get_by_id(self, post_id: int) -> Post | None:
        return _row_to(Post, self.db.query_one("SELECT * FROM social_posts WHERE id = ?", (post_id,)))

    def list(self, limit: int = 100) -> list[Post]:
        rows = self.db.query("SELECT * FROM social_posts ORDER BY id DESC LIMIT ?", (limit,))
        return [_row_to(Post, r) for r in rows]


class CommentRepository:
    def __init__(self, db: Database):
        self.db = db

    def exists(self, platform: str, platform_comment_id: str) -> bool:
        return self.db.query_one(
            "SELECT 1 FROM social_comments WHERE platform = ? AND platform_comment_id = ?",
            (platform, platform_comment_id),
        ) is not None

    def insert_if_new(self, comment: Comment) -> Comment | None:
        """Store a freshly fetched comment. Returns ``None`` if already known.

        This is the first duplicate gate: a comment seen in a previous polling
        cycle is silently skipped rather than reprocessed.
        """
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO social_comments
                        (platform, platform_comment_id, post_id, platform_post_id,
                         parent_comment_id, author_id, author_name, comment_text,
                         comment_url, comment_created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment.platform, comment.platform_comment_id, comment.post_id,
                        comment.platform_post_id, comment.parent_comment_id,
                        comment.author_id, comment.author_name, comment.comment_text,
                        comment.comment_url, comment.comment_created_at,
                        comment.status or CommentStatus.NEW.value,
                    ),
                )
                comment.id = int(cur.lastrowid)
                return comment
        except sqlite3.IntegrityError:
            return None

    def claim_new(self, limit: int) -> list[Comment]:
        """Atomically move up to ``limit`` NEW comments into PROCESSING.

        The conditional UPDATE means a comment can only be claimed once, so two
        overlapping cycles never send the same comment to OpenAI twice.
        """
        claimed: list[Comment] = []
        rid = get_request_id()
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM social_comments WHERE status = ? ORDER BY id ASC LIMIT ?",
                (CommentStatus.NEW.value, limit),
            ).fetchall()
            for row in rows:
                cur = conn.execute(
                    """
                    UPDATE social_comments
                       SET status = ?, request_id = ?, updated_at = datetime('now')
                     WHERE id = ? AND status = ?
                    """,
                    (CommentStatus.PROCESSING.value, rid, row["id"], CommentStatus.NEW.value),
                )
                if cur.rowcount == 1:
                    claimed.append(row["id"])
        return [c for c in (self.get(cid) for cid in claimed) if c is not None]

    def get(self, comment_id: int) -> Comment | None:
        return _row_to(Comment, self.db.query_one("SELECT * FROM social_comments WHERE id = ?", (comment_id,)))

    def get_by_platform_id(self, platform: str, platform_comment_id: str) -> Comment | None:
        return _row_to(Comment, self.db.query_one(
            "SELECT * FROM social_comments WHERE platform = ? AND platform_comment_id = ?",
            (platform, platform_comment_id),
        ))

    def save_decision(
        self,
        comment_id: int,
        *,
        intent: str,
        confidence: float,
        risk_level: str,
        action: str,
        status: str,
        reason: str,
    ) -> None:
        self.db.execute(
            """
            UPDATE social_comments
               SET intent = ?, confidence = ?, risk_level = ?, action = ?,
                   status = ?, reason = ?, processed_at = ?, updated_at = datetime('now')
             WHERE id = ?
            """,
            (intent, confidence, risk_level, action, status, reason, utc_now(), comment_id),
        )

    def set_status(self, comment_id: int, status: str) -> None:
        self.db.execute(
            "UPDATE social_comments SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, comment_id),
        )

    def list(
        self,
        *,
        status: str | Iterable[str] | None = None,
        platform: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Comment]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if platform:
            where.append("platform = ?")
            params.append(platform)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([limit, offset])
        rows = self.db.query(
            f"SELECT * FROM social_comments {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        return [_row_to(Comment, r) for r in rows]

    def counts_by_status(self) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS n FROM social_comments GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def counts_by_platform(self) -> dict[str, int]:
        rows = self.db.query("SELECT platform, COUNT(*) AS n FROM social_comments GROUP BY platform")
        return {r["platform"]: r["n"] for r in rows}

    def total(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM social_comments")
        return int(row["n"]) if row else 0


class ReplyRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, reply: Reply) -> Reply:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_replies
                    (comment_id, reply_text, final_reply_text, model, confidence,
                     approved, approved_by, approved_at, auto, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reply.comment_id, reply.reply_text, reply.final_reply_text or reply.reply_text,
                    reply.model, reply.confidence, int(reply.approved), reply.approved_by,
                    reply.approved_at, int(reply.auto), int(reply.dry_run),
                ),
            )
            reply.id = int(cur.lastrowid)
        return reply

    def get(self, reply_id: int) -> Reply | None:
        return _row_to(
            Reply,
            self.db.query_one("SELECT * FROM ai_replies WHERE id = ?", (reply_id,)),
            bools=("approved", "posted", "auto", "dry_run"),
        )

    def latest_for_comment(self, comment_id: int) -> Reply | None:
        return _row_to(
            Reply,
            self.db.query_one(
                "SELECT * FROM ai_replies WHERE comment_id = ? ORDER BY id DESC LIMIT 1",
                (comment_id,),
            ),
            bools=("approved", "posted", "auto", "dry_run"),
        )

    def has_posted_reply(self, comment_id: int) -> bool:
        """Second duplicate gate: has anything already been published here?"""
        return self.db.query_one(
            "SELECT 1 FROM ai_replies WHERE comment_id = ? AND posted = 1", (comment_id,)
        ) is not None

    def approve(self, reply_id: int, *, text: str | None = None, approved_by: str = "operator") -> Reply | None:
        with self.db.transaction() as conn:
            if text is None:
                conn.execute(
                    """
                    UPDATE ai_replies SET approved = 1, approved_by = ?, approved_at = ?,
                                          updated_at = datetime('now')
                     WHERE id = ?
                    """,
                    (approved_by, utc_now(), reply_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE ai_replies SET approved = 1, approved_by = ?, approved_at = ?,
                                          final_reply_text = ?, updated_at = datetime('now')
                     WHERE id = ?
                    """,
                    (approved_by, utc_now(), text, reply_id),
                )
        return self.get(reply_id)

    def mark_posted(self, reply_id: int, platform_reply_id: str | None, *, dry_run: bool) -> bool:
        """Record a successful publish. Returns ``False`` if one already exists.

        The partial unique index makes the second write fail loudly rather than
        creating a duplicate reply record, which is exactly what we want on a
        retry that raced with a successful first attempt.
        """
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE ai_replies
                       SET posted = 1, posted_at = ?, platform_reply_id = ?, dry_run = ?,
                           updated_at = datetime('now')
                     WHERE id = ? AND posted = 0
                    """,
                    (utc_now(), platform_reply_id, int(dry_run), reply_id),
                )
            return True
        except sqlite3.IntegrityError:
            log.warning("duplicate posted reply prevented", extra={"reply_id": reply_id})
            return False

    def mark_error(self, reply_id: int, error: str) -> None:
        self.db.execute(
            "UPDATE ai_replies SET error = ?, updated_at = datetime('now') WHERE id = ?",
            (error[:2000], reply_id),
        )

    def recent_reply_texts(self, *, platform: str | None = None, limit: int = 12) -> list[str]:
        """Most recent published/approved replies — fed back to the AI so it
        does not repeat itself (spec §14)."""
        if platform:
            rows = self.db.query(
                """
                SELECT r.final_reply_text AS t
                  FROM ai_replies r JOIN social_comments c ON c.id = r.comment_id
                 WHERE c.platform = ? AND TRIM(COALESCE(r.final_reply_text, '')) <> ''
                 ORDER BY r.id DESC LIMIT ?
                """,
                (platform, limit),
            )
        else:
            rows = self.db.query(
                "SELECT final_reply_text AS t FROM ai_replies "
                "WHERE TRIM(COALESCE(final_reply_text, '')) <> '' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        return [r["t"] for r in rows]

    def history(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.db.query(
            """
            SELECT r.id, r.reply_text, r.final_reply_text, r.model, r.confidence,
                   r.approved, r.posted, r.posted_at, r.platform_reply_id, r.auto,
                   r.dry_run, r.error, r.created_at,
                   c.id AS comment_pk, c.platform, c.author_name, c.comment_text,
                   c.intent, c.risk_level, c.status, c.comment_url
              FROM ai_replies r JOIN social_comments c ON c.id = r.comment_id
             ORDER BY r.id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            for flag in ("approved", "posted", "auto", "dry_run"):
                d[flag] = bool(d[flag])
            out.append(d)
        return out

    def counts(self) -> dict[str, int]:
        row = self.db.query_one(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(posted), 0) AS posted,
                   COALESCE(SUM(auto), 0)   AS auto,
                   COALESCE(SUM(CASE WHEN posted = 1 AND dry_run = 1 THEN 1 ELSE 0 END), 0) AS dry_run_posted
              FROM ai_replies
            """
        )
        return {k: int(row[k]) for k in row.keys()} if row else {}


class KnowledgeRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, item: KnowledgeItem) -> KnowledgeItem:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO knowledge_items
                    (title, content, category, status, valid_from, valid_until, human_only)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.title, item.content, item.category, item.status,
                    item.valid_from, item.valid_until, int(item.human_only),
                ),
            )
            item.id = int(cur.lastrowid)
        return item

    def update(self, item_id: int, **fields: Any) -> KnowledgeItem | None:
        allowed = {"title", "content", "category", "status", "valid_from", "valid_until", "human_only"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get(item_id)
        if "human_only" in updates:
            updates["human_only"] = int(bool(updates["human_only"]))
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(
            f"UPDATE knowledge_items SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            (*updates.values(), item_id),
        )
        return self.get(item_id)

    def delete(self, item_id: int) -> None:
        self.db.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))

    def get(self, item_id: int) -> KnowledgeItem | None:
        return _row_to(
            KnowledgeItem,
            self.db.query_one("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)),
            bools=("human_only",),
        )

    def list(self, *, status: str | None = None, limit: int = 500) -> list[KnowledgeItem]:
        if status:
            rows = self.db.query(
                "SELECT * FROM knowledge_items WHERE status = ? ORDER BY category, title LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.db.query("SELECT * FROM knowledge_items ORDER BY category, title LIMIT ?", (limit,))
        return [_row_to(KnowledgeItem, r, bools=("human_only",)) for r in rows]

    def active_items(self, *, now: str | None = None) -> list[KnowledgeItem]:
        """Only ACTIVE, currently valid, non-``human_only`` knowledge.

        This is the *only* company-specific information the AI is ever shown.
        """
        today = now or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self.db.query(
            """
            SELECT * FROM knowledge_items
             WHERE status = 'active'
               AND human_only = 0
               AND (valid_from  IS NULL OR valid_from  = '' OR valid_from  <= ?)
               AND (valid_until IS NULL OR valid_until = '' OR valid_until >= ?)
             ORDER BY category, title
            """,
            (today, today),
        )
        return [_row_to(KnowledgeItem, r, bools=("human_only",)) for r in rows]


class SettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    def bootstrap(self, defaults) -> AgentSettings:
        """Create the singleton settings row from environment defaults once."""
        if self.db.query_one("SELECT 1 FROM agent_settings WHERE id = 1") is None:
            self.db.execute(
                """
                INSERT INTO agent_settings
                    (id, dry_run, human_approval_required, auto_reply_enabled,
                     minimum_confidence, max_reply_length, polling_interval_seconds,
                     max_comments_per_run, max_posts_per_run, openai_model,
                     facebook_enabled, instagram_enabled,
                     facebook_comments_actor, facebook_posts_actor, facebook_reply_actor,
                     instagram_comments_actor, instagram_posts_actor, instagram_reply_actor,
                     high_engagement_threshold, daily_token_budget,
                     input_cost_per_million, output_cost_per_million)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(defaults.dry_run), int(defaults.human_approval_required),
                    int(defaults.auto_reply_enabled), float(defaults.minimum_confidence),
                    int(defaults.max_reply_length), int(defaults.polling_interval_seconds),
                    int(defaults.max_comments_per_run), int(defaults.max_posts_per_run),
                    defaults.openai_model,
                    1, 1,
                    defaults.facebook_comments_actor, defaults.facebook_posts_actor,
                    defaults.facebook_reply_actor, defaults.instagram_comments_actor,
                    defaults.instagram_posts_actor, defaults.instagram_reply_actor,
                    int(defaults.high_engagement_threshold),
                    int(defaults.daily_token_budget), float(defaults.input_cost_per_million),
                    float(defaults.output_cost_per_million),
                ),
            )
        return self.get()

    def get(self) -> AgentSettings:
        row = self.db.query_one("SELECT * FROM agent_settings WHERE id = 1")
        if row is None:
            return AgentSettings()
        return _row_to(
            AgentSettings, row,
            bools=("dry_run", "human_approval_required", "auto_reply_enabled",
                   "facebook_enabled", "instagram_enabled"),
        )

    def update(self, **fields: Any) -> AgentSettings:
        allowed = set(AgentSettings.EDITABLE)
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get()
        for key, value in list(updates.items()):
            if isinstance(getattr(AgentSettings(), key), bool):
                updates[key] = int(bool(value))
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(
            f"UPDATE agent_settings SET {assignments}, updated_at = datetime('now') WHERE id = 1",
            tuple(updates.values()),
        )
        return self.get()


class UsageRepository:
    """Token accounting.

    Recorded for *every* call, including failed and retried ones — a retry
    costs real money whether or not its output was usable, so a ledger that
    only counted successes would understate the bill.
    """

    def __init__(self, db: Database):
        self.db = db

    def record(self, usage: UsageRecord) -> UsageRecord:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_usage
                    (comment_id, platform, model, input_tokens, output_tokens,
                     total_tokens, attempts, request_id, response_id, succeeded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage.comment_id, usage.platform, usage.model,
                    usage.input_tokens, usage.output_tokens, usage.total_tokens,
                    usage.attempts, usage.request_id, usage.response_id,
                    int(usage.succeeded),
                ),
            )
            usage.id = int(cur.lastrowid)
        return usage

    def totals(self, *, since_hours: int | None = None) -> dict[str, int]:
        """Aggregate usage, optionally limited to the last ``since_hours``."""
        clause, params = "", ()
        if since_hours is not None:
            clause = "WHERE created_at >= datetime('now', ?)"
            params = (f"-{int(since_hours)} hours",)
        row = self.db.query_one(
            f"""
            SELECT COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                   COUNT(*)                        AS calls,
                   COALESCE(SUM(CASE WHEN succeeded = 0 THEN 1 ELSE 0 END), 0) AS failed_calls
              FROM ai_usage {clause}
            """,
            params,
        )
        return {k: int(row[k]) for k in row.keys()} if row else {}

    def tokens_last_24h(self) -> int:
        return self.totals(since_hours=24).get("total_tokens", 0)

    def estimated_cost(self, totals: dict[str, int], settings: AgentSettings) -> float:
        """Cost in USD from the operator-configured rates.

        Returns 0.0 when no rates are set, and the dashboard says "not
        configured" rather than showing a made-up figure.
        """
        return round(
            (totals.get("input_tokens", 0) / 1_000_000) * settings.input_cost_per_million
            + (totals.get("output_tokens", 0) / 1_000_000) * settings.output_cost_per_million,
            4,
        )

    def budget_status(self, settings: AgentSettings) -> dict[str, Any]:
        """Where spend stands against the configured daily cap."""
        used = self.tokens_last_24h()
        budget = int(settings.daily_token_budget or 0)
        return {
            "tokens_last_24h": used,
            "daily_token_budget": budget,
            "enabled": budget > 0,
            "exceeded": budget > 0 and used >= budget,
            "remaining": max(0, budget - used) if budget > 0 else None,
        }

    def recent(self, limit: int = 50) -> list[UsageRecord]:
        rows = self.db.query("SELECT * FROM ai_usage ORDER BY id DESC LIMIT ?", (limit,))
        return [_row_to(UsageRecord, r, bools=("succeeded",)) for r in rows]


class AuditRepository:
    def __init__(self, db: Database):
        self.db = db

    def log(
        self,
        event_type: str,
        *,
        level: str = "info",
        platform: str = "",
        comment_id: int | None = None,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO audit_logs (event_type, level, platform, comment_id, request_id, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type, level, platform, comment_id,
                request_id if request_id is not None else get_request_id(),
                json.dumps(details or {}, ensure_ascii=False, default=str),
            ),
        )

    def list(self, *, level: str | None = None, limit: int = 200, offset: int = 0) -> list[AuditLog]:
        if level:
            rows = self.db.query(
                "SELECT * FROM audit_logs WHERE level = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (level, limit, offset),
            )
        else:
            rows = self.db.query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        out = []
        for r in rows:
            entry = _row_to(AuditLog, r)
            try:
                entry.details = json.loads(r["details"])
            except (TypeError, ValueError):
                entry.details = {"raw": r["details"]}
            out.append(entry)
        return out


class Repositories:
    """Bundle handed to services so they take one dependency, not six."""

    def __init__(self, db: Database):
        self.db = db
        self.posts = PostRepository(db)
        self.comments = CommentRepository(db)
        self.replies = ReplyRepository(db)
        self.knowledge = KnowledgeRepository(db)
        self.settings = SettingsRepository(db)
        self.usage = UsageRepository(db)
        self.audit = AuditRepository(db)
