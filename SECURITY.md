# Security Policy

## Scope

`ai-code-review-qa` is a portfolio/MVP AI-assisted code-review CLI. The project is maintained on `main` on a best-effort basis and does not provide a commercial support SLA.

## Supported Versions

| Version | Supported |
| --- | --- |
| `main` | Yes |
| Everything else | No |

## Reporting a Vulnerability

Please do not open public GitHub issues for suspected vulnerabilities.

Primary reporting channel: use GitHub private vulnerability reporting from the repository Security tab, then select **Report a vulnerability**. Private Vulnerability Reporting must be enabled in the repository settings for this channel to be available.

Please include:

- reproduction steps
- affected command, file, or workflow
- expected and actual impact
- suggested fix or mitigation, if known

## Response Expectations

Security reports are handled on a best-effort basis. The maintainer will try to acknowledge valid reports, assess impact, and ship fixes where practical, but this portfolio project does not provide a commercial SLA.

## Threat Model

The tool is a local CLI that reads git diffs, runs detected test commands, and renders an HTML report. Diffs, PR or issue text, model output, and test output are untrusted input.

The tool must never execute commands derived from model output. Automated test execution is limited to deterministic project-type detection in the local test runner, but the behavior of the tested project and its dependencies remains outside this repository's control.

GitHub review payload generation treats diff-derived and model-derived text as untrusted Markdown. The payload builder escapes HTML-sensitive characters, Markdown link and emphasis controls, backticks, mentions, issue autolinks, code-fence breakout attempts, and leading block-level Markdown before placing finding text in comment bodies.

The PR comment workflow is manual-only. Its default build job runs with `contents: read` and posts nothing. The posting job runs only when `post_summary` is explicitly `true`, requires a `pull_number`, and scopes `pull-requests: write` to that posting job only. `pull_request_target` remains forbidden for this project.

Posted summary comment bodies are passed to `gh api` with `--input` JSON files, not interpolated into shell commands.

The same-repo PR summary workflow uses `pull_request`, not `pull_request_target`. It gates every job on `github.event.pull_request.head.repo.full_name == github.repository`, so fork PRs skip entirely and do not get checked out, tested, artifacted, or commented on. Its summary post job is gated by `AI_REVIEW_SUMMARY_AUTOPOST == 'true'`. No secrets are exposed to forks.

Inline create-review posting is behind a separate opt-in gate and only runs when both `AI_REVIEW_SUMMARY_AUTOPOST == 'true'` and `AI_REVIEW_INLINE_COMMENTS == 'true'`. Summary posting runs first and remains the fallback output. The inline post job scopes permissions to `contents: read` and `pull-requests: write`, recomputes the PR diff, revalidates every target line against `DiffIndex` immediately before posting, filters already-posted fingerprints, and passes the create-review body to `gh api` with `--input`. GitHub create-review failures, including 422-style invalid-review failures, are emitted as warnings and are non-fatal because the summary comment already exists. Fork PRs remain skipped and `pull_request_target` remains forbidden.

Stale inline detection and resolve enrichment are read-only. Detection only considers inline review comments that contain this project's hidden AI finding marker and writes a `stale-plan.json` artifact. Resolve enrichment reads GitHub review threads and writes a `stale-action-plan.json` artifact; eligibility requires the marker, `github-actions[bot]` author ownership, a found review thread, and an unresolved thread. Human comments and other-bot comments are not eligible even if they copy the marker. These steps do not delete, resolve, edit, patch, or reply to comments.

Any future stale-comment action must be separately gated, must remain marker-owned and non-destructive, and should resolve via GitHub review-thread APIs rather than deleting comments. `pull_request_target` remains forbidden. Future stale-action failures must be non-fatal so summary and inline review output remain available.

## Out of Scope

- upstream dependency CVEs unless this project adds an unsafe integration or configuration
- pre-compromised hosts or developer machines
- vulnerabilities in OpenAI infrastructure, APIs, or account configuration

For data and artifact boundaries, see [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md).
