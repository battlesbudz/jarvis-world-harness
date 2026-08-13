#!/usr/bin/env bash
# Deterministic control-plane tests for kernel locks, restart continuity, health staleness, and gate errors.
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
mkdir -p "$TMP/bin" "$TMP/fakebin" "$TMP/spec" "$TMP/milestones/TEST_INCOMPLETE" "$TMP/milestones/TEST_BAD" "$TMP/milestones/TEST_PAUSE_RACE"
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
cat > "$TMP/milestones/TEST_PAUSE_RACE/gate.json" <<'JSON'
{"milestone":"TEST_PAUSE_RACE","checks":[{"id":"pause-window","command":["bash","-c","touch .harness/test-gate-entered; while [ ! -e .harness/test-gate-release ]; do sleep 0.05; done; exit 1"],"timeout_seconds":10}]}
JSON
TRACE="$TMP/fake-codex.args"
cat > "$TMP/fakebin/codex" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then echo "codex-cli fake-0.2"; exit 0; fi
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

# 2) Supervisor lock: stale PID text is overwritten by the one process that owns
# the kernel lock; concurrent supervisors are rejected.
rm -rf .harness; mkdir -p .harness/logs; touch .harness/codex-supervisor.pause
printf '%s\n' 99999999 > .harness/codex-supervisor.lock
rm -f status.supervisor.*
sup_pids=()
for i in $(seq 1 5); do
  (
    set +e
    MILESTONE_ID=TEST_INCOMPLETE CHECK_S=1 bash bin/supervise-codex.sh >/dev/null 2>&1
    printf '%s\n' "$?" > "status.supervisor.$i"
  ) &
  sup_pids+=("$!")
done
owner=""
for _ in $(seq 1 160); do
  owner=$(cat .harness/codex-supervisor.lock 2>/dev/null || true)
  [ -n "$owner" ] && [ "$owner" != "99999999" ] && break
  sleep 0.05
done
[ -n "$owner" ] && [ "$owner" != "99999999" ] || { echo "supervisor did not replace stale PID text under flock" >&2; exit 1; }
for _ in $(seq 1 100); do
  done_count=$(find . -maxdepth 1 -name 'status.supervisor.*' | wc -l | tr -d ' ')
  [ "$done_count" -ge 4 ] && break
  sleep 0.05
done
[ "$(grep -l '^2$' status.supervisor.* 2>/dev/null | wc -l | tr -d ' ')" -eq 4 ] || { echo "supervisor contenders were not atomically rejected" >&2; exit 1; }
kill "$owner" 2>/dev/null || true
for p in "${sup_pids[@]}"; do wait "$p" 2>/dev/null || true; done
rm -f .harness/codex-supervisor.pause

# 3) Manual restart: interrupt the first fresh turn after thread.started, then prove replacement resumes it.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
FAKE_CODEX_SLEEP=30 bash bin/run-codex.sh >/dev/null 2>&1 &
old_runner=$!
for _ in $(seq 1 100); do
  [ -s .harness/codex-session-id ] && [ -s .harness/codex-runner.lock ] && break
  sleep 0.05
done
[ "$(cat .harness/codex-session-id 2>/dev/null || true)" = "fixture-thread-123" ] || { echo "thread id was not persisted while streaming" >&2; exit 1; }
FAKE_CODEX_SLEEP=5 bash bin/restart-codex.sh >/dev/null
wait "$old_runner" 2>/dev/null || true
sleep 0.2
grep -q 'resume fixture-thread-123' "$TRACE" || { echo "manual restart did not resume persisted thread" >&2; exit 1; }
replacement=$(cat .harness/codex-runner.lock 2>/dev/null || true)
[ -n "$replacement" ] && kill "$replacement" 2>/dev/null || true
lock_free=1
for _ in $(seq 1 100); do
  set +e
  flock -n .harness/codex-runner.lock true >/dev/null 2>&1
  lock_free=$?
  set -e
  [ "$lock_free" -eq 0 ] && break
  sleep 0.05
done
[ "$lock_free" -eq 0 ] || { echo "replacement runner did not release kernel lock" >&2; exit 1; }

# 4) Health: a live runner with a stale event log must fail health.
rm -rf .harness; mkdir -p .harness/logs
sleep 30 & hp1=$!
sleep 30 & hp2=$!
printf '%s\n' "$hp1" > .harness/codex-runner.lock
printf '%s\n' "$hp2" > .harness/codex-child.pid
printf '%s\n' '{"type":"thread.started","thread_id":"stale"}' > .harness/logs/codex-stale.jsonl
python3 - <<'PY'
import os, time
p='.harness/logs/codex-stale.jsonl'
t=time.time()-180
os.utime(p,(t,t))
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

# 6) Pause race: if manual restart pauses the supervisor while milestone_passed
# is still evaluating, the supervisor must recheck PAUSE before launching Codex.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
MILESTONE_ID=TEST_PAUSE_RACE CHECK_S=0.1 MAX_RUNS=1 bash bin/supervise-codex.sh > pause-race.out 2>&1 &
pause_sup=$!
for _ in $(seq 1 100); do
  [ -e .harness/test-gate-entered ] && break
  sleep 0.05
done
[ -e .harness/test-gate-entered ] || { echo "pause-race gate never started" >&2; cat pause-race.out >&2; exit 1; }
touch .harness/codex-supervisor.pause
touch .harness/test-gate-release
sleep 0.3
[ ! -s "$TRACE" ] || { echo "supervisor launched Codex after pause appeared during gate" >&2; cat "$TRACE" >&2; exit 1; }
[ ! -s .harness/codex-runner.lock ] || { echo "runner PID appeared during paused launch window" >&2; exit 1; }
touch .harness/STOP
wait "$pause_sup"

echo "Harness control test passed: kernel locks tolerate stale PID text, restart continuity holds, stale health fails, gates fail closed, and pause-race safety holds"
