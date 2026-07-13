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
verify whether declared external-state invariants still hold after retry or
checkpoint resume.

Its first concrete target is detecting when a lost result causes a local MCP tool
or durable agent to charge, refund, create, send, update, or delete twice.

## Current state

The repository currently contains the Python package skeleton and its development,
quality, contribution, and CI foundations. It does not yet execute subjects,
inject failures, observe external effects, or produce verdicts.

The first implementation milestone is a deterministic vulnerable-versus-corrected
refund example with a fake local provider. Passing against a harness-controlled
provider will demonstrate the testing method; it will not validate the semantics
of a production payment provider.

## Planned alpha scope

- Trusted local subjects and isolated test state.
- MCP stdio as the first supported integration.
- One logical operation, no more than two attempts, one injected failure, and no
  concurrent schedule exploration.
- Clean-versus-perturbed execution from equivalent seeded worlds.
- Deterministic duplicate delivery and commit-then-lose-result scenarios.
- Declared state surfaces and append-only committed-effect histories.
- Clean functional postconditions separated from retry-safety invariants.
- `PASS`, `FAIL`, `INCONCLUSIVE`, and `ERROR` verdicts with explicit evidence
  limitations.
- Replay from saved concrete inputs and schedules under a compatible environment.
- Experimental property-generated inputs and LangGraph checkpoint/resume support.
- Local and CI execution without LLM calls, paid APIs, or production credentials.

## Scope and safety boundaries

> EffectProbe requires trusted subjects and isolates only declared test state. It
> is not a security sandbox. A passing result applies only to the tested inputs,
> observer coverage, environment, invariants, and failure schedules.

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

The intended passing statement is deliberately narrow:

> No invariant violation was observed within this test's declared inputs,
> evidence surfaces, environment, and supported failure schedule.

## Core terminology

- **Ambiguous outcome:** the caller cannot know whether an external effect
  committed.
- **Logical operation:** the business action intended to happen once.
- **Attempt:** one concrete execution of a logical operation.
- **Delivery:** one transport-level delivery, distinct from the logical operation.
- **Operation key:** the domain idempotency key supplied or observed in arguments.
- **Effect surface:** named external state inspected by an observer.
- **State evidence:** projected state at a point in time.
- **History evidence:** append-only committed effects, required to detect transient
  duplicates that final state alone can hide.
- **Clean trial:** one normal execution.
- **Perturbed trial:** an equivalent initial world plus a supported injected failure
  and recovery.
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
