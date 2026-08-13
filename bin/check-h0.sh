#!/usr/bin/env bash
# Static H0 harness checks. Does not invoke real Codex or Unreal.
set -euo pipefail
cd "$(dirname "$0")/.."

required=(
  AGENTS.md MILESTONE.md ACCEPTANCE-TESTS.md HARNESS-RULES.md
  spec/CORE-LAWS.md spec/WORLD-VISION.md docs/ARCHITECTURE.md
  milestones/H0/gate.json
  bin/run-codex.sh bin/health-codex.sh bin/supervise-codex.sh bin/restart-codex.sh
  bin/milestone-gate.py bin/test-codex-lifecycle.sh
)
for f in "${required[@]}"; do
  [ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

for f in bin/run-codex.sh bin/health-codex.sh bin/supervise-codex.sh bin/restart-codex.sh bin/test-codex-lifecycle.sh; do
  bash -n "$f"
done
python3 -m py_compile bin/milestone-gate.py
python3 - <<'PY'
import json
p='milestones/H0/gate.json'
d=json.load(open(p, encoding='utf-8'))
assert d.get('milestone') == 'H0'
assert len(d.get('checks', [])) >= 2
PY

grep -q -- '--sandbox "$SANDBOX"' bin/run-codex.sh
grep -q 'thread.started' bin/run-codex.sh
grep -q 'resume "$session_id"' bin/run-codex.sh
grep -q 'codex-supervisor.pause' bin/supervise-codex.sh
grep -q 'BACKOFF_MAX' bin/supervise-codex.sh
grep -q 'milestone-gate.py' bin/supervise-codex.sh
grep -q 'MAX_EVENT_AGE_MIN' bin/health-codex.sh

echo "H0 static harness checks passed"
