## What changed

Describe the smallest useful change and the milestone/acceptance criteria it serves.

## Evidence / validation

List tests, simulation traces, Unreal/PIE evidence, screenshots, logs, or other checks used to verify the change.

## Safety / architecture

- [ ] Protected core laws were not changed as an implementation shortcut.
- [ ] World OS / Unreal authority boundaries remain intact.
- [ ] No secrets, credentials, generated caches, or huge derived assets were committed.
- [ ] Upstream AAABench attribution and MIT license remain intact.

## Codex review gate

- [ ] Relevant tests/evidence have been run before requesting review.
- [ ] `@codex review` has been triggered, or automatic Codex review has started.
- [ ] Codex review feedback has been read and triaged.
- [ ] Actionable Codex findings have been fixed or explicitly resolved.
- [ ] If commits were pushed after Codex feedback, Codex was re-triggered for the updated diff.

Do not merge with unresolved actionable Codex review findings.
