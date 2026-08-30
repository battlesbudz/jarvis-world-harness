# Active Milestone — H2: Mobile-Web Engine Prototype

## Goal

Connect the proven headless World OS to a small playable Babylon.js greybox and demonstrate the engine/runtime boundary through mobile movement, simple combat, System UI, and visibly distinct Thinker/Non-Thinker behavior.

## Authorized scope

H2 authorizes the narrow bridge, one small Albion village greybox, one Bio player, a minimal prototype cast, player movement, simple combat, System UI, deterministic browser scenarios, and captured evidence defined in `milestones/H2/SPEC.md`.

It does not authorize awakening gameplay, final art, procedural cities, large open-world content, production networking, multiplayer, or LLM-dependent durable state transitions.

## Required outcomes

1. The H1 deterministic simulator and causal evidence remain green.
2. A versioned, narrow bridge maps authoritative engine events into World OS inputs and World OS proposals into engine validation requests.
3. The Babylon.js game client remains authoritative for prototype physical state and rejects impossible, stale, or unauthorized proposals without partial mutation.
4. A Bio player can enter and move through one small Albion village greybox in a mobile browser.
5. Simple combat has a complete input, validation, hit/damage, defeat, and feedback loop.
6. System UI exposes the minimum playable state without displaying hidden awakening evidence.
7. Thinkers and Non-Thinkers have visibly different behavior backed by stable World OS identities and causal events.
8. Automated and visual evidence proves the bridge and playable loop end to end.

## Stop condition

H2 is complete only when every command in `milestones/H2/gate.json` passes and its evidence covers every H2 criterion in `ACCEPTANCE-TESTS.md`.

Do not begin H3 Awakening Prototype work during H2.
