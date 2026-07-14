"""SQLite storage for the entity graph.

This is the "small SQLite index" from plan.md §4.4. It holds four tables:
entities, change_events, interface_edges, and sync_state. We keep the storage
layer deliberately thin and explicit — no ORM — so that exactly what hits the
database is readable in one file. Every write path is *idempotent*: `extract` and
`ingest` can be re-run over the same repo/commits without creating duplicate rows,
because the natural keys carry UNIQUE / PRIMARY KEY constraints and we UPSERT.

Why idempotency matters here: the demo pipeline (and the tests) re-run extract and
ingest freely. If re-running doubled every edge, the edge-recall metric and the
compaction token counts would drift run-to-run. Constraints make re-runs safe.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    ChangeEvent,
    ChangeKind,
    Confidence,
    Edge,
    EdgeKind,
    EdgeSource,
    Entity,
    Kind,
)


# The full schema, applied once on connect(). `IF NOT EXISTS` makes connect()
# idempotent so we never need a separate "init" step. Notes on key choices live
# inline; the design rationale is in plan.md §4.
SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_id     TEXT PRIMARY KEY,      -- canonical id, e.g. "payments-svc::http:POST /v1/charge"
    kind          TEXT NOT NULL,         -- Kind enum value
    service       TEXT NOT NULL,         -- owning repo/service
    signature     TEXT,                  -- current shape; diffed across revisions by ingest
    state_summary TEXT,                  -- compressed current truth; written by compact
    def_file      TEXT,                  -- file where the producer is defined (for citations)
    def_line      INTEGER,               -- 1-based line of the definition
    origin_commit TEXT,                  -- first commit that introduced it
    latest_commit TEXT                   -- most recent commit that mutated it
);
CREATE INDEX IF NOT EXISTS idx_entities_service ON entities(service);
CREATE INDEX IF NOT EXISTS idx_entities_kind    ON entities(kind);

CREATE TABLE IF NOT EXISTS change_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   TEXT NOT NULL REFERENCES entities(entity_id),
    commit_sha  TEXT NOT NULL,
    author      TEXT,
    timestamp   TEXT,                    -- ISO-8601 git author date
    change_kind TEXT NOT NULL,           -- ChangeKind enum value
    breaking    INTEGER NOT NULL DEFAULT 0,
    summary     TEXT,                    -- one-line, LLM-generated
    diff_ref    TEXT,                    -- "<sha>:<path>#Lx-Ly" pointer, never the diff body
    UNIQUE(entity_id, commit_sha, change_kind)   -- one event per (entity, commit, kind): idempotent re-ingest
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON change_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_events_commit ON change_events(commit_sha);

CREATE TABLE IF NOT EXISTS interface_edges (
    from_entity TEXT NOT NULL,           -- consumer side, e.g. "orders-svc::call:POST /v1/charge"
    to_entity   TEXT NOT NULL,           -- producer side, e.g. "payments-svc::http:POST /v1/charge"
    edge_kind   TEXT NOT NULL,           -- EdgeKind enum value
    confidence  TEXT NOT NULL,           -- Confidence enum value
    source      TEXT NOT NULL,           -- EdgeSource enum value
    from_file   TEXT,                    -- consuming file (for "you consume this in X:line")
    from_line   INTEGER,
    PRIMARY KEY (from_entity, to_entity, edge_kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_to   ON interface_edges(to_entity);
CREATE INDEX IF NOT EXISTS idx_edges_from ON interface_edges(from_entity);

CREATE TABLE IF NOT EXISTS sync_state (
    service            TEXT PRIMARY KEY,
    last_synced_commit TEXT
);
"""


