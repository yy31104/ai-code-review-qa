"""Provider confidence as agreement across samples, behind AI_REVIEW_CONSISTENCY_SAMPLES.

The provider is a fake that returns a scripted response per call. No network
call or API key is used.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Put backend/app on sys.path in the same way as the other application tests.
import evals.run_local  # noqa: F401
from diff_index import parse_unified_diff
from github_review_reporter import route_inline_findings
from llm_reviewer import CONSISTENCY_SAMPLES_ENV, MAX_DIFF_CHARS, review_diff
from report_generator import generate_report
from sample_consistency import aggregate_by_agreement
from schemas import Finding, ProposedFinding, ProviderReview

PATH = "service.py"
LINES = [
    "def load(path, retries):",
    "    handle = open(path)",
    "    data = handle.read()",
    "    ratio = len(data) / retries",
    "    return data, ratio",
]
DIFF = (
    f"diff --git a/{PATH} b/{PATH}\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    f"+++ b/{PATH}\n"
    f"@@ -0,0 +1,{len(LINES)} @@\n" + "".join(f"+{line}\n" for line in LINES)
)


def proposal(line: int, category: str = "possible_bug", **overrides: object) -> ProposedFinding:
    payload: dict[str, object] = {
        "file": PATH,
        "line": line,
        "category": category,
        "severity": "medium",
        # Every scripted sample claims near-certainty. None of it may survive.
        "confidence": 0.99,
        "message": f"Issue on line {line}.",
        "evidence": LINES[line - 1] if 1 <= line <= len(LINES) else None,
    }
    payload.update(overrides)
    return ProposedFinding(**payload)  # type: ignore[arg-type]


def sample(*findings: ProposedFinding) -> ProviderReview:
    return ProviderReview(project_summary="Loads a file.", findings=list(findings))


def install_provider(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[object],
    *,
    samples: str | None,
) -> dict[str, list[object]]:
    """Return scripted responses in order; an Exception instance is raised instead."""
    seen: dict[str, list[object]] = {"user": [], "system": []}
    queue = list(responses)

    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            messages = kwargs["input"]
            seen["system"].append(messages[0]["content"])  # type: ignore[index]
            seen["user"].append(messages[1]["content"])  # type: ignore[index]
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(output_parsed=item)

    class FakeOpenAI:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    monkeypatch.setenv("AI_REVIEW_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    if samples is None:
        monkeypatch.delenv(CONSISTENCY_SAMPLES_ENV, raising=False)
    else:
        monkeypatch.setenv(CONSISTENCY_SAMPLES_ENV, samples)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    return seen


def provider_findings(review) -> list[Finding]:  # type: ignore[no-untyped-def]
    return [finding for finding in review.findings if finding.rule_id is None]


def by_line(review) -> dict[int, Finding]:  # type: ignore[no-untyped-def]
    return {finding.line: finding for finding in provider_findings(review)}  # type: ignore[misc]


FIVE_SAMPLES = [
    sample(proposal(2), proposal(4, "security_reliability"), proposal(3)),
    sample(proposal(2), proposal(4, "security_reliability")),
    sample(proposal(2), proposal(4, "security_reliability")),
    sample(proposal(2)),
    sample(proposal(2)),
]


def test_flag_off_makes_one_call_and_keeps_self_report_out_of_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = install_provider(monkeypatch, [sample(proposal(2))], samples=None)

    review = review_diff(DIFF, [PATH])
    html = generate_report(review, tmp_path / "r.html").read_text(encoding="utf-8")

    assert len(seen["user"]) == 1
    assert review.provider_samples == 1
    [finding] = provider_findings(review)
    assert finding.confidence_source == "model_self_report"
    # The value is kept in the artifact for auditing ...
    assert finding.confidence == pytest.approx(0.99)
    # ... but the report does not present it as a measurement.
    assert "99%" not in html
    assert "confidence not measured" in html
    assert "samples:" not in html


def test_sample_count_of_one_is_the_same_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_provider(monkeypatch, [sample(proposal(2))], samples="1")

    review = review_diff(DIFF, [PATH])

    assert len(seen["user"]) == 1
    assert provider_findings(review)[0].confidence_source == "model_self_report"


def test_confidence_is_the_share_of_samples_that_reported_the_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = install_provider(monkeypatch, list(FIVE_SAMPLES), samples="5")

    review = review_diff(DIFF, [PATH])
    found = by_line(review)

    assert len(seen["user"]) == 5
    assert review.review_status == "completed"
    assert review.provider_samples == 5
    assert {line: f.confidence for line, f in found.items()} == {2: 1.0, 4: 0.6, 3: 0.2}
    assert {line: (f.sample_support, f.sample_count) for line, f in found.items()} == {
        2: (5, 5),
        4: (3, 5),
        3: (1, 5),
    }
    assert all(f.confidence_source == "sample_agreement" for f in found.values())
    assert "agreement across 5 samples" in (review.review_status_detail or "")


def test_every_sample_receives_identical_input(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_provider(monkeypatch, list(FIVE_SAMPLES), samples="5")

    review_diff(DIFF, [PATH])

    assert len(set(seen["user"])) == 1
    assert len(set(seen["system"])) == 1


def test_report_shows_agreement_and_never_the_self_reported_number(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_provider(monkeypatch, list(FIVE_SAMPLES), samples="5")

    review = review_diff(DIFF, [PATH])
    html = generate_report(review, tmp_path / "r.html").read_text(encoding="utf-8")

    assert "in 5/5 samples" in html
    assert "in 3/5 samples" in html
    assert "in 1/5 samples" in html
    assert "99%" not in html
    assert "samples: 5" in html


def test_a_sample_repeating_a_finding_still_casts_one_vote(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        sample(proposal(2), proposal(2, message="Same line, reworded.")),
        sample(),
    ]
    install_provider(monkeypatch, responses, samples="2")

    found = by_line(review_diff(DIFF, [PATH]))

    assert found[2].sample_support == 1
    assert found[2].confidence == 0.5
    assert found[2].message == "Issue on line 2."


def test_a_sample_supports_a_finding_only_if_it_survives_grounding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        sample(proposal(2)),
        sample(proposal(2, evidence="os.remove(path)")),
        sample(proposal(900, evidence=LINES[1])),
        sample(proposal(2)),
    ]
    install_provider(monkeypatch, responses, samples="4")

    review = review_diff(DIFF, [PATH])

    assert by_line(review)[2].sample_support == 2
    assert by_line(review)[2].confidence == 0.5
    assert {f.grounding_rejection for f in review.rejected_findings} == {
        "evidence_mismatch",
        "line_not_added",
    }
    assert "rejected by grounding" in (review.review_status_detail or "")


def test_different_category_on_the_same_line_is_a_different_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [sample(proposal(4, "possible_bug")), sample(proposal(4, "security_reliability"))]
    install_provider(monkeypatch, responses, samples="2")

    findings = provider_findings(review_diff(DIFF, [PATH]))

    assert sorted((f.category, f.confidence) for f in findings) == [
        ("possible_bug", 0.5),
        ("security_reliability", 0.5),
    ]


def test_line_less_findings_agree_only_on_the_same_message() -> None:
    general = dict(file=None, line=None, category="recommended_action", severity="info")
    samples = [
        [Finding(message="Add tests for load().", **general)],  # type: ignore[arg-type]
        [Finding(message="add tests  for LOAD().", **general)],  # type: ignore[arg-type]
        [Finding(message="Consider documenting load().", **general)],  # type: ignore[arg-type]
    ]

    findings, rejected = aggregate_by_agreement(samples, parse_unified_diff(DIFF))

    assert rejected == []
    assert [(f.message, f.sample_support) for f in findings] == [
        ("Add tests for load().", 2),
        ("Consider documenting load().", 1),
    ]


def test_inline_routing_uses_agreement_instead_of_self_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every sample claimed 0.99; only majority-supported findings may go inline."""
    install_provider(monkeypatch, list(FIVE_SAMPLES), samples="5")

    review = review_diff(DIFF, [PATH])
    inline, summary = route_inline_findings(review, parse_unified_diff(DIFF))

    assert sorted(f.line for f in inline if f.rule_id is None) == [2, 4]
    assert [f.line for f in summary if f.rule_id is None] == [3]


