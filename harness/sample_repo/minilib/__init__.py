"""minilib: small helpers for durations, sizes, rate limiting and plain-text tables.

>>> parse_duration("1h30m")
5400
>>> format_size(1536, binary=True)
'1.5 KiB'
"""
from .errors import LimitExceeded, MinilibError, ParseError
from .limiter import RateLimiter
from .text import HAVE_YAML, dump_config, render_table, slugify, summarize, wrap_cell
from .units import UNIT_SECONDS, format_duration, format_size, parse_duration, parse_size

__all__ = [
    "MinilibError",
    "ParseError",
    "LimitExceeded",
    "RateLimiter",
    "UNIT_SECONDS",
    "parse_duration",
    "format_duration",
    "parse_size",
    "format_size",
    "render_table",
    "wrap_cell",
    "slugify",
    "summarize",
    "dump_config",
    "HAVE_YAML",
]

__version__ = "1.4.0"
