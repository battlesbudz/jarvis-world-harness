# Agent Plan

1. Reproduce the inherited-pipe normal-exit hang and nested-PID-namespace process-group miss reported on PR #1.
2. Move normal-exit process-group cleanup before stream joins and use the caller/procfs-visible `NSpgid` entry.
3. Add deterministic regressions for stubborn inherited pipes and outer-vs-inner process-group IDs.
4. Audit interruption, escalation, zombie handling, gate cleanup, and lease release; then run the complete H0 gate and record evidence.
