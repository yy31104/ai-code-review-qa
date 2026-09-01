# AI-assisted PR review CLI

This repository is an experimental, human-in-the-loop reviewer for bounded Python/backend diffs. It reads a Git diff, runs ten named deterministic rules over the lines the diff adds, optionally asks an OpenAI model for further proposed findings, checks every finding against the diff it claims to describe, and writes artifacts for a developer to inspect.

It is not an autonomous reviewer, a security scanner, or evidence that an LLM finds bugs accurately. The offline eval suite measures the deterministic rules on labeled synthetic diffs. A labeled real-diff evaluation of provider quality has not been done yet, so nothing here should be read as a model accuracy claim.

## One bounded use case

The current target is a same-repository pull request containing a small Python/backend change. The useful output is a short list of proposed findings tied to changed files and added lines, plus test output and an explicit review status. A human decides whether any finding is correct and whether anything should be posted to GitHub.

The test-command detector also recognizes Node.js and .NET projects. That detection support is not a claim that review quality has been evaluated for those ecosystems.

## Try the deterministic path

```bash
git clone https://github.com/yy31104/ai-code-review-qa.git
cd ai-code-review-qa
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r backend/requirements.txt

AI_REVIEW_MODE=static python backend/app/main.py \
  --repo . \
  --base HEAD~1 \
  --head HEAD \
  --output backend/reports/review_report.html
```

This command reviews the most recent commit range and runs this repository's detected test command. Static mode runs the deterministic rules only: no model call, no credentials, and findings labelled `static_rules` with the rule that produced them.

> Only run the CLI against a repository you trust. The test runner executes `pytest`, `npm test`, or `dotnet test` on the host. Its timeout is not a sandbox.

## Data flow

1. `git_diff_reader.py` reads a working-tree diff or an explicit `<base>..<head>` range.
2. `test_runner.py` detects and executes a fixed test command for the target checkout.
3. `static_review.py` runs the named rules over the added lines and returns matches with evidence, or `llm_reviewer.py` calls the configured provider.
4. `schemas.py` validates the result shape with Pydantic.
5. `finding_grounding.py` drops any finding that does not point at a line this diff added.
6. `main.py` recomputes the review decision from review status, risk, and the real test result.
7. `diff_index.py` checks that inline anchors refer to added lines in the saved diff.
8. The pipeline renders an HTML report and optional dry-run GitHub JSON artifacts.
9. Opt-in workflows re-read the current PR diff before any GitHub write.

See [Architecture and trust boundaries](docs/ARCHITECTURE.md) for module ownership and invariants.

## Deterministic rules

Every finding the reviewer produces without a model comes from one of these rules. Each match is anchored to the added line it matched and carries that line as evidence.

| Rule | Category | Severity | What it matches |
| --- | --- | --- | --- |
| `broad_except` | possible_bug | medium | `except:` or `except BaseException:` |
| `swallowed_exception` | possible_bug | medium | an except block whose only statement is `pass` |
| `mutable_default_argument` | possible_bug | medium | `def f(x=[])`, `={}`, `=set()` |
| `assert_for_validation` | possible_bug | low | `assert` as a runtime guard outside test files |
| `subprocess_shell_true` | security_reliability | high | `shell=True` |
| `sql_string_interpolation` | security_reliability | high | `execute()` on a string built by f-string, `%`, `+` or `.format()` |
| `hardcoded_secret` | security_reliability | high | a credential-named variable assigned a literal |
| `dynamic_eval` | security_reliability | high | `eval()` / `exec()`, excluding `ast.literal_eval` |
| `yaml_unsafe_load` | security_reliability | high | `yaml.load()` with no explicit loader |
| `request_without_timeout` | security_reliability | medium | a `requests` call with no `timeout=`, outside test files |

What the rules deliberately do not do: they read only the lines a diff adds, they match lexically, and they have no import resolution, type information, or cross-function data flow. A match means the shape is usually a defect, not that the line is proven wrong. `severity` and `confidence` carry that distinction, and a human still decides.

Four properties are worth more than the rule list itself:

- **A clean diff produces no findings.** The reviewer stays silent rather than emitting generic advice, so a report with findings means something.
- **A rule fires on what the diff introduced, not on what it touched.** A unified diff marks a line as added when a trailing `# noqa` is deleted, a version bound is bumped, or a type hint is relaxed. The reviewer compares each match against the lines the same change block removed, and stays silent when the construct was already there. On a real corpus this one mechanism accounted for ten of thirteen false positives.
- **Evidence is always a line the diff added.** A statement joined across continuation lines can begin on unchanged context; quoting that context was both misleading and a hole in grounding.
- **Rules do not read string or comment contents.** `logger.info("never use shell=True")` matches nothing, and overlapping rules report once: a bare `except:` whose body is `pass` reports `swallowed_exception` only.

## Grounding

