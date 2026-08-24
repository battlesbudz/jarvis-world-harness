# H1 Text Simulator Specification

## Goal

Build a deterministic, headless Jarvis World OS simulator that proves causal living-world behavior before any Unreal integration or large-scale content generation begins.

## Authorization and boundaries

H1 authorizes only the standalone simulation runtime, deterministic fixtures, persistence format, causal traces, and their automated tests. It does not authorize Unreal assets or gameplay integration, procedural cities, large open-world content generation, production networking, or LLM-dependent state transitions.

`spec/CORE-LAWS.md` remains protected. The simulator may refine implementation details but may not weaken those laws. Jarvis World OS proposes actions; an authoritative validator applies only legal actions.

## Deterministic runtime contract

- Every run accepts an explicit seed and advances through logical ticks rather than wall-clock time.
- The same initial state, seed, and ordered inputs produce the same ordered events, derived state, persistence hash, and causal trace.
- Stable ordering resolves simultaneous proposals; iteration order, process timing, network access, and unlogged LLM output may not affect durable state.
- Runtime state is derived from an append-only event history or from a verified event-backed snapshot plus its subsequent events.
- Invalid proposals fail without partially mutating durable state and emit inspectable rejection evidence.

## Required domain model

### Actors

- A **Bio** is conscious, remembers Earth immediately, and can provide meaningful soul-pattern contact.
- A **Thinker** is conscious and has persistent memory, beliefs, goals, values, relationships, and agency.
- A **Non-Thinker** follows a role and routine before awakening. Any meaningful role-based Non-Thinker can potentially awaken; no fixture may encode a canonical first awakened character.

Each actor has a stable identity and category. Awakening changes cognition/personhood state without replacing that identity or erasing prior causal history.

### Events and history

Every durable world event records at least:

- stable event id, schema version, logical tick, and event type;
- actor, target or targets, location/context, and witnesses;
- causal parent event ids or an explicit root-input reference;
- validated payload and deterministic ordering metadata.

Events are append-only. Corrections are new events rather than edits to prior history.

### Memory, beliefs, and relationships

- Episodic memories reference source events and record the actor's perspective.
- Beliefs record provenance and confidence; they are not omniscient copies of world truth.
- Relationship state is derived from evidence and has multiple dimensions, including trust, fear, respect, resentment, and affection/debt or documented equivalents.
- A relationship change exposes the contributing events and deterministic rule or calculation.

### Rumors

A rumor carries the source event, teller-to-listener provenance chain, and confidence/believability. Propagation is limited by witness knowledge, relationships, and deterministic rules; a listener cannot acquire untraceable knowledge.

### Routines, awakening, and agency

- An unawakened Non-Thinker follows role/routine behavior and uses only the bounded state that behavior requires.
- Awakening evidence comes from repeated meaningful interaction factors such as attention, shared danger, protection, trust, emotional weight, and narrative relevance.
- There is no public direct `awaken` command or player-facing awakening meter.
- Crossing the deterministic threshold emits an `awakening_transition` event with contributing evidence.
- After awakening, the same actor gains persistent memory, independent goals and values, and may refuse, leave, oppose, retire, change careers, or pursue personal aims.

### World pressure and validation

At least one town crisis changes over time without player action. Actor decisions and world pressure generate proposals; the validator checks identity, permissions, preconditions, cooldowns, physical possibility, World Anchor constraints, and playability before emitting applied or rejected events.

## Required causal scenarios

Deterministic seeded regressions must prove all of the following:

1. A witnessed interaction creates perspective-correct memory, belief, and multidimensional relationship consequences.
2. A rumor propagates through at least two actors while retaining provenance and changing confidence deterministically.
3. A role-based Non-Thinker follows routine behavior, accumulates meaningful relationship evidence, and awakens without a direct awakening command.
4. The awakened actor retains pre-awakening history, forms an independent goal, and makes at least one values-based refusal or opposing choice toward the Bio.
5. A town crisis evolves through multiple logical ticks when the player takes no action.
6. Save/reload at an intermediate tick produces the same later events, derived state, and final digest as an uninterrupted run.
7. A causal trace explains why an awakening, rumor belief, relationship change, refusal, and crisis outcome occurred.

## Persistence and trace evidence

The persistence format is versioned and records the seed, logical tick, append-only event history or verified snapshot boundary, and enough metadata to reproduce deterministic ordering. Loading invalid, incompatible, or tampered state fails closed.

Tests must emit machine-readable traces under ignored `.harness/` evidence storage. Each trace identifies inputs, seed, relevant events, rule decisions, resulting state, and a stable digest; a prose-only explanation is not acceptance evidence.

## Executable evidence contract

`milestones/H1/gate.json` is the future H1 completion gate. It requires separate checks for:

- specification integrity;
- event history, memory, and beliefs;
- relationships and rumor provenance;
- awakening and post-awakening agency;
- autonomous world pressure;
- persistence determinism and causal traces.

The implementation test commands are intentionally named before H1 begins. Their absence or failure keeps H1 incomplete; it is not evidence that H0 may implement the simulator.

## Stop condition

H1 is complete only when every command in `milestones/H1/gate.json` exits successfully and the evidence covers every finalized H1 acceptance criterion in `ACCEPTANCE-TESTS.md`. Passing unit tests without the required seeded end-to-end traces is insufficient. Unreal integration remains outside H1.
