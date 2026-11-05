"""Measure how many real Python bugs the reviewer flags, using BugsInPy.

BugsInPy (Widyasari et al., ESEC/FSE 2020) records real bugs from 17 Python
projects. Each bug has `bug_patch.txt`, the diff that *fixed* it. Reversing that
diff gives the change that would have *introduced* the bug: the lines the fix
removed come back as added lines. Those lines are the known-buggy lines, so a
review of the reversed diff can be scored without human labelling:

- line hit: a finding is anchored on one of the buggy lines;
- file hit: a finding is anchored anywhere in a file the bug touched.

Bugs whose fix only adds code (the bug is a missing check, a missing call...) have
no buggy line to point at after reversal. They are counted separately as
"omission" bugs and excluded from line-level recall.

The forward fix diffs are also reviewed. A fix is not supposed to introduce a
defect, so findings there are an upper bound on noise, not confirmed false
positives.

Only the deterministic static reviewer runs by default. The provider reviewer is
wired through the same interface but is never called unless `--reviewer openai`
is passed explicitly with a key, because it spends money.

The dataset is not vendored. Clone it and pass its path:

    git clone --depth 1 https://github.com/soarsmu/BugsInPy /tmp/BugsInPy
    python evals/bugsinpy_recall.py --bugsinpy /tmp/BugsInPy \
        --out reports/evals/bugsinpy-static.json --md reports/evals/bugsinpy-static.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

APP_DIR = Path(__file__).resolve().parents[1] / "backend" / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from diff_index import parse_unified_diff  # noqa: E402
from schemas import Finding  # noqa: E402

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


# ----------------------------------------------------------------------------
# Diff reversal
# ----------------------------------------------------------------------------


def reverse_patch(patch: str) -> str:
    """Return the diff that undoes `patch` (old and new sides swapped)."""
    out: list[str] = []
    pending_old: str | None = None
    for line in patch.splitlines():
        if pending_old is not None and line.startswith("+++ "):
            # Keep "---" before "+++"; swap which path each side names.
            out.append("--- " + _swap_prefix(line[4:], "b/", "a/"))
            out.append("+++ " + _swap_prefix(pending_old, "a/", "b/"))
            pending_old = None
            continue
        if line.startswith("diff --git "):
            match = re.match(r"^diff --git a/(.+) b/(.+)$", line)
            out.append(f"diff --git a/{match.group(2)} b/{match.group(1)}" if match else line)
        elif line.startswith("--- "):
            pending_old = line[4:]
        elif line.startswith("index "):
            match = re.match(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)(.*)$", line)
            out.append(f"index {match.group(2)}..{match.group(1)}{match.group(3)}" if match else line)
        elif line.startswith("new file mode"):
            out.append(line.replace("new file mode", "deleted file mode", 1))
        elif line.startswith("deleted file mode"):
            out.append(line.replace("deleted file mode", "new file mode", 1))
        elif line.startswith("@@"):
            match = _HUNK_RE.match(line)
            if not match:
                out.append(line)
                continue
            old_start, old_len, new_start, new_len, tail = match.groups()
            old = old_start + (f",{old_len}" if old_len is not None else "")
            new = new_start + (f",{new_len}" if new_len is not None else "")
            out.append(f"@@ -{new} +{old} @@{tail}")
        elif line.startswith("+"):
            out.append("-" + line[1:])
        elif line.startswith("-"):
            out.append("+" + line[1:])
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _swap_prefix(path: str, old: str, new: str) -> str:
    return new + path[len(old) :] if path.startswith(old) else path


# ----------------------------------------------------------------------------
# Reviewers
# ----------------------------------------------------------------------------


class Reviewer(Protocol):
    name: str

    def review(self, diff: str, changed_files: list[str]) -> list[Finding]: ...


@dataclass
class StaticReviewer:
    """The deterministic rules in static_review.py. No model is called."""

    name: str = "static"

    def review(self, diff: str, changed_files: list[str]) -> list[Finding]:
        from llm_reviewer import _static_review

        return list(_static_review(diff, changed_files).findings)


@dataclass
class ProviderReviewer:
    """The provider path. Spends money; only constructed with --reviewer openai."""

    name: str = "openai"

    def __post_init__(self) -> None:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise SystemExit("--reviewer openai needs OPENAI_API_KEY; nothing was called.")

    def review(self, diff: str, changed_files: list[str]) -> list[Finding]:
        from llm_reviewer import review_diff

        os.environ["AI_REVIEW_MODE"] = "openai"
        return list(review_diff(diff, changed_files).findings)


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------


@dataclass
class BugResult:
    bug: str
    python_files: int
    buggy_lines: int
    omission: bool
    findings: int
    line_hit: bool
    file_hit: bool
    rules: list[str] = field(default_factory=list)
    fix_findings: int = 0
    fix_rules: list[str] = field(default_factory=list)


def _python_only(diff: str) -> tuple[str, list[str]]:
    """Keep only the per-file sections of Python files."""
    sections = re.split(r"(?m)^(?=diff --git )", diff)
    kept = [section for section in sections if re.match(r"diff --git a/\S+\.py b/\S+\.py", section)]
    files = [re.match(r"diff --git a/\S+ b/(\S+)", section).group(1) for section in kept]
    return "".join(kept), files


def score_bug(bug: str, patch: str, reviewer: Reviewer) -> BugResult | None:
    fix_diff, files = _python_only(patch)
    if not files:
        return None
    introducing = reverse_patch(fix_diff)
    index = parse_unified_diff(introducing)
    buggy = {(path, line) for path, diff_file in index.files.items() for line in diff_file.right_lines}
    findings = reviewer.review(introducing, files)
    anchored = {(f.file, f.line) for f in findings if f.file and f.line is not None}
    fix_findings = reviewer.review(fix_diff, files)
    return BugResult(
        bug=bug,
        python_files=len(files),
        buggy_lines=len(buggy),
        omission=not buggy,
        findings=len(findings),
        line_hit=bool(anchored & buggy),
        file_hit=any(f.file in index.files for f in findings),
        rules=sorted({f.rule_id or f.category for f in findings}),
        fix_findings=len(fix_findings),
        fix_rules=sorted({f.rule_id or f.category for f in fix_findings}),
    )


def iter_bugs(root: Path):
    for patch in sorted(root.glob("projects/*/bugs/*/bug_patch.txt"), key=_bug_sort_key):
        project = patch.parts[-4]
        bug_id = patch.parts[-2]
        yield f"{project}-{bug_id}", patch.read_text(encoding="utf-8", errors="replace")


def _bug_sort_key(path: Path):
    project, bug_id = path.parts[-4], path.parts[-2]
    return (project, int(bug_id) if bug_id.isdigit() else bug_id)


def summarize(results: list[BugResult]) -> dict:
    scorable = [r for r in results if not r.omission]
    per_project: dict[str, dict] = {}
    for result in results:
        project = result.bug.rsplit("-", 1)[0]
        bucket = per_project.setdefault(project, {"bugs": 0, "scorable": 0, "line_hits": 0})
        bucket["bugs"] += 1
        bucket["scorable"] += int(not result.omission)
        bucket["line_hits"] += int(result.line_hit)
    rule_counts = Counter(rule for r in results for rule in r.rules)
    fix_rule_counts = Counter(rule for r in results for rule in r.fix_rules)
    return {
        "bugs_with_python_changes": len(results),
        "omission_bugs": len(results) - len(scorable),
        "scorable_bugs": len(scorable),
        "line_hits": sum(r.line_hit for r in scorable),
        "line_recall": _ratio(sum(r.line_hit for r in scorable), len(scorable)),
        "file_hits": sum(r.file_hit for r in scorable),
        "file_recall": _ratio(sum(r.file_hit for r in scorable), len(scorable)),
        "bugs_with_any_finding": sum(r.findings > 0 for r in results),
        "fix_diffs_with_findings": sum(r.fix_findings > 0 for r in results),
        "rules_on_introducing_diffs": dict(rule_counts.most_common()),
        "rules_on_fix_diffs": dict(fix_rule_counts.most_common()),
        "per_project": dict(sorted(per_project.items())),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def render_markdown(summary: dict, meta: dict, results: list[BugResult]) -> str:
    lines = [
        f"# BugsInPy recall: {meta['reviewer']} reviewer",
        "",
        f"Dataset: BugsInPy at commit `{meta['bugsinpy_commit']}` ({meta['patches_found']} bug patches).",
        f"Reviewer: `{meta['reviewer']}`. Generated by `evals/bugsinpy_recall.py`.",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Bugs with Python changes | {summary['bugs_with_python_changes']} |",
        f"| Omission bugs (fix only adds code; no buggy line) | {summary['omission_bugs']} |",
        f"| Scorable bugs | {summary['scorable_bugs']} |",
        f"| Line-level hits | {summary['line_hits']} |",
        f"| **Line-level recall** | **{_percent(summary['line_recall'])}** |",
        f"| File-level hits | {summary['file_hits']} |",
        f"| File-level recall | {_percent(summary['file_recall'])} |",
        f"| Introducing diffs with any finding | {summary['bugs_with_any_finding']} |",
        f"| Fix diffs with any finding (noise upper bound) | {summary['fix_diffs_with_findings']} |",
        "",
        "## Rules that fired",
        "",
        "| Rule | Introducing diffs | Fix diffs |",
        "| --- | ---: | ---: |",
    ]
    rules = sorted(
        set(summary["rules_on_introducing_diffs"]) | set(summary["rules_on_fix_diffs"])
    )
    for rule in rules:
        lines.append(
            f"| `{rule}` | {summary['rules_on_introducing_diffs'].get(rule, 0)} | "
            f"{summary['rules_on_fix_diffs'].get(rule, 0)} |"
        )
    if not rules:
        lines.append("| (none) | 0 | 0 |")
    lines += ["", "## Line-level hits", ""]
    hits = [r for r in results if r.line_hit]
    lines += [f"- `{r.bug}`: {', '.join(r.rules)}" for r in hits] or ["- none"]
    lines += [
        "",
        "## Per project",
        "",
        "| Project | Bugs | Scorable | Line hits |",
        "| --- | ---: | ---: | ---: |",
    ]
    for project, bucket in summary["per_project"].items():
        lines.append(
            f"| {project} | {bucket['bugs']} | {bucket['scorable']} | {bucket['line_hits']} |"
        )
    return "\n".join(lines) + "\n"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bugsinpy", required=True, type=Path, help="Path to a BugsInPy clone")
    parser.add_argument("--reviewer", choices=("static", "openai"), default="static")
    parser.add_argument("--limit", type=int, default=None, help="Score only the first N bugs")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--md", type=Path, default=None)
    args = parser.parse_args(argv)

    reviewer: Reviewer = StaticReviewer() if args.reviewer == "static" else ProviderReviewer()
    bugs = list(iter_bugs(args.bugsinpy))
    if args.limit is not None:
        bugs = bugs[: args.limit]
    results = [r for bug, patch in bugs if (r := score_bug(bug, patch, reviewer)) is not None]
    summary = summarize(results)
    meta = {
        "reviewer": reviewer.name,
        "bugsinpy_commit": _git_head(args.bugsinpy),
        "patches_found": len(bugs),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"meta": meta, "summary": summary, "bugs": [asdict(r) for r in results]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_markdown(summary, meta, results), encoding="utf-8")
    print(
        f"{meta['reviewer']}: line recall {_percent(summary['line_recall'])} "
        f"({summary['line_hits']}/{summary['scorable_bugs']} scorable bugs), "
        f"file recall {_percent(summary['file_recall'])}, "
        f"{summary['omission_bugs']} omission bugs"
    )
    return 0


def _git_head(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