@pytest.mark.parametrize("value", ["0", "11", "-3", "five", "2.5"])
def test_invalid_sample_count_is_a_configuration_error_with_no_call(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    seen = install_provider(monkeypatch, [], samples=value)

    review = review_diff(DIFF, [PATH])

    assert seen["user"] == []
    assert review.review_status == "configuration_error"
    assert CONSISTENCY_SAMPLES_ENV in (review.review_status_detail or "")
    assert review.findings == []


def test_one_failed_sample_fails_the_whole_review(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[object] = [sample(proposal(2)), RuntimeError("rate limited"), sample(proposal(2))]
    seen = install_provider(monkeypatch, responses, samples="3")

    review = review_diff(DIFF, [PATH])

    assert len(seen["user"]) == 2
    assert review.review_status == "provider_failed"
    assert review.findings == []
    assert review.review_decision == "needs_human_review"


def test_one_unparsed_sample_is_invalid_output(monkeypatch: pytest.MonkeyPatch) -> None:
    install_provider(monkeypatch, [sample(proposal(2)), {"not": "a review"}], samples="2")

    review = review_diff(DIFF, [PATH])

    assert review.review_status == "invalid_output"
    assert review.findings == []


def test_rule_findings_keep_rule_confidence_alongside_sampling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    diff = (
        "diff --git a/h.py b/h.py\nnew file mode 100644\n--- /dev/null\n+++ b/h.py\n"
        "@@ -0,0 +1,2 @@\n+def handle(user_input):\n+    return eval(user_input)\n"
    )
    duplicate = ProposedFinding(
        file="h.py",
        line=2,
        category="security_reliability",
        severity="high",
        confidence=0.99,
        message="eval on input.",
        evidence="    return eval(user_input)",
    )
    install_provider(
        monkeypatch, [sample(duplicate), sample(duplicate), sample()], samples="3"
    )

    review = review_diff(diff, ["h.py"])
    html = generate_report(review, tmp_path / "r.html").read_text(encoding="utf-8")

    on_line = [f for f in review.findings if f.line == 2]
    assert len(on_line) == 1
    assert on_line[0].rule_id is not None
    assert on_line[0].confidence_source == "rule"
    assert "rule confidence" in html


def test_oversized_diff_with_sampling_stays_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    padding = [f"value_{i} = compute_something_reasonably_long({i})  # padding" for i in range(400)]
    big = (
        "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"
        f"@@ -0,0 +1,{len(padding)} @@\n" + "".join(f"+{line}\n" for line in padding)
    )
    diff = big + DIFF
    assert len(diff) > MAX_DIFF_CHARS
    seen = install_provider(monkeypatch, [sample(), sample(), sample()], samples="3")

    review = review_diff(diff, ["a.py", PATH])

    assert review.review_status == "incomplete"
    assert review.review_decision == "needs_human_review"
    assert all(f"diff --git a/{PATH}" not in user for user in seen["user"])  # type: ignore[operator]
    detail = review.review_status_detail or ""
    assert f"not sent: {PATH}" in detail
    assert "agreement across 3 samples" in detail
