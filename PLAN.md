# Agent Plan

1. Audit every H0 runner, wrapper, restart, supervisor, lock, and signal handoff against current PR #1 feedback.
2. Make restart completion authoritative: zombie-safe lock acquisition plus a verified replacement wrapper/child readiness handshake.
3. Make shell fallback cleanup terminate the entire Codex process group if the Python wrapper is hard-killed.
4. Add deterministic zombie, repeated-restart, readiness, and escaped-descendant regressions.
5. Stress the focused lifecycle tests, run the complete H0 gate repeatedly, record evidence, publish one consolidated fix, and request a fresh Codex review.
