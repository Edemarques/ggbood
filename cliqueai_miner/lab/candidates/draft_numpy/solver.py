"""
限时最大团求解器（给 CliqueAI 子网 netuid 83 的矿工用）。

算法：多邻域禁忌搜索（Multi-Neighborhood Tabu Search，MN/TS 的无权版本）
+ 基于使用频率的重启。适合验证者出的那种稠密随机图（密度约 0.7~0.8，
300~900 个点），在这种图上精确的分支限界在几秒内跑不完，局部搜索更有效。

每一步在三种动作里选：
  ADD  : 把一个与团内所有点都相邻的点加入团（选在候选集中度数最大的）
  SWAP : 加入一个只与团内 1 个点不相邻的点，同时踢掉那个点（被踢的点进入禁忌）
  DROP : 前两种都做不了时，随机删掉一个团内点（进入禁忌）
连续很多步没有提升就重启，起点优先选「被用得最少」的点，保证探索到图的不同区域。

对外只需要用 `solve(number_of_nodes, adjacency_list, time_budget, seed)`，
返回值保证是一个合法的 **极大团**（验证者要求不能再扩展，否则 0 分）。
"""

from __future__ import annotations

import random
import time

import numpy as np


def build_adjacency(number_of_nodes: int, adjacency_list: list[list[int]]) -> np.ndarray:
    adj = np.zeros((number_of_nodes, number_of_nodes), dtype=bool)
    for v, neighbors in enumerate(adjacency_list):
        if neighbors:
            adj[v, neighbors] = True
    adj |= adj.T  # 保证对称
    np.fill_diagonal(adj, False)
    return adj


def is_clique(adj: np.ndarray, clique: list[int]) -> bool:
    if len(set(clique)) != len(clique):
        return False
    idx = np.asarray(clique, dtype=np.int64)
    sub = adj[np.ix_(idx, idx)]
    return int(sub.sum()) == len(clique) * (len(clique) - 1)


def extend_to_maximal(adj: np.ndarray, clique: list[int], rng: random.Random) -> list[int]:
    """贪心补点直到不能再加，保证返回极大团（验证者的硬性要求）。"""
    clique = list(clique)
    n = adj.shape[0]
    if clique:
        cand = np.all(adj[clique], axis=0)
    else:
        cand = np.ones(n, dtype=bool)
    while True:
        pa = np.flatnonzero(cand)
        if pa.size == 0:
            return clique
        # 选在剩余候选里度数最大的点，平局随机
        deg = adj[np.ix_(pa, pa)].sum(axis=1)
        best = pa[deg == deg.max()]
        v = int(best[rng.randrange(best.size)])
        clique.append(v)
        cand &= adj[v]


class TabuCliqueSearch:
    def __init__(self, adj: np.ndarray, seed: int | None = None):
        self.adj = adj
        self.n = adj.shape[0]
        nonadj = ~adj
        np.fill_diagonal(nonadj, False)
        self.nonadj = nonadj
        self.nonadj_i = nonadj.astype(np.int16)
        self.rng = random.Random(seed)

    def run(self, deadline: float, max_stagnation: int | None = None, keep_best: int = 64):
        n, adj, nonadj, nonadj_i, rng = self.n, self.adj, self.nonadj, self.nonadj_i, self.rng
        if n == 0:
            return []
        if max_stagnation is None:
            max_stagnation = max(1000, 4 * n)

        in_c = np.zeros(n, dtype=bool)
        miss = np.zeros(n, dtype=np.int16)  # miss[v] = 团内与 v 不相邻的点数
        tabu = np.zeros(n, dtype=np.int64)  # tabu[v] > it 表示 v 暂时不能加入
        freq = np.zeros(n, dtype=np.int64)  # 每个点被加入的次数，用于重启选起点
        clique: list[int] = []

        best_size = 0
        bests: dict[tuple[int, ...], None] = {}  # 同样大小的不同最优团，用于多样性

        def add(v: int):
            nonlocal miss
            clique.append(v)
            in_c[v] = True
            miss += nonadj_i[v]
            freq[v] += 1

        def remove(u: int):
            nonlocal miss
            clique.remove(u)
            in_c[u] = False
            miss -= nonadj_i[u]

        def restart():
            for u in list(clique):
                remove(u)
            tabu[:] = 0
            if bests and rng.random() < 0.3:
                # 从某个最优团出发，随机丢掉一部分点，在附近继续搜
                base = list(rng.choice(list(bests)))
                rng.shuffle(base)
                for v in base[: max(1, len(base) // 2)]:
                    add(v)
            else:
                low = np.flatnonzero(freq == freq.min())
                add(int(low[rng.randrange(low.size)]))

        it = 0
        stagnation = 0
        restart()
        while True:
            it += 1
            if (it & 31) == 0 and time.perf_counter() >= deadline:
                break

            allowed = ~in_c & (tabu <= it)
            pa = np.flatnonzero((miss == 0) & allowed)
            if pa.size:
                # ADD：选在候选集内部度数最大的点（加入后留下的候选最多）
                if pa.size == 1:
                    v = int(pa[0])
                else:
                    deg = adj[np.ix_(pa, pa)].sum(axis=1)
                    top = pa[deg == deg.max()]
                    v = int(top[rng.randrange(top.size)])
                add(v)
                size = len(clique)
                if size > best_size:
                    best_size = size
                    bests = {tuple(sorted(clique)): None}
                    stagnation = 0
                    continue
                if size == best_size and len(bests) < keep_best:
                    bests[tuple(sorted(clique))] = None
                stagnation += 1
            else:
                om = np.flatnonzero((miss == 1) & allowed)
                if om.size:
                    # SWAP：加入 v，踢掉团里唯一与 v 不相邻的点 u
                    v = int(om[rng.randrange(om.size)])
                    u = int(np.flatnonzero(nonadj[v] & in_c)[0])
                    remove(u)
                    tabu[u] = it + 7 + om.size // 2 + rng.randrange(10)
                    add(v)
                elif clique:
                    # DROP：随机删一个点
                    u = clique[rng.randrange(len(clique))]
                    remove(u)
                    tabu[u] = it + 7 + rng.randrange(5)
                stagnation += 1

            if stagnation > max_stagnation or not clique:
                restart()
                stagnation = 0

        return [list(c) for c in bests]


def solve(
    number_of_nodes: int,
    adjacency_list: list[list[int]],
    time_budget: float = 3.0,
    seed: int | None = None,
) -> list[int]:
    """在 time_budget 秒内找尽量大的团，返回一个合法的极大团。"""
    start = time.perf_counter()
    rng = random.Random(seed)
    if number_of_nodes <= 0:
        return []
    adj = build_adjacency(number_of_nodes, adjacency_list)
    deadline = start + max(0.05, time_budget)

    candidates = TabuCliqueSearch(adj, seed=rng.randrange(1 << 30)).run(deadline)

    # 在同样大小的最优团里随机挑一个 → 提升「多样性」得分
    rng.shuffle(candidates)
    best: list[int] = []
    for c in candidates:
        c = extend_to_maximal(adj, c, rng)
        if is_clique(adj, c) and len(c) > len(best):
            best = c
    if not best:
        best = extend_to_maximal(adj, [], rng)
    return sorted(int(v) for v in best)
