from __future__ import annotations

# Importing the eval runner puts backend/app on sys.path, matching the existing
# test modules in this directory.
import evals.run_local  # noqa: F401
from static_review import RULE_IDS, analyze_diff, build_logical_lines
from diff_index import parse_unified_diff


def make_diff(path: str, start: int, lines: list[tuple[str, str]]) -> str:
    """Build a one-hunk unified diff. `lines` entries are (' ' | '+', text)."""
    old_count = sum(1 for kind, _ in lines if kind == " ")
    body = "\n".join(f"{kind}{text}" for kind, text in lines)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -{start},{old_count} +{start},{len(lines)} @@\n{body}\n"
    )


def rules_for(diff: str, changed_files: list[str]) -> list[tuple[str, int]]:
    return [(match.rule_id, match.line) for match in analyze_diff(diff, changed_files).matches]


def test_every_rule_id_is_unique() -> None:
    assert len(set(RULE_IDS)) == len(RULE_IDS)


def test_findings_anchor_to_the_added_line_not_the_first_line() -> None:
    diff = make_diff(
        "backend/app/svc.py",
        30,
        [
            (" ", "def run(cmd):"),
            (" ", "    log(cmd)"),
            ("+", "    subprocess.run(cmd, shell=True)"),
        ],
    )

    assert rules_for(diff, ["backend/app/svc.py"]) == [("subprocess_shell_true", 32)]


def test_context_only_lines_never_produce_findings() -> None:
    """An unchanged line is read for context but is not this diff's responsibility."""
    diff = make_diff(
        "backend/app/svc.py",
        10,
        [
            (" ", "    subprocess.run(cmd, shell=True)"),
            ("+", "    log('done')"),
        ],
    )

    assert rules_for(diff, ["backend/app/svc.py"]) == []


def test_deleted_lines_produce_no_findings() -> None:
    diff = (
        "diff --git a/backend/app/svc.py b/backend/app/svc.py\n"
        "--- a/backend/app/svc.py\n+++ b/backend/app/svc.py\n"
        "@@ -4,2 +4,1 @@\n"
        " def run(cmd):\n"
        "-    subprocess.run(cmd, shell=True)\n"
    )

    assert rules_for(diff, ["backend/app/svc.py"]) == []


def test_continuation_lines_are_joined_into_one_statement() -> None:
    diff = make_diff(
        "backend/app/client.py",
        8,
        [
            (" ", "def fetch(url):"),
            ("+", "    return requests.get("),
            ("+", "        url,"),
            ("+", "        timeout=5,"),
            ("+", "    )"),
        ],
    )
    diff_file = parse_unified_diff(diff).files["backend/app/client.py"]
    logical = build_logical_lines("backend/app/client.py", diff_file)

    assert len(logical) == 1
    assert logical[0].line == 9
    assert "timeout=5" in logical[0].code
    assert rules_for(diff, ["backend/app/client.py"]) == []


def test_string_contents_do_not_trigger_code_rules() -> None:
    diff = make_diff(
        "backend/app/svc.py",
        4,
        [
            (" ", "def warn():"),
            ("+", '    logger.info("do not pass shell=True here")'),
        ],
    )

    assert rules_for(diff, ["backend/app/svc.py"]) == []


def test_comment_contents_do_not_trigger_code_rules() -> None:
    diff = make_diff(
        "backend/app/svc.py",
        4,
        [
            (" ", "def warn():"),
            ("+", "    return run(cmd)  # never shell=True"),
        ],
    )

    assert rules_for(diff, ["backend/app/svc.py"]) == []


def test_evidence_is_the_exact_added_line() -> None:
    diff = make_diff(
        "backend/app/settings.py",
        2,
        [(" ", "import os"), ("+", 'API_KEY = "9f2b7c41d8e35a06b1c4"')],
    )

    match = analyze_diff(diff, ["backend/app/settings.py"]).matches[0]
    assert match.evidence == 'API_KEY = "9f2b7c41d8e35a06b1c4"'


def test_analysis_is_ordered_and_repeatable() -> None:
    diff = make_diff(
        "backend/app/billing.py",
        10,
        [
            ("+", "def charge(order, retries=[]):"),
            ("+", "    subprocess.run(cmd(order), shell=True)"),
        ],
    )

    first = rules_for(diff, ["backend/app/billing.py"])
    second = rules_for(diff, ["backend/app/billing.py"])

    assert first == second
    assert first == sorted(first, key=lambda item: (item[1] if item[1] is not None else -1, item[0]))


def test_unanchorable_files_are_reported() -> None:
    result = analyze_diff("", ["backend/app/brand_new.py"])

    assert result.matches == []
    assert result.unanchorable_files == ["backend/app/brand_new.py"]


def test_editing_a_line_does_not_introduce_a_preexisting_construct() -> None:
    """The dominant false positive on real code: churn on a line that already had it."""
    diff = (
        "diff --git a/backend/app/app.py b/backend/app/app.py\n"
        "--- a/backend/app/app.py\n+++ b/backend/app/app.py\n"
        "@@ -1510,3 +1510,3 @@\n"
        "             response = self.handle_exception(e)\n"
        "-        except:  # noqa: B001\n"
        "+        except:\n"
        "             error = sys.exc_info()[1]\n"
    )

    assert rules_for(diff, ["backend/app/app.py"]) == []


def test_a_construct_added_without_a_removal_is_still_reported() -> None:
    diff = make_diff(
        "backend/app/app.py",
        10,
        [(" ", "def handle(job):"), ("+", "    try:"), ("+", "        job.run()"), ("+", "    except:")],
    )

    assert rules_for(diff, ["backend/app/app.py"]) == [("broad_except", 13)]


def test_replacing_a_line_with_a_new_construct_is_reported() -> None:
    """Attribution suppresses only what the removed line already had."""
    diff = (
        "diff --git a/backend/app/cart.py b/backend/app/cart.py\n"
        "--- a/backend/app/cart.py\n+++ b/backend/app/cart.py\n"
        "@@ -5,2 +5,2 @@\n"
        "-def add_items(cart, items):\n"
        "+def add_items(cart, items=[]):\n"
        "     cart.extend(items)\n"
    )

    assert rules_for(diff, ["backend/app/cart.py"]) == [("mutable_default_argument", 5)]


def test_evidence_never_quotes_an_unchanged_line() -> None:
    """A statement can start on context; the evidence must still be an added line."""
    diff = make_diff(
        "backend/app/client.py",
        20,
        [
            (" ", "def fetch(url, retries):"),
            ("+", "    timeout = None"),
            ("+", "    return requests.get(url)"),
        ],
    )

    match = analyze_diff(diff, ["backend/app/client.py"]).matches[0]
    assert match.line == 22
    assert match.evidence == "return requests.get(url)"


def test_a_touched_signature_does_not_fire_on_a_context_def_line() -> None:
    diff = (
        "diff --git a/backend/app/helpers.py b/backend/app/helpers.py\n"
        "--- a/backend/app/helpers.py\n+++ b/backend/app/helpers.py\n"
        "@@ -398,4 +398,4 @@\n"
        " def send_file(\n"
        "-    path_or_file: os.PathLike[str] | str | t.BinaryIO,\n"
        "+    path_or_file: os.PathLike[str] | str | t.IO[bytes],\n"
        "     mimetype: str | None = None,\n"
        " ):\n"
    )

    assert rules_for(diff, ["backend/app/helpers.py"]) == []
