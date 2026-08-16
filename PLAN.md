# Agent Plan

1. Reproduce evaluator SIGKILL with an active check whose detached child closes every inherited descriptor.
2. Keep the supervisor alive as the outer gate subreaper and hold serialization until adopted descendants are terminated.
3. Remove stale numeric process-group signaling after namespace-aware descendant cleanup succeeds.
4. Exercise evaluator death, descriptor closure, PID-reuse safety, lock ordering, and the complete H0 gate before requesting another review.
