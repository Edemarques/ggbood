"""numba kernels for the numba_pls clique solver (loaded by solver.py under a unique module name)."""
import warnings

import numpy as np
from numba import njit

_NOCACHE = globals().get("_NUMBA_PLS_NOCACHE", False)


def _jit(**kw):
    """njit(cache=True); falls back to an uncached njit if caching is unavailable."""

    def deco(f):
        if not _NOCACHE:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    return njit(cache=True, **kw)(f)
            except Exception:
                pass
        return njit(cache=False, **kw)(f)

    return deco


# --------------------------------------------------------------------------- scalars layout
S_K = 0        # clique size
S_N0 = 1       # |C0|
S_N1 = 2       # |C1|
S_BEST = 3     # best clique size seen
S_NPOOL = 4    # cliques in pool
S_PID = 5      # plateau id
S_OVL = 6      # number of vertices of the plateau-start clique still in K
S_NEWPL = 7    # 1 -> a new plateau phase must start
S_ITER = 8     # iterations done in current phase
S_PHASE = 9    # 0 random, 1 penalty, 2 degree
S_NPU = 10     # penalty updates done
S_STEPS = 11   # total steps
S_HASH = 12    # zobrist hash of K
S_LAST = 13    # last added vertex
S_BSTEP = 14   # step at which best was found
S_NDIST = 15   # distinct best cliques seen (reservoir)
S_TITER = 16   # total iterations (perturbations)
S_IMPIT = 17   # iteration of last best improvement / restart
S_LEN = 20

# params layout
P_IT0 = 0      # iterations of random phase
P_IT1 = 1      # iterations of penalty phase
P_IT2 = 2      # iterations of degree phase
P_PD = 3       # penalty delay
P_PENRESTART = 4  # penalty phase perturbation: 0 -> K={last added}, 1 -> K={random}
P_RESTART = 5  # >0: full restart after this many iterations without improvement
P_ADDDYN = 6   # >0: ADD ranked by degree inside C0 when |C0| <= this
P_PERT = 7     # perturbation tournament size (min miss among this many random vertices)
P_LEN = 8


@_jit()
def _rand(rs):
    x = rs[0]
    x ^= x >> np.uint64(12)
    x ^= x << np.uint64(25)
    x ^= x >> np.uint64(27)
    rs[0] = x
    return np.int64((x * np.uint64(2685821657736338717)) >> np.uint64(33))


@_jit()
def _build(n, lens, flat):
    A = np.zeros((n, n), dtype=np.uint8)
    pos = 0
    for v in range(n):
        for j in range(lens[v]):
            u = flat[pos]
            pos += 1
            if u >= 0 and u < n and u != v:
                A[v, u] = 1
                A[u, v] = 1
    deg = np.zeros(n, dtype=np.int32)
    nptr = np.zeros(n + 1, dtype=np.int32)
    for v in range(n):
        d = 0
        for u in range(n):
            d += A[v, u]
        deg[v] = d
        nptr[v + 1] = nptr[v] + (n - 1 - d)
    nidx = np.empty(max(nptr[n], 1), dtype=np.int32)
    for v in range(n):
        p = nptr[v]
        for u in range(n):
            if u != v and A[v, u] == 0:
                nidx[p] = u
                p += 1
    return A, deg, nptr, nidx


@_jit()
def _add(v, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob):
    # precondition: v in C0
    n0 = S[S_N0]
    n1 = S[S_N1]
    p = pos0[v]
    last = C0[n0 - 1]
    C0[p] = last
    pos0[last] = p
    pos0[v] = -1
    n0 -= 1
    k = S[S_K]
    inK[v] = 1
    posK[v] = k
    K[k] = v
    S[S_K] = k + 1
    S[S_HASH] ^= zob[v]
    S[S_LAST] = v
    for j in range(nptr[v], nptr[v + 1]):
        u = nidx[j]
        m = miss[u] + 1
        miss[u] = m
        if m == 1:
            p = pos0[u]
            last = C0[n0 - 1]
            C0[p] = last
            pos0[last] = p
            pos0[u] = -1
            n0 -= 1
            C1[n1] = u
            pos1[u] = n1
            n1 += 1
        elif m == 2:
            p = pos1[u]
            last = C1[n1 - 1]
            C1[p] = last
            pos1[last] = p
            pos1[u] = -1
            n1 -= 1
    S[S_N0] = n0
    S[S_N1] = n1


