# v2.14: back to the v2.12 agent

`codexv2.14版.py` is byte-identical to `codexv2.12_orig.py`.

## Scores (ridges-bench test-generation, personal/gpt-6-luna, one run each, reported by the user)

| task | v2.12 | v2.13 (mutants caught) |
|---|:---:|:---:|
| boltons__iterutils | pass | fail (5/8) |
| inflect__english-inflection | pass | pass (2/2) |
| jmespath__query-language | pass | fail (4/6) |
| loguru__logger-pipeline | pass | fail (2/5) |
| natsort__natural-ordering | pass | fail (1/2) |
| python-slugify__slug-options | pass | pass (3/3) |
| tenacity__retry-strategies | pass | fail (7/8) |
| textdistance__string-distances | pass | pass (4/4) |
| toolz__dict-helpers | pass | pass (4/4) |
| tqdm__meter-formatting | pass | pass (4/4) |
| **total** | **10/10** | **5/10** |

Every v2.13 failure is an uncaught mutant; no decoy failed.

## What went wrong in v2.13

The model endpoint is not reachable from the cloud container, so these checks run the agent with scripted
case files instead of model replies (`run_scripted.py`: the first writer's draft is the given case file, every
other reply is "DONE"). Everything else (recording, the change checker, rounds, final suite) is the real code.

1. v2.13's change checker refuses the fast path for any change inside a function that ran while the library
   was imported (`the changed function runs when the library is imported`) and sends it to the slow path,
   which re-imports the library and re-runs the cases. On natsort, whose key factories run at import, that
   moved 370 of 670 changes to the slow path:

   | natsort, same 19 cases | v2.12 | v2.13 | v2.13 without that check |
   |---|---:|---:|---:|
   | first sweep (changes judged) | 455 in 2 s | 415 in 9 s | 455 in 2 s |
   | slow-path checks / seconds | 160 / 17 s | 960 / 88 s | 158 / 13 s |
   | changes never judged at the end | 0 | 52 | 0 |

   With a real draft (hundreds of cases) every slow check costs far more, so many changes are never judged,
   never shown to the writers, and the suite ships with gaps. The fast path is safe without the check: a
   function that ran at import keeps results computed with the old code, so the fast path can only
   under-report a detection, never invent one.

2. The fast and slow verdicts agree with real pytest runs: `xcheck_mutants.py` applies each of the 670 natsort
   changes to the library and runs the final suite; the only disagreement is one change the agent rated as
   undetected that pytest detects.

3. On boltons, v2.12 and v2.13 behave the same under this scripted run (545 detected / 229 undetected), so
   the boltons, jmespath, loguru and tenacity failures may have further causes that only show with the real
   model. Rather than keep any of v2.13's other unvalidated changes, v2.14 returns to the agent that scored
   10/10.

## Reproduce

```
TG_BENCH=.../ridges-bench/test-generation python3 harness/ridges_local.py prepare natsort__natural-ordering
AGENT_TIMEOUT=400 python3.13 work/v214/run_scripted.py "$PWD/codexv2.14版.py" natsort__natural-ordering \
    work/v214/natsort_cases.py /tmp/out
python3.13 work/v214/xcheck_mutants.py /tmp/out python3.13
```
