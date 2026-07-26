# Experimental Python cases

EffectProbe includes an explicitly unstable Python contract for running one trusted
local case without importing private `effectprobe._*` modules or editing the
package. It is the first bring-your-own-case path, not a stable plugin API.

The caller runs its own Python module and passes a case object to
`effectprobe.experimental.run_case`. EffectProbe does not dynamically import a
configured file, discover entry points, execute a user-selected command, or create
a security boundary around the code.

## Supported envelope

One experimental case contains:

- one concrete input and one harness logical-operation identity;
- an optional operation key selected from data already visible to the subject;
- one named external-effect surface with declared state and history coverage;
- a fresh provision/cleanup session for every clean or perturbed trial;
- zero or more clean assertions and at least one required retry invariant;
- one applicability condition and one preflight callback;
- one cooperative provider-commit/result-loss boundary followed by one retry; and
- no concurrency, no more than two perturbed invocations, and at most one fault.

The observer can declare state, ordered history, or both. An invariant that
requires at-most-once evidence must require `COMPLETE_HISTORY`. The history must
be complete and append-only for the declared surface and observation interval.
EffectProbe measures each trial from its own recorded baseline and rejects a
history that removes or rewrites that prefix.

Multiple observer surfaces, other schedules, generated inputs, generic MCP
configuration, dynamic case loading, evidence artifacts, replay, and stable report
schemas are not supported by this interface.

## Run the complete example

The repository includes a caller-owned JSON Lines refund case at
[`examples/experimental_refund_case.py`](../examples/experimental_refund_case.py).
It imports only `effectprobe.experimental`. Each trial creates a disposable
journal, derives current state from its complete event history, and removes the
journal during cleanup.

Run the keyed subject:

```bash
uv run --locked python examples/experimental_refund_case.py --mode keyed
```

Its expected axes are:

```text
clean_validity: PASS
retry_safety: PASS
```

Run the unsafe subject:

```bash
uv run --locked python examples/experimental_refund_case.py --mode unsafe
```

Its expected axes are:

```text
clean_validity: PASS
retry_safety: FAIL
```

The unsafe contradiction is evaluated again in exactly two fresh confirmation
pairs before the retry invariant becomes `FAIL`. Both example commands exit zero
when evaluation and rendering complete. The process status is not an overall
subject verdict.

## Attachment shape

A caller constructs a `Case` and runs it directly:

```python
from pathlib import Path

from effectprobe.experimental import Canonicalization, Case, render_terminal, run_case

case = Case(
    case_name="my_local_case",
    subject_name="my_trusted_agent",
    input=my_input,
    operation_id="operation/example-001",
    operation_key_selector=lambda value: value.operation_key,
    canonicalize_input=canonicalize_input,
    canonicalization=Canonicalization(
        input="example_input/v1",
        state="example_state/v1",
        event="example_event/v1",
    ),
    source_files=(Path(__file__),),
    dependency_lock=Path("uv.lock"),
    world_factory=world_factory,
    coverage=coverage,
    canonicalize_state=canonicalize_state,
    canonicalize_event=canonicalize_event,
    clean_assertions=clean_assertions,
    retry_invariants=retry_invariants,
    limitations=("single_surface_only",),
)

report = run_case(case)
print(render_terminal(report), end="")
```

This is a library attachment. There is intentionally no
`effectprobe run path.py:case` command.

### World lifecycle

`world_factory(trial_id)` returns a `WorldSession` with:

- `provision()`, which creates isolated state and returns a `World`; and
- `cleanup(world_or_none)`, which releases resources.

The session is new for every primary or confirmation clean/perturbed trial.
Cleanup is attempted once after successful provision. If provision raises after
the session exists, cleanup is attempted once with `None`.

The provisioned `World` supplies:

- `invoke(input, deliver_result)`, which invokes the trusted subject adapter;
- `observe()`, which returns one `Observation(state, history)`; and
- `validate_fixture(observation)`, which checks fresh-world assumptions separately
  from subject correctness.

Provisioning, fixture, subject, observer, evaluator, controller, history, or cleanup
malfunction is infrastructure `ERROR`; it is not a failed subject invariant.

