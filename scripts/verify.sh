#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

uv run --locked pre-commit run --all-files --show-diff-on-failure
uv run --locked pyright
uv run --locked pytest
uv build --no-sources
