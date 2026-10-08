"""Parsing and formatting of durations ("1h30m") and byte sizes ("1.5 MiB")."""
import re

from .errors import ParseError

UNIT_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_ORDER = ("w", "d", "h", "m")
MAX_DURATION = 10 * 365 * 86400

_SINGLE_RE = re.compile(r"^(\d+(?:\.\d+)?)([a-z]*)$")
_PART_RE = re.compile(r"(\d+(?:\.\d+)?)([a-z]+)")
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d*)?)\s*([kmgt]?)(i?)(b?)\s*$", re.I)

SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")
BINARY_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def _number(total):
    if total == int(total):
        return int(total)
    return round(total, 6)


def parse_duration(text, default_unit="s", strict=False):
    """Parse '90', '1.5h', '2h30m' or '1d 2h' into a number of seconds.

    A bare number uses `default_unit`. With `strict=True` a unit may appear
    only once and the parts must go from the largest unit to the smallest.
    """
    if not isinstance(text, str):
        raise TypeError("duration must be a string, not %s" % type(text).__name__)
    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned:
        raise ParseError(text, "empty string")
    single = _SINGLE_RE.match(cleaned)
    if single:
        number, unit = single.groups()
        unit = unit or default_unit
        if unit not in UNIT_SECONDS:
            raise ParseError(text, "unknown unit %r" % unit)
        total = float(number) * UNIT_SECONDS[unit]
    else:
        parts = _PART_RE.findall(cleaned)
        if not parts or "".join(n + u for n, u in parts) != cleaned:
            raise ParseError(text)
        total = 0.0
        seen = []
        for number, unit in parts:
            if unit not in UNIT_SECONDS:
                raise ParseError(text, "unknown unit %r" % unit)
            if strict:
                if unit in seen:
                    raise ParseError(text, "unit %r repeated" % unit)
                if seen and UNIT_SECONDS[seen[-1]] < UNIT_SECONDS[unit]:
                    raise ParseError(text, "units out of order")
            seen.append(unit)
            total += float(number) * UNIT_SECONDS[unit]
    if total > MAX_DURATION:
        raise ParseError(text, "longer than ten years")
    return _number(total)


def format_duration(seconds, precision=0, compact=False):
    """Render a number of seconds as '1d 2h 3m 4s' (or '1d2h3m4s' when compact)."""
    if seconds < 0:
        raise ValueError("negative duration")
    if 0 < seconds < 1:
        ms = round(seconds * 1000, precision)
        return ("%.*fms" % (precision, ms)) if precision else "%dms" % ms
    parts = []
    remaining = int(seconds)
    frac = seconds - remaining
    for unit in _ORDER:
        size = UNIT_SECONDS[unit]
        if remaining >= size:
            count, remaining = divmod(remaining, size)
            parts.append("%d%s" % (count, unit))
    secs = remaining + frac
    if secs or not parts:
        if precision:
            parts.append("%.*fs" % (precision, secs))
        else:
            parts.append("%ds" % round(secs))
    return ("" if compact else " ").join(parts)


def format_size(n, binary=False, precision=1):
    """Render a byte count with a decimal (KB, MB) or binary (KiB, MiB) unit."""
    if n < 0:
        return "-" + format_size(-n, binary=binary, precision=precision)
    base = 1024 if binary else 1000
    units = BINARY_UNITS if binary else SIZE_UNITS
    value = float(n)
    i = 0
    while value >= base and i < len(units) - 1:
        value /= base
        i += 1
    if i == 0:
        return "%d %s" % (n, units[0])
    return "%.*f %s" % (precision, value, units[i])


def parse_size(text, binary_default=False):
    """Parse '10', '10kb', '1.5 MB' or '2KiB' into a number of bytes.

    'k', 'm', 'g' and 't' without 'i' are decimal unless `binary_default` is true.
    """
    m = _SIZE_RE.match(text)
    if not m:
        raise ParseError(text, "not a size")
    number, letter, binary, _b = m.groups()
    letter = letter.lower()
    if binary and not letter:
        raise ParseError(text, "'i' needs a unit letter")
    power = "_kmgt".index(letter) if letter else 0
    base = 1024 if (binary or binary_default) else 1000
    return _number(float(number) * base ** power)
