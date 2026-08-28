# H2 Engine Prototype Specification

## Goal

Prove that the deterministic Jarvis World OS can drive a small, playable Unreal Engine prototype through a narrow validated boundary without moving simulation rules into ad-hoc engine scripts.

## Authorization and boundaries

H2 authorizes one compact Albion village greybox, one Bio player, a minimal stable-identity cast, player movement, simple combat, minimum System UI, the Unreal/World OS bridge, deterministic PIE automation, and machine-readable plus visual evidence.

H2 does not authorize awakening gameplay, final art, large-scale world generation, procedural cities, production networking, multiplayer, economy/factions, a complete quest line, or LLM-dependent durable state transitions. Placeholder geometry, materials, animation, audio, and effects are expected. Gameplay before art is a milestone requirement, not a temporary quality failure.

`spec/CORE-LAWS.md` remains protected. H1 behavior remains authoritative for living-world state. Unreal owns rendering, movement, combat execution, physics, navigation, input, UI/audio, and application of validated physical mutations.

## Playable slice

The greybox contains only the space needed to evaluate the loop:

- one player start and a short readable traversal route;
- one village interaction space with clearly placed role stations;
- one combat space with blocking geometry and one defeatable hostile target;
- one Thinker and one Non-Thinker visible in the required path;
- collision, navigation, camera framing, and reset behavior sufficient for repeatable PIE runs.

The Bio can move and turn with keyboard/mouse and gamepad-compatible actions. Movement metrics are fixed before the greybox layout is accepted. The route must be played at real player speed; editor-camera traversal is not evidence.

Simple combat requires an explicit attack input, bounded attack timing, authoritative range/line-of-sight or overlap validation, damage, defeat, and readable feedback. A missed, impossible, stale, or duplicate attack cannot apply damage. Combat depth beyond this loop is outside H2.

System UI shows the minimum state needed to play and diagnose the slice: player health, interaction/target state, and combat feedback. It must not display awakening evidence, thresholds, or a player-facing awakening meter.

## Bridge contract

The bridge uses versioned, serializable envelopes. Each envelope records a stable message id, correlation id, logical/world ordering data, actor identity, message type, validated payload, and schema version. The transport may evolve, but durable semantics may not depend on delivery timing or an unlogged LLM response.

### Engine to World OS

Unreal sends authoritative observations such as player movement completion, interaction, attack outcome, witnessed event, location entry, and explicit time advance. Delivery is idempotent: retrying a message with the same identity cannot append a second world event.

### World OS to engine

World OS emits proposals, never direct engine mutation. Each proposal names its causal event or trace root and the intended actor/action. Unreal validates identity, permissions, current physical state, ordering/preconditions, duplicate/stale delivery, collision/range/line of sight where relevant, World Anchor constraints, and playability before applying it.

Every proposal receives one durable outcome: applied or rejected with a machine-readable reason. An applied outcome references the resulting authoritative engine event; a rejection leaves physical state unchanged. The outcome is returned to World OS so the causal history does not claim an action the engine did not apply.

Bridge failure must fail closed. Disconnects, malformed payloads, unknown identities, unsupported schema versions, and timeouts cannot silently mutate either side. Logs must make retries and rejection reasons distinguishable from successful application.

## Cognition proof

The prototype contains at least one stable-identity Thinker and one stable-identity Non-Thinker sourced from the World OS fixture or an equivalent deterministic H2 fixture.

- The Non-Thinker follows a visible role routine between named routine locations using only bounded routine state.
- The Thinker makes at least one visible goal- or values-backed choice exposed by causal evidence.
- Their distinction must be behavioral. Different labels, colors, or floating text alone do not pass.
- Awakening is not exercised in H2; that remains the H3 prototype.

## Required deterministic scenarios

Automated evidence must prove all of the following from a resettable start state:

1. PIE starts at the Bio spawn, the player traverses the required route, and collision prevents an invalid shortcut.
2. One valid attack produces exactly one validated damage outcome and the hostile can be defeated.
3. Duplicate, stale, out-of-range, or otherwise impossible combat/proposal input is rejected with no partial physical mutation.
4. An authoritative engine observation enters World OS once, produces a traceable proposal where applicable, and receives one correlated engine outcome.
5. The Non-Thinker completes a role-routine step while the Thinker performs a goal- or values-backed choice.
6. System UI exposes health, target/interaction state, and combat feedback while omitting awakening progress.
7. The complete path runs without a crash, softlock, Blueprint runtime error, unresolved validation failure, or surviving test process.

## Evidence and visual QA

Machine-readable evidence belongs under ignored `.harness/evidence/h2/` and includes the scenario seed/reset id, bridge envelopes, validation decisions, engine outcomes, relevant World OS causal traces, final physical state, and stable correlation identifiers.

Visual evidence is captured from PIE, not solely from the editor camera. At minimum it includes:

- a wide establishing view of the complete greybox village slice;
- the Bio traversing the required route at player height;
- combat with visible hit/damage or defeat feedback;
- the System UI during play;
- the Thinker choice and Non-Thinker routine behavior.

Evidence tooling must identify the exact project revision and scenario. Screenshots or video without matching machine-readable run evidence do not pass. Visual review must record what was inspected; file existence alone is insufficient.

## Executable evidence contract

`milestones/H2/gate.json` defines completion with separate checks for:

- H2 contract integrity and the complete H1 regression gate;
- bridge schema, idempotency, correlation, and fail-closed validation;
- Unreal project structure and deterministic fixture identity;
- PIE movement, collision, combat, and System UI;
- end-to-end cognition/bridge behavior and causal evidence;
- captured PIE visual evidence and runtime-log cleanliness.

Implementation commands are named before H2 begins. Missing commands or evidence keep H2 incomplete; they do not permit the milestone to weaken or skip a criterion.

## Stop condition

H2 is complete only when every command in `milestones/H2/gate.json` exits successfully, every H2 criterion in `ACCEPTANCE-TESTS.md` has matching evidence, and the visual evidence has been inspected. Passing headless bridge tests without a playable PIE run is insufficient. Do not begin H3 Awakening Prototype work during H2.
