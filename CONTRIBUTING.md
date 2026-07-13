# Contributing to EffectProbe

Thank you for considering a contribution. EffectProbe is pre-alpha: public APIs,
schemas, and extension boundaries may change while the core semantics are being
validated.

## Before starting

Open an issue before substantial features, public API changes, new integrations, or
semantic changes. Small fixes and documentation improvements can go directly to a
pull request.

Do not include vulnerability details in a public issue. Follow
[SECURITY.md](SECURITY.md) instead.

## Development setup

EffectProbe requires Python 3.12 and uv.

```bash
git clone https://github.com/dosonc/effect-probe.git
cd effect-probe
uv sync --locked
uv run --locked pre-commit install
```

Run the same checks used in CI before requesting review:

```bash
uv run --locked pre-commit run --all-files
uv run --locked pyright
uv run --locked pytest
uv build --no-sources
```

## Branches, commits, and pull requests

Use a short-lived branch named for one concern, for example:

```text
feat/lost-result-fault
fix/operation-identity
docs/claim-boundaries
chore/ci-bootstrap
```

Commit and pull-request titles follow Conventional Commits:

```text
<type>(<optional-scope>): <imperative description>
```

Common types are `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`,
`chore`, and `revert`. Initial scopes include `core`, `faults`, `observers`,
`fixtures`, `reports`, `cli`, `mcp`, `langgraph`, and `docs`.

Examples:

```text
feat(faults): simulate lost result after commit
fix(core): preserve operation identity across retry
docs: define alpha claim boundaries
```

Pull requests are squash-merged, so the pull-request title becomes the commit
subject on `main`. Keep each pull request focused, explain the motivation and
trade-offs, include tests or a clear rationale when no test applies, and update
documentation when behavior or public interfaces change.

## Developer Certificate of Origin

EffectProbe uses the [Developer Certificate of Origin 1.1](DCO), not a contributor
license agreement. Sign off every commit to certify that you have the right to
submit the contribution under the project's license:

```bash
git commit --signoff -m "feat(core): add operation identity"
```

The sign-off records your contribution identity in Git history. It is separate from
cryptographic commit signing. Maintainers verify the trailers before merge; the
repository may also enforce them automatically.

## AI-assisted contributions

AI-assisted contributions are welcome. Contributors remain responsible for the
correctness, provenance, licensing, security, and absence of secrets in everything
they submit.

## Project boundaries

EffectProbe runs trusted local test subjects against case-provisioned test state; it
is not a security sandbox. Tests and reports must describe their observed surfaces
and must not imply that a harness-controlled dependency validates production
behavior.
