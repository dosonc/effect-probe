# ADR-0001: Bound alpha claims to observed external effects

- **Status:** Accepted
- **Date:** 2026-07-13

## Context

Ambiguous outcomes are narrower than general agent failure. An external effect can
commit while its result is lost, leaving a caller unable to distinguish success
from failure. A retry or checkpoint resume can then perform the logical operation
again.

Testing this failure requires more than invoking a tool twice or comparing final
snapshots. The harness must introduce ambiguity at a known boundary, distinguish
logical operations from deliveries and attempts, observe declared external-effect
surfaces, and determine whether its evidence is sufficient for each invariant.

Without explicit claim boundaries, a passing test could be misread as proof of
idempotency, exactly-once execution, complete side-effect coverage, production
correctness, or safe execution of untrusted subjects. None of those conclusions is
justified by the planned alpha.

## Decision

### Focus the alpha on external-effect conformance

EffectProbe will target violations of declared external-state invariants after a
supported ambiguous outcome and retry or checkpoint resume, especially duplicate or
otherwise invalid additional effects. It will not position the alpha as a general
agent evaluator, MCP conformance suite, security sandbox, or chaos-engineering
framework.

### Bind every conclusion to its evidence scope

Every evaluative conclusion will identify the subject and environment, concrete
input, logical operation, declared contract, baseline comparison, failure schedule,
observer surfaces, evidence kinds and sources, and known coverage limitations.
Configuration and preflight errors will identify which scope information was
unavailable.

A passing axis means only that every declared invariant on that axis evaluated
`PASS` with sufficient evidence within the recorded scope. It is not an
unconditional property of the subject.

### Compare clean and perturbed executions

Each evaluated input will receive fresh clean and perturbed worlds equivalent on the
declared, canonicalized baseline state surfaces, with recorded baselines for
trial-local history deltas. The clean trial establishes the reference execution.
The perturbed trial injects one supported failure and performs the configured retry
or resume.

Faults will be coordinated through cooperative hooks or barriers at explicit
boundaries. Arbitrary sleeps and timing guesses are not correctness mechanisms.

EffectProbe will keep clean functional validity separate from retry safety. Missing
clean assertions will be reported as unverified rather than allowing equivalence to
an incorrect clean execution to imply correctness.

### Require evidence appropriate to each invariant

Observers will declare named effect surfaces and whether they provide state,
committed-effect history, or both. Every invariant will declare its evidence needs.
Missing required coverage will produce `INCONCLUSIVE`.

Final-state equality is insufficient for at-most-once claims because a transient
duplicate can be created and later removed or collapsed. Those claims require
append-only committed-effect history measured from recorded trial baselines.

### Preserve verdicts, axes, and applicability

Per-invariant outcomes will distinguish `PASS`, `FAIL`, `INCONCLUSIVE`, and `ERROR`.
Clean validity and retry safety will aggregate independently. Clean validity may be
`UNVERIFIED` when no clean assertions were declared, while an inapplicable input is
`NOT_APPLICABLE` rather than an invariant result. EffectProbe will not collapse
these distinctions into an unqualified overall pass.

These names define semantic categories, not frozen Python enums, serialized values,
schemas, or exit codes.

A candidate invariant failure must satisfy a documented confirmation policy in
fresh worlds before it is reported as confirmed. A non-reproducing candidate
remains recorded evidence but cannot become a pass.

### Keep the supported schedule finite

The initial alpha will evaluate one logical operation, at most two attempts, and at
most one injected failure, without concurrent schedule exploration. Unsupported
capability combinations will fail before provisioning instead of being approximated
silently.

Harness-only knowledge may coordinate result loss or termination, but it will not
enter the subject-visible recovery path. Otherwise the harness would change the
behavior it claims to test.

### Treat subjects as trusted local code

Cases provision isolated test state for trusted local subjects; this is not
containment for malicious code. Production credentials, traffic, and shared
environments are outside the alpha scope.

The normative claim language and terminology are maintained in
[`docs/claim-boundaries.md`](../claim-boundaries.md).

## Consequences

### Positive

- Reports can be precise about what was observed and what remains unknown.
- Missing observer coverage cannot silently become a pass.
- Functional correctness and retry safety cannot mask one another.
- Append-only history supports detection of duplicates that snapshots can hide.
- A finite schedule supports deterministic reproduction and useful CI evidence.
- The project has a clear wedge distinct from broad agent evaluation and protocol
  conformance.

### Negative

- Users must provide isolated test state, observers, and explicit contracts.
- A result may be inconclusive even when final state appears correct.
- The alpha cannot make broad claims about production providers or unobserved
  effects.
- General crash schedules, concurrent histories, and malicious-subject containment
  remain unsupported.
- Evidence metadata and report wording are part of correctness, increasing
  implementation and review cost.

## Alternatives considered

### Return only pass or fail

Rejected. A binary result would collapse missing evidence and harness failure into
subject behavior, encouraging false confidence and making infrastructure defects
harder to diagnose.

### Compare final state only

Rejected. Final state can converge after a transient duplicate and cannot justify
at-most-once claims.

### Trust tool annotations or declared idempotency

Rejected. Metadata is evidence about an intended property, not proof of observed
behavior under failure.

### Award a general idempotent or safe badge

Rejected. Results are conditional on inputs, invariants, observers, environment,
and schedules. A future attestation may summarize that bounded evidence but must
link to its exact scope.

### Freeze public extension protocols now

Deferred. The semantic boundaries in this ADR are accepted, but Python factory
names, serialized status values, report schemas, exit codes, fault-point
identifiers, fixture and observer protocols, redaction interfaces, custom
integrations, property-testing mechanics, and durable-runtime adapters remain
experimental until working integrations validate them.

### Expand immediately into concurrency and arbitrary schedules

Deferred. Deterministic single-operation ambiguity is the initial product wedge.
Concurrent histories, model-based schedule exploration, multi-tool transactions,
and compensation semantics are future work driven by validated demand.
