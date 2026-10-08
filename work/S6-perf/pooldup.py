import importlib.util, threading, time, collections, os
here = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(here, "..", "..", "codexv2.12_orig.py")
spec = importlib.util.spec_from_file_location("agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

r = object.__new__(agent.Run)
r.pool_cv = threading.Condition(); r.pool_queue = []; r.pool_seq = 0; r.pool_threads = []
r.pool_stop = False; r.pool_paused = False; r.pool_busy = 0
r.mutant_status = {}; r.mutant_full = {}; r.servers = {}; r.func_feedback = {}; r.scope_files = []
r._scope_words = set(); r.deadline = time.time() + 1000
N = 200
r.mutants = {"m%d" % i: {"id": "m%d" % i, "func": "f", "file": "a.py", "module_level": False} for i in range(1, N + 1)}
calls = collections.Counter(); lock = threading.Lock()

def check_mutant(m, root, full):
    with lock:
        calls[m["id"]] += 1
    time.sleep(0.02)
    return "survived"
r.check_mutant = check_mutant
for i in range(4):
    t = threading.Thread(target=r._pool_worker, args=("w%d" % i,), daemon=True); t.start(); r.pool_threads.append(t)
r.pool_submit(list(r.mutants.values()), foreground=False)       # start_pool / refresh_mutants
time.sleep(0.1)
# execute() line 3739-3741 after round 1 and round 2
for _ in range(2):
    unchecked = [m for m in r.mutants.values() if m["id"] not in r.mutant_status]
    r.pool_submit(unchecked, foreground=False)
    time.sleep(0.1)
t0 = time.time()
while True:
    with r.pool_cv:
        if not r.pool_queue and not r.pool_busy:
            break
    time.sleep(0.01)
print("mutants", N, "checks", sum(calls.values()), "checked >1x:", sum(1 for v in calls.values() if v > 1),
      "max", max(calls.values()))
