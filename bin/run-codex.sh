#!/usr/bin/env bash
# Codex-first starter runner for Jarvis World Harness.
# This is intentionally independent of Unreal so H0/H1 can run headless.
# Unreal boot/MCP supervision remains available as a second lane from AAABench.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v codex >/dev/null || { echo "codex CLI not found on PATH" >&2; exit 127; }

mkdir -p .harness/logs
LOCK=.harness/codex-runner.lock
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "Codex runner already active: pid $(cat "$LOCK")" >&2
  exit 2
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

STAMP=$(date +%Y%m%d-%H%M%S)
LOG=".harness/logs/codex-$STAMP.jsonl"
PROMPT=$(cat <<'PROMPT'
You are the implementation agent for Jarvis World Harness.
Read AGENTS.md and follow its required reading order.
Work only on the currently authorized MILESTONE.md.
Use ACCEPTANCE-TESTS.md as the definition of evidence.
Maintain PLAN.md and append concise, evidence-based progress to PROGRESS.md.
Do not modify protected core laws to make implementation easier.
Continue until the milestone stop condition is met or a true external blocker prevents further independent progress.
PROMPT
)

# `--json` provides structured events for supervision and later App Server migration.
# Keep permissions conservative by default; set sandbox/approval config in local Codex config as appropriate.
echo "starting Codex; structured log: $LOG"
codex exec --json "$PROMPT" | tee "$LOG"
