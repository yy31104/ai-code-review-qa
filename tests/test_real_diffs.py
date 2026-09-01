from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# Importing the eval runner puts backend/app on sys.path, matching the existing
# test modules in this directory.
import evals.run_local  # noqa: F401
import evals.real_diffs as real_diffs
from evals.real_diffs import (
    _finding_set_sha256,
    _identity,
    _merge_adjudications,
    _validate_artifacts,
    harvest,
    read_jsonl,
    review_cases,
    score,
    wilson_interval,
    write_jsonl,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """A small real git repository with one defect commit and one clean commit."""
    repo = tmp_path / "corpus"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")

    (repo / "svc.py").write_text("def existing():\n    return 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("# notes\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")

    (repo / "svc.py").write_text(
        "def existing():\n    subprocess.run(cmd, shell=True)\n    return 1\n", encoding="utf-8"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add shell call")

    (repo / "notes.md").write_text("# notes\nmore\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "docs only")
    return repo


def test_harvest_keeps_only_commits_touching_the_suffix(corpus: Path) -> None:
    cases = harvest(corpus, count=10, suffix=".py")

    assert len(cases) == 2
    assert all(case["changed_files"] == ["svc.py"] for case in cases)
    assert all(case["commit_sha"] for case in cases)
    # The docs-only commit is not a Python review unit.
    assert all("notes.md" not in case["diff"] for case in cases)


def test_harvest_records_provenance_for_every_case(corpus: Path) -> None:
    for case in harvest(corpus, count=10, suffix=".py"):
        assert case["commit_sha"] and case["date"] and case["subject"]
        assert case["id"].endswith(case["commit_sha"][:12])


def test_review_produces_one_row_per_finding_with_stable_ids(corpus: Path) -> None:
    cases = harvest(corpus, count=10, suffix=".py")

    first_rows, case_rows = review_cases(cases)
    second_rows, _ = review_cases(cases)

    assert [row["finding_id"] for row in first_rows] == [row["finding_id"] for row in second_rows]
    assert len(case_rows) == len(cases)
    assert any(row["rule_id"] == "subprocess_shell_true" for row in first_rows)
    # Every row starts unjudged: labels are human work, never generated here.
    assert all(row["verdict"] == "" for row in first_rows)


def test_run_identity_includes_uncommitted_source_hashes(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"id":"one"}\n', encoding="utf-8")

    identity = _identity(dataset, [{"id": "one"}])

    assert len(identity.reviewer_sha256) == 64
    assert len(identity.harness_sha256) == 64


def test_review_rows_carry_context_the_adjudicator_can_read(corpus: Path) -> None:
    rows, _ = review_cases(harvest(corpus, count=10, suffix=".py"))
    anchored = [row for row in rows if row["line"] is not None]

    assert anchored
    assert any(">" in line for line in anchored[0]["context"])


def test_score_ignores_unjudged_rows() -> None:
    rows = [
        {"finding_id": "a", "rule_id": "r1", "line": 1, "verdict": "true_positive"},
        {"finding_id": "b", "rule_id": "r1", "line": 2, "verdict": "false_positive"},
        {"finding_id": "c", "rule_id": "r1", "line": 3, "verdict": ""},
        {"finding_id": "d", "rule_id": "r1", "line": 4, "verdict": "unsure"},
    ]
    case_rows = [{"findings": 4, "rejected_findings": 0}]

    report = score(rows, case_rows)

    assert report["adjudication"] == {"judged": 2, "unsure": 1, "unjudged": 1}
    assert report["precision"]["value"] == 0.5


def test_score_rejects_a_misspelled_verdict() -> None:
    rows = [{"finding_id": "a", "rule_id": "r1", "line": 1, "verdict": "true-positive"}]

    with pytest.raises(ValueError, match="Invalid verdict"):
        score(rows, [{"findings": 1, "rejected_findings": 0}])


def test_score_reports_no_precision_before_anything_is_judged() -> None:
    rows = [{"finding_id": "a", "rule_id": "r1", "line": 1, "verdict": ""}]

    report = score(rows, [{"findings": 1, "rejected_findings": 0}])

    assert report["precision"]["value"] is None


def test_score_separates_line_claims_from_file_claims() -> None:
    rows = [
        {"finding_id": "a", "rule_id": "r1", "line": 1, "verdict": "true_positive"},
        {"finding_id": "b", "rule_id": "r2", "line": None, "verdict": "false_positive"},
        {"finding_id": "c", "rule_id": "r2", "line": None, "verdict": "false_positive"},
    ]

    report = score(rows, [{"findings": 3, "rejected_findings": 0}])

    assert report["by_anchor"]["line"]["precision"] == 1.0
    assert report["by_anchor"]["file"]["precision"] == 0.0


def test_wilson_interval_is_wide_for_small_samples() -> None:
    narrow_low, narrow_high = wilson_interval(80, 100)
    wide_low, wide_high = wilson_interval(8, 10)

    assert narrow_low < 0.8 < narrow_high
    assert wide_low < 0.8 < wide_high
    # The same 80% over ten observations must not read as the same claim.
    assert (wide_high - wide_low) > (narrow_high - narrow_low)


def test_jsonl_round_trip_preserves_rows(tmp_path: Path) -> None:
    rows = [{"finding_id": "a", "verdict": ""}, {"finding_id": "b", "verdict": "true_positive"}]
    path = write_jsonl(rows, tmp_path / "rows.jsonl")

    assert read_jsonl(path) == rows


def test_finding_artifact_hash_allows_labels_but_not_claim_changes() -> None:
    rows = [
        {
            "finding_id": "a",
            "rule_id": "r1",
            "evidence": "shell=True",
            "verdict": "",
            "note": "",
        }
    ]
    labelled = [{**rows[0], "verdict": "true_positive", "note": "confirmed"}]
    changed = [{**rows[0], "evidence": "different claim"}]

    assert _finding_set_sha256(rows) == _finding_set_sha256(labelled)
    assert _finding_set_sha256(rows) != _finding_set_sha256(changed)


def test_artifact_validation_rejects_the_wrong_manifest() -> None:
    rows = [{"finding_id": "a", "verdict": ""}]
    manifest = {
        "identity": {
            "harness_sha256": real_diffs._files_sha256((Path(real_diffs.__file__),))
        },
        "findings_artifact": {"count": 1, "sha256": _finding_set_sha256(rows)}
    }

    _validate_artifacts(rows, manifest)
    with pytest.raises(ValueError, match="immutable content hash mismatch"):
        _validate_artifacts([{**rows[0], "rule_id": "different"}], manifest)


def test_review_rerun_preserves_matching_human_labels() -> None:
    fresh = [{"finding_id": "a", "verdict": "", "note": ""}]
    existing = [{"finding_id": "a", "verdict": "false_positive", "note": "moved code"}]

    assert _merge_adjudications(fresh, existing) == existing


def test_review_rerun_refuses_to_drop_an_old_label() -> None:
    fresh = [{"finding_id": "new", "verdict": "", "note": ""}]
    existing = [{"finding_id": "old", "verdict": "true_positive", "note": ""}]

    with pytest.raises(ValueError, match="would discard adjudication"):
        _merge_adjudications(fresh, existing)


def test_invalid_jsonl_names_the_offending_line(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"ok": 1}\nnot json\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"broken\.jsonl:2"):
        read_jsonl(path)
