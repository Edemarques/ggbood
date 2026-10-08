# Regression tests for minilib

The repository at `{REPO}` contains `minilib`, a small pure-Python library with helpers for
durations, byte sizes, a token-bucket rate limiter and plain-text tables. It has no tests yet.

Write a pytest regression suite that pins down the current behaviour of the public API, so that a
later refactoring that changes any of the behaviour below makes a test fail. Add files only under
`tests/`. The whole suite must finish in under 60 seconds. Tests must import only from `minilib`.
The examples in `README.md` are part of the documented behaviour.

## Scope

- `parse_duration` and `format_duration` in `minilib.units`: every accepted input form, the
  `default_unit` and `strict` options, `precision` and `compact`, and the ten-year limit.
- `parse_size` and `format_size`: decimal and binary units, `precision`, negative and very large
  sizes, `binary_default`.
- `RateLimiter` in `minilib.limiter`: `consume`, `wait_time`, `reset`, `stats`, the history
  kept per limiter and how tokens refill over time (always pass `now=`).
- `render_table`, `wrap_cell`, `slugify` and `summarize` in `minilib.text`.
- Which inputs raise, and which exception class they raise (`ParseError`, `LimitExceeded`,
  `ValueError`, `TypeError`).

## Not part of the contract

- The exact error message text and the `repr()` of objects may change.
- Private helpers (names starting with an underscore) and the module layout.
- `dump_config` output, which depends on whether PyYAML is installed.
