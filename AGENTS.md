# AGENTS.md — Jarvis World Harness

You are developing **Jarvis World OS**, not demonstrating a benchmark.

## Read first

Before broad work, read in this order:

1. `spec/CORE-LAWS.md`
2. `spec/WORLD-VISION.md`
3. `MILESTONE.md`
4. `ACCEPTANCE-TESTS.md`
5. `HARNESS-RULES.md`
6. `docs/ARCHITECTURE.md`
7. relevant AAABench docs/skills for Unreal implementation

## Governing principle

Product laws and acceptance criteria are authoritative. Implementation is yours to discover.

Do not silently change a core law to make implementation easier. If a core law appears impossible, record the conflict in `BLOCKERS.md` with evidence and continue on independent work.

## Development loop

For every milestone:

1. Inspect repo reality.
2. Update `PLAN.md` with the smallest useful sequence.
3. Implement one testable slice.
4. Run the strongest practical automated checks.
5. For Unreal work, launch/play/capture evidence and inspect it yourself.
6. Diagnose failures from evidence rather than assumptions.
7. Append concise evidence to `PROGRESS.md`.
8. Continue until every acceptance criterion is evidenced or a true external blocker exists.

## World OS boundary

Keep living-world logic testable without Unreal wherever practical.

The runtime should own:
- event history
- memory and beliefs
- relationships
- awakening evidence/state
- NPC goals and agency
- rumor propagation
- faction/world pressure
- companion decisions
- Dungeon Master proposals

Unreal should own:
- rendering
- movement
- combat execution
- animation
- UI/audio/input
- pathfinding/navigation
- authoritative application of validated world actions

Do not bury core simulation rules solely in ad-hoc Blueprints.

## Verification

A feature is not complete because code exists. It is complete when evidence demonstrates the acceptance criteria.

Prefer deterministic tests for simulation rules. For visual/gameplay rules, capture screenshots/video/logs and inspect them.

Never claim an awakening, relationship consequence, autonomous crisis resolution, or persistence behavior without a reproducible trace showing why it happened.

## Safety / repo integrity

Do not rewrite protected design documents unless the milestone explicitly authorizes design work.
Do not delete upstream AAABench attribution or MIT license.
Do not commit secrets, credentials, generated engine caches, or huge derived assets.
