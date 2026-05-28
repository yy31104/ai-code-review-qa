from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "reports" / "evals" / "results.json"
DEFAULT_MARKDOWN = REPO_ROOT / "reports" / "evals" / "summary.md"
DEFAULT_HTML = REPO_ROOT / "reports" / "evals" / "summary.html"


def load_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_markdown(results: dict[str, Any]) -> str:
    summary = results["summary"]
    lines = [
        "# AI Code Review Eval Summary",
        "",
        f"- Cases passed: **{summary['passed_cases']} / {summary['total_cases']}**",
        f"- Case pass rate: **{summary['case_pass_rate']:.0%}**",
        f"- Checks passed: **{summary['passed_checks']} / {summary['total_checks']}**",
        f"- Check pass rate: **{summary['check_pass_rate']:.0%}**",
        "",
        "## Cases",
        "",
        "| Case | Status | Failed checks | Tags |",
        "| --- | --- | --- | --- |",
    ]

    for case in results["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        failed_checks = [check["name"] for check in case["checks"] if not check["passed"]]
        failed_text = ", ".join(failed_checks) if failed_checks else "-"
        tags = ", ".join(case.get("tags", [])) or "-"
        lines.append(f"| `{case['id']}` | {status} | {failed_text} | {tags} |")

    lines.extend(["", "## Failed Check Details", ""])
    any_failed = False
    for case in results["cases"]:
        failed = [check for check in case["checks"] if not check["passed"]]
        if not failed:
            continue
        any_failed = True
        lines.append(f"### {case['id']}")
        lines.append("")
        for check in failed:
            lines.append(f"- `{check['name']}`: {check['detail']}")
        lines.append("")

    if not any_failed:
        lines.append("No failed checks.")
        lines.append("")

    return "\n".join(lines)


def render_html(markdown_text: str) -> str:
    escaped = html.escape(markdown_text)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>AI Code Review Eval Summary</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.5; margin: 2rem; max-width: 960px; }}
    pre {{ white-space: pre-wrap; background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 8px; padding: 1rem; }}
  </style>
</head>
<body>
  <pre>{escaped}</pre>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render eval JSON results to Markdown and HTML.")
    parser.add_argument("--in", dest="input_path", type=Path, default=DEFAULT_INPUT, help="Input JSON results path.")
    parser.add_argument("--md", dest="markdown_path", type=Path, default=DEFAULT_MARKDOWN, help="Output Markdown path.")
    parser.add_argument("--html", dest="html_path", type=Path, default=DEFAULT_HTML, help="Output HTML path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = load_results(args.input_path)
    markdown_text = render_markdown(results)
    args.markdown_path.parent.mkdir(parents=True, exist_ok=True)
    args.html_path.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_path.write_text(markdown_text + "\n", encoding="utf-8")
    args.html_path.write_text(render_html(markdown_text), encoding="utf-8")
    print(f"Wrote {args.markdown_path}")
    print(f"Wrote {args.html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
