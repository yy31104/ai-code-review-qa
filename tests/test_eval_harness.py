from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from evals.run_local import DEFAULT_DATASET, build_report, load_cases


def test_seed_dataset_loads_and_passes() -> None:
    cases = load_cases(DEFAULT_DATASET)
    report = build_report(cases)

    assert report["summary"]["total_cases"] == 5
    assert report["summary"]["failed_cases"] == 0
    assert report["summary"]["case_pass_rate"] == 1.0


def test_eval_cli_writes_results(tmp_path: Path) -> None:
    output_path = tmp_path / "results.json"

    completed = subprocess.run(
        [sys.executable, "evals/run_local.py", "--out", str(output_path)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "Eval complete" in completed.stdout
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["summary"]["failed_cases"] == 0


def test_render_report_writes_markdown_and_html(tmp_path: Path) -> None:
    json_path = tmp_path / "results.json"
    md_path = tmp_path / "summary.md"
    html_path = tmp_path / "summary.html"

    subprocess.run(
        [sys.executable, "evals/run_local.py", "--out", str(json_path)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "evals/render_report.py",
            "--in",
            str(json_path),
            "--md",
            str(md_path),
            "--html",
            str(html_path),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "AI Code Review Eval Summary" in md_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in html_path.read_text(encoding="utf-8")
