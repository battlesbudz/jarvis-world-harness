# Jarvis World Harness

Jarvis World Harness is a Codex-first adaptation layer for [AAABench](https://github.com/ukanwat/aaabench), an MIT-licensed long-horizon autonomous Unreal Engine harness. The active phone-first prototype uses Babylon.js while preserving the upstream Unreal control surface as a future high-fidelity client option.

This repository preserves AAABench's Unreal/MCP/VibeUE control surface, unattended supervision lessons, visual QA philosophy, and production knowledge while replacing the benchmark demand with the Jarvis World OS product specification and milestone/evaluation loop.

## Current scope

**This repository is the development harness and the home of the World OS prototype, not the finished game.** H0 established the harness, H1 proved the deterministic text simulator, and H2 connects it to one playable mobile-web Babylon.js greybox.

## North star

Build Jarvis World OS as a living-world fantasy RPG where **the person you cared about became real**.

The active prototype renderer and moment-to-moment gameplay live in Babylon.js. Jarvis World OS is the backend simulation brain for memory, awakening, relationships, rumors, NPC agency, factions, class evolution, world consequences, and Dungeon Master pacing. Unreal remains a deferred renderer option rather than an H2 dependency.

## Development sequence

1. **Harness foundation** — protect design laws, establish Codex runtime, logging, progress, evaluation, recovery, and resumable long-horizon execution.
2. **Text simulator** — prove the living-world rules without graphics.
3. **Mobile-web engine prototype** — one small Albion village, touch/keyboard movement, simple combat, System UI, Thinker/Non-Thinker behavior.
4. **Awakening prototype** — one Non-Thinker can awaken through sustained meaningful interaction.
5. **Companion band prototype** — multiple awakenable roles, values, joining/refusal, conflict, departure.
6. **Village crisis** — autonomous world pressure resolves differently with or without player intervention.
7. **Vertical slice** — childhood opening, transition, one town, wilderness route, dungeon, awakened companion, major choice, altered ending preview.

## Core architecture

```text
Codex development harness
        |
        +--> text/world simulation tests
        |
        +--> Babylon.js mobile-web client
        |
        +--> visual/gameplay QA
        |
        +--> progress + evidence + recovery

Babylon.js client <---- validated action boundary ----> Jarvis World OS runtime
(renderer/gameplay)                                   (memory/agency/world simulation)
```

The World OS must never have unrestricted mutation power over Unreal. It proposes legal world actions; the engine/world service validates and applies them.

## Codex lifecycle

The harness uses the stable `codex exec` automation surface with JSONL events and persisted session resume.

Start or resume one Codex turn:

```bash
./bin/run-codex.sh
```

Run continuously, resuming the same persisted thread after each turn:

```bash
nohup ./bin/supervise-codex.sh >/dev/null 2>&1 &
```

Check whether the harness is actually progressing:

```bash
./bin/health-codex.sh
```

Safely restart without racing the supervisor:

```bash
./bin/restart-codex.sh
```

Optionally pass a one-time restart note:

```bash
./bin/restart-codex.sh RESUME-NOTE.md
```

Run the static H0 harness checks:

```bash
./bin/check-h0.sh
```

To pause automatic relaunches, create `.harness/codex-supervisor.pause`. To stop the supervisor cleanly, create `.harness/STOP`. Remove the marker before starting again.

Runtime state, logs, PIDs, thread IDs, and last-run metadata live under `.harness/` and are intentionally gitignored.

## Mobile-web prototype

The active H2 client lives under `web/`. Run its fast checks and production build with:

```bash
cd web
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

Run the deterministic desktop and touch traversal scenarios with:

```bash
npx playwright install chromium
npm run test:e2e
```

The browser tests drive real keyboard, pointer, and touch events through the greybox and write ignored screenshots plus machine-readable state under `.harness/evidence/h2/`.

## Important files

- `AGENTS.md` — Codex operating contract.
- `spec/WORLD-VISION.md` — concise product vision.
- `spec/CORE-LAWS.md` — protected design invariants.
- `MILESTONE.md` — the currently authorized milestone.
- `ACCEPTANCE-TESTS.md` — evidence required before advancing.
- `milestones/H1/SPEC.md` — completed deterministic text-simulator contract.
- `milestones/H2/SPEC.md` — active engine-prototype scope and evidence contract.
- `HARNESS-RULES.md` — operator/agent boundaries.
- `docs/ARCHITECTURE.md` — intended split between game clients and World OS.
- `bin/run-codex.sh` — resumable Codex runner with structured events.
- `bin/supervise-codex.sh` — single-instance unattended supervisor with backoff.
- `bin/restart-codex.sh` — race-safe manual restart.
- `bin/health-codex.sh` — progress-oriented health probe.
- `bin/check-h0.sh` — static H0 harness checks.

## AAABench relationship

This is not a claim of independent origin. AAABench provides the upstream Unreal harness concepts and substantial implementation adapted here. The upstream MIT license and attribution are preserved in `LICENSE`, `LICENSE-UPSTREAM-AAABENCH`, and `UPSTREAM.md`.
