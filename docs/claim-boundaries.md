# Alpha claim boundaries

This document defines what an EffectProbe alpha result is allowed to mean. It is a
normative design contract for implementation and report wording, not documentation
of already-available functionality.

Uppercase status names in this document describe semantic categories. Python enums,
JSON values, schemas, artifact fields, and CLI exit codes remain experimental.

## Product claim

EffectProbe's intended alpha claim is:

> Under a declared observer set and supported deterministic failure schedule,
> execute recorded clean and perturbed trials and evaluate whether the declared
> external-state invariants hold across retry or checkpoint resume.

The initial target is a local MCP tool or durable agent whose external effect may
commit even though its result is lost. EffectProbe compares one clean execution
with a perturbed execution matched on declared, canonicalized baseline surfaces, in
which that ambiguity is introduced and the logical operation is retried or resumed.

EffectProbe finds bounded evidence for or against declared invariants. It does not
prevent effects, make a subject idempotent, or prove exactly-once execution.

## Scope of every conclusion

Every evaluative conclusion records all applicable parts of the following scope:

- The exact recorded subject, code, dependency lock, runtime, and environment
  fingerprint.
- The concrete input and optional user-configured domain operation key.
- The declared clean postconditions and retry-safety invariants.
- The initial-world seed; equivalence on declared, canonicalized baseline state
  surfaces; and the baseline from which trial-local history deltas are measured.
- The exact supported failure schedule and recovery behavior.
- The named external-effect surfaces observed by the harness.
- The evidence kinds, sources, observation interval, coverage limitations, and
  canonicalization rules.
- The runner and evidence-schema versions.

Changing one of these can change the result. Unobserved surfaces remain unknown.
Configuration and preflight errors identify which scope information was unavailable;
they do not pretend that an evaluative trial occurred.

## Alpha execution envelope

The initial alpha execution envelope is intentionally finite:

- One logical operation.
- No more than two concrete attempts.
- No more than one injected failure.
- No concurrent schedule exploration.
- Trusted local subjects operating on case-provisioned isolated test state.
- MCP stdio as the first supported integration.

Each applicable input and scenario pair provisions fresh clean and perturbed worlds.
The harness compares only their declared, canonicalized baseline state surfaces and
records separate baselines for trial-local history deltas.

The planned supported schedules are:

- One clean reference call without an injected failure.
- Duplicate delivery of the same logical operation.
- Provider commit followed by provider-result loss and retry.
- MCP tool completion followed by client-result loss and retry.

Effect commit, process termination, and checkpoint resume is an experimental
schedule outside the initial supported set.

Deterministic means that the harness coordinates a cooperative hook or barrier at
the declared boundary between effect commit and result delivery or persistence. It
does not use arbitrary sleeps or timing guesses as a correctness mechanism, and it
does not imply coverage of every real-world failure.

A requested schedule that the subject and harness cannot execute faithfully is a
preflight `ERROR`. EffectProbe must not silently substitute an approximation.

## Identity boundaries

Reports and internal evidence must keep these concepts distinct:

- **Logical operation identity:** harness identity stable across the intended
  business action.
- **Operation key:** an optional domain idempotency key explicitly selected by user
  configuration from subject-visible data and recorded by EffectProbe.
- **Delivery identity:** identity supplied by a transport or delivery mechanism,
  whose redelivery semantics must be recorded rather than assumed.
- **Attempt identity:** unique identity for each concrete subject invocation.

An MCP JSON-RPC request identifier is not automatically any of these. EffectProbe
can test a subject with no operation key by retaining its harness-level logical
operation identity. It must never silently add, rewrite, or disclose a domain key to
recovery logic that would not otherwise receive it.

## Applicability, verdicts, and axes

Configuration and preflight failures produce a report-level `ERROR` before trial
axes exist. Once evaluative execution begins, EffectProbe keeps three semantic
layers separate.

### Applicability

An input applicability condition determines whether a generated or concrete input
belongs to the case domain. When it is false, the trial is `NOT_APPLICABLE`; this is
a disposition, not an invariant verdict.

A fixture validity condition checks harness assumptions such as required seeded
records or an empty effect-history baseline. When it is false, the result is an
`ERROR`. Clean functional assertions evaluate the subject's ordinary result and
post-execution state; they are neither applicability nor fixture conditions.

