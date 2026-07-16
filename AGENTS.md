# EffectProbe agent guidance

## Start with project context

- Read `.local/plan.md` when it exists. It is the project owner's private working
  plan; keep exactly one progress item marked `active`.
- Before semantic or claim-related work, read
  `docs/adr/0001-alpha-scope-and-claim-boundaries.md` and
  `docs/claim-boundaries.md`.
- Read `CONTRIBUTING.md` before preparing work intended for a pull request.
- Preserve unrelated user changes and call out any overlap before editing it.

## Preserve the product boundaries

- Keep operation, delivery, attempt, and subject-visible operation-key identities
  distinct.
- Keep clean validity and retry safety as separate axes. Do not emit an
  unqualified overall status.
- Require append-only committed history for at-most-once conclusions; final state
  alone is insufficient.
- Keep harness-only fault knowledge out of subject recovery behavior.
- Do not introduce a public API, schema, dependency, transport, or integration
  unless the approved task explicitly includes it.
- Use bounded language. Do not claim general safety, correctness, idempotency,
  exactly-once behavior, or production-provider validation.

## Follow the gated workflow

- Use `$run-effectprobe-pr` for an active plan item when the skill is available.
- Review a proposed plan against the current code and normative documents before
  implementing it.
- Treat plan approval as a user decision. Do not infer approval from a request to
  review a plan.
- Resolve reviewable plan defects in a bounded AI revision loop and present one
  self-contained `final-plan.md` for user approval.
- Keep issue creation, pushes, pull requests, merges, and other external writes
  behind explicit user authorization.
- Treat approval as authorization only for the external actions named in the final
  plan. Merge and release always require a separate request.
- Record local workflow artifacts under `.local/runs/`; never put secrets there.

## Verify changes

- Run focused tests while iterating.
- Run `scripts/verify.sh` before handing off a completed change.
- Review the final diff against both the approved plan and its merge base.
- Never weaken, delete, or bypass a check merely to make verification pass.
