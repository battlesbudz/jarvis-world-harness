#!/usr/bin/env bash
# Safely restart Codex without racing supervise-codex.sh.
# Usage: ./bin/restart-codex.sh [optional-note-file]
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p .harness/logs
PAUSE=.harness/codex-supervisor.pause
RUNNER_LOCK=.harness/codex-runner.lock
CHILD_PID_FILE=.harness/codex-child.pid
NOTE="${1:-}"
trap 'rm -f "$PAUSE"' EXIT

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
stop_pid() {
  p="${1:-}"
  pid_alive "$p" || return 0
  kill "$p" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    sleep 1
    pid_alive "$p" || return 0
  done
  kill -9 "$p" 2>/dev/null || true
}

touch "$PAUSE"
echo "Codex supervisor paused"

runner_pid=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
child_pid=$(cat "$CHILD_PID_FILE" 2>/dev/null || true)

# Stop the runner first; its trap is responsible for terminating the child.
stop_pid "$runner_pid"
# Belt-and-suspenders cleanup if the runner died before its trap completed.
stop_pid "$child_pid"
rm -f "$RUNNER_LOCK" "$CHILD_PID_FILE"

OUT=".harness/logs/manual-restart-$(date +%Y%m%d-%H%M%S).out"
if [ -n "$NOTE" ] && [ -f "$NOTE" ]; then
  nohup env RESUME_NOTE_FILE="$NOTE" bin/run-codex.sh > "$OUT" 2>&1 &
else
  nohup bin/run-codex.sh > "$OUT" 2>&1 &
fi

for _ in $(seq 1 20); do
  sleep 1
  p=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
  pid_alive "$p" && { echo "Codex runner up: $p"; exit 0; }
done

echo "Codex runner did not acquire its lock; inspect $OUT" >&2
exit 1
