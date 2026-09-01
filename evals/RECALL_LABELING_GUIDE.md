# Silent-commit miss-audit labeling guide

This probe asks one bounded question: when the static reviewer emitted nothing, how often does a person still find an actionable defect in the changed lines? It is not classical `TP / (TP + FN)` recall because it samples only silent commits.

## Case verdict

Read the full diff and linked commit, then set exactly one `verdict`:

- `clean`: you inspected the case and found no actionable defect in the changed lines; `missed` must stay empty.
- `missed_defect`: you found at least one actionable defect; add every such defect to `missed`.
- `unsure`: the diff does not provide enough context or requires domain knowledge you do not have; keep `missed` empty and explain why in `note`.

An empty verdict means unjudged. Never turn it into `clean` merely because `missed` is empty.

## Missed-defect entry

Each entry is human-written:

```json
{
  "file": "src/package/service.py",
  "line": 42,
  "category": "correctness",
  "description": "The new negative value reaches a wait call that rejects it.",
  "rule_scope": "out_of_scope"
}
```

- `file` must be in `changed_files`.
- `line` must be a line the diff added.
- `category` is a short stable label such as `correctness`, `security`, `reliability`, or `test_gap`.
- `description` is one concrete, actionable line.
- `rule_scope` is the exact rule id from the probe header when that rule's recorded scope definition covers the defect, `out_of_scope` when no recorded definition covers it, or `unsure` when the boundary is genuinely unclear. Use the frozen `rule_scopes` text in the header rather than memory or intuition.

Do not use `rule_scope` to say whether the rule successfully detected the issue—the sampled cases are silent by construction. It records whether the missed issue falls inside the rule's stated responsibility.

## Consistency

Finish the first pass without changing rules. Then check that similar defects received the same category and scope decision. Put borderline reasoning in `note`. The development probe is spent as soon as its examples influence a rule; it cannot become a held-out result later.

Re-running `recall-probe` with the same source manifest, seed, count, and output path preserves matching labels. If the source review manifest changes, use a new output path or archive the old probe; the tool intentionally refuses to imply that two different source runs are the same experiment.
