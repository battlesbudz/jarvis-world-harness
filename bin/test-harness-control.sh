#!/usr/bin/env bash
# Deterministic control-plane tests for kernel locks, handoffs, health, and gate errors.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-control.XXXXXX")
cleanup_all() {
  set +e
  if [ -d "$TMP" ]; then
    child=$(cat "$TMP"/.harness/codex-child.pid 2>/dev/null || true)
    [ -n "$child" ] && kill -- "-$child" 2>/dev/null || true
    [ -n "$child" ] && kill -9 -- "-$child" 2>/dev/null || true
    for f in "$TMP"/.harness/codex-runner.lock "$TMP"/.harness/codex-supervisor.lock "$TMP"/.harness/codex-restart.lock "$TMP"/.harness/codex-wrapper.pid \
      "$TMP"/.harness/backoff-sleep.pid "$TMP"/.harness/restart-control-wait.pid \
      "$TMP"/.harness/test-gate-child.pid "$TMP"/.harness/test-gate-parent.pid \
      "$TMP"/.harness/test-background-child.pid "$TMP"/.harness/test-escaped-child.pid \
      "$TMP"/.harness/evaluator-detached.pid "$TMP"/.harness/GATE-ACTIVE \
      "$TMP"/.harness/gate-watchdog-child.pid; do
      p=$(cat "$f" 2>/dev/null || true); [ -n "$p" ] && kill "$p" 2>/dev/null || true
    done
    for f in "$TMP"/.harness/zombie-child.pid "$TMP"/.harness/zombie-parent.pid; do
      p=$(cat "$f" 2>/dev/null || true); [ -n "$p" ] && kill "$p" 2>/dev/null || true
    done
  fi
  rm -rf "$TMP"
}
trap cleanup_all EXIT

command -v flock >/dev/null || { echo "flock not found on PATH" >&2; exit 127; }
command -v setsid >/dev/null || { echo "setsid not found on PATH" >&2; exit 127; }
mkdir -p "$TMP/bin" "$TMP/fakebin" "$TMP/spec" \
  "$TMP/milestones/TEST_INCOMPLETE" "$TMP/milestones/TEST_BAD" \
  "$TMP/milestones/TEST_GATE_RACE" "$TMP/milestones/TEST_GATE_BACKGROUND" \
  "$TMP/milestones/TEST_GATE_ESCAPED" "$TMP/milestones/TEST_GATE_EVALUATOR_DEATH"
for f in run-codex.sh codex-process.py process_group.py supervise-codex.sh restart-codex.sh health-codex.sh milestone-gate.py milestone-gate-watchdog.sh; do
  cp "$ROOT/bin/$f" "$TMP/bin/$f"
done
chmod +x "$TMP/bin/"*
printf '# Progress\n' > "$TMP/PROGRESS.md"
printf '# Active Milestone — H0: Test\n' > "$TMP/MILESTONE.md"
printf '# Acceptance Tests\n' > "$TMP/ACCEPTANCE-TESTS.md"
printf '# Laws\n' > "$TMP/spec/CORE-LAWS.md"
cat > "$TMP/milestones/TEST_INCOMPLETE/gate.json" <<'JSON'
{"milestone":"TEST_INCOMPLETE","checks":[{"id":"always-fail","command":["bash","-c","exit 1"],"timeout_seconds":5}]}
JSON
cat > "$TMP/milestones/TEST_BAD/gate.json" <<'JSON'
{"milestone":"TEST_BAD","checks":[{"id":"missing-executable","command":["definitely-not-a-real-jwh-command"],"timeout_seconds":5}]}
JSON
cat > "$TMP/milestones/TEST_GATE_RACE/gate.json" <<'JSON'
{"milestone":"TEST_GATE_RACE","checks":[{"id":"gate-window","command":["bash","-c","printf '%s\\n' \"$$\" > .harness/test-gate-child.pid; printf '%s\\n' \"$PPID\" > .harness/test-gate-parent.pid; touch .harness/test-gate-entered; while [ ! -e .harness/test-gate-release ]; do sleep 0.05; done; exit 1"],"timeout_seconds":15}]}
JSON
cat > "$TMP/milestones/TEST_GATE_BACKGROUND/gate.json" <<'JSON'
{"milestone":"TEST_GATE_BACKGROUND","checks":[{"id":"background-after-exit","command":["bash","-c","(trap '' TERM HUP; printf '%s\\n' \"$BASHPID\" > .harness/test-background-child.pid; exec >/dev/null 2>&1; sleep 1; touch .harness/LEAKED_BACKGROUND_CHECK; sleep 30) & exit 0"],"timeout_seconds":5}]}
JSON
cat > "$TMP/milestones/TEST_GATE_ESCAPED/gate.json" <<'JSON'
{"milestone":"TEST_GATE_ESCAPED","checks":[{"id":"escaped-after-exit","command":["bash","-c","if [ ! -e .harness/test-escaped-once ]; then touch .harness/test-escaped-once; setsid bash -c 'trap \"\" TERM HUP; printf \"%s\\n\" \"$BASHPID\" > .harness/test-escaped-child.pid; exec >/dev/null 2>&1; sleep 1; touch .harness/LEAKED_ESCAPED_CHECK; sleep 30' & fi; exit 1"],"timeout_seconds":5}]}
JSON
cat > "$TMP/milestones/TEST_GATE_EVALUATOR_DEATH/gate.json" <<'JSON'
{"milestone":"TEST_GATE_EVALUATOR_DEATH","checks":[{"id":"close-fds-after-evaluator-death","command":["python3","evaluator-death-check.py"],"timeout_seconds":30}]}
JSON
cat > "$TMP/evaluator-death-check.py" <<'PY'
import os
import subprocess
import sys
import time
from pathlib import Path

