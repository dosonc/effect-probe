# Review contract

Use this contract for both plan review and final diff review. Findings must be
specific, evidence-backed, and ordered by severity.

## Run state

Use this shape for `run-state.json`:

```json
{
  "schema_version": 1,
  "run_id": "local directory name",
  "base_commit": "full commit hash",
  "phase": "plan_review | plan_revision | awaiting_plan_decision | awaiting_plan_approval | implementation | verification | final_review | pr | finished",
  "outcome": null,
  "attempts": {
    "plan_review": 0,
    "verification": 0,
    "final_review": 0
  }
}
```

Update the file at every phase boundary. When `phase` becomes `finished`, set
`outcome` to exactly one of:

- `ready_for_pr`: local implementation, full verification, and final review passed
  but external PR actions were not authorized;
- `pr_opened`: the authorized pull request was opened successfully;
- `blocked`: missing authority, unsafe overlap, or an external prerequisite;
- `iteration_limit`: the configured verification or review retry limit was reached.

Do not use any outcome to imply merge, release, or a broad product-safety
conclusion.

## Final-plan contract

Write `final-plan.md` for human approval. Include:

1. Goal and base commit.
2. Material semantic decisions.
3. Included implementation scope.
4. Evidence and identity contract.
5. Trial lifecycle, failure mapping, confirmation, and aggregation rules.
6. Domain comparison and expected observations.
7. Focused and full validation.
8. Explicit exclusions.
9. Authorization requested by approval: local implementation, issue creation,
   branch, signed-off commit, push, and pull request, each stated separately.

The plan must be self-contained. A reviewer must not need to reconstruct required
behavior from earlier review findings.

## Human approval brief

Present a concise approval brief in the user-facing skill response. Do not require
the user to open JSON artifacts or read the complete plan before deciding. Include:

- **Outcome:** one sentence describing what will exist after implementation.
- **Expected result:** the important behavior or axis outcomes.
- **Key decisions:** at most five bullets covering material semantics and tradeoffs.
- **Scope:** the main implementation components, compressed into at most four
  bullets.
- **Validation:** focused tests, deterministic full checks, and independent review.
- **Not included:** one compact line of the important exclusions.
- **Approval authorizes:** every external action approval would permit.
- **Still requires separate approval:** merge, release, and any other irreversible
  or out-of-scope action.
- **Full plan:** a clickable path to `final-plan.md` for optional inspection.

Keep the brief short enough to scan without scrolling through implementation
detail. Do not hide a material risk, scope expansion, dependency, or external action
for brevity.

When interactive approval controls are available, offer exactly:

1. `Approve and continue` — approve the summarized plan and its listed actions.
2. `Review or change` — inspect the full plan or provide requested changes.

When controls are unavailable, end with those same two textual choices.

## Plan-review criteria

Confirm that the plan:

- advances the single active item in `.local/plan.md`;
- is consistent with the ADR and normative claim boundaries;
- states concrete behavior, scope, exclusions, and acceptance evidence;
- keeps clean validity, retry safety, applicability, and infrastructure errors
  semantically distinct;
- declares evidence requirements and does not infer history from final state;
- preserves operation, delivery, attempt, and subject-visible key boundaries;
- defines fresh-world equivalence, trial-local history baselines, cleanup, and
  failure confirmation where relevant;
- has deterministic tests for success, failure, inconclusive, and error paths
  proportional to the change;
- does not prematurely freeze a public API or artifact schema;
- does not require unapproved dependencies or external writes.

Use this shape for each `plan-review-NN.json`:

```json
{
  "schema_version": 1,
  "verdict": "approve | revise | blocked",
  "base_commit": "full commit hash",
  "blocking_findings": [
    {
      "severity": "high | medium",
      "summary": "short finding",
      "evidence": "file and location or observed repository fact",
      "required_change": "concrete correction"
    }
  ],
  "non_blocking_findings": ["short recommendation"],
  "approved_scope": ["concrete included behavior"],
  "exclusions": ["explicitly deferred behavior"],
  "required_validation": ["command or observable acceptance condition"]
}
```

Use JSON arrays even when empty. Do not add an overall product-safety conclusion.

## Verification record

Use this shape for each `verification-NN.json`:

```json
{
  "schema_version": 1,
  "base_commit": "full commit hash",
  "head_commit_or_worktree": "commit hash or worktree",
  "focused_checks": [
    {"command": "exact command", "status": "pass | fail"}
  ],
  "full_check": {
    "command": "scripts/verify.sh",
    "status": "pass | fail",
    "failed_stage": null
  }
}
```

Record only checks actually run. Set `failed_stage` to the failing command label
when the full check fails.

## Final-review criteria

Review the entire diff against the recorded base, not only the last agent turn.
Prioritize:

1. Incorrect semantic conclusions or claim-boundary violations.
2. Behavior that can pass without sufficient evidence or schedule proof.
3. Fault, cleanup, confirmation, aggregation, or exception paths that lose or
   misclassify evidence.
4. Subject-visible leakage of harness-only identities or fault knowledge.
5. Tests that miss the intended regression or encode an implementation accident.
6. Public API, dependency, serialization, transport, or scope expansion.
7. Unrelated changes, secrets, generated debris, and documentation drift.

Use this shape for each `final-review-NN.json`:

```json
{
  "schema_version": 1,
  "verdict": "pass | changes_required | blocked",
  "base_commit": "full commit hash",
  "findings": [
    {
      "severity": "high | medium | low",
      "summary": "short finding",
      "evidence": "file and location",
      "required_change": "concrete correction or null"
    }
  ],
  "missing_plan_items": ["approved requirement not implemented"],
  "validation_gaps": ["missing or insufficient check"],
  "remaining_risks": ["bounded residual risk"]
}
```

`pass` requires no high- or medium-severity findings, no missing approved plan
items, successful full verification, and no unresolved validation gap.
