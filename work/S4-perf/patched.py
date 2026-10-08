import os, sys, shutil, subprocess, importlib.util, time
HOME = os.path.expanduser("~/tgopt/S4-perf")
SRC = "/mnt/c/Users/Administrator/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/scratch-workspaces/e275ff1c-f08a-41d9-8870-2551a0569dde/d8f32c1c-bde9-4d74-a4d5-921ffbabcb67/scratch-2026-10-08-2e3d58/codexv2.12_orig.py"
text = open(SRC, encoding="utf-8").read()

def rep(old, new):
    global text
    assert text.count(old) == 1, old[:80]
    text = text.replace(old, new)

# fix 1: keep_previous_cases sees the source add_case_file will store
rep("""        good = [c for c in self.case_order.get(module, []) if "%s::%s" % (module, c) in self.records]
        try:
            new_cases = {n.name for n in ast.parse(new_source).body if isinstance(n, ast.FunctionDef)}
        except SyntaxError:
            new_cases = set()
""", """        good = [c for c in self.case_order.get(module, []) if "%s::%s" % (module, c) in self.records]
        fixed, _ = _repair_source(_adopt_test_names(new_source))
        if not fixed.strip() or not re.search(r"(?m)^def case_\\w+\\s*\\(", fixed):
            return None
        new_cases = {n.name for n in ast.parse(fixed).body if isinstance(n, ast.FunctionDef)}
""")
# fix 2 + 3: parallel record runs, skip re-recorded mended cases
rep("""        first = self._record(todo, trace=True, reverse=False, seed="0")
        second = self._record(todo, trace=False, reverse=True, seed="4217", clock_shift=CLOCK_SHIFT)
        third = self._record(todo, trace=False, reverse=False, seed="91", clock_shift=CLOCK_SHIFT / 37.0,
                             absent=True)
""", """        runs = (dict(trace=True, reverse=False, seed="0"),
                dict(trace=False, reverse=True, seed="4217", clock_shift=CLOCK_SHIFT),
                dict(trace=False, reverse=False, seed="91", clock_shift=CLOCK_SHIFT / 37.0, absent=True))
        if _available_cpus() >= 3:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(len(runs)) as ex:
                futs = [ex.submit(self._record, todo, **kw) for kw in runs]
                first, second, third = [f.result() for f in futs]
        else:
            first, second, third = [self._record(todo, **kw) for kw in runs]
""")
rep("""        if not mended:
            again = []
""", """        again = []
        if not mended:
""")
rep("""            if name in self.module_problems:
                continue
            a, b, c = first.get(key), second.get(key), third.get(key)""",
"""            if name in self.module_problems or (name in again and case in self.case_order.get(name, ())):
                continue
            a, b, c = first.get(key), second.get(key), third.get(key)""")
p = os.path.join(HOME, "agent_patched.py")
open(p, "w", encoding="utf-8").write(text)
print("written", p)