child_code = r'''
import os
import signal
import time
from pathlib import Path

signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
Path(".harness/evaluator-detached.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
time.sleep(5)
Path(".harness/LEAKED_EVALUATOR_CHILD").touch()
time.sleep(30)
'''
subprocess.Popen(
    [sys.executable, "-c", child_code],
    close_fds=True,
    start_new_session=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
Path(".harness/test-gate-child.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
Path(".harness/test-gate-parent.pid").write_text(f"{os.getppid()}\n", encoding="utf-8")
while not Path(".harness/evaluator-detached.pid").exists():
    time.sleep(0.01)
Path(".harness/test-gate-entered").touch()
while True:
    time.sleep(1)
PY
TRACE="$TMP/fake-codex.args"
cat > "$TMP/fakebin/codex" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then echo "codex-cli fake-0.3"; exit 0; fi
{
  printf 'CALL'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$FAKE_CODEX_TRACE"
last=""; prev=""
for arg in "$@"; do
  if [ "$prev" = "--output-last-message" ]; then last="$arg"; fi
  prev="$arg"
done
[ -n "$last" ] && printf 'fake\n' > "$last"
if ! printf '%s\n' "$*" | grep -q ' resume fixture-thread-123 '; then
  printf '%s\n' '{"type":"thread.started","thread_id":"fixture-thread-123"}'
fi
if [ -n "${FAKE_CODEX_RELEASE_FILE:-}" ]; then
  while [ ! -e "$FAKE_CODEX_RELEASE_FILE" ]; do sleep 0.05; done
elif [ -n "${FAKE_CODEX_ESCAPE_MARKER:-}" ]; then
  setsid python3 -c '
import os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
fd = os.open(os.devnull, os.O_RDWR)
os.dup2(fd, 0); os.dup2(fd, 1); os.dup2(fd, 2)
time.sleep(4)
Path(os.environ["FAKE_CODEX_ESCAPE_MARKER"]).touch()
time.sleep(30)
' &
  wait
elif [ -n "${FAKE_CODEX_SLEEP:-}" ]; then
  sleep "$FAKE_CODEX_SLEEP"
fi
printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
FAKE
chmod +x "$TMP/fakebin/codex"
cat > "$TMP/fakebin/codex-redirected" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
marker="$1"
trap 'exit 0' TERM
(
  trap '' TERM HUP
  exec >/dev/null 2>&1
  sleep 4
  touch "$marker"
  sleep 30
) &
printf '%s\n' '{"type":"thread.started","thread_id":"redirected-fixture"}'
wait
FAKE
chmod +x "$TMP/fakebin/codex-redirected"
JWH_REAL_SLEEP=$(command -v sleep)
JWH_REAL_FLOCK=$(command -v flock)
JWH_REAL_PYTHON=$(command -v python3)
export JWH_REAL_SLEEP JWH_REAL_FLOCK JWH_REAL_PYTHON
cat > "$TMP/fakebin/sleep" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ -n "${JWH_TEST_SLEEP_MARKER:-}" ] && [ "$#" -eq 1 ] && [ "$1" = "${JWH_TEST_SLEEP_ARG:-}" ]; then
  printf '%s\n' "$$" > "$JWH_TEST_SLEEP_MARKER"
fi
exec "$JWH_REAL_SLEEP" "$@"
FAKE
cat > "$TMP/fakebin/flock" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ -n "${JWH_TEST_FLOCK_MARKER:-}" ] && [ "$#" -eq 1 ] && [ "$1" = "${JWH_TEST_FLOCK_FD:-}" ]; then
  printf '%s\n' "$$" > "$JWH_TEST_FLOCK_MARKER"
fi
exec "$JWH_REAL_FLOCK" "$@"
FAKE
cat > "$TMP/fakebin/python3" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ -n "${JWH_TEST_CLEANUP_HELPER_PID:-}" ] \
  && [ "${1:-}" = "bin/process_group.py" ] \
  && [ "${2:-}" = "terminate-descendants-strict" ] \
  && [ ! -e "$JWH_TEST_CLEANUP_ONCE" ]; then
  : > "$JWH_TEST_CLEANUP_ONCE"
  printf '%s\n' "$$" > "$JWH_TEST_CLEANUP_HELPER_PID"
  while true; do "$JWH_REAL_SLEEP" 1; done
fi
exec "$JWH_REAL_PYTHON" "$@"
FAKE
chmod +x "$TMP/fakebin/sleep" "$TMP/fakebin/flock" "$TMP/fakebin/python3"
export PATH="$TMP/fakebin:$PATH"
export FAKE_CODEX_TRACE="$TRACE"
cd "$TMP"

wait_lock_free() {
  path="$1"
  for _ in $(seq 1 120); do
    if flock -n "$path" true >/dev/null 2>&1; then return 0; fi
    sleep 0.05
  done
  return 1
}

wait_trace() {
  pattern="$1"
  for _ in $(seq 1 240); do
    grep -q "$pattern" "$TRACE" 2>/dev/null && return 0
    sleep 0.05
  done
  return 1
}

# 1) Runner lock: stale PID text must not matter. Eight simultaneous starts against
# one unlocked inode must yield exactly one Codex owner because flock is authoritative.
rm -rf .harness; mkdir -p .harness/logs
printf '%s\n' 99999999 > .harness/codex-runner.lock
release_file="$TMP/release-runner"
rm -f "$release_file" status.runner.*
pids=()
for i in $(seq 1 8); do
  (
    set +e
    FAKE_CODEX_RELEASE_FILE="$release_file" bash bin/run-codex.sh >/dev/null 2>&1
    printf '%s\n' "$?" > "status.runner.$i"
  ) &
  pids+=("$!")
done
for _ in $(seq 1 100); do
  done_count=$(find . -maxdepth 1 -name 'status.runner.*' | wc -l | tr -d ' ')
  [ "$done_count" -ge 7 ] && break
  sleep 0.05
done
[ "$(find . -maxdepth 1 -name 'status.runner.*' | wc -l | tr -d ' ')" -eq 7 ] || { echo "runner lock did not reject seven contenders" >&2; exit 1; }
touch "$release_file"
for p in "${pids[@]}"; do wait "$p"; done
zeros=$(grep -l '^0$' status.runner.* | wc -l | tr -d ' ')
twos=$(grep -l '^2$' status.runner.* | wc -l | tr -d ' ')
[ "$zeros" -eq 1 ] && [ "$twos" -eq 7 ] || { echo "runner lock results: success=$zeros rejected=$twos" >&2; exit 1; }

# 2) Supervisor single-instance lock is kernel-backed even with stale metadata.
rm -rf .harness; mkdir -p .harness/logs; touch .harness/codex-supervisor.pause
printf '%s\n' 99999999 > .harness/codex-supervisor.lock
MILESTONE_ID=TEST_INCOMPLETE CHECK_S=0.1 bash bin/supervise-codex.sh >/dev/null 2>&1 &
sup_owner=$!
for _ in $(seq 1 100); do
  p=$(cat .harness/codex-supervisor.lock 2>/dev/null || true)
  [ -n "$p" ] && [ "$p" != "99999999" ] && break
  sleep 0.05
done
set +e
MILESTONE_ID=TEST_INCOMPLETE CHECK_S=0.1 bash bin/supervise-codex.sh >/dev/null 2>&1
second_sup_rc=$?
set -e
[ "$second_sup_rc" -eq 2 ] || { echo "second supervisor was not rejected" >&2; exit 1; }
kill "$sup_owner" 2>/dev/null || true
wait "$sup_owner" 2>/dev/null || true
rm -f .harness/codex-supervisor.pause

# 2b) Supervisor lease isolation: killing a supervisor must release its kernel
# lease even while the already-launched runner remains alive.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
supervised_release="$TMP/release-supervised-runner"
rm -f "$supervised_release"
MILESTONE_ID=TEST_INCOMPLETE CHECK_S=0.1 BACKOFF_MIN=0 FAST_DEATH_S=999 \
  FAKE_CODEX_RELEASE_FILE="$supervised_release" bash bin/supervise-codex.sh > supervisor-lease.out 2>&1 &
lease_sup=$!
for _ in $(seq 1 100); do
  runner=$(cat .harness/codex-runner.lock 2>/dev/null || true)
  [ -n "$runner" ] && [ -s .harness/codex-wrapper.ready ] && break
  sleep 0.05
