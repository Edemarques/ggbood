# CliqueAI（Bittensor 子网 83）矿工：快速最大团求解器

用一个更强、并且有时间控制的求解器，替换官方矿工里的 `networkx` 近似算法（`CliqueAI/miner.py` 里的 `networkx_algorithm`）。

## 当前进度

| 步骤 | 状态 |
|---|---|
| 1. 测试环境：测试图、对比脚本 `lab/bench.py` | ✅ |
| 2. 候选求解器：C++ 禁忌/DLS 局部搜索、numba PLS 局部搜索 | ✅ |
| 3. 统一对比，选出最好的方案 | ✅ 选定 **C++（`fast_clique/solver.py` + `mc.cpp`）** |
| 4. 矿工接入（`fast_clique/miner.py`），冒烟测试 | ✅ 初版能跑通，见下文 |
| 5. 代码审查，在真实 bittensor axon 上测试 | ⏳ 待做 |
| 6. 一键部署脚本、上线 | ⏳ 待做 |

第三个候选（C++ 局部搜索 + MCS 分支限界混合）没有做。在下面这些图上，局部搜索 1 秒内就能找到估计的最优解，分支限界的收益不大，可以以后再试。

## 对比结果

10 张基准图：官方测试数据里的 298 点和 493 点各一张，再加上按种子生成的 G(n,p) 随机图。每格列出 3 个随机种子各自找到的团大小，所有结果都已验证是合法的极大团，并且没有超时。

| 图 | 官方 networkx | 初版 numpy | numba PLS（1s） | **C++（1s）** |
|---|---|---|---|---|
| test298 | 16 | 22 | 22,22,22 | **22,22,22** |
| test493 | 18（耗时 4.2s） | 25 | 25,25,25 | **25,25,25** |
| g700_p735 | 20（耗时 10.5s） | 26–27 | 27,27,27 | **27,27,27** |
| g900_p735 | 20（耗时 20.9s，会超时） | 27–29 | 29,29,29 | **29,29,29** |
| g900_p850 | – | 46–47 | 47,47,47 | **47,47,47** |
| g500_p900 | 43（耗时 6.4s） | 56 | 56,56,56 | **56,56,56** |

上面这批图两个新候选打成平手，所以又用 12 张更难的 700 点和 900 点图（`graphs_hard/`）做了正面对比，每张图跑 6 个种子：

- **C++**：x900_2 和 x900_7 两张图上 6 次都找到最大团（29 和 30），整体只有 x900_0、x900_3 在 0.5 秒预算下偶尔差 1 到 2 个点；
- **numba**：x900_2 上 6 次里 5 次只找到 28，x900_7 上有几次只找到 29。只有 x900_0 在 0.5 秒预算下 numba 更好（6 次里 5 次找到 29，C++ 只有 3 次）。到了 1 秒预算，两者在这张图上都能每次找到 29。

详细输出见 `lab/candidates/*/bench_hard.txt`。C++ 每秒能跑约 1100 万步，numba 约 160 万步，所以选了 C++。

## 目录

```
fast_clique/            # 部署到服务器上的包（放到 CliqueAI 仓库根目录）
  solver.py, mc.cpp     #   求解器：C++ DLS-MC 局部搜索，ctypes 加载，首次 import 时用 g++ 编译（约 10~12s，之后走缓存）
  worker.py             #   子进程里跑的函数（预热、带截止时间的求解、纯 Python 贪心兜底）
  miner.py              #   FastMiner：继承官方 Miner，只替换 forward_graph
  __main__.py           #   入口：python -m fast_clique
lab/
  bench.py              # 对比脚本：检查合法、极大、不超时
  gen_graphs.py         # 生成基准图（需要官方 CliqueAI 仓库路径）
  graphs.tar.gz         # 基准图（graphs/ 和 graphs_hard/），先解压：tar xzf graphs.tar.gz
  candidates/           # 各候选求解器和它们的测试输出
```

