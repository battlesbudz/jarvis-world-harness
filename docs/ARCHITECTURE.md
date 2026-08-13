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
