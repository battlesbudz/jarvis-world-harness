#!/usr/bin/env bash
# Codex-first runner for Jarvis World Harness.
# Persists the Codex thread id so repeated invocations continue the same milestone.
# H0/H1 are intentionally headless; Unreal/MCP remains a separate lane.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v codex >/dev/null || { echo "codex CLI not found on PATH" >&2; exit 127; }
command -v python3 >/dev/null || { echo "python3 not found on PATH" >&2; exit 127; }

mkdir -p .harness/logs
RUNNER_LOCK=.harness/codex-runner.lock
CHILD_PID_FILE=.harness/codex-child.pid
SESSION_FILE=.harness/codex-session-id
LAST_RUN=.harness/last-run.json
LAST_MESSAGE=.harness/last-message.md

pid_alive() {
  [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

if [ -s "$RUNNER_LOCK" ] && pid_alive "$(cat "$RUNNER_LOCK" 2>/dev/null)"; then
  echo "Codex runner already active: pid $(cat "$RUNNER_LOCK")" >&2
  exit 2
fi
rm -f "$RUNNER_LOCK" "$CHILD_PID_FILE"
echo $$ > "$RUNNER_LOCK"

child_pid=""
cleanup() {
  rc=$?
  if [ -n "${child_pid:-}" ] && pid_alive "$child_pid"; then
    kill "$child_pid" 2>/dev/null || true
    sleep 1
    pid_alive "$child_pid" && kill -9 "$child_pid" 2>/dev/null || true
  fi
  rm -f "$RUNNER_LOCK" "$CHILD_PID_FILE"
  return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

STAMP=$(date +%Y%m%d-%H%M%S)
LOG=".harness/logs/codex-$STAMP.jsonl"
ERRLOG=".harness/logs/codex-$STAMP.stderr.log"
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

if [ -n "${RESUME_NOTE_FILE:-}" ] && [ -f "$RESUME_NOTE_FILE" ]; then
  CONTINUE_PROMPT="$(cat "$RESUME_NOTE_FILE")

$CONTINUE_PROMPT"
fi

CMD=(codex exec --json --sandbox "$SANDBOX" --output-last-message "$LAST_MESSAGE")
if [ -n "${CODEX_MODEL:-}" ]; then
  CMD+=(--model "$CODEX_MODEL")
fi

mode="fresh"
session_id=""
if [ "${FRESH:-0}" != "1" ] && [ -s "$SESSION_FILE" ]; then
  session_id="$(tr -d '[:space:]' < "$SESSION_FILE")"
  if [ -n "$session_id" ]; then
    mode="resume"
    CMD+=(resume "$session_id" "$CONTINUE_PROMPT")
  else
    CMD+=("$BASE_PROMPT")
  fi
else
  CMD+=("$BASE_PROMPT")
fi

printf '%s\n' "$(codex --version 2>/dev/null || true)" > .harness/codex-version.txt
echo "starting Codex ($mode, sandbox=$SANDBOX); structured log: $LOG"

# Keep stdout as valid JSONL. Stderr is captured separately so diagnostics never corrupt the event stream.
set +e
"${CMD[@]}" > >(tee "$LOG") 2> >(tee "$ERRLOG" >&2) &
child_pid=$!
echo "$child_pid" > "$CHILD_PID_FILE"
wait "$child_pid"
rc=$?
set -e

# A fresh exec emits thread.started. Persist it so the next run resumes the exact thread.
new_session_id=$(python3 - "$LOG" <<'PY'
import json, sys
sid = ""
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started" and event.get("thread_id"):
                sid = str(event["thread_id"])
except FileNotFoundError:
    pass
print(sid)
PY
)
if [ -n "$new_session_id" ]; then
  printf '%s\n' "$new_session_id" > "$SESSION_FILE"
  session_id="$new_session_id"
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
import json, sys
path, started, ended, rc, log, errlog, mode, sid, terminal = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump({
        "started_at_epoch": int(started),
        "ended_at_epoch": int(ended),
        "duration_seconds": int(ended) - int(started),
        "exit_code": int(rc),
        "event_log": log,
        "stderr_log": errlog,
        "mode": mode,
        "session_id": sid or None,
        "terminal_event": terminal or None,
    }, f, indent=2)
    f.write("\n")
PY

exit "$rc"
