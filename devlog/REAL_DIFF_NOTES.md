# Real-diff development notes

A 120-commit development corpus from `flask`, `httpx` and `requests` was reviewed. The committed `evals/data/real_diffs/verdicts_dev_pass*.jsonl` files are model-assisted preliminary triage produced at the maintainer's request, not human ground truth; their annotator provenance is not encoded in the artifacts, and their numbers require human verification before publication.

| Pass | Findings | Precision | Change made after it |
| --- | --- | --- | --- |
| 1 | 16 | 19% (3/16, CI 7–43%) | construct attribution; evidence anchored to added lines |
| 2 | 2 | 50% (1/2) | `new_function_without_test` deleted |
| 3 | 1 | 100% (1/1, CI 21–100%) | — |

Ten of the thirteen first-pass false positives had one cause: the diff touched a line that already contained the construct. Two came from a defect in the reviewer — evidence quoted an unchanged context line. One was code moved between files, which a single-file lexical rule cannot see.

`new_function_without_test` was deleted rather than narrowed further. It produced 12 of the 16 first-pass findings at 17% precision, and construct attribution suppressed both of its true positives too, because `-def f(...)` followed by `+def f(...)` looks identical whether the signature gained a parameter or the function is new. A lexical rule cannot make that distinction, so the rule went.

**These numbers are not a result.** The corpus is development data: the rules were changed after looking at it, so pass 3's 100% is measured on cases that shaped the rules. `1/1` is also not a sample. What the passes establish is the direction of the errors and the mechanism that caused them, not a precision figure.

Two things the numbers make concrete for the next step:

- At 0.008 findings per commit, reaching thirty judged findings needs roughly 3,600 commits. A held-out corpus has to be far larger than 120, or drawn from code that is reviewed less rigorously than these three libraries.
- 119 of 120 commits produced no finding. Four of those runs were `abstained` because they had no readable added Python lines; the remaining 115 completed-silent commits form the recall-probe population. These are output-volume and input-state facts, not evidence that the silent commits were clean.

`recall-probe` creates that deterministic silent-commit sample with an empty case-level `adjudication`; this is intentionally separate from the finding-level `verdict` used above. `recall-score` reports the human-recorded miss rate and rule-scope breakdown. Despite the command name, this is a miss audit rather than classical `TP / (TP + FN)` recall. See [`evals/RECALL_LABELING_GUIDE.md`](../evals/RECALL_LABELING_GUIDE.md).
