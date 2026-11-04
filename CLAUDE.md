# Contributing

Use Git and Python 3.11+.

Install in a virtual environment:
```bash
python -m pip install -r backend/requirements.txt
```
Run the CLI against a trusted checkout:
```bash
AI_REVIEW_MODE=static python backend/app/main.py --repo . --base HEAD~1 --head HEAD --output reports/review.html
```
Check changes:
```bash
python -m pytest -q
python evals/run_local.py --out reports/evals/results.json
python evals/render_report.py --in reports/evals/results.json --md reports/evals/summary.md --html reports/evals/summary.html
git diff --check
```

- Keep static mode deterministic and credential-free.
- Preserve explicit provider failure states and added-line evidence checks.
- Add a positive case and a silent near-miss control when changing a rule.
- Leave real-diff verdicts and adjudications to a human.
- Keep report text escaped and GitHub writes behind their existing gates.
- Never execute model-proposed commands or commit secrets or generated reports.
- The test runner executes checkout code on the host; use trusted repositories.
- Update command and behavior documentation when changing public interfaces.
