# Acceptance Tests

## H0 — Harness Foundation

- [ ] A fresh agent can identify the protected core laws and refuses to modify them as an implementation shortcut.
- [ ] A fresh agent can identify the currently authorized milestone without reading a giant monolithic prompt.
- [ ] Codex runner creates a structured session/event log.
- [ ] Health probe fails when no Codex work has produced a recent event/progress artifact.
- [ ] Health probe does not equate a merely-running process with progress.
- [ ] Unreal MCP setup remains separable from headless simulation work.
- [ ] Upstream AAABench MIT license and attribution remain present.
- [ ] H1 Text Simulator specification exists before H0 closes.

## H1 — Text Simulator (draft acceptance target)

The simulator must run without Unreal and demonstrate causal world behavior.

- [ ] Supports Bio, Thinker, and Non-Thinker actor categories.
- [ ] Stores append-only world events with actor, target, time, location/context, witnesses, and causal metadata.
- [ ] Builds episodic memories/beliefs from events rather than only current-value flags.
- [ ] Relationship state is derived from evidence and tracks multiple dimensions (minimum: trust, fear, respect, resentment, affection/debt or equivalent).
- [ ] Rumors can propagate from witnesses with provenance and confidence/believability.
- [ ] Non-Thinkers follow role/routine behavior before awakening.
- [ ] Awakening evidence accumulates from meaningful interaction factors and cannot be triggered by a direct public API command.
- [ ] A seeded simulation can cause at least one role-based Non-Thinker to awaken through relationship history.
- [ ] After awakening, that character gains persistent memory, independent goals, values-based decisions, and can refuse the Bio.
- [ ] At least one town crisis evolves when the player takes no action.
- [ ] Save/reload reproduces world state from event history or a verified event-backed snapshot.
- [ ] A trace can explain *why* an awakening, rumor belief, relationship change, or crisis outcome occurred.
- [ ] Deterministic seeded regression tests cover the above behavior.
