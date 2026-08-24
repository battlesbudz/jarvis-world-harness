# Agent Plan

1. Put each supervised gate behind a dedicated lease-holding subreaper/watchdog.
2. Keep an active-gate marker until descendant cleanup is verified, and make every execution entry point fail closed on a stale marker.
3. Retry failed cleanup while retaining both serialization leases; quarantine autonomous execution if verification is ever interrupted.
4. Exercise supervisor/evaluator double death, cleanup-helper failure, lock retention, and the complete H0 gate before requesting another review.
