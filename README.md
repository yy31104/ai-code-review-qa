# AI Code Review & QA Automation Platform

A portfolio-ready MVP for automated code review and QA reporting. The tool reads Git changes, runs automated tests, generates structured AI review feedback, validates the result with Pydantic, and outputs a clean HTML report.

This project is positioned for Junior AI Engineer and AI Automation Engineer roles. It supports demo mode by default and optional OpenAI-powered structured review mode when an API key is configured. The committed sample report and eval path use deterministic, credential-free demo mode; OpenAI mode remains optional and config-gated.

Released under the [MIT License](LICENSE).

## Why This Is Useful

Modern teams need fast feedback before code reaches human reviewers. This tool helps by combining diff analysis, automated test execution, structured AI review notes, and a shareable HTML report. It can run safely in demo mode without credentials, or use OpenAI mode for real structured review output while keeping a demo fallback if the API call or validation fails.

## Key Features

- Optional OpenAI-powered structured code review
- Demo fallback mode
- Structured JSON review output validated with Pydantic
- Git working-tree and commit-range diff support
- Automatic test command detection for Python, Node.js, and .NET projects
- Automated test execution
- Deterministic review decision derived from risk level and automated test status
- Anchored findings with file, line, category, severity, confidence, and message fields
- Dry-run GitHub review payload JSON generation for future PR comments
- Inline review payload artifact with fingerprints and a conservative hard cap
- Manual, opt-in summary comment upsert for GitHub PRs
- Same-repo PR summary workflow with merge-base diff resolution
- Opt-in inline PR comments with post-time diff revalidation and fingerprint dedupe
- Dry-run stale inline comment detection for marker-owned AI findings
- Dry-run stale resolve enrichment that maps stale bot comments to GitHub review threads
- Opt-in non-destructive stale inline resolution for eligible bot-owned marker threads
- GitHub-style HTML report generated with Jinja2
- Local golden-case eval harness
- Markdown/HTML eval summary artifacts
- GitHub Actions artifact upload

## Production Upgrade Highlights

A three-PR production-upgrade phase added a deterministic regression baseline around the review engine so behavior changes are caught before they ship:

- Deterministic, offline eval harness that runs without API keys
- 25 hand-reviewed golden cases covering risk, missing-test, false-positive, and anchor-position behavior
- Pytest coverage for the harness, prediction seam, risk tokenization, review decision rules, and report verdict rendering
- A `predict(case) -> ReviewResult` seam that grades cases through the public `review_diff()` path in forced demo mode
- False-positive guards for lookalike terms such as `author`, `tokenizer`, and `deleted-at`/docs-only payment wording
- camelCase/PascalCase risk-term support (e.g. `authToken`, `deleteUser`, `runSql`, `PaymentProcessor`)
- Deterministic report verdicts that distinguish `needs_human_review`, `review_recommended`, and `looks_good`
- GitHub Actions `eval-report` artifact (results JSON plus Markdown/HTML summaries) on PRs, pushes, and nightly runs
- Demo mode remains credential-free; OpenAI mode stays optional and config-gated

See [docs/portfolio-engineering-summary.md](docs/portfolio-engineering-summary.md) for the full engineering closeout.

## Sample Report Screenshot

![AI Code Review Report](docs/screenshots/report-preview.png)

The screenshot is illustrative; the committed HTML sample report is regenerated from the current deterministic demo-mode workflow.

## Architecture / Workflow

```text
Developer pushes code or runs CLI
        |
        v
Git diff reader
        |
        +--> changed files
        +--> raw diff
        |
        v
Test runner detects project type
        |
        +--> pytest
        +--> npm test
        +--> dotnet test
        |
        v
Review engine creates structured JSON
        |
        +--> demo mode
        +--> OpenAI mode
        |
        v
Pydantic schemas validate review and test results
        |
        +--> Jinja2 HTML review report
        +--> eval harness regression report
```

## Tech Stack

- Python 3.11
- Pydantic
- Jinja2
- Pytest
- Git
- GitHub Actions

## Local Setup