done
[ -n "${runner:-}" ] && kill -0 "$runner" 2>/dev/null || { echo "supervisor lease fixture runner did not start" >&2; exit 1; }
touch .harness/codex-supervisor.pause
kill -9 "$lease_sup"
wait "$lease_sup" 2>/dev/null || true
kill -0 "$runner" 2>/dev/null || { echo "supervised runner did not survive supervisor SIGKILL" >&2; exit 1; }
flock -n .harness/codex-supervisor.lock true >/dev/null 2>&1 || { echo "runner inherited the dead supervisor's kernel lease" >&2; exit 1; }
MILESTONE_ID=TEST_INCOMPLETE CHECK_S=0.1 bash bin/supervise-codex.sh >/dev/null 2>&1 & replacement_sup=$!
for _ in $(seq 1 100); do
  [ "$(cat .harness/codex-supervisor.lock 2>/dev/null || true)" = "$replacement_sup" ] && break
  sleep 0.05
done
[ "$(cat .harness/codex-supervisor.lock 2>/dev/null || true)" = "$replacement_sup" ] || { echo "replacement supervisor could not acquire its lease" >&2; exit 1; }
kill "$replacement_sup" 2>/dev/null || true
wait "$replacement_sup" 2>/dev/null || true
touch "$supervised_release"
wait_lock_free .harness/codex-runner.lock || { echo "supervisor lease fixture runner did not stop" >&2; exit 1; }
rm -f .harness/codex-supervisor.pause

# The same private lease must not leak into a backoff sleep after a short run.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
JWH_TEST_SLEEP_MARKER=.harness/backoff-sleep.pid JWH_TEST_SLEEP_ARG=30 \
  MILESTONE_ID=TEST_INCOMPLETE CHECK_S=0.1 BACKOFF_MIN=30 BACKOFF_MAX=30 FAST_DEATH_S=999 \
  bash bin/supervise-codex.sh > supervisor-backoff.out 2>&1 &
backoff_sup=$!
for _ in $(seq 1 200); do
  [ -s .harness/backoff-sleep.pid ] && break
  sleep 0.05
done
[ -s .harness/backoff-sleep.pid ] || { echo "supervisor never entered its backoff child" >&2; exit 1; }
backoff_sleep=$(cat .harness/backoff-sleep.pid)
kill -0 "$backoff_sleep" 2>/dev/null || { echo "backoff sleep exited before supervisor kill" >&2; exit 1; }
touch .harness/codex-supervisor.pause
kill -9 "$backoff_sup"
wait "$backoff_sup" 2>/dev/null || true
kill -0 "$backoff_sleep" 2>/dev/null || { echo "backoff sleep did not survive supervisor SIGKILL" >&2; exit 1; }
flock -n .harness/codex-supervisor.lock true >/dev/null 2>&1 || { echo "backoff child inherited the dead supervisor's kernel lease" >&2; exit 1; }
kill "$backoff_sleep" 2>/dev/null || true
MILESTONE_ID=TEST_INCOMPLETE CHECK_S=0.1 bash bin/supervise-codex.sh >/dev/null 2>&1 & replacement_sup=$!
for _ in $(seq 1 100); do
  [ "$(cat .harness/codex-supervisor.lock 2>/dev/null || true)" = "$replacement_sup" ] && break
  sleep 0.05
done
[ "$(cat .harness/codex-supervisor.lock 2>/dev/null || true)" = "$replacement_sup" ] || { echo "replacement supervisor could not start during orphaned backoff" >&2; exit 1; }
kill "$replacement_sup" 2>/dev/null || true
wait "$replacement_sup" 2>/dev/null || true
rm -f .harness/codex-supervisor.pause

# Gate evaluation closes the private supervisor lease but retains the control and
# runner handoff leases, preserving serialization after hard or graceful death.
assert_gate_lease_boundary() {
  signal=$1
  rm -rf .harness; mkdir -p .harness/logs
  MILESTONE_ID=TEST_GATE_RACE CHECK_S=0.1 bash bin/supervise-codex.sh > "supervisor-gate-${signal}.out" 2>&1 &
  gate_lease_sup=$!
  for _ in $(seq 1 100); do [ -e .harness/test-gate-entered ] && break; sleep 0.05; done
  [ -e .harness/test-gate-entered ] || { echo "supervisor $signal fixture gate did not start" >&2; exit 1; }
  gate_child=$(cat .harness/test-gate-child.pid)
  gate_parent=$(cat .harness/test-gate-parent.pid)
  kill "-$signal" "$gate_lease_sup"
  wait "$gate_lease_sup" 2>/dev/null || true
  kill -0 "$gate_child" 2>/dev/null || { echo "gate child did not survive supervisor $signal" >&2; exit 1; }
  kill -0 "$gate_parent" 2>/dev/null || { echo "gate evaluator did not survive supervisor $signal" >&2; exit 1; }
  flock -n .harness/codex-supervisor.lock true >/dev/null 2>&1 || { echo "gate evaluator inherited the private supervisor lease after $signal" >&2; exit 1; }
  for lock in codex-control.lock codex-runner.lock; do
    if flock -n ".harness/$lock" true >/dev/null 2>&1; then
      echo "live gate evaluator released $lock after supervisor $signal" >&2
      exit 1
    fi
  done
  touch .harness/test-gate-release
  wait_lock_free .harness/codex-control.lock || { echo "completed $signal gate did not release the control lock" >&2; exit 1; }
  wait_lock_free .harness/codex-runner.lock || { echo "completed $signal gate did not release the runner lock" >&2; exit 1; }
}
assert_gate_lease_boundary KILL
assert_gate_lease_boundary TERM

# Killing the evaluator itself must not release serialization even when an active
# check created a detached child with close_fds=True. The supervisor remains the
# outer subreaper, adopts and kills the whole tree, then releases both leases.
rm -rf .harness; mkdir -p .harness/logs
MILESTONE_ID=TEST_GATE_EVALUATOR_DEATH CHECK_S=0.1 bash bin/supervise-codex.sh > evaluator-lease.out 2>&1 &
evaluator_lease_sup=$!
for _ in $(seq 1 100); do [ -e .harness/test-gate-entered ] && break; sleep 0.05; done
[ -e .harness/test-gate-entered ] || { echo "evaluator lease fixture gate did not start" >&2; exit 1; }
gate_child=$(cat .harness/test-gate-child.pid)
gate_parent=$(cat .harness/test-gate-parent.pid)
detached_child=$(cat .harness/evaluator-detached.pid)
kill -9 "$gate_parent"
sleep 0.1
for lock in codex-control.lock codex-runner.lock; do
  if flock -n ".harness/$lock" true >/dev/null 2>&1; then
    echo "supervisor released $lock before evaluator-orphan cleanup" >&2
    exit 1
  fi
