#!/usr/bin/env bash
# Progress-oriented Codex health probe.
# Process existence is not progress: this checks the child, JSONL events, and progress artifacts.
set -euo pipefail
cd "$(dirname "$0")/.."

bad=0
ok() { echo "  OK       $*"; }
note() { echo "  NOTE     $*"; }
problem() { echo "  PROBLEM  $*"; bad=1; }

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
}
age_minutes() {
  echo $(( ( $(date +%s) - $(mtime "$1") ) / 60 ))
}

runner_pid=$(cat .harness/codex-runner.lock 2>/dev/null || true)
child_pid=$(cat .harness/codex-child.pid 2>/dev/null || true)
supervisor_pid=$(cat .harness/codex-supervisor.lock 2>/dev/null || true)

runner_live=0
child_live=0
supervisor_live=0
pid_alive "$runner_pid" && runner_live=1
pid_alive "$child_pid" && child_live=1
pid_alive "$supervisor_pid" && supervisor_live=1

if [ "$runner_live" -eq 1 ]; then
  ok "Codex runner active (pid $runner_pid)"
  [ "$child_live" -eq 1 ] && ok "Codex child active (pid $child_pid)" || problem "runner is active but Codex child is not"
else
  note "Codex runner is idle"
fi

if [ "$supervisor_live" -eq 1 ]; then
  ok "Codex supervisor active (pid $supervisor_pid)"
  if [ ! -e .harness/codex-supervisor.pause ] && [ ! -e .harness/STOP ] && [ "$runner_live" -eq 0 ]; then
    problem "supervisor is active but no runner is alive"
  fi
else
  note "supervisor not running (manual mode)"
fi

latest=$(ls -t .harness/logs/codex-*.jsonl 2>/dev/null | head -1 || true)
if [ -z "$latest" ]; then
  [ "$runner_live" -eq 1 ] && problem "runner is active but no Codex structured log exists" || note "no Codex run has started yet"
else
  age=$(age_minutes "$latest")
  if [ "$runner_live" -eq 1 ]; then
    if [ "$age" -le "${MAX_EVENT_AGE_MIN:-20}" ]; then
      ok "Codex event log updated ${age} min ago"
    else
      problem "Codex process exists but event log is ${age} min old"
    fi
  else
    note "latest Codex event log is ${age} min old"
  fi
fi

if [ -s .harness/codex-session-id ]; then
  ok "resumable Codex session recorded"
else
  note "no Codex session id recorded yet"
fi

if [ -f PROGRESS.md ]; then
  page=$(age_minutes PROGRESS.md)
  if [ "$runner_live" -eq 1 ] && [ "$page" -gt "${MAX_PROGRESS_AGE_MIN:-60}" ]; then
    problem "Codex is running but PROGRESS.md has not changed for ${page} min"
  else
    ok "PROGRESS.md present (last update ${page} min ago)"
  fi
else
  problem "PROGRESS.md does not exist"
fi

if [ -f MILESTONE.md ] && [ -f ACCEPTANCE-TESTS.md ] && [ -f spec/CORE-LAWS.md ]; then
  ok "milestone, acceptance tests, and protected laws present"
else
  problem "required product-control documents missing"
fi

if [ -f .harness/last-run.json ]; then
  python3 - .harness/last-run.json <<'PY'
import json, sys
try:
    d=json.load(open(sys.argv[1], encoding="utf-8"))
    print(f"  NOTE     last run: mode={d.get('mode')} exit={d.get('exit_code')} terminal={d.get('terminal_event')} duration={d.get('duration_seconds')}s")
except Exception as e:
    print(f"  PROBLEM  could not parse last-run.json: {e}")
    raise SystemExit(1)
PY
  [ "$?" -eq 0 ] || bad=1
fi

exit "$bad"
