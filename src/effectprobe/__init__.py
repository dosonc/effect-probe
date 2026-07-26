"""EffectProbe package metadata.

The stable fault-injection API has not been implemented. The explicitly unstable
trusted-local case contract lives in :mod:`effectprobe.experimental`.
"""

from importlib.metadata import version as distribution_version

__version__ = distribution_version("effectprobe")

__all__ = ("__version__",)
