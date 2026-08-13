#!/usr/bin/env bash
# Progress-oriented health probe. A process existing is not sufficient evidence of work.
set -euo pipefail
cd "$(dirname "$0")/.."

bad=0
ok() { echo "  OK       $*"; }
problem() { echo "  PROBLEM  $*"; bad=1; }

latest=$(ls -t .harness/logs/codex-*.jsonl 2>/dev/null | head -1 || true)
if [ -z "$latest" ]; then
  problem "no Codex structured log exists"
else
  age=$(( ( $(date +%s) - $(stat -c %Y "$latest" 2>/dev/null || stat -f %m "$latest") ) / 60 ))
  if [ "$age" -le "${MAX_EVENT_AGE_MIN:-20}" ]; then
    ok "Codex event log updated ${age} min ago"
  else
    problem "Codex event log is ${age} min old"
  fi
fi

if [ -f PROGRESS.md ]; then
  page=$(( ( $(date +%s) - $(stat -c %Y PROGRESS.md 2>/dev/null || stat -f %m PROGRESS.md) ) / 60 ))
  if [ "$page" -le "${MAX_PROGRESS_AGE_MIN:-60}" ]; then
    ok "PROGRESS.md updated ${page} min ago"
  else
    problem "PROGRESS.md has not changed for ${page} min"
  fi
else
  problem "PROGRESS.md does not exist"
fi

if [ -f MILESTONE.md ] && [ -f ACCEPTANCE-TESTS.md ] && [ -f spec/CORE-LAWS.md ]; then
  ok "milestone, acceptance tests, and protected laws present"
else
  problem "required product-control documents missing"
fi

exit "$bad"