```bash
git clone https://github.com/yy31104/ai-code-review-qa.git
cd ai-code-review-qa

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

## Review Modes

Demo mode is the default and does not require an API key:

```bash
AI_REVIEW_MODE=demo
```

To configure optional OpenAI review mode, copy the example env file and add your own API key:

```bash
cp .env.example .env
```

```text
AI_REVIEW_MODE=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5.4-mini
```

When `AI_REVIEW_MODE=openai`, the CLI sends the git diff and changed files to the OpenAI Responses API and validates the structured JSON response against the existing Pydantic `ReviewResult` schema. If the API key is missing, the API call fails, or the model response cannot be validated, the tool falls back to demo mode and includes a warning in the report summary. API keys are loaded from `.env` and should never be committed.

## Review Decision

The HTML report verdict is deterministic and test-aware. After automated checks run, the CLI derives `review_decision` from `risk_level` and `automated_test_results`:

- failed tests with a detected command, or High risk: `needs_human_review`
- Medium risk with passing or not-run tests: `review_recommended`
- Low risk with passing or not-run tests: `looks_good`

Tests that were not detected do not increase severity by themselves. Every verdict keeps a human-in-the-loop explanation so the report helps decide what to verify before merging.

## Anchored Findings

Review output includes an additive `findings` list alongside the existing human-readable string fields. Each finding can carry a file path, optional line number, category, severity, confidence score, and message.

When a unified diff can be validated, anchored findings use true added-line anchors from the right side of the diff. Findings that cannot be safely anchored remain file-level or summary-routed for future reporters.

This structured format is the foundation for future inline PR comments. The current version renders anchored findings in the HTML report but does not post GitHub comments yet.

## Security & Trust

The default demo path is deterministic and credential-free. Optional OpenAI mode is the main code-egress path: when enabled, the tool sends the changed-file list and truncated git diff to the OpenAI Responses API.

Reports are local artifacts controlled by the user, and generated eval reports under `reports/evals/` are ignored by git. The `main` branch is protected by required GitHub Actions checks for evals and report generation.

Trust and governance docs:

- [MIT License](LICENSE)
- [Security Policy](SECURITY.md)
- [Data Handling](docs/DATA_HANDLING.md)
- [Release Checklist](docs/RELEASE_CHECKLIST.md)

## CLI Usage

### Local Working-Tree Review

Run the MVP review workflow against the included sample Python project:

```bash
python backend/app/main.py --repo ./sample-projects/python-demo --output backend/reports/review_report.html
```

This mode inspects the current uncommitted working-tree diff for the target project.

### Commit Range Review

Run the review against an explicit Git commit range:

```bash
python backend/app/main.py --repo . --base HEAD~1 --head HEAD --output backend/reports/review_report.html
```

This mode compares `<base>..<head>` and is the mode used by GitHub Actions.

Both modes will:

- inspect Git changes for the target project
- detect and run the appropriate test command
- create structured review output using demo or OpenAI mode
- derive a deterministic review decision from risk and test status
- generate an HTML review report

### GitHub Review Payload, Inline Payload, And Summary Comment

Generate a local GitHub create-review payload JSON without posting comments:

```bash
AI_REVIEW_MODE=demo python backend/app/main.py \
  --repo . \
  --output backend/reports/review_report.html \
  --emit-github-review reports/github/review.json \
  --emit-summary-comment reports/github/summary-comment.json \
  --emit-inline-review reports/github/inline-review.json \
  --emit-finding-fingerprints reports/github/finding-fingerprints.json \
  --head-sha HEAD_SHA
