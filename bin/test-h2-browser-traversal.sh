#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../web"

[ -d node_modules ] || npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
npm run test:e2e

evidence="../.harness/evidence/h2"
expected_revision="${H2_REVISION:-$(git -C .. rev-parse HEAD)}"
for project in desktop-chromium android-folded android-unfolded-landscape; do
  case "$project" in
    desktop-chromium) expected_width=1280; expected_height=720 ;;
    android-folded) expected_width=360; expected_height=800 ;;
    android-unfolded-landscape) expected_width=800; expected_height=600 ;;
  esac
  test -s "$evidence/establishing-$project.png"
  test -s "$evidence/blocked-shortcut-$project.png"
  test -s "$evidence/route-complete-$project.png"
  test -s "$evidence/traversal-$project.json"
  node -e '
    const fs = require("node:fs");
    const [file, expected, project, width, height] = process.argv.slice(1);
    const evidence = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!/^[0-9a-f]{40}$/i.test(evidence.revision) || evidence.revision !== expected) {
      throw new Error(`${file} revision ${evidence.revision} does not match ${expected}`);
    }
    if (evidence.environment?.project !== project
      || !evidence.environment?.browser?.name
      || !evidence.environment?.browser?.version
      || evidence.environment?.viewport?.width !== Number(width)
      || evidence.environment?.viewport?.height !== Number(height)) {
      throw new Error(`${file} does not identify the expected browser environment`);
    }
  ' "$evidence/traversal-$project.json" "$expected_revision" "$project" "$expected_width" "$expected_height"
done

echo "H2 browser traversal checks passed"