done
set +e
wait "$evaluator_lease_sup"
evaluator_sup_rc=$?
set -e
[ "$evaluator_sup_rc" -eq 2 ] || { echo "evaluator-death supervisor returned $evaluator_sup_rc instead of 2" >&2; cat evaluator-lease.out >&2; exit 1; }
state=$(awk '{print $3}' "/proc/$detached_child/stat" 2>/dev/null || true)
{ [ -z "$state" ] || [ "$state" = "Z" ]; } || { echo "close-fds child survived evaluator fallback cleanup" >&2; exit 1; }
sleep 5
[ ! -e .harness/LEAKED_EVALUATOR_CHILD ] || { echo "close-fds child mutated worktree after evaluator death" >&2; exit 1; }
wait_lock_free .harness/codex-control.lock || { echo "evaluator fallback did not release the control lock" >&2; exit 1; }
wait_lock_free .harness/codex-runner.lock || { echo "evaluator fallback did not release the runner lock" >&2; exit 1; }
grep -q 'gate evaluator exited while executable descendants remained' evaluator-lease.out || { echo "evaluator fallback was not reported fail-closed" >&2; cat evaluator-lease.out >&2; exit 1; }

# Supervisor and evaluator double death must still leave a living gate owner that
# holds both leases until the close-fds detached child has been adopted and killed.
rm -rf .harness; mkdir -p .harness/logs
MILESTONE_ID=TEST_GATE_EVALUATOR_DEATH CHECK_S=0.1 bash bin/supervise-codex.sh > double-death.out 2>&1 &
double_death_sup=$!
for _ in $(seq 1 100); do [ -e .harness/test-gate-entered ] && [ -s .harness/GATE-ACTIVE ] && [ -s .harness/gate-watchdog-child.pid ] && break; sleep 0.05; done
[ -e .harness/test-gate-entered ] || { echo "double-death fixture gate did not start" >&2; exit 1; }
gate_parent=$(cat .harness/test-gate-parent.pid)
detached_child=$(cat .harness/evaluator-detached.pid)
gate_watchdog=$(cat .harness/GATE-ACTIVE)
gate_guard=$(cat .harness/gate-watchdog-child.pid)
kill -9 "$double_death_sup"
wait "$double_death_sup" 2>/dev/null || true
kill -0 "$gate_watchdog" 2>/dev/null || { echo "gate watchdog did not survive supervisor SIGKILL" >&2; exit 1; }
kill -STOP "$gate_guard"
kill -9 "$gate_parent"
kill -9 "$gate_guard"
sleep 0.1
for lock in codex-control.lock codex-runner.lock; do
  if flock -n ".harness/$lock" true >/dev/null 2>&1; then
    echo "gate watchdog released $lock before double-death cleanup" >&2
    exit 1
  fi
done
wait_lock_free .harness/codex-control.lock || { echo "double-death cleanup did not release the control lock" >&2; exit 1; }
wait_lock_free .harness/codex-runner.lock || { echo "double-death cleanup did not release the runner lock" >&2; exit 1; }
[ ! -e .harness/GATE-ACTIVE ] || { echo "verified double-death cleanup left the active marker" >&2; exit 1; }
state=$(awk '{print $3}' "/proc/$detached_child/stat" 2>/dev/null || true)
{ [ -z "$state" ] || [ "$state" = "Z" ]; } || { echo "double-death cleanup left detached work executable" >&2; exit 1; }
sleep 5
[ ! -e .harness/LEAKED_EVALUATOR_CHILD ] || { echo "double-death child mutated the worktree" >&2; exit 1; }

# If strict cleanup itself is killed, the watchdog must quarantine execution and
# retry while retaining both leases. This remains safe even if the supervisor then dies.
rm -rf .harness; mkdir -p .harness/logs
JWH_TEST_CLEANUP_HELPER_PID=.harness/test-cleanup-helper.pid \
  JWH_TEST_CLEANUP_ONCE=.harness/test-cleanup-once \
  MILESTONE_ID=TEST_GATE_EVALUATOR_DEATH CHECK_S=0.1 \
  bash bin/supervise-codex.sh > cleanup-retry.out 2>&1 &
cleanup_retry_sup=$!
for _ in $(seq 1 100); do [ -e .harness/test-gate-entered ] && [ -s .harness/GATE-ACTIVE ] && [ -s .harness/gate-watchdog-child.pid ] && break; sleep 0.05; done
[ -e .harness/test-gate-entered ] || { echo "cleanup-retry fixture gate did not start" >&2; exit 1; }
gate_parent=$(cat .harness/test-gate-parent.pid)
detached_child=$(cat .harness/evaluator-detached.pid)
gate_watchdog=$(cat .harness/GATE-ACTIVE)
gate_guard=$(cat .harness/gate-watchdog-child.pid)
kill -STOP "$gate_guard"
kill -9 "$gate_parent"
kill -9 "$gate_guard"
for _ in $(seq 1 200); do [ -s .harness/test-cleanup-helper.pid ] && break; sleep 0.01; done
[ -s .harness/test-cleanup-helper.pid ] || { echo "strict cleanup helper was not observable" >&2; exit 1; }
cleanup_helper=$(cat .harness/test-cleanup-helper.pid)
kill -9 "$cleanup_helper"
for _ in $(seq 1 100); do [ -e .harness/GATE-TIMEOUT-BLOCKED ] && break; sleep 0.02; done
[ -e .harness/GATE-TIMEOUT-BLOCKED ] || { echo "failed cleanup did not quarantine autonomous execution" >&2; exit 1; }
kill -9 "$cleanup_retry_sup"
wait "$cleanup_retry_sup" 2>/dev/null || true
for lock in codex-control.lock codex-runner.lock; do
  if flock -n ".harness/$lock" true >/dev/null 2>&1; then
    echo "cleanup retry released $lock before verification" >&2
    exit 1
  fi
done
wait_lock_free .harness/codex-control.lock || { echo "retried cleanup did not release the control lock" >&2; exit 1; }
wait_lock_free .harness/codex-runner.lock || { echo "retried cleanup did not release the runner lock" >&2; exit 1; }
[ ! -e .harness/GATE-ACTIVE ] || { echo "verified cleanup retry left the active marker" >&2; exit 1; }
[ -e .harness/STOP ] || { echo "cleanup failure did not leave STOP fail-closed" >&2; exit 1; }
state=$(awk '{print $3}' "/proc/$detached_child/stat" 2>/dev/null || true)
{ [ -z "$state" ] || [ "$state" = "Z" ]; } || { echo "cleanup retry left detached work executable" >&2; exit 1; }

# A stale active marker is the durable boundary after watchdog death. Every path
# capable of starting or replacing Codex must reject it even when both locks are free.
rm -rf .harness; mkdir -p .harness/logs
printf '%s\n' 99999999 > .harness/GATE-ACTIVE
set +e
bash bin/run-codex.sh >/dev/null 2>&1
stale_run_rc=$?
bash bin/restart-codex.sh >/dev/null 2>&1
stale_restart_rc=$?
MILESTONE_ID=TEST_INCOMPLETE CHECK_S=0.1 bash bin/supervise-codex.sh >/dev/null 2>&1
stale_supervisor_rc=$?
set -e
[ "$stale_run_rc" -eq 2 ] || { echo "runner ignored stale gate ownership" >&2; exit 1; }
[ "$stale_restart_rc" -eq 2 ] || { echo "restart ignored stale gate ownership" >&2; exit 1; }
[ "$stale_supervisor_rc" -eq 2 ] || { echo "supervisor ignored stale gate ownership" >&2; exit 1; }

