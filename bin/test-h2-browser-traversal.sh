#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../web"

[ -d node_modules ] || npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
npm run test:e2e

evidence="../.harness/evidence/h2"
for project in desktop-chromium android-folded android-unfolded-landscape; do
  test -s "$evidence/establishing-$project.png"
  test -s "$evidence/blocked-shortcut-$project.png"
  test -s "$evidence/route-complete-$project.png"
  test -s "$evidence/traversal-$project.json"
done

echo "H2 browser traversal checks passed"
