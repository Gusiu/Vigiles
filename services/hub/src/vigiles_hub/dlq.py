"""Dead Letter Channel: invalid and undeliverable messages, kept for replay."""

import hashlib
from typing import Any

import psycopg


def dead_letter(conn: psycopg.Connection, *, source_topic: str, reason: str, raw: bytes | str,
                error: str, message_id: str | None = None) -> dict[str, Any] | None:
    raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    dedup_key = message_id or hashlib.sha256(raw_text.encode()).hexdigest()
    return conn.execute(
        """
        INSERT INTO dead_letters (dedup_key, message_id, source_topic, reason, raw_payload, error)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedup_key) DO UPDATE SET error = EXCLUDED.error, replayed_at = NULL
        RETURNING *
        """,
        (dedup_key, message_id, source_topic, reason, raw_text, error),
    ).fetchone()
