"""
在子进程里跑的函数（ProcessPoolExecutor 用）。这里只能依赖 solver，不能 import bittensor。
"""

import os
import random
import time

from fast_clique.solver import solve


def warmup() -> int:
    """在每个子进程里先跑一次很小的图，把编译 / JIT 的开销提前付掉。"""
    solve(3, [[1, 2], [0, 2], [0, 1]], 0.05, 0)
    return os.getpid()


def run_solve(number_of_nodes: int, adjacency_list: list[list[int]], deadline: float, seed: int) -> list[int]:
    """deadline 是 time.monotonic() 的绝对时间（Linux 上各进程共用同一个时钟），排队等待的时间也算在内。"""
    budget = deadline - time.monotonic()
    if budget < 0.05:
        return greedy_maximal(number_of_nodes, adjacency_list, seed)
    return solve(number_of_nodes, adjacency_list, budget, seed)


def greedy_maximal(number_of_nodes: int, adjacency_list: list[list[int]], seed: int | None = None) -> list[int]:
    """兜底用的纯 Python 贪心：每次加入候选集里度数最大的点，保证返回极大团。"""
    if number_of_nodes <= 0:
        return []
    rng = random.Random(seed)
    neighbors = [set(nb) - {v} for v, nb in enumerate(adjacency_list[:number_of_nodes])]
    neighbors += [set() for _ in range(number_of_nodes - len(neighbors))]
    for v, nb in enumerate(neighbors):  # 保证对称
        for u in nb:
            if u < number_of_nodes:
                neighbors[u].add(v)
    candidates = set(range(number_of_nodes))
    clique = []
    while candidates:
        best = max(len(neighbors[v] & candidates) for v in candidates)
        v = rng.choice([v for v in candidates if len(neighbors[v] & candidates) == best])
        clique.append(v)
        candidates &= neighbors[v]
    return clique
