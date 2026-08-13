# Milestone Gates

Jarvis World Harness does not treat an agent saying "done" or a process exiting cleanly as milestone completion.

## Contract

Each authorized milestone has a committed evidence manifest at:

```text
milestones/<MILESTONE_ID>/gate.json
```

`MILESTONE.md` names the active milestone. `bin/milestone-gate.py` resolves that id, loads the matching manifest, executes every required check, and records the result under ignored `.harness/` runtime state.

A gate passes only when **all** required checks return exit code `0`.

Exit codes from `bin/milestone-gate.py`:

- `0` — every required check passed.
- `1` — the gate evaluated correctly but one or more checks failed; more milestone work remains.
- `2` — the gate could not be evaluated safely because its configuration or infrastructure is invalid, including malformed commands or executables that cannot be launched.

The supervisor treats exit `2` as fail-closed and stops autonomous work rather than repeatedly launching Codex into an unmeasurable milestone.

## Supervisor behavior

The Codex supervisor evaluates the milestone gate only when no Codex runner is actively editing the worktree.

```text
runner active -> do not evaluate; wait
runner idle -> evaluate gate
  pass -> stop supervisor
  incomplete -> launch/resume Codex
  gate error -> stop supervisor (fail closed)
runner exits -> evaluate again before deciding whether to relaunch
```

This prevents an evaluation command from racing Codex while files are changing.

## H0 evidence

H0 deliberately uses tests that do not require a real Codex account or Unreal installation:

- static harness/document/shell checks;
- a fake-Codex lifecycle test proving exact thread persistence/resume, collision-resistant logs, and parseable JSONL/last-run metadata;
- a control-plane regression test proving atomic runner/supervisor locks, interrupted-turn restart continuity, stale-event health detection, and fail-closed malformed-gate handling.

Later milestones can add simulation tests, saved causal traces, Unreal PIE tests, screenshots, performance captures, or other deterministic evidence. The manifest is the executable definition of "done"; prose remains the product definition.
