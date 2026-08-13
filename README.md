# Jarvis World Harness

Jarvis World Harness is a Codex-first adaptation layer for [AAABench](https://github.com/ukanwat/aaabench), an MIT-licensed long-horizon autonomous Unreal Engine harness.

This repository preserves AAABench's Unreal/MCP/VibeUE control surface, unattended supervision, visual QA philosophy, and production knowledge while replacing the benchmark demand with the Jarvis World OS product specification and milestone/evaluation loop.

## North star

Build Jarvis World OS as a living-world fantasy RPG where **the person you cared about became real**.

The game renderer and moment-to-moment gameplay live in Unreal Engine. Jarvis World OS is the backend simulation brain for memory, awakening, relationships, rumors, NPC agency, factions, class evolution, world consequences, and Dungeon Master pacing.

## Development sequence

1. **Harness foundation** — protect design laws, establish Codex runtime, logging, progress, evaluation, and recovery.
2. **Text simulator** — prove the living-world rules without graphics.
3. **Engine prototype** — one small Albion village, player movement, simple combat, System UI, Thinker/Non-Thinker behavior.
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
        +--> Unreal Engine through MCP + VibeUE
        |
        +--> visual/gameplay QA
        |
        +--> progress + evidence + recovery

Unreal Engine <---- validated action boundary ----> Jarvis World OS runtime
(renderer/gameplay)                              (memory/agency/world simulation)
```

The World OS must never have unrestricted mutation power over Unreal. It proposes legal world actions; the engine/world service validates and applies them.

## Important files

- `AGENTS.md` — Codex operating contract.
- `spec/WORLD-VISION.md` — concise product vision.
- `spec/CORE-LAWS.md` — protected design invariants.
- `MILESTONE.md` — the currently authorized milestone.
- `ACCEPTANCE-TESTS.md` — evidence required before advancing.
- `HARNESS-RULES.md` — operator/agent boundaries.
- `docs/ARCHITECTURE.md` — intended split between Unreal and World OS.
- `bin/run-codex.sh` — first-pass Codex runner.
- `bin/health-codex.sh` — progress-oriented health probe.

## AAABench relationship

This is not a claim of independent origin. AAABench provides the upstream Unreal harness concepts and substantial implementation adapted here. The upstream MIT license and attribution are preserved in `LICENSE` and `UPSTREAM.md`.