@_jit()
def _remove(u, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob):
    n0 = S[S_N0]
    n1 = S[S_N1]
    k = S[S_K] - 1
    p = posK[u]
    last = K[k]
    K[p] = last
    posK[last] = p
    posK[u] = -1
    inK[u] = 0
    S[S_K] = k
    S[S_HASH] ^= zob[u]
    C0[n0] = u
    pos0[u] = n0
    n0 += 1
    for j in range(nptr[u], nptr[u + 1]):
        w = nidx[j]
        m = miss[w] - 1
        miss[w] = m
        if m == 0:
            p = pos1[w]
            last = C1[n1 - 1]
            C1[p] = last
            pos1[last] = p
            pos1[w] = -1
            n1 -= 1
            C0[n0] = w
            pos0[w] = n0
            n0 += 1
        elif m == 1:
            C1[n1] = w
            pos1[w] = n1
            n1 += 1
    S[S_N0] = n0
    S[S_N1] = n1


@_jit()
def _init_state(n, S, K, posK, inK, miss, C0, pos0, C1, pos1, pen, remst, origst):
    for v in range(n):
        C0[v] = v
        pos0[v] = v
        pos1[v] = -1
        posK[v] = -1
        inK[v] = 0
        miss[v] = 0
        pen[v] = 0
        remst[v] = 0
        origst[v] = 0
    for i in range(S.shape[0]):
        S[i] = 0
    S[S_N0] = n
    S[S_NEWPL] = 1


@_jit()
def _record(S, K, pool, poolh, rs):
    k = S[S_K]
    h = S[S_HASH]
    npool = S[S_NPOOL]
    for i in range(npool):
        if poolh[i] == h:
            return
    S[S_NDIST] += 1
    cap = pool.shape[0]
    if npool < cap:
        slot = npool
        S[S_NPOOL] = npool + 1
    else:
        r = _rand(rs) % S[S_NDIST]
        if r >= cap:
            return
        slot = r
    poolh[slot] = h
    for i in range(k):
        pool[slot, i] = K[i]
    for i in range(k, pool.shape[1]):
        pool[slot, i] = -1


@_jit()
def _key2(phase, u, pen, deg):
    if phase == 0:
        return 0
    elif phase == 1:
        return -pen[u]
    return deg[u]


