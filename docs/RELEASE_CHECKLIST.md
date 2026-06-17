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
- dry-run PR comment payload workflow has no `pull-requests: write`
- payload artifact contains no secrets
- `reports/github/` not committed

## Branch and PR Hygiene

- use a feature branch only
- do not push directly to `main`
- do not force push
- wait for CI to pass before merge

## Docs Sync

- README links resolve
- behavior changes are reflected in README and eval docs
- known limitations are documented
