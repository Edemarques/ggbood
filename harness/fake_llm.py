"""Deterministic stand-in for the agent's LLM (patches LLM._attempts).

Replies are chosen by WHAT is asked, never by timing:
  * the layout question ("Read the task statement below ...") -> REPO/TESTS lines;
  * a writer conversation (identified by object identity with run.writers[w]) at its k-th
    question -> REPLIES[(w, k)] (k = 0 draft, 1 = its first round message, 2 = second ...);
  * anything else -> "DONE".
Round replies also SKIP the last mutant id listed in the prompt (that id depends on mutation
status, so the skipped set is NOT deterministic; the cases written are).

Deliberately problematic content (each marked with "# PROBLEM:" below):
  syntax-error block (-> [MEND] drops one block), case importing a missing name
  (-> _mend_imports), time.time (lint), unrecordable library object, module-level library
  call (module NOT USABLE), private-module import against the task's import rule (NOT USABLE),
  `import requests` (NOT USABLE), `class Test...` (NOT USABLE), message-text / repr cases
  (dropped by contract_check), an UnboundLocalError message naming a local variable (dropped by
  rewrite_check), a result that changes when optional packages are hidden (dump_config),
  an unnamed block, a file name on the first line, EXCEPTION_CLASSES in body and in info string,
  a bash block and SKIP lines.
"""
import re
import threading
import time

FENCE = "```"

W1_DRAFT = '''I split my share into durations, sizes and error cases.

```python cases_durations.py
from minilib import parse_duration, format_duration


def case_parse_plain_seconds():
    return parse_duration("45")


def case_parse_default_unit_minutes():
    return parse_duration("3", default_unit="m")


def case_parse_hours_fraction():
    return parse_duration("1.5h")


def case_parse_compound():
    return parse_duration("2h30m")


def case_parse_compound_with_spaces():
    return parse_duration("1d 2h 3m")


def case_parse_milliseconds():
    return parse_duration("250ms")


def case_parse_uppercase_and_padding():
    return parse_duration("  10S ")


def case_parse_repeated_unit_lenient():
    return [parse_duration("1m1m"), parse_duration("30s1h")]


def case_parse_broken_syntax():
    # PROBLEM: syntax error, the tool must drop only this block
    return parse_duration("1h" +* 2)


def case_format_seconds_only():
    return [format_duration(0), format_duration(59), format_duration(60), format_duration(61)]


def case_format_compound():
    """Days, hours and seconds without minutes."""
    return format_duration(3 * 86400 + 4 * 3600 + 5)


def case_format_compact_and_precision():
    return (format_duration(90.25, precision=2, compact=True), format_duration(3600, compact=True))


def case_format_sub_second():
    return [format_duration(0.25), format_duration(0.0005, precision=2)]


def case_round_trip():
    values = ["1w", "6d23h", "90m", "45s"]
    return {v: format_duration(parse_duration(v)) for v in values}
```

```python cases_sizes.py
from minilib import format_size, parse_size


def case_format_small_and_boundaries():
    return [format_size(0), format_size(999), format_size(1000), format_size(1001)]


def case_format_binary():
    return [format_size(1023, binary=True), format_size(1024, binary=True),
            format_size(5 * 1024 ** 3, binary=True)]


def case_format_precision():
    return [format_size(123456789, precision=0), format_size(123456789, precision=3)]


def case_format_negative():
    return format_size(-2048, binary=True)


def case_format_huge():
    return format_size(10 ** 18)


def case_parse_sizes():
    return [parse_size("10"), parse_size("10kb"), parse_size("1.5 MB"), parse_size("2KiB"), parse_size("1g")]


def case_parse_size_rejects_words():
    return parse_size("lots")
```

```python cases_unit_errors.py
EXCEPTION_CLASSES = True
from minilib import parse_duration, format_duration, ParseError


def case_empty_duration_raises():
    return parse_duration("   ")


def case_unknown_unit_raises():
    return parse_duration("5y")


def case_strict_repeated_unit_raises():
    return parse_duration("1m1m", strict=True)


def case_too_long_raises():
    return parse_duration("600w")


def case_non_string_raises():
    return parse_duration(30)


def case_negative_format_raises():
    return format_duration(-1)


def case_parse_error_is_value_error():
    try:
        parse_duration("abc")
    except ValueError as e:
        return [type(e).__name__, isinstance(e, ParseError), e.text]
    return None
```

```python cases_messages.py
from minilib import parse_duration, ParseError, summarize


def case_parse_error_message():
    # PROBLEM: pins message text (contract_check must drop it)
    try:
        parse_duration("5y")
    except ParseError as e:
        return str(e)
    return None


def case_parse_error_reason():
    # PROBLEM: pins message text (contract_check must drop it)
    try:
        parse_duration("")
    except ParseError as error:
        return error.reason


def case_summarize_unknown_mode():
    # PROBLEM: the message names a local variable (rewrite_check must drop it)
    try:
        return summarize([1, 2, 3], mode="median")
    except Exception as e:
        return [type(e).__name__, str(e)]


def case_summarize_known_modes():
    return [summarize([3, 1, 2]), summarize([3, 1, 2], mode="spread")]
```

```python cases_defaults.py
from minilib import parse_duration

# PROBLEM: library code runs when the file is imported (module NOT USABLE)
DEFAULT_TIMEOUT = parse_duration("1m")


def case_default_timeout():
    return DEFAULT_TIMEOUT
```
'''

