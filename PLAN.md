# Agent Plan

1. Reproduce detached `setsid` and double-fork escapes from both Codex turns and successful milestone checks.
2. Establish Linux child-subreaper ownership at the runner shell, Codex wrapper, and gate evaluator boundaries.
3. Track and terminate the full namespace-aware descendant tree before stream joins, evidence acceptance, post-processing, or lease release.
4. Cover normal exit, timeout, hard-wrapper death, detached sessions, double forks, inherited/redirected pipes, zombies, and restart races; then run the complete H0 gate and request a clean review.
