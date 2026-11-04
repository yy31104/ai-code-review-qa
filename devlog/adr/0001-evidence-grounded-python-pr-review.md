# ADR 0001 — Evidence-grounded review for Python backend pull requests

- Status: accepted
- Date: 2026-09-01
- Applies to: `recovery/trustworthy-review-core` (4 commits ahead of `origin/main` at `406f583` when accepted)
- Supersedes: the portfolio framing in the pre-recovery README

## Correction to the brief this decision was requested under

The brief stated that `static_review.py`, `finding_grounding.py` and `real_diffs.py` were absent
and should be treated as possibly-lost candidate work. That is true of `origin/main` and of the
uploaded archive. It is not true of the working tree, where all three exist, 197 tests pass, and
42/42 synthetic cases pass. The work is now preserved on `recovery/trustworthy-review-core`.

This matters for sequencing. Most of the brief's Gate A and Gate B are already implemented, so
re-running those PRs would redo finished work. What follows starts from measured state.

## Context

The reviewer produced identical canned prose for every diff until this recovery, and the eval
suite graded that prose. Both are now replaced. The measured state is:

| Property | Value | How it was established |
| --- | --- | --- |
| Deterministic rules | 10 lexical rules over added lines | `backend/app/static_review.py` |
| Synthetic regression | 42 cases, 109 checks | `evals/run_local.py` |
| Real-diff provisional precision, pass 1 | 19% (3/16, 95% CI 7–43%) | model-assisted triage, `verdicts_dev_pass1.jsonl` |
| Real-diff precision, pass 3 | 1/1 — not a sample | `verdicts_dev_pass3.jsonl` |
| Emission rate | 0.008 findings per commit | 1 finding over 120 commits |
| Recall | unmeasured | no silent commit has been adjudicated |

The last two rows are the finding that drives this ADR, and the brief does not have them.

**At 0.008 findings per commit, a held-out corpus of 120 commits yields about one finding.**
Thirty judged findings would need roughly 3,600 commits of comparably mature code. The brief's
plan — freeze a held-out set, measure precision, then compare against an LLM — cannot execute at
that emission rate. Precision is not the measurable quantity right now; coverage is.

## Decision

### 1. Target user and bounded use case

A developer reviewing a Python backend pull request in a repository they control, who wants a
short list of machine-checkable concerns before a human reads the diff. Not a security scanner,
not a merge gate, not a multi-language tool.

### 2. Product claim and non-goals

Claim: *proposed findings, each traceable to a line this diff added, with the reviewer's own
error rate published.* The differentiator is not the rules; it is that the numbers exist and are
adverse. That differentiator does not exist until a held-out number is published, so the claim
stays unmade until then.

Non-goals for the next six weeks: languages other than Python, automatic fixes, a GitHub App,
a dashboard, a service or database, multi-agent orchestration, and any container work not
implementing a named isolation boundary.

### 3. Deterministic versus LLM responsibilities

Deterministic code owns everything that must be reproducible: file selection, diff parsing,
line attribution, grounding, deduplication, severity policy, run status, and every gate. The
model proposes candidate findings and nothing else. It never sets provider success, grounding
status, merge advice, publication, provenance, or final severity.

Given the emission rate above, the working assumption is inverted from the brief's: the static rules
are a floor and a control, not the product. Whether the LLM adds enough over that floor to be
worth its cost is the central open question, not a validation step at the end.

### 4. Run status and failure model

The six run states are `completed`, `configuration_error`, `provider_failed`, `invalid_output`,
`abstained` and `no_changes`. `no_changes` means there is no reviewable Python diff. `abstained`
means an in-scope Python change exists but the reviewer lacks readable added-line evidence. A
provider failure produces no findings, no GitHub artifact, and a documented non-zero exit code.
Static success is `mode=static`, `status=completed`, `source=static_rules`; status is not inferred
from whether the finding list is empty.

### 5. Finding schema and grounding contract

A finding carries file, line, category, severity, confidence, rule id or model provenance, the
message, and evidence. For a line-anchored finding, grounding checks three things against the
current diff: the file is in it, the line is one the diff added, and the quoted evidence matches
that line's text. A file-level finding proves only that the file is in the diff; a summary-level
finding has no location to ground. Rejections record a reason and are counted separately from
model judgment.

Grounding proves provenance, not correctness. A grounded finding can still be wrong; the
provisional pass-1 triage marked 81% false, but that figure is not publishable human ground truth.

### 6. Evaluation contract

Three tiers, kept apart: `synthetic/` (regression), `development/` (rule iteration, already
spent), `heldout/` (frozen, never inspected before reporting). Every benchmark records dataset
hash, code revision, reviewer type, model, prompt version, config, raw output, grounding
decisions, human labels, tokens, cost, latency.

Finding-level labels use `verdict` (`true_positive`, `false_positive`, `unsure`); recall-probe
case labels use `adjudication` (`clean`, `missed_defect`, `unsure`). Publishable labels are entered
by a person. The three committed finding-verdict files were produced by
a model at the maintainer's explicit instruction and are only first-pass triage; the artifacts do
not record annotator provenance, so they require human verification before any of them backs a
published number.

### 7. Untrusted-repository execution boundary

`subprocess.run(..., timeout=...)` is not a sandbox. Test execution stays limited to trusted and
self repositories, off by default elsewhere, and fork PRs never receive secrets. No container work
until a specific isolation requirement is named.