# A normally exiting check must not leave a same-session background process that
# can overlap a runner through the shared lease. Terminate it and fail the gate.
rm -rf .harness; mkdir -p .harness/logs
set +e
MILESTONE_ID=TEST_GATE_BACKGROUND python3 bin/milestone-gate.py --json --no-record > background-gate.out 2> background-gate.err
background_gate_rc=$?
set -e
[ "$background_gate_rc" -eq 1 ] || { echo "background-descendant gate returned $background_gate_rc instead of 1" >&2; cat background-gate.out >&2; exit 1; }
grep -q 'check exited while descendant processes remained' background-gate.out || { echo "background-descendant gate did not report fail-closed cleanup" >&2; cat background-gate.out >&2; exit 1; }
sleep 2
[ ! -e .harness/LEAKED_BACKGROUND_CHECK ] || { echo "completed check left a worktree-mutating descendant" >&2; exit 1; }

# A completed check's detached session is terminated before the next runner starts.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
escaped_release="$TMP/release-escaped-runner"
rm -f "$escaped_release"
MILESTONE_ID=TEST_GATE_ESCAPED CHECK_S=0.1 MAX_RUNS=1 BACKOFF_MIN=0 FAST_DEATH_S=999 \
  FAKE_CODEX_RELEASE_FILE="$escaped_release" bash bin/supervise-codex.sh > escaped-gate.out 2>&1 &
escaped_gate_sup=$!
for _ in $(seq 1 100); do [ -s .harness/test-escaped-child.pid ] && break; sleep 0.05; done
[ -s .harness/test-escaped-child.pid ] || { echo "escaped-session fixture did not start" >&2; exit 1; }
escaped_child=$(cat .harness/test-escaped-child.pid)
for _ in $(seq 1 100); do
  state=$(awk '{print $3}' "/proc/$escaped_child/stat" 2>/dev/null || true)
  { [ -z "$state" ] || [ "$state" = "Z" ]; } && break
  sleep 0.05
done
{ [ -z "${state:-}" ] || [ "$state" = "Z" ]; } || { echo "gate left a detached executable child alive" >&2; exit 1; }
[ ! -e .harness/LEAKED_ESCAPED_CHECK ] || { echo "detached check modified the worktree after leader exit" >&2; exit 1; }
for _ in $(seq 1 120); do [ -s "$TRACE" ] && break; sleep 0.05; done
[ -s "$TRACE" ] || { echo "runner did not start after escaped-session lease release" >&2; exit 1; }
touch "$escaped_release"
wait "$escaped_gate_sup"

# 3) Manual restart preserves a thread that was already emitted by an interrupted first turn.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
FAKE_CODEX_SLEEP=30 bash bin/run-codex.sh >/dev/null 2>&1 &
old_runner=$!
for _ in $(seq 1 100); do
  [ -s .harness/codex-session-id ] && break
  sleep 0.05
done
[ "$(cat .harness/codex-session-id 2>/dev/null || true)" = "fixture-thread-123" ] || { echo "thread id was not persisted while streaming" >&2; exit 1; }
FAKE_CODEX_SLEEP=5 bash bin/restart-codex.sh >/dev/null
wait "$old_runner" 2>/dev/null || true
wait_trace 'resume fixture-thread-123' || { echo "manual restart did not resume persisted thread" >&2; cat "$TRACE" >&2; exit 1; }
touch .harness/STOP
replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$replacement" ] && kill "$replacement" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || { echo "replacement runner did not release kernel lock" >&2; exit 1; }
rm -f .harness/STOP

# 4) Health uses the actual runner lock, and detects stale event progress while it is held.
rm -rf .harness; mkdir -p .harness/logs
(
  exec 8<>.harness/codex-runner.lock
  flock 8
  : > .harness/codex-runner.lock
  printf '%s\n' "$BASHPID" > .harness/codex-runner.lock
  sleep 30
) & hp1=$!
sleep 30 & hp2=$!
printf '%s\n' "$hp2" > .harness/codex-child.pid
for _ in $(seq 1 50); do
  set +e; flock -n .harness/codex-runner.lock true >/dev/null 2>&1; probe=$?; set -e
  [ "$probe" -ne 0 ] && break
  sleep 0.05
done
printf '%s\n' '{"type":"thread.started","thread_id":"stale"}' > .harness/logs/codex-stale.jsonl
python3 - <<'PY'
import os, time
p='.harness/logs/codex-stale.jsonl'; t=time.time()-180; os.utime(p,(t,t))
PY
set +e
MAX_EVENT_AGE_MIN=1 MAX_PROGRESS_AGE_MIN=999 bash bin/health-codex.sh > health.out 2>&1
health_rc=$?
set -e
kill "$hp1" "$hp2" 2>/dev/null || true
wait "$hp1" "$hp2" 2>/dev/null || true
[ "$health_rc" -eq 1 ] || { echo "stale health unexpectedly passed" >&2; cat health.out >&2; exit 1; }
grep -q 'event log is' health.out || { echo "stale-event health reason missing" >&2; cat health.out >&2; exit 1; }

# 5) Gate configuration errors: missing executables must return rc=2, never rc=1/incomplete.
set +e
MILESTONE_ID=TEST_BAD python3 bin/milestone-gate.py --no-record >/dev/null 2> gate.err
bad_gate_rc=$?
set -e
[ "$bad_gate_rc" -eq 2 ] || { echo "bad gate returned $bad_gate_rc instead of 2" >&2; cat gate.err >&2; exit 1; }

# 6) Stale PID safety: a free runner lock containing an unrelated live PID must never
# cause restart to signal that process.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
sleep 30 & innocent=$!
printf '%s\n' "$innocent" > .harness/codex-runner.lock
FAKE_CODEX_SLEEP=5 bash bin/restart-codex.sh >/dev/null
kill -0 "$innocent" 2>/dev/null || { echo "restart killed process referenced only by stale PID text" >&2; exit 1; }
touch .harness/STOP
replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$replacement" ] && kill "$replacement" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || { echo "stale-PID test replacement did not stop" >&2; exit 1; }
kill "$innocent" 2>/dev/null || true
wait "$innocent" 2>/dev/null || true
rm -f .harness/STOP

