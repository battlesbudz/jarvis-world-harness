#!/usr/bin/env bash
# Keep exactly one Codex runner alive until explicitly paused/stopped.
# Uses the persisted session id from run-codex.sh, so relaunches continue the same thread.
set -uo pipefail
cd "$(dirname "$0")/.."

mkdir -p .harness/logs
SUPERVISOR_LOCK=.harness/codex-supervisor.lock
RUNNER_LOCK=.harness/codex-runner.lock
PAUSE=.harness/codex-supervisor.pause
STOP=.harness/STOP
LOG=.harness/logs/codex-supervisor.log
OUT=.harness/logs/codex-supervised-runner.out
CHECK_S=${CHECK_S:-30}
FAST_DEATH_S=${FAST_DEATH_S:-180}
BACKOFF_MIN=${BACKOFF_MIN:-60}
BACKOFF_MAX=${BACKOFF_MAX:-1800}
MAX_RUNS=${MAX_RUNS:-0}
backoff=$BACKOFF_MIN
runs=0

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
say() { echo "$(date '+%F %T')  $*" | tee -a "$LOG"; }

if [ -s "$SUPERVISOR_LOCK" ] && pid_alive "$(cat "$SUPERVISOR_LOCK" 2>/dev/null)"; then
  echo "Codex supervisor already active: pid $(cat "$SUPERVISOR_LOCK")" >&2
  exit 2
fi
rm -f "$SUPERVISOR_LOCK"
echo $$ > "$SUPERVISOR_LOCK"
trap 'rm -f "$SUPERVISOR_LOCK"' EXIT

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

  rp=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
  if pid_alive "$rp"; then
    backoff=$BACKOFF_MIN
    sleep "$CHECK_S"
    continue
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

  if [ "$rc" -ne 0 ] || [ "$dur" -lt "$FAST_DEATH_S" ]; then
    say "short/failed run; backing off ${backoff}s"
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    [ "$backoff" -gt "$BACKOFF_MAX" ] && backoff=$BACKOFF_MAX
  else
    # A clean Codex turn is not proof the milestone is complete. Resume the same thread.
    backoff=$BACKOFF_MIN
    sleep 15
  fi
done
