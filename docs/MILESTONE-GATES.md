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

The supervisor explicitly delegates its control/runner serialization leases to the gate evaluator and each active evidence check. The leases therefore survive a hard-killed supervisor or evaluator while evidence is still running. The Linux evaluator acts as a child subreaper, so `setsid` and double-forked work is adopted, tracked across PID namespaces, terminated, and causes the check to fail; zombie-only descendants are ignored and reaped because they hold no descriptors and cannot execute. After an incomplete evaluation, fresh lock acquisition remains mandatory before runner handoff, so lease-bearing work still blocks safely if the evaluator itself is hard-killed before cleanup.

## H0 evidence

H0 deliberately uses tests that do not require a real Codex account or Unreal installation:

- static harness/document/shell checks;
- a finalized H1 Text Simulator specification and future evidence manifest, checked without authorizing H1 implementation;
- a fake-Codex lifecycle test proving exact thread persistence/resume, collision-resistant logs, and parseable JSONL/last-run metadata;
- a control-plane regression test proving atomic runner/supervisor/restart locks, an independent wrapper kernel lease, interrupted-turn continuity, serialized gate/restart handoffs, stale-event health detection, fail-closed malformed-gate handling, zombie-safe repeated restart, wrapper/child readiness, and process-group cleanup after hard wrapper death.

Later milestones can add simulation tests, saved causal traces, Unreal PIE tests, screenshots, performance captures, or other deterministic evidence. The manifest is the executable definition of "done"; prose remains the product definition.
