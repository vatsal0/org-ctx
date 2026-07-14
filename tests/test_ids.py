"""Unit tests for the canonical id / route-normalization rules (orgctx/ids.py).

These are the load-bearing invariants: if producer and consumer sides normalize a
route differently, the `consumes` edge silently fails to resolve. So we pin the
normalization behavior explicitly.
"""

from orgctx.ids import make_consumer_id, make_id, normalize_route, parse_id, slugify
from orgctx.models import ConsumerTag, Kind


def test_normalize_route_method_and_slash():
    # Method upper-cased; trailing slash stripped; leading slash ensured.
    assert normalize_route("post", "/v1/charge") == "POST /v1/charge"
    assert normalize_route("POST", "/v1/charge/") == "POST /v1/charge"
    assert normalize_route("get", "v1/health") == "GET /v1/health"


def test_normalize_route_path_params_collapse():
    # {id}, :id, and bare numeric segments all collapse to {} so a route and its
    # callers agree regardless of param naming or a concrete id.
    assert normalize_route("get", "/v1/charge/{id}") == "GET /v1/charge/{}"
    assert normalize_route("get", "/v1/charge/{order_id}") == "GET /v1/charge/{}"
    assert normalize_route("get", "/v1/charge/123") == "GET /v1/charge/{}"
    assert normalize_route("get", "/v1/charge/:id") == "GET /v1/charge/{}"


def test_parse_id_roundtrip_with_colons_and_spaces():
    # Route ids contain a space and the local name must survive intact.
    eid = make_id("payments-svc", Kind.HTTP_ROUTE, "POST /v1/charge")
    assert eid == "payments-svc::http:POST /v1/charge"
    service, tag, local = parse_id(eid)
    assert (service, tag, local) == ("payments-svc", "http", "POST /v1/charge")


def test_parse_id_schema_field():
    eid = make_id("contracts", Kind.SCHEMA_FIELD, "Charge.amount")
    assert parse_id(eid) == ("contracts", "schema", "Charge.amount")


def test_consumer_id_distinct_from_producer():
    # Consumer pseudo-ids use a different tag so they never collide with producers.
    prod = make_id("payments-svc", Kind.HTTP_ROUTE, "POST /v1/charge")
    cons = make_consumer_id("orders-svc", ConsumerTag.CALL, "POST /v1/charge")
    assert prod != cons
    assert cons == "orders-svc::call:POST /v1/charge"


def test_slugify_is_filesystem_safe():
    slug = slugify("payments-svc::http:POST /v1/charge")
    assert "/" not in slug and ":" not in slug and " " not in slug
    assert slug == "payments-svc-http-post-v1-charge"
