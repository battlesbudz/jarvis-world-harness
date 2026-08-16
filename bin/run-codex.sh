#!/usr/bin/env bash
# Codex-first runner for Jarvis World Harness.
# Persists the Codex thread id while events stream so restarts keep long-horizon context.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v codex >/dev/null || { echo "codex CLI not found on PATH" >&2; exit 127; }
command -v python3 >/dev/null || { echo "python3 not found on PATH" >&2; exit 127; }
command -v flock >/dev/null || { echo "flock not found on PATH" >&2; exit 127; }

# Re-exec this same shell process as a Linux child subreaper. If the Python
# wrapper is hard-killed, detached/double-forked Codex descendants reparent here
# and remain cleanable before the runner lock is released.
if [ "${JWH_SUBREAPER_ACTIVE:-0}" != "1" ]; then
  exec python3 bin/process_group.py exec-subreaper bash "$0" "$@"
fi

mkdir -p .harness/logs
CONTROL_LOCK=.harness/codex-control.lock
RUNNER_LOCK=.harness/codex-runner.lock
CHILD_PID_FILE=.harness/codex-child.pid
WRAPPER_PID_FILE=.harness/codex-wrapper.pid
WRAPPER_LOCK_FILE=.harness/codex-wrapper.lock
WRAPPER_READY_FILE=.harness/codex-wrapper.ready
WRAPPER_STOP_FILE=.harness/codex-wrapper.stop
SESSION_FILE=.harness/codex-session-id
LAST_RUN=.harness/last-run.json
LAST_MESSAGE=.harness/last-message.md

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

# Runner startup participates in the shared control handoff. The supervisor holds
# this same lock while evaluating a milestone and deciding whether to launch; a
# manual restart holds it while installing PAUSE and transferring runner ownership.
if [ "${JWH_CONTROL_LOCK_HELD:-0}" = "1" ]; then
  { true >&9; } 2>/dev/null || { echo "inherited control lock fd is missing" >&2; exit 2; }
else
  exec 9>"$CONTROL_LOCK"
  flock 9
fi

# The runner kernel lock, not PID text in the file, is authoritative.
if [ "${JWH_RUNNER_LOCK_HELD:-0}" = "1" ]; then
  { true >&8; } 2>/dev/null || { echo "inherited runner lock fd is missing" >&2; exit 2; }
else
  exec 8<>"$RUNNER_LOCK"
  if ! flock -n 8; then
    owner=$(cat "$RUNNER_LOCK" 2>/dev/null || true)
    echo "Codex runner already active${owner:+: pid $owner}" >&2
    flock -u 9 2>/dev/null || true
    exec 9>&- 2>/dev/null || true
    exec 8>&- 2>/dev/null || true
    exit 2
  fi
fi

# Under the kernel lock, PID text is safe as metadata for diagnostics/restart.
: > "$RUNNER_LOCK"
printf '%s\n' "$$" > "$RUNNER_LOCK"
rm -f "$CHILD_PID_FILE"
rm -f "$WRAPPER_PID_FILE"
rm -f "$WRAPPER_READY_FILE"
rm -f "$WRAPPER_STOP_FILE"

# Startup handoff is complete. Keep fd 8 for the whole run, but release fd 9 so
# restart/control operations can proceed while Codex is working.
flock -u 9 2>/dev/null || true
exec 9>&- 2>/dev/null || true

process_pid=""
job_running() {
  p="${1:-}"
  [ -n "$p" ] && jobs -pr | grep -Fxq -- "$p"
}

clean_descendants() {
  python3 bin/process_group.py terminate-descendants "$$"
}

