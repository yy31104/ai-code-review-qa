"""Measure the reviewer on real commit diffs instead of synthetic ones.

`evals/run_local.py` proves the rules behave as written on diffs that were
written to exercise them. It cannot say whether those rules are useful, because
a case that was authored to trip a rule will trip it. This module answers the
question that decides whether the reviewer is usable at all:

    of the findings it reports on real code, what fraction are real?

The workflow is five commands and two pieces of human judgement:

    harvest  pull real commit diffs out of a git repository, with provenance
    review   run the current reviewer over them and emit one row per finding
    score    read back the rows a human labelled and report precision
    recall-probe  sample commits where the reviewer emitted nothing
    recall-score  summarize human-recorded misses in that sample

The labels are the point. They are not generated here, and they must not be:
a precision number is only worth what the person who adjudicated it is worth.
Nothing in this module calls a model unless AI_REVIEW_MODE says to, so the
deterministic baseline can be measured at zero cost before anything is spent
comparing a provider against it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from llm_reviewer import DEFAULT_OPENAI_MODEL, REVIEW_PROMPT_VERSION, review_diff  # noqa: E402
from static_review import RULE_IDS  # noqa: E402

#: Diffs longer than this are skipped. A very large commit is not a review unit
#: a human can adjudicate fairly, and it would dominate the sample.
MAX_DIFF_CHARS = 20000
#: Lines of surrounding source stored with each finding so the adjudicator can
#: judge it without opening the repository.
CONTEXT_RADIUS = 3
VERDICTS = ("true_positive", "false_positive", "unsure")
PROBE_VERDICTS = ("clean", "missed_defect", "unsure")
PROBE_SCHEMA_VERSION = 1
OUT_OF_SCOPE = "out_of_scope"
SCOPE_UNSURE = "unsure"
STATIC_SUCCESS_STATUSES = frozenset({"completed", "demo", "static"})
STATIC_REVIEW_SOURCES = frozenset({"demo_rules", "static_rules"})
RULE_SCOPE_DESCRIPTIONS = {
    "broad_except": "An added bare except or except BaseException handler.",
    "swallowed_exception": "An added exception handler whose only statement is pass.",
    "mutable_default_argument": "An added function parameter defaulting to a mutable list, dict, or set.",
    "assert_for_validation": "An added runtime assert outside tests used as the only guard.",
    "subprocess_shell_true": "An added subprocess call with shell=True.",
    "sql_string_interpolation": "An added execute call whose SQL is built by interpolation or concatenation.",
    "hardcoded_secret": "An added credential-named variable assigned a literal value.",
    "dynamic_eval": "An added eval or exec call, excluding ast.literal_eval.",
    "yaml_unsafe_load": "An added yaml.load call without an explicit safe loader.",
    "request_without_timeout": "An added requests call without timeout outside tests.",
    "todo_marker": "An added TODO, FIXME, or XXX marker.",
}
REVIEWER_SOURCE_PATHS = (
    APP_DIR / "diff_index.py",
    APP_DIR / "finding_grounding.py",
    APP_DIR / "llm_reviewer.py",
    APP_DIR / "schemas.py",
    APP_DIR / "static_review.py",
)
ADJUDICATION_FIELDS = frozenset({"verdict", "note"})
PROBE_ADJUDICATION_FIELDS = frozenset({"verdict", "missed", "note"})


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _commit_url(remote: str, sha: str) -> str | None:
    """Turn an origin URL into a browsable commit URL, or None if unknown."""
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", remote)
    if match is None:
        return None
    return f"https://github.com/{match.group('owner')}/{match.group('repo')}/commit/{sha}"


# ---------------------------------------------------------------------------
# harvest
# ---------------------------------------------------------------------------


def harvest(repo: Path, count: int, suffix: str, skip_merges: bool = True) -> list[dict[str, Any]]:
    """Collect real commit diffs from ``repo``, newest first.

    Only commits that touch at least one file with ``suffix`` are kept, and each
    stored diff is restricted to those files. Merge commits are skipped by
    default: their diff against the first parent is not what a reviewer sees.
    """
    remote = ""
    try:
        remote = _git(repo, "remote", "get-url", "origin").strip()
    except RuntimeError:
        pass

    log_args = ["log", "--format=%H%x1f%an%x1f%aI%x1f%s", f"-n{count * 6}"]
    if skip_merges:
        log_args.append("--no-merges")

    cases: list[dict[str, Any]] = []
    for entry in _git(repo, *log_args).splitlines():
        if len(cases) >= count:
            break
        parts = entry.split("\x1f")
        if len(parts) != 4:
            continue
        sha, author, date, subject = parts

        names = _git(repo, "show", "--name-only", "--format=", sha).split()
        targeted = sorted({name for name in names if name.endswith(suffix)})
        if not targeted:
            continue

        diff = _git(repo, "show", "--format=", "--unified=3", sha, "--", *targeted)
        if not diff.strip() or len(diff) > MAX_DIFF_CHARS:
            continue

        cases.append(
            {
                "id": f"{repo.name}@{sha[:12]}",
                "repo": remote or str(repo),
                "commit_sha": sha,
                "commit_url": _commit_url(remote, sha),
                "author": author,
                "date": date,
                "subject": subject,
                "changed_files": targeted,
                "diff": diff,
            }
        )

    return cases


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunIdentity:
    """Everything a result has to be tagged with to stay comparable later."""

    dataset_sha256: str
    dataset_cases: int
    review_mode: str
    review_model: str | None
    prompt_version: str
    code_revision: str
    reviewer_sha256: str
    harness_sha256: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_sha256": self.dataset_sha256,
            "dataset_cases": self.dataset_cases,
            "review_mode": self.review_mode,
            "review_model": self.review_model,
            "prompt_version": self.prompt_version,
            "code_revision": self.code_revision,
            "reviewer_sha256": self.reviewer_sha256,
            "harness_sha256": self.harness_sha256,
            "started_at": self.started_at,
        }


def _files_sha256(paths: Iterable[Path]) -> str:
    """Hash file names and bytes so uncommitted source has an identity too."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            name = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            name = str(path.resolve())
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _identity(dataset_path: Path, cases: list[dict[str, Any]]) -> RunIdentity:
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    try:
        revision = _git(REPO_ROOT, "rev-parse", "HEAD").strip()
    except RuntimeError:
        revision = "unknown"
    review_mode = os.getenv("AI_REVIEW_MODE", "demo").strip().lower() or "demo"
    return RunIdentity(
        dataset_sha256=digest,
        dataset_cases=len(cases),
        review_mode=review_mode,
        review_model=(
            os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
            if review_mode == "openai"
            else None
        ),
        prompt_version=REVIEW_PROMPT_VERSION,
        code_revision=revision,
        reviewer_sha256=_files_sha256(REVIEWER_SOURCE_PATHS),
        harness_sha256=_files_sha256((Path(__file__),)),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )


