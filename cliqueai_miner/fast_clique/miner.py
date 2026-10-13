"""
CliqueAI 矿工：沿用官方 Miner（钱包、axon、黑名单、优先级都不变），只替换求解部分。

- 求解放到独立子进程池里跑，不阻塞 axon 的事件循环，同时来多个验证者的题也能并行处理；
- 时间预算根据验证者给的 timeout 计算，留出网络传输的余量；
- 机器空闲时，同一道题用不同随机种子并行跑多路，取最大的团（同样大小的随机挑一个）。

可用环境变量调整：
  CLIQUE_WORKERS         子进程个数，默认 = CPU 核数
  CLIQUE_PARALLEL_SEEDS  每道题最多并行跑几路，默认 = CLIQUE_WORKERS
  CLIQUE_TIME_FRACTION   用掉 timeout 的比例，默认 0.9
  CLIQUE_SAFETY_SECONDS  再额外留出的秒数，默认 1.5
"""

import asyncio
import multiprocessing as mp
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor

import bittensor as bt
from CliqueAI.graph.codec import GraphCodec
from CliqueAI.miner import Miner
from CliqueAI.protocol import MaximumCliqueOfLambdaGraph

from fast_clique import worker

WORKERS = max(1, int(os.environ.get("CLIQUE_WORKERS", os.cpu_count() or 1)))
PARALLEL_SEEDS = max(1, int(os.environ.get("CLIQUE_PARALLEL_SEEDS", WORKERS)))
TIME_FRACTION = float(os.environ.get("CLIQUE_TIME_FRACTION", 0.9))
SAFETY_SECONDS = float(os.environ.get("CLIQUE_SAFETY_SECONDS", 1.5))
DEFAULT_TIMEOUT = 6.0  # 验证者没给 timeout 时按最短的时限算


def compute_time_budget(synapse: MaximumCliqueOfLambdaGraph) -> float:
    timeout = float(synapse.timeout) if synapse.timeout else DEFAULT_TIMEOUT
    # dendrite.nonce 是验证者发请求时的 time_ns，用来估计路上已经花掉的时间；
    # 两边时钟可能不准，所以只取 0~1 秒之间的值。
    elapsed = 0.0
    nonce = getattr(synapse.dendrite, "nonce", None) if synapse.dendrite else None
    if nonce:
        elapsed = min(max(time.time() - nonce / 1e9, 0.0), 1.0)
    return max(0.3, timeout * TIME_FRACTION - SAFETY_SECONDS - elapsed)


class FastMiner(Miner):
    def __init__(self, config=None):
        super().__init__(config=config)
        # 用 spawn 而不是 fork：主进程里有 bittensor 的线程，fork 可能死锁
        self.pool = ProcessPoolExecutor(
            max_workers=WORKERS, mp_context=mp.get_context("spawn"), initializer=worker.warmup
        )
        self.inflight = 0
        t = time.monotonic()
        # 同时提交 WORKERS 个任务，逼所有子进程都启动并完成预热
        for f in [self.pool.submit(time.sleep, 0.5) for _ in range(WORKERS)]:
            f.result()
        bt.logging.info(f"Solver pool ready: {WORKERS} workers in {time.monotonic() - t:.1f}s")

    async def forward_graph(
        self, synapse: MaximumCliqueOfLambdaGraph
    ) -> MaximumCliqueOfLambdaGraph:
        start = time.monotonic()
        deadline = start + compute_time_budget(synapse)

        codec = GraphCodec()
        adjacency_list = codec.matrix_to_list(codec.decode_matrix(synapse.encoded_matrix))
        n = len(adjacency_list)
        if n != synapse.number_of_nodes:
            bt.logging.warning(f"number_of_nodes={synapse.number_of_nodes} but matrix has {n} rows; using {n}")

        runs = max(1, min(PARALLEL_SEEDS, WORKERS - self.inflight))
        self.inflight += runs
        try:
            loop = asyncio.get_running_loop()
            futures = [
                loop.run_in_executor(
                    self.pool, worker.run_solve, n, adjacency_list, deadline, random.getrandbits(31)
                )
                for _ in range(runs)
            ]
            results = await asyncio.gather(*futures, return_exceptions=True)
        finally:
            self.inflight -= runs

        cliques = []
        for r in results:
            if isinstance(r, BaseException):
                bt.logging.error(f"Solver failed: {r!r}")
            elif r:
                cliques.append([int(v) for v in r])
        if not cliques:
            cliques = [worker.greedy_maximal(n, adjacency_list)]

        best_size = max(len(c) for c in cliques)
        maximum_clique = random.choice([c for c in cliques if len(c) == best_size])
        bt.logging.info(
            f"uuid={synapse.uuid} n={n} timeout={synapse.timeout} runs={runs} "
            f"sizes={sorted(len(c) for c in cliques)} -> {best_size} in {time.monotonic() - start:.2f}s"
        )
        synapse.maximum_clique = maximum_clique
        return synapse

    def __exit__(self, exc_type, exc_value, traceback):
        super().__exit__(exc_type, exc_value, traceback)
        self.pool.shutdown(wait=False, cancel_futures=True)


def main():
    with FastMiner() as miner:
        bt.logging.info("FastMiner has started running.")
        while not miner.should_exit:
            time.sleep(1)
        bt.logging.info("FastMiner is exiting.")
