import inspect, time
def case_a():
    return 1
def case_b(tmp_path):
    return 1
for f in (case_a, case_b):
    n = 20000
    t = time.perf_counter()
    for _ in range(n):
        "tmp_path" in inspect.signature(f).parameters
    print(f.__name__, "%.2f us" % ((time.perf_counter() - t) / n * 1e6))
