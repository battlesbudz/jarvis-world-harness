# Agent Plan

1. Reproduce the lost gate-serialization leases after hard and graceful supervisor death.
2. Close only private fd 7 for gate evaluation, retain fd 8/9 until the evaluator exits, and prove all three release at the correct boundaries.
3. Run the focused lifecycle suite and complete H0 gate, record evidence, publish the fix, and request a fresh Codex review.
