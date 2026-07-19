# Controlled MCP tutorial

This tutorial runs EffectProbe's one installed, user-operable case: a trusted local
MCP refund subject backed by a harness-controlled SQLite provider. You will compare
an unsafe subject with one that forwards a selected operation key, render recorded
evidence without re-executing the subject, and perform one compatible replay.

The example demonstrates behavior under one recorded input, observer, environment,
contract, and deterministic client-result-loss schedule. It does not validate a
production payment provider or prove general idempotency or exactly-once execution.
Read [Limitations](limitations.md) before adapting its conclusions.

## Prerequisites

- Linux and Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- A local checkout of this repository

From the repository root, install the locked environment:

```bash
uv sync --locked
```

Create an isolated destination for tutorial output. `mktemp` prints the directory
path; retain it if you want to inspect or remove the files later.

```bash
tutorial_dir="$(mktemp -d)"
printf '%s\n' "$tutorial_dir"
```

Every artifact and report destination below must be absent before the command runs.
EffectProbe refuses to overwrite an existing path.

## 1. Reproduce the duplicate refund

Run the bundled unsafe subject and record its private evidence artifact:

```bash
uv run --locked effectprobe run controlled-mcp-refund \
  --mode unsafe \
  --artifact "$tutorial_dir/unsafe.json"
```

The command performs a clean reference trial and a fresh perturbed trial. In the
perturbed trial, the provider commits the first refund, EffectProbe loses the
validated MCP client result at its cooperative boundary, and the subject retries
the same logical operation. The unsafe subject ignores the selected operation key,
so the append-only provider history records two committed refunds.

The expected axes are:

| Axis | Expected status | Meaning in this case |
| --- | --- | --- |
| `clean_validity` | `PASS` | One ordinary execution satisfied the bundled clean contract. |
| `retry_safety` | `FAIL` | The duplicate committed effects violated the declared retry invariant and reproduced in two fresh confirmation trials. |

The process exits `0` because evidence capture and report rendering completed. Exit
`0` is not an overall pass and does not hide the retry-safety failure.

## 2. Compare the keyed subject

Run the same case with the subject forwarding its selected operation key:

```bash
uv run --locked effectprobe run controlled-mcp-refund \
  --mode keyed \
  --artifact "$tutorial_dir/keyed.json"
```

The perturbed path still makes two concrete attempts after losing the first result.
The harness-controlled provider recognizes the repeated key and its append-only
history records one trial-local committed refund. The expected axes are
`clean_validity=PASS` and `retry_safety=PASS`.

That passing retry axis is limited to this bundled subject, input, fake provider,
observer coverage, and supported schedule. It is not a general label for the
subject and says nothing about an untested production provider.

## 3. Render evidence without execution

`report` validates and projects an eligible artifact without starting the subject,
provisioning a world, or replaying the case. Render the unsafe artifact as canonical
JSON on stdout:

```bash
uv run --locked effectprobe report "$tutorial_dir/unsafe.json" --format json
```

Or write the keyed result as a new JUnit file:

```bash
uv run --locked effectprobe report "$tutorial_dir/keyed.json" \
  --format junit \
  --output "$tutorial_dir/keyed-report.xml"
```

`--output -` is the default and emits exactly the selected report on stdout. With a
file destination, stdout is empty. Terminal wording and JSON/XML shapes are private
and experimental; automation should not treat this pre-alpha tutorial as a schema
compatibility promise.

## 4. Strictly replay compatible evidence

Replay the keyed artifact into fresh worlds and write a separate child artifact:

```bash
uv run --locked effectprobe replay "$tutorial_dir/keyed.json" \
  --artifact "$tutorial_dir/keyed-replay.json"
```

Replay first compares the source artifact's registered subject, contract, fixture,
observer, schedule, dependency-lock, runtime, producer, and private schema
descriptors with the live checkout. Detected drift refuses verified replay before
evaluative provisioning. A compatible replay independently executes the case and
records whether its canonical evidence reproduced the source; reproduction match is
separate from the newly evaluated clean and retry axes.

## 5. Interpret the evidence boundary

The bundled observer records both projected state and append-only committed refund
history. Final state alone is insufficient for the at-most-once invariant because
a duplicate could be hidden by later collapse or deletion. History is measured
from each trial's recorded baseline so fixture setup is not counted as a subject
effect.

Keep these identities distinct when reading a report:

- the **logical operation** is the one intended refund;
- the optional **operation key** is subject-visible domain data selected for this
  operation;
- a **delivery** belongs to the transport path;
- an **attempt** is one concrete subject invocation;
- an MCP request identifier is transport metadata, not automatically any of the
  identities above.

The report also distinguishes `PASS`, `FAIL`, `INCONCLUSIVE`, and `ERROR` invariant
verdicts. `clean_validity` may be `UNVERIFIED` when no clean assertions were
declared. The bundled case declares clean assertions, so its expected clean status
is `PASS` in both modes.

For the complete aggregation, evidence-sufficiency, confirmation, and permitted
claim rules, read the normative [alpha claim boundaries](claim-boundaries.md).

## 6. Handle local outputs

Evidence artifacts and report files are created with private permissions, but they
remain user-controlled local test data. Inspect them before sharing and retain them
only as long as your workflow requires. Do not place production credentials or
sensitive production inputs in this controlled fixture.

If a report step fails after an artifact was successfully committed, the artifact
is intentionally retained as evidence. Operational errors use bounded stderr
categories rather than raw exception details. See the [threat model](threat-model.md)
for the security assumptions behind those controls.
