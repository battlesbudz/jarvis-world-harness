#!/usr/bin/env bash
# Keep exactly one Codex runner alive until the active milestone objectively passes,
# or the operator explicitly pauses/stops the supervisor.
set -uo pipefail
cd "$(dirname "$0")/.."

command -v python3 >/dev/null || { echo "python3 not found on PATH" >&2; exit 127; }
command -v flock >/dev/null || { echo "flock not found on PATH" >&2; exit 127; }
mkdir -p .harness/logs
SUPERVISOR_LOCK=.harness/codex-supervisor.lock
CONTROL_LOCK=.harness/codex-control.lock
RUNNER_LOCK=.harness/codex-runner.lock
PAUSE=.harness/codex-supervisor.pause
STOP=.harness/STOP
LOG=.harness/logs/codex-supervisor.log
OUT=.harness/logs/codex-supervised-runner.out
GATE_OUT=.harness/logs/milestone-gate.stdout.tmp
GATE_ERR=.harness/logs/milestone-gate.stderr.tmp
CHECK_S=${CHECK_S:-30}
FAST_DEATH_S=${FAST_DEATH_S:-180}
BACKOFF_MIN=${BACKOFF_MIN:-60}
BACKOFF_MAX=${BACKOFF_MAX:-1800}
MAX_RUNS=${MAX_RUNS:-0}
backoff=$BACKOFF_MIN
runs=0
post_delay=0
locks_held=0

say() {
  local stamp line
  stamp=$(
    exec 7>&-
    date '+%F %T'
  )
  line="$stamp  $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$LOG"
}

# Supervisor identity is protected by the kernel lock; stale PID text is metadata only.
exec 7<>"$SUPERVISOR_LOCK"
if ! flock -n 7; then
  owner=$(cat "$SUPERVISOR_LOCK" 2>/dev/null || true)
  echo "Codex supervisor already active${owner:+: pid $owner}" >&2
  exit 2
fi
: > "$SUPERVISOR_LOCK"
printf '%s\n' "$$" > "$SUPERVISOR_LOCK"

acquire_control_and_runner() {
  exec 9>"$CONTROL_LOCK"
  flock 9 7>&- || return 2
  exec 8<>"$RUNNER_LOCK"
  if ! flock -n 8 7>&- 9>&-; then
    exec 8>&- 2>/dev/null || true
    exec 9>&- 2>/dev/null || true
    locks_held=0
    return 1
  fi
  locks_held=1
  return 0
}

release_control_and_runner() {
  [ "$locks_held" -eq 1 ] || return 0
  # Close without LOCK_UN: a live gate may share these open descriptions after
  # supervisor death and must retain serialization until it finishes.
  exec 8>&- 2>/dev/null || true
  exec 9>&- 2>/dev/null || true
  locks_held=0
}

# Launch transfers the already-held runner/control file descriptions to the child.
# The child releases control immediately after writing its PID but retains runner ownership.
launch_runner_from_handoff() {
  runs=$((runs + 1))
  say "launching Codex runner (run $runs)"
  start=$SECONDS
  env JWH_CONTROL_LOCK_HELD=1 JWH_RUNNER_LOCK_HELD=1 bin/run-codex.sh 7>&- >> "$OUT" 2>&1 &
  runner_pid=$!
  # Do not LOCK_UN here: the child inherited the same open file descriptions.
  exec 8>&- 2>/dev/null || true
  exec 9>&- 2>/dev/null || true
  locks_held=0
  wait "$runner_pid"
  rc=$?
  dur=$((SECONDS - start))
  say "runner exited rc=$rc after ${dur}s"
  if [ "$rc" -ne 0 ] || [ "$dur" -lt "$FAST_DEATH_S" ]; then
    post_delay=$backoff
    backoff=$(( backoff * 2 ))
    [ "$backoff" -gt "$BACKOFF_MAX" ] && backoff=$BACKOFF_MAX
  else
    post_delay=15
    backoff=$BACKOFF_MIN
  fi
  return "$rc"
}

