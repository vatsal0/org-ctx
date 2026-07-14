"""Canonical entity identifiers.

Every entity and every edge endpoint is named by a single stable, human-readable
string id. The whole graph is glued together by these ids, so getting the format
*canonical* (same logical thing -> byte-identical id, computed independently on
the producer and consumer sides) is load-bearing. If a consumer computes a
slightly different id for the route it calls than the producer computes for the
route it defines, the edge silently fails to resolve and impact analysis goes
quiet exactly when it should speak.

Format
------
    "{service}::{tag}:{local_name}"

- `service` is the owning repo/service ("payments-svc").
- `::` separates the service from the rest.
- `tag` is a short producer/consumer role tag ("http", "schema", "call", ...).
- the first ":" *after* the "::" separates the tag from the local name.
- `local_name` may itself contain ":" (rare), "/" and spaces (routes), so parsing
  splits on the FIRST such delimiters only, never with a naive str.split(":").

Producer tags vs consumer tags
------------------------------
Producers and consumers of the same contract get *different* tags (see
`models.ConsumerTag`) so their ids never collide in the entities table:

    consumer "call:METHOD /path"   pairs with producer "http:METHOD /path"
    consumer "import:Symbol"       pairs with producer "export:Symbol"/"schema:Symbol"
    consumer "subscribe:topic"     pairs with producer "topic:topic"

The matcher in extract.py resolves a consumer to its producer by *name* via
`ProducerIndex` (normalized route / symbol / topic), not by tag-swapping. Because
both sides run `normalize_route`/plain-name rules here, a call to `/v1/charge/`
resolves to a route declared as `/v1/charge`.
"""

from __future__ import annotations

import re

from .models import ConsumerTag, Kind


# The producer tag used in the id for each entity kind. Consumer-side pseudo
# entities use their own tags (see models.ConsumerTag).
KIND_TO_TAG: dict[Kind, str] = {
    Kind.HTTP_ROUTE: "http",
    Kind.EXPORT: "export",
    Kind.SCHEMA_MODEL: "schema",
    Kind.SCHEMA_FIELD: "schema",   # fields share the schema namespace: "Model.field"
    Kind.CONFIG_KEY: "env",
    Kind.QUEUE_TOPIC: "topic",
    Kind.PKG_VERSION: "pkg",
}


def make_id(service: str, kind: Kind, local_name: str) -> str:
    """Build a producer entity id from its parts.

    We intentionally do NOT normalize `local_name` here for non-route kinds — the
    caller passes an already-canonical symbol/field/topic name. Routes are the one
    case with real ambiguity (method casing, trailing slash, param naming), so
    routes get normalized at their construction sites via `normalize_route` and
    then handed to us as a finished "METHOD /path" string.
    """
    tag = KIND_TO_TAG[kind]
    return f"{service}::{tag}:{local_name}"


def make_consumer_id(service: str, consumer_tag: ConsumerTag, local_name: str) -> str:
    """Build a consumer-side pseudo entity id (the `from_entity` of an edge)."""
    return f"{service}::{consumer_tag.value}:{local_name}"


def parse_id(entity_id: str) -> tuple[str, str, str]:
    """Split an id back into (service, tag, local_name).

    Splits on the FIRST "::" and then the FIRST ":" after it, so local names
    containing ":" / "/" / spaces survive intact.
    """
    service, _, rest = entity_id.partition("::")
    if not _:
        raise ValueError(f"malformed entity id (no '::'): {entity_id!r}")
    tag, _, local_name = rest.partition(":")
    if not _:
        raise ValueError(f"malformed entity id (no tag ':'): {entity_id!r}")
    return service, tag, local_name


def normalize_route(method: str, path: str) -> str:
    """Canonicalize an HTTP method+path into the "METHOD /path" local name.

    Normalizations (applied identically on producer and consumer sides so the two
    always agree):
      - method upper-cased ("post" -> "POST").
      - exactly one leading slash on the path.
      - any trailing slash stripped (but never the root "/").
      - collapse runs of whitespace.
      - path parameters are erased to a placeholder "{}" so that a route declared
        as "/v1/charge/{id}" matches a client that hits "/v1/charge/{order_id}"
        or a concrete "/v1/charge/123". We recognize both "{name}" style
        parameters and ":name" style, plus purely-numeric path segments.
    """
    method = method.strip().upper()

    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path
    # Strip a trailing slash except when the whole path is just "/".
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    segments = path.split("/")
    normalized_segments = []
    for seg in segments:
        if not seg:
            normalized_segments.append(seg)  # preserves the leading empty segment
            continue
        # "{id}" or ":id" style path params, or a bare numeric id, collapse to "{}".
        if (seg.startswith("{") and seg.endswith("}")) or seg.startswith(":") or seg.isdigit():
            normalized_segments.append("{}")
        else:
            normalized_segments.append(seg)
    path = "/".join(normalized_segments)

    return f"{method} {path}"


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def slugify(entity_id: str) -> str:
    """Turn an entity id into a filesystem-safe slug for central/entities/<slug>.md.

    We replace every run of non-alphanumeric characters (which includes "::", ":",
    "/", spaces) with a single hyphen and trim. This is lossy (not reversible),
    which is fine: the file's frontmatter/body carries the real id; the slug is
    only a stable, readable filename.
    """
    return _SLUG_RE.sub("-", entity_id).strip("-").lower()
