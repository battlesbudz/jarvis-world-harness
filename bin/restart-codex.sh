#!/usr/bin/env bash
# Safely restart Codex without racing supervise-codex.sh or manual runner startup.
# Usage: ./bin/restart-codex.sh [optional-note-file]
set -euo pipefail
cd "$(dirname "$0")/.."

command -v flock >/dev/null || { echo "flock not found on PATH" >&2; exit 127; }
mkdir -p .harness/logs
PAUSE=.harness/codex-supervisor.pause
CONTROL_LOCK=.harness/codex-control.lock
RUNNER_LOCK=.harness/codex-runner.lock
RESTART_LOCK=.harness/codex-restart.lock
CHILD_PID_FILE=.harness/codex-child.pid
NOTE="${1:-}"
pause_created=0
runner_handoff=0
control_handoff=0

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

# Only one restart coordinator at a time.
exec 6<>"$RESTART_LOCK"
if ! flock -n 6; then
  owner=$(cat "$RESTART_LOCK" 2>/dev/null || true)
  echo "Another Codex restart is already active${owner:+: pid $owner}" >&2
  exit 2
fi
: > "$RESTART_LOCK"
printf '%s\n' "$$" > "$RESTART_LOCK"

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

cleanup() {
  rc=$?
  trap - EXIT
  [ "$pause_created" -eq 1 ] && rm -f "$PAUSE"
  if [ "$runner_handoff" -eq 1 ]; then
    flock -u 8 2>/dev/null || true
    exec 8>&- 2>/dev/null || true
  fi
  if [ "$control_handoff" -eq 1 ]; then
    flock -u 9 2>/dev/null || true
    exec 9>&- 2>/dev/null || true
  fi
  : > "$RESTART_LOCK" 2>/dev/null || true
  flock -u 6 2>/dev/null || true
  exec 6>&- 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT

# Take the shared control lock *before* creating PAUSE. If the supervisor is in a
# gate/launch handoff, wait until that atomic region completes, then take control.
exec 9>"$CONTROL_LOCK"
flock 9
control_handoff=1
if [ ! -e "$PAUSE" ]; then
  touch "$PAUSE"
  pause_created=1
fi
echo "Codex supervisor paused"

# Probe the kernel runner lock. Free lock => PID text is stale metadata and must
# never be signaled. Held lock => the stored PID belongs to the active runner handoff.
exec 8<>"$RUNNER_LOCK"
if flock -n 8; then
  runner_handoff=1
else
  runner_pid=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
  # Re-probe immediately before trusting the PID; the runner may have exited since
  # the first probe. If the lock became free, we now own it and do not signal anyone.
  if flock -n 8; then
    runner_handoff=1
  else
    if [ -n "$runner_pid" ] && pid_alive "$runner_pid"; then
      stop_pid "$runner_pid"
    fi
    # Wait for the authoritative kernel lock. If an unknown holder remains, fail
    # closed rather than killing a PID we cannot prove owns the runner lock.
    if ! flock -w 10 8; then
      echo "runner lock is held but no safe owner could be stopped" >&2
      exit 2
    fi
    runner_handoff=1
  fi
fi

# We own the runner lock now. Stale metadata can be cleared safely.
: > "$RUNNER_LOCK"
rm -f "$CHILD_PID_FILE"

OUT=".harness/logs/manual-restart-$(date +%Y%m%d-%H%M%S)-$$-${RANDOM:-0}.out"
if [ -n "$NOTE" ] && [ -f "$NOTE" ]; then
  nohup env JWH_CONTROL_LOCK_HELD=1 JWH_RUNNER_LOCK_HELD=1 RESUME_NOTE_FILE="$NOTE" bin/run-codex.sh > "$OUT" 2>&1 &
else
  nohup env JWH_CONTROL_LOCK_HELD=1 JWH_RUNNER_LOCK_HELD=1 bin/run-codex.sh > "$OUT" 2>&1 &
fi
replacement_shell=$!

# Transfer ownership: do not LOCK_UN; the child inherited both open descriptions.
exec 8>&- 2>/dev/null || true
runner_handoff=0
exec 9>&- 2>/dev/null || true
control_handoff=0

runner_lock_held() {
  exec 5<>"$RUNNER_LOCK"
  if flock -n 5; then
    flock -u 5 2>/dev/null || true
    exec 5>&- 2>/dev/null || true
    return 1
  fi
  exec 5>&- 2>/dev/null || true
  return 0
}

for _ in $(seq 1 40); do
  sleep 0.25
  if runner_lock_held; then
    p=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
    if [ -n "$p" ] && pid_alive "$p"; then
      echo "Codex runner up: $p"
      exit 0
    fi
  fi
  pid_alive "$replacement_shell" || true
done

echo "Codex runner did not acquire its kernel lock; inspect $OUT" >&2
exit 1
