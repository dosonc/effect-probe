# Threat model

EffectProbe's installed command executes one trusted, local, bundled test subject
against case-provisioned SQLite state. Its experimental Python contract also lets a
caller run one trusted local case in the caller's own process. This document
explains the security and trust boundaries of both paths. It does not describe a
sandbox or make a claim about untrusted subjects, arbitrary MCP servers, or
production systems.

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

The experimental Python path does not load a configured module or command. The
caller imports `effectprobe.experimental`, constructs a typed case in its own
trusted process, and calls `run_case`. The case provisions fresh worlds, invokes
its subject adapter, supplies one state/history observer, places the cooperative
provider-result boundary after commit, evaluates its contracts, and cleans up its
resources. EffectProbe returns a bounded immutable in-memory projection; this path
does not write an evidence artifact or support replay.

The actors and components have these trust assumptions:

- The user invoking EffectProbe controls the checkout, command arguments, and
  destination paths.
- The bundled subject, harness, observer, and MCP server are trusted local code.
- Caller-owned experimental cases, subject adapters, lifecycle callbacks,
  observers, canonicalizers, and evaluators are trusted local code with the same
  host authority as the invoking process.
- The case-provisioned SQLite files are disposable test state, not production or
  shared state.
- Controlled MCP evidence and reports are local files controlled by the invoking
  user. Experimental Python reports remain in memory unless the caller chooses to
  persist or transmit them. All formats and interfaces remain experimental.
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
- **Experimental projection boundary:** the Python report contains tokenized names,
  fingerprints, axes, identities, fault proof, cleanup outcomes, and fixed error
  categories, but omits raw input, operation keys, state, history, results, paths,
  exception messages, tracebacks, and arbitrary representations.

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

The experimental Python renderer follows the same bounded-output principle. Every
case-supplied runtime label uses a restricted token grammar, and free-form
exceptions or evidence values are not projected. Source, dependency-lock, input,
operation-key, contract, observer, runtime, and schedule values enter the scope as
digests or fixed descriptors. Digests are not encryption and can disclose guesses
about low-entropy values; do not use production secrets.

### Trusted-code attachment

EffectProbe does not accept a Python file path, module target, shell command,
environment override, or plugin entry point for the experimental case. The caller
is responsible for importing and invoking its own trusted module. This avoids a
second command-loading surface but does not reduce the authority of that code or
contain it.

Fingerprint inputs must be caller-selected regular files within bounded sizes.
Only file contents are retained as digests; paths and contents are not returned.
The fingerprints cover only the selected source files and dependency lock. They do
not discover dynamic imports, sign code, establish provenance, or defend against
concurrent modification.

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

- malicious or compromised subjects, caller-owned cases, plugins, MCP servers,
  Python dependencies, or repository code;
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
descriptors, experimental Python contract, and output formats may change. Bugs in
the harness, caller callbacks, observer, cleanup, fingerprint, or redaction path can
invalidate evidence or disclose data. Files can remain after process termination,
and completed MCP artifacts intentionally remain after a later reporting failure.
Neither a bundled case nor a single-surface external case can reveal effects outside
its named observer surface.

An EffectProbe result therefore supports only the bounded semantic statement in the
[claim-boundary document](claim-boundaries.md). It cannot establish that a subject
is generally safe, correct, idempotent, secure, or suitable for production.
