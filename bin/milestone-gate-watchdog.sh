#!/usr/bin/env bash
# Run the milestone gate behind a wall-clock watchdog.
# If the gate stops returning, quarantine the harness instead of assuming its
# process tree is safe to clean up automatically.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p .harness/logs
QUARANTINE=.harness/GATE-TIMEOUT-BLOCKED
WATCHDOG_S=${GATE_WATCHDOG_S:-900}
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
OUT=".harness/logs/gate-watchdog-$STAMP.stdout.tmp"
ERR=".harness/logs/gate-watchdog-$STAMP.stderr.tmp"

python3 bin/milestone-gate.py "$@" >"$OUT" 2>"$ERR" &
gate_pid=$!
started=$(date +%s)

while kill -0 "$gate_pid" 2>/dev/null; do
  now=$(date +%s)
  if [ $((now - started)) -ge "$WATCHDOG_S" ]; then
    cat > "$QUARANTINE" <<EOF
Milestone gate watchdog expired after ${WATCHDOG_S}s.
Gate pid at timeout: ${gate_pid}
Autonomous Codex execution is blocked because gate process state is no longer trusted.
Confirm no escaped gate process can still modify the worktree, then remove this marker manually.
EOF
    [ -s "$OUT" ] && cat "$OUT"
    [ -s "$ERR" ] && cat "$ERR" >&2
    echo "milestone gate watchdog expired; harness quarantined at $QUARANTINE" >&2
    exit 2
  fi
  sleep 0.2
done

set +e
wait "$gate_pid"
rc=$?
set -e
[ -s "$OUT" ] && cat "$OUT"
[ -s "$ERR" ] && cat "$ERR" >&2
rm -f "$OUT" "$ERR"
exit "$rc"
