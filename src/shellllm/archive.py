"""Sqlite-backed archive of expired shellllm sessions for ``--recall``.

When a sticky session crosses its idle TTL (see :mod:`shellllm.session`)
the JSONL file gets rotated aside; the same hook also runs the
transcript through this archive so the conversation stays searchable
across days/panes/restarts.

Schema
------
A single ``archives`` table holds one row per archived session,
mirrored into an FTS5 virtual table so we can run BM25 over the
content cheaply. An optional ``embedding`` BLOB column stores a
normalized fp32 vector when an embedding server is available; recall
falls back to FTS5-only when no embeddings are stored.

Hybrid search
-------------
``Archive.search`` runs BM25 always and (when an embedder is provided)
also does a cosine sweep over stored vectors, then fuses the rankings
with Reciprocal Rank Fusion (RRF). RRF is a known-good default that
needs no relevance threshold tuning — it just rewards items that show
up high in either list.

No deps
-------
sqlite3 is in the stdlib. Embeddings are packed via
:mod:`shellllm.embed` helpers; we don't import numpy.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .embed import cosine, pack_embedding, unpack_embedding

Embedder = Callable[[str], "list[float] | None"]

ARCHIVE_DB_ENV = "SHELLLM_ARCHIVE_DB"
DEFAULT_ARCHIVE_DB = Path.home() / ".cache" / "shellllm" / "archive.db"

# A safe-ish RRF default; tweak only with measurements.
RRF_K = 60

# How many candidates to consider from each ranking before fusion.
DEFAULT_CANDIDATE_POOL = 50


@dataclass
class ArchiveHit:
    """Single recall result. Public surface for callers and tests."""

    id: int
    archived_at: float
    cmd: str
    last_pwd: str
    last_date: str
    turn_count: int
    snippet: str
    content: str
    fts_rank: float | None = None
    cosine_score: float | None = None
    fused_score: float = 0.0


def _resolve_db_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get(ARCHIVE_DB_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_ARCHIVE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS archives (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    archived_at     REAL NOT NULL,
    cmd             TEXT NOT NULL,
    terminal_id     TEXT NOT NULL,
    created_at      REAL NOT NULL,
    last_used       REAL NOT NULL,
    last_pwd        TEXT NOT NULL DEFAULT '',
    last_date       TEXT NOT NULL DEFAULT '',
    turn_count      INTEGER NOT NULL DEFAULT 0,
    content         TEXT NOT NULL,
    embedding       BLOB
);

CREATE VIRTUAL TABLE IF NOT EXISTS archives_fts USING fts5(
    content,
    content='archives',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS archives_ai AFTER INSERT ON archives BEGIN
    INSERT INTO archives_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS archives_ad AFTER DELETE ON archives BEGIN
    INSERT INTO archives_fts(archives_fts, rowid, content)
        VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS archives_au AFTER UPDATE ON archives BEGIN
    INSERT INTO archives_fts(archives_fts, rowid, content)
        VALUES('delete', old.id, old.content);
    INSERT INTO archives_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


@dataclass
class Archive:
    """Open handle to the archive database. Cheap to construct."""

    path: Path = field(default_factory=_resolve_db_path)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── Writes ----------------------------------------------------------

    def ingest_session(
        self,
        *,
        cmd: str,
        terminal_id: str,
        created_at: float,
        last_used: float,
        last_pwd: str,
        last_date: str,
        turn_count: int,
        messages: list[dict[str, Any]],
        embedding: list[float] | None = None,
        embed_fn: Embedder | None = None,
        archived_at: float | None = None,
    ) -> int | None:
        """Flatten ``messages`` and store one row. Returns the new id, or None.

        Returns None when the transcript is empty (nothing semantically
        useful to recall later — we'd rather not pollute the index).

        If ``embedding`` is provided directly, it's used as-is. Otherwise,
        if ``embed_fn`` is provided, we call it on the flattened content
        and store whatever it returns (None is fine — FTS5 alone will
        still serve recall).
        """
        content = flatten_transcript(messages)
        if not content.strip():
            return None

        ts = archived_at if archived_at is not None else time.time()
        vec = embedding
        if vec is None and embed_fn is not None:
            try:
                vec = embed_fn(content)
            except Exception:  # noqa: BLE001 — embedding is best-effort
                vec = None
        blob = pack_embedding(vec) if vec else None

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO archives (
                    archived_at, cmd, terminal_id, created_at, last_used,
                    last_pwd, last_date, turn_count, content, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    cmd,
                    terminal_id,
                    created_at,
                    last_used,
                    last_pwd,
                    last_date,
                    turn_count,
                    content,
                    blob,
                ),
            )
            return int(cur.lastrowid) if cur.lastrowid is not None else None

    def count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM archives").fetchone()
            return int(row[0]) if row else 0

    # ── Browse ---------------------------------------------------------

    def recent(
        self,
        *,
        limit: int = 20,
        cmd_filter: str | None = None,
    ) -> list[ArchiveHit]:
        """Most-recent archives, no FTS query — for `??? --archives`.

        Use this to browse what's been archived without a specific
        recall query in mind. The snippet field is synthesized from
        the head of the content (FTS5 ``snippet()`` is only available
        on a MATCH expression).
        """
        sql = "SELECT id, archived_at, cmd, last_pwd, last_date, turn_count, content FROM archives"
        params: list[Any] = []
        if cmd_filter:
            sql += " WHERE cmd = ?"
            params.append(cmd_filter)
        sql += " ORDER BY archived_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            ArchiveHit(
                id=int(r[0]),
                archived_at=float(r[1]),
                cmd=str(r[2]),
                last_pwd=str(r[3] or ""),
                last_date=str(r[4] or ""),
                turn_count=int(r[5]),
                content=str(r[6]),
                snippet=_truncate_snippet(str(r[6])),
            )
            for r in rows
        ]

    def get(self, archive_id: int) -> ArchiveHit | None:
        """Fetch one archive by id, or None — for `??? --show <id>`."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, archived_at, cmd, last_pwd, last_date, "
                "turn_count, content FROM archives WHERE id = ?",
                (archive_id,),
            ).fetchone()
        if row is None:
            return None
        return ArchiveHit(
            id=int(row[0]),
            archived_at=float(row[1]),
            cmd=str(row[2]),
            last_pwd=str(row[3] or ""),
            last_date=str(row[4] or ""),
            turn_count=int(row[5]),
            content=str(row[6]),
            snippet=_truncate_snippet(str(row[6])),
        )

    # ── Searches -------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        query_embedding: list[float] | None = None,
        cmd_filter: str | None = None,
        candidate_pool: int = DEFAULT_CANDIDATE_POOL,
    ) -> list[ArchiveHit]:
        """Hybrid recall: BM25 always, cosine if an embedding is provided.

        Results are fused with RRF and trimmed to ``limit``. An empty
        return is normal — recall fires speculatively on plain
        questions, and not finding anything is the common case.
        """
        if not query.strip():
            return []

        bm25_rows = self._search_fts(query, limit=candidate_pool, cmd_filter=cmd_filter)
        cosine_rows = (
            self._search_cosine(query_embedding, limit=candidate_pool, cmd_filter=cmd_filter)
            if query_embedding
            else []
        )

        fused = _rrf_fuse(
            [(row.id, row.fts_rank or 0.0) for row in bm25_rows],
            [(row.id, row.cosine_score or 0.0) for row in cosine_rows],
            k=RRF_K,
        )

        by_id: dict[int, ArchiveHit] = {row.id: row for row in bm25_rows}
        for row in cosine_rows:
            if row.id in by_id:
                by_id[row.id].cosine_score = row.cosine_score
            else:
                by_id[row.id] = row

        ordered: list[ArchiveHit] = []
        for hit_id, score in fused[:limit]:
            hit = by_id.get(hit_id)
            if hit is None:
                continue
            hit.fused_score = score
            ordered.append(hit)
        return ordered

    def _search_fts(self, query: str, *, limit: int, cmd_filter: str | None) -> list[ArchiveHit]:
        fts_query = _build_fts_query(query)
        if not fts_query:
            return []

        sql = """
            SELECT a.id, a.archived_at, a.cmd, a.last_pwd, a.last_date,
                   a.turn_count, a.content,
                   snippet(archives_fts, 0, '«', '»', '…', 12) AS snip,
                   bm25(archives_fts) AS rank
            FROM archives_fts
            JOIN archives a ON a.id = archives_fts.rowid
            WHERE archives_fts MATCH ?
        """
        params: list[Any] = [fts_query]
        if cmd_filter:
            sql += " AND a.cmd = ?"
            params.append(cmd_filter)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # FTS5 parser rejected the query (e.g. only stopwords).
                return []

        return [
            ArchiveHit(
                id=int(r[0]),
                archived_at=float(r[1]),
                cmd=str(r[2]),
                last_pwd=str(r[3] or ""),
                last_date=str(r[4] or ""),
                turn_count=int(r[5]),
                content=str(r[6]),
                snippet=str(r[7]),
                fts_rank=float(r[8]),
            )
            for r in rows
        ]

    def _search_cosine(
        self,
        query_embedding: list[float],
        *,
        limit: int,
        cmd_filter: str | None,
    ) -> list[ArchiveHit]:
        sql = (
            "SELECT id, archived_at, cmd, last_pwd, last_date, "
            "turn_count, content, embedding FROM archives "
            "WHERE embedding IS NOT NULL"
        )
        params: list[Any] = []
        if cmd_filter:
            sql += " AND cmd = ?"
            params.append(cmd_filter)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        scored: list[tuple[float, ArchiveHit]] = []
        for r in rows:
            blob = r[7]
            if not blob:
                continue
            vec = unpack_embedding(blob)
            score = cosine(query_embedding, vec)
            if score <= 0.0:
                continue
            hit = ArchiveHit(
                id=int(r[0]),
                archived_at=float(r[1]),
                cmd=str(r[2]),
                last_pwd=str(r[3] or ""),
                last_date=str(r[4] or ""),
                turn_count=int(r[5]),
                content=str(r[6]),
                snippet=_truncate_snippet(str(r[6])),
                cosine_score=score,
            )
            scored.append((score, hit))

        scored.sort(key=lambda t: -t[0])
        return [hit for _, hit in scored[:limit]]

    # ── Internals ------------------------------------------------------

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a transaction, then *close* it.

        ``with sqlite3.connect(...)`` alone only scopes the transaction —
        the underlying connection (3 fds in WAL mode: db, -wal, -shm)
        stays open until GC. Under the test suite that exhausted the
        default macOS fd limit, so we close deterministically.
        """
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            with conn:
                yield conn
        finally:
            conn.close()


# ── Helpers --------------------------------------------------------------


def flatten_transcript(messages: Iterable[dict[str, Any]]) -> str:
    """Reduce a session to Q/A pairs for indexing.

    Tool messages are skipped — file reads / search results are big and
    noisy, and what we want recall on is the human conversation."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        if role == "user":
            lines.append(f"Q: {text}")
        elif role == "assistant":
            lines.append(f"A: {text}")
        elif role == "system" and text.startswith("<summary-so-far>"):
            # Carry the compaction summary forward — it represents the
            # older turns that already got distilled away.
            lines.append(text)
    return "\n\n".join(lines)


