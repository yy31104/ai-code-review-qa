from __future__ import annotations

import evals.run_local  # noqa: F401
from diff_index import parse_unified_diff, resolve_anchor


def test_single_file_one_hunk_records_added_line() -> None:
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1,2 +1,3 @@",
            " def existing():",
            "+    return 1",
            " pass",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["app.py"].right_lines == {2}
    assert resolve_anchor(index, "app.py") == 2


def test_realistic_hunk_start_line_differs_from_first_added_line() -> None:
    diff = "\n".join(
        [
            "diff --git a/backend/app/auth.py b/backend/app/auth.py",
            "--- a/backend/app/auth.py",
            "+++ b/backend/app/auth.py",
            "@@ -10,2 +10,3 @@",
            " def existing():",
            "+    authToken = build(user)",
            "     return user",
        ]
    )

    index = parse_unified_diff(diff)

    assert resolve_anchor(index, "backend/app/auth.py") == 11


def test_multi_hunk_file_returns_smallest_added_line() -> None:
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -5,1 +5,2 @@",
            " existing",
            "+first",
            "@@ -20,1 +21,2 @@",
            " later",
            "+second",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["app.py"].right_lines == {6, 22}
    assert resolve_anchor(index, "app.py") == 6


def test_multi_file_diff_keeps_file_anchors_isolated() -> None:
    diff = "\n".join(
        [
            "diff --git a/a.py b/a.py",
            "--- a/a.py",
            "+++ b/a.py",
            "@@ -1,1 +1,2 @@",
            " a",
            "+a2",
            "diff --git a/b.py b/b.py",
            "--- a/b.py",
            "+++ b/b.py",
            "@@ -30,1 +30,2 @@",
            " b",
            "+b2",
        ]
    )

    index = parse_unified_diff(diff)

    assert resolve_anchor(index, "a.py") == 2
    assert resolve_anchor(index, "b.py") == 31


def test_modified_line_records_left_and_right_lines() -> None:
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -3,1 +3,1 @@",
            "-old",
            "+new",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["app.py"].left_lines == {3}
    assert index.files["app.py"].right_lines == {3}
    assert resolve_anchor(index, "app.py") == 3


def test_pure_deletion_hunk_has_no_right_anchor() -> None:
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ /dev/null",
            "@@ -4,2 +0,0 @@",
            "-old",
            "-older",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["app.py"].left_lines == {4, 5}
    assert index.files["app.py"].right_lines == set()
    assert resolve_anchor(index, "app.py") is None


def test_pure_addition_file_anchors_to_first_added_line() -> None:
    diff = "\n".join(
        [
            "diff --git a/new.py b/new.py",
            "--- /dev/null",
            "+++ b/new.py",
            "@@ -0,0 +1,2 @@",
            "+one",
            "+two",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["new.py"].right_lines == {1, 2}
    assert resolve_anchor(index, "new.py") == 1


def test_renamed_file_anchors_to_new_path() -> None:
    diff = "\n".join(
        [
            "diff --git a/old.py b/new.py",
            "similarity index 88%",
            "rename from old.py",
            "rename to new.py",
            "--- a/old.py",
            "+++ b/new.py",
            "@@ -1,1 +1,2 @@",
            " value = 1",
            "+extra = 2",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["new.py"].is_rename is True
    assert index.files["new.py"].new_path == "new.py"
    assert resolve_anchor(index, "new.py") == 2
    assert resolve_anchor(index, "old.py") is None


def test_binary_file_has_no_right_anchor() -> None:
    diff = "\n".join(
        [
            "diff --git a/image.png b/image.png",
            "index 111..222 100644",
            "Binary files a/image.png and b/image.png differ",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["image.png"].is_binary is True
    assert resolve_anchor(index, "image.png") is None


def test_no_newline_marker_does_not_advance_counters() -> None:
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1,1 +1,2 @@",
            " context",
            "+added",
            "\\ No newline at end of file",
        ]
    )

    index = parse_unified_diff(diff)

    assert index.files["app.py"].right_lines == {2}
    assert resolve_anchor(index, "app.py") == 2


def test_crlf_diff_is_parsed() -> None:
    diff = "\r\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1,1 +1,2 @@",
            " context",
            "+added",
        ]
    )

    index = parse_unified_diff(diff)

    assert resolve_anchor(index, "app.py") == 2


def test_empty_diff_has_no_files_or_anchor() -> None:
    index = parse_unified_diff("")

    assert index.files == {}
    assert resolve_anchor(index, "app.py") is None


def test_parsing_and_anchor_resolution_are_deterministic() -> None:
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1,1 +1,2 @@",
            " context",
            "+added",
        ]
    )

    first = parse_unified_diff(diff)
    second = parse_unified_diff(diff)

    assert first == second
    assert resolve_anchor(first, "app.py") == resolve_anchor(second, "app.py")


def test_change_blocks_pair_removed_and_added_lines() -> None:
    from diff_index import parse_unified_diff

    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -10,4 +10,4 @@\n"
        " def f():\n"
        "-    old_one()\n"
        "-    old_two()\n"
        "+    new_one()\n"
        " def g():\n"
        "@@ -30,1 +30,2 @@\n"
        " def h():\n"
        "+    fresh()\n"
    )

    diff_file = parse_unified_diff(diff).files["a.py"]

    assert len(diff_file.change_blocks) == 2
    assert list(diff_file.change_blocks[0].removed.values()) == ["    old_one()", "    old_two()"]
    assert list(diff_file.change_blocks[0].added.values()) == ["    new_one()"]
    # The second hunk's addition replaced nothing.
    assert diff_file.change_blocks[1].removed == {}
    assert diff_file.block_of_added[31] == 1


def test_a_context_line_closes_a_change_block() -> None:
    from diff_index import parse_unified_diff

    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1,5 +1,5 @@\n"
        "-first()\n"
        "+first_v2()\n"
        " untouched()\n"
        "-second()\n"
        "+second_v2()\n"
    )

    diff_file = parse_unified_diff(diff).files["a.py"]

    assert len(diff_file.change_blocks) == 2
