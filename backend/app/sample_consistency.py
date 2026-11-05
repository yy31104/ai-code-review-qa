"""Confidence from agreement across repeated provider samples.

A model's own confidence number is a poor guide to whether a finding is real:
verbalized confidence is consistently overconfident, while agreement among
several sampled answers to the same prompt separates right from wrong answers
better (Xiong et al., "Can LLMs Express Their Uncertainty? An Empirical
Evaluation of Confidence Elicitation in LLMs", ICLR 2024).

This module applies that idea to review findings. The same request is sampled
``n`` times. Each sample is grounded on its own, so a sample only supports a
finding it attached to an added line with valid evidence. A finding's
confidence is then ``supporting samples / n``.

Two findings from different samples are "the same finding" when they share a
file, line and category. Line-less findings carry no position to compare, so
for them the normalised message is part of the key as well; in practice such
findings rarely agree across samples and get low confidence, which is accurate:
their agreement cannot be measured.
"""

from __future__ import annotations

from collections import Counter

from diff_index import DiffIndex, normalize_path
from finding_grounding import ground_findings
from schemas import Finding

FindingKey = tuple[str, int | None, str, str]


def finding_key(finding: Finding) -> FindingKey:
    message = "" if finding.line is not None else " ".join(finding.message.lower().split())
    return (normalize_path(finding.file), finding.line, finding.category, message)


def aggregate_by_agreement(
    samples: list[list[Finding]], diff_index: DiffIndex
) -> tuple[list[Finding], list[Finding]]:
    """Merge per-sample proposals into findings whose confidence is agreement.

    Returns ``(grounded, rejected)``. ``grounded`` holds one finding per key, in
    order of first appearance, with the wording of its first occurrence.
    ``rejected`` holds each distinct grounding rejection once.
    """
    total = len(samples)
    if total == 0:
        return [], []

    support: Counter[FindingKey] = Counter()
    first_seen: dict[FindingKey, Finding] = {}
    rejected: list[Finding] = []
    rejected_seen: set[tuple[FindingKey, str | None]] = set()

    for proposed in samples:
        grounded, sample_rejected = ground_findings(proposed, diff_index)

        keys_in_sample: set[FindingKey] = set()
        for finding in grounded:
            key = finding_key(finding)
            if key in keys_in_sample:
                # A sample that repeats itself still casts one vote.
                continue
            keys_in_sample.add(key)
            support[key] += 1
            first_seen.setdefault(key, finding)

        for finding in sample_rejected:
            rejected_key = (finding_key(finding), finding.grounding_rejection)
            if rejected_key not in rejected_seen:
                rejected_seen.add(rejected_key)
                rejected.append(finding)

    findings = [
        finding.model_copy(
            update={
                "confidence": support[key] / total,
                "confidence_source": "sample_agreement",
                "sample_support": support[key],
                "sample_count": total,
            }
        )
        for key, finding in first_seen.items()
    ]
    return findings, rejected