W2_DRAFT = '''Here are the limiter and text cases.

```python cases_limiter.py
from minilib import RateLimiter, LimitExceeded


def case_consume_until_empty():
    lim = RateLimiter(3, rate=1.0)
    return [lim.consume(now=0.0), lim.consume(now=0.0), lim.consume(now=0.0), lim.consume(now=0.0)]


def case_refill_over_time():
    lim = RateLimiter(2, rate=0.5)
    out = [lim.consume(2, now=10.0), lim.consume(now=11.0), lim.consume(now=12.0)]
    return out, lim.stats()


def case_refill_caps_at_capacity():
    lim = RateLimiter(5, rate=10.0)
    lim.consume(5, now=0.0)
    lim.consume(1, now=100.0)
    return lim.stats()


def case_start_empty():
    lim = RateLimiter(4, rate=2.0, start_full=False)
    return [lim.consume(now=0.0), lim.consume(now=0.5), lim.wait_time(3, now=0.5)]


def case_wait_time_zero_rate():
    lim = RateLimiter(2, rate=0.0)
    lim.consume(2, now=1.0)
    return lim.wait_time(1, now=5.0)


def case_history_and_len():
    lim = RateLimiter(2, rate=0.0)
    for t in range(5):
        lim.consume(now=float(t))
    return lim.history, len(lim)


def case_reset():
    lim = RateLimiter(3, rate=1.0)
    lim.consume(3, now=0.0)
    lim.reset(full=False)
    first = lim.stats()
    lim.reset()
    return first, lim.stats()


def case_repr():
    # PROBLEM: pins repr() (contract_check must drop it)
    return repr(RateLimiter(3, rate=1.5))


def case_limiter_object():
    # PROBLEM: returns a library object (not recordable)
    return RateLimiter(5, rate=1.0)


def case_uses_real_clock():
    # PROBLEM: reads the real clock (lint)
    import time
    lim = RateLimiter(1, rate=1.0)
    return lim.consume(now=time.time())
```

```python cases_limiter_errors.py
from minilib import RateLimiter


def case_over_capacity_raises():
    return RateLimiter(2).consume(3, now=0.0)


def case_zero_consume_raises():
    return RateLimiter(2).consume(0, now=0.0)


def case_bad_capacity_raises():
    return RateLimiter(0)
```

```python
# cases_text.py
from minilib import render_table, wrap_cell, slugify, summarize, dump_config, table_width


def case_wrap_short():
    return wrap_cell("abc")


def case_wrap_long():
    return [wrap_cell("abcdefghijkl"), wrap_cell("abcdefghijkl", width=5), wrap_cell("abcdef", width=2)]


def case_wrap_custom_ellipsis():
    return wrap_cell("hello world", width=8, ellipsis="~")


def case_table_basic():
    return render_table([[1, "a"], [22, "bb"]], headers=["n", "s"])


def case_table_right_no_border():
    return render_table([["x", None]], align="right", border=False)


def case_table_center_padding():
    return render_table([["ab", "c"]], headers=["h1", "h2"], align="center", padding=2)


def case_table_max_width():
    return render_table([["a very long cell", "b"]], max_width=6)


def case_table_bad_align():
    return render_table([["a"]], align="justify")


def case_table_written_to_file(tmp_path):
    path = tmp_path / "table.txt"
    path.write_text(render_table([[1, 2]], border=False))
    return path.read_text()


def case_slugify():
    return [slugify("Hello, World!"), slugify("  A--B  ", sep="_"), slugify("Long title here", max_len=8)]


def case_summarize_modes():
    return {m: summarize([4, 1, 7, 2], mode=m) for m in ("mean", "max", "min", "spread")}


def case_summarize_empty():
    return summarize([])


def case_table_width():
    # PROBLEM: `table_width` does not exist; the import breaks the file (mend path)
    return table_width([["abc"]])


def case_dump_config():
    # PROBLEM: result changes when the optional PyYAML is hidden
    return dump_config({"debug": True, "name": "x"})
```

```python
from minilib import slugify


def case_slugify_accents():
    return slugify("Caf\\u00e9 au lait, cr\\u00e8me")
```

```python cases_limiter_internal.py
# PROBLEM: imports from a submodule although the task allows only `minilib` (NOT USABLE)
from minilib.limiter import RateLimiter


def case_internal_capacity():
    return RateLimiter(7).capacity
```

```bash
python -m pytest -q
```
'''

