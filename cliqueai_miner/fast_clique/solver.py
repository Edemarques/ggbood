"""
Time-limited maximum clique solver for the CliqueAI subnet (netuid 83) miner.

The search itself is C++ (mc.cpp, compiled once at import time and loaded with
ctypes): a bitset implementation of DLS-MC style local search (random expansion,
plateau swaps with per-phase tabu, and a perturbation that mixes "add a random
vertex" with "add a vertex missing exactly two / three clique neighbours").
Per-vertex miss counts are kept as bit-sliced counters over uint64 words,
so one move costs O(log k * n/64) word operations (~10M moves/s at n=900).  It keeps a
pool of distinct best-size cliques and returns a seed-chosen one (diversity).

Public API:
    solve(number_of_nodes, adjacency_list, time_budget, seed=None) -> list[int]

The returned clique is always valid and maximal; the whole call (input
conversion included) stays within time_budget.
"""

from __future__ import annotations

import ctypes
import hashlib
import itertools
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "mc.cpp")
_FLAGS = ["-O3", "-march=native", "-std=c++17", "-shared", "-fPIC", "-pthread"]

# Search parameters for mc_solve: algo 2 = DLS-style search on bit-sliced miss counters.
# params: [1]=perturb mode, [2]=greedy expand, [3]=P(perturb with a miss==2 vertex),
#         [4]=P(perturb with a miss==3 vertex), [5]=P(miss==4), [6]=perturbation tabu tenure.
_ALGO = 2
_PARAMS = (1.0, 0.0, 0.0, 0.6, 0.3, 0.0, 0.0)

# Seconds kept in reserve for the work after the C++ search returns.
_RESERVE = 0.05


def _cpu_tag() -> bytes:
    """Identify the CPU so a -march=native build is never reused on another CPU."""
    tag = [sys.platform, os.uname().machine if hasattr(os, "uname") else ""]
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith(("model name", "flags")):
                    tag.append(line.strip())
                if len(tag) >= 4:
                    break
    except OSError:
        pass
    return "\n".join(tag).encode()


def _compile(dst: str, cxx: str) -> None:
    d = os.path.dirname(dst)
    fd, tmp = tempfile.mkstemp(prefix=".mc_build_", suffix=".so", dir=d)
    os.close(fd)
    try:
        r = subprocess.run([cxx, *_FLAGS, "-o", tmp, _SRC], capture_output=True, text=True)
        if r.returncode != 0:
            raise ImportError(f"cpp_tabu solver: compiling mc.cpp failed:\n{r.stderr[-4000:]}")
        os.replace(tmp, dst)  # atomic: concurrent importers see either nothing or a full file
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_library() -> ctypes.CDLL:
    cxx = os.environ.get("CXX") or shutil.which("g++")
    with open(_SRC, "rb") as f:
        src = f.read()
    key = hashlib.sha256(src + " ".join(_FLAGS).encode() + _cpu_tag()).hexdigest()[:20]
    name = f"mc_{key}.so"
    dirs = [os.path.join(_HERE, "__mccache__"), os.path.join(tempfile.gettempdir(), "cliqueai_mc_cache")]
    last_err: Exception | None = None
    for d in dirs:
        path = os.path.join(d, name)
        if os.path.exists(path):
            try:
                return ctypes.CDLL(path)
            except OSError as e:  # corrupted / foreign file: rebuild below
                last_err = e
        if cxx is None:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            _compile(path, cxx)
            return ctypes.CDLL(path)
        except ImportError:
            raise
        except OSError as e:
            last_err = e
            continue
    if cxx is None:
        raise ImportError("cpp_tabu solver needs a C++ compiler: install g++ (e.g. `apt install g++`).")
    raise ImportError(f"cpp_tabu solver: could not build/load mc.cpp: {last_err}")


