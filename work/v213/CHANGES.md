# codexv2.13版.py 相对 codexv2.12_orig.py 的改动

此前几轮会话（`work/S1`–`S7`、`X1`–`X3` 下的实验脚本）找出了一批 bug 和性能问题，但 `codexv2.13版.py`
一直和 v2.12 逐字节相同，修复从未落地。本次把这些问题逐个复现（Python 3.14），修复，并用回归脚本验证。

## 验证方式

- `work/v213/regress.py AGENT.py [检查名 ...]`：25 项检查，每项对应一个已发现的问题。
  v2.12 上 24 项失败（唯一通过的是“`_to_src` 输出不变”这项对照检查），v2.13 上全部通过
  （Python 3.11 / 3.12 / 3.13 / 3.14 均已跑过）。
- `harness/run_e2e.py AGENT.py OUT_DIR [秒数]`：用 `harness/fake_llm.py` 在 `harness/sample_repo` 上完整跑一遍
  agent（execute + finalize），把补丁应用到干净副本上再用 pytest 跑。
  在 3.11–3.14 上，v2.13 与 v2.12 的测试数、变异体数、杀死/存活数完全一致，
  生成的补丁除 `conftest.py` 外逐字相同，总耗时少 20–25%（17.1 s → 12.4–13.6 s，Python 3.14）。

## 正确性修复

| 问题 | 来源实验 | 修复 |
|---|---|---|
| 异常的 `__str__` 本身抛错时，录制进程整个崩溃 | S1-bugs/t3, S1-perf/bugs | `_safe_str`，`_fmt_exc` 与 `rec["msg"]` 都用它 |
| 某个 case 替换了 SIGALRM 处理器后，后续 case 的超时失效 | S1-perf/bugs | 每个 case 前重新安装处理器 |
| `except:` 吞掉超时异常的死循环 / C 代码里卡死，录制一直挂到外层超时 | S1-bugs/t4 | 定时器每 0.5 s 重发；录制模式下用 `faulthandler.dump_traceback_later(exit=True)` 兜底，`_record` 已有的逻辑会把最后开始的 case 标成挂起，再继续录其余 case（集成检查：197 s → 25 s） |
| case 留下的非守护线程（如 `threading.Timer`）让 runner 进程迟迟不退出 | S1-bugs/t4, S7-bugs/x2 | runner 结束后 `os._exit` |
| 变异检查服务器 fork 出的子进程共享协议用的 stdin，读 stdin 的 case 会吞掉/阻塞协议 | S2-bugs/exp2 | 协议改用复制出的 fd，fd 0 指向 `/dev/null` |
| 导入时就运行过的库函数（模块级表格、预热的 `lru_cache`），快速路径换代码后看不到变化，误判为“存活” | S2-bugs/exp1 | 服务器导入阶段用 `sys.monitoring`（旧版本用 `setprofile`）记录运行过的函数，这类变异交给慢速路径 |
| Python 3.14 的 `__annotate__` 代码对象覆盖了单行带注解的函数，快速路径找不到函数 | S2-bugs/exp3 | `_code_children` 忽略 `__annotate__` |
| 可选依赖被隐藏时 `importlib.util.find_spec` 抛异常而不是返回 `None` | S1-bugs/t1, t3 | sitecustomize 里包装 `find_spec` |
| 已安装的 pytest 插件被当成可选依赖隐藏，导致 pytest 启动失败 | S1-bugs/t1, t2 | 带 `pytest11` 入口点的分发包视为必需 |
| conftest：一个测试超时后其余所有测试都直接失败，校验时整套用例被剔除 | S5-bugs/cascade | 改为累计 3 个超时后才快速失败 |
| case 文件里的 `from __future__ import ...` 让生成的测试文件无法编译 | S7-bugs/x1 | 生成时把 `__future__` 导入放到最前面 |
| 改名重构副本里，`match` 模式捕获的名字没改，副本行为与原库不同 | S7-perf/t1 | 被模式绑定的名字不参与改名 |
| `_difference` 把“没跑完”说成 `raised None` | S7-perf/t1 | 按状态给出正确描述 |
| 修复导入时只删直接用到缺失名字的 case，通过辅助函数间接用到的 case 留下来，被录成“抛 NameError” | S7-perf/t1 | 沿辅助函数、类、模块级赋值传递，一并删除 |
| 回复里带语法错误块或不可用时，`keep_previous_cases` 把旧 case 复制一份，测试重复 | S4-bugs/x | 按 `add_case_file` 实际会保存的内容判断；新内容不会保存时什么都不复制 |
| 修复导入后重录的文件，旧结果又被报成“case not found”（14 个误报，case 同时出现在 records 和 case_problems） | S4-bugs/y, S6-bugs/mend, e2e | 重录过的模块不再用旧结果；被删的 case 报具体原因 |
| 4 个 writer 同时遇到模型被拒，各自切换一次模型，越过可用的备选模型 | S3-bugs/race, X1/llm_race | 切换以“发请求时用的模型/端点”为准（比较后交换），`refused` 改为线程局部 |
| 代理 404 时并发线程把好端点也标成坏的 | S3-bugs/race, X1/url_race | 同上 |
| 服务器慢慢滴流返回时，writer 线程可以被无限期占住（socket 超时只限单次 read） | S3-perf/trickle | 读响应体总时长受限 |
| 测试本身很快、只是 pytest 启动慢时，`_verify_suite` 跑满 8 次 pytest 后放弃最新套件 | S5-perf/verify | 没有可裁剪的测试时接受该套件 |
| 后台重复提交仍在队列里或正在检查的变异体，同一变异体被检查多次 | S6-perf/pooldup | 提交时跳过已排队/正在跑的 |
| `execute()` 在变异池线程写字典时用生成器遍历，可能抛 `dictionary changed size` | X1/dict_race | 用 `mutation_stats()` 的快照 |
| 覆盖率报告把 `global`、多行 `if (` 头、`nonlocal` 等永远不产生行事件的行报成“未运行” | S7-bugs/x1, x3 | 按语句行区间判断 |