cleanup() {
  rc=$?
  trap - EXIT INT TERM HUP
  if [ -n "${process_pid:-}" ] && job_running "$process_pid"; then
    kill "$process_pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      sleep 1
      job_running "$process_pid" || break
    done
    job_running "$process_pid" && kill -9 "$process_pid" 2>/dev/null || true
  fi
  [ -n "${process_pid:-}" ] && wait "$process_pid" 2>/dev/null || true

  # Verify the wrapper did not leave descendants in another session/process
  # group. This is also the hard-wrapper-death fallback cleanup boundary.
  if ! clean_descendants; then
    echo "failed to clean complete Codex descendant tree" >&2
    rc=2
  fi

  rm -f "$CHILD_PID_FILE"
  rm -f "$WRAPPER_PID_FILE"
  rm -f "$WRAPPER_READY_FILE"
  rm -f "$WRAPPER_STOP_FILE"
  : > "$RUNNER_LOCK" 2>/dev/null || true
  flock -u 8 2>/dev/null || true
  exec 8>&- 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$-${RANDOM:-0}"
LOG=".harness/logs/codex-$RUN_ID.jsonl"
ERRLOG=".harness/logs/codex-$RUN_ID.stderr.log"
STARTED_AT=$(date +%s)
SANDBOX="${CODEX_SANDBOX:-workspace-write}"

BASE_PROMPT=$(cat <<'PROMPT'
You are the implementation agent for Jarvis World Harness.
Read AGENTS.md and follow its required reading order.
Work only on the currently authorized MILESTONE.md.
Use ACCEPTANCE-TESTS.md as the definition of evidence.
Maintain PLAN.md and append concise, evidence-based progress to PROGRESS.md.
Do not modify protected core laws to make implementation easier.
Do not begin a later milestone until the current milestone's acceptance evidence passes.
Continue until the milestone stop condition is met or a true external blocker prevents further independent progress.
PROMPT
)

CONTINUE_PROMPT=$(cat <<'PROMPT'
Continue the same Jarvis World Harness milestone and the same plan.
Read current repo state, PLAN.md, PROGRESS.md, MILESTONE.md, and ACCEPTANCE-TESTS.md before deciding what remains.
Do not repeat completed work. Verify evidence as you go.
Do not begin a later milestone. Stop only when the current milestone acceptance criteria pass or a true external blocker prevents further independent progress.
PROMPT
)

# A manual restart note is one-turn operator context, not resume-only context.
# If the interrupted runner had not emitted thread.started yet, the replacement is
# necessarily a fresh Codex turn and must still receive the note.
if [ -n "${RESUME_NOTE_FILE:-}" ] && [ -f "$RESUME_NOTE_FILE" ]; then
  RESTART_NOTE="$(cat "$RESUME_NOTE_FILE")"
  BASE_PROMPT="$RESTART_NOTE

$BASE_PROMPT"
  CONTINUE_PROMPT="$RESTART_NOTE

$CONTINUE_PROMPT"
fi

CMD=(codex exec --json --sandbox "$SANDBOX" --output-last-message "$LAST_MESSAGE")
if [ -n "${CODEX_MODEL:-}" ]; then
  CMD+=(--model "$CODEX_MODEL")
fi

mode="fresh"
session_id=""
if [ "${FRESH:-0}" = "1" ]; then
  rm -f "$SESSION_FILE"
elif [ -s "$SESSION_FILE" ]; then
  session_id="$(tr -d '[:space:]' < "$SESSION_FILE")"
  if [ -n "$session_id" ]; then
    mode="resume"
  fi
fi

if [ "$mode" = "resume" ]; then
  CMD+=(resume "$session_id" "$CONTINUE_PROMPT")
else
  CMD+=("$BASE_PROMPT")
fi

printf '%s\n' "$(codex --version 2>/dev/null || true)" > .harness/codex-version.txt
echo "starting Codex ($mode, sandbox=$SANDBOX); structured log: $LOG"

# The Python process wrapper owns the Codex child and does not return until both stdout/stderr
# consumers are drained. It persists thread.started immediately while streaming JSONL.
set +e
python3 bin/codex-process.py \
  --log "$LOG" \
  --errlog "$ERRLOG" \
  --session-file "$SESSION_FILE" \
  --child-pid-file "$CHILD_PID_FILE" \
  --wrapper-pid-file "$WRAPPER_PID_FILE" \
  --wrapper-lock-file "$WRAPPER_LOCK_FILE" \
  --ready-file "$WRAPPER_READY_FILE" \
  --stop-file "$WRAPPER_STOP_FILE" \
  -- "${CMD[@]}" &
process_pid=$!
wait "$process_pid"
rc=$?
process_pid=""
if ! clean_descendants; then
  echo "failed to clean complete Codex descendant tree before post-processing" >&2
  rc=2
fi
set -e

if [ -s "$SESSION_FILE" ]; then
  session_id="$(tr -d '[:space:]' < "$SESSION_FILE")"
fi

terminal_event=$(python3 - "$LOG" <<'PY'
import json, sys
terminal = ""
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        for line in f:
            try:
                t = json.loads(line).get("type", "")
            except json.JSONDecodeError:
                continue
            if t in {"turn.completed", "turn.failed", "error"}:
                terminal = t
except FileNotFoundError:
    pass
print(terminal)
PY
)

ENDED_AT=$(date +%s)
python3 - "$LAST_RUN" "$STARTED_AT" "$ENDED_AT" "$rc" "$LOG" "$ERRLOG" "$mode" "${session_id:-}" "${terminal_event:-}" <<'PY'
import json, os, sys, tempfile
from pathlib import Path
path, started, ended, rc, log, errlog, mode, sid, terminal = sys.argv[1:]
p = Path(path)
p.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "started_at_epoch": int(started),
    "ended_at_epoch": int(ended),
    "duration_seconds": int(ended) - int(started),
    "exit_code": int(rc),
    "event_log": log,
    "stderr_log": errlog,
    "mode": mode,
    "session_id": sid or None,
    "terminal_event": terminal or None,
}
fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".tmp.", dir=p.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_name, p)
finally:
    try: os.unlink(tmp_name)
    except FileNotFoundError: pass
PY

exit "$rc"
