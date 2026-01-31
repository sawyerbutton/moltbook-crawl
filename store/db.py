"""SQLite storage with WAL mode, schema management, and CRUD operations."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    post_count INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS submolts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    description TEXT,
    subscriber_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    url TEXT,
    upvotes INTEGER NOT NULL DEFAULT 0,
    downvotes INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    author_id TEXT,
    submolt_id TEXT,
    fetched_at TEXT NOT NULL,
    comments_fetched INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (author_id) REFERENCES agents(id),
    FOREIGN KEY (submolt_id) REFERENCES submolts(id)
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    author_id TEXT,
    parent_id TEXT,
    content TEXT,
    created_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (author_id) REFERENCES agents(id),
    FOREIGN KEY (parent_id) REFERENCES comments(id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    kind TEXT NOT NULL,
    records_fetched INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running'
);

-- Analysis tables (stubs for future LLM integration)
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    parent_id INTEGER,
    FOREIGN KEY (parent_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS post_topics (
    post_id TEXT NOT NULL,
    topic_id INTEGER NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (post_id, topic_id),
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS post_summaries (
    post_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id)
);

CREATE TABLE IF NOT EXISTS agent_profiles (
    agent_id TEXT PRIMARY KEY,
    bio_summary TEXT,
    interests TEXT,  -- JSON array
    personality TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS agent_relationships (
    agent_a TEXT NOT NULL,
    agent_b TEXT NOT NULL,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    relationship_type TEXT,
    PRIMARY KEY (agent_a, agent_b),
    FOREIGN KEY (agent_a) REFERENCES agents(id),
    FOREIGN KEY (agent_b) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS trend_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL  -- JSON
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_posts_author_id ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_posts_submolt_id ON posts(submolt_id);
CREATE INDEX IF NOT EXISTS idx_posts_comments_unfetched ON posts(comments_fetched) WHERE comments_fetched = 0;
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_author_id ON comments(author_id);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA_SQL)
        return self._conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Sync state ---

    def get_sync_state(self, key: str, default: str = "") -> str:
        conn = self.connect()
        row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_sync_state(self, key: str, value: str):
        conn = self.connect()
        conn.execute(
            "INSERT INTO sync_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()

    # --- Crawl log ---

    def start_crawl_log(self, kind: str) -> int:
        conn = self.connect()
        cur = conn.execute(
            "INSERT INTO crawl_log(started_at, kind, status) VALUES (?, ?, 'running')",
            (_now(), kind),
        )
        conn.commit()
        return cur.lastrowid

    def finish_crawl_log(self, log_id: int, records: int, errors: int, status: str = "done"):
        conn = self.connect()
        conn.execute(
            "UPDATE crawl_log SET finished_at = ?, records_fetched = ?, errors = ?, status = ? WHERE id = ?",
            (_now(), records, errors, status, log_id),
        )
        conn.commit()

    # --- Agents ---

    def upsert_agent(self, agent_id: str, name: str, now: Optional[str] = None):
        now = now or _now()
        conn = self.connect()
        conn.execute(
            """INSERT INTO agents(id, name, first_seen, last_seen)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name = excluded.name,
                 last_seen = excluded.last_seen""",
            (agent_id, name, now, now),
        )

    # --- Submolts ---

    def upsert_submolt(self, submolt: dict):
        conn = self.connect()
        conn.execute(
            """INSERT INTO submolts(id, name, display_name, description, subscriber_count)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name = excluded.name,
                 display_name = excluded.display_name,
                 description = excluded.description,
                 subscriber_count = excluded.subscriber_count""",
            (
                submolt["id"],
                submolt["name"],
                submolt.get("display_name"),
                submolt.get("description"),
                submolt.get("subscriber_count", 0),
            ),
        )

    # --- Posts ---

    def upsert_post(self, post: dict):
        """Upsert a post and its author/submolt references."""
        conn = self.connect()
        now = _now()

        author = post.get("author") or {}
        submolt = post.get("submolt") or {}

        if author.get("id"):
            self.upsert_agent(author["id"], author.get("name", ""), post.get("created_at", now))
        if submolt.get("id"):
            self.upsert_submolt(submolt)

        conn.execute(
            """INSERT INTO posts(id, title, content, url, upvotes, downvotes,
                   comment_count, created_at, author_id, submolt_id, fetched_at, comments_fetched)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title = excluded.title,
                 content = excluded.content,
                 url = excluded.url,
                 upvotes = excluded.upvotes,
                 downvotes = excluded.downvotes,
                 comment_count = excluded.comment_count,
                 fetched_at = excluded.fetched_at""",
            (
                post["id"],
                post.get("title", ""),
                post.get("content"),
                post.get("url"),
                post.get("upvotes", 0),
                post.get("downvotes", 0),
                post.get("comment_count", 0),
                post.get("created_at", now),
                author.get("id"),
                submolt.get("id"),
                now,
                0,
            ),
        )

    def mark_comments_fetched(self, post_id: str):
        conn = self.connect()
        conn.execute("UPDATE posts SET comments_fetched = 1 WHERE id = ?", (post_id,))

    def get_posts_needing_comments(self, limit: int = 100) -> list[dict]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT id, comment_count FROM posts WHERE comments_fetched = 0 ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Comments ---

    def upsert_comment(self, comment: dict, post_id: str):
        conn = self.connect()
        now = _now()
        author = comment.get("author") or {}

        if author.get("id"):
            self.upsert_agent(author["id"], author.get("name", ""), comment.get("created_at", now))

        conn.execute(
            """INSERT INTO comments(id, post_id, author_id, parent_id, content, created_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 content = excluded.content,
                 fetched_at = excluded.fetched_at""",
            (
                comment["id"],
                post_id,
                author.get("id"),
                comment.get("parent_id"),
                comment.get("content"),
                comment.get("created_at", now),
                now,
            ),
        )

    # --- Queries for Streamlit ---

    def count_table(self, table: str) -> int:
        conn = self.connect()
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def get_stats(self) -> dict:
        return {
            "agents": self.count_table("agents"),
            "submolts": self.count_table("submolts"),
            "posts": self.count_table("posts"),
            "comments": self.count_table("comments"),
        }

    def query_posts(
        self,
        sort: str = "new",
        submolt_id: Optional[str] = None,
        author_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        conn = self.connect()
        where_clauses = []
        params: list[Any] = []

        if submolt_id:
            where_clauses.append("p.submolt_id = ?")
            params.append(submolt_id)
        if author_id:
            where_clauses.append("p.author_id = ?")
            params.append(author_id)

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        order = {
            "new": "p.created_at DESC",
            "hot": "(p.upvotes - p.downvotes) DESC",
            "comments": "p.comment_count DESC",
        }.get(sort, "p.created_at DESC")

        sql = f"""
            SELECT p.*, a.name as author_name, s.name as submolt_name, s.display_name as submolt_display_name
            FROM posts p
            LEFT JOIN agents a ON p.author_id = a.id
            LEFT JOIN submolts s ON p.submolt_id = s.id
            {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_posts(
        self,
        submolt_id: Optional[str] = None,
        author_id: Optional[str] = None,
    ) -> int:
        conn = self.connect()
        where_clauses = []
        params: list[Any] = []
        if submolt_id:
            where_clauses.append("submolt_id = ?")
            params.append(submolt_id)
        if author_id:
            where_clauses.append("author_id = ?")
            params.append(author_id)
        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        return conn.execute(f"SELECT COUNT(*) FROM posts {where}", params).fetchone()[0]

    def get_post_with_comments(self, post_id: str) -> tuple[Optional[dict], list[dict]]:
        conn = self.connect()
        post_row = conn.execute(
            """SELECT p.*, a.name as author_name, s.name as submolt_name, s.display_name as submolt_display_name
               FROM posts p
               LEFT JOIN agents a ON p.author_id = a.id
               LEFT JOIN submolts s ON p.submolt_id = s.id
               WHERE p.id = ?""",
            (post_id,),
        ).fetchone()
        if not post_row:
            return None, []

        comment_rows = conn.execute(
            """SELECT c.*, a.name as author_name
               FROM comments c
               LEFT JOIN agents a ON c.author_id = a.id
               WHERE c.post_id = ?
               ORDER BY c.created_at""",
            (post_id,),
        ).fetchall()
        return dict(post_row), [dict(r) for r in comment_rows]

    def search_agents(self, query: str, limit: int = 20) -> list[dict]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM agents WHERE name LIKE ? ORDER BY post_count + comment_count DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_top_agents(self, order_by: str = "post_count", limit: int = 20) -> list[dict]:
        conn = self.connect()
        col = order_by if order_by in ("post_count", "comment_count") else "post_count"
        rows = conn.execute(
            f"SELECT * FROM agents ORDER BY {col} DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_agent_by_id(self, agent_id: str) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None

    def get_agent_by_name(self, name: str) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def get_submolts(self, order_by: str = "subscriber_count", limit: int = 50, offset: int = 0) -> list[dict]:
        conn = self.connect()
        col = order_by if order_by in ("subscriber_count", "name") else "subscriber_count"
        rows = conn.execute(
            f"SELECT * FROM submolts ORDER BY {col} DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_submolt_by_id(self, submolt_id: str) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM submolts WHERE id = ?", (submolt_id,)).fetchone()
        return dict(row) if row else None

    def get_submolt_by_name(self, name: str) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM submolts WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def get_daily_counts(self, table: str = "posts", days: int = 30) -> list[dict]:
        conn = self.connect()
        rows = conn.execute(
            f"""SELECT DATE(created_at) as date, COUNT(*) as count
                FROM {table}
                GROUP BY DATE(created_at)
                ORDER BY date DESC
                LIMIT ?""",
            (days,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_crawl_logs(self, limit: int = 20) -> list[dict]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM crawl_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_agent_counts(self):
        """Recompute post_count and comment_count for all agents."""
        conn = self.connect()
        conn.execute("""
            UPDATE agents SET
                post_count = (SELECT COUNT(*) FROM posts WHERE author_id = agents.id),
                comment_count = (SELECT COUNT(*) FROM comments WHERE author_id = agents.id)
        """)
        conn.commit()
