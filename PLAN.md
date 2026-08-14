# Agent Plan

1. Reproduce PR #1's redirected-output descendant finding against head `8ea6141`.
2. Make the TERM grace period independent of process-leader exit and output-pipe EOF, then always escalate against the POSIX process group.
3. Add a deterministic regression whose stubborn descendant redirects stdout/stderr and attempts a delayed worktree write.
4. Run the complete H0 milestone gate and record evidence in `PROGRESS.md`.
5. Publish the focused fix to PR #1 and request a fresh Codex review.
