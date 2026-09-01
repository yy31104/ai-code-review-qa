# Real-diff finding labeling guide

Use this protocol before changing a rule. The target metric is **actionable finding precision**: if this finding appeared in a pull request review, would a careful reviewer leave substantially the same comment?

## Unit of judgment

Judge one finding at a time against the code and commit diff linked in that row. Judge the claim the message actually makes, not whether the rule's general idea is useful. A commit being merged upstream does not make every finding false, and a risky-looking line does not make an inaccurate message true.

Use exactly one verdict:

- `true_positive`: the diff introduces or exposes the issue described, the evidence supports it, and the comment asks for an actionable improvement a reviewer could reasonably request.
- `false_positive`: the claim is factually wrong, describes moved or pre-existing behavior as new, misses context that makes the code intentional, or would be review noise rather than an actionable comment.
- `unsure`: the diff and linked commit do not provide enough context, or the judgment needs domain knowledge you do not have. Add a short `note`; unsure rows are reported but excluded from precision.

Do not use severity to decide the verdict. A real low-severity issue is still a true positive; an alarming but incorrect high-severity message is a false positive.

## Anchor-specific checks

For a line-level finding, verify both claims:

1. the message is correct about the anchored line in its surrounding function; and
2. the issue is attributable to this change rather than merely visible in unchanged context.

For `new_function_without_test`, mark true positive only when all of these hold:

1. the change genuinely adds externally meaningful behavior, rather than moving, renaming, re-exporting, overloading, stubbing, or nesting an existing function;
2. the behavior has a meaningful failure or boundary path that should be tested; and
3. the change set does not already exercise that behavior through an existing or modified test.

This is a file-level claim. Do not treat its `evidence` line as proof that the function itself is defective.

## Consistency pass

Label all rows once without changing the rules. Then review rows grouped by `rule_id` and check that the same standard was applied each time. Record borderline reasoning in `note`. Only after saving and scoring this first pass should a rule be changed; once changed using these examples, this corpus is development data and cannot supply the reported held-out result.

Precision from emitted findings does not measure recall. Measuring recall requires adjudicating a sample of cases where the reviewer emitted nothing and recording defects it missed.
