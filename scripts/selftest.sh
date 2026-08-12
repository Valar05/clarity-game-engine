#!/usr/bin/env bash
set -euo pipefail

ROOT="${CLARITY_TEST_HOME:-$(mktemp -d)}"
cleanup() { rm -rf "$ROOT"; }
trap cleanup EXIT

clarity --root "$ROOT" init
clarity --root "$ROOT" doctor
clarity --root "$ROOT" mission add \
  --kind world.build_slice \
  --spec examples/april-test-node.mission.json \
  --idempotency-key april-canary-v1
clarity --root "$ROOT" mission run --once
clarity --root "$ROOT" verify
clarity --root "$ROOT" status

python - <<'PY' "$ROOT"
import json, pathlib, sqlite3, sys
root = pathlib.Path(sys.argv[1])
con = sqlite3.connect(root / 'clarity.db')
state = con.execute("select state from missions where idempotency_key='april-canary-v1'").fetchone()
assert state == ('promoted',), state
artifact = con.execute("select relative_path from artifacts limit 1").fetchone()
assert artifact and (root / artifact[0]).exists(), artifact
print(json.dumps({'ok': True, 'canary': 'april-test-node', 'state': state[0]}))
PY