# 7) Gate serialization: a manual runner started during milestone evaluation cannot
# acquire runner ownership or invoke Codex until the gate's critical section ends.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
MILESTONE_ID=TEST_GATE_RACE CHECK_S=0.1 MAX_RUNS=1 BACKOFF_MIN=0 FAST_DEATH_S=999 bash bin/supervise-codex.sh > gate-race.out 2>&1 &
gate_sup=$!
for _ in $(seq 1 100); do [ -e .harness/test-gate-entered ] && break; sleep 0.05; done
[ -e .harness/test-gate-entered ] || { echo "gate race fixture never entered" >&2; exit 1; }
manual_release="$TMP/manual-release"; rm -f "$manual_release"
FAKE_CODEX_RELEASE_FILE="$manual_release" bash bin/run-codex.sh >/dev/null 2>&1 & manual_runner=$!
sleep 0.3
[ ! -s "$TRACE" ] || { echo "manual Codex started while milestone gate was evaluating" >&2; exit 1; }
touch .harness/STOP .harness/test-gate-release
wait "$gate_sup"
for _ in $(seq 1 100); do [ -s "$TRACE" ] && break; sleep 0.05; done
[ -s "$TRACE" ] || { echo "manual runner never started after gate released control" >&2; exit 1; }
touch "$manual_release"
wait "$manual_runner"
rm -f .harness/STOP

# 8) Restart handoff: beginning a restart while the supervisor is inside a gate must
# preserve the requested note, including when the replacement must start a fresh thread.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
printf 'UNIQUE_RESTART_NOTE\n' > restart-note.txt
MILESTONE_ID=TEST_GATE_RACE CHECK_S=0.1 BACKOFF_MIN=0 FAST_DEATH_S=999 bash bin/supervise-codex.sh > restart-race.out 2>&1 &
race_sup=$!
for _ in $(seq 1 100); do [ -e .harness/test-gate-entered ] && break; sleep 0.05; done
[ -e .harness/test-gate-entered ] || { echo "restart race fixture never entered" >&2; exit 1; }
JWH_TEST_FLOCK_MARKER=.harness/restart-control-wait.pid JWH_TEST_FLOCK_FD=9 FAKE_CODEX_SLEEP=30 \
  bash bin/restart-codex.sh restart-note.txt > restart-race-restart.out 2>&1 & restart_job=$!
for _ in $(seq 1 100); do [ -s .harness/restart-control-wait.pid ] && break; sleep 0.05; done
[ -s .harness/restart-control-wait.pid ] || { echo "restart did not block on the gate-held control lock" >&2; exit 1; }
restart_waiter=$(cat .harness/restart-control-wait.pid)
kill -0 "$restart_waiter" 2>/dev/null || { echo "restart control-lock waiter exited before gate release" >&2; exit 1; }
# The confirmed control-lock waiter cannot launch Codex until the gate releases.
touch .harness/test-gate-release
if ! wait "$restart_job"; then
  echo "restart handoff failed before replacement readiness" >&2
  cat restart-race-restart.out >&2
  find .harness/logs -maxdepth 1 -name 'manual-restart-*.out' -type f -exec sh -c 'echo "--- $1" >&2; cat "$1" >&2' _ {} \;
  exit 1
fi
wait_trace 'UNIQUE_RESTART_NOTE' || { echo "restart handoff lost RESUME_NOTE_FILE" >&2; cat restart-race-restart.out >&2; exit 1; }
touch .harness/STOP
current=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$current" ] && kill "$current" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || true
wait "$race_sup" 2>/dev/null || true

# 9) Wrapper escalation: if the command leader exits on TERM and a TERM-ignoring
# descendant redirected both streams, preserve the full grace window and kill the
# original process group before returning.
rm -rf .harness; mkdir -p .harness/logs
redirect_marker="$TMP/redirected-descendant-survived"
python3 bin/codex-process.py \
  --log .harness/logs/redirected.jsonl \
  --errlog .harness/logs/redirected.stderr.log \
  --session-file .harness/redirected-session \
  --child-pid-file .harness/codex-child.pid \
  --wrapper-pid-file .harness/codex-wrapper.pid \
  --wrapper-lock-file .harness/codex-wrapper.lock \
  --ready-file .harness/codex-wrapper.ready \
  --stop-file .harness/codex-wrapper.stop \
  -- codex-redirected "$redirect_marker" >/dev/null 2>&1 &
redirect_wrapper=$!
for _ in $(seq 1 100); do
  [ -s .harness/codex-child.pid ] && [ -s .harness/codex-wrapper.pid ] && break
  sleep 0.05
done
[ -s .harness/codex-child.pid ] && [ -s .harness/codex-wrapper.pid ] || { echo "redirected fixture did not start" >&2; exit 1; }
start_ns=$(python3 -c 'import time; print(time.monotonic_ns())')
kill -TERM "$redirect_wrapper"
set +e
wait "$redirect_wrapper"
redirect_rc=$?
set -e
end_ns=$(python3 -c 'import time; print(time.monotonic_ns())')
elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
[ "$redirect_rc" -eq 143 ] || { echo "redirected wrapper returned $redirect_rc instead of 143" >&2; exit 1; }
[ "$elapsed_ms" -ge 1800 ] && [ "$elapsed_ms" -lt 8000 ] || { echo "redirected wrapper grace was ${elapsed_ms}ms" >&2; exit 1; }
sleep 3
[ ! -e "$redirect_marker" ] || { echo "redirected descendant survived process-group escalation" >&2; exit 1; }
[ ! -e .harness/codex-child.pid ] && [ ! -e .harness/codex-wrapper.pid ] && [ ! -e .harness/codex-wrapper.ready ] || { echo "wrapper readiness metadata was not cleaned" >&2; exit 1; }
flock -n .harness/codex-wrapper.lock true >/dev/null 2>&1 || { echo "wrapper kernel lock was not released" >&2; exit 1; }

# 10) Orphan recovery: SIGKILL can remove the runner shell while its Python wrapper
# retains fd 8. Restart must use the wrapper's own stop control before replacing it.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
FAKE_CODEX_SLEEP=30 bash bin/run-codex.sh >/dev/null 2>&1 &
orphan_shell=$!
for _ in $(seq 1 100); do
  wrapper=$(cat .harness/codex-wrapper.pid 2>/dev/null || true)
  [ -n "$wrapper" ] && [ "$(cat .harness/codex-wrapper.ready 2>/dev/null || true)" = "$wrapper" ] && break
  sleep 0.05