def _context_lines(diff: str, file: str, line: int) -> list[str]:
    """Return the added lines near ``line`` as they appear in the diff."""
    from diff_index import normalize_path, parse_unified_diff

    diff_file = parse_unified_diff(diff).files.get(normalize_path(file))
    if diff_file is None:
        return []

    window = range(line - CONTEXT_RADIUS, line + CONTEXT_RADIUS + 1)
    return [
        f"{'>' if number == line else ' '} {number:>5}  {diff_file.right_source[number]}"
        for number in window
        if number in diff_file.right_source
    ]


def _finding_id(case_id: str, finding: Any) -> str:
    """Identify a claim by its content, not its position in provider output."""
    claim = {
        "case_id": case_id,
        "rule_id": finding.rule_id,
        "category": finding.category,
        "file": finding.file,
        "line": finding.line,
        "message": finding.message,
        "evidence": finding.evidence,
    }
    encoded = json.dumps(claim, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{case_id}#{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def review_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the reviewer over every case. Returns (finding rows, case rows)."""
    rows: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []

    for case in cases:
        started = time.perf_counter()
        review = review_diff(str(case["diff"]), [str(path) for path in case["changed_files"]])
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        case_rows.append(
            {
                "case_id": case["id"],
                "commit_url": case.get("commit_url"),
                "subject": case.get("subject", ""),
                "changed_files": case["changed_files"],
                "diff_chars": len(str(case["diff"])),
                "review_status": review.review_status,
                "review_source": review.review_source,
                "risk_level": review.risk_level,
                "findings": len(review.findings),
                "rejected_findings": len(review.rejected_findings),
                "latency_ms": elapsed_ms,
            }
        )

        for finding in review.findings:
            rows.append(
                {
                    # Content-addressed so reordering findings does not attach
                    # a human verdict to a different claim.
                    "finding_id": _finding_id(str(case["id"]), finding),
                    "case_id": case["id"],
                    "commit_url": case.get("commit_url"),
                    "rule_id": finding.rule_id,
                    "category": finding.category,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "file": finding.file,
                    "line": finding.line,
                    "message": finding.message,
                    "evidence": finding.evidence,
                    "context": _context_lines(str(case["diff"]), str(finding.file), finding.line)
                    if finding.file and finding.line is not None
                    else [],
                    # Filled in by a human. Leave "" for anything not judged.
                    "verdict": "",
                    "note": "",
                }
            )

    return rows, case_rows


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def _validate_verdicts(rows: list[dict[str, Any]]) -> None:
    invalid: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for position, row in enumerate(rows, start=1):
        finding_id = str(row.get("finding_id") or f"row {position}")
        if finding_id in seen:
            duplicates.append(finding_id)
        seen.add(finding_id)

        verdict = row.get("verdict", "")
        if verdict not in {"", *VERDICTS}:
            invalid.append(f"{finding_id}={verdict!r}")

    if duplicates:
        raise ValueError(f"Duplicate finding_id value(s): {', '.join(sorted(set(duplicates)))}")
    if invalid:
        raise ValueError(
            "Invalid verdict value(s): "
            + ", ".join(invalid)
            + f". Expected an empty string or one of: {', '.join(VERDICTS)}"
        )


def _finding_set_sha256(rows: list[dict[str, Any]]) -> str:
    """Hash finding claims while allowing a human to edit verdict and note."""
    immutable_rows = [
        {key: value for key, value in row.items() if key not in ADJUDICATION_FIELDS}
        for row in rows
    ]
    immutable_rows.sort(key=lambda row: str(row.get("finding_id", "")))
    encoded = json.dumps(
        immutable_rows,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _merge_adjudications(
    rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Carry matching human labels forward and refuse to discard old ones."""
    _validate_verdicts(existing_rows)
    labelled = {
        str(row["finding_id"]): row
        for row in existing_rows
        if row.get("verdict") or row.get("note")
    }
    new_ids = {str(row["finding_id"]) for row in rows}
    orphaned = sorted(set(labelled) - new_ids)
    if orphaned:
        raise ValueError(
            "Review would discard adjudication for finding_id value(s): "
            + ", ".join(orphaned)
            + ". Use a new --out path or archive the labelled file first."
        )

    merged: list[dict[str, Any]] = []
    for row in rows:
        updated = dict(row)
        existing = labelled.get(str(row["finding_id"]))
        if existing is not None:
            updated["verdict"] = existing.get("verdict", "")
            updated["note"] = existing.get("note", "")
        merged.append(updated)
    return merged


def _validate_artifacts(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    """Prove that the labelled rows belong to the supplied run manifest."""
    _validate_verdicts(rows)
    identity = manifest.get("identity")
    expected_harness = identity.get("harness_sha256") if isinstance(identity, dict) else None
    current_harness = _files_sha256((Path(__file__),))
    if expected_harness != current_harness:
        raise ValueError(
            "The scoring harness differs from the version recorded in this manifest. "
            "Rerun `review` so the run identity matches the current evaluator."
        )

    expected = manifest.get("findings_artifact")
    if not isinstance(expected, dict):
        raise ValueError(
            "Manifest does not bind a findings artifact. Rerun `review` with this version "
            "before scoring."
        )

    expected_count = expected.get("count")
    expected_sha256 = expected.get("sha256")
    if expected_count != len(rows):
        raise ValueError(
            f"Findings/manifest count mismatch: manifest has {expected_count!r}, "
            f"file has {len(rows)}."
        )
    actual_sha256 = _finding_set_sha256(rows)
    if expected_sha256 != actual_sha256:
        raise ValueError(
            "Findings do not match this manifest (immutable content hash mismatch). "
            "Use the manifest produced with this findings file."
        )


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Reported because these samples are small. A precision of 0.8 over 20 judged
    findings is not the same claim as 0.8 over 500, and the interval is what
    stops the smaller one from being written up as the larger one.
    """
    if total == 0:
        return (0.0, 0.0)

    phat = successes / total
    denominator = 1 + z**2 / total
    centre = phat + z**2 / (2 * total)
    margin = z * math.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2))
    return (
        round(max(0.0, (centre - margin) / denominator), 4),
        round(min(1.0, (centre + margin) / denominator), 4),
    )


def score(rows: list[dict[str, Any]], case_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute precision from adjudicated rows. Unjudged rows are excluded."""
    _validate_verdicts(rows)
    judged = [row for row in rows if row.get("verdict") in {"true_positive", "false_positive"}]
    unsure = [row for row in rows if row.get("verdict") == "unsure"]
    unjudged = [row for row in rows if not row.get("verdict")]

    true_positives = sum(1 for row in judged if row["verdict"] == "true_positive")
    low, high = wilson_interval(true_positives, len(judged))

    per_rule: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in judged:
        grouped[str(row.get("rule_id") or "provider")].append(row)

    for rule_id, rule_rows in sorted(grouped.items()):
        hits = sum(1 for row in rule_rows if row["verdict"] == "true_positive")
        rule_low, rule_high = wilson_interval(hits, len(rule_rows))
        per_rule[rule_id] = {
            "judged": len(rule_rows),
            "true_positives": hits,
            "precision": round(hits / len(rule_rows), 4),
            "precision_95ci": [rule_low, rule_high],
        }

    # A line-anchored finding claims "this line is a defect". A file-level one
    # claims something about the change set. Averaging them produces a number
    # that answers neither question, so they are reported apart.
    by_anchor: dict[str, dict[str, Any]] = {}
    for label, subset in (
        ("line", [row for row in judged if row.get("line") is not None]),
        ("file", [row for row in judged if row.get("line") is None]),
    ):
        if not subset:
            continue
        hits = sum(1 for row in subset if row["verdict"] == "true_positive")
        anchor_low, anchor_high = wilson_interval(hits, len(subset))
        by_anchor[label] = {
            "judged": len(subset),
            "true_positives": hits,
            "precision": round(hits / len(subset), 4),
            "precision_95ci": [anchor_low, anchor_high],
        }

    silent_cases = sum(1 for case in case_rows if case["findings"] == 0)
    return {
        "by_anchor": by_anchor,
        "coverage": {
            "cases": len(case_rows),
            "cases_with_no_finding": silent_cases,
            "silence_rate": round(silent_cases / len(case_rows), 4) if case_rows else 0.0,
            "findings_total": len(rows),
            "findings_per_case": round(len(rows) / len(case_rows), 3) if case_rows else 0.0,
            "rejected_by_grounding": sum(case["rejected_findings"] for case in case_rows),
        },
        "adjudication": {
            "judged": len(judged),
            "unsure": len(unsure),
            "unjudged": len(unjudged),
        },
        "precision": {
            "true_positives": true_positives,
            "false_positives": len(judged) - true_positives,
            "value": round(true_positives / len(judged), 4) if judged else None,
            "precision_95ci": [low, high] if judged else None,
        },
        "per_rule": per_rule,
        "rule_frequency": dict(Counter(str(row.get("rule_id") or "provider") for row in rows)),
    }


def format_score(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    adjudication = report["adjudication"]
    precision = report["precision"]

    lines = [
        f"cases                 {coverage['cases']}",
        f"  with no finding     {coverage['cases_with_no_finding']} "
        f"({coverage['silence_rate']:.0%} silent)",
        f"findings              {coverage['findings_total']} "
        f"({coverage['findings_per_case']} per case)",
        f"rejected by grounding {coverage['rejected_by_grounding']}",
        "",
        f"judged                {adjudication['judged']} "
        f"(unsure {adjudication['unsure']}, unjudged {adjudication['unjudged']})",
    ]

    if precision["value"] is None:
        lines.append("precision             not computed: no finding has a verdict yet")
        return "\n".join(lines)

    low, high = precision["precision_95ci"]
    lines.append(
        f"precision             {precision['value']:.0%} "
        f"({precision['true_positives']}/{adjudication['judged']}, 95% CI {low:.0%}-{high:.0%})"
    )

    if report.get("by_anchor"):
        lines.extend(["", "by anchor:"])
        for label, stats in sorted(report["by_anchor"].items()):
            anchor_low, anchor_high = stats["precision_95ci"]
            claim = "this line is a defect" if label == "line" else "this change set needs work"
            lines.append(
                f"  {label + '-level':<14} {stats['precision']:.0%} "
                f"({stats['true_positives']}/{stats['judged']}, CI {anchor_low:.0%}-{anchor_high:.0%})"
                f"  claim: {claim}"
            )

    if report["per_rule"]:
        lines.extend(["", "per rule:"])
        for rule_id, stats in sorted(
            report["per_rule"].items(), key=lambda item: (-item[1]["judged"], item[0])
        ):
            rule_low, rule_high = stats["precision_95ci"]
            lines.append(
                f"  {rule_id:<28} {stats['precision']:.0%} "
                f"({stats['true_positives']}/{stats['judged']}, CI {rule_low:.0%}-{rule_high:.0%})"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# silent-commit recall probe
# ---------------------------------------------------------------------------


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_dataset_manifest(
    dataset_path: Path,
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate that a static-review manifest describes this exact corpus."""
    identity = manifest.get("identity")
    case_rows = manifest.get("cases")
    artifact = manifest.get("findings_artifact")
    if not isinstance(identity, dict) or not isinstance(case_rows, list):
        raise ValueError("Manifest is missing its identity or case records.")
    if not isinstance(artifact, dict):
        raise ValueError("Manifest does not bind a findings artifact.")

    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if identity.get("dataset_sha256") != dataset_sha256:
        raise ValueError("Dataset does not match the dataset hash recorded in the manifest.")
    if identity.get("dataset_cases") != len(cases):
        raise ValueError(
            f"Dataset/manifest case-count mismatch: manifest has "
            f"{identity.get('dataset_cases')!r}, dataset has {len(cases)}."
        )
    if identity.get("review_mode") not in {"demo", "static"}:
        raise ValueError("Recall probe requires a deterministic static-review manifest.")
    if identity.get("reviewer_sha256") != _files_sha256(REVIEWER_SOURCE_PATHS):
        raise ValueError(
            "Reviewer source differs from the source recorded in the manifest. "
            "Rerun `review` before creating a recall probe."
        )

    failed_cases = [
        str(case.get("case_id") or "unknown")
        for case in case_rows
        if case.get("review_status") not in STATIC_SUCCESS_STATUSES
        or case.get("review_source") not in STATIC_REVIEW_SOURCES
    ]
    if failed_cases:
        raise ValueError(
            "Recall probe requires successful static review cases; non-successful case(s): "
            + ", ".join(failed_cases)
        )

    dataset_ids = [str(case.get("id") or "") for case in cases]
    manifest_ids = [str(case.get("case_id") or "") for case in case_rows]
    if not all(dataset_ids) or len(dataset_ids) != len(set(dataset_ids)):
        raise ValueError("Dataset case ids must be non-empty and unique.")
    if not all(manifest_ids) or len(manifest_ids) != len(set(manifest_ids)):
        raise ValueError("Manifest case ids must be non-empty and unique.")
    if set(dataset_ids) != set(manifest_ids):
        raise ValueError("Dataset case ids do not match the cases recorded in the manifest.")

    try:
        finding_count = sum(int(case["findings"]) for case in case_rows)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Every manifest case must record an integer findings count.") from exc
    if artifact.get("count") != finding_count:
        raise ValueError(
            f"Manifest finding-count mismatch: artifact has {artifact.get('count')!r}, "
            f"case records total {finding_count}."
        )
    return case_rows


def _sample_silent_case_ids(
    case_rows: list[dict[str, Any]], count: int, seed: int
) -> tuple[list[str], int]:
    if count <= 0:
        raise ValueError("Recall probe count must be greater than zero.")
    eligible = sorted(
        str(case["case_id"])
        for case in case_rows
        if int(case.get("findings", 0)) == 0
        and case.get("review_status") in STATIC_SUCCESS_STATUSES
        and case.get("review_source") in STATIC_REVIEW_SOURCES
    )
    if count > len(eligible):
        raise ValueError(
            f"Recall probe requested {count} case(s), but only {len(eligible)} silent case(s) exist."
        )

    # Hash ranking is a deterministic, cross-runtime equivalent of seeded
    # sampling: the same seed and population always produce the same order.
    ranked = sorted(
        eligible,
        key=lambda case_id: hashlib.sha256(f"{seed}\0{case_id}".encode("utf-8")).hexdigest(),
    )
    return ranked[:count], len(eligible)


def _probe_id(source_manifest_sha256: str, case: dict[str, Any]) -> str:
    immutable_case = {
        "source_manifest_sha256": source_manifest_sha256,
        "case_id": case["id"],
        "commit_url": case.get("commit_url"),
        "subject": case.get("subject", ""),
        "changed_files": case["changed_files"],
        "diff": case["diff"],
    }
    return f"recall#{_canonical_sha256(immutable_case)[:20]}"


def _probe_artifact_sha256(rows: list[dict[str, Any]]) -> str:
    immutable_rows = [
        {key: value for key, value in row.items() if key not in PROBE_ADJUDICATION_FIELDS}
        for row in rows
    ]
    immutable_rows.sort(key=lambda row: str(row.get("probe_id", "")))
    return _canonical_sha256(immutable_rows)


def build_recall_probe(
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
    count: int,
    seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build an empty, deterministic audit sample from silent review cases."""
    case_rows = list(manifest["cases"])
    sampled_ids, eligible_count = _sample_silent_case_ids(case_rows, count, seed)
    case_by_id = {str(case["id"]): case for case in cases}
    source_manifest_sha256 = _canonical_sha256(manifest)

    rows: list[dict[str, Any]] = []
    for case_id in sampled_ids:
        case = case_by_id[case_id]
        rows.append(
            {
                "record_type": "recall_probe_case",
                "probe_id": _probe_id(source_manifest_sha256, case),
                "case_id": case_id,
                "commit_url": case.get("commit_url"),
                "subject": case.get("subject", ""),
                "changed_files": case["changed_files"],
                "diff": case["diff"],
                # Human-owned fields. The tool never generates a missed defect.
                "verdict": "",
                "missed": [],
                "note": "",
            }
        )

    identity = manifest["identity"]
    if set(RULE_SCOPE_DESCRIPTIONS) != set(RULE_IDS):
        raise ValueError("Rule-scope descriptions do not match the current static rule inventory.")
    header = {
        "record_type": "recall_probe_header",
        "schema_version": PROBE_SCHEMA_VERSION,
        "source_manifest_sha256": source_manifest_sha256,
        "dataset_sha256": identity["dataset_sha256"],
        "reviewer_sha256": identity["reviewer_sha256"],
        "probe_harness_sha256": _files_sha256((Path(__file__),)),
        "seed": seed,
        "requested_count": count,
        "eligible_count": eligible_count,
        "sampled_count": len(rows),
        "rule_ids": list(RULE_IDS),
        "rule_scopes": {
            rule_id: RULE_SCOPE_DESCRIPTIONS[rule_id] for rule_id in RULE_IDS
        },
        "probe_artifact": {
            "count": len(rows),
            "sha256": _probe_artifact_sha256(rows),
        },
    }
    return header, rows


def _split_probe_records(
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not records or records[0].get("record_type") != "recall_probe_header":
        raise ValueError("Recall probe must start with a recall_probe_header record.")
    rows = records[1:]
    if any(row.get("record_type") != "recall_probe_case" for row in rows):
        raise ValueError("Every record after the recall header must be a recall_probe_case.")
    return records[0], rows


def _validate_probe_labels(rows: list[dict[str, Any]], rule_ids: list[str]) -> None:
    from diff_index import normalize_path, parse_unified_diff

    allowed_scopes = {*rule_ids, OUT_OF_SCOPE, SCOPE_UNSURE}
    seen_ids: set[str] = set()
    for position, row in enumerate(rows, start=1):
        probe_id = str(row.get("probe_id") or f"row {position}")
        if probe_id in seen_ids:
            raise ValueError(f"Duplicate probe_id value: {probe_id}")
        seen_ids.add(probe_id)

        verdict = row.get("verdict", "")
        if verdict not in {"", *PROBE_VERDICTS}:
            raise ValueError(
                f"Invalid recall verdict {verdict!r} on {probe_id}. Expected an empty string "
                f"or one of: {', '.join(PROBE_VERDICTS)}"
            )
        note = row.get("note", "")
        if not isinstance(note, str):
            raise ValueError(f"{probe_id} note must be a string.")
        if verdict == "unsure" and not note.strip():
            raise ValueError(f"{probe_id} is unsure and must explain why in note.")
        missed = row.get("missed")
        if not isinstance(missed, list):
            raise ValueError(f"{probe_id} must contain a missed list.")
        if verdict == "clean" and missed:
            raise ValueError(f"{probe_id} is clean but contains missed defects.")
        if verdict == "missed_defect" and not missed:
            raise ValueError(f"{probe_id} is missed_defect but its missed list is empty.")
        if verdict in {"", "unsure"} and missed:
            raise ValueError(f"{probe_id} cannot contain missed defects with verdict {verdict!r}.")

        diff_index = parse_unified_diff(str(row.get("diff") or ""))
        changed_files = {normalize_path(str(path)) for path in row.get("changed_files", [])}
        seen_misses: set[tuple[Any, ...]] = set()
        for miss_number, miss in enumerate(missed, start=1):
            label = f"{probe_id} missed[{miss_number}]"
            if not isinstance(miss, dict):
                raise ValueError(f"{label} must be an object.")
            file = miss.get("file")
            line = miss.get("line")
            category = miss.get("category")
            description = miss.get("description")
            rule_scope = miss.get("rule_scope")
            if not isinstance(file, str) or not file.strip():
                raise ValueError(f"{label} must name a non-empty file.")
            normalized_file = normalize_path(file)
            if normalized_file not in changed_files:
                raise ValueError(f"{label} names a file outside changed_files.")
            if not isinstance(line, int) or isinstance(line, bool) or line <= 0:
                raise ValueError(f"{label} line must be a positive integer.")
            diff_file = diff_index.files.get(normalized_file)
            if diff_file is None or line not in diff_file.right_lines:
                raise ValueError(f"{label} must point to a line this diff added.")
            if not isinstance(category, str) or not category.strip():
                raise ValueError(f"{label} must name a non-empty category.")
            if (
                not isinstance(description, str)
                or not description.strip()
                or "\n" in description
                or "\r" in description
            ):
                raise ValueError(f"{label} description must be one non-empty line.")
            if rule_scope not in allowed_scopes:
                raise ValueError(
                    f"{label} rule_scope must be a recorded rule id, {OUT_OF_SCOPE}, or "
                    f"{SCOPE_UNSURE}."
                )
            fingerprint = (normalized_file, line, category, description, rule_scope)
            if fingerprint in seen_misses:
                raise ValueError(f"{label} duplicates another missed defect in this case.")
            seen_misses.add(fingerprint)


def _validate_probe_bundle(
    header: dict[str, Any],
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    if header.get("schema_version") != PROBE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported recall probe schema: {header.get('schema_version')!r}")
    if header.get("source_manifest_sha256") != _canonical_sha256(manifest):
        raise ValueError("Recall probe does not match the supplied source manifest.")

    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("Source manifest has no identity.")
    if header.get("dataset_sha256") != identity.get("dataset_sha256"):
        raise ValueError("Recall probe dataset identity does not match the source manifest.")
    if header.get("reviewer_sha256") != identity.get("reviewer_sha256"):
        raise ValueError("Recall probe reviewer identity does not match the source manifest.")

    rule_ids = header.get("rule_ids")
    if (
        not isinstance(rule_ids, list)
        or not rule_ids
        or not all(isinstance(rule_id, str) and rule_id for rule_id in rule_ids)
        or len(rule_ids) != len(set(rule_ids))
    ):
        raise ValueError("Recall probe header must record a unique, non-empty rule inventory.")
    rule_scopes = header.get("rule_scopes")
    if (
        not isinstance(rule_scopes, dict)
        or set(rule_scopes) != set(rule_ids)
        or not all(isinstance(scope, str) and scope.strip() for scope in rule_scopes.values())
    ):
        raise ValueError("Recall probe header must define the stated scope of every rule id.")
    if header.get("probe_harness_sha256") != _files_sha256((Path(__file__),)):
        raise ValueError(
            "Recall probe was created by a different scoring harness. Regenerate it before "
            "scoring with this version."
        )

    seed = header.get("seed")
    requested_count = header.get("requested_count")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("Recall probe seed must be an integer.")
    if not isinstance(requested_count, int) or isinstance(requested_count, bool):
        raise ValueError("Recall probe requested_count must be an integer.")
    expected_ids, eligible_count = _sample_silent_case_ids(
        list(manifest.get("cases") or []), requested_count, seed
    )
    actual_ids = [str(row.get("case_id") or "") for row in rows]
    if actual_ids != expected_ids:
        raise ValueError("Recall probe cases do not match the sample selected by its seed.")
    if header.get("eligible_count") != eligible_count:
        raise ValueError("Recall probe eligible population does not match the source manifest.")
    if header.get("sampled_count") != len(rows):
        raise ValueError("Recall probe sampled_count does not match its case records.")

    artifact = header.get("probe_artifact")
    if not isinstance(artifact, dict) or artifact.get("count") != len(rows):
        raise ValueError("Recall probe header does not bind the case-record count.")
    if artifact.get("sha256") != _probe_artifact_sha256(rows):
        raise ValueError("Recall probe immutable content hash does not match its header.")

    manifest_cases = {str(case["case_id"]): case for case in manifest["cases"]}
    source_manifest_sha256 = str(header["source_manifest_sha256"])
    for row in rows:
        case = manifest_cases[str(row["case_id"])]
        if row.get("commit_url") != case.get("commit_url"):
            raise ValueError(f"Recall probe commit URL changed for {row['case_id']}.")
        if row.get("subject") != case.get("subject", ""):
            raise ValueError(f"Recall probe subject changed for {row['case_id']}.")
        if row.get("changed_files") != case.get("changed_files"):
            raise ValueError(f"Recall probe changed_files changed for {row['case_id']}.")
        expected_probe_id = _probe_id(
            source_manifest_sha256,
            {
                "id": row["case_id"],
                "commit_url": row.get("commit_url"),
                "subject": row.get("subject", ""),
                "changed_files": row.get("changed_files", []),
                "diff": row.get("diff", ""),
            },
        )
        if row.get("probe_id") != expected_probe_id:
            raise ValueError(f"Recall probe id does not match immutable content for {row['case_id']}.")

    _validate_probe_labels(rows, rule_ids)


def _merge_probe_adjudications(
    rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    labelled = {
        str(row["probe_id"]): row
        for row in existing_rows
        if row.get("verdict") or row.get("missed") or row.get("note")
    }
    new_ids = {str(row["probe_id"]) for row in rows}
    orphaned = sorted(set(labelled) - new_ids)
    if orphaned:
        raise ValueError(
            "Recall probe would discard adjudication for probe_id value(s): "
            + ", ".join(orphaned)
            + ". Use a new --out path or archive the labelled file first."
        )

    merged: list[dict[str, Any]] = []
    for row in rows:
        updated = dict(row)
        existing = labelled.get(str(row["probe_id"]))
        if existing is not None:
            for field in PROBE_ADJUDICATION_FIELDS:
                updated[field] = existing.get(field, updated[field])
        merged.append(updated)
    return merged


def score_recall_probe(rows: list[dict[str, Any]], rule_ids: list[str]) -> dict[str, Any]:
    """Summarize a silent-commit miss audit; this is not classical recall."""
    _validate_probe_labels(rows, rule_ids)
    judged = [row for row in rows if row.get("verdict") in {"clean", "missed_defect"}]
    unsure = [row for row in rows if row.get("verdict") == "unsure"]
    unjudged = [row for row in rows if not row.get("verdict")]
    commits_with_misses = sum(1 for row in judged if row["verdict"] == "missed_defect")
    miss_low, miss_high = wilson_interval(commits_with_misses, len(judged))
    misses = [miss for row in judged for miss in row["missed"]]

    in_scope = [miss for miss in misses if miss["rule_scope"] in rule_ids]
    out_of_scope = [miss for miss in misses if miss["rule_scope"] == OUT_OF_SCOPE]
    scope_unsure = [miss for miss in misses if miss["rule_scope"] == SCOPE_UNSURE]
    decided_scope = len(in_scope) + len(out_of_scope)
    return {
        "sample": {"sampled": len(rows)},
        "adjudication": {
            "judged": len(judged),
            "unsure": len(unsure),
            "unjudged": len(unjudged),
        },
        "silent_commit_miss_rate": {
            "commits_with_missed_defect": commits_with_misses,
            "value": round(commits_with_misses / len(judged), 4) if judged else None,
            "95ci": [miss_low, miss_high] if judged else None,
        },
        "missed_defects": {
            "total": len(misses),
            "by_category": dict(sorted(Counter(str(miss["category"]) for miss in misses).items())),
        },
        "static_rule_scope": {
            "in_scope": len(in_scope),
            "out_of_scope": len(out_of_scope),
            "unsure": len(scope_unsure),
            "decided": decided_scope,
            "in_scope_share": round(len(in_scope) / decided_scope, 4)
            if decided_scope
            else None,
            "by_rule": dict(
                sorted(Counter(str(miss["rule_scope"]) for miss in in_scope).items())
            ),
        },
    }


def format_recall_score(report: dict[str, Any]) -> str:
    adjudication = report["adjudication"]
    miss_rate = report["silent_commit_miss_rate"]
    defects = report["missed_defects"]
    scope = report["static_rule_scope"]
    lines = [
        "silent-commit miss audit (not TP / (TP + FN) recall)",
        f"commits sampled       {report['sample']['sampled']}"
        + (
            f" of {report['sample']['eligible_population']} eligible"
            if "eligible_population" in report["sample"]
            else ""
        ),
        f"judged                {adjudication['judged']} "
        f"(unsure {adjudication['unsure']}, unjudged {adjudication['unjudged']})",
    ]
    if miss_rate["value"] is None:
        lines.append("commits with misses   not computed: no sampled commit has been judged")
    else:
        low, high = miss_rate["95ci"]
        lines.append(
            f"commits with misses   {miss_rate['value']:.0%} "
            f"({miss_rate['commits_with_missed_defect']}/{adjudication['judged']}, "
            f"95% CI {low:.0%}-{high:.0%})"
        )
    lines.append(f"missed defects        {defects['total']}")
    if defects["by_category"]:
        lines.append("by category:")
        for category, count in defects["by_category"].items():
            lines.append(f"  {category:<24} {count}")
    if scope["in_scope_share"] is None:
        lines.append("in static-rule scope  not computed: no miss has a decided scope")
    else:
        lines.append(
            f"in static-rule scope  {scope['in_scope_share']:.0%} "
            f"({scope['in_scope']}/{scope['decided']}; unsure {scope['unsure']})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# io
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            rows.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on {path}:{number}: {exc}") from exc
    return rows


def write_jsonl(rows: Iterable[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def _cmd_harvest(args: argparse.Namespace) -> int:
    cases = harvest(args.repo.resolve(), args.count, args.suffix)
    if not cases:
        print(f"No commit in {args.repo} touched a '{args.suffix}' file.", file=sys.stderr)
        return 1

    write_jsonl(cases, args.out)
    print(f"Harvested {len(cases)} case(s) from {args.repo} into {args.out}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    cases = read_jsonl(args.dataset)
    if not cases:
        print(f"No cases in {args.dataset}", file=sys.stderr)
        return 1

    identity = _identity(args.dataset, cases)
    rows, case_rows = review_cases(cases)
    if args.out.exists():
        rows = _merge_adjudications(rows, read_jsonl(args.out))

    write_jsonl(rows, args.out)
    manifest = {
        "identity": identity.to_dict(),
        "findings_artifact": {
            "count": len(rows),
            "sha256": _finding_set_sha256(rows),
        },
        "cases": case_rows,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(
        f"Reviewed {len(cases)} case(s) in {identity.review_mode} mode: "
        f"{len(rows)} finding(s) to adjudicate."
    )
    print(f"  findings:  {args.out}")
    print(f"  manifest:  {args.manifest}")
    print(f"Set \"verdict\" on each row to one of: {', '.join(VERDICTS)}")
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    rows = read_jsonl(args.findings)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    _validate_artifacts(rows, manifest)
    report = score(rows, manifest["cases"])
    report["identity"] = manifest["identity"]

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(format_score(report))
    if args.out:
        print(f"\nWrote {args.out}")
    return 0


def _cmd_recall_probe(args: argparse.Namespace) -> int:
    cases = read_jsonl(args.dataset)
    if not cases:
        print(f"No cases in {args.dataset}", file=sys.stderr)
        return 1
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    _validate_dataset_manifest(args.dataset, cases, manifest)
    header, rows = build_recall_probe(cases, manifest, args.count, args.seed)

    if args.out.exists():
        existing_header, existing_rows = _split_probe_records(read_jsonl(args.out))
        _validate_probe_bundle(existing_header, existing_rows, manifest)
        rows = _merge_probe_adjudications(rows, existing_rows)

    write_jsonl([header, *rows], args.out)
    print(
        f"Sampled {len(rows)} of {header['eligible_count']} silent case(s) "
        f"with seed {args.seed}."
    )
    print(f"  probe: {args.out}")
    print('Set "verdict" on each case to clean, missed_defect, or unsure.')
    print("The tool leaves every missed list empty; a person must supply missed defects.")
    return 0


def _cmd_recall_score(args: argparse.Namespace) -> int:
    header, rows = _split_probe_records(read_jsonl(args.probe))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    _validate_probe_bundle(header, rows, manifest)
    report = score_recall_probe(rows, list(header["rule_ids"]))
    report["sample"]["eligible_population"] = header["eligible_count"]
    report["probe_identity"] = {
        key: header[key]
        for key in (
            "schema_version",
            "source_manifest_sha256",
            "dataset_sha256",
            "reviewer_sha256",
            "probe_harness_sha256",
            "seed",
            "eligible_count",
            "sampled_count",
            "rule_ids",
            "rule_scopes",
        )
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(format_recall_score(report))
    if args.out:
        print(f"\nWrote {args.out}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    harvest_parser = sub.add_parser("harvest", help="Collect real commit diffs from a git repository.")
    harvest_parser.add_argument("--repo", type=Path, required=True, help="Path to a local git clone.")
    harvest_parser.add_argument("--count", type=int, default=30, help="Number of cases to keep.")
    harvest_parser.add_argument("--suffix", default=".py", help="Only keep commits touching this suffix.")
    harvest_parser.add_argument("--out", type=Path, required=True, help="Destination JSONL path.")
    harvest_parser.set_defaults(func=_cmd_harvest)

    review_parser = sub.add_parser("review", help="Run the reviewer over a harvested dataset.")
    review_parser.add_argument("--dataset", type=Path, required=True)
    review_parser.add_argument("--out", type=Path, required=True, help="Findings JSONL to adjudicate.")
    review_parser.add_argument("--manifest", type=Path, required=True, help="Run identity and per-case stats.")
    review_parser.set_defaults(func=_cmd_review)

    score_parser = sub.add_parser("score", help="Report precision from adjudicated findings.")
    score_parser.add_argument("--findings", type=Path, required=True)
    score_parser.add_argument("--manifest", type=Path, required=True)
    score_parser.add_argument("--out", type=Path, help="Optional JSON report path.")
    score_parser.set_defaults(func=_cmd_score)

    probe_parser = sub.add_parser(
        "recall-probe",
        help="Sample commits where the static reviewer emitted no finding.",
    )
    probe_parser.add_argument("--dataset", type=Path, required=True)
    probe_parser.add_argument("--manifest", type=Path, required=True)
    probe_parser.add_argument("--count", type=int, default=30)
    probe_parser.add_argument("--seed", type=int, required=True)
    probe_parser.add_argument("--out", type=Path, required=True)
    probe_parser.set_defaults(func=_cmd_recall_probe)

    recall_score_parser = sub.add_parser(
        "recall-score",
        help="Score human-recorded misses from a silent-commit probe.",
    )
    recall_score_parser.add_argument("--probe", type=Path, required=True)
    recall_score_parser.add_argument("--manifest", type=Path, required=True)
    recall_score_parser.add_argument("--out", type=Path, help="Optional JSON report path.")
    recall_score_parser.set_defaults(func=_cmd_recall_score)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
