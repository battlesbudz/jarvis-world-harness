#!/usr/bin/env bash
# Run one milestone evidence command with a shorter inner deadline than the
# milestone-gate outer timeout. If it overruns, fail closed by writing STOP and a
# persistent quarantine marker; do not depend on process-tree cleanup succeeding.
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$#" -ge 2 ] || { echo "usage: $0 <timeout-seconds> <command> [args...]" >&2; exit 2; }
INNER_TIMEOUT_S="$1"
shift
case "$INNER_TIMEOUT_S" in
  ''|*[!0-9]*) echo "timeout must be a positive integer" >&2; exit 2 ;;
esac
[ "$INNER_TIMEOUT_S" -gt 0 ] || { echo "timeout must be > 0" >&2; exit 2; }

mkdir -p .harness/logs
QUARANTINE=.harness/GATE-TIMEOUT-BLOCKED
STOP=.harness/STOP
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
OUT=".harness/logs/gate-check-$STAMP.stdout.tmp"
ERR=".harness/logs/gate-check-$STAMP.stderr.tmp"

"$@" >"$OUT" 2>"$ERR" &
check_pid=$!
started=$(date +%s)

while kill -0 "$check_pid" 2>/dev/null; do
  now=$(date +%s)
  if [ $((now - started)) -ge "$INNER_TIMEOUT_S" ]; then
    cat > "$QUARANTINE" <<EOF
Milestone evidence check exceeded its inner deadline of ${INNER_TIMEOUT_S}s.
Check pid at timeout: ${check_pid}
Command: $*
Autonomous Codex execution is quarantined because check process state is no longer trusted.
Confirm no escaped check can still modify the worktree, then remove both .harness/GATE-TIMEOUT-BLOCKED and .harness/STOP manually.
EOF
    touch "$STOP"
    [ -s "$OUT" ] && cat "$OUT"
    [ -s "$ERR" ] && cat "$ERR" >&2
    echo "gate check exceeded inner deadline; harness quarantined" >&2
    exit 1
  fi
  sleep 0.2
done

set +e
wait "$check_pid"
rc=$?
set -e
[ -s "$OUT" ] && cat "$OUT"
[ -s "$ERR" ] && cat "$ERR" >&2
rm -f "$OUT" "$ERR"
exit "$rc"
