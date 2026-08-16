#!/usr/bin/env bash
# Regression test: a timed-out milestone check must not leave descendant processes alive.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-gate-tree.XXXXXX")
cleanup() {
  set +e
  for pid_file in "$TMP"/.harness/stubborn.pid "$TMP"/.harness/redirected.pid; do
    pid=$(cat "$pid_file" 2>/dev/null || true)
    command_line=$([ -n "$pid" ] && ps -o command= -p "$pid" 2>/dev/null || true)
    case "$command_line" in
      *LEAKED_DESCENDANT*|*LEAKED_REDIRECTED_DESCENDANT*)
        kill -9 "$pid" 2>/dev/null || true
        ;;
    esac
  done
  rm -rf "$TMP"
}
trap cleanup EXIT

command -v timeout >/dev/null || { echo "timeout not found on PATH" >&2; exit 127; }

mkdir -p "$TMP/bin" "$TMP/milestones/TEST_TIMEOUT" \
  "$TMP/milestones/TEST_TIMEOUT_REDIRECTED" "$TMP/milestones/TEST_ZOMBIE_ONLY" \
  "$TMP/.harness"
cp "$ROOT/bin/milestone-gate.py" "$ROOT/bin/process_group.py" "$TMP/bin/"
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
        "trap 'exit 0' TERM; (trap '' TERM HUP; printf '%s\\n' \"$BASHPID\" > .harness/stubborn.pid; sleep 5; touch .harness/LEAKED_DESCENDANT; sleep 60) & wait"
      ],
      "timeout_seconds": 1
    }
  ]
}
JSON

cat > "$TMP/milestones/TEST_ZOMBIE_ONLY/gate.json" <<'JSON'
{
  "milestone": "TEST_ZOMBIE_ONLY",
  "checks": [
    {
      "id": "successful-leader-with-orphan-zombie",
      "command": [
        "python3",
        "-c",
        "import os,time; child=os.fork(); os._exit(0) if child == 0 else time.sleep(0.1)"
      ],
      "timeout_seconds": 5
    }
  ]
}
JSON

cat > "$TMP/milestones/TEST_TIMEOUT_REDIRECTED/gate.json" <<'JSON'
{
  "milestone": "TEST_TIMEOUT_REDIRECTED",
  "checks": [
    {
      "id": "redirected-stubborn-descendant",
      "command": [
        "bash",
        "-c",
        "trap 'exit 0' TERM; (trap '' TERM HUP; printf '%s\\n' \"$BASHPID\" > .harness/redirected.pid; exec >/dev/null 2>&1; sleep 5; touch .harness/LEAKED_REDIRECTED_DESCENDANT; sleep 60) & wait"
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

# Pipe EOF is not process-tree EOF. This child redirects both streams before the
# leader exits on TERM, then attempts a delayed worktree write while ignoring TERM/HUP.
started_ns=$(python3 -c 'import time; print(time.monotonic_ns())')
set +e
MILESTONE_ID=TEST_TIMEOUT_REDIRECTED timeout 8s python3 bin/milestone-gate.py --json --no-record > redirected.out 2> redirected.err
redirected_rc=$?
set -e
ended_ns=$(python3 -c 'import time; print(time.monotonic_ns())')
redirected_elapsed_ms=$(( (ended_ns - started_ns) / 1000000 ))

[ "$redirected_rc" -ne 124 ] || {
  echo "redirected-output gate hung during process-tree cleanup" >&2
  cat redirected.out >&2 || true
  cat redirected.err >&2 || true
  exit 1
}

[ "$redirected_rc" -eq 1 ] || {
  echo "redirected-output gate returned $redirected_rc instead of 1" >&2
  cat redirected.out >&2 || true
  cat redirected.err >&2 || true
  exit 1
}

[ "$redirected_elapsed_ms" -ge 2800 ] && [ "$redirected_elapsed_ms" -lt 8000 ] || {
  echo "redirected-output gate did not honor its bounded TERM grace and cleanup window" >&2
  exit 1
}

sleep 3
[ ! -e .harness/LEAKED_REDIRECTED_DESCENDANT ] || {
  echo "redirected-output descendant survived the gate and modified the worktree" >&2
  exit 1
}

# If SIGKILL still cannot close inherited pipes, cleanup must raise a fail-closed
# infrastructure error after a second bounded communicate call—never wait forever.
python3 - "$ROOT/bin/milestone-gate.py" <<'PY'
import importlib.util
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).parent))
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

# Zombie-only groups are not executable and hold no descriptors. Exercise the
# /proc parser deterministically so slow container PID 1 reaping cannot fail gates.
python3 - "$ROOT/bin/process_group.py" <<'PY'
import importlib.util
import tempfile
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("process_group", sys.argv[1])
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    zombie = root / "101"
    zombie.mkdir()
    (zombie / "status").write_text(
        "Name:\tzombie fixture\nState:\tZ (zombie)\nNSpgid:\t5000\t777\n",
        encoding="utf-8",
    )
    assert not module.has_executable_members(777, root)

    live = root / "102"
    live.mkdir()
    (live / "status").write_text(
        "Name:\tlive fixture\nState:\tS (sleeping)\nNSpgid:\t5000\t777\n",
        encoding="utf-8",
    )
    assert module.has_executable_members(777, root)
PY

MILESTONE_ID=TEST_ZOMBIE_ONLY python3 bin/milestone-gate.py --json --no-record > zombie-only.out
grep -q '"passed": true' zombie-only.out || {
  echo "zombie-only process group falsely failed a successful gate check" >&2
  cat zombie-only.out >&2
  exit 1
}

echo "Gate timeout-tree test passed: timed-out descendants cannot outlive the gate"
