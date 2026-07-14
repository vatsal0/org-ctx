#!/usr/bin/env bash
# Introduce the KNOWN breaking change for the eval (Scenario A).
#
# Renames `Charge.amount` -> `Charge.amount_cents` in the shared contracts schema
# AND updates payments-svc to build the response with the new field. On ingest this
# single commit produces:
#   - `removed`  on contracts::schema:Charge.amount
#   - `added`    on contracts::schema:Charge.amount_cents
#   - `signature_change` on contracts::schema:Charge (its field set changed)
#   - `signature_change` on payments-svc::http:POST /v1/charge  <-- the breaking one
#       (the route's signature embeds Charge's fields, so the rename changes it)
#
# orders-svc consumes that route and reads resp.json()["amount"], so this is
# exactly the break it must be warned about. notifications-svc consumes neither the
# route nor Charge, so it must stay silent.
#
# Run AFTER seed-history.sh. Deterministic date so the eval is reproducible.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORG="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ORG"

git config user.name "Seed Bot"
git config user.email "seed@example.com"
git config commit.gpgsign false

# Rename the field in the shared schema.
python3 - "$ORG/contracts/schemas.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace("    amount: int\n", "    amount_cents: int\n")
open(p, "w").write(s)
PY

# Update the payments handler to build the response with the renamed field.
python3 - "$ORG/payments-svc/app.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace("amount=amount", "amount_cents=amount")
open(p, "w").write(s)
PY

git add contracts/schemas.py payments-svc/app.py
WHEN="1710000000 +0000"
GIT_AUTHOR_DATE="$WHEN" GIT_COMMITTER_DATE="$WHEN" \
  git commit -q -m "refactor(contracts): rename Charge.amount -> amount_cents (BREAKING)"

echo "introduced breaking change at $(git rev-parse --short HEAD)"
