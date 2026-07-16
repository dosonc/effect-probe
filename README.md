# EffectProbe

Deterministic external-effect testing for retryable tools and durable agents.

> **Status: pre-alpha.** EffectProbe is under active development. There is no
> usable fault-injection API or CLI yet. The public API, evidence schema, and
> compatibility guarantees are not stable, and the capabilities described as
> planned below are targets for the first alpha rather than current features.

A timeout does not prove that a tool did nothing. An external effect may commit
successfully while its result is lost, causing retry or checkpoint resume to
perform it again.

EffectProbe aims to reproduce supported ambiguous outcomes deterministically and
evaluate declared external-state invariants in recorded clean and perturbed trials
across retry or checkpoint resume.

Its first concrete target is detecting when a lost result causes a local MCP tool
or durable agent to charge, refund, create, send, update, or delete twice.

## Current state

The repository now contains a private, test-driven semantic slice that evaluates
fresh clean and perturbed refund worlds. Its vulnerable subject ignores a selected
operation key and produces `clean_validity=PASS`, `retry_safety=FAIL`; its corrected
subject forwards that key to a deduplicating fake provider and produces
`clean_validity=PASS`, `retry_safety=PASS`. The comparison records current state,
append-only history, and distinct operation, trial, delivery, attempt, and subject
key identities.

This kernel is deliberately private and its provisional scope is not reportable:
environment, code, and version fingerprints are deferred. The examples demonstrate
the testing semantics against a harness-controlled provider; they do not validate
the behavior of a production payment provider or expose a usable fault-injection
API, CLI, or stable verdict/report schema.

## Design documentation

- [Alpha claim boundaries](docs/claim-boundaries.md) defines the permitted meaning
  of verdicts and the evidence scope required for every evaluative conclusion.
- [ADR-0001](docs/adr/0001-alpha-scope-and-claim-boundaries.md) records why the alpha
  deliberately adopts those boundaries.

## Planned alpha scope

- Trusted local subjects and case-provisioned isolated test state.
- MCP stdio as the first supported integration.
- One logical operation, no more than two attempts, one injected failure, and no
  concurrent schedule exploration.
- Clean-versus-perturbed execution matched on declared, canonicalized baseline
  surfaces.
- Deterministic duplicate delivery and commit-then-lose-result scenarios.
- Declared state surfaces and append-only committed-effect histories.
- Clean functional postconditions separated from retry-safety invariants.
- `PASS`, `FAIL`, `INCONCLUSIVE`, and `ERROR` invariant verdicts, plus explicit
  `UNVERIFIED` clean validity when no clean assertions were declared.
- Strict replay from explicitly replayable artifacts under a compatible environment.
- Experimental property-generated inputs and LangGraph checkpoint/resume support.
- Local and CI execution without LLM calls, paid APIs, or production credentials.

## Scope and safety boundaries

> EffectProbe requires trusted subjects and operates on case-provisioned test state.
> It is not a security sandbox. An evaluative conclusion applies only to the
> recorded inputs, observer coverage, environment, invariants, and failure
> schedules.

EffectProbe does not aim to:

- Execute against production systems, credentials, traffic, or shared environments.
- Contain malicious or untrusted code.
- Evaluate prompts, model quality, tool selection, or general agent behavior.
- Automatically discover every external side effect.
- Provide general chaos engineering, arbitrary crash schedules, network partitions,
  model checking, or concurrent schedule exploration.
- Test MCP protocol conformance.
- Prove exactly-once execution, guarantee idempotency, or award an unconditional
  "safe" badge.
- Claim that fake dependencies reproduce production semantics.
- Provide stable third-party extension compatibility during `0.x`.

The intended passing statement is deliberately scoped and axis-specific:

> `retry_safety=PASS`: Every declared retry invariant evaluated `PASS` for the
> applicable recorded input and scenario pairs, using evidence declared sufficient
> for each invariant. This conclusion is limited to the recorded subject, contract,
> inputs, environment, observer coverage, and failure schedules.

## Core terminology

- **Ambiguous outcome:** the caller cannot know whether an external effect
  committed.
- **Logical operation:** the business action intended to happen once.
- **Attempt:** one concrete execution of a logical operation.
- **Delivery:** one transport-level delivery, distinct from the logical operation.
- **Operation key:** an optional domain idempotency key explicitly selected from
  subject-visible data by user configuration and recorded, but never injected, by
  EffectProbe.
- **Effect surface:** named external state inspected by an observer.
- **State evidence:** projected state at a point in time.
- **History evidence:** append-only committed effects, required to detect transient
  duplicates that final state alone can hide.
- **Clean trial:** one normal execution.
- **Perturbed trial:** a fresh world matched to the clean trial on declared,
  canonicalized baseline surfaces, plus a supported injected failure and recovery.
- **Evidence sufficiency:** whether configured observers can justify a particular
  invariant.
- **Harness truth:** information known by the test harness but unavailable to the
  subject's recovery logic.

## Development

EffectProbe currently targets Python 3.12 on Linux and uses
[uv](https://docs.astral.sh/uv/) for project management.

```bash
uv sync --locked
uv run --locked pre-commit install
uv run --locked pre-commit run --all-files
uv run --locked pyright
uv run --locked pytest
uv build --no-sources
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

EffectProbe is licensed under the [Apache License 2.0](LICENSE).
