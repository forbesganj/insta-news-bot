"""SQLite persistence: items, stories, posts, metrics, learned weights, run log, key-values."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT,
    domain TEXT,
    tier INTEGER,
    origin TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    story_id TEXT
);
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    tokens TEXT NOT NULL,
    status TEXT NOT NULL,
    pillar TEXT,
    score REAL DEFAULT 0,
    sources TEXT NOT NULL DEFAULT '[]',
    facts TEXT NOT NULL DEFAULT '[]',
    verification TEXT,
    script TEXT,
    caption TEXT,
    hook_formula INTEGER,
    video_path TEXT,
    video_url TEXT,
    queue_key TEXT,
    approval TEXT DEFAULT 'none',
    sensitivity TEXT DEFAULT 'low',
    meta TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_stories_status ON stories(status);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    media_id TEXT,
    permalink TEXT,
    container_id TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0,
    pillar TEXT,
    hook_formula INTEGER,
    source_domain TEXT,
    duration_s REAL,
    published_at TEXT NOT NULL,
    hour_ist INTEGER
);
CREATE TABLE IF NOT EXISTS metrics (
    post_id TEXT NOT NULL,
    checkpoint_h INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    reach REAL, views REAL, likes REAL, comments REAL, shares REAL, saved REAL,
    total_interactions REAL, avg_watch_ms REAL,
    perf REAL,
    raw TEXT,
    PRIMARY KEY (post_id, checkpoint_h)
);
CREATE TABLE IF NOT EXISTS weights (
    dim TEXT NOT NULL,
    key TEXT NOT NULL,
    n INTEGER NOT NULL,
    mean REAL NOT NULL,
    multiplier REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dim, key)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


JSON_FIELDS = {"sources", "facts", "verification", "script", "meta", "tokens"}


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for key in JSON_FIELDS:
            if key in data and isinstance(data[key], str):
                try:
                    data[key] = json.loads(data[key])
                except ValueError:
                    pass
        return data

    # ------------------------------------------------------------------ items
    def item_seen(self, url_hash: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM items WHERE url_hash=?", (url_hash,)).fetchone()
        return row is not None

    def add_item(self, url_hash: str, item: dict[str, Any], story_id: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO items(url_hash,url,title,source,domain,tier,origin,published_at,fetched_at,story_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (url_hash, item["url"], item["title"], item.get("source"), item.get("domain"),
                 item.get("tier"), item.get("origin"), item.get("published_at"), iso(utcnow()), story_id),
            )

    # ---------------------------------------------------------------- stories
    def new_story_id(self) -> str:
        return "s" + uuid.uuid4().hex[:11]

    def create_story(self, title: str, tokens: Iterable[str], sources: list[dict], pillar: str,
                     status: str = "candidate") -> str:
        story_id = self.new_story_id()
        now = iso(utcnow())
        with self.tx() as c:
            c.execute(
                "INSERT INTO stories(id,title,tokens,status,pillar,sources,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (story_id, title, json.dumps(sorted(tokens)), status, pillar, json.dumps(sources), now, now),
            )
        return story_id

    def get_story(self, story_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
        return self._decode(row)

    def update_story(self, story_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for key, value in fields.items():
            cols.append(f"{key}=?")
            vals.append(json.dumps(value) if key in JSON_FIELDS and not isinstance(value, str) else value)
        cols.append("updated_at=?")
        vals.append(iso(utcnow()))
        vals.append(story_id)
        with self.tx() as c:
            c.execute(f"UPDATE stories SET {', '.join(cols)} WHERE id=?", vals)

    def merge_meta(self, story_id: str, **meta: Any) -> None:
        story = self.get_story(story_id) or {}
        current = story.get("meta") or {}
        if not isinstance(current, dict):
            current = {}
        current.update(meta)
        self.update_story(story_id, meta=current)

    def list_stories(self, status: str | Iterable[str] | None = None, limit: int = 500,
                     order: str = "created_at DESC") -> list[dict[str, Any]]:
        sql = "SELECT * FROM stories"
        params: list[Any] = []
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            sql += " WHERE status IN (%s)" % ",".join("?" * len(statuses))
            params += statuses
        sql += f" ORDER BY {order} LIMIT ?"
        params.append(limit)
        return [self._decode(r) for r in self.conn.execute(sql, params).fetchall()]

    def recent_stories(self, days: int, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        cutoff = iso(utcnow() - timedelta(days=days))
        sql = "SELECT * FROM stories WHERE created_at>=?"
        params: list[Any] = [cutoff]
        if statuses:
            statuses = list(statuses)
            sql += " AND status IN (%s)" % ",".join("?" * len(statuses))
            params += statuses
        return [self._decode(r) for r in self.conn.execute(sql, params).fetchall()]

    def count_status(self, status: str) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM stories WHERE status=?", (status,)).fetchone()[0])

    # ------------------------------------------------------------------ posts
    def add_post(self, story_id: str, media_id: str | None, permalink: str | None, dry_run: bool,
                 pillar: str | None, hook_formula: int | None, source_domain: str | None,
                 duration_s: float | None, container_id: str | None = None) -> str:
        post_id = "p" + uuid.uuid4().hex[:11]
        now = utcnow()
        hour_ist = (now + timedelta(hours=5, minutes=30)).hour
        with self.tx() as c:
            c.execute(
                "INSERT INTO posts(id,story_id,media_id,permalink,container_id,dry_run,pillar,hook_formula,"
                "source_domain,duration_s,published_at,hour_ist) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (post_id, story_id, media_id, permalink, container_id, 1 if dry_run else 0, pillar,
                 hook_formula, source_domain, duration_s, iso(now), hour_ist),
            )
        return post_id

    def list_posts(self, real_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM posts"
        if real_only:
            sql += " WHERE dry_run=0"
        sql += " ORDER BY published_at DESC"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def posts_since(self, hours: float, real_only: bool = True) -> list[dict[str, Any]]:
        cutoff = iso(utcnow() - timedelta(hours=hours))
        sql = "SELECT * FROM posts WHERE published_at>=?"
        if real_only:
            sql += " AND dry_run=0"
        return [dict(r) for r in self.conn.execute(sql, (cutoff,)).fetchall()]

    def last_post_time(self, real_only: bool = True) -> datetime | None:
        sql = "SELECT published_at FROM posts"
        if real_only:
            sql += " WHERE dry_run=0"
        sql += " ORDER BY published_at DESC LIMIT 1"
        row = self.conn.execute(sql).fetchone()
        return parse_iso(row[0]) if row else None

    # ---------------------------------------------------------------- metrics
    def has_metrics(self, post_id: str, checkpoint_h: int) -> bool:
        row = self.conn.execute("SELECT 1 FROM metrics WHERE post_id=? AND checkpoint_h=?",
                                (post_id, checkpoint_h)).fetchone()
        return row is not None

    def save_metrics(self, post_id: str, checkpoint_h: int, values: dict[str, float | None],
                     perf: float | None, raw: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO metrics(post_id,checkpoint_h,fetched_at,reach,views,likes,comments,shares,"
                "saved,total_interactions,avg_watch_ms,perf,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (post_id, checkpoint_h, iso(utcnow()), values.get("reach"), values.get("views"),
                 values.get("likes"), values.get("comments"), values.get("shares"), values.get("saved"),
                 values.get("total_interactions"), values.get("ig_reels_avg_watch_time"), perf, json.dumps(raw)),
            )

    def set_perf(self, post_id: str, checkpoint_h: int, perf: float) -> None:
        with self.tx() as c:
            c.execute("UPDATE metrics SET perf=? WHERE post_id=? AND checkpoint_h=?", (perf, post_id, checkpoint_h))

    def latest_metrics(self) -> list[dict[str, Any]]:
        sql = (
            "SELECT m.*, p.pillar, p.hook_formula, p.source_domain, p.hour_ist, p.duration_s, p.story_id "
            "FROM metrics m JOIN posts p ON p.id=m.post_id "
            "WHERE m.checkpoint_h=(SELECT MAX(checkpoint_h) FROM metrics WHERE post_id=m.post_id) AND p.dry_run=0"
        )
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    # ---------------------------------------------------------------- weights
    def set_weight(self, dim: str, key: str, n: int, mean: float, multiplier: float) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO weights(dim,key,n,mean,multiplier,updated_at) VALUES(?,?,?,?,?,?)",
                      (dim, str(key), n, mean, multiplier, iso(utcnow())))

    def get_weights(self, dim: str) -> dict[str, float]:
        rows = self.conn.execute("SELECT key,multiplier FROM weights WHERE dim=?", (dim,)).fetchall()
        return {r["key"]: float(r["multiplier"]) for r in rows}

    def all_weights(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM weights ORDER BY dim, multiplier DESC").fetchall()]

    # -------------------------------------------------------------------- kv
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, value))

    # ------------------------------------------------------------------- runs
    def start_run(self, stage: str) -> str:
        run_id = "r" + uuid.uuid4().hex[:11]
        with self.tx() as c:
            c.execute("INSERT INTO runs(id,stage,started_at,status) VALUES(?,?,?,?)",
                      (run_id, stage, iso(utcnow()), "running"))
        return run_id

    def finish_run(self, run_id: str, status: str, detail: str = "") -> None:
        with self.tx() as c:
            c.execute("UPDATE runs SET finished_at=?, status=?, detail=? WHERE id=?",
                      (iso(utcnow()), status, detail[:2000], run_id))

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()]