```

The review payload includes a summary body and any findings that can be safely validated against true right-side diff lines. Findings that cannot be safely posted inline remain summary-routed. The summary comment artifact contains only `{ "body": "..." }` with the hidden summary marker used for upsert.

The inline review payload embeds stable finding fingerprints, caps inline candidates at 5 comments, and only allows High or Medium findings with confidence >= 0.5 after line validation through `DiffIndex`. `suggested_test`, `recommended_action`, low-confidence, low/info, overflow, or unanchorable findings stay in the summary body.

Summary comments remain the primary and fallback output. The summary flow can create or patch one marker-based top-level PR conversation comment. Inline comments are opt-in and require both repository variables to be set:

```text
AI_REVIEW_SUMMARY_AUTOPOST=true
AI_REVIEW_INLINE_COMMENTS=true
```

Before posting inline comments, the workflow recomputes the PR diff, revalidates every inline line against `DiffIndex`, and skips duplicate inline findings by fingerprint. If GitHub create-review fails, including a 422-style invalid-review failure, the workflow emits a warning and keeps the job green because the summary comment already exists.

The workflow also emits stale inline artifacts when inline posting is enabled. `stale-plan.json` compares marker-owned inline comments against all current finding fingerprints, including summary-routed and overflow findings, so capped findings are not misclassified as stale. `stale-action-plan.json` maps stale marker-owned `github-actions[bot]` comments to GraphQL review thread node IDs and records whether each thread is eligible for non-destructive resolve. These artifacts do not delete, edit, reply to, or minimize comments.

Stale inline resolution is separately opt-in through the repository variable `AI_REVIEW_STALE_ACTION=true`. It enables GraphQL `resolveReviewThread` only for bot-authored, marker-owned, unresolved, now-stale inline threads. It resolves only; it never deletes, edits, replies to, or minimizes comments. Fork and other non-same-repository PRs are skipped, and this repository variable is not created or enabled by the project.

The default workflow behavior is dry-run artifact generation only. Summary posting is manual and opt-in through `.github/workflows/pr-comment.yml`:

- `post_summary`: defaults to `false`
- `pull_number`: required only when `post_summary` is `true`

When `post_summary` is `true`, the workflow uses the GitHub issue-comments endpoint to create or patch the marker-based PR summary comment. It still does not post inline comments.

Dispatch this workflow from the target PR's head branch. In this manual stage, the generated summary reflects the selected workflow ref's `HEAD~1..HEAD` range, not the full PR `base..head` diff. Full PR diff resolution is planned for the future pull_request-triggered workflow.

Planned rollout:

- current: manual summary-comment upsert and same-repo PR summary workflow
- current: opt-in inline comments after every line is re-validated against the diff index
- current: dry-run stale inline detection artifact for marker-owned comments
- current: dry-run stale resolve enrichment artifact for marker-owned bot comments
- current: separately gated non-destructive stale inline resolution for eligible bot-owned marker threads
- later: richer noise controls and provider hardening

## Eval Harness

The local eval harness provides a deterministic regression baseline for the demo review engine. It is designed to run without API keys and to catch behavior drift before the project expands into PR comments, API service mode, or richer model comparisons.

Run the seed eval dataset:

```bash
python evals/run_local.py --out reports/evals/results.json
```

Render Markdown and HTML summaries:

```bash
python evals/render_report.py \
  --in reports/evals/results.json \
  --md reports/evals/summary.md \
  --html reports/evals/summary.html
