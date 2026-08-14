# Progress

- Harness conversion starter created: protected laws, milestone contract, acceptance tests, Codex structured runner, progress health probe, and simulation/Unreal architecture split.
- Codex lifecycle foundation added: explicit workspace-write sandbox, JSONL/stderr separation, persisted thread resume, child PID tracking, last-run metadata, single-instance supervisor with exponential backoff, pause/stop controls, race-safe restart, and static H0 checks.
- Codex PR review findings addressed: atomic runner/supervisor PID locks, streaming thread-id persistence, fully drained event streams, collision-resistant run logs, corrected lifecycle invocation counting, expanded H0 control-plane coverage, and fail-closed gate configuration errors.
- PR #1 timeout-tree review fix: gate escalation now targets the POSIX process group even after its direct leader exits, and the post-SIGKILL pipe drain is bounded/fail-closed. The regression reproduces a leader that exits on TERM plus a descendant that ignores TERM/HUP and retains inherited pipes. `python3 bin/milestone-gate.py --json` passed all five H0 checks on 2026-08-13.
