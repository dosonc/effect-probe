# ADR-0002: Gate durable integration on an external case path

- **Status:** Accepted
- **Date:** 2026-07-26

## Context

EffectProbe currently exercises its semantics through repository-owned refund
fixtures. The installed command runs one controlled MCP stdio case; users cannot
attach their own subject, fixture, observer, contract, or schedule. A private
LangGraph example could validate another runtime while leaving that product gap
unchanged.

The first alpha should do more than demonstrate the semantic kernel against bundled
fixtures. One external user must be able to run a documented experimental case for
a trusted local agent without editing EffectProbe internals. The extension can
remain unstable before 1.0, but it cannot remain private or usable only by
repository tests.

The proposed LangGraph schedule is narrower than general process-crash testing:
one effect task commits against a controlled dependency, the subject process is
terminated before the task result is persisted, and a fresh process resumes one
recorded thread from a persistent checkpoint. The question is whether current
LangGraph behavior makes that future experiment faithful enough to pursue without
changing EffectProbe's claim boundaries.

## Evidence considered

Current LangGraph documentation and source describe the necessary building blocks:

- The
  [Functional API documentation](https://docs.langchain.com/oss/python/langgraph/functional-api)
  says completed task results are restored during replay, a task that starts but
  does not finish may run again on resume, and recovery after an error invokes the
  entrypoint with `None` and the same thread identifier.
- The
  [persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence)
  distinguishes thread checkpoints from long-term stores and notes that in-memory
  savers do not survive process restart, while a local SQLite saver is available
  for persistent development workflows.
- The current
  [`Durability` source definition](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/types.py#L2338-L2346)
  defines synchronous durability as persisting changes before the next step
  starts.

These upstream statements support a feasibility decision only. They do not prove
that EffectProbe has exercised the schedule, that a later locked dependency set
will behave identically, or that LangGraph supplies external-effect evidence.

## Decision

### Make one external case path a first-alpha release gate

EffectProbe will not prepare the first alpha release until one documented,
supported, experimental bring-your-own-case path lets an external user run a
trusted local agent without importing private EffectProbe modules or modifying
EffectProbe internals.

The external path must accept or derive:

- a trusted-local subject invocation and concrete input;
- an optional domain operation key selected from subject-visible data;
- isolated fresh-world provisioning and cleanup;
- named observer surfaces with state and history coverage, provenance, observation
  interval, and limitations;
- clean assertions and required retry invariants with declared evidence needs;
- one supported cooperative fault boundary and recovery policy;
- bounded subject, code, dependency, runtime, environment, contract, observer,
  input, and schedule scope; and
- separate clean-validity and retry-safety results, including inconclusive and
  infrastructure-error explanations.

The next work item will choose the narrowest viable attachment mechanism, such as a
trusted local Python case contract or a narrower MCP-based adapter. This ADR does
not freeze Python names, a plugin mechanism, configuration syntax, serialization,
report shape, or compatibility policy. “Experimental” permits breaking changes
before 1.0; it does not relax the evidence or claim-boundary requirements.

### Proceed conditionally with one LangGraph consumer

The LangGraph integration receives a conditional **go** for a later, separately
approved implementation. It must consume or validate the same external case path
instead of bypassing it with another repository-only fixture.

The bounded candidate will use:

- the Python Functional API with one checkpointed entrypoint and one effect task;
- a case-owned persistent SQLite checkpointer and explicit thread identifier;
- synchronous durability;
- one logical operation, no more than two concrete task/provider attempts, one
  injected process termination, and no concurrent schedule exploration; and
- exact dependency versions selected, locked, and recorded by its implementation
  plan.

It will not require an LLM, LangSmith, Agent Server, hosted infrastructure,
production credentials, or a production provider.

### Require proof of the commit-termination-resume schedule

The future perturbed trial must:

1. provision a fresh provider world and case-owned checkpoint store equivalent to
   the clean baseline on declared state surfaces;
2. synchronously persist the checkpoint from which the effect task can run;
3. let the controlled provider commit the effect and emit a one-way harness-only
   boundary signal while preventing the provider adapter from returning;
4. have the parent harness terminate the subject child and confirm that it exited;
5. preserve the first committed effect in complete append-only provider history;
6. start a distinct subject process with the same checkpoint store and LangGraph
   thread identifier;
7. resume with no new operation input and prove whether the unfinished task
   executes again; and
8. capture the subject result, final state, complete history, framework task
   evidence, process evidence, and cleanup outcome.

The fault signal may coordinate termination but must not enter subject-visible
recovery input or operation-key selection. A timeout may bound cleanup or detect a
malfunction; sleep-based timing is not proof that the boundary occurred.

An armed boundary that is never reached, a first task that returns before
termination, a child that is not proven terminated, a resume that starts another
thread, or missing proof of a second attempt cannot produce
`retry_safety=PASS`. Missing schedule proof is `INCONCLUSIVE`. Checkpointer,
process-control, fixture, observer, provider, driver, evaluator, or cleanup
malfunction is `ERROR`.

### Preserve evidence and identity boundaries

Current state and complete append-only committed-effect history remain the
external-effect evidence for an at-most-once invariant. LangGraph checkpoint data,
task records, boundary signals, process identifiers, and exit status prove only
the selected schedule. Final state or checkpoint state alone is insufficient.

Every future user-defined or LangGraph case must keep distinct:

- EffectProbe's logical operation identity;
- the optional subject-visible domain operation key;
- delivery identities for initial delivery and retry or resume;
- concrete attempt identities;
- LangGraph thread, checkpoint, and task identities; and
- process-generation identities.

No framework or process identity is automatically an operation, delivery, attempt,
or operation key.

### Preserve the existing evaluation lifecycle

The external path and LangGraph consumer must keep applicability, fixture validity,
clean validity, retry safety, and infrastructure errors separate. Clean and
perturbed trials use fresh worlds with trial-local history baselines. Candidate
retry failures require two valid confirmations in newly provisioned worlds.

Aggregation remains axis-specific: a confirmed `FAIL` takes precedence, otherwise
`ERROR`, otherwise `INCONCLUSIVE`, and only then `PASS`. EffectProbe does not emit
an unqualified overall status.

The first external acceptance case must cover a valid retry-safe outcome, a
confirmed retry failure with sufficient history, an inconclusive schedule or
evidence path, and an infrastructure error path. The LangGraph refund consumer is
expected to show:

- unsafe: `clean_validity=PASS`, then two perturbed committed refunds and a
  confirmed `retry_safety=FAIL`; and
- keyed: `clean_validity=PASS`, two concrete perturbed task/provider attempts, one
  committed refund, and `retry_safety=PASS`.

If the task does not rerun or the commit-termination boundary cannot be proven, the
LangGraph schedule has not been faithfully exercised.

## Consequences

### Positive

- The first alpha has a concrete external-use criterion instead of shipping only
  repository-owned demonstrations.
- A framework example must validate the same path users receive, reducing the risk
  of a private integration seam that cannot support external cases.
- LangGraph's documented incomplete-task behavior matches the ambiguity
  EffectProbe is intended to observe.
- The decision preserves append-only effect evidence, fresh-world comparison,
  identity separation, confirmation, and axis-specific results.
- Deferring exact extension names and serialization lets working external cases
  inform the contract before it is stabilized.

### Negative

- The first alpha now requires a substantive external-case slice before release.
- Users must provide trusted local test code, disposable state, declared observers,
  contracts, and a cooperative supported boundary; this is not zero-configuration
  agent testing.
- Loading user-defined trusted code introduces validation, cleanup, path, redaction,
  fingerprinting, and compatibility work that the current bundled case avoids.
- A process-termination slice adds checkpoint and child-process cleanup failure
  modes.
- The later LangGraph dependency and SQLite checkpointer will change the recorded
  compatibility scope and intentionally invalidate replay across detected drift.

## Alternatives considered

### Release the first alpha with bundled fixtures only

Rejected. The current command demonstrates the semantics but does not let a user
test their own agent through a supported path.

### Add a private LangGraph fixture first

Rejected. It could validate framework mechanics while bypassing the external
extension problem. Any approved framework example must consume or validate the
user-defined path.

### Freeze a generic public plugin or configuration schema now

Deferred. One working external case must first establish the minimum subject,
fixture, observer, contract, fault, scope, and result boundaries. The first
documented interface may be explicitly experimental and breaking before 1.0.

### Treat arbitrary agents, effects, or crash points as automatically supported

Rejected. EffectProbe cannot infer every external effect, manufacture sufficient
history, or faithfully inject an uncooperative boundary. Unsupported combinations
must fail during preflight or remain inconclusive rather than being approximated.

### Reject LangGraph integration

Rejected for now. The documented incomplete-task replay, same-thread recovery, and
persistent checkpoint behavior justify one bounded implementation attempt under
the gates above. Failure to prove the schedule in executable tests would reverse
that implementation decision without weakening EffectProbe's evidence rules.

## Status of implementation

This ADR accepts a direction and release gate. It does not ship an external case
interface, LangGraph support, a dependency, a new fault schedule, or a public
schema. Until separately approved work is implemented, the installed command
continues to run only the registered controlled MCP refund case.