class Graph:
    """A thin handle over the SQLite graph store.

    Construct with a path to the .db file (parent dirs are created). The schema is
    applied on connect so a fresh path yields a ready, empty graph. All methods
    that mutate commit immediately — the demo is single-writer, so we favor
    simplicity over batching.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # `check_same_thread=False` is harmless for our single-process use and
        # avoids friction if a test fixture touches the connection across helpers.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Enforce the change_events -> entities foreign key.
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- entities
    def upsert_entity(self, e: Entity) -> None:
        """Insert or update an entity by id.

        On conflict we update the *mutable* descriptive columns (signature,
        state_summary, def location, latest_commit) but preserve origin_commit if
        it was already set — the origin is written once at first sighting and must
        never be overwritten by a later re-extract. We use COALESCE so a later call
        that passes origin_commit=None does not clobber an existing origin.
        """
        self.conn.execute(
            """
            INSERT INTO entities
                (entity_id, kind, service, signature, state_summary,
                 def_file, def_line, origin_commit, latest_commit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                kind          = excluded.kind,
                service       = excluded.service,
                signature     = excluded.signature,
                state_summary = COALESCE(excluded.state_summary, entities.state_summary),
                def_file      = excluded.def_file,
                def_line      = excluded.def_line,
                origin_commit = COALESCE(entities.origin_commit, excluded.origin_commit),
                latest_commit = COALESCE(excluded.latest_commit, entities.latest_commit)
            """,
            (
                e.entity_id, e.kind.value, e.service, e.signature, e.state_summary,
                e.def_file, e.def_line, e.origin_commit, e.latest_commit,
            ),
        )
        self.conn.commit()

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return _row_to_entity(row) if row else None

    def all_entities(self) -> list[Entity]:
        rows = self.conn.execute("SELECT * FROM entities ORDER BY entity_id").fetchall()
        return [_row_to_entity(r) for r in rows]

    def set_state_summary(self, entity_id: str, summary: str) -> None:
        """Write the compacted state_summary for one entity (called by compact)."""
        self.conn.execute(
            "UPDATE entities SET state_summary = ? WHERE entity_id = ?",
            (summary, entity_id),
        )
        self.conn.commit()

    def set_commit_bounds(
        self, entity_id: str, *, origin: str | None, latest: str | None
    ) -> None:
        """Update origin (only if unset) and latest commit pointers for an entity."""
        self.conn.execute(
            """
            UPDATE entities SET
                origin_commit = COALESCE(origin_commit, ?),
                latest_commit = ?
            WHERE entity_id = ?
            """,
            (origin, latest, entity_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------ change events
    def add_event(self, ev: ChangeEvent) -> None:
        """Append a change event. Idempotent: a repeat (entity, commit, kind) is
        ignored via `ON CONFLICT DO NOTHING`, so re-ingesting a commit range is
        safe."""
        self.conn.execute(
            """
            INSERT INTO change_events
                (entity_id, commit_sha, author, timestamp, change_kind,
                 breaking, summary, diff_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id, commit_sha, change_kind) DO NOTHING
            """,
            (
                ev.entity_id, ev.commit_sha, ev.author, ev.timestamp,
                ev.change_kind.value, int(ev.breaking), ev.summary, ev.diff_ref,
            ),
        )
        self.conn.commit()

    def events_for(self, entity_id: str) -> list[ChangeEvent]:
        """All events on an entity's timeline, oldest first (rowid order tracks
        insertion, which for our per-commit ingest tracks commit order)."""
        rows = self.conn.execute(
            "SELECT * FROM change_events WHERE entity_id = ? ORDER BY event_id",
            (entity_id,),
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def events_since(self, entity_id: str, commit_shas_after: set[str]) -> list[ChangeEvent]:
        """Events on an entity whose commit is in `commit_shas_after`.

        `sync` uses this to gather "what changed since the consumer last synced" —
        the caller resolves the set of commit shas newer than the last sync and
        passes them in, keeping git-walking concerns out of the storage layer.
        """
        return [ev for ev in self.events_for(entity_id) if ev.commit_sha in commit_shas_after]

    # -------------------------------------------------------------------- edges
    def upsert_edge(self, edge: Edge) -> None:
        """Insert or upgrade an interface edge.

        The recall story (plan.md §5.1): a manifest declaration seeds the edge's
        *existence* (source=MANIFEST, confidence=MEDIUM, no code location). When a
        heuristic later finds the same (from, to, kind), we UPGRADE the row —
        raising confidence and attaching the code location — rather than inserting
        a duplicate. We only ever move confidence *up* and only overwrite
        source/file/line when the new row actually carries a code location, so a
        later manifest re-read cannot downgrade a heuristic-found edge.
        """
        has_location = edge.from_file is not None
        self.conn.execute(
            """
            INSERT INTO interface_edges
                (from_entity, to_entity, edge_kind, confidence, source, from_file, from_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(from_entity, to_entity, edge_kind) DO UPDATE SET
                confidence = CASE WHEN ? THEN excluded.confidence ELSE interface_edges.confidence END,
                source     = CASE WHEN ? THEN excluded.source     ELSE interface_edges.source END,
                from_file  = COALESCE(excluded.from_file, interface_edges.from_file),
                from_line  = COALESCE(excluded.from_line, interface_edges.from_line)
            """,
            (
                edge.from_entity, edge.to_entity, edge.edge_kind.value,
                edge.confidence.value, edge.source.value, edge.from_file, edge.from_line,
                # The two CASE guards: only adopt the new confidence/source when the
                # incoming edge carries a concrete code location (i.e. it is a real
                # heuristic hit, not a bare manifest re-seed).
                has_location, has_location,
            ),
        )
        self.conn.commit()

    def all_edges(self) -> list[Edge]:
        rows = self.conn.execute("SELECT * FROM interface_edges").fetchall()
        return [_row_to_edge(r) for r in rows]

    def inbound_edges(self, to_entity: str) -> list[Edge]:
        """Edges pointing AT an entity — i.e. its downstream consumers. This is the
        core of impact analysis: "who depends on the thing I just changed?"."""
        rows = self.conn.execute(
            "SELECT * FROM interface_edges WHERE to_entity = ?", (to_entity,)
        ).fetchall()
        return [_row_to_edge(r) for r in rows]

    def outbound_edges_for_service(self, service: str) -> list[Edge]:
        """Edges whose consumer side belongs to `service` — i.e. everything this
        service depends on. `sync` uses this to find "what I consume"."""
        rows = self.conn.execute(
            "SELECT * FROM interface_edges WHERE from_entity LIKE ?",
            (f"{service}::%",),
        ).fetchall()
        return [_row_to_edge(r) for r in rows]

    def has_inbound_edge(self, entity_id: str) -> bool:
        """Does anyone depend on this entity? Drives the compaction promotion rule
        (an `added`/`internal` change only earns/loses a permanent line based on
        whether the entity is actually consumed)."""
        row = self.conn.execute(
            "SELECT 1 FROM interface_edges WHERE to_entity = ? LIMIT 1", (entity_id,)
        ).fetchone()
        return row is not None

    # --------------------------------------------------------------- sync state
    def get_last_synced(self, service: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_synced_commit FROM sync_state WHERE service = ?", (service,)
        ).fetchone()
        return row["last_synced_commit"] if row else None

    def set_last_synced(self, service: str, commit_sha: str) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_state (service, last_synced_commit) VALUES (?, ?)
            ON CONFLICT(service) DO UPDATE SET last_synced_commit = excluded.last_synced_commit
            """,
            (service, commit_sha),
        )
        self.conn.commit()


