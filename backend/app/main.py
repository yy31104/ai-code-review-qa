from __future__ import annotations

import argparse
import sys
from pathlib import Path

from git_diff_reader import read_git_diff
from llm_reviewer import derive_decision, review_diff
from report_generator import generate_report
from test_runner import run_tests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an AI code review QA report from a working-tree or commit-range diff."
    )
    parser.add_argument("--repo", required=True, help="Path to the repository or project to review.")
    parser.add_argument("--output", required=True, help="Path for the generated HTML report.")
    parser.add_argument("--base", help="Base git ref for commit range diff. Use with --head.")
    parser.add_argument("--head", help="Head git ref for commit range diff. Use with --base.")

    args = parser.parse_args()
    if bool(args.base) != bool(args.head):
        parser.error("--base and --head must be provided together.")
    return args


def main() -> int:
    args = parse_args()
    repo_path = Path(args.repo).resolve()
    output_path = Path(args.output).resolve()

    try:
        git_result = read_git_diff(repo_path, base=args.base, head=args.head)
        test_result = run_tests(repo_path)
        review = review_diff(git_result.diff, git_result.changed_files)
        review.automated_test_results = test_result
        review.review_decision, review.human_review_decision = derive_decision(
            review.risk_level,
            test_result,
        )

        if git_result.error:
            review.recommended_actions.append(f"Git diff warning: {git_result.error}")

        report_path = generate_report(review, output_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Review report generated: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