A finding is published only if it points at a line this diff added, and its quoted evidence matches that line. `finding_grounding.py` checks both and records a reason for every rejection:

| Reason | Meaning |
| --- | --- |
| `file_not_in_diff` | the cited file is not in the reviewed diff |
| `line_not_added` | the cited line exists but this diff did not add it |
| `evidence_mismatch` | the quoted line is not what the diff says is there |
| `binary_file` | the file has no readable added lines |

Rejected findings are kept in the report under "Rejected by grounding" and are never posted. This matters most for provider mode, where it is the mechanism that stops a plausible-sounding finding about a line that does not exist.

Grounding proves attribution, not correctness. A perfectly grounded finding can still be wrong.

## Measuring on real diffs

The eval suite above uses diffs written to exercise the rules, so it cannot say whether the rules are useful. `evals/real_diffs.py` answers the question that decides that: **of the findings reported on real code, what fraction are real?**

```bash
git clone --depth 400 https://github.com/pallets/flask.git /tmp/flask

python evals/real_diffs.py harvest \
  --repo /tmp/flask --count 40 --out evals/data/real_diffs/dev_corpus.jsonl

AI_REVIEW_MODE=static python evals/real_diffs.py review \
  --dataset evals/data/real_diffs/dev_corpus.jsonl \
  --out evals/data/real_diffs/dev_findings.jsonl \
  --manifest evals/data/real_diffs/dev_manifest.json

# Open dev_findings.jsonl and set "verdict" on each row to
# true_positive / false_positive / unsure. This step is human judgement.

python evals/real_diffs.py score \
  --findings evals/data/real_diffs/dev_findings.jsonl \
  --manifest evals/data/real_diffs/dev_manifest.json
```

`score` reports precision with a 95% Wilson interval, split by whether the finding claims something about a line or about the change set, and broken down per rule. The interval is there because these samples are small: 80% over ten judged findings is not the claim that 80% over five hundred would be.

The tool never generates verdicts. A publishable precision number is worth exactly what the person who adjudicated it is worth, so `review` leaves `verdict` empty and the labeling criteria live in [`evals/LABELING_GUIDE.md`](evals/LABELING_GUIDE.md). The manifest hashes the dataset, current reviewer source (including uncommitted code), harness, and immutable finding content so mismatched run artifacts fail instead of producing a plausible number.

### What the first run found

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

`recall-probe` creates that deterministic silent-commit sample with an empty case-level `adjudication`; this is intentionally separate from the finding-level `verdict` used above. `recall-score` reports the human-recorded miss rate and rule-scope breakdown. Despite the command name, this is a miss audit rather than classical `TP / (TP + FN)` recall. See [`evals/RECALL_LABELING_GUIDE.md`](evals/RECALL_LABELING_GUIDE.md).

## What the repository verifies

| Verified by tests or deterministic evals | Not verified yet |
| --- | --- |
| Each rule fires on its own labeled case at the expected file and line | Semantic correctness of provider findings |
| Each rule stays silent on its labeled near-miss control | Finding precision/recall on labeled **real** PR diffs |
| Schema parsing and explicit provider failure states | Calibration of the confidence values the rules assign |
| Risk-rule and review-decision regression behavior | Whether a model adds anything over these rules |
| Grounding rejects off-diff files, lines, and mismatched evidence | Safe execution of tests from an untrusted repository |
| GitHub payload escaping, caps, fingerprints, and mutation gates | Broad language/framework review quality |

Pydantic proves that output has the expected fields and types. It does not prove that a finding is true, useful, or complete.

The eval set is synthetic: the diffs were written to exercise the rules. It shows the rules do what they say on cases chosen to test them, which is a regression baseline, not a measurement of usefulness on real pull requests.

## Review modes and failure states

`ReviewResult` records three separate facts:

- `review_mode`: what was requested (`static` or `openai`);
- `review_status`: whether that request completed;
- `review_source`: where the findings came from (`static_rules`, `provider`, or `none`).

| Situation | Status | Source | CLI result |
| --- | --- | --- | --- |
| Deterministic run with reviewable Python additions | `completed` | `static_rules` | success; artifacts allowed |
| Provider output parsed and validated | `completed` | `provider` | success |
| Missing/unknown configuration | `configuration_error` | `none` | exit 2 |
| Provider request failed | `provider_failed` | `none` | exit 2 |
| Provider output failed schema validation | `invalid_output` | `none` | exit 2 |
| No diff or no Python files in scope | `no_changes` | `none` | success; no GitHub artifacts |
| Python change exists but has no readable added lines | `abstained` | `none` | success; no GitHub artifacts |

`review_mode` says which reviewer was requested; `review_status` says what happened during this run. A completed review with zero findings remains `completed`: status is never inferred from `findings == 0`. Failure, abstention, and no-change runs still save an HTML artifact, but do not emit GitHub artifacts. Provider failures never substitute static findings.

