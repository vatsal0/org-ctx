#!/usr/bin/env bash
# Seed a KNOWN commit history into the dummy org.
#
# This script is the demo's ground-truth timeline generator. It initializes a git
# repo inside dummy-org/ and lays down a scripted sequence of commits so that, per
# entity, we know exactly what SHOULD appear on its change timeline:
#   - an `added` (the feature) — the ORIGIN for attribution,
#   - a later `modified` (a field-validation tweak) — folds into a one-liner,
#   - for /v1/charge, a mid-life `signature_change` (idempotency key),
#   - and a dozen internal-only churn commits on the UNCONSUMED /v1/health route,
#     which the compression policy must DROP from state_summary.
#
# It is deterministic: fixed author, fixed dates (so the eval never depends on the
# wall clock), fixed edit sequence. Re-running it from scratch reproduces the same
# history. Run from anywhere: it resolves paths relative to this script.
set -euo pipefail

# ---- Locate the dummy-org root (parent of this scripts/ dir). ----------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORG="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ORG"

# ---- Fresh repo. Deterministic identity + dates. -----------------------------
rm -rf .git
git init -q
git config user.name "Seed Bot"
git config user.email "seed@example.com"
git config commit.gpgsign false

# A monotonically increasing fake clock so commit order is stable and dates are
# reproducible. Each commit advances the clock by one day.
CLOCK=1700000000  # fixed epoch base
commit() {
  local msg="$1"
  CLOCK=$((CLOCK + 86400))
  local when="$CLOCK +0000"
  GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" git commit -q -m "$msg"
}

# churn_fn <file> <function_name> <note>: insert a comment as the first line of the
# named function's body, so the edit lands INSIDE that entity's line span (a true
# body-touch that ingest classifies as an `internal` change). Robust to line drift
# because it locates the function via the AST, not a fixed line number.
churn_fn() {
  python3 - "$1" "$2" "$3" <<'PY'
import sys, ast
path, fn, note = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().splitlines(keepends=True)
tree = ast.parse("".join(lines))
target = next(
    (n for n in ast.walk(tree)
     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn),
    None,
)
assert target is not None, f"function {fn} not found in {path}"
first = target.body[0]
indent = " " * first.col_offset
lines.insert(first.lineno - 1, f"{indent}# churn: {note}\n")
open(path, "w").write("".join(lines))
PY
  git add "$1"
}

# ---- 1. Skeleton: everything is `added`. -------------------------------------
git add contracts payments-svc orders-svc notifications-svc */manifest.yaml
commit "feat: scaffold contracts + payments/orders/notifications services"

# ---- 2. payments: signature_change — add idempotency_key to /v1/charge. ------
# The route's ORIGIN is commit 1 (added); this is a mid-life signature mutation,
# so Scenario B can assert both origin and latest are retained.
python3 - "$ORG/payments-svc/app.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace(
    "def create_charge(order_id: str, amount: int, currency: str) -> Charge:",
    "def create_charge(order_id: str, amount: int, currency: str, idempotency_key: str) -> Charge:",
)
open(p, "w").write(s)
PY
git add payments-svc/app.py
commit "feat(payments): add idempotency_key to POST /v1/charge"

# ---- 3. contracts: currency gains ISO-4217 validation (foldable modification). -
python3 - "$ORG/contracts/schemas.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
if "from pydantic import BaseModel" in s and "Field" not in s:
    s = s.replace("from pydantic import BaseModel", "from pydantic import BaseModel, Field")
s = s.replace(
    "    currency: str\n",
    "    currency: str = Field(pattern=\"^[A-Z]{3}$\")\n",
)
open(p, "w").write(s)
PY
git add contracts/schemas.py
commit "feat(contracts): validate Charge.currency as ISO-4217"

# ---- 4-15. Internal-only churn on the UNCONSUMED /v1/health route. -----------
# Twelve body-touch edits to payments-svc health(). Each produces an `internal`
# change event on payments-svc::http:GET /v1/health — an entity with NO inbound
# edge. The compression policy must drop every one of these from state_summary;
# the state-size-over-commits curve must stay flat across this stretch.
for i in $(seq 1 12); do
  churn_fn "$ORG/payments-svc/app.py" health "no-op refactor pass $i"
  commit "chore(payments): internal churn on health pass $i"
done

echo "seeded $(git rev-list --count HEAD) commits into $ORG"
