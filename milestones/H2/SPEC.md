# H2 Mobile-Web Engine Prototype Specification

## Goal

Prove that the deterministic Jarvis World OS can drive a small, playable Babylon.js prototype through a narrow validated boundary without moving simulation rules into ad-hoc client scripts.

## Authorization and boundaries

H2 authorizes one compact Albion village greybox, one Bio player, a minimal stable-identity cast, touch and keyboard/pointer movement, simple combat, minimum System UI, the browser-client/World OS bridge, deterministic browser automation, and machine-readable plus visual evidence.

H2 does not authorize awakening gameplay, final art, large-scale world generation, procedural cities, production networking, multiplayer, economy/factions, a complete quest line, or LLM-dependent durable state transitions. Placeholder geometry, materials, animation, audio, and effects are expected. Gameplay before art is a milestone requirement, not a temporary quality failure.

`spec/CORE-LAWS.md` remains protected. H1 behavior remains authoritative for living-world state. The Babylon.js game client owns rendering, movement, combat execution, collision, navigation, input, UI/audio, and application of validated physical mutations during H2. Unreal remains a deferred client option and is not an H2 runtime dependency.

## Playable slice

The greybox contains only the space needed to evaluate the loop:

- one player start and a short readable traversal route;
- one village interaction space with clearly placed role stations;
- one combat space with blocking geometry and one defeatable hostile target;
- one Thinker and one Non-Thinker visible in the required path;
- collision, navigation, camera framing, and reset behavior sufficient for repeatable browser runs.

The Bio can move and turn with mobile touch controls and keyboard/pointer controls. Movement metrics are fixed before the greybox layout is accepted. The route must be played at real player speed; direct state mutation or teleportation is not evidence.

Simple combat requires an explicit attack input, bounded attack timing, authoritative range/line-of-sight or overlap validation, damage, defeat, and readable feedback. A missed, impossible, stale, or duplicate attack cannot apply damage. Combat depth beyond this loop is outside H2.

System UI shows the minimum state needed to play and diagnose the slice: player health, interaction/target state, and combat feedback. It must not display awakening evidence, thresholds, or a player-facing awakening meter.

## Bridge contract

The bridge uses versioned, serializable envelopes. Each envelope records a stable message id, correlation id, logical/world ordering data, actor identity, message type, validated payload, and schema version. The transport may evolve, but durable semantics may not depend on delivery timing or an unlogged LLM response.

### Game client to World OS

The Babylon.js client sends authoritative prototype observations such as player movement completion, interaction, attack outcome, witnessed event, location entry, and explicit time advance. Delivery is idempotent: retrying a message with the same identity cannot append a second world event.

### World OS to game client

World OS emits proposals, never direct engine mutation. Each proposal names its causal event or trace root and the intended actor/action. The game client validates identity, permissions, current physical state, ordering/preconditions, duplicate/stale delivery, collision/range/line of sight where relevant, World Anchor constraints, and playability before applying it.

Every proposal receives one durable outcome: applied or rejected with a machine-readable reason. An applied outcome references the resulting authoritative game-client event; a rejection leaves physical state unchanged. The outcome is returned to World OS so the causal history does not claim an action the client did not apply.

Bridge failure must fail closed. Disconnects, malformed payloads, unknown identities, unsupported schema versions, and timeouts cannot silently mutate either side. Logs must make retries and rejection reasons distinguishable from successful application.

## Cognition proof

The prototype contains at least one stable-identity Thinker and one stable-identity Non-Thinker initialized in World OS from its fixture or an equivalent deterministic H2 fixture. World OS selects both actors' cognition-driven behavior and emits proposals; the game client may only validate the proposal, navigate or animate the actor, apply the legal physical result, and return the correlated outcome.

- World OS selects a visible Non-Thinker role routine between named routine locations using only bounded routine state and proposes the next routine action to the game client.
- World OS selects at least one Thinker goal- or values-backed choice, emits the corresponding proposal, and exposes the decision through causal evidence.
- Their distinction must be behavioral. Different labels, colors, or floating text alone do not pass.
- Awakening is not exercised in H2; that remains the H3 prototype.

## Required deterministic scenarios

Automated evidence must prove all of the following from a resettable start state:

1. The browser game starts at the Bio spawn, the player traverses the required route with real keyboard or touch input, and collision prevents an invalid shortcut.
2. One valid attack produces exactly one validated damage outcome and the hostile can be defeated.
3. Duplicate, stale, out-of-range, or otherwise impossible combat/proposal input is rejected with no partial physical mutation.
4. A physically possible, current proposal from a known identity that lacks permission is durably rejected, records the permission reason, and leaves physical state unchanged.
5. An authoritative game-client observation enters World OS exactly once; World OS then emits at least one deterministic proposal that the client validates and successfully applies, and the resulting authoritative client event is correlated and returned to World OS.
6. World OS selects and proposes the Non-Thinker's role-routine step and the Thinker's goal- or values-backed choice; the game client validates and performs both without reimplementing their cognition rules.
7. System UI exposes health, target/interaction state, and combat feedback while omitting awakening progress.
8. The complete path runs without a crash, softlock, browser runtime error, unresolved validation failure, or surviving test process.

## Evidence and visual QA

Machine-readable evidence belongs under ignored `.harness/evidence/h2/` and includes the scenario seed/reset id, browser and viewport identity, bridge envelopes, validation decisions, game-client outcomes, relevant World OS causal traces, final physical state, and stable correlation identifiers.

Visual evidence is captured from the running browser game, not from a free camera or generated mockup. At minimum it includes:

- a wide establishing view of the complete greybox village slice;
- the Bio traversing the required route at player height;
- combat with visible hit/damage or defeat feedback;
- the System UI during play;
- the Thinker choice and Non-Thinker routine behavior.

Evidence tooling must identify the exact project revision and scenario. Screenshots or video without matching machine-readable run evidence do not pass. Visual review must record what was inspected; file existence alone is insufficient. Automated mobile emulation covers touch and responsive behavior, while at least one operator check on the deployed URL covers the physical target phone before H2 closes.

## Executable evidence contract

`milestones/H2/gate.json` defines completion with separate checks for:

- H2 contract integrity and the complete H1 regression gate;
- bridge schema, idempotency, correlation, and fail-closed validation;
- Babylon.js project structure and deterministic fixture identity;
- browser movement, collision, combat, and System UI;
- end-to-end cognition/bridge behavior and causal evidence;
- captured mobile-browser visual evidence and runtime-log cleanliness.

Implementation commands are named before each H2 slice begins. Missing commands or evidence keep H2 incomplete; they do not permit the milestone to weaken or skip a criterion.

## Stop condition

H2 is complete only when every command in `milestones/H2/gate.json` exits successfully, every H2 criterion in `ACCEPTANCE-TESTS.md` has matching evidence, the deployed build has been checked on the target phone, and the visual evidence has been inspected. Passing the bridge or browser-foundation tests without the complete playable loop is insufficient. Do not begin H3 Awakening Prototype work during H2.