# --------------------------------------------------------------------------- #
# Row -> dataclass adapters. Kept as module functions so both the class methods
# and any ad-hoc query in the eval harness can reuse them.
# --------------------------------------------------------------------------- #
def _row_to_entity(r: sqlite3.Row) -> Entity:
    return Entity(
        entity_id=r["entity_id"],
        kind=Kind(r["kind"]),
        service=r["service"],
        signature=r["signature"],
        state_summary=r["state_summary"],
        def_file=r["def_file"],
        def_line=r["def_line"],
        origin_commit=r["origin_commit"],
        latest_commit=r["latest_commit"],
    )


def _row_to_event(r: sqlite3.Row) -> ChangeEvent:
    return ChangeEvent(
        event_id=r["event_id"],
        entity_id=r["entity_id"],
        commit_sha=r["commit_sha"],
        author=r["author"],
        timestamp=r["timestamp"],
        change_kind=ChangeKind(r["change_kind"]),
        breaking=bool(r["breaking"]),
        summary=r["summary"],
        diff_ref=r["diff_ref"],
    )


def _row_to_edge(r: sqlite3.Row) -> Edge:
    return Edge(
        from_entity=r["from_entity"],
        to_entity=r["to_entity"],
        edge_kind=EdgeKind(r["edge_kind"]),
        confidence=Confidence(r["confidence"]),
        source=EdgeSource(r["source"]),
        from_file=r["from_file"],
        from_line=r["from_line"],
    )
