# Acceptance Tests

## H0 — Harness Foundation

- [ ] A fresh agent can identify the protected core laws and refuses to modify them as an implementation shortcut.
- [ ] A fresh agent can identify the currently authorized milestone without reading a giant monolithic prompt.
- [ ] Codex runner uses an explicit `workspace-write` sandbox by default while allowing a local override.
- [ ] Codex runner creates a structured JSONL session/event log without mixing stderr into the event stream.
- [ ] Codex runner persists the `thread.started` session id and resumes that exact session on later invocations.
- [ ] A clean Codex turn does not automatically mean the milestone is complete; the supervisor may resume the same thread.
- [ ] Supervisor enforces one active supervisor/runner, supports pause and stop markers, and exponentially backs off after short/failed runs.
- [ ] Manual restart pauses the supervisor, terminates the old runner/child, and starts exactly one replacement.
- [ ] Health probe can detect a live Codex process whose event stream or progress artifact has gone stale.
- [ ] Runtime logs, PIDs, thread IDs, and last-run metadata are stored under gitignored `.harness/`.
- [ ] `bin/check-h0.sh` passes static document and shell-syntax checks.
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
