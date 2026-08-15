# Agent Plan

1. Reproduce the gate-evaluator death window and trace the control/runner lease through every spawned check boundary.
2. Preserve explicit serialization leases in active checks, reject completed checks with live process groups, and require fresh lock acquisition before runner handoff.
3. Add deterministic evaluator-death, background-descendant, and escaped-session regressions, then stress the focused lifecycle suites.
4. Run the complete H0 gate, record evidence, publish the fix, and request a fresh Codex review.
