# minilib

Small helpers for durations, byte sizes, rate limiting and plain-text tables.

## Durations

```python
>>> from minilib import parse_duration, format_duration
>>> parse_duration("2h30m")
9000
>>> parse_duration("1.5h")
5400
>>> parse_duration("90", default_unit="m")
5400
>>> format_duration(9000)
'2h 30m'
>>> format_duration(90.25, precision=2, compact=True)
'1m30.25s'
```

`parse_duration` raises `ParseError` (a subclass of `ValueError`) for text it
cannot read, for unknown units and for durations longer than ten years. With
`strict=True` every unit may appear once, from the largest to the smallest.

## Sizes

```python
>>> format_size(1536, binary=True)
'1.5 KiB'
>>> parse_size("1.5 MB")
1500000
```

## Rate limiting

`RateLimiter(capacity, rate=1.0)` is a token bucket. Pass `now=` to `consume`
and `wait_time` to use your own clock readings.

## Tables

`render_table(rows, headers=None, align="left", padding=1, max_width=None, border=True)`
renders a list of rows; `wrap_cell` shortens a single cell; `slugify` builds URL
slugs; `summarize` reduces a list of numbers; `dump_config` writes a flat mapping
(as YAML when PyYAML is installed).
