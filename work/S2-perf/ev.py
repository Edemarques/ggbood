import time
NS = {"__builtins__": {}, "float": float, "set": set, "frozenset": frozenset, "complex": complex}
srcs = {
    "small": "[1, 2, 'abc']",
    "medium": repr({"k%d" % i: [i, "v%d" % i, (i, None)] for i in range(40)}),
    "large": repr([{"name": "item%d" % i, "vals": [i * 1.5, i, "x" * 5]} for i in range(150)]),
}
for name, s in srcs.items():
    code = compile(s, "<string>", "eval")
    n = 2000
    t = time.perf_counter()
    for _ in range(n):
        eval(s, dict(NS))
    a = (time.perf_counter() - t) / n
    t = time.perf_counter()
    for _ in range(n):
        eval(code, dict(NS))
    b = (time.perf_counter() - t) / n
    assert eval(code, dict(NS)) == eval(s, dict(NS))
    print("%-7s len %5d  eval(str) %.1f us  eval(code) %.1f us" % (name, len(s), a * 1e6, b * 1e6))