```

The 25-case seed dataset currently covers:

- authentication/token changes without tests
- test-only changes that should stay low risk
- large cross-file changes that should raise risk level
- subprocess, SQL, delete, and payment-sensitive changes
- risk terms inside camelCase/PascalCase identifiers
- false-positive guards for nearby low-risk terms
- empty-diff/untracked-file cases with limited line-level analysis
- diff-validated anchored-finding line behavior

## Local Tests

Run the Python test suite:

```bash
python -m pytest -q
```

The tests cover the eval dataset loader, local eval CLI, and report rendering command.
They also cover risk estimation, review decision derivation, and the report verdict badge.

## GitHub Actions CI Usage

The workflow in `.github/workflows/ai-review.yml` runs on:

- push to `main`
- pull request to `main`
- manual `workflow_dispatch`

In CI, GitHub Actions checks out the repository, installs backend dependencies, runs the CLI with `--base HEAD~1 --head HEAD`, and uploads the generated report as an artifact named `review-report`.

The workflow in `.github/workflows/pr-comment.yml` is manual-only. Its build job uses `workflow_dispatch`, `contents: read`, demo mode, and uploads `reports/github/review.json` plus `reports/github/summary-comment.json` as artifacts. Its post job runs only when `post_summary` is explicitly `true`, requires `pull_number`, has `pull-requests: write`, and upserts the hidden-marker summary comment. It does not post inline comments and does not use `pull_request_target`.

The workflow in `.github/workflows/pr-summary.yml` runs on same-repository pull requests to `main` for `opened`, `synchronize`, and `reopened` events. Fork PRs are skipped entirely: no checkout, no tests, no artifacts, and no comments. For same-repo PRs, the workflow resolves the true PR diff with `git merge-base` and generates the summary artifact plus an inline review payload from `<merge-base>..<head>`.

Automatic summary posting is off by default. Set the repository variable `AI_REVIEW_SUMMARY_AUTOPOST=true` to let the post job upsert one marker-based PR summary comment.

Inline posting is also off by default and only runs when both `AI_REVIEW_SUMMARY_AUTOPOST=true` and `AI_REVIEW_INLINE_COMMENTS=true`. The inline job runs after the summary post job, recomputes the PR diff, revalidates comment lines, filters already-posted fingerprints, and sends a capped create-review payload. The create-review body is intentionally minimal and points readers to the summary comment. GitHub create-review failures are non-fatal so the summary comment remains the reliable fallback.

The inline job also writes stale debug artifacts. `reports/github/stale-plan.json` only considers comments carrying the hidden AI finding marker and reports comments whose fingerprints no longer exist in the current review result. The workflow then reads GraphQL review thread data and writes `reports/github/stale-action-plan.json`, which maps stale marker-owned `github-actions[bot]` comments to unresolved review thread node IDs. This inline artifact path does not mutate GitHub state.

The separate `resolve-stale` job is off by default and only runs for same-repository PRs after the build succeeds when `AI_REVIEW_STALE_ACTION=true`. Before resolving anything, it fetches paginated GraphQL review threads with `gh api graphql --paginate --slurp`, rebuilds `stale-action-plan.json`, derives `resolve-apply.json`, and then calls GraphQL `resolveReviewThread` for the selected thread node IDs. It resolves eligible stale bot-authored marker threads only; it never deletes, edits, replies to, or minimizes comments.

The workflow in `.github/workflows/evals.yml` runs the eval harness on:

- pull request to `main`
- push to `main`
- manual `workflow_dispatch`
- nightly schedule

It runs pytest, runs the golden-case evals, renders Markdown/HTML eval summaries, uploads `eval-report`, and publishes the Markdown summary to the GitHub job summary.

## HTML Report Artifact

The generated review report is saved to:

```text
backend/reports/review_report.html
```

It includes:

- project summary
- changed files
- risk level
- possible bugs
- missing tests
- suggested test cases
- security and reliability concerns
- anchored findings
- automated test results
- recommended actions
- deterministic review decision and human-review explanation

The committed report is included as a sample portfolio artifact generated in deterministic demo mode.

## Current MVP Status

Complete MVP:

- CLI workflow is implemented
- sample Python project is included
- automated tests run successfully
- HTML report generation works locally and in CI
- dry-run GitHub review payload generation works locally and in a manual CI workflow
- inline review payload generation works locally and in the same-repo PR summary workflow
- manual opt-in PR summary comment upsert is available
- opt-in inline PR comments are available behind summary-first gates
- dry-run stale inline detection is available as a CI artifact
- dry-run stale resolve enrichment is available as a CI artifact
- opt-in stale inline resolution is available behind `AI_REVIEW_STALE_ACTION=true`
- same-repo PR summary artifacts use merge-base diff resolution
- optional OpenAI-powered structured review output is supported when configured
- committed sample and eval artifacts run in deterministic demo mode without credentials
- report verdicts are derived from risk and automated test status
- demo fallback remains available for local and CI-safe execution
- first deterministic eval harness baseline is implemented
- GitHub Actions eval workflow is available for PR/manual/nightly runs

## Future Improvements

- richer prompt evaluation and reviewer tuning
- React dashboard
- ASP.NET Core API
- Docker packaging
- Historical review storage
