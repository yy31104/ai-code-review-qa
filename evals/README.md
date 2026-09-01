# Eval Harness

This directory holds two things that answer different questions.

| | `run_local.py` | `real_diffs.py` |
| --- | --- | --- |
| Diffs | synthetic, written to exercise a rule | real commits from a git repository |
| Labels | in the dataset, written with the case | entered by a human after the run |
| Answers | do the rules still behave as written? | are the findings on real code real? |
| Fails when | behaviour drifts | never; it reports a number |
| Cost | none | none for rules, provider price for `AI_REVIEW_MODE=openai` |

A green `run_local.py` is a regression signal, not evidence of usefulness. Both run without API keys unless provider mode is requested.

## What it measures

The JSONL dataset checks the deterministic rules on labeled synthetic diffs:

- **Rule detection.** Each rule has a case whose diff contains exactly that defect, pinning the rule id, the file, the anchored line, and often the exact evidence string.
- **Controls.** Each rule also has a near-miss case that must produce nothing: `yaml.safe_load` against `yaml.load`, `requests.get(url, timeout=5)` against a call with no timeout, `ast.literal_eval` against `eval`, parameterised SQL against an f-string statement, `os.getenv` and placeholder values against a real key literal, `assert` in a test file against `assert` in a request handler, and a risk term inside a string literal or comment.
- **Noise control.** Overlapping rules on one line report once.
- **Diff shape.** Continuation lines are joined; context lines and deleted lines produce nothing; binary files and files with no hunk produce nothing and are reported as unanchorable.
- **Risk and decision.** Keyword risk classification, its camelCase/PascalCase handling, its false-positive guards (`author`, `tokenizer`, docs-only payment wording), the file-count boundary, and escalation of risk to match the most severe finding.

Roughly half the cases are controls. That ratio is the point: a reviewer that never stays silent is not a reviewer.

This is not a model-quality benchmark, and it is not a measurement of usefulness on real pull requests. The diffs are synthetic and were written to exercise the rules. It is a regression baseline that prevents drift.

The eval runner calls the public review engine through `predict()` while forcing deterministic demo mode for each case. It does not call OpenAI and does not require credentials.

## Dataset format

Cases live in `evals/data/golden_cases.jsonl`. Each non-empty line is one JSON object:

```json
{
  "id": "sql_built_by_interpolation_is_flagged",
  "title": "An f-string SQL statement is flagged",
  "tags": ["rule", "security", "sql"],
  "changed_files": ["backend/app/repo.py"],
  "diff": "diff --git ...",
  "expected": {
    "risk_level": "High",
    "review_decision": "needs_human_review",
    "findings": {
      "rule_ids": ["sql_string_interpolation"],
      "line_anchors": [
        {"file": "backend/app/repo.py", "line": 41, "rule_id": "sql_string_interpolation"}
      ]
    }
  }
}
```

Supported expectation keys:

- `risk_level`: exact expected `ReviewResult.risk_level`.
- `review_decision`: exact expected `ReviewResult.review_decision`.
- `min_counts` / `exact_counts`: list lengths for fields such as `possible_bugs` or `missing_tests`.
- `required_keywords`: required case-insensitive substrings in a review field.
- `changed_file_keywords`: required case-insensitive substrings in `changed_files`.
- `findings.rule_ids`: the **exact** multiset of rule ids the case must produce. This is the strongest check in the suite: it fails on a missed detection and on a spurious extra one, so one assertion covers precision and recall. A control case sets it to `[]`.
- `findings.line_anchors`: required findings at a given `file` and `line`, optionally pinning `category`, `rule_id`, and the exact `evidence` string.
- `findings.require_evidence`: every anchored finding must carry non-empty evidence.
- `findings.min_total` / `max_total` / `categories_present` / `file_anchored` / `severity_at_least` / `require_line_anchor`: coarser shape checks.

## Local commands

```bash
python evals/run_local.py --out reports/evals/results.json
python evals/render_report.py --in reports/evals/results.json --md reports/evals/summary.md --html reports/evals/summary.html
```

The runner derives the case and check totals from the dataset, prints both totals, and exits with status `1` when any case fails.

## How to add cases

Every new rule needs **two** cases: one diff that must produce it, and one near-miss diff that must not. A rule with no control case is a rule with an unmeasured false-positive rate.

Also add cases for:

- false-positive fixes, with the exact code that used to trip the rule;
- diff-shape handling (continuations, renames, binary files, missing hunks);
- risk and decision changes;
- GitHub PR adapter behavior.

