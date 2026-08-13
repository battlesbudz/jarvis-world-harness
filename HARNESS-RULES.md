# Harness Rules — Jarvis World OS

AAABench was designed as a benchmark where operator hints contaminate the result. Jarvis World Harness has a different goal: **build a game reliably while preserving autonomous diagnosis and measurable evidence**.

## Operator may

- Define or revise product requirements before a milestone begins.
- Supply design documents, reference material, assets, credentials, engine plugins, MCP tools, and hardware.
- Repair the harness itself when the runner, editor, MCP bridge, permissions, paths, or credentials fail.
- Stop unsafe/destructive work.
- Reject a milestone that does not satisfy acceptance evidence.
- Prioritize the next milestone.

## Operator should avoid during an autonomous milestone

- Handing the agent the diagnosis of an implementation bug it can observe itself.
- Editing the agent's implementation while claiming the milestone was autonomous.
- Moving acceptance criteria after seeing a failure without recording the change.

These are not forbidden forever; they are recorded because they change what was actually demonstrated.

## Agent may

- Diagnose its own implementation.
- Refactor its own code when evidence supports it.
- Use Unreal/MCP/Python/Blueprint tooling.
- Add tests, instrumentation, debug views, and simulation traces.
- Consult the provided design and production knowledge.

## Agent must not

- Change `spec/CORE-LAWS.md` to make a failing implementation pass.
- Declare success without evidence.
- Replace the living-world simulation with scripted quest flags merely to satisfy a demo.
- Give the World OS unrestricted authority to mutate Unreal state.
- Hide non-deterministic behavior behind unlogged LLM calls.

## Change log

Record any human diagnosis, acceptance-criterion change, manual code intervention, model/runtime change, or major environment change in `CONTAMINATION-LOG.md`. The name is retained from AAABench because it is useful: it makes the provenance of a result inspectable.
