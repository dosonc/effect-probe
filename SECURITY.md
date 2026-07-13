# Security policy

## Supported versions

EffectProbe has no supported release yet. The `main` branch is pre-alpha development
code, and security fixes currently land there.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in public issues, discussions, pull
requests, or logs. Use GitHub's private vulnerability reporting for this repository:

<https://github.com/dosonc/effect-probe/security/advisories/new>

Include the affected commit or version, impact, reproduction steps, and a minimal
redacted example when possible. If private reporting is unavailable, do not submit
sensitive details publicly.

There is currently no bug bounty or guaranteed response-time service level.

## Security boundary

EffectProbe is intended to execute trusted local test subjects against
case-provisioned test state. It is not a sandbox for malicious or untrusted code.

Examples of issues that are in scope include EffectProbe introducing command or
environment injection, escaping a declared path boundary, persisting unsafe data,
or leaking secrets through artifacts, reports, exceptions, or subprocess output.
The ability of a deliberately malicious subject to access the invoking user's
resources is outside the planned alpha's security boundary and must not be treated
as containment.

A passing EffectProbe result is evidence only for the declared inputs, observed
surfaces, environment, invariants, and supported failure schedules. It is not a
general security or safety certification.
