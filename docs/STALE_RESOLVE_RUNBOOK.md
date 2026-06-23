# Stale Resolve Runbook

## Purpose

This runbook describes a safe manual verification procedure for the opt-in stale inline review-thread resolve path. It is for a throwaway same-repository PR only.

This is manual verification, not an automated mutating CI canary. Do not run it on a shared, high-signal, or real production PR.

## Safety Boundaries

- `AI_REVIEW_STALE_ACTION` is not enabled by default.
- Enable `AI_REVIEW_STALE_ACTION=true` only for the short canary window, then delete it or set it back to `false` immediately.
- Fork PRs are skipped by `.github/workflows/pr-summary.yml`.
- The resolve path must never delete, edit, reply to, patch, or minimize comments.
- The only allowed mutation is GraphQL `resolveReviewThread`.
- A valid stale action must be bot-authored by `github-actions[bot]`, marker-owned, unresolved, found in a review thread, and absent from the current finding fingerprints.
- If no eligible stale thread exists, the expected `resolve-apply.json` result is an empty action set:

```json
{
  "thread_node_ids": [],
  "count": 0
}
```

## Prerequisites

1. Use a throwaway branch and same-repository PR targeting `main`.
2. Keep `AI_REVIEW_SUMMARY_AUTOPOST=true` and `AI_REVIEW_INLINE_COMMENTS=true` scoped to the canary only if they are needed to create or refresh bot-owned marker inline comments.
3. Confirm `.github/workflows/pr-summary.yml` still uses `pull_request`, not `pull_request_target`.
4. Confirm the target PR is same-repo, not a fork.

## Manual Verification Procedure

1. Create a throwaway same-repository PR with a change that produces at least one eligible inline finding.
2. Let the PR summary workflow post the summary and inline review comments, or observe an existing bot-owned inline thread that contains this project's hidden AI finding marker.
3. Push a follow-up commit to the same throwaway PR that removes or changes the finding so the previous inline comment becomes stale.
4. Temporarily create or set the repository Actions variable:

```text
AI_REVIEW_STALE_ACTION=true
```

5. Rerun the PR summary workflow for the throwaway PR.
6. Verify the workflow jobs:

```text
build: success
post: success, skipped, or not applicable to the canary setup
post-inline: success when inline posting is enabled
resolve-stale: success
```

7. Download the `pr-stale-resolve` artifact.
8. Confirm the artifact includes at least:

```text
review-threads.json
stale-action-plan.json
resolve-apply.json
```

9. Inspect `stale-action-plan.json` and confirm every eligible action satisfies all of these:

- `eligible` is `true`
- `reason` is `eligible`
- `author` is `github-actions[bot]`
- `marker_present` is `true`
- `is_resolved` is `false`
- `thread_node_id` is a non-empty string

10. Inspect `resolve-apply.json`.

- If `count` is `0`, no mutation should have been attempted.
- If `count` is greater than `0`, each listed `thread_node_id` must correspond to an eligible action in `stale-action-plan.json`.

11. Verify one real `resolveReviewThread` result only when a valid stale eligible thread exists. The expected outcome is that the stale review thread becomes resolved; no comment should be deleted, edited, replied to, patched, or minimized.
12. Immediately delete the repository variable or set it back to false:

```text
AI_REVIEW_STALE_ACTION=false
```

13. Close the throwaway PR after recording the artifact outcome.

## Interpreting Results

| Result | Meaning | Action |
| --- | --- | --- |
| `resolve-stale` is skipped | `AI_REVIEW_STALE_ACTION` was not true, or the PR/job gate did not match | Expected for default-off behavior |
| `resolve-stale` succeeds with `count: 0` | Runtime path worked, but no eligible stale thread existed | Safe canary result |
| `resolve-stale` succeeds with `count > 0` | One or more eligible stale threads were selected and resolve calls were attempted | Verify the resolved thread state and audit artifact eligibility |
| Per-thread warning appears | A selected thread failed to resolve | Investigate the warning; the job should continue |
| Any delete/edit/reply/minimize behavior appears | Safety invariant is broken | Stop, disable `AI_REVIEW_STALE_ACTION`, and fix before further testing |

## Known Limitation

The workflow paginates the outer `reviewThreads` connection in the execute path, but each thread's inner `comments(first: 100)` connection is not paginated. This is intentionally safe in failure mode: an eligible stale comment beyond the first 100 comments in a single thread may be missed, producing under-resolve behavior instead of resolving an uncertain thread.
