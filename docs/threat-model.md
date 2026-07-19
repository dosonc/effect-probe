# Threat model

EffectProbe's current installed command executes one trusted, local, bundled test
subject against case-provisioned SQLite state. This document explains the security
and trust boundaries of that path. It does not describe a sandbox or make a claim
about arbitrary subjects, MCP servers, or production systems.

For the semantic limits on evaluative conclusions, see the normative
[alpha claim boundaries](claim-boundaries.md). Report suspected vulnerabilities as
described in the repository [security policy](../SECURITY.md).

## System in scope

The supported command starts the registered controlled MCP refund case. EffectProbe
provisions fresh local worlds, launches its bundled MCP server over stdio, invokes a
bundled unsafe or keyed subject, injects one cooperative client-result loss, retries
once, observes SQLite state and append-only refund history, attempts cleanup, and
records private evidence. It may then project eligible evidence as a terminal,
JSON, or JUnit report.

The actors and components have these trust assumptions:

- The user invoking EffectProbe controls the checkout, command arguments, and
  destination paths.
- The bundled subject, harness, observer, and MCP server are trusted local code.
- The case-provisioned SQLite files are disposable test state, not production or
  shared state.
- Evidence and reports are local files controlled by the invoking user. Their
  formats remain private and experimental.
- The operating system, Python runtime, installed dependencies, and repository
  checkout are part of the recorded execution environment, not adversarial
  isolation boundaries.

## Assets and security properties

The current implementation is responsible for protecting several narrow properties
within that trusted-local model:

- **User-owned file integrity:** artifact and report destinations are exclusive;
  existing paths are refused rather than overwritten.
- **Evidence integrity:** reports are built only from eligible artifacts whose
  registered scope, schedule, identities, history, confirmation, cleanup,
  compatibility descriptor, and redaction policy pass validation.
- **Conclusion integrity:** clean validity and retry safety remain separate, and a
  successful process exit is not presented as an evaluative pass.
- **Identity separation:** a logical operation, subject-visible operation key,
  transport delivery, concrete attempt, trial, and MCP request identifier are not
  silently substituted for one another.
- **Bounded disclosure:** persisted evidence and diagnostics use explicit
  allowlists and bounded error categories rather than raw exceptions or subprocess
  output.

These properties are implementation goals within the declared scope. They are not
a general security guarantee.

## Threats addressed by the current slice

### Accidental overwrite or path collision

Run and replay require a new evidence-artifact destination. Optional report files
also require a new destination. The command rejects existing paths, symlinks,
missing or non-directory parents, `-` for evidence artifacts, and collisions among
source, child-artifact, and report destinations. Lower-level exclusive publication
remains authoritative if the filesystem changes after preflight.

### Partial report publication

Report files are rendered before publication and committed through an exclusive
same-directory temporary file. A failed publication should not expose a partial
report at the requested destination. An evidence artifact already committed before
a later reporting failure remains evidence and is not deleted.

### Secret or environment disclosure

Evidence capture and report projection use deny-by-default field selection.
Reports and bounded CLI diagnostics do not intentionally expose raw exception text,
tracebacks, subprocess stderr, command lines, process identifiers, MCP request
identifiers, undelivered results, or arbitrary environment values. The installed
facade does not accept arbitrary executable arguments or environment overrides.

This is defense in depth, not a promise that user-selected test inputs are
non-sensitive. Treat generated artifacts and reports as potentially sensitive local
test data and inspect them before sharing.

### Tampered or incompatible evidence

Artifact readers validate the private schema and registered case shape. Exact
replay compares recorded and live subject, contract, fixture, observer, schedule,
dependency-lock, runtime, and producer descriptors. Detected drift is refused before
evaluative work instead of being treated as a verified reproduction.

These checks establish compatibility with the registered local replay path; they
are not a signature, provenance attestation, or protection against an attacker who
can modify both evidence and trusted code.

### Misleading conclusions

At-most-once evaluation uses append-only committed-effect history from a recorded
trial baseline, not final state alone. Retry safety requires proof that the selected
fault point was reached and injected. A candidate violation is confirmed twice in
fresh worlds before the current policy reports `FAIL`. Missing evidence remains
`INCONCLUSIVE`, and harness malfunction remains `ERROR`.

The subject receives only its ordinary subject-visible inputs. Harness-only fault
knowledge and transport identities do not enter its recovery decisions.

## Threats explicitly outside scope

EffectProbe does not currently defend against:

- malicious or compromised subjects, plugins, MCP servers, Python dependencies, or
  repository code;
- filesystem, process, container, kernel, or host escape by executed code;
- denial of service, resource exhaustion, fork bombs, or intentionally
  non-terminating subjects;
- theft from the invoking user's files, environment, credentials, network, or other
  processes by trusted-local code that violates the model;
- concurrent hostile modification of the checkout, runtime, dependency cache, or
  generated files;
- production traffic, credentials, providers, databases, or shared environments;
- cryptographic authenticity, signed evidence, remote attestation, access control,
  retention policy, or secure deletion;
- arbitrary MCP protocol validation, network isolation, or security certification.

Do not run EffectProbe with untrusted code or production credentials. Use an
appropriately isolated environment when the local code or dependency chain is not
fully trusted.

## Residual risks

The current implementation is pre-alpha. Private redaction rules, compatibility
descriptors, and output formats may change. Bugs in the harness, observer, cleanup,
or redaction path can invalidate evidence or disclose data. Files can remain after
process termination, and completed artifacts intentionally remain after a later
reporting failure. The single bundled case cannot reveal effects outside its named
SQLite surfaces.

An EffectProbe result therefore supports only the bounded semantic statement in the
[claim-boundary document](claim-boundaries.md). It cannot establish that a subject
is generally safe, correct, idempotent, secure, or suitable for production.
