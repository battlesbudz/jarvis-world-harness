#!/usr/bin/env bash
# Deterministic lifecycle test: no network, no real Codex account, no Unreal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-lifecycle.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/fakebin"
cp "$ROOT/bin/run-codex.sh" "$TMP/bin/run-codex.sh"
chmod +x "$TMP/bin/run-codex.sh"
TRACE="$TMP/fake-codex.args"

cat > "$TMP/fakebin/codex" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then
  echo "codex-cli fake-0.1"
  exit 0
fi
printf '%s\n' "$*" >> "$FAKE_CODEX_TRACE"
last=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--output-last-message" ]; then last="$arg"; fi
  prev="$arg"
done
[ -n "$last" ] && printf 'fake completed\n' > "$last"
if printf '%s\n' "$*" | grep -q ' resume fixture-thread-123 '; then
  printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
else
  printf '%s\n' '{"type":"thread.started","thread_id":"fixture-thread-123"}'
  printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
fi
FAKE
chmod +x "$TMP/fakebin/codex"

export PATH="$TMP/fakebin:$PATH"
export FAKE_CODEX_TRACE="$TRACE"

(
  cd "$TMP"
  bash bin/run-codex.sh >/dev/null
  [ "$(tr -d '[:space:]' < .harness/codex-session-id)" = "fixture-thread-123" ]
  sleep 1
  bash bin/run-codex.sh >/dev/null
  python3 - <<'PY'
import glob, json
logs=sorted(glob.glob('.harness/logs/codex-*.jsonl'))
assert len(logs) >= 2, logs
for path in logs:
    with open(path, encoding='utf-8') as f:
        for line in f:
            json.loads(line)
with open('.harness/last-run.json', encoding='utf-8') as f:
    last=json.load(f)
assert last['mode'] == 'resume', last
assert last['session_id'] == 'fixture-thread-123', last
assert last['exit_code'] == 0, last
PY
)

lines=$(wc -l < "$TRACE" | tr -d ' ')
[ "$lines" -eq 2 ] || { echo "expected 2 fake Codex invocations, got $lines" >&2; exit 1; }
sed -n '2p' "$TRACE" | grep -q 'resume fixture-thread-123'

echo "Codex lifecycle test passed: fresh thread persisted and exact thread resumed"
