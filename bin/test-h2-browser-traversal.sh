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
  test -s "$evidence/collision-$project.json"
  test -s "$evidence/route-complete-$project.png"
  test -s "$evidence/traversal-$project.json"
  node -e '
    const fs = require("node:fs");
    const [traversalFile, collisionFile, expected, project, width, height] = process.argv.slice(1);
    const traversal = JSON.parse(fs.readFileSync(traversalFile, "utf8"));
    const collision = JSON.parse(fs.readFileSync(collisionFile, "utf8"));
    for (const [file, record] of [[traversalFile, traversal], [collisionFile, collision]]) {
      if (!/^[0-9a-f]{40}$/i.test(record.revision) || record.revision !== expected) {
        throw new Error(`${file} revision ${record.revision} does not match ${expected}`);
      }
      if (record.environment?.project !== project
        || !record.environment?.browser?.name
        || !record.environment?.browser?.version
        || record.environment?.viewport?.width !== Number(width)
        || record.environment?.viewport?.height !== Number(height)) {
        throw new Error(`${file} does not identify the expected browser environment`);
      }
    }
    if (collision.scenario !== "h2-babylon-blocked-shortcut-v1"
      || collision.screenshot !== `blocked-shortcut-${project}.png`
      || collision.state?.resetId !== 1
      || !(collision.state?.collisionCount >= 1)
      || typeof collision.state?.position?.z !== "number"
      || collision.state?.runtimeErrors?.length !== 0) {
      throw new Error(`${collisionFile} does not prove the linked collision scenario`);
    }
  ' "$evidence/traversal-$project.json" "$evidence/collision-$project.json" "$expected_revision" "$project" "$expected_width" "$expected_height"
done

echo "H2 browser traversal checks passed"
