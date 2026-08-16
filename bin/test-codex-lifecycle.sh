#!/usr/bin/env bash
# Deterministic lifecycle test: no network, no real Codex account, no Unreal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-lifecycle.XXXXXX")
cleanup() {
  set +e
  descendant=$(cat "$TMP/.harness/normal-descendant.pid" 2>/dev/null || true)
  [ -n "$descendant" ] && kill -9 "$descendant" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

command -v flock >/dev/null || { echo "flock not found on PATH" >&2; exit 127; }
mkdir -p "$TMP/bin" "$TMP/fakebin"
for f in run-codex.sh codex-process.py process_group.py; do cp "$ROOT/bin/$f" "$TMP/bin/$f"; done
chmod +x "$TMP/bin/"*
TRACE="$TMP/fake-codex.args"

cat > "$TMP/fakebin/codex" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then
  echo "codex-cli fake-0.1"
  exit 0
fi
{
  printf 'CALL'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$FAKE_CODEX_TRACE"
last=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--output-last-message" ]; then last="$arg"; fi
  prev="$arg"
done
[ -n "$last" ] && printf 'fake completed\n' > "$last"
if [ -n "${FAKE_CODEX_NORMAL_DESCENDANT_MARKER:-}" ]; then
  (
    trap '' TERM HUP
    printf '%s\n' "$BASHPID" > .harness/normal-descendant.pid
    exec >/dev/null 2>&1
    sleep 4
    touch "$FAKE_CODEX_NORMAL_DESCENDANT_MARKER"
    sleep 30
  ) &
fi
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
  # Intentionally run again immediately: log names must remain collision-resistant within one second.
  bash bin/run-codex.sh >/dev/null
  python3 - <<'PY'
import glob, json
logs=sorted(glob.glob('.harness/logs/codex-*.jsonl'))
assert len(logs) == 2, logs
assert logs[0] != logs[1]
for path in logs:
    with open(path, encoding='utf-8') as f:
        for line in f:
            json.loads(line)
with open('.harness/last-run.json', encoding='utf-8') as f:
    last=json.load(f)
assert last['mode'] == 'resume', last
assert last['session_id'] == 'fixture-thread-123', last
assert last['exit_code'] == 0, last
assert last['terminal_event'] == 'turn.completed', last
PY
)

lines=$(wc -l < "$TRACE" | tr -d ' ')
[ "$lines" -eq 2 ] || { echo "expected 2 fake Codex invocations, got $lines" >&2; exit 1; }
sed -n '2p' "$TRACE" | grep -q 'resume fixture-thread-123'

# A normally successful Codex leader cannot leave a redirected descendant that
# writes after the wrapper releases the runner lock.
normal_marker="$TMP/NORMAL_DESCENDANT_LEAKED"
(
  cd "$TMP"
  FAKE_CODEX_NORMAL_DESCENDANT_MARKER="$normal_marker" bash bin/run-codex.sh >/dev/null
)
sleep 3
[ ! -e "$normal_marker" ] || { echo "normal Codex exit leaked a worktree-mutating descendant" >&2; exit 1; }
flock -n "$TMP/.harness/codex-runner.lock" true >/dev/null 2>&1 || { echo "normal-exit cleanup did not release the runner lock" >&2; exit 1; }

echo "Codex lifecycle test passed: exact thread resume, distinct logs, and normal-exit tree cleanup"
