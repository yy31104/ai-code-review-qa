# Eval Harness

This directory contains the first production-grade regression harness for `ai-code-review-qa`. It is intentionally small and deterministic so it can run locally, in CI, and in nightly jobs without API keys.

## What it measures

The current 23-case dataset checks whether the demo review engine keeps stable behavior for:

- risk-level classification for high-risk terms such as auth, token, subprocess, and SQL;
- detection of risk terms inside camelCase/PascalCase identifiers such as authToken, deleteUser, runSql, and PaymentProcessor;
- false-positive guards for nearby non-risk terms such as tokenizer, author, deleted-at, and docs/test-only payment wording;
- missing-test detection when production files change without nearby test files;
- exact or minimum useful finding counts for possible bugs, missing tests, suggested tests, and reliability concerns;
- preservation of changed-file context in the structured review output.

This is not yet a model-quality benchmark. It is a baseline regression harness that prevents accidental behavior drift while the project moves from MVP to product-like architecture.

The eval runner calls the public review engine through `predict()` while forcing deterministic demo mode for each case. It does not call OpenAI and does not require credentials.

## Dataset format

Cases live in `evals/data/golden_cases.jsonl`. Each non-empty line is one JSON object:

```json
{
  "id": "auth_token_change_without_tests",
  "title": "Authentication/token code changed without tests",
  "tags": ["risk", "missing-tests"],
  "changed_files": ["backend/app/auth.py"],
  "diff": "diff --git ...",
  "expected": {
    "risk_level": "High",
    "min_counts": {
      "possible_bugs": 2,
      "missing_tests": 2
    },
    "required_keywords": {
      "missing_tests": ["No test files detected"]
    },
    "changed_file_keywords": ["auth.py"]
  }
}
```

Supported expectation keys:

- `risk_level`: exact expected `ReviewResult.risk_level`.
- `min_counts`: minimum list lengths for fields such as `possible_bugs` or `missing_tests`.
- `exact_counts`: exact list lengths for branch-sensitive behavior such as test-file detection.
- `required_keywords`: required case-insensitive substrings in a review field.
- `changed_file_keywords`: required case-insensitive substrings in `changed_files`.

## Local commands

```bash
python evals/run_local.py --out reports/evals/results.json
python evals/render_report.py --in reports/evals/results.json --md reports/evals/summary.md --html reports/evals/summary.html
```

The runner exits with status `1` when any case fails. That makes it safe to use as a CI gate once the baseline is accepted.

## How to add cases

Add new cases when review behavior changes, especially for:

- false-positive fixes;
- missing-test detection improvements;
- security or secret-redaction behavior;
- new language or framework support;
- GitHub PR adapter behavior.

Keep the dataset hand-reviewed and deterministic. The next milestone is to grow from this 23-case baseline to 30-50 golden cases with explicit false-positive and false-negative examples.
