"""Heuristic, static extraction of entities and interface edges from Python source.

This module answers two questions about a repo, using only the stdlib `ast`
module (we parse source; we never import or run the target services):

  1. **What contracts does this service PRODUCE?** — HTTP routes, exported symbols,
     schema models/fields, config keys, queue topics. These become *entities*.
  2. **What contracts does this service CONSUME?** — HTTP client calls to known
     routes, imports of known schemas/exports, subscriptions to known topics.
     These become the consumer side of *interface edges*.

Two design points worth internalizing:

- **A route's signature embeds its response model's fields.** A route declared
  with `response_model=Charge` (or `-> Charge`) has its signature expanded to
  include Charge's current field list. This is the mechanism (plan.md §5.1) by
  which renaming `Charge.amount` becomes a *signature change on the route*, which
  the route's downstream consumer (orders-svc) is then warned about — even though
  orders never imports Charge directly. Resolution reads the model's source at the
  same revision, so it stays consistent commit-to-commit.

- **manifest.yaml is the recall floor; heuristics add confidence + citation.**
  Each repo declares its cross-service deps in a manifest with full producer ids.
  We seed those edges first (source=MANIFEST). When a heuristic independently finds
  the same edge, `Graph.upsert_edge` upgrades it to HIGH confidence and attaches
  the exact consuming file/line. So the graph is never empty and recall never
  depends on a heuristic firing, while the honest heuristic-only recall is still
  measurable (metrics.py strips manifest-only edges).
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from .db import Graph
from .ids import make_consumer_id, make_id, normalize_route, parse_id
from .models import (
    Confidence,
    ConsumerTag,
    Edge,
    EdgeKind,
    EdgeSource,
    Entity,
    Kind,
)


# HTTP verbs we recognize on both the producer (route decorator) and consumer
# (client call) sides. Kept as one set so the two sides never drift.
HTTP_METHODS = {"get", "post", "put", "delete", "patch"}

# Names we treat as HTTP client objects when we see `<name>.post(...)`. Heuristic:
# these are the common ways a Python service issues an outbound request.
HTTP_CLIENT_NAMES = {"httpx", "requests", "client", "session", "http"}

# Attribute names we treat as a pub/sub publish (producer) or subscribe (consumer).
PUBLISH_ATTRS = {"publish", "send", "emit"}
SUBSCRIBE_ATTRS = {"subscribe", "consume", "on"}


def is_contract_source(path: str) -> bool:
    """Heuristic for "this file defines shared schemas": path under a contracts/
    package. Used both to widen schema detection (treat any class here as a model)
    and to locate files for response-model resolution."""
    return "contracts" in Path(path).parts


# --------------------------------------------------------------------------- #
# Source-segment helpers.
# --------------------------------------------------------------------------- #
def _seg(source: str, node: ast.AST) -> str:
    """The exact source text of a node, collapsed to one line for use as a stable
    signature fragment. Falls back to unparsing if segment extraction fails."""
    text = ast.get_source_segment(source, node)
    if text is None:
        try:
            text = ast.unparse(node)
        except Exception:
            text = "<?>"
    return " ".join(text.split())


def _name_of(node: ast.AST) -> str | None:
    """Best-effort dotted name for a Name/Attribute node (e.g. `app.router`)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