milestone_passed() {
  # The gate excludes private fd 7 but retains fd 8/9 so serialization survives
  # supervisor death until the evaluator itself finishes.
  python3 bin/milestone-gate.py 7>&- >"$GATE_OUT" 2>"$GATE_ERR"
  gate_rc=$?
  case "$gate_rc" in
    0)
      while IFS= read -r line; do say "gate: $line"; done < "$GATE_OUT"
      return 0
      ;;
    1) return 1 ;;
    *)
      say "milestone gate configuration/infrastructure error (rc=$gate_rc)"
      [ -s "$GATE_ERR" ] && while IFS= read -r line; do say "gate error: $line"; done < "$GATE_ERR"
      return 2
      ;;
  esac
}

cleanup() {
  rc=$?
  trap - EXIT
  release_control_and_runner
  : > "$SUPERVISOR_LOCK" 2>/dev/null || true
  flock -u 7 2>/dev/null || true
  exec 7>&- 2>/dev/null || true
  rm -f "$GATE_OUT" "$GATE_ERR"
  exit "$rc"
}
trap cleanup EXIT

say "supervisor started (pid $$)"
while true; do
  if [ -e "$STOP" ]; then
    say "STOP marker present; supervisor exiting"
    exit 0
  fi
  if [ -e "$PAUSE" ]; then
    sleep "$CHECK_S" 7>&-
    continue
  fi

  # Acquire control first, then the runner lock. This both proves whether a runner
  # actually exists and prevents manual startup/restart from racing gate evaluation.
  acquire_control_and_runner
  lock_rc=$?
  if [ "$lock_rc" -eq 1 ]; then
    backoff=$BACKOFF_MIN
    sleep "$CHECK_S" 7>&-
    continue
  elif [ "$lock_rc" -ne 0 ]; then
    say "could not acquire control/runner handoff locks"
    exit 2
  fi

  # PAUSE/STOP are rechecked while control+runner are both held, closing the
  # check-to-launch window entirely.
  if [ -e "$STOP" ]; then
    release_control_and_runner
    say "STOP marker present after handoff; supervisor exiting"
    exit 0
  fi
  if [ -e "$PAUSE" ]; then
    release_control_and_runner
    sleep "$CHECK_S" 7>&-
    continue
  fi

  milestone_passed
  gate_rc=$?
  if [ "$gate_rc" -eq 0 ]; then
    release_control_and_runner
    say "active milestone objectively passed; supervisor exiting"
    exit 0
  elif [ "$gate_rc" -eq 2 ]; then
    release_control_and_runner
    say "cannot safely continue without a valid milestone gate; supervisor exiting"
    exit 2
  fi

  # Backoff happens only after an objective gate says work remains. Release locks
  # during the delay, then loop and re-evaluate under locks before any launch.
  if [ "$post_delay" -gt 0 ]; then
    delay=$post_delay
    post_delay=0
    release_control_and_runner
    say "milestone incomplete; delaying next launch ${delay}s"
    sleep "$delay" 7>&-
    continue
  fi

  if [ "$MAX_RUNS" -gt 0 ] && [ "$runs" -ge "$MAX_RUNS" ]; then
    release_control_and_runner
    say "MAX_RUNS=$MAX_RUNS reached; supervisor exiting"
    exit 0
  fi

  if [ -e "$STOP" ]; then
    release_control_and_runner
    say "STOP marker appeared before launch; supervisor exiting"
    exit 0
  fi
  if [ -e "$PAUSE" ]; then
    release_control_and_runner
    sleep "$CHECK_S" 7>&-
    continue
  fi

  launch_runner_from_handoff || true
  # Loop immediately. The next iteration reacquires both locks and evaluates the
  # gate before any backoff/relaunch decision.
done