_FTS_TOKEN_RE = re.compile(r"[^\w -￿]+", flags=re.UNICODE)


def _build_fts_query(raw: str) -> str:
    """Sanitize user input into a safe FTS5 MATCH expression.

    We split on non-word, drop empties, and OR the terms — that gives
    BM25 the widest set to score without exposing FTS5 operator syntax
    to accidental injection.
    """
    parts = [p for p in _FTS_TOKEN_RE.split(raw) if p]
    cleaned = [p for p in parts if not p.isdigit() or len(p) > 1]
    return " OR ".join(cleaned)


def _truncate_snippet(content: str, *, limit: int = 240) -> str:
    snippet = content.strip().replace("\n", " ")
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 1].rstrip() + "…"


def _rrf_fuse(
    *rankings: list[tuple[int, float]],
    k: int = RRF_K,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: pos-only, no thresholding needed."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, (item_id, _score) in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def render_hits_block(hits: list[ArchiveHit]) -> str:
    """Wrap hits in a system-prompt block for context injection."""
    if not hits:
        return ""
    lines = [
        "<shellllm-recall>",
        "Possibly-relevant snippets from your earlier sessions:",
    ]
    for hit in hits:
        lines.append(f"- ({hit.cmd}, {hit.last_date}) {hit.snippet}")
    lines.append("</shellllm-recall>")
    return "\n".join(lines)
