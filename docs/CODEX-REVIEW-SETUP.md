# Codex GitHub Review Setup

Jarvis World Harness requires OpenAI Codex review on every pull request before merge.

## Repository workflow

The repository-side policy is enforced by:

- `AGENTS.md` — requires Codex review, triage, fixes, and re-review after changes.
- `.github/pull_request_template.md` — makes the review gate visible on every PR.

## Account-level prerequisite

The GitHub repository must also be authorized in the owner's Codex Cloud/GitHub connection with code review available for the repository. This setting is external to repository source control and cannot be enabled by changing files in this repository.

After the repository is authorized, a PR can request a manual review with:

`@codex review`

If commits are pushed after Codex gives feedback, request another Codex review against the updated diff.

## Merge policy

Do not merge a PR until:

1. relevant tests/evidence pass,
2. Codex review has run on the current meaningful diff,
3. all Codex feedback has been reviewed,
4. actionable findings are fixed or explicitly resolved.
