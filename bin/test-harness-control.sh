#!/usr/bin/env bash
# Deterministic control-plane tests for kernel locks, handoffs, health, and gate errors.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-control.XXXXXX")
cleanup_all() {
  set +e
  if [ -d "$TMP" ]; then
    for f in "$TMP"/.harness/codex-runner.lock "$TMP"/.harness/codex-supervisor.lock "$TMP"/.harness/codex-restart.lock; do
      p=$(cat "$f" 2>/dev/null || true); [ -n "$p" ] && kill "$p" 2>/dev/null || true
    done
  fi
  rm -rf "$TMP"
}
trap cleanup_all EXIT

command -v flock >/dev/null || { echo "flock not found on PATH" >&2; exit 127; }
mkdir -p "$TMP/bin" "$TMP/fakebin" "$TMP/spec" \
  "$TMP/milestones/TEST_INCOMPLETE" "$TMP/milestones/TEST_BAD" \
  "$TMP/milestones/TEST_GATE_RACE"
for f in run-codex.sh codex-process.py supervise-codex.sh restart-codex.sh health-codex.sh milestone-gate.py; do
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
{"milestone":"TEST_GATE_RACE","checks":[{"id":"gate-window","command":["bash","-c","touch .harness/test-gate-entered; while [ ! -e .harness/test-gate-release ]; do sleep 0.05; done; exit 1"],"timeout_seconds":15}]}
JSON
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
elif [ -n "${FAKE_CODEX_SLEEP:-}" ]; then
  sleep "$FAKE_CODEX_SLEEP"
fi
printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
FAKE
chmod +x "$TMP/fakebin/codex"
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
FAKE_CODEX_SLEEP=30 bash bin/restart-codex.sh restart-note.txt > restart-race-restart.out 2>&1 & restart_job=$!
sleep 0.2
# Restart is blocked on the control lock while the gate is active; no Codex turn can be its replacement yet.
touch .harness/test-gate-release
wait_trace 'UNIQUE_RESTART_NOTE' || { echo "restart handoff lost RESUME_NOTE_FILE" >&2; cat restart-race-restart.out >&2; exit 1; }
wait "$restart_job"
touch .harness/STOP
current=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$current" ] && kill "$current" 2>/dev/null || true
wait_lock_free .harness/codex-runner.lock || true
wait "$race_sup" 2>/dev/null || true

echo "Harness control test passed: kernel-authoritative liveness, safe restart, serialized gates, and atomic restart handoff"
