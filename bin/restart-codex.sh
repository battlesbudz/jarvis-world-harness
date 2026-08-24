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
WRAPPER_PID_FILE=.harness/codex-wrapper.pid
WRAPPER_LOCK_FILE=.harness/codex-wrapper.lock
WRAPPER_READY_FILE=.harness/codex-wrapper.ready
WRAPPER_STOP_FILE=.harness/codex-wrapper.stop
GATE_ACTIVE=.harness/GATE-ACTIVE
GATE_QUARANTINE=.harness/GATE-TIMEOUT-BLOCKED
NOTE="${1:-}"
pause_created=0
runner_handoff=0
control_handoff=0

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
job_running() { [ -n "${1:-}" ] && jobs -pr 6>&- | grep -Fxq -- "$1" 6>&-; }
read_metadata() {
  METADATA=
  IFS= read -r METADATA 2>/dev/null < "$1" || true
}

# Only one restart coordinator at a time.
exec 6<>"$RESTART_LOCK"
if ! flock -n 6; then
  read_metadata "$RESTART_LOCK"
  owner=$METADATA
  echo "Another Codex restart is already active${owner:+: pid $owner}" >&2
  exit 2
fi
: > "$RESTART_LOCK"
printf '%s\n' "$$" > "$RESTART_LOCK"

cleanup() {
  rc=$?
  trap - EXIT
  [ "$pause_created" -eq 1 ] && rm -f "$PAUSE" 6>&-
  if [ "$runner_handoff" -eq 1 ]; then
    flock -u 8 6>&- 9>&- 2>/dev/null || true
    { exec 8>&-; } 2>/dev/null || true
  fi
  if [ "$control_handoff" -eq 1 ]; then
    flock -u 9 6>&- 2>/dev/null || true
    { exec 9>&-; } 2>/dev/null || true
  fi
  : > "$RESTART_LOCK" 2>/dev/null || true
  flock -u 6 2>/dev/null || true
  { exec 6>&-; } 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT

# Take the shared control lock *before* creating PAUSE. If the supervisor is in a
# gate/launch handoff, wait until that atomic region completes, then take control.
exec 9>"$CONTROL_LOCK"
flock 9 6>&-
control_handoff=1
if [ -e "$GATE_ACTIVE" ] || [ -e "$GATE_QUARANTINE" ]; then
  echo "unverified milestone gate ownership blocks Codex restart" >&2
  exit 2
fi
if [ ! -e "$PAUSE" ]; then
  touch "$PAUSE" 6>&-
  pause_created=1
fi
echo "Codex supervisor paused"

# Probe the kernel runner lock. Free lock => PID text is stale metadata and must
# never be signaled. Held lock => the stored PID belongs to the active runner handoff.
exec 8<>"$RUNNER_LOCK"
if flock -n 8 6>&- 9>&-; then
  runner_handoff=1
else
  read_metadata "$RUNNER_LOCK"; runner_pid=$METADATA
  read_metadata "$WRAPPER_PID_FILE"; wrapper_pid=$METADATA
  # Re-probe immediately before trusting the PID; the runner may have exited since
  # the first probe. If the lock became free, we now own it and do not signal anyone.
  if flock -n 8 6>&- 9>&-; then
    runner_handoff=1
  else
    if [ -n "$wrapper_pid" ]; then
      # Ask the process that actually watches this control file to stop itself.
      # Unlike signaling PID metadata, this cannot target an unrelated recycled PID.
      touch "$WRAPPER_STOP_FILE" 6>&-
    elif pid_alive "$runner_pid"; then
      # Narrow startup fallback: the shell owns the lock but has not launched and
      # recorded its wrapper yet.
      kill -TERM "$runner_pid" 2>/dev/null || true
    fi
    # PID signalability is not exit evidence: an orphaned shell can remain a zombie
    # after releasing every file descriptor. The kernel lock is authoritative.
    if ! flock -w 5 8 6>&- 9>&-; then
      read_metadata "$WRAPPER_PID_FILE"; late_wrapper_pid=$METADATA
      if [ -n "$late_wrapper_pid" ]; then
        # The wrapper may have appeared after the initial startup-gap probe.
        touch "$WRAPPER_STOP_FILE" 6>&-
      else
        read_metadata "$RUNNER_LOCK"
      fi
      if [ -z "$late_wrapper_pid" ] && [ "$METADATA" = "$runner_pid" ] && pid_alive "$runner_pid"; then
        # Last resort for a shell stuck before it can create a controllable wrapper.
        kill -KILL "$runner_pid" 2>/dev/null || true
      fi
      if ! flock -w 5 8 6>&- 9>&-; then
        echo "runner lock is held but no safe owner could be stopped" >&2
        exit 2
      fi
    fi
    runner_handoff=1
  fi
fi

# We own the runner lock now. Stale metadata can be cleared safely.
: > "$RUNNER_LOCK"
rm -f "$CHILD_PID_FILE" "$WRAPPER_PID_FILE" "$WRAPPER_READY_FILE" "$WRAPPER_STOP_FILE" 6>&-

restart_stamp=$(
  exec 6>&-
  date +%Y%m%d-%H%M%S
)
OUT=".harness/logs/manual-restart-${restart_stamp}-$$-${RANDOM:-0}.out"
runner_env=(JWH_CONTROL_LOCK_HELD=1 JWH_RUNNER_LOCK_HELD=1)
if [ -n "$NOTE" ] && [ -f "$NOTE" ]; then
  runner_env+=(RESUME_NOTE_FILE="$NOTE")
fi
nohup env "${runner_env[@]}" bin/run-codex.sh 6>&- > "$OUT" 2>&1 &
replacement_shell=$!

# Transfer ownership: do not LOCK_UN; the child inherited both open descriptions.
{ exec 8>&-; } 2>/dev/null || true
runner_handoff=0
{ exec 9>&-; } 2>/dev/null || true
control_handoff=0

runner_lock_held() {
  exec 5<>"$RUNNER_LOCK"
  if flock -n 5 6>&-; then
    flock -u 5 6>&- 2>/dev/null || true
    { exec 5>&-; } 2>/dev/null || true
    return 1
  fi
  { exec 5>&-; } 2>/dev/null || true
  return 0
}

wrapper_lock_held() {
  exec 4<>"$WRAPPER_LOCK_FILE"
  if flock -n 4 6>&-; then
    flock -u 4 6>&- 2>/dev/null || true
    { exec 4>&-; } 2>/dev/null || true
    return 1
  fi
  { exec 4>&-; } 2>/dev/null || true
  return 0
}

for ((attempt = 0; attempt < 40; attempt++)); do
  sleep 0.25 6>&-
  if runner_lock_held; then
    read_metadata "$RUNNER_LOCK"; p=$METADATA
    read_metadata "$WRAPPER_PID_FILE"; wrapper=$METADATA
    read_metadata "$WRAPPER_READY_FILE"; ready=$METADATA
    read_metadata "$CHILD_PID_FILE"; child=$METADATA
    if [ "$p" = "$replacement_shell" ] && job_running "$replacement_shell" && wrapper_lock_held \
      && [ -n "$wrapper" ] && [ "$ready" = "$wrapper" ] \
      && pid_alive "$wrapper" && [ -n "$child" ] && pid_alive "$child"; then
      echo "Codex runner ready: shell=$p wrapper=$wrapper child=$child"
      exit 0
    fi
  fi
  job_running "$replacement_shell" || break
done

echo "Spawned Codex runner did not become ready; inspect $OUT" >&2
exit 1