@_jit()
def _run(A, deg, nptr, nidx, zob, S, rs, K, posK, inK, miss, C0, pos0, C1, pos1,
         pen, remst, origst, pool, poolh, params, max_steps):
    n = A.shape[0]
    if n == 0:
        return
    adddyn = params[P_ADDDYN]
    tour = max(params[P_PERT], 1)
    stop = S[S_STEPS] + max_steps
    while S[S_STEPS] < stop:
        S[S_STEPS] += 1
        phase = S[S_PHASE]
        n0 = S[S_N0]
        if n0 > 0:
            # ---------------- ADD
            if phase == 0 and (adddyn == 0 or n0 > adddyn):
                v = C0[_rand(rs) % n0]
            else:
                bk = -(1 << 60)
                cnt = 0
                v = -1
                dyn = adddyn > 0 and n0 <= adddyn
                for i in range(n0):
                    u = C0[i]
                    key = _key2(phase, u, pen, deg)
                    if dyn:
                        g = 0
                        for j in range(n0):
                            g += A[u, C0[j]]
                        key += g << 20
                    if key > bk:
                        bk = key
                        v = u
                        cnt = 1
                    elif key == bk:
                        cnt += 1
                        if _rand(rs) % cnt == 0:
                            v = u
            _add(v, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
            S[S_NEWPL] = 1
            if S[S_K] > S[S_BEST]:
                S[S_BEST] = S[S_K]
                S[S_BSTEP] = S[S_STEPS]
                S[S_IMPIT] = S[S_TITER]
                S[S_NPOOL] = 0
                S[S_NDIST] = 0
                _record(S, K, pool, poolh, rs)
            continue
        # K is maximal here
        k = S[S_K]
        if k == S[S_BEST]:
            _record(S, K, pool, poolh, rs)
        if S[S_NEWPL] == 1:
            S[S_NEWPL] = 0
            S[S_PID] += 1
            pid = S[S_PID]
            for i in range(k):
                origst[K[i]] = pid
            S[S_OVL] = k
        pid = S[S_PID]
        if S[S_OVL] > 0 and S[S_N1] > 0:
            # ---------------- SWAP (plateau)
            n1 = S[S_N1]
            v = -1
            cnt = 0
            bk = -(1 << 60)
            for i in range(n1):
                u = C1[i]
                if remst[u] != pid:
                    key = _key2(phase, u, pen, deg)
                    if key > bk:
                        bk = key
                        v = u
                        cnt = 1
                    elif key == bk:
                        cnt += 1
                        if _rand(rs) % cnt == 0:
                            v = u
            if v >= 0:
                u = -1
                for i in range(k):
                    if A[v, K[i]] == 0:
                        u = K[i]
                        break
                _remove(u, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
                remst[u] = pid
                if origst[u] == pid:
                    S[S_OVL] -= 1
                _add(v, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
                continue
        # ---------------- end of iteration: penalties + perturbation
        if k >= n:
            return  # complete graph, nothing to do
        if phase == 1:
            for i in range(k):
                pen[K[i]] += 1
            S[S_NPU] += 1
            if S[S_NPU] % params[P_PD] == 0:
                for u in range(n):
                    if pen[u] > 0:
                        pen[u] -= 1
            if params[P_PENRESTART] == 0:
                v = S[S_LAST]
            else:
                v = _rand(rs) % n
            while S[S_K] > 0:
                _remove(K[S[S_K] - 1], S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
            _add(v, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
        elif params[P_RESTART] > 0 and S[S_TITER] - S[S_IMPIT] >= params[P_RESTART]:
            S[S_IMPIT] = S[S_TITER]
            v = _rand(rs) % n
            while S[S_K] > 0:
                _remove(K[S[S_K] - 1], S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
            _add(v, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
        else:
            v = -1
            bm = 1 << 30
            for _ in range(tour):
                x = _rand(rs) % n
                while inK[x] == 1:
                    x = _rand(rs) % n
                if miss[x] < bm:
                    bm = miss[x]
                    v = x
            i = 0
            while i < S[S_K]:
                u = K[i]
                if A[v, u] == 0:
                    _remove(u, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
                else:
                    i += 1
            _add(v, S, K, posK, inK, miss, C0, pos0, C1, pos1, nptr, nidx, zob)
        S[S_NEWPL] = 1
        S[S_ITER] += 1
        S[S_TITER] += 1
        if S[S_ITER] >= params[phase]:
            S[S_ITER] = 0
            ph = phase
            for _ in range(3):
                ph = (ph + 1) % 3
                if params[ph] > 0:
                    break
            S[S_PHASE] = ph


@_jit()
def _extend(A, deg, clique, k, rs):
    """Greedily extend clique[:k] to a maximal clique (most candidate-neighbours first)."""
    n = A.shape[0]
    cand = np.ones(n, dtype=np.uint8)
    for i in range(k):
        v = clique[i]
        cand[v] = 0
        for u in range(n):
            if A[v, u] == 0:
                cand[u] = 0
    out = np.empty(n, dtype=np.int64)
    for i in range(k):
        out[i] = clique[i]
    while True:
        best = -1
        bv = -1
        cnt = 0
        for u in range(n):
            if cand[u] == 1:
                d = 0
                for w in range(n):
                    if cand[w] == 1 and A[u, w] == 1:
                        d += 1
                if d > best:
                    best = d
                    bv = u
                    cnt = 1
                elif d == best:
                    cnt += 1
                    if _rand(rs) % cnt == 0:
                        bv = u
        if bv < 0:
            break
        out[k] = bv
        k += 1
        for u in range(n):
            if A[bv, u] == 0:
                cand[u] = 0
        cand[bv] = 0
    return out[:k].copy()


@_jit()
def _is_maximal_clique(A, c):
    n = A.shape[0]
    k = c.shape[0]
    if k == 0:
        return n == 0
    for i in range(k):
        if c[i] < 0 or c[i] >= n:
            return False
        for j in range(i + 1, k):
            if c[i] == c[j] or A[c[i], c[j]] == 0:
                return False
    for u in range(n):
        ok = True
        for i in range(k):
            if c[i] == u or A[u, c[i]] == 0:
                ok = False
                break
        if ok:
            return False
    return True


DEFAULT_PARAMS = np.array([30, 0, 100, 2, 1, 0, 0, 32], dtype=np.int64)
POOL_CAP = 64


