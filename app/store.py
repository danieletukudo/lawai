"""SQLite notebook: chat lines, matter notes, tool actions."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from .settings import DATA_DIR, DB_PATH, FACT_LIMIT, HISTORY_LIMIT


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, role TEXT, content TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY, fact TEXT UNIQUE, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY, kind TEXT, payload TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY, value TEXT
        );
        """
    )
    # Per-message display data (activity card, source chips, draft id, case file).
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "meta" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
        conn.commit()
    return conn


def get_kv(key: str) -> str | None:
    conn = db()
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    conn.close()
    return None if row is None else row["value"]


def set_kv(key: str, value: str) -> None:
    conn = db()
    conn.execute(
        "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def add_message(role: str, content: str, meta: dict | None = None) -> str:
    message_id = uuid.uuid4().hex
    conn = db()
    conn.execute(
        "INSERT INTO messages (id, role, content, created_at, meta) VALUES (?, ?, ?, ?, ?)",
        (message_id, role, content.strip(), now(), json.dumps(meta) if meta else None),
    )
    conn.commit()
    conn.close()
    return message_id


def set_message_meta(message_id: str, meta: dict) -> None:
    conn = db()
    conn.execute("UPDATE messages SET meta=? WHERE id=?", (json.dumps(meta), message_id))
    conn.commit()
    conn.close()


def thread() -> list[dict]:
    """Every chat line with its display data, oldest first (for the UI)."""
    conn = db()
    rows = conn.execute("SELECT * FROM messages ORDER BY created_at ASC").fetchall()
    conn.close()
    out = []
    for row in rows:
        try:
            meta = json.loads(row["meta"]) if row["meta"] else {}
        except ValueError:
            meta = {}
        out.append(
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "meta": meta,
            }
        )
    return out


def clear_thread() -> None:
    """Start a new conversation. Matter notes are kept."""
    conn = db()
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()


def history():
    conn = db()
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT * FROM messages ORDER BY created_at DESC LIMIT ?
        ) ORDER BY created_at ASC
        """,
        (HISTORY_LIMIT,),
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def all_facts() -> list[str]:
    conn = db()
    rows = conn.execute("SELECT fact FROM facts ORDER BY created_at").fetchall()
    conn.close()
    return [r["fact"] for r in rows]


def add_fact(fact: str) -> str | None:
    text = " ".join(fact.strip().split())
    if not text:
        return None
    conn = db()
    exists = conn.execute("SELECT 1 FROM facts WHERE lower(fact)=lower(?)", (text,)).fetchone()
    if exists:
        conn.close()
        return None
    conn.execute("INSERT INTO facts VALUES (?, ?, ?)", (uuid.uuid4().hex, text, now()))
    conn.commit()
    conn.close()
    return text


def forget_fact(text: str) -> list[str]:
    needle = text.strip().lower()
    removed = []
    conn = db()
    for row in conn.execute("SELECT id, fact FROM facts").fetchall():
        if needle in row["fact"].lower() or row["fact"].lower() in needle:
            conn.execute("DELETE FROM facts WHERE id=?", (row["id"],))
            removed.append(row["fact"])
    conn.commit()
    conn.close()
    return removed


def add_action(kind: str, payload: dict):
    conn = db()
    conn.execute(
        "INSERT INTO actions VALUES (?, ?, ?, ?)",
        (uuid.uuid4().hex, kind, json.dumps(payload), now()),
    )
    conn.commit()
    conn.close()


def relevant_facts(question: str) -> list[str]:
    facts = all_facts()
    if len(facts) <= FACT_LIMIT:
        return facts
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
    ranked = sorted(facts, key=lambda f: sum(1 for w in words if w in f.lower()), reverse=True)
    return ranked[:FACT_LIMIT]
