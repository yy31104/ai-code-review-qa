"""The BugsInPy recall harness: diff reversal, scoring and the reviewer interface.

These tests build a tiny BugsInPy-shaped tree, so they need no dataset, network
or API key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Put backend/app on sys.path in the same way as the other application tests.
import evals.run_local  # noqa: F401
from evals.bugsinpy_recall import (
    ProviderReviewer,
    StaticReviewer,
    main,
    reverse_patch,
    score_bug,
)
from schemas import Finding

FIX_REPLACES_EVAL = """diff --git a/app/calc.py b/app/calc.py
index 1111111..2222222 100644
--- a/app/calc.py
+++ b/app/calc.py
@@ -10,3 +10,3 @@ def compute(expression):
     cleaned = expression.strip()
-    return eval(cleaned)
+    return ast.literal_eval(cleaned)
     # end
"""

FIX_ONLY_ADDS_CHECK = """diff --git a/app/io.py b/app/io.py
index 3333333..4444444 100644
--- a/app/io.py
+++ b/app/io.py
@@ -4,2 +4,4 @@ def read(path):
     handle = open(path)
+    if handle is None:
+        raise ValueError(path)
     return handle.read()
"""

FIX_IN_NON_PYTHON = """diff --git a/docs/guide.md b/docs/guide.md
index 5555555..6666666 100644
--- a/docs/guide.md
+++ b/docs/guide.md
@@ -1 +1 @@
-old
+new
"""


def test_reverse_patch_swaps_sides_and_keeps_header_order():
    reversed_patch = reverse_patch(FIX_REPLACES_EVAL)

    lines = reversed_patch.splitlines()
    assert lines[2] == "--- a/app/calc.py"
    assert lines[3] == "+++ b/app/calc.py"
    assert "+    return eval(cleaned)" in lines
    assert "-    return ast.literal_eval(cleaned)" in lines
    assert reverse_patch(reversed_patch).strip() == FIX_REPLACES_EVAL.strip()


def test_reverse_patch_swaps_hunk_ranges():
    patch = "@@ -4,2 +4,4 @@ def read(path):\n"
    assert reverse_patch(patch).strip() == "@@ -4,4 +4,2 @@ def read(path):"


def test_static_reviewer_hits_the_line_the_fix_removed():
    result = score_bug("demo-1", FIX_REPLACES_EVAL, StaticReviewer())

    assert result is not None
    assert result.buggy_lines == 1
    assert not result.omission
    assert result.line_hit and result.file_hit
    assert result.rules == ["dynamic_eval"]
    assert result.fix_findings == 0


def test_add_only_fix_is_an_omission_bug():
    result = score_bug("demo-2", FIX_ONLY_ADDS_CHECK, StaticReviewer())

    assert result is not None
    assert result.omission
    assert result.buggy_lines == 0
    assert not result.line_hit


def test_non_python_patches_are_skipped():
    assert score_bug("demo-3", FIX_IN_NON_PYTHON, StaticReviewer()) is None


def test_reviewer_interface_accepts_any_implementation():
    class OffByOneReviewer:
        name = "fake"

        def review(self, diff, changed_files):
            return [
                Finding(file="app/calc.py", line=99, category="possible_bug", message="elsewhere")
            ]

    result = score_bug("demo-4", FIX_REPLACES_EVAL, OffByOneReviewer())

    assert result.file_hit and not result.line_hit


def test_provider_reviewer_refuses_to_start_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        ProviderReviewer()


def test_main_writes_json_and_markdown(tmp_path: Path):
    root = tmp_path / "BugsInPy"
    for project, bug, patch in (
        ("demo", "1", FIX_REPLACES_EVAL),
        ("demo", "2", FIX_ONLY_ADDS_CHECK),
        ("other", "1", FIX_IN_NON_PYTHON),
    ):
        folder = root / "projects" / project / "bugs" / bug
        folder.mkdir(parents=True)
        (folder / "bug_patch.txt").write_text(patch, encoding="utf-8")

    out, md = tmp_path / "result.json", tmp_path / "result.md"
    assert main(["--bugsinpy", str(root), "--out", str(out), "--md", str(md)]) == 0

    summary = json.loads(out.read_text())["summary"]
    assert summary["bugs_with_python_changes"] == 2
    assert summary["scorable_bugs"] == 1
    assert summary["omission_bugs"] == 1
    assert summary["line_recall"] == 1.0
    assert "**Line-level recall** | **100.0%**" in md.read_text()
