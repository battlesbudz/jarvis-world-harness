# Agent Plan

1. Reproduce PR #1's replacement-wrapper startup race against head `df93fbd`.
2. Poll with a bounded timeout for the distinct live replacement wrapper after restart returns.
3. Run the focused control-plane regression and the complete H0 gate.
4. Record evidence, publish the focused fix to PR #1, and request a fresh Codex review.
