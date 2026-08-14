# Agent Plan

1. Bind restart readiness to the exact replacement shell it spawned, not any healthy runner that appears during the polling window.
2. Add a deterministic regression where the intended replacement fails and an unrelated direct runner starts concurrently.
3. Stress the focused lifecycle tests, run the complete H0 gate, record evidence, publish the fix, and request a fresh Codex review.