## Optional provider mode

Copy the example configuration and set your own key:

```bash
cp .env.example .env
```

```text
AI_REVIEW_MODE=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5.4-mini
```

Then run the same CLI command. OpenAI mode sends the changed-file list and a diff truncated to `MAX_DIFF_CHARS` to the Responses API. The report records the configured model, status, and source.

Do not use provider mode for private code unless you are authorized to send that code to the configured provider. Do not put `.env` in a shared archive: `.gitignore` prevents a Git commit, but it does not prevent a generic ZIP command from including the file.

## Tests and deterministic evals

```bash
python -m pytest -q
python evals/run_local.py --out reports/evals/results.json
python evals/render_report.py \
  --in reports/evals/results.json \
  --md reports/evals/summary.md \
  --html reports/evals/summary.html
```

The eval runner derives its case and check totals from `evals/data/golden_cases.jsonl` and prints them at runtime. Roughly half the cases are controls: near-miss code that must produce no finding (`yaml.safe_load`, `requests.get(url, timeout=5)`, `ast.literal_eval`, parameterised SQL, a placeholder key, a risk term inside a string). The dataset exercises the deterministic rules; it does not call OpenAI and must not be reported as model accuracy.

## Working-tree review

To inspect uncommitted changes in a trusted checkout:

```bash
AI_REVIEW_MODE=static python backend/app/main.py \
  --repo /path/to/trusted/repository \
  --output reports/review.html
```

Untracked files appear in the changed-file list, but Git does not provide line-level diff content for them until they are staged. Findings for those files therefore cannot be reliably anchored.

## GitHub artifacts and mutation gates

Generate artifacts locally without posting anything:

```bash
AI_REVIEW_MODE=static python backend/app/main.py \
  --repo . \
  --base HEAD~1 \
  --head HEAD \
  --output backend/reports/review_report.html \
  --emit-summary-comment reports/github/summary-comment.json \
  --emit-inline-review reports/github/inline-review.json \
  --emit-finding-fingerprints reports/github/finding-fingerprints.json \
  --head-sha HEAD_SHA
```

GitHub writes are off by default. The same-repository PR workflow uses separate repository-variable gates:

- `AI_REVIEW_SUMMARY_AUTOPOST=true` permits one marker-owned summary upsert;
- `AI_REVIEW_INLINE_COMMENTS=true` also permits capped inline comments after current-diff revalidation;
- `AI_REVIEW_STALE_ACTION=true` separately permits resolving eligible stale bot-owned threads.

Fork PRs are skipped by the mutation workflow. It uses `pull_request`, not `pull_request_target`, and scopes write permission to mutation jobs. See [Stale resolve runbook](docs/STALE_RESOLVE_RUNBOOK.md) for the narrow stale-thread path.

## Repository map

- `backend/app/main.py` — CLI orchestration and final status handling
- `backend/app/llm_reviewer.py` — rule/provider review paths, risk estimation, decision gate
- `backend/app/static_review.py` — the deterministic rules and the diff lexer they run on
- `backend/app/finding_grounding.py` — rejects findings that do not match the reviewed diff
- `backend/app/schemas.py` — review, finding, test, status, and provenance schemas
- `backend/app/diff_index.py` — unified-diff parsing and right-side line validation
- `backend/app/github_review_reporter.py` — escaped summary/inline payload construction
- `backend/app/test_runner.py` — trusted-checkout test detection and execution
- `evals/run_local.py` — credential-free regression cases for the rules
- `evals/real_diffs.py` — harvest, review and score real commit diffs from any git repository
- `tests/` — unit and CLI coverage for the pipeline and GitHub adapters

## Known limitations

- No precision number is reported yet. The development corpus has been adjudicated, but it was used to change the rules, so it cannot supply a reported figure.
- Recall is entirely unmeasured. No sample of silently-reviewed commits has been checked for defects the rules missed.
- Code moved between files still produces a false positive; attribution only sees one file's change blocks.
- There is no frozen, labeled real-diff provider evaluation yet, so there is no evidence that the model path beats the rule path.
- The eval diffs are synthetic and short. Real pull requests are longer, noisier, and will surface false positives these cases do not.
- The rules cover Python only, and only ten patterns of it.
- Provider raw output, token usage, latency, and cost are not persisted yet.
- Test execution is host-level and unsafe for arbitrary untrusted repositories.
- The project is not a GitHub App and does not mutate fork PRs.
- Diff egress is character-truncated, so large changes may receive incomplete provider context.
- Finding grounding validates file/line provenance, not semantic correctness.

The next substantive milestone is a small, versioned dataset of **real** Python/backend PR diffs with held-out labels, run against both the rules above and a provider, so the two can be compared on precision, recall, and cost. Until that exists, the honest claim is that this pipeline is reproducible and grounded, not that it is useful. UI expansion is deferred until then.

## License

[MIT](LICENSE)
