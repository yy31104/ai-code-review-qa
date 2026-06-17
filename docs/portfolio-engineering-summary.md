# Portfolio Engineering Summary

## Project Pitch

`ai-code-review-qa` is a portfolio-grade, AI-assisted SDLC tool that reads a Git diff, runs the project's automated tests, produces a structured code-review (possible bugs, missing tests, suggested test cases, security/reliability concerns, and a risk level) validated with Pydantic, and renders a shareable HTML report. It runs **deterministically and offline by default** in demo mode, with an **optional, config-gated OpenAI mode**, and is wrapped in a deterministic eval harness so review behavior can change on purpose but never by accident.

## Architecture Summary

```text
Git diff reader  ->  Test runner (pytest / npm / dotnet)
        |
        v
Review engine (review_diff)
  +-- demo mode  : deterministic mock_review_json (default, credential-free)
  +-- openai mode: optional, config-gated, falls back to demo on any failure
        |
        v
Pydantic schemas (ReviewResult / TestResult)
        |
        +--> Jinja2 HTML review report  (artifact: review-report)
        +--> Eval harness regression report (artifact: eval-report)
```

The eval harness sits beside the engine rather than inside it. `evals/run_local.py` loads `evals/data/golden_cases.jsonl`, calls the engine through a `predict(case) -> ReviewResult` seam that forces demo mode, grades each case against declared expectations, and writes `results.json`. `evals/render_report.py` turns those results into Markdown and HTML summaries. Both the review workflow and the eval workflow upload their outputs as GitHub Actions artifacts.

## What Changed Across the Upgrade Phase

### PR #1 — Deterministic eval harness baseline
- Added `evals/run_local.py` (loader, grader, JSON results) and `evals/render_report.py` (Markdown/HTML summaries).
- Added the first `golden_cases.jsonl` seed dataset and pytest coverage for the harness.
- Added the GitHub Actions **Eval Harness** workflow and the `eval-report` artifact.
- Updated `README.md`, `AGENTS.md`, and `CLAUDE.md`.

### PR #2 — Prediction seam and dataset expansion
- Added a `predict(case) -> ReviewResult` seam that routes eval predictions through the **public `review_diff()` path** while forcing demo mode and restoring the prior `AI_REVIEW_MODE` afterward.
- Expanded golden cases from 5 to 18, including true-negative and false-positive guard cases.
- Added a `reports/evals/` ignore rule so generated artifacts stay uncommitted.
- Added `.gitattributes` for LF normalization (removes cross-platform line-ending churn).

### PR #3 — Identifier-aware risk tokenization
- Improved the deterministic demo risk tokenizer to split camelCase/PascalCase identifiers before lowercasing, so risk terms inside names like `authToken`, `deleteUser`, `runSql`, and `PaymentProcessor` are detected.
- Preserved the existing false-positive guards (`author`, `tokenizer`, `deleted-at`, and docs/test-only payment wording).
- Added direct unit tests for risk estimation and tokenization.
- Expanded golden cases from 18 to 23.

## Key Engineering Metrics

- 23 deterministic golden cases.
- 10 pytest tests.
- Latest eval result: **23/23 cases passed, 150/150 checks passed.**
- Two CI artifacts: `eval-report` (eval harness) and `review-report` (review CLI).
- Eval workflow runs on pull request, push to `main`, manual dispatch, and a nightly schedule, with `permissions: contents: read`.
- Deterministic and offline by default in demo/eval mode; no credentials required.

## Run It Locally

```bash
# Install
python -m pip install -r backend/requirements.txt

# Tests
python -m pytest -q

# Evals (deterministic, no API key)
python evals/run_local.py --out reports/evals/results.json
python evals/render_report.py \
  --in reports/evals/results.json \
  --md reports/evals/summary.md \
  --html reports/evals/summary.html

# Core CLI smoke test
python backend/app/main.py --repo ./sample-projects/python-demo --output backend/reports/review_report.html
```

Generated files under `reports/evals/` are gitignored and should stay uncommitted.

## What This Proves in Interviews

- **Eval-driven development:** the review engine is guarded by a deterministic regression suite, not just unit tests, so behavior drift is caught in CI.
- **Designing for change:** the `predict()` seam grades the real public entrypoint, leaving a clean path to later compare demo vs. model output without rewriting the harness.
- **Precision/recall reasoning:** PR #3 is a concrete example of trading recall against precision deliberately (camelCase detection added while lookalike false positives stayed guarded), with cases that lock both directions.
- **Production hygiene:** minimal CI permissions, ignored generated artifacts, LF normalization, and honest documentation of scope.

## Limitations (Honest Scope)

- This is a **deterministic regression baseline, not a full model-quality benchmark.** It pins the demo engine's behavior; it does not score answer quality.
- It is **not yet a GitHub App** and does not post inline comments or run automatic PR-triggered commenting; it supports a manual, opt-in summary-comment upsert workflow.
- It **does not measure real developer comment acceptance rate** or any human-feedback metric.
- The demo risk heuristic is intentionally simple; risk terms fused into a single lowercase run (e.g. `authtoken`) are not split, and the engine is not a security scanner.
- **OpenAI mode is optional and config-gated** — it is never required to run tests or evals.

## Next Safe Improvements

- Grow from 23 to ~30-50 golden cases with more explicit false-negative examples.
- Optionally extend risk terms (e.g. `secret`, `credential`, `apikey`) with matching guard cases.
- Add a small grader abstraction to support model-vs-demo comparison through the existing `predict()` seam.
- Pin GitHub Actions to commit SHAs for supply-chain hardening.

## How I Would Explain This Project in an Interview

"It's an AI-assisted code-review tool, but the part I'm proud of is the engineering around the AI, not just the AI call. The review engine runs deterministically and offline by default, and I wrapped it in an eval harness with 25 hand-reviewed golden cases and CI tests that run as a gate. I added a `predict()` seam so the evals grade the real public review path, then used that harness to make a deliberate precision/recall change — detecting risk terms inside camelCase identifiers like `authToken` while keeping lookalikes like `author` from triggering false positives — with golden cases that lock both behaviors. I kept scope honest: it's a regression baseline, not a model-quality benchmark, it's not a GitHub App yet, and the paid model path is optional and gated."
