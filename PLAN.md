# Agent Plan

1. Reproduce the supervisor-lock descriptor leak while its background runner remains active.
2. Close only the child's inherited supervisor descriptor and prove a replacement supervisor can acquire its lease immediately.
3. Run the focused lifecycle suite and complete H0 gate, record evidence, publish the fix, and request a fresh Codex review.
