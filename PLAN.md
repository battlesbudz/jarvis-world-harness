# Agent Plan

1. Reproduce the two newest PR #1 findings against head `c2dda71`.
2. Preserve the Codex wrapper's full TERM grace period and always escalate its child process group, even after redirected streams close.
3. Persist the Python wrapper PID and give the actual wrapper a safe stop-control path so restart can recover its inherited runner lock after the shell dies without signaling recycled PID metadata.
4. Add deterministic redirected-descendant and hard-killed-runner recovery regressions.
5. Run the complete H0 gate, record evidence, publish the fixes to PR #1, and request a fresh Codex review.
