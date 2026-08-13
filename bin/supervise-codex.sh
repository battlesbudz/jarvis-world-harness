#!/usr/bin/env bash
# Keep exactly one Codex runner alive until the active milestone objectively passes,
# or the operator explicitly pauses/stops the supervisor.
set -uo pipefail
cd "$(dirname "$0")/.."

command -v python3 >/dev/null || { echo "python3 not found on PATH" >&2; exit 127; }
mkdir -p .harness/logs
SUPERVISOR_LOCK=.harness/codex-supervisor.lock
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

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
say() { echo "$(date '+%F %T')  $*" | tee -a "$LOG"; }

acquire_supervisor_lock() {
  if python3 bin/pid-lock.py acquire "$SUPERVISOR_LOCK" "$$"; then return 0; fi
  owner=$(cat "$SUPERVISOR_LOCK" 2>/dev/null || true)
  if pid_alive "$owner"; then
    echo "Codex supervisor already active: pid $owner" >&2
    return 2
  fi
  rm -f "$SUPERVISOR_LOCK"
  if python3 bin/pid-lock.py acquire "$SUPERVISOR_LOCK" "$$"; then return 0; fi
  owner=$(cat "$SUPERVISOR_LOCK" 2>/dev/null || true)
  echo "Codex supervisor lock was acquired concurrently${owner:+ by pid $owner}" >&2
  return 2
}

milestone_passed() {
  python3 bin/milestone-gate.py >"$GATE_OUT" 2>"$GATE_ERR"
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

acquire_supervisor_lock || exit $?
cleanup() {
  rc=$?
  trap - EXIT
  python3 bin/pid-lock.py release "$SUPERVISOR_LOCK" "$$" >/dev/null 2>&1 || true
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
    sleep "$CHECK_S"
    continue
  fi

  # Never run milestone evals against a worktree while Codex is actively editing it.
  rp=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
  if pid_alive "$rp"; then
    backoff=$BACKOFF_MIN
    sleep "$CHECK_S"
    continue
  fi

  milestone_passed
  gate_rc=$?
  if [ "$gate_rc" -eq 0 ]; then
    say "active milestone objectively passed; supervisor exiting"
    exit 0
  elif [ "$gate_rc" -eq 2 ]; then
    say "cannot safely continue without a valid milestone gate; supervisor exiting"
    exit 2
  fi

  if [ "$MAX_RUNS" -gt 0 ] && [ "$runs" -ge "$MAX_RUNS" ]; then
    say "MAX_RUNS=$MAX_RUNS reached; supervisor exiting"
    exit 0
  fi

  runs=$((runs + 1))
  say "launching Codex runner (run $runs)"
  start=$(date +%s)
  bin/run-codex.sh >> "$OUT" 2>&1
  rc=$?
  dur=$(( $(date +%s) - start ))
  say "runner exited rc=$rc after ${dur}s"

  [ -e "$STOP" ] && continue
  [ -e "$PAUSE" ] && continue

  milestone_passed
  gate_rc=$?
  if [ "$gate_rc" -eq 0 ]; then
    say "active milestone passed after run $runs; supervisor exiting"
    exit 0
  elif [ "$gate_rc" -eq 2 ]; then
    say "milestone gate failed to evaluate safely; supervisor exiting"
    exit 2
  fi

  if [ "$rc" -ne 0 ] || [ "$dur" -lt "$FAST_DEATH_S" ]; then
    say "short/failed run; backing off ${backoff}s"
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    [ "$backoff" -gt "$BACKOFF_MAX" ] && backoff=$BACKOFF_MAX
  else
    backoff=$BACKOFF_MIN
    sleep 15
  fi
done