Prefer `rule_ids` over `min_total`. A minimum count passes when the reviewer becomes noisier, which is the failure mode this project most needs to catch.

Keep this dataset deterministic. Provider quality requires a separate versioned set of real diffs, provenance, labels, a frozen held-out split, and provider result artifacts; do not add provider-quality claims to this suite.

## Measuring on real diffs

```bash
git clone --depth 400 https://github.com/pallets/flask.git /tmp/flask
python evals/real_diffs.py harvest --repo /tmp/flask --count 40 --out evals/data/real_diffs/dev_corpus.jsonl
AI_REVIEW_MODE=demo python evals/real_diffs.py review \
  --dataset evals/data/real_diffs/dev_corpus.jsonl \
  --out evals/data/real_diffs/dev_findings.jsonl \
  --manifest evals/data/real_diffs/dev_manifest.json
python evals/real_diffs.py score \
  --findings evals/data/real_diffs/dev_findings.jsonl \
  --manifest evals/data/real_diffs/dev_manifest.json

python evals/real_diffs.py recall-probe \
  --dataset evals/data/real_diffs/dev_corpus.jsonl \
  --manifest evals/data/real_diffs/dev_manifest.json \
  --count 30 --seed 20260901 \
  --out evals/data/real_diffs/recall_probe_dev.jsonl

python evals/real_diffs.py recall-score \
  --probe evals/data/real_diffs/recall_probe_dev.jsonl \
  --manifest evals/data/real_diffs/dev_manifest.json \
  --out reports/evals/recall_dev.json
```

`harvest` keeps commits that touch the requested suffix, skips merges, and caps diff size. Each case records the commit SHA, author, date, subject and a browsable URL, so any finding can be traced back to real code.

`review` writes one row per finding with the rule, the anchor, the evidence, and surrounding source, plus an empty `verdict`. It also writes a manifest recording the dataset hash, case count, review mode/model, prompt version, Git revision, reviewer-source hash, harness hash and timestamp. The source hash covers uncommitted reviewer code, which a Git revision alone does not identify. The manifest also binds the immutable finding content, so `score` rejects a findings file from another run while still allowing edits to `verdict` and `note`.

`score` reads the verdicts back and reports precision with a 95% Wilson interval, split by anchor granularity and per rule. Rows left unjudged are excluded rather than assumed correct; unknown verdict spellings fail instead of disappearing from the counts. Re-running `review` preserves matching labels by content-addressed finding id and refuses to discard a label that no longer matches.

`recall-probe` samples only successful static-review cases with zero emitted findings. Its first row binds the dataset, source manifest, reviewer source, seed, eligible population, exact rule inventory and scope definitions, scoring-harness hash, and immutable probe content. Every case starts with an empty verdict and empty `missed` list; the tool never invents a miss. `recall-score` rejects a wrong manifest, changed harness, changed diff, changed sample, invalid added-line anchor, or inconsistent label before reporting the silent-commit miss rate and descriptive rule-scope breakdown.

Read [`RECALL_LABELING_GUIDE.md`](RECALL_LABELING_GUIDE.md) before entering probe labels. An empty `missed` list is not clean until the case verdict is explicitly set to `clean`.

Read [`LABELING_GUIDE.md`](LABELING_GUIDE.md) before entering verdicts. It fixes the meaning of an actionable true positive, separates line-level and file-level claims, and prevents the standard from changing after the findings are visible.

### Rules for using it honestly

- **Adjudicate before tuning, or accept that the corpus is spent.** Once a rule is changed after looking at a corpus, that corpus is a development set. A reported number needs a fresh harvest that was not inspected first.
- **Do not fill verdicts from a model.** The label is the measurement.
- **Report the interval, not just the point.** Twenty judged findings support a range, not a figure.
- **Keep line-level and file-level claims apart.** They are different assertions and a combined average answers neither.
- **Do not call silence recall.** A commit with no emitted finding may be clean or may contain a missed defect. This workflow measures the precision/noise of emitted findings; recall requires labels for silent cases too.

The committed `verdicts_dev_pass*.jsonl` files predate that rule and contain model-assisted preliminary triage requested by the maintainer. They are development artifacts, carry no machine-readable annotator provenance, and must not support a published metric until a person verifies them.

Harvested corpora are gitignored: the diffs belong to their upstream projects and are regenerated from the commands above. Verdict files are keyed by commit SHA and are the part worth committing.
