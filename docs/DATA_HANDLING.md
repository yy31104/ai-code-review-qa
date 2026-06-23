# Data Handling

## Overview

`ai-code-review-qa` runs in deterministic demo mode by default. OpenAI-powered review mode is opt-in and requires explicit environment configuration.

## No-Store Policy

The project does not include a database, server-side history, telemetry, or hosted backend. Generated review reports are local files controlled by the user.

The `reports/evals/` directory is gitignored and intended for ephemeral local or CI eval artifacts.

The `reports/github/` directory is also gitignored and intended for dry-run GitHub review payload artifacts controlled by the user.

## Network and Egress Boundary

In demo mode, the review engine does not call OpenAI.

The user-configured project test command may still do whatever that project does. This tool detects and runs supported test commands, but it does not control third-party test behavior, dependency behavior, or network access from the project under review.

In OpenAI mode, the changed-file list and git diff are sent to the OpenAI Responses API. The diff payload is truncated by `MAX_DIFF_CHARS = 20000` in `backend/app/llm_reviewer.py`.

## Secrets and Private-Code Boundaries

`.env` is gitignored. `.env.example` ships an empty `OPENAI_API_KEY`.

API keys are loaded at runtime and must not be printed to logs, reports, fixtures, or other committed artifacts.

Do not run private or proprietary repositories in OpenAI mode without authorization. Do not commit reports generated from private code.

## Report Artifact Safety

The HTML report does not embed the raw git diff. It may include changed-file paths, structured findings, the review verdict, and sanitized test output.

Structured findings may include code-derived messages, file paths, line numbers, categories, severity, confidence, and verdict context. Finding messages are rendered through Jinja autoescaping in the HTML report.

Test output path sanitization maps the repository root to `<repo>` and normalizes path separators.

The committed sample report should be demo-mode and credential-free.

## GitHub Review Payloads (Dry Run)

The optional `--emit-github-review` CLI flag writes a local JSON payload shaped like a GitHub create-review request. The optional `--emit-summary-comment` flag writes a local JSON body for a marker-based PR summary comment. The optional `--emit-inline-review` flag writes a dry-run inline review payload. These payloads may contain finding messages, file paths, line numbers, category, severity, confidence, finding fingerprints, review verdict, risk level, and test status.

The payloads do not include the raw git diff. They are generated from the final `ReviewResult` plus a local `DiffIndex` so inline finding counts and summary routing are derived from validated right-side diff lines.

By default, the workflow posts nothing to GitHub. The build job is a manual artifact workflow, and the payloads remain local or CI artifacts controlled by the user.

When `post_summary` is explicitly set to `true`, the workflow uses the repository-scoped `GITHUB_TOKEN` to create or patch one PR summary comment. Summary comments are visible to repository collaborators; on public repositories, they are public. Comments may trigger GitHub notifications. No raw git diff is posted.

For same-repository pull requests, `.github/workflows/pr-summary.yml` can generate one auto-updating summary comment when the repository variable `AI_REVIEW_SUMMARY_AUTOPOST` is set to `true`. Fork pull requests are skipped entirely. Summary comments are visible to repository collaborators; on public repositories, they are public. Comments may trigger GitHub notifications. The raw git diff is not posted.

Inline comments are off by default and require both `AI_REVIEW_SUMMARY_AUTOPOST=true` and `AI_REVIEW_INLINE_COMMENTS=true`. When enabled, inline comments are visible to repository collaborators; on public repositories, they are public and may trigger GitHub notifications. Fingerprint markers are embedded in inline comment bodies so repeated workflow runs can skip already-posted findings. The workflow recomputes the PR diff and revalidates each inline line before posting, but it does not post the raw diff.

The stale inline detection artifact, `stale-plan.json`, may contain GitHub review comment IDs, finding fingerprints, file paths, line numbers, and comment authors for marker-owned inline comments. The stale resolve enrichment artifacts, `review-threads.json` and `stale-action-plan.json`, may contain comment IDs, review thread node IDs, finding fingerprints, comment authors, and resolved state. The opt-in stale resolve artifact, `resolve-apply.json`, contains only selected review thread node IDs plus a count. These artifacts do not include the raw git diff, and no secrets are expected. They are controlled by GitHub Actions artifact retention.

`AI_REVIEW_STALE_ACTION=true` enables an isolated same-repository PR job that writes to GitHub only through GraphQL `resolveReviewThread`. The selector is limited to bot-authored, marker-owned, unresolved, now-stale inline review threads. It resolves only; it never deletes, edits, replies to, or minimizes comments. Fork and other non-same-repository PRs are skipped, and the project does not create or enable the repository variable.

The no-store boundary still holds for this tool: it does not add a database, server-side history, telemetry, or hosted backend. GitHub stores comments that the user explicitly chooses to post.

## OpenAI Optional-Mode Boundary

OpenAI mode is off by default. API, configuration, or schema-validation failures fall back to demo mode with a warning in the report summary.

OpenAI output is validated with the Pydantic `ReviewResult` schema before report generation.

This project does not control OpenAI data retention, account settings, or organization policy.

## Practical Operator Checklist

Before sharing generated reports, inspect them for:

- sensitive paths
- logs or stack traces
- secrets or credentials
- proprietary filenames or module names
- private issue, PR, or customer context