W1_ROUND1 = '''```python cases_durations_2.py
from minilib import parse_duration, format_duration


def case_threshold_minute():
    return [parse_duration("59s"), parse_duration("60s"), parse_duration("61s")]


def case_max_duration_boundary():
    return [parse_duration("520w"), parse_duration("3650d")]


def case_default_unit_hours():
    return parse_duration("2", default_unit="h")


def case_format_week_exact():
    return [format_duration(604800), format_duration(604799), format_duration(604801)]


def case_strict_distinct_units():
    return parse_duration("1h1m", strict=True)


def case_strict_out_of_order():
    try:
        return parse_duration("1m1h", strict=True)
    except ValueError:
        return "rejected"


def case_format_precision_whole_seconds():
    return [format_duration(61.5), format_duration(61.5, precision=1), format_duration(0.9999)]
```

```python cases_sizes_2.py
from minilib import format_size, parse_size


def case_size_boundaries_binary():
    return [format_size(1024 ** 2 - 1, binary=True), format_size(1024 ** 2, binary=True)]


def case_parse_size_binary_variants():
    return [parse_size("1KiB"), parse_size("1kib"), parse_size("3 TB"), parse_size("2k", binary_default=True)]


def case_parse_size_needs_letter():
    try:
        return parse_size("5ib")
    except ValueError:
        return "rejected"
```

```python cases_extra.py
# PROBLEM: imports a third-party package (NOT USABLE)
import requests
from minilib import format_size


def case_requests_version():
    return format_size(len(requests.__version__))
```
'''

W2_ROUND1 = '''```python cases_limiter_2.py
from minilib import RateLimiter


def case_partial_refill():
    lim = RateLimiter(4, rate=0.25)
    lim.consume(4, now=0.0)
    return [lim.wait_time(1, now=2.0), lim.consume(now=4.0), lim.stats()]


def case_history_limit():
    lim = RateLimiter(1, rate=0.0)
    for t in range(12):
        lim.consume(now=float(t))
    return [len(lim.history), lim.history[0], lim.stats()["deny_ratio"]]


def case_clock_injected():
    readings = iter([5.0, 5.0, 9.0])
    lim = RateLimiter(2, rate=1.0, clock=lambda: next(readings))
    return [lim.consume(2), lim.consume(), lim.consume()]


def case_negative_rate_rejected():
    try:
        RateLimiter(1, rate=-1)
    except ValueError:
        return "rejected"
    return "accepted"
```

```python cases_limiter_errors_2.py EXCEPTION_CLASSES = True
from minilib import RateLimiter


def case_wait_time_over_capacity():
    return RateLimiter(2).wait_time(5, now=0.0)


def case_consume_over_capacity_class():
    return RateLimiter(1).consume(2, now=0.0)
```

```python cases_text_2.py
from minilib import render_table, slugify


class TestHelper:
    # PROBLEM: `Test...` class names are reserved (NOT USABLE)
    pass


def case_table_ragged_rows():
    return render_table([["a"], ["b", "c", "d"]], headers=["x"])
```
'''

