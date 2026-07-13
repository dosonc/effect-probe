"""EffectProbe package metadata.

The public fault-injection API has not been implemented yet.
"""

from importlib.metadata import version as distribution_version

__version__ = distribution_version("effectprobe")

__all__ = ("__version__",)