A run with zero applicable input and scenario pairs cannot pass. Retry safety is
`INCONCLUSIVE`. Clean validity is `UNVERIFIED` when no clean assertions were
declared; otherwise it is `INCONCLUSIVE` because no clean trial was evaluated.

### Invariant verdicts

Each evaluated invariant returns one of four verdicts:

- **`PASS`:** the invariant held and its declared evidence requirements were met.
- **`FAIL`:** sufficient evidence demonstrated an invariant violation and the
  confirmation policy succeeded.
- **`INCONCLUSIVE`:** the available evidence could not justify pass or fail.
- **`ERROR`:** the fixture, observer, subject driver, failure controller, invariant
  evaluator, or configuration malfunctioned.

A contradiction observed in one trial is a provisional failure candidate, not a
final invariant verdict.

### Axis statuses

Invariant verdicts aggregate independently into two axes:

- **`clean_validity`:** whether one ordinary execution satisfied the declared
  functional contract.
- **`retry_safety`:** whether the perturbed execution satisfied the declared retry
  invariants.

Each axis may be `PASS`, `FAIL`, `INCONCLUSIVE`, or `ERROR`. `clean_validity` may
also be `UNVERIFIED` when no clean assertions were declared. `UNVERIFIED` is not an
invariant verdict and does not imply that the clean execution was correct.

An empty retry contract is a configuration `ERROR`; retry safety is EffectProbe's
core purpose.

Every invariant declared in an alpha case is required. Optional or informational
invariants are not supported in the alpha.

Within each axis, aggregation uses this precedence:

1. A confirmed `FAIL` remains a failure.
2. Otherwise, an `ERROR` prevents a valid conclusion.
3. Otherwise, an `INCONCLUSIVE` prevents a passing conclusion.
4. Only then may that axis be `PASS`.

An unrelated error or evidence gap must not erase a conclusive violation already
supported and confirmed by sufficient evidence. EffectProbe does not collapse both
axes into an unqualified overall `PASS`.

| Clean validity | Retry safety | Meaning |
|---|---|---|
| `PASS` | `FAIL` | Normal behavior satisfied its contract, but the tested retry path did not. |
| `FAIL` | `PASS` | Retry invariants passed despite an incorrect clean execution. |
| `UNVERIFIED` | `PASS` | Only retry safety was evaluated; clean correctness is unknown. |
| `PASS` | `INCONCLUSIVE` | Clean behavior passed, but retry safety could not be established. |

## Clean validity

The clean contract may cover expected state transition, expected result, and
consistency between the returned result and observed external state.

A clean execution that refunds nothing, refunds the wrong amount, or performs the
same incorrect duplicate effect as the perturbed execution must not be described as
functionally correct. Retry equivalence cannot turn an already-wrong clean
execution into a valid one.

## Retry safety

Retry invariants evaluate observed external state and committed-effect history
after a supported failure and retry or resume. Typical invariants compare the
perturbed execution with one clean execution and require no more than one committed
effect for the logical operation.

`retry_safety=PASS` requires evidence that the selected schedule executed as
declared. When the schedule arms a fault, the evidence must show that the fault point
was reached and the fault was injected. Otherwise retry safety is `INCONCLUSIVE`,
not `PASS`.

## Failure confirmation

An observed invariant contradiction is a provisional failure candidate. The planned
alpha confirmation policy reruns the same concrete input and schedule twice in newly
provisioned worlds and records every outcome.

- If both valid confirmation trials reproduce the violation with sufficient
  evidence, the final invariant verdict is `FAIL`.
- If a valid confirmation trial does not reproduce it, the final invariant verdict
  is `INCONCLUSIVE` with a determinism limitation.
- If confirmation infrastructure malfunctions, the final invariant verdict is
  `ERROR`.

The candidate evidence is always preserved. Unless another confirmed failure
determines the axis, its final status follows the invariant verdict above. A
non-reproducing candidate must never be converted to `PASS`.

## Condition mapping

