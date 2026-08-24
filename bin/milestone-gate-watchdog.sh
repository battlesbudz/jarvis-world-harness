#!/usr/bin/env bash
# Own one supervised milestone evaluation and its serialization leases.
# A persistent active marker blocks new work if this ownership boundary dies.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v python3 >/dev/null || { echo "python3 not found on PATH" >&2; exit 127; }
command -v timeout >/dev/null || { echo "timeout not found on PATH" >&2; exit 127; }

if [ "${JWH_GATE_WATCHDOG_SUBREAPER_ACTIVE:-0}" != "1" ]; then
  exec env JWH_GATE_WATCHDOG_SUBREAPER_ACTIVE=1 \
    python3 bin/process_group.py exec-subreaper bash "$0" "$@"
fi
unset JWH_SUBREAPER_ACTIVE JWH_GATE_WATCHDOG_SUBREAPER_ACTIVE

mkdir -p .harness/logs
ACTIVE=.harness/GATE-ACTIVE
QUARANTINE=.harness/GATE-TIMEOUT-BLOCKED
STOP=.harness/STOP
WATCHDOG_S=${GATE_WATCHDOG_S:-900}
KILL_GRACE_S=${GATE_WATCHDOG_KILL_GRACE_S:-2}
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
OUT=".harness/logs/gate-watchdog-$STAMP.stdout.tmp"
ERR=".harness/logs/gate-watchdog-$STAMP.stderr.tmp"
CLEANUP_FAILED=".harness/gate-watchdog-$STAMP.cleanup-failed"
descendants_found=0

case "$WATCHDOG_S" in
  ''|*[!0-9]*) echo "GATE_WATCHDOG_S must be a positive integer" >&2; exit 2 ;;
esac
[ "$WATCHDOG_S" -gt 0 ] || { echo "GATE_WATCHDOG_S must be > 0" >&2; exit 2; }

# Creation occurs while the caller holds both serialization leases. Never remove
# this marker from a trap: abnormal watchdog death must leave a durable barrier.
if ! (set -C; printf '%s\n' "$$" > "$ACTIVE") 2>/dev/null; then
  echo "active or unverified milestone gate already exists: $ACTIVE" >&2
  exit 2
fi

quarantine() {
  reason=$1
  touch "$STOP"
  if [ ! -e "$QUARANTINE" ]; then
    cat > "$QUARANTINE" <<EOF
$reason
Gate watchdog pid: $$
Autonomous Codex execution is blocked until process-tree safety is verified.
After verification, remove $QUARANTINE and $STOP manually.
EOF
  fi
}

cleanup_until_verified() {
  while true; do
    set +e
    python3 bin/process_group.py terminate-descendants-strict "$$"
    cleanup_rc=$?
    set -e
    case "$cleanup_rc" in
      0) return 0 ;;
      3) descendants_found=1; return 0 ;;
      *)
        : > "$CLEANUP_FAILED"
        quarantine "Milestone gate descendant cleanup could not be verified (rc=$cleanup_rc)."
        echo "gate cleanup verification failed (rc=$cleanup_rc); retaining leases and retrying" >&2
        sleep 1
        ;;
    esac
  done
}

set +e
timeout --signal=TERM --kill-after="${KILL_GRACE_S}s" "${WATCHDOG_S}s" \
  python3 bin/milestone-gate.py "$@" >"$OUT" 2>"$ERR"
gate_rc=$?
set -e
if [ "$gate_rc" -eq 124 ]; then
  quarantine "Milestone gate watchdog expired after ${WATCHDOG_S}s."
fi

# Do not release inherited control/runner leases until the complete adopted tree
# is gone. A killed or failed helper is retried while the watchdog remains alive.
cleanup_until_verified

[ -s "$OUT" ] && cat "$OUT"
[ -s "$ERR" ] && cat "$ERR" >&2
rm -f "$OUT" "$ERR"

if [ "$descendants_found" -eq 1 ]; then
  echo "gate evaluator exited while executable descendants remained; tree terminated" >&2
  gate_rc=2
fi
if [ -e "$CLEANUP_FAILED" ]; then
  rm -f "$CLEANUP_FAILED"
  gate_rc=2
fi

# Cleanup is now verified. Remove only our own marker; mismatched content means
# ownership metadata was replaced and autonomous execution must remain blocked.
owner=$(cat "$ACTIVE" 2>/dev/null || true)
if [ "$owner" != "$$" ]; then
  quarantine "Milestone gate active-owner metadata changed during evaluation."
  echo "active gate owner changed from $$ to ${owner:-missing}" >&2
  exit 2
fi
rm -f "$ACTIVE"
exit "$gate_rc"
