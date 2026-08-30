# Codex Visual Playtest

The deterministic Playwright suite proves known controls and routes. The Codex visual playtest proves a different property: Codex can inspect the rendered world, choose an action, observe the result, and continue without receiving a scripted route or hidden game state.

## Trust boundary

Codex receives only:

- the current Chromium screenshot;
- the visible objective and goal stated in its prompt;
- a fixed schema of bounded keyboard, pointer, wait, and finish actions;
- the public summary of its own previous action;
- rejection of a premature `finish` claim.

Codex does not receive source files, terminal access, web search, DOM or accessibility extraction, `window.__JARVIS_H2__`, coordinates, checkpoint names, or a predetermined action sequence. Every Codex JSONL event is inspected; a tool call invalidates the run.

Playwright is only the actuator and camera. A private evaluator reads authoritative game state after each action and is solely responsible for pass/fail. The final artifact records the tested revision, screenshots, decisions, bounded inputs, model events, private evaluations, final state, and Playwright trace.

## Run locally

From `web/`:

```bash
npm ci
npx playwright install --with-deps chromium
OPENAI_API_KEY=... npm run test:codex-operator
```

Do not commit or print the API key. `H2_BASE_URL` may target a deployed build; otherwise the runner owns a temporary local Vite server. `H2_CODEX_MAX_STEPS` sets the action budget from 1 through 40, and `H2_CODEX_MODEL` optionally pins the Codex model.

Evidence is written under `.harness/evidence/h2/codex-operator/`, which is ignored by git.
