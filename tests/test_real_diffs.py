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
    OUT_OF_SCOPE,
    SCOPE_UNSURE,
    _canonical_sha256,
    _finding_set_sha256,
    _identity,
    _merge_adjudications,
    _merge_probe_adjudications,
    _probe_artifact_sha256,
    _validate_artifacts,
    _validate_dataset_manifest,
    _validate_probe_bundle,
    build_recall_probe,
    harvest,
    read_jsonl,
    review_cases,
    score,
    score_recall_probe,
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


def recall_inputs(
    tmp_path: Path, total: int = 6, finding_case_indexes: set[int] | None = None
) -> tuple[Path, list[dict[str, object]], dict[str, object]]:
    finding_case_indexes = finding_case_indexes or {0}
    cases: list[dict[str, object]] = []
    case_rows: list[dict[str, object]] = []
    for index in range(total):
        case_id = f"repo@{index:012d}"
        path = f"svc_{index}.py"
        cases.append(
            {
                "id": case_id,
                "repo": "https://github.com/example/repo.git",
                "commit_sha": f"{index:040d}",
                "commit_url": f"https://github.com/example/repo/commit/{index:040d}",
                "author": "Test User",
                "date": "2026-09-01T00:00:00Z",
                "subject": f"change {index}",
                "changed_files": [path],
                "diff": (
                    f"diff --git a/{path} b/{path}\n"
                    "new file mode 100644\n"
                    "--- /dev/null\n"
                    f"+++ b/{path}\n"
                    "@@ -0,0 +1 @@\n"
                    f"+value = {index}\n"
                ),
            }
        )
        case_rows.append(
            {
                "case_id": case_id,
                "commit_url": f"https://github.com/example/repo/commit/{index:040d}",
                "subject": f"change {index}",
                "changed_files": [path],
                "diff_chars": 100,
                "review_status": "demo",
                "review_source": "demo_rules",
                "risk_level": "Low",
                "findings": 1 if index in finding_case_indexes else 0,
                "rejected_findings": 0,
                "latency_ms": 0.1,
            }
        )

    dataset = write_jsonl(cases, tmp_path / "corpus.jsonl")
    identity = _identity(dataset, cases).to_dict()
    finding_count = sum(int(case["findings"]) for case in case_rows)
    manifest: dict[str, object] = {
        "identity": identity,
        "findings_artifact": {"count": finding_count, "sha256": "test-only"},
        "cases": case_rows,
    }
    return dataset, cases, manifest


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


def test_recall_probe_is_deterministic_and_only_samples_silent_cases(tmp_path: Path) -> None:
    dataset, cases, manifest = recall_inputs(tmp_path)
    _validate_dataset_manifest(dataset, cases, manifest)

    first_header, first_rows = build_recall_probe(cases, manifest, count=3, seed=20260901)
    second_header, second_rows = build_recall_probe(cases, manifest, count=3, seed=20260901)

    assert [row["case_id"] for row in first_rows] == [row["case_id"] for row in second_rows]
    assert all(row["case_id"] != "repo@000000000000" for row in first_rows)
    assert first_header["seed"] == 20260901
    assert first_header["eligible_count"] == 5
    assert first_header["rule_ids"] == list(real_diffs.RULE_IDS)
    assert set(first_header["rule_scopes"]) == set(real_diffs.RULE_IDS)
    assert first_header == second_header


def test_recall_probe_rejects_a_sample_larger_than_the_silent_population(
    tmp_path: Path,
) -> None:
    _, cases, manifest = recall_inputs(tmp_path, total=3)

    with pytest.raises(ValueError, match="only 2 silent"):
        build_recall_probe(cases, manifest, count=3, seed=1)


def test_recall_probe_rejects_dataset_manifest_mismatch(tmp_path: Path) -> None:
    dataset, cases, manifest = recall_inputs(tmp_path)
    manifest["identity"]["dataset_sha256"] = "wrong"  # type: ignore[index]

    with pytest.raises(ValueError, match="dataset hash"):
        _validate_dataset_manifest(dataset, cases, manifest)


def test_recall_probe_rejects_failed_static_cases(tmp_path: Path) -> None:
    dataset, cases, manifest = recall_inputs(tmp_path)
    manifest["cases"][1]["review_status"] = "configuration_error"  # type: ignore[index]
    manifest["cases"][1]["review_source"] = "none"  # type: ignore[index]

    with pytest.raises(ValueError, match="non-successful case"):
        _validate_dataset_manifest(dataset, cases, manifest)


def test_recall_probe_artifact_allows_labels_but_not_diff_changes(tmp_path: Path) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    _, rows = build_recall_probe(cases, manifest, count=2, seed=7)
    labelled = [{**rows[0], "verdict": "clean", "note": "reviewed"}, rows[1]]
    changed = [{**rows[0], "diff": str(rows[0]["diff"]) + "\n"}, rows[1]]

    assert _probe_artifact_sha256(rows) == _probe_artifact_sha256(labelled)
    assert _probe_artifact_sha256(rows) != _probe_artifact_sha256(changed)


def test_recall_probe_rerun_preserves_labels_and_refuses_to_drop_them(
    tmp_path: Path,
) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    _, rows = build_recall_probe(cases, manifest, count=2, seed=7)
    existing = [dict(row) for row in rows]
    existing[0]["verdict"] = "clean"
    existing[0]["note"] = "checked"

    merged = _merge_probe_adjudications(rows, existing)

    assert merged[0]["verdict"] == "clean"
    assert merged[0]["note"] == "checked"
    with pytest.raises(ValueError, match="would discard adjudication"):
        _merge_probe_adjudications(rows[1:], existing)


