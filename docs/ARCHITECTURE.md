# Architecture and trust boundaries

This document explains what each part of the review pipeline owns, which guarantees are deterministic, and where human judgment is still required.

## Scope

The current product slice is a human-in-the-loop review of a bounded Python/backend diff. The pipeline may propose findings and prepare GitHub artifacts. It does not decide that code is safe to merge, and model output never authorizes a repository write by itself.

## Main path

```text
repository path + optional base/head
                |
                v
        GitDiffResult
        (diff, files, error)
                |
        +-------+-------+
        |               |
        v               v
   TestResult       ReviewResult
   (host command)   (static or provider)
        |               |
        +-------+-------+
                v
       final decision/status
                |
        +-------+---------+
        |                 |
        v                 v
     HTML report     GitHub JSON artifacts
                              |
                              v
                    separate opt-in workflows
```

## Module ownership

### Diff input — `git_diff_reader.py`

`read_git_diff()` returns a `GitDiffResult`; it does not interpret code. Working-tree mode reads tracked diffs plus untracked filenames. Commit-range mode uses an explicit `base..head` range. The PR workflow computes the merge base before invoking the CLI.

Important boundary: an untracked filename is observable, but there is no unified diff hunk for it. The pipeline must not claim a validated line anchor in that case.

### Test execution — `test_runner.py`

`detect_test_command()` selects one fixed command based on repository markers:

- Python: the current interpreter runs `-m pytest`;
- Node.js: `npm test`;
- .NET: `dotnet test`.

No command comes from a model. That prevents model-driven command injection, but it does not make execution safe: test code and dependencies belong to the target repository and run on the host. The 120-second timeout limits duration only; it is not process, network, filesystem, memory, or secret isolation.

### Deterministic rules — `static_review.py`

`analyze_diff()` owns every finding that is produced without a model.

It reads the right side of the diff only. Physical lines are joined into logical lines while brackets are open, so a call split across lines is matched once, at the first line the diff added. Two views of each logical line exist: `code` keeps string contents, and `code_outside_strings` empties them. A rule declares which view it matches on, which is why `logger.info("shell=True")` produces nothing while `cursor.execute(f"... {x}")` produces a finding.

Each match carries the rule id, the anchored line, and that line as evidence. Ordering is `(path, line, rule_id)`, so two runs over one diff are byte-identical.

Before a match is kept, `_preexisting_rules()` re-runs the rules over the lines the same change block *removed*. If the rule already matched there, the diff edited a line that had the construct rather than introducing it, and the match is dropped. This is what separates a review comment about this change from a comment about someone's earlier one.

Every `LogicalLine` carries two views of its text for this reason: `code` is the whole joined statement, and `added_code` is only the part the diff added. A rule matches on `added_code` when a construct sitting in unchanged context must not count — `def send_file(` is often context while the diff only relaxed a parameter's type hint.

Boundary: these rules are lexical. There is no import resolution, type information, or data flow. A match is a shape that is usually a defect. Nothing here establishes that a line is wrong.

Rule shape is settled against real code, not by argument. On a 120-commit corpus from three public Python projects, hand adjudication put first-pass precision at 19%, with ten of thirteen false positives caused by the missing attribution check above. That is what added attribution, fixed evidence anchoring, and deleted `new_function_without_test` outright. `evals/real_diffs.py` owns that loop, and `evals/LABELING_GUIDE.md` fixes the standard the verdicts are held to.

### Review generation — `llm_reviewer.py`

`review_diff()` selects one of two intentional paths:

- `static`: `static_review` rules create visibly labeled `static_rules` findings without a network call;
- `openai`: the changed-file list and truncated diff are sent to the configured provider and parsed as `ProviderReview`.

`ProviderReview` is deliberately narrower than `ReviewResult`. A model fills a summary and a list of proposed findings, and nothing else. Risk level, review status, provenance, test results and the review decision are computed by the pipeline, so a model response cannot move the merge gate by asserting a field.

`findings` is the single source of truth. The flat lists the report renders (`possible_bugs`, `security_reliability_concerns`, `missing_tests`, `suggested_test_cases`) are a projection of it, so the report cannot show a concern that no finding backs.

Risk starts as a keyword match over the diff text and is then escalated by `escalate_risk()` to match the most severe grounded finding. Escalation is one-directional: a finding can add human review, never remove it. Without it, a real defect in a file whose identifiers contain no risk term scored Low and the gate said `looks_good`.

### Finding grounding — `finding_grounding.py`

`ground_findings()` splits findings into grounded and rejected. A finding is grounded when its file is in the diff, its line is one the diff added, and its quoted evidence matches that line after whitespace normalisation. Rejections carry a reason (`file_not_in_diff`, `line_not_added`, `evidence_mismatch`, `binary_file`) and are kept in the artifact rather than dropped silently.

Boundary: grounding establishes attribution. A grounded finding can still be semantically wrong, and a human review remains the decision point.

