# Agent Plan

1. Reproduce both reported private-lock leaks and the adjacent gate/control-wait SIGKILL paths.
2. Keep supervisor fd 7 and restart fd 6 out of every child while preserving the intentional control/runner handoffs through one replacement launch.
3. Run the focused lifecycle suite and complete H0 gate, record evidence, publish both fixes together, and request a fresh Codex review.
