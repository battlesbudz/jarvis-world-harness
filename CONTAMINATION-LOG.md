# Intervention / Provenance Log

Record human diagnoses, manual code interventions, changed acceptance criteria, model/runtime changes, and major environment changes during an autonomous milestone.

- 2026-08-13: PR #1 Codex review identified that a timed-out gate leader could exit before SIGKILL escalation while a stubborn descendant retained inherited pipes. The harness implementation and regression were manually updated in response; product laws and acceptance criteria were unchanged.
