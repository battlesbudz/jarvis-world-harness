#!/usr/bin/env bash
# Deterministic lifecycle test: no network, no real Codex account, no Unreal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/jwh-lifecycle.XXXXXX")
cleanup() {
  set +e
  for pid_file in normal-descendant.pid pipe-descendant.pid detached-descendant.pid; do
    descendant=$(cat "$TMP/.harness/$pid_file" 2>/dev/null || true)
    [ -n "$descendant" ] && kill -9 "$descendant" 2>/dev/null || true
  done
  rm -rf "$TMP"
}
trap cleanup EXIT

command -v flock >/dev/null || { echo "flock not found on PATH" >&2; exit 127; }
command -v timeout >/dev/null || { echo "timeout not found on PATH" >&2; exit 127; }
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
if [ "${FAKE_CODEX_PIPE_DESCENDANT:-0}" = "1" ]; then
  (
    trap '' TERM HUP
    printf '%s\n' "$BASHPID" > .harness/pipe-descendant.pid
    sleep 30
  ) &
fi
if [ -n "${FAKE_CODEX_DETACHED_MARKER:-}" ]; then
  setsid python3 -c '
import os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
Path(".harness/detached-descendant.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
fd = os.open(os.devnull, os.O_RDWR)
os.dup2(fd, 0); os.dup2(fd, 1); os.dup2(fd, 2)
time.sleep(4)
Path(os.environ["FAKE_CODEX_DETACHED_MARKER"]).touch()
time.sleep(30)
' &
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

# Cleanup must precede the stdout/stderr joins. This descendant deliberately keeps
# both inherited pipes open and ignores TERM, so the wrapper must escalate and
# return instead of waiting forever for EOF.
(
  cd "$TMP"
  FAKE_CODEX_PIPE_DESCENDANT=1 timeout 8 bash bin/run-codex.sh >/dev/null
) || { echo "normal-exit cleanup blocked on inherited Codex pipes" >&2; exit 1; }
pipe_descendant=$(cat "$TMP/.harness/pipe-descendant.pid")
if kill -0 "$pipe_descendant" 2>/dev/null; then
  state=$(awk '{print $3}' "/proc/$pipe_descendant/stat" 2>/dev/null || true)
  [ "$state" = "Z" ] || { echo "inherited-pipe descendant survived wrapper cleanup" >&2; exit 1; }
fi
flock -n "$TMP/.harness/codex-runner.lock" true >/dev/null 2>&1 || { echo "pipe cleanup did not release the runner lock" >&2; exit 1; }

# A new session/process group must not escape wrapper ownership when its streams
# are redirected. Child-subreaper adoption keeps it in the wrapper's full tree.
detached_marker="$TMP/DETACHED_DESCENDANT_LEAKED"
(
  cd "$TMP"
  FAKE_CODEX_DETACHED_MARKER="$detached_marker" timeout 8 bash bin/run-codex.sh >/dev/null
) || { echo "normal-exit cleanup failed for a detached Codex descendant" >&2; exit 1; }
sleep 3
[ ! -e "$detached_marker" ] || { echo "detached Codex descendant modified the worktree after wrapper return" >&2; exit 1; }
flock -n "$TMP/.harness/codex-runner.lock" true >/dev/null 2>&1 || { echo "detached cleanup did not release the runner lock" >&2; exit 1; }

echo "Codex lifecycle test passed: exact resume plus same-group, inherited-pipe, and detached tree cleanup"
