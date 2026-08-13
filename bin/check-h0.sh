#!/usr/bin/env bash
# Static H0 harness checks. Does not invoke Codex or Unreal.
set -euo pipefail
cd "$(dirname "$0")/.."

required=(
  AGENTS.md MILESTONE.md ACCEPTANCE-TESTS.md HARNESS-RULES.md
  spec/CORE-LAWS.md spec/WORLD-VISION.md docs/ARCHITECTURE.md
  bin/run-codex.sh bin/health-codex.sh bin/supervise-codex.sh bin/restart-codex.sh
)
for f in "${required[@]}"; do
  [ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

for f in bin/run-codex.sh bin/health-codex.sh bin/supervise-codex.sh bin/restart-codex.sh; do
  bash -n "$f"
done

grep -q -- '--sandbox "$SANDBOX"' bin/run-codex.sh
grep -q 'thread.started' bin/run-codex.sh
grep -q 'resume "$session_id"' bin/run-codex.sh
grep -q 'codex-supervisor.pause' bin/supervise-codex.sh
grep -q 'BACKOFF_MAX' bin/supervise-codex.sh
grep -q 'MAX_EVENT_AGE_MIN' bin/health-codex.sh

echo "H0 static harness checks passed"
