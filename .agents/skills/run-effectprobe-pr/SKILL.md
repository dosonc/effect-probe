---
name: run-effectprobe-pr
description: Turn one EffectProbe work item into an AI-reviewed final plan, obtain human approval, implement it, verify and review the diff, and prepare or open an authorized pull request. Use when Codex is asked to execute, continue, or prepare the active item in `.local/plan.md`, revise a proposal into an implementation-ready plan, or run the project plan-to-PR workflow. Do not use for a standalone explanation, narrow code review, or ad hoc fix unrelated to the project plan.
---

# Run an EffectProbe work item

Advance one active work item from proposal through an authorized pull request. Keep
plan refinement and implementation correction inside bounded AI loops. Preserve a
human approval gate on the final plan and a human merge decision.

Read `references/review-contract.md` completely before reviewing a plan or final
diff.

## 1. Establish the run

1. Read `AGENTS.md`, `.local/plan.md`, the files named by the user, the normative
   semantic documents, and the relevant implementation and tests.
2. Inspect the worktree, current branch, merge base, and remote-tracking state.
3. Preserve unrelated changes. Stop and explain any overlap that cannot be handled
   safely.
4. Create an ignored run directory under `.local/runs/`, record the starting
   commit and user request, and initialize `run-state.json` from the review
   contract. Do not store credentials, environment dumps, or secret values.
5. Do not mutate an external system before an approved final plan explicitly
   authorizes the exact action.

Fetching remote references is allowed. Switching branches or creating a local
branch is allowed only when it will not disturb user changes.

## 2. Produce an AI-reviewed final plan

Compare the proposal with the current repository, active direction, ADR, normative
claims, and existing tests. Check scope, evidence sufficiency, failure mapping,
cleanup, confirmation, identity separation, public boundaries, and testability.

Write numbered `plan-review-NN.json` artifacts using the review contract. A plan is
AI-approved only when it has no blocking findings and its scope, exclusions,
authorization, and validation are concrete.

When a review returns `revise`, resolve every finding that can be answered from the
repository and user request, write or update `final-plan.md`, then perform a fresh
read-only review. Do not interrupt the user for mechanical corrections. Allow at
most two AI revision cycles after the initial review.

Stop early only when a finding requires a genuine product choice, expanded scope,
new dependency, incompatible requirements, or new authority. Record the question,
set the phase to `awaiting_plan_decision`, and ask the user for that decision.

When the review returns `approve`, set the phase to `awaiting_plan_approval` and
present the approval brief defined in the review contract. Link `final-plan.md` for
optional drill-down and keep JSON reviews as audit evidence. Do not interpret the
original invocation as approval of a plan that had not yet been produced and
reviewed.

## 3. Apply the human approval gate

Use interactive approval controls when the active surface provides them. Offer
exactly two actions: `Approve and continue` and `Review or change`. Otherwise ask
for the same two choices in plain text. The user should be able to decide from the
approval brief without opening the full plan.

Approval authorizes implementation plus only the issue, commit, push, or
pull-request actions shown in the brief and listed in the final plan's authorization
section. It never authorizes merge or release.

If the user requests plan changes, return to the plan revision loop and re-review
the complete plan before asking again. Do not begin implementation until the final
plan is explicitly approved.

## 4. Prepare and implement the work

1. Recheck the starting commit, remote base, and worktree before editing.
2. If required by `CONTRIBUTING.md` and authorized, create the issue before
   substantial implementation.
3. Create or switch to the approved local branch without disturbing user changes.
4. Implement only the approved scope. Keep deferred items deferred.
5. Add or update focused tests alongside the behavior they establish.
6. Run focused formatting, linting, typing, and tests while iterating.
7. Record material deviations from the approved plan. Stop for user direction if a
   deviation changes semantics, scope, dependencies, public interfaces, or external
   state.

Do not update the active work item to `done` before its required completion event,
such as a merge, has actually occurred.

## 5. Verify and review in a bounded loop

Run `scripts/verify.sh` from the repository. Treat command exit codes as the source
of truth. Do not summarize a check as passing unless it completed successfully.

If verification fails, diagnose the failure, make the narrowest in-scope fix, and
rerun the affected focused check followed by `scripts/verify.sh`. Never weaken a
check or test to obtain a pass. Stop after two full fix-and-verify cycles if the
same blocking condition remains.

Write `verification-NN.json` using the review contract. Preserve concise failure
details and command names, not full environment dumps. Update the attempt counter
and current phase in `run-state.json`.

Perform a fresh read-only review against the recorded base commit. Use a dedicated
Codex review when available; otherwise start a distinct review pass and do not edit
files during it.

Evaluate the diff against the approved plan and the final-review criteria in the
review contract. Write `final-review-NN.json` using its schema.

If the verdict is `changes_required`, return only the blocking findings to the
implementation pass, fix them, rerun full verification, and repeat the independent
review once. Stop for user direction if blocking findings remain after two review
cycles. Record the review attempt and terminal outcome in `run-state.json`.

## 6. Prepare or open the pull request

When verification and final review pass:

1. Update documentation and `.local/plan.md` as required by the approved plan.
2. If commit, push, and pull-request creation were authorized, create a signed-off
   commit, push the approved branch, and open the pull request with validation and
   risk evidence. Never merge it.
3. Otherwise record `ready_for_pr` and hand the local change back to the user.

CI remains authoritative after a pull request opens. Report failures; do not widen
the approved scope while fixing them.

## 7. Hand off

Report:

- the implemented outcome;
- changed files;
- focused and full verification results;
- final-review verdict and remaining risks;
- any issue, branch, commit, and pull-request references;
- the run-artifact location and external actions intentionally not performed.

Never merge or release without a separate explicit user request.