### 8. GitHub mutation boundary

Artifact first, human approval second, revalidation against the current head third, write last.
Every write stays behind its own repository-variable gate. Fork PRs are skipped.

### 9. Module boundaries

Adopt the analyzer/reporter separation: reviewers (static, provider, hybrid) emit findings;
reporters (terminal, JSON, HTML, GitHub Check, inline) consume them and share no reviewer code.
Findings are already the single source of truth — the concern lists are projections — so this is
a refactor of the output side only.

### 10. Existing GitHub lifecycle code

Retain and freeze: marker upsert, inline revalidation, fingerprint dedupe, fork skip, mutation
gates, stale detection and resolve. It works and it encodes real safety reasoning. Add nothing to
it. Roughly 950 lines of application code and 1,400 lines of tests currently serve the delivery of
findings whose usefulness is unmeasured; that ratio is the project's core imbalance and it is
corrected by not extending this layer, not by deleting it.

## Rejected

| Proposal | Why |
| --- | --- |
| Rename the project now | No evidence it changes any outcome; costs URL, history and link churn. Revisit at v0.1. |
| PyPI / pipx release next | Distributing a reviewer whose error rate is unpublished is the same mistake in a new package. |
| Dashboard, service, database | No review history exists to display or query. |
| Multi-agent, LangGraph | A single reviewer has not been measured. |
| Kubernetes, Docker | No named deployment or isolation requirement. |
| GitHub App | The Action path is not yet used by anyone. |
| Broad language support | One language is not yet demonstrated. |
| More comment-lifecycle work | See section 10. |

## Discard rather than polish

- **The TODO-marker heuristic.** It fired on ordinary commits and
  dilute every precision number without ever identifying a defect. Remove before the held-out run.
- **`suggested_test_cases` and the concern-list projections.** Harmless but they exist only to
  fill HTML sections. Reassess once the reporter split lands.
- **The obsolete placeholder vocabulary** throughout README, workflows and schema. Replaced by `static` in PR-2.

## PR sequence

| PR | Content | Acceptance |
| --- | --- | --- |
| 1 | Publish the recovery branch to `main` | Public README carries no claim the repository cannot verify; CI green on the PR |
| 2 | Complete the state model; finalize `static`; drop the TODO-marker heuristic | Each of the six states has a test and a smoke test asserting exit code and artifacts |
| 3 | **Recall probe**: adjudicate 30 silently-reviewed commits | A committed adjudication file records what the rules missed and a taxonomy of miss types |
| 4 | Corpus selection study | Emission rate measured on ≥3 corpora of differing review rigour; held-out size derived from it |
| 5 | Freeze the held-out benchmark | Split created and hashed before any result is read; documented as never inspected |
| 6 | Provider instrumentation | A run is reproducible from its manifest; a budget overrun aborts before spending |
| 7 | Static vs LLM vs hybrid on the frozen corpus, ≥3 repetitions | Reports what the LLM adds, what it costs per true positive, and its added false-positive rate |
| 8 | Reporter split | Adding a reporter touches no reviewer module |
| 9 | Adversarial hardening | Fixed cases for injection, forged delimiters, invisible Unicode, oversized diffs, secret-like text |
| 10 | v0.1: packaging and Action | Zero to first review under ten minutes; Action defaults to a Check, never inline comments |

PR-3 is the gate. If the rules miss most real defects, the lexical baseline is a control rather
than a product, and PRs 6–7 become the main line of work. If they miss little, PR-4 onward is
worth the cost. That question is answerable in a day and decides several weeks of direction, so it
comes before any further corpus investment.

## Ownership condition

This ADR is void as a portfolio claim until its author can, without notes or AI, draw the
pipeline, add a rule with a positive and a control case, predict which tests move, explain one
real false positive and the decision it drove, and explain why grounding does not imply
correctness.

## First implementation task

Build the recall probe (PR-3 tooling only, no adjudication):

Add `evals/real_diffs.py recall-probe`. Given a harvested corpus and a manifest, sample N commits
the reviewer reported nothing on, using a seed recorded in the output. For each, emit a row with
the commit URL, subject, changed files, the full diff, an empty `adjudication`, an empty `missed` list and
an empty `note`. A person marks the case `clean`, `missed_defect` or `unsure`. Each missed defect
records its file, added line, category, one-line description, and `rule_scope`: the exact recorded
rule id, `out_of_scope`, or `unsure`. These extra fields distinguish unreviewed cases from reviewed
clean cases and make the scope share computable.

Add `evals/real_diffs.py recall-score`, which reads those rows back and reports: commits sampled,
commits with at least one missed defect, total missed defects by category, and the share of missed
defects that fall inside the 10 rules' stated scope — the last being the number that decides
whether the rules are worth extending.

This is a silent-commit miss audit, not classical `TP / (TP + FN)` recall: it samples only commits
where the reviewer emitted nothing and does not fully label the emitted-finding population.

The tooling can be implemented before PR-2, but the development sample must be regenerated and
only then adjudicated after the TODO-marker heuristic is removed and the static rule inventory is final.

Reuse the existing manifest binding, content-addressed ids, adjudication validation and label-preserving
rerun. Do not generate `missed` entries. Do not change any rule. Tests must cover sampling
determinism under a fixed seed, label preservation across reruns, and refusal to score a probe
file whose manifest does not match.
