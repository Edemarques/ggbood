"""
Benchmark one solver module.

  taskset -c 2 python3 bench.py --solver cand/x/solver.py [--budgets 1,4] [--seeds 0,1,2] [--graphs a,b] [--json out.json]

The module must expose solve(number_of_nodes, adjacency_list, time_budget, seed) -> list[int].
Each result is checked: valid clique, maximal (no vertex can extend it), wall time <= budget + 0.25s.
"""
import argparse, importlib.util, json, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

def load_graph(path):
    with open(path) as f:
        n, m = map(int, f.readline().split())
        adj = [[] for _ in range(n)]
        for line in f:
            u, v = map(int, line.split())
            adj[u].append(v); adj[v].append(u)
    return n, adj

def check(n, adj, clique):
    if not clique or len(set(clique)) != len(clique) or not all(0 <= v < n for v in clique):
        return "bad-vertices"
    A = np.zeros((n, n), dtype=bool)
    for u, nb in enumerate(adj):
        A[u, nb] = True
    c = np.asarray(clique)
    if A[np.ix_(c, c)].sum() != len(c) * (len(c) - 1):
        return "not-a-clique"
    if np.any(np.all(A[c], axis=0)):
        return "not-maximal"
    return "ok"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", required=True)
    ap.add_argument("--budgets", default="1,4")
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--graphs", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--graphdir", default=os.path.join(HERE, "graphs"))
    a = ap.parse_args()

    spec = importlib.util.spec_from_file_location("cand_solver", a.solver)
    mod = importlib.util.module_from_spec(spec)
    t = time.perf_counter(); spec.loader.exec_module(mod)
    t_import = time.perf_counter() - t
    t = time.perf_counter(); mod.solve(3, [[1, 2], [0, 2], [0, 1]], 0.2, 0)
    t_warm = time.perf_counter() - t
    print(f"import {t_import:.2f}s  warmup {t_warm:.2f}s", flush=True)

    names = a.graphs.split(",") if a.graphs else sorted(f[:-4] for f in os.listdir(a.graphdir))
    budgets = [float(b) for b in a.budgets.split(",")]
    seeds = [int(s) for s in a.seeds.split(",")]
    results = {"import": t_import, "warmup": t_warm, "runs": []}
    for name in names:
        n, adj = load_graph(os.path.join(a.graphdir, name + ".txt"))
        row = [f"{name:<12}"]
        for b in budgets:
            sizes, worst = [], 0.0
            for s in seeds:
                t = time.perf_counter()
                c = mod.solve(n, adj, b, s)
                dt = time.perf_counter() - t
                st = check(n, adj, c)
                if dt > b + 0.25:
                    st = st + f"|OVERTIME {dt:.2f}s"
                worst = max(worst, dt)
                sizes.append(len(c) if st == "ok" else f"{len(c)}!{st}")
                results["runs"].append({"graph": name, "budget": b, "seed": s, "size": len(c), "status": st, "time": dt})
            row.append(f"{b:g}s={sizes} (max {worst:.2f}s)")
        print("  ".join(row), flush=True)
    if a.json:
        json.dump(results, open(a.json, "w"), indent=1)

if __name__ == "__main__":
    main()
