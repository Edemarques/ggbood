"""Plain-text helpers: table rendering, cell wrapping, slugs and summaries."""
import re

try:  # optional: nicer config dumps when PyYAML is installed
    import yaml
except ImportError:  # pragma: no cover - depends on the environment
    yaml = None

HAVE_YAML = yaml is not None

ALIGNMENTS = {"left": str.ljust, "right": str.rjust, "center": str.center}
BORDER_CHARS = {"corner": "+", "horizontal": "-", "vertical": "|"}
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_ACCENTS = str.maketrans("àáâäçèéêëìíîïñòóôöùúûüý", "aaaaceeeeiiiinoooouuuuy")


def wrap_cell(text, width=10, ellipsis="..."):
    """Shorten `text` to at most `width` characters, ending with `ellipsis` when cut."""
    text = str(text)
    if width < 1:
        raise ValueError("width must be at least 1")
    if len(text) <= width:
        return text
    if len(ellipsis) >= width:
        return text[:width]
    return text[: width - len(ellipsis)] + ellipsis


def _widths(rows, headers):
    widths = [len(h) for h in headers] if headers else []
    for row in rows:
        for i, cell in enumerate(row):
            if i >= len(widths):
                widths.append(len(cell))
            elif len(cell) > widths[i]:
                widths[i] = len(cell)
    return widths


def render_table(rows, headers=None, align="left", padding=1, max_width=None, border=True):
    """Render rows (lists of cells) as a text table; None cells render empty."""
    if align not in ALIGNMENTS:
        raise ValueError("unknown alignment %r" % align)
    if padding < 0:
        raise ValueError("padding must not be negative")
    cells = [["" if c is None else str(c) for c in row] for row in rows]
    heads = [str(h) for h in headers] if headers else None
    if max_width is not None:
        cells = [[wrap_cell(c, max_width) for c in row] for row in cells]
        if heads:
            heads = [wrap_cell(h, max_width) for h in heads]
    widths = _widths(cells, heads)
    justify = ALIGNMENTS[align]
    pad = " " * padding

    def line(row):
        row = row + [""] * (len(widths) - len(row))
        inner = [pad + justify(c, w) + pad for c, w in zip(row, widths)]
        if border:
            return BORDER_CHARS["vertical"] + BORDER_CHARS["vertical"].join(inner) + BORDER_CHARS["vertical"]
        return " ".join(inner).rstrip()

    rule = BORDER_CHARS["corner"] + BORDER_CHARS["corner"].join(
        BORDER_CHARS["horizontal"] * (w + 2 * padding) for w in widths) + BORDER_CHARS["corner"]
    out = [rule] if border else []
    if heads:
        out.append(line(heads))
        out.append(rule if border else "-" * len(out[-1]))
    out.extend(line(r) for r in cells)
    if border:
        out.append(rule)
    return "\n".join(out)


def slugify(text, sep="-", max_len=None):
    """Lower-case ASCII slug of `text` with words joined by `sep`."""
    slug = _SLUG_STRIP.sub(sep, text.lower().translate(_ACCENTS)).strip(sep)
    if max_len is not None and len(slug) > max_len:
        slug = slug[:max_len].rstrip(sep)
    return slug


def summarize(values, mode="mean"):
    """One number describing `values`: mode is 'mean', 'max', 'min' or 'spread'."""
    if not values:
        raise ValueError("no values")
    if mode == "mean":
        result = sum(values) / len(values)
    elif mode == "max":
        result = max(values)
    elif mode == "min":
        result = min(values)
    elif mode == "spread":
        result = max(values) - min(values)
    return round(result, 3)


def dump_config(data):
    """Serialise a flat mapping, as YAML when PyYAML is installed."""
    if yaml is not None:
        return yaml.safe_dump(dict(data), sort_keys=True).strip()
    return "\n".join("%s: %s" % (k, data[k]) for k in sorted(data))
