# Upstream

Jarvis World Harness is an adaptation of AAABench by Utkarsh Kanwat / AAABench contributors:

https://github.com/ukanwat/aaabench

AAABench is MIT licensed. Preserve the upstream license and notices.

Primary upstream pieces to retain/adapt:

- Unreal MCP/VibeUE control surface
- `tools/ue_qa.py`
- project skeleton and MCP auto-start configuration
- setup-capabilities plugin provisioning
- supervisor/restart/backoff design
- engine/MCP health checks
- game-development skill packs that remain relevant

Primary pieces to replace/adapt:

- city benchmark `PROMPT.md` -> milestone/spec hierarchy
- Claude-first resume/liveness logic -> Codex App Server/structured-event logic
- benchmark contamination semantics -> engineering provenance semantics
- city-specific production demand -> Jarvis World OS laws/evals

The original AAABench implementation remains available through Git history and the fork relationship; this file makes the adaptation provenance explicit at the repository root.
