#!/usr/bin/env bash
# Validate the finalized phone-first H2 contract without executing gameplay.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import json
import re
from pathlib import Path

acceptance_path = Path("ACCEPTANCE-TESTS.md")
spec_path = Path("milestones/H2/SPEC.md")
gate_path = Path("milestones/H2/gate.json")
for path in (acceptance_path, spec_path, gate_path):
    if not path.is_file():
        raise SystemExit(f"missing finalized H2 contract file: {path}")

acceptance = acceptance_path.read_text(encoding="utf-8")
heading = "## H2 — Mobile-Web Engine Prototype"
if heading not in acceptance or "H2 — Mobile-Web Engine Prototype (draft" in acceptance:
    raise SystemExit("H2 acceptance criteria are not finalized")
h2_acceptance = acceptance.split(heading, 1)[1]
h2_acceptance = re.split(r"^## ", h2_acceptance, maxsplit=1, flags=re.MULTILINE)[0]
criteria = re.findall(r"^- \[([ x])\] ", h2_acceptance, flags=re.MULTILINE)
if len(criteria) < 14:
    raise SystemExit("H2 acceptance criteria are incomplete")

spec = spec_path.read_text(encoding="utf-8")
required_headings = (
    "# H2 Mobile-Web Engine Prototype Specification",
    "## Authorization and boundaries",
    "## Playable slice",
    "## Bridge contract",
    "## Cognition proof",
    "## Required deterministic scenarios",
    "## Evidence and visual QA",
    "## Executable evidence contract",
    "## Stop condition",
)
for required in required_headings:
    if required not in spec:
        raise SystemExit(f"H2 specification missing section: {required}")

required_contract_terms = (
    "one compact Albion village greybox",
    "World OS emits proposals, never direct engine mutation",
    "idempotent",
    "applied or rejected",
    "physical state unchanged",
    "System UI",
    "player-facing awakening meter",
    "Thinker",
    "Non-Thinker",
    "World OS selects both actors' cognition-driven behavior and emits proposals",
    "physically possible, current proposal from a known identity that lacks permission",
    "World OS then emits at least one deterministic proposal that the client validates and successfully applies",
    "Awakening is not exercised in H2",
    "captured from the running browser game",
    "mobile touch controls and keyboard/pointer controls",
    "operator check on the deployed URL",
    "Do not begin H3 Awakening Prototype work during H2",
)
for required in required_contract_terms:
    if required not in spec:
        raise SystemExit(f"H2 specification missing contract term: {required}")

gate = json.loads(gate_path.read_text(encoding="utf-8"))
if gate.get("milestone") != "H2":
    raise SystemExit("H2 evidence manifest has the wrong milestone id")
checks = gate.get("checks")
if not isinstance(checks, list) or not checks:
    raise SystemExit("H2 evidence manifest has no checks")

expected = {
    "h2-spec": ["bash", "bin/check-h2-spec.sh"],
    "h1-regression": ["env", "MILESTONE_ID=H1", "python3", "bin/milestone-gate.py", "--json", "--no-record"],
    "h2-bridge-contract": ["bash", "bin/test-h2-bridge-contract.sh"],
    "h2-web-foundation": ["bash", "bin/test-h2-web-foundation.sh"],
    "h2-browser-traversal": ["bash", "bin/test-h2-browser-traversal.sh"],
    "h2-browser-combat-ui": ["bash", "bin/test-h2-browser-combat-ui.sh"],
    "h2-cognition-bridge": ["bash", "bin/test-h2-cognition-bridge.sh"],
    "h2-visual-evidence": ["bash", "bin/test-h2-visual-evidence.sh"],
}
actual = {}
for check in checks:
    check_id = check.get("id")
    if not isinstance(check_id, str) or check_id in actual:
        raise SystemExit(f"invalid or duplicate H2 check id: {check_id!r}")
    command = check.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise SystemExit(f"invalid command for H2 check: {check_id}")
    if not isinstance(check.get("timeout_seconds"), (int, float)) or check["timeout_seconds"] <= 0:
        raise SystemExit(f"invalid timeout for H2 check: {check_id}")
    actual[check_id] = command
for check_id, command in expected.items():
    if actual.get(check_id) != command:
        raise SystemExit(f"H2 evidence manifest missing required command: {check_id}")
PY

echo "H2 specification contract checks passed"