在本地跑对比：

```bash
cd lab && tar xzf graphs.tar.gz
python3 bench.py --solver candidates/cpp_tabu/solver.py --budgets 1,4 --seeds 0,1,2
python3 bench.py --solver candidates/cpp_tabu/solver.py --graphdir graphs_hard --budgets 0.5,1 --seeds 0,1,2,3,4,5
```

## 矿工接入设计（`fast_clique/miner.py`）

- **继承官方 `CliqueAI.miner.Miner`**：钱包、axon、黑名单、优先级都沿用官方实现，只替换 `forward_graph`。
- **子进程池**：用 `ProcessPoolExecutor` 并用 spawn 方式启动子进程，避免 fork 带着 bittensor 的线程出问题。求解在子进程里进行，不会阻塞 axon。启动时每个子进程都会先预热，把 C++ 编译的开销提前付掉。
- **时间预算**：`timeout × 0.9 − 1.5s − 路上已经花掉的时间`。路上的时间用 `dendrite.nonce` 估算，并限制在 0 到 1 秒之间。例如 6 秒时限给求解器约 3.9 秒，30 秒时限约 25.5 秒。截止时间用 `time.monotonic()` 的绝对时间传给子进程，排队等待的时间也算在内。
- **多路并行**：机器空闲时，同一道题会用不同随机种子并行跑多路，取最大的团。同样大小的团会随机挑一个，提升多样性得分。
- **兜底**：子进程出错时，改用纯 Python 贪心算法，保证仍然交出一个极大团。

可以用这些环境变量调整：`CLIQUE_WORKERS`（默认等于 CPU 核数）、`CLIQUE_PARALLEL_SEEDS`、`CLIQUE_TIME_FRACTION`（默认 0.9）、`CLIQUE_SAFETY_SECONDS`（默认 1.5）。

冒烟测试在 Python 3.12、numpy 2.2.6、bittensor 10.3.2 下进行。官方 `Miner.__init__` 被替换成空函数，因为要跳过钱包和链的初始化：

| 题目 | timeout | 结果 | 用时 |
|---|---|---|---|
| general_0.4（493 点） | 6s | 25，合法极大团 | 3.85s |
| general_0.2（298 点） | 7.5s | 22，合法极大团 | 5.24s |
| sample（3 点） | 6s | 3 | 0.01s |

## 待办（下一步）

1. **代码审查**：检查超时余量是否稳妥、spawn 子进程能否正常启动和退出、出错时兜底是否生效、多个验证者同时发题时能否正确处理。
2. **用真实入口 `python -m fast_clique` 测试**。冒烟测试用的是自定义脚本，子进程退出时会打印 bittensor 日志线程的 EOFError。用真实入口时，`__main__.py` 不会在子进程里导入 bittensor，预计不会出现，需要确认。
3. **启动脚本 `start_fast_miner.sh`**：参照官方 `start_miner.sh` 来写，用 pm2 运行 `-m fast_clique`。
4. **一键部署脚本**：`apt install build-essential`（服务器上还没有 g++）、安装 node 和 pm2、克隆 CliqueAI、拷入 `fast_clique/`、安装依赖并预编译、启动。
5. **自动更新的问题**：官方矿工默认开启 `--neuron.autoupdate 1`，发现上游有更新就会 `git pull` 然后退出。`fast_clique/` 是官方仓库里没有的新目录，不会和上游冲突。但上游如果改了 `Miner` 或协议，需要重新测试。
6. **可选：没有 g++ 时的兜底**。可以退回到 numba 版本 `lab/candidates/numba_pls/`，或者纯 numpy 版本。

## 目标服务器

Intel Xeon Platinum 8255C，16 核，15GB 内存，Ubuntu 24.04.1，Python 3.12.3，**没装 g++**。

钱包需要你自己在服务器上创建或导入，并在 83 号子网注册热钱包。axon 端口要在安全组里对公网开放。
