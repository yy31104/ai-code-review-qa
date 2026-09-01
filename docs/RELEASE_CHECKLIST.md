# Release Checklist

Use this checklist before merging production-trust, review-engine, or report-artifact changes.

## Pre-Merge Gates

```powershell
python -m pytest -q
python evals/run_local.py --out reports/evals/results.json
python evals/render_report.py --in reports/evals/results.json --md reports/evals/summary.md --html reports/evals/summary.html
AI_REVIEW_MODE=static python backend/app/main.py --repo . --base HEAD~1 --head HEAD --output backend/reports/review_report.html
git diff --check
```

## Trust Gates

- no `.env` staged
- no `.env` or credential file included in shared archives
- no secrets in the sample report
- `reports/evals/` not committed
- GitHub Actions permissions stay minimal
- branch protection checks enabled
- `LICENSE` and `SECURITY.md` present
- Markdown escaping tests pass
- report displays review mode, status, and finding source
- provider failure contains no static findings and returns status `2`
- dry-run PR comment payload workflow is `workflow_dispatch` only
- dry-run PR comment payload workflow has `contents: read`
- dry-run PR comment payload build job has no `pull-requests: write`
- payload artifact contains no secrets
- `reports/github/` not committed
- `post_summary` default is `false`
- summary comment write permission exists only in the post job
- PR comment workflow remains manual-only
- no `pull_request_target`
- summary marker upsert prevents duplicate active summary comments
- upsert decision tests pass
- Markdown hardening tests pass
- same-repo PR summary workflow uses `pull_request` only
- same-repo PR summary workflow does not use `pull_request_target`
- same-repo gate skips fork PRs entirely
- `AI_REVIEW_SUMMARY_AUTOPOST` default is off
- `pull-requests: write` exists only in summary post, opt-in inline post, and opt-in stale resolve jobs
- merge-base PR diff resolution is verified on a canary PR
- summary upsert patches the same marker comment on synchronize
- inline review payload artifact is generated when requested
- `AI_REVIEW_INLINE_COMMENTS` default is off
- inline posting requires `AI_REVIEW_SUMMARY_AUTOPOST=true`
- inline hard cap and finding fingerprint tests pass
- inline posting revalidates every line against `DiffIndex` before posting
- inline posting skips existing fingerprints before create-review
- GitHub create-review failures are non-fatal and fall back to summary-only
- inline post job scopes permissions to `contents: read` and `pull-requests: write`
- stale inline detection dry-run artifact exists
- stale inline detection has no delete, resolve, patch, edit, or reply behavior
- stale detection considers marker-owned comments only
- `stale-plan.json` artifact contains no secrets
- inline stale resolve enrichment remains dry-run and has no GraphQL mutation
- opt-in stale resolve job fetches review threads with GraphQL pagination before mutation
- `AI_REVIEW_STALE_ACTION` default is off and is not created by the project
- stale resolve eligibility requires marker plus `github-actions[bot]` author ownership
- human comments and other-bot comments must not be eligible for stale actions
- stale resolve action uses its own opt-in variable
- stale resolve action is separately gated and non-destructive
- stale resolve action only calls GraphQL `resolveReviewThread`
- stale resolve action never deletes, edits, replies to, or minimizes comments
- same-repo PR workflows continue to avoid `pull_request_target`

## Inline Comment Canary

- enable `AI_REVIEW_SUMMARY_AUTOPOST=true` on a same-repo test PR
- enable `AI_REVIEW_INLINE_COMMENTS=true` only for the canary
- verify the summary comment is present before inline posting
- verify one create-review appears with capped inline comments
- push another commit and verify duplicate inline comments are skipped by fingerprint
- verify the summary comment remains present and patched
- verify fork PRs skip
- disable `AI_REVIEW_INLINE_COMMENTS` after the canary unless continuous inline posting is desired

## Branch and PR Hygiene

- use a feature branch only
- do not push directly to `main`
- do not force push
- wait for CI to pass before merge

## Docs Sync

- README links resolve
- behavior changes are reflected in README and eval docs
- known limitations are documented
