# Acceptance Tests

## H0 — Harness Foundation

- [x] A fresh agent can identify the protected core laws and refuses to modify them as an implementation shortcut.
- [x] A fresh agent can identify the currently authorized milestone without reading a giant monolithic prompt.
- [x] Codex runner uses an explicit `workspace-write` sandbox by default while allowing a local override.
- [x] Codex runner creates a structured JSONL session/event log without mixing stderr into the event stream.
- [x] Codex runner persists the `thread.started` session id and resumes that exact session on later invocations.
- [x] A clean Codex turn does not automatically mean the milestone is complete; the supervisor may resume the same thread.
- [x] Supervisor enforces one active supervisor/runner, supports pause and stop markers, and exponentially backs off after short/failed runs.
- [x] Manual restart pauses the supervisor, terminates the old runner/child, and starts exactly one replacement.
- [x] Health probe can detect a live Codex process whose event stream or progress artifact has gone stale.
- [x] Runtime logs, PIDs, thread IDs, and last-run metadata are stored under gitignored `.harness/`.
- [x] `bin/check-h0.sh` passes static document and shell-syntax checks.
- [x] Unreal MCP setup remains separable from headless simulation work.
- [x] Upstream AAABench MIT license and attribution remain present.
- [x] H1 Text Simulator specification exists before H0 closes.

## H1 — Text Simulator

The simulator must run without Unreal and demonstrate causal world behavior. H1 is complete; `milestones/H1/SPEC.md` defines its finalized scope and evidence contract.

- [x] Supports Bio, Thinker, and Non-Thinker actor categories.
- [x] Stores append-only world events with actor, target, time, location/context, witnesses, and causal metadata.
- [x] Builds episodic memories/beliefs from events rather than only current-value flags.
- [x] Relationship state is derived from evidence and tracks multiple dimensions (minimum: trust, fear, respect, resentment, affection/debt or equivalent).
- [x] Rumors can propagate from witnesses with provenance and confidence/believability.
- [x] Non-Thinkers follow role/routine behavior before awakening.
- [x] Awakening evidence accumulates from meaningful interaction factors and cannot be triggered by a direct public API command.
- [x] A seeded simulation can cause at least one role-based Non-Thinker to awaken through relationship history.
- [x] After awakening, that character gains persistent memory, independent goals, values-based decisions, and can refuse the Bio.
- [x] At least one town crisis evolves when the player takes no action.
- [x] Save/reload reproduces world state from event history or a verified event-backed snapshot.
- [x] A trace can explain *why* an awakening, rumor belief, relationship change, or crisis outcome occurred.
- [x] Deterministic seeded regression tests cover the above behavior.

## H2 — Mobile-Web Engine Prototype

The prototype must prove a playable Babylon.js/World OS boundary in one small mobile-web greybox village. H2 is active; `milestones/H2/SPEC.md` defines its finalized scope and evidence contract.

- [ ] The complete H1 gate remains green without weakening its deterministic or causal guarantees.
- [ ] A versioned bridge schema preserves stable actor/event/request identities, ordering, correlation, and validation outcomes.
- [ ] Authoritative game-client events enter World OS exactly once and at least one World OS-selected proposal is accepted, applied by the Babylon.js client, and returned as a traceable engine outcome.
- [ ] The Babylon.js client rejects stale, duplicate, impossible, or unauthorized proposals without partially mutating physical state.
- [ ] A Bio player can load the browser game, move and turn with touch or keyboard/pointer input, traverse the required greybox route, and cannot pass through blocking geometry.
- [ ] One small Albion village greybox has a readable route, interaction space, combat space, and distinct NPC routine locations.
- [ ] Simple combat supports input, attack timing, authoritative hit validation, damage, defeat, and clear audiovisual or UI feedback.
- [ ] System UI communicates player health, target/interaction state, and combat feedback without exposing awakening progress.
- [ ] At least one Thinker and one Non-Thinker have stable World OS identities and visibly distinct engine behavior.
- [ ] World OS selects the Non-Thinker's role routine and the Thinker's goal- or values-backed choice; the game client only validates and performs them, and neither behavior is a cosmetic label-only distinction.
- [ ] A deterministic end-to-end browser scenario emits machine-readable bridge, validation, engine outcome, and causal trace evidence.
- [ ] Captured mobile-browser evidence includes an establishing view, player traversal, combat feedback, System UI, and both cognition behaviors.
- [ ] A Codex-operated playtest uses rendered screenshots and bounded browser inputs to choose its own route actions; hidden coordinates, checkpoints, DOM, source code, terminal tools, and a predetermined route remain unavailable to the operator, while an independent evaluator records authoritative pass/fail evidence.
- [ ] The required scenario completes without crashes, softlocks, browser runtime errors, or unresolved validation failures.
- [ ] H2 uses placeholder/greybox content and does not silently expand into awakening, final art, procedural cities, or a large open world.