### Cooperative fault boundary

The subject adapter must call `deliver_result(result)` only after the declared
external effect has committed and its result is ready:

```python
def invoke(input_value, deliver_result):
    receipt = provider.commit(input_value)
    return deliver_result(receipt)
```

The clean trial uses an ordinary delivery. In the perturbed trial, EffectProbe
raises a harness-only control signal at the first callback crossing, observes the
committed effect, and invokes the whole subject adapter once more with the same
concrete input. The adapter must not catch that control signal.

If the armed callback is never reached, retry safety is `INCONCLUSIVE`. If the
adapter catches the signal, the controller path is `ERROR`. EffectProbe does not
insert the fault state, logical-operation identity, delivery identity, attempt
identity, or operation key into subject recovery input.

### Observers and contracts

Clean assertions receive the concrete input, baseline and final observation,
trial-local history delta, and returned result. Retry invariants additionally
receive clean and perturbed evidence, canonical history deltas, attempt count and
outcomes, boundary proof, and the final subject result.

Each evaluator returns `Decision(passed, reason_code)`. Names, reason codes,
provenance, intervals, and limitations must match
`[a-z][a-z0-9_.:/-]{0,127}`. Each bounded token list contains at most 32 unique
values. Free-form values are not accepted into the runtime report. A false clean
decision is a clean contract `FAIL`. A false retry decision is provisional until
two valid fresh confirmations reproduce it.

The three `Canonicalization` identifiers describe the input, state, and event
projection rules used by the supplied callables. The immutable report retains
those identifiers plus inspectable clean/retry contract names and evidence
requirements; their combined descriptor is also included in the scope fingerprint.

Missing declared evidence maps the affected contract to `INCONCLUSIVE`; its
evaluator does not run. A false applicability decision provisions no world and
produces zero applicable pairs. Retry safety is then `INCONCLUSIVE`; clean validity
is `UNVERIFIED` without clean assertions and `INCONCLUSIVE` otherwise.

## Scope fingerprints and redaction

Before preflight or provisioning, the runner fingerprints:

- the contents of caller-selected source files;
- one caller-selected dependency lock file;
- the canonical concrete input and selected operation key;
- contract and observer declarations;
- the fixed schedule and boundary; and
- the EffectProbe and Python runtime plus bounded platform fields.

A case selects between one and 32 regular, non-symlink source files plus one
regular, non-symlink dependency lock. Each file is limited to 16 MiB. The file
contents are hashed without returning their paths.

The returned report contains those digests, separate identity and axis summaries,
fault proof, cleanup outcomes, fixed error categories, and tokenized limitations.
It does not contain raw input, operation keys, state, history, results, paths,
exception messages, tracebacks, subprocess output, or arbitrary object
representations.

For each trial, the report retains the baseline history count and the observed
history count after each attempt. The keyed example therefore exposes `0/1/1` for
its primary perturbed trial, while the unsafe example exposes `0/1/2`. These counts
make the bounded observation visible without persisting raw event values; they do
not replace the declared complete append-only history used by the invariant.

Content digests are compatibility identifiers, not encryption, signatures,
provenance attestations, or protection for low-entropy secrets. Use only disposable
test state and non-production inputs. Do not share a report without treating its
fingerprints as potentially sensitive local test data.

Only the selected source files and lock are covered. Dynamically imported code and
undeclared host state remain limitations. A later run with any changed descriptor
is a new evaluation. Experimental reports are in-memory projections: they are not
artifacts and cannot be strictly replayed or called verified reproductions.

## Interpret the result narrowly

`clean_validity` and `retry_safety` are independent. There is no overall status.
A passing retry axis applies only to the recorded case, concrete input fingerprint,
subject and source fingerprint, dependency lock, runtime, contract, observer
surface, and provider-result-loss schedule.

The example uses a trusted local file journal and does not validate a production
payment provider, filesystem crash durability, unobserved effects, general agent
correctness, idempotency, or exactly-once execution. Read
[Alpha claim boundaries](claim-boundaries.md) and
[Current limitations](limitations.md) before interpreting or sharing a result.
