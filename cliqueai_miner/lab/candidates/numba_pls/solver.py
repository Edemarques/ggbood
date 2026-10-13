"""
Time-limited maximum clique solver (CliqueAI, netuid 83) -- numba Phased Local Search.

Algorithm (kernels in pls_kernels.py): Phased Local Search (Pullan 2006) / DLS-MC style
  * iterative improvement: ADD a vertex of C0 (adjacent to the whole clique)
  * plateau search: SWAP in a vertex of C1 (adjacent to all but one clique vertex);
    vertices swapped out may not come back during the same plateau, the plateau ends
    when no vertex of the starting clique is left
  * perturbation after every expand/plateau iteration
  * phases with different selection rules (random / penalty / vertex degree)
A pool of distinct maximal cliques of the best size is kept; the answer is a seed-chosen
member of the pool, re-verified and greedily extended to a maximal clique.

Public API:  solve(number_of_nodes, adjacency_list, time_budget, seed=None) -> list[int]
"""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import os
import sys
import time

import numpy as np

try:
    import numba  # noqa: F401
except Exception as exc:  # pragma: no cover
    raise ImportError("numba_pls solver requires numba (pip install numba)") from exc

_HERE = os.path.dirname(os.path.abspath(__file__))
_KPATH = os.path.join(_HERE, "pls_kernels.py")


def _load_kernels(nocache: bool = False):
    """Import pls_kernels.py under a stable, importable module name (needed by numba's cache)."""
    with open(_KPATH, "rb") as f:
        tag = hashlib.sha1(f.read()).hexdigest()[:16]
    name = f"_numba_pls_kernels_{tag}" + ("_nocache" if nocache else "")
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(name, _KPATH)
    mod = importlib.util.module_from_spec(spec)
    if nocache:
        mod.__dict__["_NUMBA_PLS_NOCACHE"] = True
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


_K = _load_kernels()
S_STEPS = _K.S_STEPS
S_BEST = _K.S_BEST
DEFAULT_PARAMS = _K.DEFAULT_PARAMS
POOL_CAP = _K.POOL_CAP


class _Solver:
    def __init__(self, n, adjacency_list, seed, params=None, kern=None):
        K_ = self.k_ = kern if kern is not None else _K
        self.n = n
        lens = np.fromiter(map(len, adjacency_list), dtype=np.int64, count=n)
        tot = int(lens.sum())
        flat = np.fromiter(itertools.chain.from_iterable(adjacency_list), dtype=np.int64, count=tot)
        self.A, self.deg, self.nptr, self.nidx = K_._build(n, lens, flat)
        g = np.random.default_rng(np.random.SeedSequence(seed))
        self.zob = g.integers(-(1 << 62), 1 << 62, size=max(n, 1), dtype=np.int64)
        self.rs = np.array([int(g.integers(1, 1 << 62))], dtype=np.uint64)
        self.pick_rng = np.random.default_rng(g.integers(0, 1 << 62))
        self.params = K_.DEFAULT_PARAMS.copy() if params is None else np.asarray(params, dtype=np.int64)
        m = max(n, 1)
        i32 = np.int32
        self.S = np.zeros(K_.S_LEN, dtype=np.int64)
        self.K = np.zeros(m, dtype=i32)
        self.posK = np.zeros(m, dtype=i32)
        self.inK = np.zeros(m, dtype=np.uint8)
        self.miss = np.zeros(m, dtype=i32)
        self.C0 = np.zeros(m, dtype=i32)
        self.pos0 = np.zeros(m, dtype=i32)
        self.C1 = np.zeros(m, dtype=i32)
        self.pos1 = np.zeros(m, dtype=i32)
        self.pen = np.zeros(m, dtype=i32)
        self.remst = np.zeros(m, dtype=np.int64)
        self.origst = np.zeros(m, dtype=np.int64)
        self.pool = np.full((K_.POOL_CAP, m), -1, dtype=i32)
        self.poolh = np.zeros(K_.POOL_CAP, dtype=np.int64)
        K_._init_state(n, self.S, self.K, self.posK, self.inK, self.miss, self.C0, self.pos0,
                       self.C1, self.pos1, self.pen, self.remst, self.origst)

    def run(self, steps):
        self.k_._run(self.A, self.deg, self.nptr, self.nidx, self.zob, self.S, self.rs, self.K, self.posK,
                     self.inK, self.miss, self.C0, self.pos0, self.C1, self.pos1, self.pen,
                     self.remst, self.origst, self.pool, self.poolh, self.params, steps)

    def run_until(self, deadline, on_improve=None):
        steps = 256
        last_best = -1
        S = self.S
        while True:
            t = time.perf_counter()
            rem = deadline - t
            if rem <= 0:
                break
            self.run(steps)
            t2 = time.perf_counter()
            dt = t2 - t
            if on_improve is not None and S[S_BEST] != last_best:
                last_best = int(S[S_BEST])
                on_improve(t2, last_best, int(S[S_STEPS]))
            if S[self.k_.S_K] >= self.n:
                break
            rate = steps / max(dt, 1e-6)
            rem = deadline - t2
            target = min(0.01, 0.5 * rem)
            steps = int(max(64, min(rate * target, steps * 4)))

    def result(self):
        K_ = self.k_
        rs = self.rs
        A = self.A
        npool = int(self.S[K_.S_NPOOL])
        best = None
        for i in self.pick_rng.permutation(npool):
            row = self.pool[i]
            k = int(np.count_nonzero(row >= 0))
            c = K_._extend(A, self.deg, row[:k].astype(np.int64), k, rs)
            if K_._is_maximal_clique(A, c):
                best = c
                break
        k = int(self.S[K_.S_K])
        if best is None or k > len(best):
            cur = K_._extend(A, self.deg, self.K[:k].astype(np.int64), k, rs)
            if K_._is_maximal_clique(A, cur) and (best is None or len(cur) > len(best)):
                best = cur
        if best is None:
            best = K_._extend(A, self.deg, np.zeros(0, dtype=np.int64), 0, rs)
        return sorted(int(v) for v in best)


SAFETY = 0.04


def _solve(n, adjacency_list, time_budget, seed, params, kern):
    t0 = time.perf_counter()
    n = int(n)
    if n <= 0:
        return []
    if n == 1:
        return [0]
    deadline = t0 + max(0.0, float(time_budget) - SAFETY)
    s = _Solver(n, adjacency_list, seed, params, kern)
    s.run_until(deadline)
    return s.result()


def solve(number_of_nodes: int, adjacency_list: list[list[int]], time_budget: float = 3.0,
          seed: int | None = None, params=None) -> list[int]:
    return _solve(number_of_nodes, adjacency_list, time_budget, seed, params, _K)


def _warmup(kern):
    """Compile / load every kernel now (import time), so solve() never pays JIT cost."""
    for n, adj, seed in ((3, [[1, 2], [0, 2], [0, 1]], 0),
                         (6, [[1, 2], [0, 2], [0, 1], [4], [3, 5], [4]], 1)):
        s = _Solver(n, adj, seed, None, kern)
        s.run(200)
        r = s.result()
        if not r:
            raise RuntimeError("warm-up produced an empty clique")
    _solve(3, [[1, 2], [0, 2], [0, 1]], 0.0, 0, None, kern)


try:
    _warmup(_K)
except Exception:
    # a stale / foreign numba cache could not be loaded: recompile without caching
    _K = _load_kernels(nocache=True)
    _warmup(_K)
