#!/usr/bin/env bash
# Static H0 harness checks. Does not invoke real Codex or Unreal.
set -euo pipefail
cd "$(dirname "$0")/.."

required=(
  AGENTS.md MILESTONE.md ACCEPTANCE-TESTS.md HARNESS-RULES.md
  spec/CORE-LAWS.md spec/WORLD-VISION.md docs/ARCHITECTURE.md docs/MILESTONE-GATES.md
  milestones/H0/gate.json milestones/H1/SPEC.md milestones/H1/gate.json
  bin/run-codex.sh bin/health-codex.sh bin/supervise-codex.sh bin/restart-codex.sh
  bin/milestone-gate.py bin/codex-process.py bin/process_group.py
  bin/milestone-gate-watchdog.sh
  bin/check-h1-spec.sh
  bin/test-codex-lifecycle.sh bin/test-harness-control.sh bin/test-lock-races.sh
  bin/test-gate-timeout-tree.sh
)
for f in "${required[@]}"; do
  [ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
done
[ -x bin/milestone-gate-watchdog.sh ] || { echo "not executable: bin/milestone-gate-watchdog.sh" >&2; exit 1; }

for f in bin/run-codex.sh bin/health-codex.sh bin/supervise-codex.sh bin/restart-codex.sh bin/milestone-gate-watchdog.sh bin/check-h1-spec.sh bin/test-codex-lifecycle.sh bin/test-harness-control.sh bin/test-lock-races.sh bin/test-gate-timeout-tree.sh; do
  bash -n "$f"
done
python3 -m py_compile bin/milestone-gate.py bin/codex-process.py bin/process_group.py
python3 - <<'PY'
import json
p='milestones/H0/gate.json'
d=json.load(open(p, encoding='utf-8'))
assert d.get('milestone') == 'H0'
checks={c.get('id'): c for c in d.get('checks', [])}
ids=set(checks)
assert {'h0-static','h1-spec','codex-lifecycle','harness-control','lock-races','gate-timeout-tree'} <= ids, ids
assert checks['h1-spec'].get('command') == ['bash','bin/check-h1-spec.sh']
PY

grep -q -- '--sandbox "$SANDBOX"' bin/run-codex.sh
grep -q 'resume "$session_id"' bin/run-codex.sh
grep -q 'CONTROL_LOCK=.harness/codex-control.lock' bin/run-codex.sh
grep -q 'flock -n 8' bin/run-codex.sh
grep -q 'codex-process.py' bin/run-codex.sh

grep -q 'CONTROL_LOCK=.harness/codex-control.lock' bin/supervise-codex.sh
grep -q 'acquire_control_and_runner' bin/supervise-codex.sh
grep -q 'JWH_CONTROL_LOCK_HELD=1 JWH_RUNNER_LOCK_HELD=1' bin/supervise-codex.sh
grep -q 'milestone-gate-watchdog.sh' bin/supervise-codex.sh
grep -q 'exec-subreaper' bin/supervise-codex.sh
grep -q 'terminate-descendants-strict' bin/supervise-codex.sh
grep -q 'unset JWH_SUBREAPER_ACTIVE' bin/supervise-codex.sh
grep -q 'BACKOFF_MAX' bin/supervise-codex.sh
grep -q 'GATE_ACTIVE=.harness/GATE-ACTIVE' bin/supervise-codex.sh
grep -q 'cleanup_until_verified' bin/milestone-gate-watchdog.sh
grep -q 'terminate-descendants-strict' bin/milestone-gate-watchdog.sh

grep -q 'CONTROL_LOCK=.harness/codex-control.lock' bin/restart-codex.sh
grep -q 'flock -w 5 8' bin/restart-codex.sh
grep -q 'WRAPPER_STOP_FILE=.harness/codex-wrapper.stop' bin/restart-codex.sh
grep -q 'WRAPPER_READY_FILE=.harness/codex-wrapper.ready' bin/restart-codex.sh
grep -q 'WRAPPER_LOCK_FILE=.harness/codex-wrapper.lock' bin/restart-codex.sh
grep -q 'JWH_CONTROL_LOCK_HELD=1 JWH_RUNNER_LOCK_HELD=1' bin/restart-codex.sh
grep -q 'GATE_ACTIVE=.harness/GATE-ACTIVE' bin/run-codex.sh
grep -q 'GATE_ACTIVE=.harness/GATE-ACTIVE' bin/restart-codex.sh

grep -q -- '--wrapper-pid-file' bin/run-codex.sh
grep -q -- '--wrapper-lock-file' bin/run-codex.sh
grep -q -- '--stop-file' bin/run-codex.sh
grep -q -- '--ready-file' bin/run-codex.sh
grep -q 'timer.join' bin/codex-process.py
grep -q 'enable_child_subreaper' bin/codex-process.py
grep -q 'terminate_executable_descendants' bin/codex-process.py
grep -q 'exec-subreaper' bin/run-codex.sh
grep -q 'terminate-descendants' bin/run-codex.sh
! grep -q 'kill -TERM -- "-\$child_pid"' bin/run-codex.sh
! grep -q 'kill -KILL -- "-\$child_pid"' bin/run-codex.sh

grep -q 'lock_held "$RUNNER_LOCK"' bin/health-codex.sh
grep -q 'MAX_EVENT_AGE_MIN' bin/health-codex.sh
! grep -q 'pid-lock.py' bin/test-codex-lifecycle.sh

grep -q 'start_new_session' bin/milestone-gate.py
grep -q 'pass_fds' bin/milestone-gate.py
grep -q 'enable_child_subreaper' bin/milestone-gate.py
grep -q 'terminate_executable_descendants' bin/milestone-gate.py
grep -q 'JWH_GATE_LEASE_FDS=8,9' bin/supervise-codex.sh
grep -q 'reacquire control/runner locks after milestone gate' bin/supervise-codex.sh
grep -q 'os.killpg' bin/milestone-gate.py
grep -q 'process tree terminated' bin/milestone-gate.py

echo "H0 static harness checks passed"