## 性能

- 录制时的行追踪改用 `sys.monitoring`（3.12+），比 `settrace` 快 4–10 倍，行集合相同；`stop()` 缓存 relpath。
- 每次录制的三次运行在 CPU ≥ 3 时并行。
- runner 子进程不再导入 `urllib.request`（每次启动省约 22 ms），改为 LLM 调用时再导入。
- 变异服务器：fork 前预导入 `inspect`/`pathlib`/`faulthandler`，`gc.freeze()`，期望值源码编译缓存，相同变异的 plan 缓存；
  检查模式不再发送 `start` 记录。
- `check_payload` 每个模块只做一次 `pins_classes` 正则；`run_check_slow` 复用它。
- `relevant_keys` 的模块级回退改用按文件的索引；`usage_note`/`coverage_report` 解析结果按文件缓存。
- 生成测试模块：去掉多余的 deepcopy、缓存 `SAME_SRC` 的 AST（输出逐字节相同，单次快约 22%），
  `build_suite` 对未变化的模块直接复用。
- `_to_src` 带长度预算，超大结果尽早放弃；`_public_class_path` 缓存模块列表；`tmp_path` 检测不再用 `inspect.signature`。

## 计费（之后追加）

- 新增环境变量 `TG_PRICE_AS`：用中转站别名测试时（如 `TG_MODEL=personal/gpt-6-luna`），设
  `TG_PRICE_AS=openai/gpt-6-luna`，按该模型的价格和返回的 token 数计费，忽略中转站自己报的 cost。
  不设时行为不变。原因：不在 `PRICES` 里的模型名按 $5/$25 每百万 token 计费，比 gpt-6-luna 贵 50 倍，
  一次 2 万输入 + 6 千输出的调用就记 $0.25，$0.29 预算一两次调用就用完。
- `[SETUP]` 日志行显示计费方式；模型没有价格时会提示。
