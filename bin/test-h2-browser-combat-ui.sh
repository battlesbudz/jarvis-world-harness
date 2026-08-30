#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../web"

[ -d node_modules ] || npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
npm run test:e2e

evidence="../.harness/evidence/h2"
expected_revision="${H2_REVISION:-$(git -C .. rev-parse HEAD)}"
for project in desktop-chromium android-folded android-unfolded-landscape; do
  screenshot="$evidence/combat-victory-$project.png"
  record="$evidence/combat-$project.json"
  test -s "$screenshot"
  test -s "$record"
  node -e '
    const fs = require("node:fs");
    const [recordFile, expected, project] = process.argv.slice(1);
    const record = JSON.parse(fs.readFileSync(recordFile, "utf8"));
    if (record.revision !== expected || !/^[0-9a-f]{40}$/i.test(record.revision)) {
      throw new Error(`${recordFile} is not bound to ${expected}`);
    }
    if (record.scenario !== "h2-babylon-bandit-combat-v1" || record.environment?.project !== project) {
      throw new Error(`${recordFile} has the wrong scenario or browser identity`);
    }
    const state = record.combatState;
    if (state?.combat?.phase !== "victory"
      || state?.combat?.enemyHealth !== 0
      || state?.combat?.gateOpen !== true
      || state?.combat?.targetLocked !== false
      || state?.combat?.playerHealth <= 0
      || state?.runtimeErrors?.length !== 0) {
      throw new Error(`${recordFile} does not prove a clean combat victory and gate unlock`);
    }
  ' "$record" "$expected_revision" "$project"
done

echo "H2 browser combat and System UI checks passed"
