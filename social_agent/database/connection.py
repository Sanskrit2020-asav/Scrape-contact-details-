"""SQLite connection handling and migrations.

SQLite is deliberate for V1: one file, no server to operate, transactional
guarantees strong enough to make the duplicate-reply constraint meaningful.
The repository layer is the only thing that touches SQL, so moving to Postgres
later is a change in this module plus the migration dialect — not a rewrite.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..observability import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Database:
    """A thin, thread-safe wrapper around a SQLite file."""

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._shared: sqlite3.Connection | None = None
        if path == ":memory:":
            # An in-memory database dies with its connection, so tests share one.
            self._shared = self._new_connection()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a unit of work atomically; roll back on any exception.

        An ``IntegrityError`` is not logged as a failure: the unique constraints
        on comments and posted replies are *how* duplicate protection works, so
        callers hit them routinely and handle them. Logging a traceback per
        already-seen comment would bury real errors under polling noise.
        """
        conn = self.connection
        try:
            with conn:
                yield conn
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error:
            log.exception("database transaction failed")
            raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def migrate(self) -> list[str]:
        """Apply every ``NNN_*.sql`` migration that has not run yet."""
        conn = self.connection
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.commit()
        applied = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        run: list[str] = []
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name in applied:
                continue
            with self.transaction() as tx:
                tx.executescript(sql_file.read_text(encoding="utf-8"))
                tx.execute("INSERT INTO schema_migrations (name) VALUES (?)", (sql_file.name,))
            run.append(sql_file.name)
            log.info("migration applied", extra={"migration": sql_file.name})
        return run

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


_db: Database | None = None


def get_database(path: str | None = None, refresh: bool = False) -> Database:
    """Return the process-wide database, running migrations on first use."""
    global _db
    if _db is None or refresh:
        from ..config import get_settings

        resolved = path or get_settings().database_path
        _db = Database(resolved)
        _db.migrate()
    return _db


def reset_database_handle() -> None:
    """Forget the cached handle (tests build their own)."""
    global _db
    if _db is not None:
        _db.close()
    _db = None
