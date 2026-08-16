# Architecture — Jarvis World Harness

## Principle: simulation brain != renderer

The harness must let Codex develop two connected systems without conflating them.

### Jarvis World OS runtime

Prefer a standalone, deterministic/testable core for:

- event bus / world-event schema
- append-only event history
- episodic memory
- semantic beliefs
- relationship evidence/graph
- awakening evidence and transition
- NPC goals, values, refusal, joining, leaving
- rumor propagation
- town/economy/safety/faction pressure
- class/profession evolution
- Dungeon Master pacing proposals
- persistence and causal traces

LLM-assisted behavior may be added later, but world legality and durable state must not depend on unvalidated free-form text.

### Unreal Engine

Owns:

- character control
- combat execution
- physics
- animation
- camera
- audio
- UMG/System UI
- navigation/pathfinding
- world rendering
- interaction surfaces
- application of validated world actions

### Bridge

Define a narrow command/event interface.

World OS receives authoritative events such as:

`conversation`, `gift`, `attack`, `rescue`, `trade`, `death`, `quest_outcome`, `witnessed_event`, `time_advance`, `location_entered`.

World OS emits **proposals**, e.g.:

`npc_goal_changed`, `npc_refuses`, `npc_joins`, `routine_changed`, `rumor_shared`, `faction_action_proposed`, `town_pressure_changed`, `awakening_transition`, `quest_pressure_proposed`.

A validator checks identity, permissions, state preconditions, cooldowns, physical possibility, story-anchor rules, and safety/playability before Unreal applies a mutation.

## Development lanes

### Lane A — headless simulation

Fast deterministic tests; thousands of seeded histories; no renderer cost.

### Lane B — Unreal integration

AAABench MCP/VibeUE control, PIE, viewport capture, Blueprint/asset authoring, performance and visual QA.

### Lane C — evaluation

Milestone-specific tests collect evidence from A and B. No milestone passes solely because the agent says it passed.

## Harness lifecycle

H0 deliberately keeps Codex orchestration separate from the game implementation.

`bin/run-codex.sh` owns one Codex turn. It records JSONL events, captures stderr separately, persists the Codex thread id, and resumes that thread later. The runner shell and Python wrapper are Linux child subreapers: orphaned `setsid` and double-forked tools reparent to a living harness owner instead of escaping to PID 1. Namespace-aware `/proc` tracking terminates the complete executable descendant tree before stream joins, post-processing, or runner-lease release, while ignoring and reaping harmless zombies. The shell repeats this cleanup immediately after wrapper completion and in its EXIT trap, so hard wrapper death retains a full-tree fallback rather than only the original process group.

`bin/supervise-codex.sh` is the outer watchdog. It guarantees one supervisor, avoids racing an already-live runner, retries clean stops on the same thread, and exponentially backs off after short or failed runs.

Milestone evaluation holds the control and runner kernel leases across the complete active process chain: supervisor, gate evaluator, and current evidence check. The evaluator is also a child subreaper and rejects a check that returns with any executable descendant, including redirected `setsid` or double-forked work outside the original process group. If an ancestor is hard-killed, surviving work retains serialization until it exits. Before an incomplete gate can launch Codex, the supervisor closes evaluator-era descriptions and acquires both locks afresh, preserving a fail-closed fallback even if the evaluator itself dies before cleanup.

`bin/restart-codex.sh` is the operator-safe restart path. It pauses the supervisor before terminating the runner/child and releases the pause only after one replacement owns the runner lock, its wrapper owns a separate kernel lease, and matching shell/wrapper/readiness/child metadata exists. PID signalability is never treated as exit evidence because unreaped zombies remain signalable; kernel lock state is authoritative.

`bin/health-codex.sh` treats process existence, recent Codex events, and recent `PROGRESS.md` updates as separate signals. A process that exists but has stopped producing events/progress is unhealthy.

Runtime lifecycle state belongs under `.harness/` and is not source-of-truth product state.
