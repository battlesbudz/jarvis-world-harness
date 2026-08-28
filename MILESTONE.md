# Active Milestone — H1: Text Simulator

## Goal

Build a deterministic, headless Jarvis World OS simulator that proves causal living-world behavior before Unreal integration or large-scale content generation.

## Authorized scope

H1 authorizes the standalone simulation runtime, deterministic fixtures, persistence format, causal traces, and automated evidence defined in `milestones/H1/SPEC.md`.

It does not authorize Unreal assets or gameplay integration, procedural cities, large open-world content generation, production networking, or LLM-dependent state transitions.

## Required outcomes

1. Bio, Thinker, and Non-Thinker actors have stable identities and distinct cognition rules.
2. An append-only event history drives memories, beliefs, relationships, rumors, awakening, agency, and world pressure.
3. A role-based Non-Thinker can awaken through meaningful relationship evidence without a direct awakening command.
4. An awakened actor retains prior history, forms independent goals, and can refuse or oppose the Bio.
5. A town crisis evolves without player action.
6. Save/reload is deterministic and tampered or incompatible state fails closed.
7. Machine-readable causal traces explain the required outcomes.
8. The World OS only proposes actions; an authoritative validator applies legal mutations.

## Stop condition

H1 is complete only when every command in `milestones/H1/gate.json` passes and its evidence covers every H1 criterion in `ACCEPTANCE-TESTS.md`.

Unreal integration remains outside H1.