done
orphan_wrapper=$(cat .harness/codex-wrapper.pid 2>/dev/null || true)
[ -n "$orphan_wrapper" ] && [ "$(cat .harness/codex-wrapper.ready 2>/dev/null || true)" = "$orphan_wrapper" ] && kill -0 "$orphan_wrapper" 2>/dev/null || { echo "orphan fixture wrapper did not become ready" >&2; exit 1; }
set +e
flock -n .harness/codex-runner.lock true >/dev/null 2>&1
orphan_lock_probe=$?
set -e
[ "$orphan_lock_probe" -ne 0 ] || { echo "orphan fixture never acquired runner lock" >&2; exit 1; }
kill -9 "$orphan_shell"
wait "$orphan_shell" 2>/dev/null || true
kill -0 "$orphan_wrapper" 2>/dev/null || { echo "wrapper did not retain the inherited runner lock" >&2; exit 1; }
FAKE_CODEX_SLEEP=5 bash bin/restart-codex.sh > orphan-restart.out 2>&1 || { cat orphan-restart.out >&2; exit 1; }
replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$replacement" ] && kill -0 "$replacement" 2>/dev/null || { echo "orphan recovery replacement did not start" >&2; exit 1; }
replacement_wrapper=$(cat .harness/codex-wrapper.pid 2>/dev/null || true)
[ -n "$replacement_wrapper" ] && [ "$replacement_wrapper" != "$orphan_wrapper" ] && [ "$(cat .harness/codex-wrapper.ready 2>/dev/null || true)" = "$replacement_wrapper" ] && kill -0 "$replacement_wrapper" 2>/dev/null || { echo "restart returned before a distinct wrapper was ready" >&2; exit 1; }
set +e
flock -n .harness/codex-runner.lock true >/dev/null 2>&1
replacement_lock_probe=$?
set -e
[ "$replacement_lock_probe" -ne 0 ] || { echo "orphan recovery replacement does not own the runner lock" >&2; exit 1; }
first_replacement="$replacement"
first_replacement_wrapper="$replacement_wrapper"
FAKE_CODEX_SLEEP=5 bash bin/restart-codex.sh > repeated-restart.out 2>&1 || { cat repeated-restart.out >&2; exit 1; }
replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
replacement_wrapper=$(cat .harness/codex-wrapper.pid 2>/dev/null || true)
[ -n "$replacement" ] && [ "$replacement" != "$first_replacement" ] && kill -0 "$replacement" 2>/dev/null || { echo "immediate second restart did not replace the shell" >&2; exit 1; }
[ -n "$replacement_wrapper" ] && [ "$replacement_wrapper" != "$first_replacement_wrapper" ] && [ "$(cat .harness/codex-wrapper.ready 2>/dev/null || true)" = "$replacement_wrapper" ] && kill -0 "$replacement_wrapper" 2>/dev/null || { echo "immediate second restart did not install a ready wrapper" >&2; exit 1; }
touch .harness/STOP
kill "$replacement" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || { echo "orphan recovery replacement did not stop" >&2; exit 1; }
rm -f .harness/STOP

# 11) Zombie startup shell: a terminated pre-wrapper shell can remain signalable
# while its runner lock is already free. Restart must proceed from flock evidence.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
python3 - .harness/codex-runner.lock .harness/zombie-child.pid .harness/reap-zombie <<'PY' &
import fcntl, os, signal, sys, time
from pathlib import Path
lock_path, child_path, reap_path = map(Path, sys.argv[1:])
child = os.fork()
if child == 0:
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    while True:
        signal.pause()
else:
    child_path.write_text(f"{child}\n", encoding="utf-8")
    while not reap_path.exists():
        time.sleep(0.05)
    os.waitpid(child, 0)
PY
zombie_parent=$!
printf '%s\n' "$zombie_parent" > .harness/zombie-parent.pid
for _ in $(seq 1 100); do
  zombie_child=$(cat .harness/zombie-child.pid 2>/dev/null || true)
  set +e; flock -n .harness/codex-runner.lock true >/dev/null 2>&1; zombie_probe=$?; set -e
  [ -n "$zombie_child" ] && [ "$zombie_probe" -ne 0 ] && break
  sleep 0.05
done
[ -n "${zombie_child:-}" ] && [ "$zombie_probe" -ne 0 ] || { echo "zombie startup fixture did not acquire runner lock" >&2; exit 1; }
FAKE_CODEX_SLEEP=5 bash bin/restart-codex.sh > zombie-restart.out 2>&1 || { cat zombie-restart.out >&2; exit 1; }
kill -0 "$zombie_child" 2>/dev/null || { echo "startup fixture was reaped before zombie behavior could be checked" >&2; exit 1; }
replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
wrapper=$(cat .harness/codex-wrapper.pid 2>/dev/null || true)
[ -n "$replacement" ] && [ -n "$wrapper" ] && [ "$(cat .harness/codex-wrapper.ready 2>/dev/null || true)" = "$wrapper" ] || { echo "zombie-safe restart did not return a ready replacement" >&2; exit 1; }
kill "$replacement" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || { echo "zombie-safe replacement did not stop" >&2; exit 1; }
touch .harness/reap-zombie
wait "$zombie_parent"

# 12) Wrapper hard death: if the Python owner is SIGKILLed, shell fallback cleanup
# must kill the entire Codex session even after its leader exits on TERM.
rm -rf .harness; mkdir -p .harness/logs
escape_marker="$TMP/wrapper-kill-descendant-survived"
FAKE_CODEX_ESCAPE_MARKER="$escape_marker" bash bin/run-codex.sh > wrapper-death.out 2>&1 &
escaped_runner=$!
for _ in $(seq 1 100); do
  escaped_wrapper=$(cat .harness/codex-wrapper.ready 2>/dev/null || true)
  [ -n "$escaped_wrapper" ] && [ -s .harness/codex-child.pid ] && break
  sleep 0.05
done
[ -n "${escaped_wrapper:-}" ] || { echo "wrapper-kill fixture did not become ready" >&2; exit 1; }
kill -9 "$escaped_wrapper"
wait "$escaped_runner" 2>/dev/null || true
sleep 4
[ ! -e "$escape_marker" ] || { echo "wrapper SIGKILL left a Codex descendant alive" >&2; cat wrapper-death.out >&2; exit 1; }
wait_lock_free .harness/codex-runner.lock || { echo "wrapper-kill runner lock was not released" >&2; exit 1; }

# 13) Restart coordinator lock: concurrent restart requests yield exactly one
# replacement and one deterministic rejection.
rm -rf .harness; mkdir -p .harness/logs
FAKE_CODEX_SLEEP=30 bash bin/run-codex.sh >/dev/null 2>&1 & concurrent_runner=$!
for _ in $(seq 1 100); do [ -s .harness/codex-wrapper.ready ] && break; sleep 0.05; done
restart_jobs=()
for i in 1 2; do
  (
    set +e
    FAKE_CODEX_SLEEP=30 bash bin/restart-codex.sh > "concurrent-restart.$i.out" 2>&1
    printf '%s\n' "$?" > "concurrent-restart.$i.status"
  ) &
  restart_jobs+=("$!")
done
for p in "${restart_jobs[@]}"; do wait "$p"; done
restart_zeros=$(awk '$0 == "0" { count++ } END { print count + 0 }' concurrent-restart.*.status)
restart_twos=$(awk '$0 == "2" { count++ } END { print count + 0 }' concurrent-restart.*.status)
if [ "$restart_zeros" -ne 1 ] || [ "$restart_twos" -ne 1 ]; then
  echo "concurrent restart results: success=$restart_zeros rejected=$restart_twos" >&2
  for i in 1 2; do
    echo "--- restart $i status=$(cat "concurrent-restart.$i.status")" >&2
    cat "concurrent-restart.$i.out" >&2
  done
  find .harness/logs -maxdepth 1 -name 'manual-restart-*.out' -type f -exec sh -c 'echo "--- $1" >&2; cat "$1" >&2' _ {} \;
  exit 1
