# Current limitations

EffectProbe is pre-alpha. Its installed command exposes one controlled local case,
and `effectprobe.experimental` exposes one unstable trusted-local Python case
contract. The normative [alpha claim boundaries](claim-boundaries.md) also
describe semantics intended for later, broader configurable cases. This document
separates the behavior available now from those planned boundaries.

## One registered case

The current command supports only `controlled-mcp-refund` in `unsafe` and `keyed`
modes. Both use repository-owned trusted subjects, a bundled local MCP stdio server,
and a harness-controlled SQLite provider.

The command still cannot configure arbitrary subjects, MCP servers, executables,
environments, inputs, contracts, fixtures, observers, operation-key selectors,
failure schedules, or report implementations. Passing the keyed fixture does not
validate a production payment provider or another MCP tool.

## One experimental external Python contract

An external caller can now import `effectprobe.experimental`, construct one trusted
local case, and run it without editing EffectProbe internals or importing a private
module. The caller supplies one concrete input, an optional subject-visible
operation-key selector, fresh provision/cleanup sessions, one named observer
surface, clean and retry contracts, applicability, selected source and lock files
for fingerprinting, and the fixed cooperative
provider-commit/result-loss/retry-once schedule.

The Python attachment and its immutable in-memory report are explicitly unstable
before 1.0. There is no dynamic case loader, plugin discovery, generic MCP
configuration, experimental CLI command, artifact persistence, JSON/JUnit schema,
or verified replay. The user runs its own trusted module directly. A later bounded
LangGraph checkpoint/resume example must consume or validate this path rather than
introduce a repository-only integration seam. See
[Experimental Python cases](experimental-cases.md) and
[ADR-0002](adr/0002-langgraph-durable-integration-go-no-go.md).

## Finite execution envelope

The installed case and experimental Python contract each evaluate:

- one logical refund operation;
- one attempt in each clean trial;
- no more than two attempts in each perturbed trial;
- one cooperatively injected result loss at their declared boundary;
- no concurrent schedule exploration.

A candidate contradiction adds two fresh confirmation pairs under the current
policy; each confirmation repeats those per-trial limits in newly provisioned
worlds.

The experimental contract exposes provider-result loss; the installed MCP case
uses client-result loss. Neither currently exercises process termination,
checkpoint resume, arbitrary crashes, timeouts, network partitions, compensation,
multi-tool transactions, concurrent histories, or model-based schedule
exploration. Determinism here means coordinating the supported cooperative fault
boundary without sleep-based timing; it does not mean exhaustive real-world
failure coverage.

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

The bundled SQLite and file-journal observers remain private. An experimental
Python case may supply one observer through its public unstable world contract,
but there is no multiple-surface observer model, generic serialized configuration,
or stable third-party observer interface.

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

Controlled MCP evidence artifacts, compatibility descriptors, registry
identifiers, terminal wording, canonical JSON fields, JUnit structure, and private
Python helpers are not stable third-party contracts. There is no public JSON
Schema, migration framework, compatibility override, best-effort replay, or
promise that artifacts remain replayable across source, dependency, runtime,
contract, observer, schedule, fixture, producer, or schema changes.

Exact replay refuses detected drift. A compatible replay is a fresh re-execution,
not a copy of a predetermined result, and it records reproduction match separately
from the child run's independently evaluated axes. Older artifacts may remain
inspectable while being ineligible for current reporting or verified replay.

Experimental Python reports are separate immutable in-memory projections. They
record source, dependency-lock, input, operation-key, contract, observer, runtime,
and fixed-schedule fingerprints but are not artifacts and cannot be reported or
replayed by the installed command. Content digests bound scope; they do not encrypt
low-entropy inputs, sign code, or establish provenance.

## Trusted-local security model

Subjects, caller-owned experimental cases, and repository code are trusted.
EffectProbe is not a security sandbox and does not contain malicious code, prevent
host access, guarantee termination, or isolate production credentials. The
installed facade narrows command configuration, while the experimental path makes
the caller responsible for importing and running its own module. Bounded output,
fingerprint, and redaction controls do not create a host security boundary.

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

Start with the [controlled MCP tutorial](tutorial.md) or
[experimental Python case guide](experimental-cases.md), and use the normative
claim document when deciding what a particular result is allowed to mean.