Mode, status, and source are separate. Mode records the requested reviewer; status records the run outcome; source records where accepted findings came from. A static review that inspects Python additions is `completed` even when it emits zero findings. An empty or out-of-scope diff is `no_changes`; an in-scope Python change without readable added-line evidence is `abstained`. A failed provider request has source `none` and contains no substituted static findings. Configuration, provider, and validation failures produce different statuses so downstream code can fail closed.

`_estimate_risk()` is a heuristic over changed filenames and tokenized diff text. It is deterministic, not learned, and not a vulnerability detector.

### Schema boundary — `schemas.py`

Pydantic validates types, required fields, enums, and confidence bounds. This prevents malformed objects from reaching report and payload code. It does not establish any of the following:

- that a finding describes a real defect;
- that cited evidence is sufficient;
- that no important defect was missed;
- that confidence is calibrated;
- that a suggested action is safe.

Those are evaluation and human-review questions, not schema questions.

### Final decision — `main.py` and `llm_reviewer.py`

The CLI replaces the provider's placeholder test result with the actual `TestResult`, then calls `derive_final_decision()`.

Provider failure and abstention are fail-closed decision gates and yield `needs_human_review`; `no_changes` records that no automated judgment was made. Otherwise `derive_decision()` combines deterministic risk and test status. Even `looks_good` means only that the current deterministic signals did not require escalation; it is not merge approval.

### Diff grounding — `diff_index.py`

`parse_unified_diff()` records valid right-side added lines. A finding can become an inline candidate only when its normalized path exists in the diff and its line is in that set.

This proves that the anchor belongs to the saved diff. It does not prove the finding's claim.

### Reporting — `report_generator.py` and `github_review_reporter.py`

The HTML template uses Jinja autoescaping. GitHub Markdown is separately escaped and truncated. The reporter routes unanchored, low-confidence, low-severity, summary-only, and overflow findings away from inline comments.

Generated artifacts do not post themselves. They are inputs to separately gated workflows.

## GitHub mutation sequence

1. The build job creates the HTML report, summary body, inline candidates, and fingerprints.
2. Summary posting requires `AI_REVIEW_SUMMARY_AUTOPOST=true`.
3. Inline posting additionally requires `AI_REVIEW_INLINE_COMMENTS=true`.
4. Immediately before inline posting, the workflow recomputes the current diff and revalidates each path/line.
5. Existing finding fingerprints are removed to avoid duplicate comments.
6. Stale detection and thread mapping produce artifacts before any stale action.
7. Thread resolution requires the separate `AI_REVIEW_STALE_ACTION=true` gate and only selects unresolved, marker-owned comments authored by `github-actions[bot]`.

The mutation path is same-repository only and does not use `pull_request_target`.

## Evaluation boundary

`evals/run_local.py` forces static mode and loads its case count from the JSONL dataset. It checks deterministic rules, schema output, decisions, statuses, and anchoring behavior. This is a regression suite for pipeline changes.

It does not measure provider precision, recall, false-positive rate, abstention, cost, latency, or reviewer acceptance. Reporting its case/check pass rate as LLM accuracy would be incorrect because the model is not called and the expectations describe current deterministic behavior.

## Invariants

- Provider failure cannot be represented as a successful static review.
- A failed provider run contains no provider or static findings, emits no GitHub artifacts, and returns a non-zero CLI status.
- `no_changes` and `abstained` emit no GitHub artifacts but are valid CLI outcomes with exit status zero.
- A completed review with zero findings remains `completed`; status is not derived from finding count.
- Test results used in the final decision come from the test runner, not the model.
- Inline comment paths and lines must exist in the current right-side diff.
- GitHub writes require explicit repository-variable gates.
- Model-derived text is escaped before entering GitHub Markdown.
- No command is constructed from model output.

## Tracing common changes

### Change one risk rule

Start in `RISKY_TERMS`, `_risk_tokens()`, or `_estimate_risk()` in `llm_reviewer.py`. Predict which risk and decision expectations will change, then update `tests/test_risk_estimation.py` and only the affected JSONL eval cases. Run pytest and the eval runner.

### Change the finding schema

Start in `Finding` or `ReviewResult` in `schemas.py`. Then update the provider instructions, deterministic static builder, HTML template, GitHub reporter, and schema/report/payload tests. A new field is not complete until both provider and static paths populate it deliberately.

### Change GitHub posting behavior

Start with pure payload or selector functions under `backend/app/github_*.py`. Preserve marker ownership, escaping, line revalidation, caps, and same-repository gates. Workflow permission changes require an explicit reason and tests for the pure selection logic.

## Known architectural gaps

- Provider input/output, token usage, latency, cost, and reproducibility identities are not persisted.
- There is no labeled real-diff provider-quality evaluation.
- Test execution is not isolated from the host.
- The provider prompt treats the diff as untrusted text but has no fixed prompt-injection eval set yet.
- The stale-thread execute path does not paginate the inner `comments(first: 100)` connection.
