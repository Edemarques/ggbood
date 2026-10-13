import importlib.util, random, time, sys, os
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('cand_solver', os.path.join(HERE, 'solver.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def check(n, adj, c):
    A = np.zeros((n, n), dtype=bool)
    for u, nb in enumerate(adj):
        for v in nb:
            if v != u: A[u, v] = A[v, u] = True
    if n == 0: return c == []
    if not c or len(set(c)) != len(c) or not all(isinstance(v, int) and 0 <= v < n for v in c): return False
    cc = np.array(c)
    if A[np.ix_(cc, cc)].sum() != len(c) * (len(c) - 1): return False
    if np.any(np.all(A[cc], axis=0)): return False
    return True

rng = random.Random(1)
bad = 0; worst = 0.0
cases = [(0, []), (1, [[]]), (2, [[], []]), (2, [[1], [0]]), (3, [[1, 1, 1], [0, 0], []]), (3, [[0, 1], [1, 0], [2]])]
for t in range(300):
    n = rng.choice([1, 2, 3, 4, 5, 8, 13, 30, 64, 65, 100, 200])
    p = rng.choice([0.0, 0.05, 0.3, 0.7, 0.95, 1.0])
    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < p:
                adj[i].append(j); adj[j].append(i)
                if rng.random() < 0.05: adj[i].append(j)  # duplicates
    cases.append((n, adj))
for i, (n, adj) in enumerate(cases):
    b = rng.choice([0.0, 0.01, 0.05, 0.2])
    t = time.perf_counter()
    c = m.solve(n, adj, b, rng.randrange(100) if i % 3 else None)
    dt = time.perf_counter() - t - b
    worst = max(worst, dt)
    if dt > 0.02: print("SLOW", i, n, b, round(dt, 3), flush=True)
    if not check(n, adj, c):
        bad += 1; print('BAD', n, c)
print('cases', len(cases), 'bad', bad, 'worst overrun', round(worst, 4))
# diversity check on a graph with many max cliques
n = 300; g = np.random.default_rng(5); U = np.triu(g.random((n, n)) < 0.735, 1); U = U | U.T
adj = [list(np.flatnonzero(U[i])) for i in range(n)]
adj = [[int(x) for x in a] for a in adj]
res = [tuple(m.solve(n, adj, 0.3, s)) for s in range(8)]
print('sizes', [len(r) for r in res], 'distinct', len(set(res)))
print('same seed reproducible?', m.solve(n, adj, 0.2, 3) == m.solve(n, adj, 0.2, 3))
