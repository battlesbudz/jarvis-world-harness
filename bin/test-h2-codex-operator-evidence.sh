#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../web"
node tools/codex-operator/verify-report.mjs

