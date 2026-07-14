"""Core domain vocabulary: enums and row dataclasses.

Everything else in the package speaks in terms of these types. We follow the
CLAUDE.md rule that *any argument or field which is one of a fixed set of string
choices must be an enum*, not a bare string — this makes illegal states
unrepresentable and gives us one authoritative place to see the whole vocabulary.

The dataclasses mirror the SQLite rows one-to-one (see db.py). We keep them as
plain frozen dataclasses rather than an ORM: the demo's data model is small and
stable, and a hand-written mapping keeps the storage layer transparent and
debuggable (you can read exactly what hits the database).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# --------------------------------------------------------------------------- #
# Enums — the closed vocabularies of the data model.
# --------------------------------------------------------------------------- #
#
# We store the *value* (the lowercase string) in SQLite, and reconstruct the enum
# on read. Using `str` mix-in enums means `Kind.HTTP_ROUTE == "http_route"` never
# accidentally holds while still letting us write `k.value` explicitly for
# storage. We choose explicit `.value` everywhere for clarity, but the str
# mix-in makes debugging (and YAML round-tripping) friendlier.


class Kind(str, Enum):
    """The categories of *entity* — a thing with a contract others can depend on.

    Each kind has a distinct extraction strategy (see extract.py) and a distinct
    tag in the canonical entity id (see ids.py). SCHEMA_MODEL and SCHEMA_FIELD are
    split because a downstream repo usually depends on a *model* by import, while
    a breaking change often happens at the *field* level (a renamed field). We
    want both granularities on the timeline.
    """

    HTTP_ROUTE = "http_route"      # e.g. POST /v1/charge
    EXPORT = "export"              # an exported package symbol (verifyToken)
    SCHEMA_MODEL = "schema_model"  # a whole schema class (Charge)
    SCHEMA_FIELD = "schema_field"  # a single field on a schema (Charge.currency)
    CONFIG_KEY = "config_key"      # an env var / config key read by a service
    QUEUE_TOPIC = "queue_topic"    # a pub/sub topic string (order.created)
    PKG_VERSION = "pkg_version"    # a package version pin (declared, not yet extracted)


class ChangeKind(str, Enum):
    """How a commit touched an entity.

    The ordering here doubles as a *severity ladder* used by ranking logic in
    sync/impact: SIGNATURE_CHANGE and REMOVED are the contract-breaking end;
    INTERNAL is pure churn. `breaking` (a separate bool) is computed from this
    plus whether anyone actually depends on the entity — a signature change to an
    entity nobody consumes is not "breaking" in the actionable sense.
    """

    ADDED = "added"                        # entity introduced this commit
    MODIFIED = "modified"                  # contract touched but shape preserved
    REMOVED = "removed"                    # entity deleted this commit
    SIGNATURE_CHANGE = "signature_change"  # shape changed (param/field/type)
    BEHAVIOR_NOTE = "behavior_note"        # same shape, changed semantics (LLM-flagged)
    INTERNAL = "internal"                  # implementation churn, no contract impact


class EdgeKind(str, Enum):
    """The relationship an interface edge encodes (consumer -> producer).

    We deliberately restrict edges to *interfaces* (routes, schemas, exports,
    config, topics) rather than a full syntactic call graph. That narrower graph
    has far higher signal for the question we actually care about: "will my change
    break something downstream?"
    """

    CONSUMES = "consumes"                    # HTTP client call -> route
    IMPORTS = "imports"                      # import of an exported symbol
    DEPENDS_ON_SCHEMA = "depends_on_schema"  # import/use of a shared schema
    DEPENDS_ON_CONFIG = "depends_on_config"  # read of a shared config/env key
    SUBSCRIBES = "subscribes"                # subscription to a queue topic


class Confidence(str, Enum):
    """How sure we are an edge is real.

    Explicit, heuristically-verified edges are HIGH; manifest-declared-only edges
    are MEDIUM (we know the dependency exists but not exactly where in code);
    fuzzy/inferred edges are LOW. Impact ranking can weight by this later.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EdgeSource(str, Enum):
    """How an edge was detected — provenance for debugging and for the honest
    "heuristic-only" edge-recall metric (which strips MANIFEST-sourced edges)."""

    ROUTE_MATCH = "route_match"          # matched an HTTP client call to a known route
    IMPORT_PARSE = "import_parse"        # parsed an import of a known symbol/schema
    SUBSCRIBE_MATCH = "subscribe_match"  # matched a subscription to a known topic
    MANIFEST = "manifest"                # declared in a repo's manifest.yaml (recall floor)
    LLM = "llm"                          # inferred by an LLM pass (stretch goal)


class ConsumerTag(str, Enum):
    """The tag used in a consumer-side pseudo-entity id (the `from_entity` of an
    edge). Distinct from producer tags so consumer and producer ids never collide;
    each maps to the producer tag it resolves against (call->http, import->export/
    schema, subscribe->topic). Made an enum per the "string choice -> enum" rule so
    the closed set of consumer roles lives in one place."""

    CALL = "call"            # an HTTP client call site -> resolves to an http route
    IMPORT = "import"        # an import site -> resolves to an export/schema
    SUBSCRIBE = "subscribe"  # a subscription site -> resolves to a queue topic


# --------------------------------------------------------------------------- #
# Row dataclasses — one per SQLite table.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Entity:
    """A thing with a contract. Mirrors the `entities` table.

    `signature` is the current shape (route+params, field type, fn signature) and
    is what `ingest` diffs across revisions to detect a SIGNATURE_CHANGE.
    `state_summary` is the *compressed current truth*, written by `compact` — it
    starts empty at extract time. `def_file`/`def_line` point at where the
    producer is defined, so downstream digests can cite it. `origin_commit` and
    `latest_commit` bookend the timeline and power origin-vs-latest attribution.
    """

    entity_id: str
    kind: Kind
    service: str
    signature: str | None = None
    state_summary: str | None = None
    def_file: str | None = None
    def_line: int | None = None
    origin_commit: str | None = None
    latest_commit: str | None = None


@dataclass(frozen=True)
class ChangeEvent:
    """One entry on an entity's timeline. Mirrors the `change_events` table.

    `diff_ref` is a *pointer* ("<sha>:<path>#Lx-Ly"), never the diff body — the
    "pointers not payloads" rule keeps the hot store small. `event_id` is None
    until the row is inserted (SQLite assigns it).
    """

    entity_id: str
    commit_sha: str
    change_kind: ChangeKind
    breaking: bool
    summary: str | None = None
    author: str | None = None
    timestamp: str | None = None  # ISO-8601 git author date
    diff_ref: str | None = None
    event_id: int | None = None


@dataclass(frozen=True)
class Edge:
    """A consumer->producer interface edge. Mirrors the `interface_edges` table.

    `from_entity` is the consumer-side pseudo-entity (e.g. an HTTP call site),
    `to_entity` the producer that owns the contract. `from_file`/`from_line` cite
    exactly where the consumer touches the contract — the "you consume this in
    src/pay.py:42" line in the agent payload.
    """

    from_entity: str
    to_entity: str
    edge_kind: EdgeKind
    confidence: Confidence
    source: EdgeSource
    from_file: str | None = None
    from_line: int | None = None
