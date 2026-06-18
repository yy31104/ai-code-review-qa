from __future__ import annotations

import argparse
import sys
from pathlib import Path

from diff_index import parse_unified_diff
from git_diff_reader import read_git_diff
from github_review_reporter import (
    all_finding_fingerprints,
    build_inline_review_payload,
    build_review_payload,
    build_summary_comment_body,
    write_payload,
)
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
    parser.add_argument("--emit-github-review", help="Optional path for a dry-run GitHub review payload JSON.")
    parser.add_argument("--emit-summary-comment", help="Optional path for a dry-run GitHub summary comment JSON.")
    parser.add_argument("--emit-inline-review", help="Optional path for a dry-run GitHub inline review payload JSON.")
    parser.add_argument("--emit-finding-fingerprints", help="Optional path for current finding fingerprints JSON.")
    parser.add_argument("--head-sha", help="Optional commit SHA to include in dry-run GitHub review payloads.")

    args = parser.parse_args()
    if bool(args.base) != bool(args.head):
        parser.error("--base and --head must be provided together.")
    return args


def main() -> int:
    args = parse_args()
    repo_path = Path(args.repo).resolve()
    output_path = Path(args.output).resolve()
    github_review_path = Path(args.emit_github_review).resolve() if args.emit_github_review else None
    summary_comment_path = Path(args.emit_summary_comment).resolve() if args.emit_summary_comment else None
    inline_review_path = Path(args.emit_inline_review).resolve() if args.emit_inline_review else None
    finding_fingerprints_path = (
        Path(args.emit_finding_fingerprints).resolve() if args.emit_finding_fingerprints else None
    )

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
        emitted_payload_path = None
        emitted_summary_path = None
        emitted_inline_path = None
        emitted_fingerprints_path = None
        diff_index = None
        if github_review_path:
            diff_index = parse_unified_diff(git_result.diff)
            payload = build_review_payload(review, diff_index, head_sha=args.head_sha)
            emitted_payload_path = write_payload(payload, github_review_path)
        if summary_comment_path:
            if diff_index is None:
                diff_index = parse_unified_diff(git_result.diff)
            summary_body = build_summary_comment_body(review, diff_index)
            emitted_summary_path = write_payload({"body": summary_body}, summary_comment_path)
        if inline_review_path:
            if diff_index is None:
                diff_index = parse_unified_diff(git_result.diff)
            inline_payload = build_inline_review_payload(review, diff_index, head_sha=args.head_sha)
            emitted_inline_path = write_payload(inline_payload, inline_review_path)
        if finding_fingerprints_path:
            fingerprints = all_finding_fingerprints(review)
            emitted_fingerprints_path = write_payload({"fingerprints": fingerprints}, finding_fingerprints_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Review report generated: {report_path}")
    if emitted_payload_path:
        print(f"GitHub review payload generated: {emitted_payload_path}")
    if emitted_summary_path:
        print(f"GitHub summary comment generated: {emitted_summary_path}")
    if emitted_inline_path:
        print(f"GitHub inline review payload generated: {emitted_inline_path}")
    if emitted_fingerprints_path:
        print(f"Finding fingerprints generated: {emitted_fingerprints_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
