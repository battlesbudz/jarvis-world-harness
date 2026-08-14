#!/usr/bin/env bash
# Regression test: a timed-out milestone check must not leave descendant processes alive.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-gate-tree.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

command -v timeout >/dev/null || { echo "timeout not found on PATH" >&2; exit 127; }

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
        "trap 'exit 0' TERM; (trap '' TERM HUP; sleep 5; touch .harness/LEAKED_DESCENDANT; sleep 60) & wait"
      ],
      "timeout_seconds": 1
    }
  ]
}
JSON

cd "$TMP"
started=$(date +%s)
set +e
MILESTONE_ID=TEST_TIMEOUT timeout 8s python3 bin/milestone-gate.py --json --no-record > gate.out 2> gate.err
rc=$?
set -e
elapsed=$(( $(date +%s) - started ))

[ "$rc" -ne 124 ] || {
  echo "timed-out gate hung after its direct leader exited" >&2
  cat gate.out >&2 || true
  cat gate.err >&2 || true
  exit 1
}

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

# The direct bash leader exits on SIGTERM, while its child ignores TERM/HUP and
# retains the output pipes. The gate must still SIGKILL the surviving process group
# and return well inside the outer watchdog.
[ "$elapsed" -lt 8 ] || {
  echo "timed-out gate did not return within its bounded cleanup window" >&2
  exit 1
}

# The stubborn child would touch this marker at t=5s if it escaped the group kill.
sleep 3
[ ! -e .harness/LEAKED_DESCENDANT ] || {
  echo "timed-out gate leaked a descendant that modified the worktree" >&2
  exit 1
}

# If SIGKILL still cannot close inherited pipes, cleanup must raise a fail-closed
# infrastructure error after a second bounded communicate call—never wait forever.
python3 - "$ROOT/bin/milestone-gate.py" <<'PY'
import importlib.util
import subprocess
import sys

spec = importlib.util.spec_from_file_location("milestone_gate", sys.argv[1])
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

class Pipe:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

class UndrainableProcess:
    pid = 999999999

    def __init__(self):
        self.stdout = Pipe()
        self.stderr = Pipe()
        self.calls = 0

    def poll(self):
        return 0

    def communicate(self, timeout=None):
        assert timeout is not None, "communicate must always be bounded during cleanup"
        self.calls += 1
        raise subprocess.TimeoutExpired(["fixture"], timeout)

proc = UndrainableProcess()
try:
    module.terminate_process_tree(proc, grace_seconds=0.01)
except module.GateConfigurationError:
    pass
else:
    raise AssertionError("undrainable pipes did not fail closed")

assert proc.calls == 2, proc.calls
assert proc.stdout.closed and proc.stderr.closed
PY

echo "Gate timeout-tree test passed: timed-out descendants cannot outlive the gate"
