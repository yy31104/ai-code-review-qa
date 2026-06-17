# Release Checklist

Use this checklist before merging production-trust, review-engine, or report-artifact changes.

## Pre-Merge Gates

```powershell
python -m pytest -q
python evals/run_local.py --out reports/evals/results.json
python evals/render_report.py --in reports/evals/results.json --md reports/evals/summary.md --html reports/evals/summary.html
$env:AI_REVIEW_MODE = "demo"; python backend/app/main.py --repo . --output backend/reports/review_report.html
git diff --check
```

## Trust Gates

- no `.env` staged
- no secrets in the sample report
- `reports/evals/` not committed
- GitHub Actions permissions stay minimal
- branch protection checks enabled
- `LICENSE` and `SECURITY.md` present
- Markdown escaping tests pass
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
- `pull-requests: write` exists only in the summary post job
- merge-base PR diff resolution is verified on a canary PR
- summary upsert patches the same marker comment on synchronize
- inline review payload artifact is generated when requested
- `AI_REVIEW_INLINE_COMMENTS` is not implemented or enabled yet
- inline hard cap and finding fingerprint tests pass
- inline payload remains artifact-only; no inline posting step exists
- every future inline posting path must revalidate lines against `DiffIndex`
- same-repo PR workflows continue to avoid `pull_request_target`

## Branch and PR Hygiene

- use a feature branch only
- do not push directly to `main`
- do not force push
- wait for CI to pass before merge

## Docs Sync

- README links resolve
- behavior changes are reflected in README and eval docs
- known limitations are documented
