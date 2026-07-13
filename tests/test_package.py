"""Package-level smoke tests."""

from importlib.metadata import version as distribution_version

import effectprobe


def test_version_matches_distribution_metadata() -> None:
    """The import version should match installed project metadata."""
    assert effectprobe.__version__ == distribution_version("effectprobe")
