#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../web"

for path in \
  package.json package-lock.json index.html vite.config.ts playwright.config.ts \
  src/main.ts src/game.ts src/input.ts src/gameState.ts tests/gameState.test.ts \
  tests/e2e/playable-loop.spec.ts; do
  [ -f "$path" ] || { echo "missing H2 web foundation file: web/$path" >&2; exit 1; }
done

node -e '
const packageJson = require("./package.json");
if (!packageJson.dependencies?.["@babylonjs/core"]) throw new Error("Babylon.js dependency missing");
for (const script of ["build", "lint", "typecheck", "test", "test:e2e"]) {
  if (!packageJson.scripts?.[script]) throw new Error(`missing npm script: ${script}`);
}
'

npm ci --ignore-scripts --no-audit --no-fund
npm run lint
npm run typecheck
npm run test
npm run build

test -f dist/index.html
grep -q '<title>Jarvis World — Albion Field Test</title>' dist/index.html
echo "H2 Babylon.js web foundation checks passed"
