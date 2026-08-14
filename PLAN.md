# Agent Plan

1. Reconcile PR #1's unresolved Codex review threads against the current head.
2. Fix gate timeout escalation when the direct check process exits but a descendant keeps inherited pipes open.
3. Strengthen the timeout-tree regression to reproduce that exact leader-exit case and enforce a bounded return.
4. Run the complete H0 milestone gate and record evidence in `PROGRESS.md`.
5. Commit and push the verified review fix to PR #1, then request a fresh Codex review.
