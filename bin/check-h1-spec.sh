#!/usr/bin/env bash
# Validate the finalized H1 contract without implementing or executing H1.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import json
import re
from pathlib import Path

acceptance_path = Path("ACCEPTANCE-TESTS.md")
spec_path = Path("milestones/H1/SPEC.md")
gate_path = Path("milestones/H1/gate.json")
for path in (acceptance_path, spec_path, gate_path):
    if not path.is_file():
        raise SystemExit(f"missing finalized H1 contract file: {path}")

acceptance = acceptance_path.read_text(encoding="utf-8")
heading = "## H1 — Text Simulator"
if heading not in acceptance or "H1 — Text Simulator (draft" in acceptance:
    raise SystemExit("H1 acceptance criteria are not finalized")
h1_acceptance = acceptance.split(heading, 1)[1]
h1_acceptance = re.split(r"^## ", h1_acceptance, maxsplit=1, flags=re.MULTILINE)[0]
if len(re.findall(r"^- \[ \] ", h1_acceptance, flags=re.MULTILINE)) < 13:
    raise SystemExit("H1 acceptance criteria are incomplete")

spec = spec_path.read_text(encoding="utf-8")
required_headings = (
    "# H1 Text Simulator Specification",
    "## Authorization and boundaries",
    "## Deterministic runtime contract",
    "## Required domain model",
    "## Required causal scenarios",
    "## Persistence and trace evidence",
    "## Executable evidence contract",
    "## Stop condition",
)
for required in required_headings:
    if required not in spec:
        raise SystemExit(f"H1 specification missing section: {required}")

required_contract_terms = (
    "append-only event history",
    "Bio",
    "Thinker",
    "Non-Thinker",
    "rumor",
    "awakening_transition",
    "no public direct `awaken` command",
    "values-based refusal",
    "player takes no action",
    "Save/reload",
    "causal trace",
    "Unreal integration remains outside H1",
)
for required in required_contract_terms:
    if required not in spec:
        raise SystemExit(f"H1 specification missing contract term: {required}")

gate = json.loads(gate_path.read_text(encoding="utf-8"))
if gate.get("milestone") != "H1":
    raise SystemExit("H1 evidence manifest has the wrong milestone id")
checks = gate.get("checks")
if not isinstance(checks, list) or not checks:
    raise SystemExit("H1 evidence manifest has no checks")

expected = {
    "h1-spec": ["bash", "bin/check-h1-spec.sh"],
    "h1-events-memory": ["bash", "bin/test-h1-events-memory.sh"],
    "h1-relationships-rumors": ["bash", "bin/test-h1-relationships-rumors.sh"],
    "h1-awakening-agency": ["bash", "bin/test-h1-awakening-agency.sh"],
    "h1-world-pressure": ["bash", "bin/test-h1-world-pressure.sh"],
    "h1-persistence-traces": ["bash", "bin/test-h1-persistence-traces.sh"],
}
actual = {}
for check in checks:
    check_id = check.get("id")
    if not isinstance(check_id, str) or check_id in actual:
        raise SystemExit(f"invalid or duplicate H1 check id: {check_id!r}")
    command = check.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise SystemExit(f"invalid command for H1 check: {check_id}")
    if not isinstance(check.get("timeout_seconds"), (int, float)) or check["timeout_seconds"] <= 0:
        raise SystemExit(f"invalid timeout for H1 check: {check_id}")
    actual[check_id] = command
for check_id, command in expected.items():
    if actual.get(check_id) != command:
        raise SystemExit(f"H1 evidence manifest missing required command: {check_id}")
PY

echo "H1 specification contract checks passed"
