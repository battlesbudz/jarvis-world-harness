#!/usr/bin/env bash
# Safely restart Codex without racing supervise-codex.sh.
# Usage: ./bin/restart-codex.sh [optional-note-file]
set -euo pipefail
cd "$(dirname "$0")/.."

command -v python3 >/dev/null || { echo "python3 not found on PATH" >&2; exit 127; }
mkdir -p .harness/logs
PAUSE=.harness/codex-supervisor.pause
RUNNER_LOCK=.harness/codex-runner.lock
RESTART_LOCK=.harness/codex-restart.lock
CHILD_PID_FILE=.harness/codex-child.pid
NOTE="${1:-}"
pause_created=0

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

acquire_restart_lock() {
  if python3 bin/pid-lock.py acquire "$RESTART_LOCK" "$$"; then return 0; fi
  owner=$(cat "$RESTART_LOCK" 2>/dev/null || true)
  if pid_alive "$owner"; then
    echo "Another Codex restart is already active: pid $owner" >&2
    return 2
  fi
  rm -f "$RESTART_LOCK"
  python3 bin/pid-lock.py acquire "$RESTART_LOCK" "$$" || return 2
}

stop_pid() {
  p="${1:-}"
  pid_alive "$p" || return 0
  kill "$p" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    sleep 1
    pid_alive "$p" || return 0
  done
  kill -9 "$p" 2>/dev/null || true
  for _ in 1 2 3; do
    sleep 1
    pid_alive "$p" || return 0
  done
  echo "process $p did not stop" >&2
  return 1
}

acquire_restart_lock || exit $?
cleanup() {
  rc=$?
  trap - EXIT
  [ "$pause_created" -eq 1 ] && rm -f "$PAUSE"
  python3 bin/pid-lock.py release "$RESTART_LOCK" "$$" >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT

if [ ! -e "$PAUSE" ]; then
  touch "$PAUSE"
  pause_created=1
fi
echo "Codex supervisor paused"

runner_pid=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
child_pid=$(cat "$CHILD_PID_FILE" 2>/dev/null || true)

# Stop the runner first; its signal/exit cleanup drains event streams before releasing its lock.
stop_pid "$runner_pid"
# Belt-and-suspenders cleanup if the runner died before its wrapper completed.
stop_pid "$child_pid"

owner=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
if pid_alive "$owner"; then
  echo "refusing restart: runner lock is now owned by live pid $owner" >&2
  exit 2
fi
rm -f "$RUNNER_LOCK" "$CHILD_PID_FILE"

OUT=".harness/logs/manual-restart-$(date +%Y%m%d-%H%M%S)-$$-${RANDOM:-0}.out"
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
