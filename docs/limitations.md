# Current limitations

EffectProbe is pre-alpha. Its installed command exposes one controlled local case,
while the normative [alpha claim boundaries](claim-boundaries.md) also describe
semantics intended for later configurable cases. This document separates the
behavior available now from those planned boundaries.

## One registered case

The current command supports only `controlled-mcp-refund` in `unsafe` and `keyed`
modes. Both use repository-owned trusted subjects, a bundled local MCP stdio server,
and a harness-controlled SQLite provider.

Users cannot yet configure arbitrary subjects, MCP servers, executables,
environments, inputs, contracts, fixtures, observers, operation-key selectors,
failure schedules, or report implementations. Passing the keyed fixture does not
validate a production payment provider or another MCP tool.

## Finite execution envelope

The installed case evaluates:

- one logical refund operation;
- one attempt in each clean trial;
- no more than two attempts in each perturbed trial;
- one cooperatively injected MCP client-result loss;
- no concurrent schedule exploration.

A candidate contradiction adds two fresh confirmation pairs under the current
policy; each confirmation repeats those per-trial limits in newly provisioned
worlds.

It does not currently exercise process termination, checkpoint resume, arbitrary
crashes, timeouts, network partitions, compensation, multi-tool transactions,
concurrent histories, or model-based schedule exploration. Determinism here means
coordinating the supported cooperative fault boundary without sleep-based timing;
it does not mean exhaustive real-world failure coverage.

## Bounded observer coverage

The installed controlled MCP case bundles an observer for named refund state and
append-only committed refund history in case-provisioned SQLite worlds. A second
private, test-only comparison reads complete ordered refund history from a
case-owned JSON Lines journal and derives current state from that history. It
validates the private observer seam against a different controlled source, but it
is not selectable through the installed command and is not eligible for the MCP
artifact, report, or replay path.

Each observer covers only its declared case-owned surface. Neither can discover or
rule out effects on unobserved files, databases, services, queues, caches, logs,
messages, or other systems. The file-journal case also does not test process crashes
or establish filesystem crash durability.

An at-most-once result for the declared refund surface requires the recorded
append-only history. Snapshot equality alone cannot establish that no transient
duplicate occurred. Observer provenance identifies the harness-controlled source;
it does not establish completeness outside that source or transfer the result to a
production provider.

Both observer implementations remain private. There is no generic configuration or
stable third-party observer extension interface.

## Conditional, axis-specific results

EffectProbe reports clean validity and retry safety separately. It does not produce
an unqualified overall subject verdict. A passing axis applies only to the recorded
subject, code and dependency state, runtime, input, contract, observer coverage,
environment, and failure schedule.

The invariant verdicts `PASS`, `FAIL`, `INCONCLUSIVE`, and `ERROR`, plus the clean
axis status `UNVERIFIED`, have distinct meanings. A command exit status instead
describes whether the requested artifact or report operation completed. In
particular, a completed run with `retry_safety=FAIL` still exits `0`.

The current confirmation policy reruns a candidate contradiction twice in fresh
worlds. This supports a bounded deterministic failure conclusion for the registered
case; it is not statistical evidence or a general reliability estimate.

## Private evidence and report formats

Evidence artifacts, compatibility descriptors, registry identifiers, terminal
wording, canonical JSON fields, JUnit structure, and private Python helpers are not
stable third-party contracts. There is no public JSON Schema, migration framework,
compatibility override, best-effort replay, or promise that artifacts remain
replayable across source, dependency, runtime, contract, observer, schedule,
fixture, producer, or schema changes.

Exact replay refuses detected drift. A compatible replay is a fresh re-execution,
not a copy of a predetermined result, and it records reproduction match separately
from the child run's independently evaluated axes. Older artifacts may remain
inspectable while being ineligible for current reporting or verified replay.

## Trusted-local security model

Subjects and repository code are trusted. EffectProbe is not a security sandbox and
does not contain malicious code, prevent host access, guarantee termination, or
isolate production credentials. The installed facade narrows command configuration
and applies path, redaction, and output controls, but those controls do not create a
host security boundary.

Use only disposable case-provisioned state and local test inputs. See the
[threat model](threat-model.md) for addressed threats and residual risks.

## Platform and operational limits

The first stable path targets Python 3.12 and Linux. The repository is developed and
verified with `uv`; clean-environment installation testing and release automation
remain future work. The package is not yet presented as a stable PyPI release.

Report destinations and evidence-artifact destinations are exclusive and cannot
overwrite existing paths. Evidence does not use stdin or stdout. There is no force
flag, streaming report, interactive configuration, shell completion, hosted
documentation site, or remote evidence service.

## Conclusions not supported

Current results do not establish:

- general subject safety, correctness, or idempotency;
- exactly-once execution or universal at-most-once behavior;
- observation of every external effect;
- production-provider, production-environment, or production-traffic behavior;
- MCP protocol conformance, security certification, or containment of untrusted
  code;
- behavior under untested inputs, source or dependency changes, integrations,
  schedules, partitions, concurrency, or environments.

Start with the [controlled MCP tutorial](tutorial.md), and use the normative claim
document when deciding what a particular result is allowed to mean.
