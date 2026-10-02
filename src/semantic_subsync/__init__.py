"""SemanticSubSync: re-time a subtitle on a reference subtitle by matching cues on meaning."""
from .core import MIN_COVERAGE, P, clean, parse, resync, sync, write

__version__ = "0.10.0"
__all__ = ["MIN_COVERAGE", "P", "clean", "parse", "resync", "sync", "write", "__version__"]
