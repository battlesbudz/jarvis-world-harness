# Active Milestone — H0: Harness Foundation

## Goal

Convert the AAABench long-horizon Unreal harness into a Codex-first development harness for Jarvis World OS without prematurely building Jarvis World OS or the full game.

## Required outcomes

1. Product laws are separated from implementation instructions and treated as protected inputs.
2. A milestone file and acceptance-test file control what Codex is authorized to build.
3. Codex work emits machine-readable logs suitable for liveness/progress checks.
4. Codex thread identity is persisted so separate runner invocations resume the same long-horizon work.
5. The harness distinguishes process liveness from real progress.
6. A single-instance supervisor can relaunch Codex safely, back off on repeated failures, and honor pause/stop controls.
7. Manual restart cannot race the supervisor or leave duplicate Codex workers behind.
8. The architecture supports both:
   - headless World OS simulation/evals
   - Unreal/MCP visual/gameplay work
9. AAABench's useful Unreal control surface, QA tooling, supervision lessons, and MIT attribution are preserved.
10. The next milestone, **H1 Text Simulator**, is fully specified before any large open-world content generation begins.

## Stop condition

H0 is complete when `ACCEPTANCE-TESTS.md` H0 criteria are satisfied and the repository is ready for the H1 text-simulator implementation.

Do not implement the Jarvis World OS simulation, large-scale world generation, procedural cities, or full game content in H0.