fi
wait "$concurrent_runner" 2>/dev/null || true
replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$replacement" ] && kill "$replacement" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || { echo "concurrent restart replacement did not stop" >&2; exit 1; }

# 14) Restart's private lease must not enter a child blocked on the control lock.
rm -rf .harness; mkdir -p .harness/logs
(
  exec 9>.harness/codex-control.lock
  flock 9
  touch .harness/restart-control-held
  while [ ! -e .harness/release-restart-control ]; do sleep 0.05; done
) & control_holder=$!
for _ in $(seq 1 100); do [ -e .harness/restart-control-held ] && break; sleep 0.05; done
[ -e .harness/restart-control-held ] || { echo "restart control-lock fixture did not start" >&2; exit 1; }
JWH_TEST_FLOCK_MARKER=.harness/restart-control-wait.pid JWH_TEST_FLOCK_FD=9 \
  bash bin/restart-codex.sh > restart-control-lease.out 2>&1 & blocked_restart=$!
for _ in $(seq 1 100); do [ -s .harness/restart-control-wait.pid ] && break; sleep 0.05; done
[ -s .harness/restart-control-wait.pid ] || { echo "restart never entered its blocking control child" >&2; exit 1; }
blocked_flock=$(cat .harness/restart-control-wait.pid)
kill -0 "$blocked_flock" 2>/dev/null || { echo "restart control child exited before coordinator kill" >&2; exit 1; }
kill -9 "$blocked_restart"
wait "$blocked_restart" 2>/dev/null || true
kill -0 "$blocked_flock" 2>/dev/null || { echo "blocking control child did not survive restart SIGKILL" >&2; exit 1; }
flock -n .harness/codex-restart.lock true >/dev/null 2>&1 || { echo "control child inherited the dead restart coordinator's lease" >&2; exit 1; }
kill "$blocked_flock" 2>/dev/null || true
touch .harness/release-restart-control
wait "$control_holder"

# 15) Restart lease isolation: if the coordinator is SIGKILLed after spawning its
# replacement, that child must not retain the coordinator's private fd 6 lease.
rm -rf .harness; mkdir -p .harness/logs
mv bin/run-codex.sh bin/run-codex.real.sh
cat > bin/run-codex.sh <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ "${JWH_RUNNER_LOCK_HELD:-0}" = "1" ] && [ "${HOLD_RESTART_REPLACEMENT:-0}" = "1" ]; then
  : > .harness/codex-runner.lock
  printf '%s\n' "$$" > .harness/codex-runner.lock
  touch .harness/restart-replacement-spawned
  while [ ! -e .harness/release-restart-replacement ]; do /bin/sleep 0.05; done
  exit 0
fi
if [ "${JWH_RUNNER_LOCK_HELD:-0}" = "1" ]; then
  # Leave the intended job alive while releasing its transferred descriptions.
  # This creates a deterministic window in which an unrelated runner can become
  # ready, without racing that runner against locks the fixture still owns.
  exec 8>&-
  exec 9>&-
  touch .harness/intended-replacement-failed
  /bin/sleep 8
  exit 23
fi
exec "$(dirname "$0")/run-codex.real.sh" "$@"
FAKE
chmod +x bin/run-codex.sh
HOLD_RESTART_REPLACEMENT=1 bash bin/restart-codex.sh > restart-lease.out 2>&1 & restart_coord=$!
for _ in $(seq 1 100); do [ -e .harness/restart-replacement-spawned ] && break; sleep 0.05; done
[ -e .harness/restart-replacement-spawned ] || { echo "restart lease fixture replacement did not start" >&2; exit 1; }
lease_replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$lease_replacement" ] && kill -0 "$lease_replacement" 2>/dev/null || { echo "restart lease fixture replacement is not alive" >&2; exit 1; }
kill -9 "$restart_coord"
wait "$restart_coord" 2>/dev/null || true
kill -0 "$lease_replacement" 2>/dev/null || { echo "replacement did not survive restart coordinator SIGKILL" >&2; exit 1; }
wait_lock_free .harness/codex-restart.lock || { echo "replacement inherited the dead restart coordinator's lease" >&2; exit 1; }
kill -0 "$lease_replacement" 2>/dev/null || { echo "replacement exited before the restart lease was released" >&2; exit 1; }
touch .harness/release-restart-replacement
wait_lock_free .harness/codex-runner.lock || { echo "restart lease fixture replacement did not stop" >&2; exit 1; }
rm -f .harness/codex-supervisor.pause

# 16) Replacement identity: if the shell spawned by restart fails, a separate
# direct runner must not satisfy that restart's readiness poll.
rm -rf .harness; mkdir -p .harness/logs
set +e
FAKE_CODEX_SLEEP=30 bash bin/restart-codex.sh > identity-restart.out 2>&1 & identity_restart=$!
set -e
for _ in $(seq 1 100); do [ -e .harness/intended-replacement-failed ] && break; sleep 0.05; done
[ -e .harness/intended-replacement-failed ] || { echo "identity fixture did not fail the intended replacement" >&2; exit 1; }
wait_lock_free .harness/codex-runner.lock || { echo "identity fixture did not release transferred runner ownership" >&2; exit 1; }
kill -0 "$identity_restart" 2>/dev/null || { echo "identity coordinator exited before unrelated-runner test window" >&2; exit 1; }
FAKE_CODEX_SLEEP=30 bash bin/run-codex.sh > identity-direct.out 2>&1 & unrelated_runner=$!
set +e
wait "$identity_restart"
identity_rc=$?
set -e
[ "$identity_rc" -ne 0 ] || { echo "restart accepted an unrelated ready runner" >&2; exit 1; }
for _ in $(seq 1 240); do
  [ "$(cat .harness/codex-runner.lock 2>/dev/null || true)" = "$unrelated_runner" ] && [ -s .harness/codex-wrapper.ready ] && break
  sleep 0.05
done
[ "$(cat .harness/codex-runner.lock 2>/dev/null || true)" = "$unrelated_runner" ] || {
  echo "identity fixture direct runner did not start" >&2
  echo "--- failed restart" >&2; cat identity-restart.out >&2
  echo "--- direct runner" >&2; cat identity-direct.out >&2
  python3 - .harness/codex-runner.lock <<'PY' >&2
import os, sys
from pathlib import Path
target = os.stat(sys.argv[1])
for entry in Path('/proc').iterdir():
    if not entry.name.isdecimal():
        continue
    try:
        for fd in (entry / 'fd').iterdir():
            try:
                stat = fd.stat()
            except OSError:
                continue
            if (stat.st_dev, stat.st_ino) == (target.st_dev, target.st_ino):
                command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
                print(f'lock fd holder mount-pid={entry.name} fd={fd.name} command={command}')
    except OSError:
        continue
PY
  exit 1
}
kill "$unrelated_runner" 2>/dev/null || true
wait "$unrelated_runner" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || { echo "identity fixture runner did not stop" >&2; exit 1; }

echo "Harness control test passed: kernel locks, serialized gates, identity-bound ready handoffs, zombie-safe repeated restart, and process-group cleanup"