| Condition | Required disposition |
|---|---|
| Requested capabilities are unsupported during preflight | report-level `ERROR`; no axes |
| Baseline comparison fails | retry safety `ERROR`; preserve completed clean axis |
| Input applicability condition is false | `NOT_APPLICABLE` |
| Fixture validity condition is false | `ERROR` |
| Armed fault point is never reached | affected retry invariant `INCONCLUSIVE` |
| Required surface or evidence kind is missing | affected invariant `INCONCLUSIVE` |
| Fixture, observer, driver, controller, or evaluator malfunctions | `ERROR` |
| Evidence contradicts an invariant | provisional candidate; apply confirmation policy |
| Zero applicable input and scenario pairs | retry safety `INCONCLUSIVE`; clean per rule above |

## Evidence requirements

Every invariant names the evidence it requires. Observer coverage is declared per
named effect surface, evidence kind, observation interval, and invariant.

- **State evidence** is a projected snapshot at a point in time.
- **History evidence** is an append-only record of committed effects.

Final-state equality cannot establish that a transient duplicate never occurred.
An at-most-once claim requires append-only committed-effect history that is complete
for the relevant surface and observation interval. It evaluates trial-local history
deltas from recorded baselines so fixture-seeding events are not counted as subject
effects. A final snapshot alone is insufficient.

Evidence provenance and limitations must be recorded. Provenance can describe a
harness-controlled model, a real local dependency, recorded dependency behavior, or
a controlled external test dependency; it never denotes a production target in the
alpha. A provenance label describes origin, not trust or completeness. Passing
against a fake or harness-controlled provider says nothing about an untested
production provider's semantics.

Harness-only knowledge may trigger result loss or process termination, but it must
not enter subject-visible recovery decisions. Otherwise the harness would change
the behavior it claims to test.

## Replay boundary

Verified replay requires an explicitly replayable artifact and compatibility with
the recorded subject, contract, fixture, observer, dependency lock, runtime, and
evidence schema. Execution under detected drift is a new trial, not a verified
reproduction of the original result.

## Permitted report language

A passing retry-safety result should use bounded language such as:

> `retry_safety=PASS`: Every declared retry invariant evaluated `PASS` for all `<N>`
> applicable recorded input and scenario pairs, using evidence declared sufficient
> for each invariant. This conclusion is limited to the recorded subject, contract,
> inputs, environment, observer coverage, and failure schedules.

The report replaces `<N>` with the concrete number of evaluated pairs.

When no clean assertions were declared, append:

> `clean_validity=UNVERIFIED`: No clean functional assertions were declared. The
> retry-safety result does not establish that one ordinary execution performs the
> intended operation correctly.

A failure may identify the invariant, input reference, schedule, observed surface,
committed-effect evidence, and confirmation outcome. An inconclusive result must
name the missing surface, evidence kind, applicability, or unreached boundary. An
error must identify the harness or configuration failure rather than presenting it
as subject behavior.

## Prohibited claims

EffectProbe documentation, reports, badges, and release material must not:

- Emit an unqualified overall `PASS` for the subject.
- Claim clean functional correctness when `clean_validity=UNVERIFIED`.
- Claim no duplicate effects or at-most-once behavior from final state alone.
- Draw a retry-safety conclusion when the selected fault boundary was not reached.
- Claim that all external effects were observed.
- Claim to prevent duplicate effects or make a subject idempotent.
- Guarantee exactly-once execution.
- Describe a tool or agent as generally safe, correct, or read-only.
- Validate production semantics by passing against a fake dependency.
- Call execution under detected environment drift a verified reproduction.
- Generalize across untested inputs, source or dependency changes, failure schedules,
  partitions, concurrency, environments, or malicious subjects.
- Present a result as MCP conformance, security certification, or general agent
  evaluation.

EffectProbe must not issue an unconditional "safe" badge. Any future attestation
must link to the precise contract, inputs, observer coverage, environment, and
failure profile that support it.

## Security boundary

EffectProbe executes trusted local test code against case-provisioned test state. It
is not a security sandbox. A subject designed to escape its fixture or access the
invoking user's resources is outside the alpha threat model.

EffectProbe itself remains responsible for avoiding command and environment
injection, path-boundary violations, unsafe persistence, and secret leakage through
artifacts, subprocess output, exceptions, logs, and reports.
