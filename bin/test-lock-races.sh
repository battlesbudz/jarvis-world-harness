#!/usr/bin/env bash
# Regression tests for stale-lock recovery and pause-during-gate races.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-lock-races.XXXXXX")
cleanup() {
  set +e
  if [ -d "$TMP" ]; then
    for f in "$TMP"/.harness/codex-runner.lock "$TMP"/.harness/codex-supervisor.lock; do
      p=$(cat "$f" 2>/dev/null || true)
      [ -n "$p" ] && kill "$p" 2>/dev/null || true
    done
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

command -v flock >/dev/null || { echo "flock is required" >&2; exit 1; }
mkdir -p "$TMP/bin" "$TMP/fakebin" "$TMP/spec" "$TMP/milestones/TEST_SLOW"
for f in run-codex.sh codex-process.py process_group.py supervise-codex.sh milestone-gate.py; do
  cp "$ROOT/bin/$f" "$TMP/bin/$f"
done
chmod +x "$TMP/bin/"*
printf '# Progress\n' > "$TMP/PROGRESS.md"
printf '# Active Milestone — H0: Test\n' > "$TMP/MILESTONE.md"
printf '# Acceptance Tests\n' > "$TMP/ACCEPTANCE-TESTS.md"
printf '# Laws\n' > "$TMP/spec/CORE-LAWS.md"
cat > "$TMP/milestones/TEST_SLOW/gate.json" <<'JSON'
{"milestone":"TEST_SLOW","checks":[{"id":"slow-fail","command":["bash","-c","touch .harness/gate-entered; while [ ! -e .harness/gate-release ]; do sleep 0.05; done; exit 1"],"timeout_seconds":10}]}
JSON
TRACE="$TMP/fake-codex.args"
cat > "$TMP/fakebin/codex" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then echo "codex-cli fake"; exit 0; fi
printf 'CALL\n' >> "$FAKE_CODEX_TRACE"
printf '%s\n' '{"type":"thread.started","thread_id":"fixture-thread"}'
while [ ! -e "$FAKE_CODEX_RELEASE_FILE" ]; do sleep 0.05; done
printf '%s\n' '{"type":"turn.completed"}'
FAKE
chmod +x "$TMP/fakebin/codex"
export PATH="$TMP/fakebin:$PATH"
export FAKE_CODEX_TRACE="$TRACE"
cd "$TMP"

# Stale PID plus simultaneous starts: one kernel lock owner, never two Codex children.
mkdir -p .harness/logs
printf '99999999\n' > .harness/codex-runner.lock
release="$TMP/release-runner"
pids=()
for i in $(seq 1 8); do
  (
    set +e
    FAKE_CODEX_RELEASE_FILE="$release" bash bin/run-codex.sh >/dev/null 2>&1
    printf '%s\n' "$?" > "status.$i"
  ) &
  pids+=("$!")
done
for _ in $(seq 1 100); do
  [ "$(find . -maxdepth 1 -name 'status.*' | wc -l | tr -d ' ')" -ge 7 ] && break
  sleep 0.05
done
[ "$(find . -maxdepth 1 -name 'status.*' | wc -l | tr -d ' ')" -eq 7 ] || { echo "runner contenders did not settle" >&2; exit 1; }
touch "$release"
for p in "${pids[@]}"; do wait "$p"; done
[ "$(grep -l '^0$' status.* | wc -l | tr -d ' ')" -eq 1 ] || { echo "expected one runner owner" >&2; exit 1; }
[ "$(grep -l '^2$' status.* | wc -l | tr -d ' ')" -eq 7 ] || { echo "expected seven rejected runners" >&2; exit 1; }
[ "$(grep -c '^CALL$' "$TRACE")" -eq 1 ] || { echo "more than one Codex child launched" >&2; exit 1; }

# If PAUSE appears while the gate is running, the supervisor must not launch afterward.
rm -rf .harness; mkdir -p .harness/logs; : > "$TRACE"
MILESTONE_ID=TEST_SLOW CHECK_S=1 MAX_RUNS=1 BACKOFF_MIN=1 BACKOFF_MAX=1 \
  bash bin/supervise-codex.sh >/dev/null 2>&1 &
sup=$!
for _ in $(seq 1 100); do [ -e .harness/gate-entered ] && break; sleep 0.05; done
[ -e .harness/gate-entered ] || { echo "slow gate did not start" >&2; exit 1; }
touch .harness/codex-supervisor.pause .harness/gate-release
sleep 0.4
[ ! -s "$TRACE" ] || { echo "supervisor launched Codex after PAUSE" >&2; exit 1; }
rp=$(cat .harness/codex-runner.lock 2>/dev/null || true)
if [ -n "$rp" ] && kill -0 "$rp" 2>/dev/null; then
  echo "runner became live while paused" >&2
  exit 1
fi
touch .harness/STOP
wait "$sup"

echo "Lock race test passed: stale PID recovery and pause-during-gate launch are safe"