# --------------------------------------------------------------------------- #
# Schema-model index (for route response-field embedding).
# --------------------------------------------------------------------------- #
def build_model_index(sources: dict[str, str]) -> dict[str, str]:
    """Map schema model name -> a stable field signature ("field1:ann, field2:ann").

    `sources` maps file path -> file content. We scan every source for schema
    models (Pydantic BaseModel subclasses, or any class defined under contracts/)
    and record its sorted field list. Sorting makes the signature order-insensitive
    so reordering fields is not spuriously flagged as a change.
    """
    index: dict[str, str] = {}
    for path, content in sources.items():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        contract_file = is_contract_source(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_schema_class(node, contract_file):
                index[node.name] = _model_field_signature(node)
    return index


def _is_schema_class(node: ast.ClassDef, contract_file: bool) -> bool:
    """A class is a schema model if it subclasses BaseModel, or if it lives in a
    contracts/ file (the demo convention that shared schemas are dataclass-like)."""
    for base in node.bases:
        if _name_of(base) in {"BaseModel", "pydantic.BaseModel"}:
            return True
    return contract_file


def _model_field_signature(node: ast.ClassDef) -> str:
    """Sorted "name:annotation" list for a schema class's annotated fields."""
    fields = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ann = _name_of(stmt.annotation) or _seg_ann(stmt.annotation)
            fields.append(f"{stmt.target.id}:{ann}")
    return ", ".join(sorted(fields))


def _seg_ann(annotation: ast.AST) -> str:
    try:
        return " ".join(ast.unparse(annotation).split())
    except Exception:
        return "?"


# --------------------------------------------------------------------------- #
# Entity extraction (producers).
# --------------------------------------------------------------------------- #
def extract_entities(source: str, service: str, path: str, model_index: dict[str, str]) -> list[Entity]:
    """Extract all producer entities defined in one source file.

    `model_index` lets route extraction embed response-model fields. Entities are
    returned WITHOUT commit bounds (origin/latest) — those are stamped by the
    caller (extract_repo at HEAD, or ingest per commit).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    entities: list[Entity] = []
    contract_file = is_contract_source(path)

    for node in ast.walk(tree):
        # ---- HTTP routes: decorated function defs. --------------------------
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for route in _routes_from_decorators(node, source, model_index):
                method, route_path, signature = route
                entities.append(
                    Entity(
                        entity_id=make_id(service, Kind.HTTP_ROUTE, normalize_route(method, route_path)),
                        kind=Kind.HTTP_ROUTE,
                        service=service,
                        signature=signature,
                        def_file=path,
                        def_line=node.lineno,
                    )
                )

        # ---- Schema models + fields. ---------------------------------------
        if isinstance(node, ast.ClassDef) and _is_schema_class(node, contract_file):
            entities.append(
                Entity(
                    entity_id=make_id(service, Kind.SCHEMA_MODEL, node.name),
                    kind=Kind.SCHEMA_MODEL,
                    service=service,
                    signature=_model_field_signature(node),
                    def_file=path,
                    def_line=node.lineno,
                )
            )
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    entities.append(
                        Entity(
                            entity_id=make_id(service, Kind.SCHEMA_FIELD, f"{node.name}.{stmt.target.id}"),
                            kind=Kind.SCHEMA_FIELD,
                            service=service,
                            # Full declaration source: captures a type change AND a
                            # validator/Field(...) addition (e.g. currency ISO-4217),
                            # so both surface as change events.
                            signature=_seg(source, stmt),
                            def_file=path,
                            def_line=stmt.lineno,
                        )
                    )

        # ---- Config/env reads: os.environ["X"] / os.getenv("X"). -----------
        for key in _env_keys(node):
            entities.append(
                Entity(
                    entity_id=make_id(service, Kind.CONFIG_KEY, key),
                    kind=Kind.CONFIG_KEY,
                    service=service,
                    signature="env",
                    def_file=path,
                    def_line=getattr(node, "lineno", None),
                )
            )

        # ---- Queue topics: <x>.publish("topic"). ---------------------------
        for topic in _published_topics(node):
            entities.append(
                Entity(
                    entity_id=make_id(service, Kind.QUEUE_TOPIC, topic),
                    kind=Kind.QUEUE_TOPIC,
                    service=service,
                    signature=topic,
                    def_file=path,
                    def_line=getattr(node, "lineno", None),
                )
            )

    # ---- Exports: only meaningful in a package __init__.py. ----------------
    if Path(path).name == "__init__.py":
        for name in _exports(tree):
            entities.append(
                Entity(
                    entity_id=make_id(service, Kind.EXPORT, name),
                    kind=Kind.EXPORT,
                    service=service,
                    signature=name,
                    def_file=path,
                    def_line=1,
                )
            )

    return _dedupe_entities(entities)


def _routes_from_decorators(
    node: ast.FunctionDef | ast.AsyncFunctionDef, source: str, model_index: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Yield (method, path, signature) for each route decorator on a function.

    Signature = "METHOD /path(params) -> ResponseModel{fields}". The response model
    is taken from a `response_model=` decorator kwarg or the function's return
    annotation, then expanded via `model_index` so a field rename in the model
    changes the route signature.
    """
    routes = []
    # Parameter part of the signature: the handler's annotated args, excluding the
    # conventional request/self first arg noise. We keep names+annotations because
    # adding a param (idempotency_key) is a signature change.
    params = ", ".join(
        f"{a.arg}:{_seg_ann(a.annotation)}" if a.annotation else a.arg
        for a in node.args.args
        if a.arg not in {"self", "request", "req"}
    )
    for dec in node.decorator_list:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
            continue
        method = dec.func.attr.lower()
        if method not in HTTP_METHODS:
            continue
        # First positional string constant is the path.
        route_path = None
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                route_path = arg.value
                break
        if route_path is None:
            continue
        # Response model: decorator kwarg response_model=..., else return annotation.
        resp_model = None
        for kw in dec.keywords:
            if kw.arg == "response_model":
                resp_model = _name_of(kw.value)
        if resp_model is None and node.returns is not None:
            resp_model = _name_of(node.returns)
        resp_part = ""
        if resp_model:
            fields = model_index.get(resp_model)
            resp_part = f" -> {resp_model}{{{fields}}}" if fields is not None else f" -> {resp_model}"
        signature = f"{normalize_route(method, route_path)}({params}){resp_part}"
        routes.append((method, route_path, signature))
    return routes


def _env_keys(node: ast.AST) -> list[str]:
    """String keys read from the environment at this node."""
    keys = []
    # os.environ["X"]
    if isinstance(node, ast.Subscript):
        base = _name_of(node.value)
        if base in {"os.environ", "environ"} and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                keys.append(node.slice.value)
    # os.getenv("X") / os.environ.get("X")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        fn = _name_of(node.func)
        if fn in {"os.getenv", "os.environ.get", "environ.get"} and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.append(first.value)
    return keys


def _published_topics(node: ast.AST) -> list[str]:
    """Topic string literals published at this node (`x.publish("topic")`)."""
    topics = []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in PUBLISH_ATTRS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                topics.append(first.value)
    return topics


def _exports(tree: ast.Module) -> list[str]:
    """Exported names from a package __init__: prefer __all__, else top-level defs.

    We also treat `from .mod import A, B` re-exports as exports, since that is how
    the demo's contracts package surfaces its schema classes.
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return [
                            e.value for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.ImportFrom):
            names.extend(alias.asname or alias.name for alias in node.names)
    return names


def _dedupe_entities(entities: list[Entity]) -> list[Entity]:
    """Collapse duplicate ids (e.g. an env var read in two places) keeping the
    first occurrence, which carries the earliest def_line."""
    seen: dict[str, Entity] = {}
    for e in entities:
        seen.setdefault(e.entity_id, e)
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Edge extraction (consumers).
# --------------------------------------------------------------------------- #
def extract_edges(source: str, service: str, path: str, producer_index: "ProducerIndex") -> list[Edge]:
    """Extract outbound edges: this file's consumption of known producer contracts.

    Each detected consumption is resolved against `producer_index` (built from all
    producer entities in the graph) to find the full producer id it points at. If a
    producer is unknown (e.g. extracted later), the heuristic edge is skipped — the
    manifest-seeded edge still guarantees existence.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    edges: list[Edge] = []
    for node in ast.walk(tree):
        # ---- consumes: HTTP client call to a known route. ------------------
        # The `!= service` guard on every branch keeps the interface graph to
        # CROSS-service coupling: a service calling its own route / importing its
        # own symbol / subscribing its own topic is not a dependency edge.
        for method, route_path, line in _http_calls(node):
            local = normalize_route(method, route_path)
            producer = producer_index.route(local)
            if producer and parse_id(producer)[0] != service:
                edges.append(
                    Edge(
                        from_entity=make_consumer_id(service, ConsumerTag.CALL, local),
                        to_entity=producer,
                        edge_kind=EdgeKind.CONSUMES,
                        confidence=Confidence.HIGH,
                        source=EdgeSource.ROUTE_MATCH,
                        from_file=path,
                        from_line=line,
                    )
                )

        # ---- imports of a known schema/export. -----------------------------
        for name, line in _imported_names(node):
            producer = producer_index.symbol(name)
            if producer and parse_id(producer)[0] != service:
                kind = EdgeKind.DEPENDS_ON_SCHEMA if ":schema:" in producer else EdgeKind.IMPORTS
                edges.append(
                    Edge(
                        from_entity=make_consumer_id(service, ConsumerTag.IMPORT, name),
                        to_entity=producer,
                        edge_kind=kind,
                        confidence=Confidence.HIGH,
                        source=EdgeSource.IMPORT_PARSE,
                        from_file=path,
                        from_line=line,
                    )
                )

        # ---- subscribes to a known topic. ----------------------------------
        for topic, line in _subscribed_topics(node):
            producer = producer_index.topic(topic)
            if producer and parse_id(producer)[0] != service:
                edges.append(
                    Edge(
                        from_entity=make_consumer_id(service, ConsumerTag.SUBSCRIBE, topic),
                        to_entity=producer,
                        edge_kind=EdgeKind.SUBSCRIBES,
                        confidence=Confidence.HIGH,
                        source=EdgeSource.SUBSCRIBE_MATCH,
                        from_file=path,
                        from_line=line,
                    )
                )

    return edges


def _http_calls(node: ast.AST) -> list[tuple[str, str, int]]:
    """(method, path, line) for HTTP client calls at this node.

    Recognizes `httpx.post(url, ...)`, `client.get(url)`, etc. The URL is pulled
    from the first positional arg — a plain string, or the trailing literal of an
    f-string like f"{base}/v1/charge" (we take the substring from the first "/").
    """
    out = []
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return out
    method = node.func.attr.lower()
    if method not in HTTP_METHODS:
        return out
    base = _name_of(node.func.value)
    root = base.split(".")[0] if base else ""
    if root not in HTTP_CLIENT_NAMES:
        return out
    if not node.args:
        return out
    url_path = _url_path_from_arg(node.args[0])
    if url_path:
        out.append((method, url_path, node.lineno))
    return out


def _url_path_from_arg(arg: ast.AST) -> str | None:
    """Extract the path portion of a URL argument (plain string or f-string)."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return _path_of(arg.value)
    if isinstance(arg, ast.JoinedStr):
        # Concatenate constant parts; a runtime {base} contributes nothing but the
        # trailing "/v1/charge" constant is what we need to match a route.
        text = "".join(
            part.value for part in arg.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        return _path_of(text)
    return None


def _path_of(url: str) -> str | None:
    """Return the path portion of a (possibly partial) URL string."""
    idx = url.find("/")
    return url[idx:] if idx != -1 else None


def _imported_names(node: ast.AST) -> list[tuple[str, int]]:
    """(imported_name, line) for `from <known> import A, B` statements.

    We return every imported name; the producer_index decides which resolve to a
    known schema/export. The module gate (that `module` names a known producer
    package) is applied by the index lookup, so a stray `from typing import ...`
    simply resolves to nothing.
    """
    out = []
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            out.append((alias.name, node.lineno))
    return out


def _subscribed_topics(node: ast.AST) -> list[tuple[str, int]]:
    """(topic, line) for `x.subscribe("topic")` / decorator subscriptions."""
    out = []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in SUBSCRIBE_ATTRS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append((first.value, node.lineno))
    return out


class ProducerIndex:
    """Lookup from a consumer-observed contract to the producer entity id.

    Built once from all producer entities in the graph. Routes are matched by
    normalized "METHOD /path"; symbols (schemas/exports) by their local name;
    topics by their literal string. Keeping this in one object means the consumer
    extractor never queries the DB directly and stays easy to test.
    """

    def __init__(self, entities: list[Entity]):
        self._routes: dict[str, str] = {}
        self._symbols: dict[str, str] = {}
        self._topics: dict[str, str] = {}
        # A schema model and an export can share a local name (e.g. `contracts`
        # exposes both `export:Charge` and `schema:Charge`). An import of a shared
        # model should resolve to the SCHEMA (a depends_on_schema edge), so we
        # register exports first and let schemas overwrite — making the preference
        # explicit rather than relying on iteration order.
        for e in entities:
            if e.kind is Kind.EXPORT:
                self._symbols[parse_id(e.entity_id)[2]] = e.entity_id
        for e in entities:
            _, _, local = parse_id(e.entity_id)
            if e.kind is Kind.HTTP_ROUTE:
                self._routes[local] = e.entity_id
            elif e.kind is Kind.SCHEMA_MODEL:
                self._symbols[local] = e.entity_id  # schema wins over a same-named export
            elif e.kind is Kind.QUEUE_TOPIC:
                self._topics[local] = e.entity_id

    def route(self, local: str) -> str | None:
        return self._routes.get(local)

    def symbol(self, name: str) -> str | None:
        return self._symbols.get(name)

    def topic(self, topic: str) -> str | None:
        return self._topics.get(topic)


# --------------------------------------------------------------------------- #
# Manifest seeding.
# --------------------------------------------------------------------------- #
def seed_manifest_edges(graph: Graph, repo: str, service: str) -> None:
    """Read <repo>/manifest.yaml and upsert every declared edge (source=MANIFEST).

    Manifest shape (see dummy-org services):
        service: orders-svc
        consumes:
          - { edge_kind: consumes,          target: "payments-svc::http:POST /v1/charge" }
          - { edge_kind: depends_on_schema, target: "contracts::schema:Order" }
        subscribes:
          - { target: "orders-svc::topic:order.created" }
        publishes:
          - { target: "orders-svc::topic:order.created" }   # producer side, entity only

    `consumes`/`subscribes` create edges (the consumer-side pseudo entity id is
    derived from the target's tag). `publishes` does not create an edge — the topic
    entity itself is produced by the extractor when it sees the publish call.
    """
    manifest_path = Path(repo) / "manifest.yaml"
    if not manifest_path.exists():
        return
    data = yaml.safe_load(manifest_path.read_text()) or {}

    def _add(target: str, edge_kind: EdgeKind, consumer_tag: ConsumerTag) -> None:
        _, _, local = parse_id(target)
        graph.upsert_edge(
            Edge(
                from_entity=make_consumer_id(service, consumer_tag, local),
                to_entity=target,
                edge_kind=edge_kind,
                confidence=Confidence.MEDIUM,   # declared but not code-located (yet)
                source=EdgeSource.MANIFEST,
            )
        )

    for item in data.get("consumes", []) or []:
        edge_kind = EdgeKind(item.get("edge_kind", "consumes"))
        # Consumer tag depends on the edge kind: an HTTP consume uses CALL, a
        # schema/import dependency uses IMPORT.
        tag = ConsumerTag.CALL if edge_kind is EdgeKind.CONSUMES else ConsumerTag.IMPORT
        _add(item["target"], edge_kind, tag)

    for item in data.get("subscribes", []) or []:
        _add(item["target"], EdgeKind.SUBSCRIBES, ConsumerTag.SUBSCRIBE)


# --------------------------------------------------------------------------- #
# Org-level orchestration.
# --------------------------------------------------------------------------- #
#
# The demo's dummy-org is a single git monorepo containing several service
# subdirectories. So `extract` operates on the ORG ROOT and derives each entity's
# service from the first path component of its file (contracts/schemas.py ->
# "contracts", orders-svc/src/pay.py -> "orders-svc"). Crucially the schema-model
# index is built ONCE across the whole org, so a route in payments-svc can resolve
# the fields of a Charge model that lives in contracts/ (cross-directory).

_IGNORE_DIRS = {".git", ".orgcontext", "__pycache__"}


def service_of(rel_path: str) -> str:
    """Service namespace = the first path component of an org-relative file path."""
    return Path(rel_path).parts[0]


def read_org_sources(root: str) -> dict[str, str]:
    """All *.py files under the org root, keyed by org-relative path."""
    root_p = Path(root)
    out: dict[str, str] = {}
    for p in root_p.rglob("*.py"):
        if _IGNORE_DIRS & set(p.parts):
            continue
        out[str(p.relative_to(root_p))] = p.read_text()
    return out


def extract_entities_from_sources(sources: dict[str, str]) -> list[Entity]:
    """Extract producers from a whole org source map, deriving service per file and
    sharing one model index across every file (so cross-directory response-model
    resolution works)."""
    model_index = build_model_index(sources)
    entities: list[Entity] = []
    for path, content in sources.items():
        entities.extend(extract_entities(content, service_of(path), path, model_index))
    return entities


def extract_org(graph: Graph, root: str, *, at_commit: str | None = None) -> None:
    """Full two-phase extraction of an org monorepo into the graph.

    Phase 1: register every producer across all services (using a shared model
    index). Phase 2: seed each service's manifest edges, then resolve heuristic
    consumer edges against the now-complete producer set. When `at_commit` is
    given, it is stamped as origin/latest for newly-seen entities.
    """
    sources = read_org_sources(root)

    # -- Phase 1: producers.
    for e in extract_entities_from_sources(sources):
        if at_commit is not None:
            e = Entity(**{**e.__dict__, "origin_commit": at_commit, "latest_commit": at_commit})
        graph.upsert_entity(e)

    # -- Phase 2a: manifest-declared edges (recall floor), one per service dir.
    for service_dir in sorted({service_of(p) for p in sources}):
        seed_manifest_edges(graph, str(Path(root) / service_dir), service_dir)

    # -- Phase 2b: heuristic edges, resolved against all producers.
    producer_index = ProducerIndex(graph.all_entities())
    for path, content in sources.items():
        for edge in extract_edges(content, service_of(path), path, producer_index):
            graph.upsert_edge(edge)


def run(args) -> int:
    """CLI handler for `orgctx extract <repo>`.

    `<repo>` is the org root (the dummy-org monorepo for the demo). We extract every
    service under it in two phases so cross-service edges resolve fully.
    """
    graph = Graph(Path(args.central) / "graph.db")
    extract_org(graph, args.repo)
    print(f"extracted {len(graph.all_entities())} entities and {len(graph.all_edges())} edges")
    graph.close()
    return 0
