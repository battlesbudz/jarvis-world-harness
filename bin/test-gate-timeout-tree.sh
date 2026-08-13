#!/usr/bin/env bash
# Regression test: a timed-out milestone check must not leave descendant processes alive.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-gate-tree.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/milestones/TEST_TIMEOUT" "$TMP/.harness"
cp "$ROOT/bin/milestone-gate.py" "$TMP/bin/milestone-gate.py"
chmod +x "$TMP/bin/milestone-gate.py"

cat > "$TMP/milestones/TEST_TIMEOUT/gate.json" <<'JSON'
{
  "milestone": "TEST_TIMEOUT",
  "checks": [
    {
      "id": "stubborn-descendant",
      "command": [
        "bash",
        "-c",
        "trap '' TERM; (trap '' TERM; sleep 4; touch .harness/LEAKED_DESCENDANT) & wait"
      ],
      "timeout_seconds": 1
    }
  ]
}
JSON

cd "$TMP"
set +e
MILESTONE_ID=TEST_TIMEOUT python3 bin/milestone-gate.py --json --no-record > gate.out 2> gate.err
rc=$?
set -e

[ "$rc" -eq 1 ] || {
  echo "timed-out gate returned $rc instead of 1" >&2
  cat gate.out >&2 || true
  cat gate.err >&2 || true
  exit 1
}

grep -q 'process tree terminated' gate.out || {
  echo "timeout result did not report process-tree termination" >&2
  cat gate.out >&2
  exit 1
}

# The stubborn child would touch this marker at t=4s if it escaped the process group.
# The gate times out at 1s, allows a 2s TERM grace period, then must SIGKILL the group.
sleep 2
[ ! -e .harness/LEAKED_DESCENDANT ] || {
  echo "timed-out gate leaked a descendant that modified the worktree" >&2
  exit 1
}

echo "Gate timeout-tree test passed: timed-out descendants cannot outlive the gate"