_lib = _load_library()
_mc_solve = _lib.mc_solve
_mc_solve.restype = ctypes.c_int
_mc_solve.argtypes = [
    ctypes.c_int,  # n
    ctypes.c_void_p,  # offsets int64[n+1]
    ctypes.c_void_p,  # neighbours int32[]
    ctypes.c_double,  # seconds
    ctypes.c_ulonglong,  # seed
    ctypes.c_int,  # algo
    ctypes.c_void_p,  # params double[]
    ctypes.c_int,  # nparams
    ctypes.c_int,  # target size (0 = none)
    ctypes.c_void_p,  # out int32[n]
    ctypes.c_void_p,  # stats double[8]
    ctypes.c_void_p,  # trace
    ctypes.c_int,  # trace capacity
]


def _to_csr(n: int, adjacency_list) -> tuple[np.ndarray, np.ndarray]:
    rows = list(adjacency_list[:n]) if len(adjacency_list) > n else list(adjacency_list)
    rows += [[]] * (n - len(rows))
    lens = np.fromiter(map(len, rows), dtype=np.int64, count=n)
    off = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(lens, out=off[1:])
    total = int(off[-1])
    flat = np.fromiter(itertools.chain.from_iterable(rows), dtype=np.int64, count=total)
    # out-of-range ids become -1 and are ignored by the C++ side
    flat[(flat < 0) | (flat >= n)] = -1
    return off, flat.astype(np.int32)


def _check(n: int, adjacency_list, clique: list[int]) -> bool:
    """Independent check with the validator's semantics: valid clique and maximal."""
    if not clique or len(set(clique)) != len(clique):
        return False
    if any(v < 0 or v >= n for v in clique):
        return False
    cs = set(clique)
    nb = [set(adjacency_list[v]) for v in clique]
    for i, v in enumerate(clique):
        for j in range(i + 1, len(clique)):
            if clique[j] not in nb[i]:
                return False
    common = set(nb[0])
    for s in nb[1:]:
        common &= s
    common -= cs
    # a vertex adjacent to every clique vertex (in its own list as well) would extend it
    for c in common:
        if c < n and cs.issubset(adjacency_list[c]):
            return False
    return True


def _python_fallback(n: int, adjacency_list, rng: random.Random) -> list[int]:
    nbs = [set(adjacency_list[v]) if v < len(adjacency_list) else set() for v in range(n)]
    order = list(range(n))
    rng.shuffle(order)
    order.sort(key=lambda v: -len(nbs[v]))
    clique: list[int] = []
    cand = set(range(n))
    for v in order:
        if v in cand:
            clique.append(v)
            cand &= nbs[v]
            cand.discard(v)
    return clique


def solve(
    number_of_nodes: int,
    adjacency_list: list[list[int]],
    time_budget: float = 3.0,
    seed: int | None = None,
) -> list[int]:
    """Return a large maximal clique found within time_budget seconds."""
    t0 = time.perf_counter()
    n = int(number_of_nodes)
    if n <= 0:
        return []
    if seed is None:
        seed = int.from_bytes(os.urandom(8), "little")
    seed = int(seed) & 0xFFFFFFFFFFFFFFFF
    budget = float(time_budget)

    off, flat = _to_csr(n, adjacency_list)
    out = np.zeros(n, dtype=np.int32)
    stats = np.zeros(8, dtype=np.float64)
    params = np.asarray(_PARAMS, dtype=np.float64)
    reserve = min(_RESERVE + 2e-6 * n, 0.25 * budget)
    seconds = max(0.0, budget - reserve - (time.perf_counter() - t0))
    k = _mc_solve(
        n,
        off.ctypes.data,
        flat.ctypes.data,
        seconds,
        seed,
        _ALGO,
        params.ctypes.data,
        len(params),
        0,
        out.ctypes.data,
        stats.ctypes.data,
        None,
        0,
    )
    clique = [int(v) for v in out[:k]]
    if not _check(n, adjacency_list, clique):
        clique = _python_fallback(n, adjacency_list, random.Random(seed))
    return clique