def test_recall_score_rejects_the_wrong_source_manifest(tmp_path: Path) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    header, rows = build_recall_probe(cases, manifest, count=2, seed=7)
    _validate_probe_bundle(header, rows, manifest)
    wrong_manifest = json.loads(json.dumps(manifest))
    wrong_manifest["identity"]["prompt_version"] = "different"

    with pytest.raises(ValueError, match="source manifest"):
        _validate_probe_bundle(header, rows, wrong_manifest)


def test_recall_score_rejects_a_probe_from_another_harness(tmp_path: Path) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    header, rows = build_recall_probe(cases, manifest, count=2, seed=7)
    header["probe_harness_sha256"] = "different"

    with pytest.raises(ValueError, match="different scoring harness"):
        _validate_probe_bundle(header, rows, manifest)


def test_recall_score_reports_miss_rate_categories_and_rule_scope(tmp_path: Path) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    header, rows = build_recall_probe(cases, manifest, count=3, seed=9)
    rows[0]["verdict"] = "clean"
    rows[1]["verdict"] = "missed_defect"
    rows[1]["missed"] = [
        {
            "file": rows[1]["changed_files"][0],
            "line": 1,
            "category": "correctness",
            "description": "The new value violates the caller's invariant.",
            "rule_scope": header["rule_ids"][0],
        }
    ]
    rows[2]["verdict"] = "missed_defect"
    rows[2]["missed"] = [
        {
            "file": rows[2]["changed_files"][0],
            "line": 1,
            "category": "reliability",
            "description": "The new path has no retry boundary.",
            "rule_scope": OUT_OF_SCOPE,
        },
        {
            "file": rows[2]["changed_files"][0],
            "line": 1,
            "category": "correctness",
            "description": "The intended range is unclear from this diff.",
            "rule_scope": SCOPE_UNSURE,
        },
    ]

    _validate_probe_bundle(header, rows, manifest)
    report = score_recall_probe(rows, header["rule_ids"])

    assert report["adjudication"] == {"judged": 3, "unsure": 0, "unjudged": 0}
    assert report["silent_commit_miss_rate"]["value"] == pytest.approx(2 / 3, abs=0.0001)
    assert report["missed_defects"] == {
        "total": 3,
        "by_category": {"correctness": 2, "reliability": 1},
    }
    assert report["static_rule_scope"]["in_scope_share"] == 0.5
    assert report["static_rule_scope"]["unsure"] == 1


def test_recall_score_does_not_treat_an_unjudged_empty_list_as_clean(
    tmp_path: Path,
) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    header, rows = build_recall_probe(cases, manifest, count=1, seed=3)

    report = score_recall_probe(rows, header["rule_ids"])

    assert report["adjudication"] == {"judged": 0, "unsure": 0, "unjudged": 1}
    assert report["silent_commit_miss_rate"]["value"] is None


def test_recall_score_requires_an_explanation_for_unsure(tmp_path: Path) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    header, rows = build_recall_probe(cases, manifest, count=1, seed=3)
    rows[0]["verdict"] = "unsure"

    with pytest.raises(ValueError, match="must explain why"):
        score_recall_probe(rows, header["rule_ids"])


@pytest.mark.parametrize(
    ("verdict", "missed", "message"),
    [
        ("clean", [{"anything": "present"}], "clean but contains"),
        ("missed_defect", [], "missed_defect but its missed list is empty"),
        ("typo", [], "Invalid recall verdict"),
    ],
)
def test_recall_score_rejects_inconsistent_case_labels(
    tmp_path: Path,
    verdict: str,
    missed: list[dict[str, object]],
    message: str,
) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    header, rows = build_recall_probe(cases, manifest, count=1, seed=3)
    rows[0]["verdict"] = verdict
    rows[0]["missed"] = missed

    with pytest.raises(ValueError, match=message):
        score_recall_probe(rows, header["rule_ids"])


def test_recall_score_rejects_unknown_scope_and_non_added_lines(tmp_path: Path) -> None:
    _, cases, manifest = recall_inputs(tmp_path)
    header, rows = build_recall_probe(cases, manifest, count=1, seed=3)
    rows[0]["verdict"] = "missed_defect"
    miss = {
        "file": rows[0]["changed_files"][0],
        "line": 1,
        "category": "correctness",
        "description": "A concrete missed defect.",
        "rule_scope": "not_a_recorded_rule",
    }
    rows[0]["missed"] = [miss]

    with pytest.raises(ValueError, match="rule_scope"):
        score_recall_probe(rows, header["rule_ids"])

    miss["rule_scope"] = OUT_OF_SCOPE
    miss["line"] = 99
    with pytest.raises(ValueError, match="line this diff added"):
        score_recall_probe(rows, header["rule_ids"])


def test_cli_exposes_recall_probe_and_recall_score() -> None:
    probe = real_diffs.parse_args(
        [
            "recall-probe",
            "--dataset",
            "corpus.jsonl",
            "--manifest",
            "manifest.json",
            "--seed",
            "7",
            "--out",
            "probe.jsonl",
        ]
    )
    recall_score = real_diffs.parse_args(
        ["recall-score", "--probe", "probe.jsonl", "--manifest", "manifest.json"]
    )

    assert probe.command == "recall-probe"
    assert probe.count == 30
    assert recall_score.command == "recall-score"


def test_invalid_jsonl_names_the_offending_line(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"ok": 1}\nnot json\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"broken\.jsonl:2"):
        read_jsonl(path)