W1_ROUND2 = '''```python cases_durations_3.py
from minilib import parse_duration, format_duration, UNIT_SECONDS


def case_units_table():
    return sorted(UNIT_SECONDS.items())


def case_decimal_compound():
    return parse_duration("1.5h30m")


def case_format_minutes_seconds():
    return [format_duration(119), format_duration(120), format_duration(121, compact=True)]
```
'''

W2_ROUND2 = '''```python cases_text_3.py
from minilib import render_table, wrap_cell, slugify


def case_wrap_exact_width():
    return [wrap_cell("abcde", width=5), wrap_cell("abcdef", width=5), wrap_cell(12345678901)]


def case_table_zero_padding():
    return render_table([["a", "bb"]], padding=0)


def case_slug_max_len_trailing_sep():
    return slugify("ab cd ef", max_len=3)


def case_table_header_rule_no_border():
    return render_table([["1"]], headers=["head"], border=False)
```
'''

REPLIES = {
    (0, 0): W1_DRAFT, (1, 0): W2_DRAFT,
    (0, 1): W1_ROUND1, (1, 1): W2_ROUND1,
    (0, 2): W1_ROUND2, (1, 2): W2_ROUND2,
}


class FakeLLM:
    """Install with FakeLLM(agent_module, run).install(); keeps a deterministic log of asks."""

    COST_PER_CALL = 0.002

    def __init__(self, agent, run):
        self.agent = agent
        self.run = run
        self.lock = threading.Lock()
        self.asks = []          # (kind, writer, k) in the order answered
        self.skips = []

    def reply_for(self, messages):
        text = messages[-1]["content"] if messages else ""
        if text.startswith("Read the task statement below"):
            m = re.search(r"repository at `(/[^`]+)`", text)
            return "layout", None, 0, "REPO: %s\nTESTS: tests/\n" % (m.group(1) if m else "unknown")
        writer = next((i for i, conv in enumerate(getattr(self.run, "writers", []) or []) if conv is messages), None)
        if writer is None:
            return "other", None, 0, "DONE"
        k = sum(1 for msg in messages if msg.get("role") == "user") - 1
        reply = REPLIES.get((writer, k))
        if reply is None:
            return "writer", writer, k, "Nothing more to add.\n\nDONE\n"
        if k >= 1:
            ids = re.findall(r"\[(m\d+)\]", text)
            if ids:
                reply = "SKIP: %s (equivalent for the requested behaviour)\n\n" % ids[-1] + reply
                with self.lock:
                    self.skips.append(ids[-1])
        return "writer", writer, k, reply

    def install(self):
        fake = self
        agent = self.agent

        def _attempts(llm, max_tokens, messages, effort=""):
            if llm.deadline - time.time() < 30:
                return None, None
            kind, writer, k, reply = fake.reply_for(messages)
            cost = fake.COST_PER_CALL
            with llm.lock:
                llm.spent += cost
                llm.last_call_cost = cost
                llm.calls += 1
                n = llm.calls
            with fake.lock:
                fake.asks.append([kind, writer, k])
            agent.log("[LLM] call %d: fake %s writer=%s k=%s, out=%d chars cost=$%.4f total=$%.4f" % (
                n, kind, None if writer is None else writer + 1, k, len(reply), cost, llm.spent))
            return reply, "stop"

        agent.LLM._attempts = _attempts
        return self
