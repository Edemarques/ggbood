# codexv2.12_重构.py 相对 codexv2.12_.py 的改动

`codexv2.12_.py` 是上传的原文件（只把 CRLF 换成了 LF）。`codexv2.12_重构.py` 不改动架构
（主写手 + lanes + zygote + probe 都保留），只做三类改动：修已证实的 bug、提速、清理代码。
所有提示词与原文件逐字相同。

## 修复（每项都有回归检查，原文件失败、重构版通过）

| 问题 | 修复 |
|---|---|
| conftest：一个测试超时后，后面所有测试直接失败（评分机稍慢就可能整题 0 分） | 累计 3 个超时后才快速失败 |
| 异常的 `__str__` 本身出错时，录制进程崩溃，整批 case 丢失 | `_safe_str`，`_fmt_exc` 也用它 |
| case 替换 SIGALRM 处理器后，后续 case 的超时失效，录制卡到外层超时（30 s → 1.1 s） | 每个 case 前重装处理器，定时器每 0.5 s 重发 |
| `except:` 吞掉超时的死循环、或卡在 C 代码里，录制卡到外层超时（30 s → 3.6 s） | 录制模式用 `faulthandler.dump_traceback_later(exit=True)` 兜底，`_record` 原有逻辑会把最后开始的 case 标成挂起 |
| case 留下非守护线程（`threading.Timer` 等），runner 进程退不出（20 s → 0.1 s） | runner 结束后 `os._exit` |
| 超大返回值先完整转成源码再判断超长（5800 万字符，3.9 s → 0.1 s） | `_to_src` 带长度预算；`_typed` 包装规则不变（316 组值逐字对比一致） |
| case 文件里的 `from __future__ import ...` 让生成的测试文件无法编译 | 生成时放到最前 |
| `_difference` 把“没跑完”说成 `raised None` | 按状态描述 |
| 覆盖率报告把 `global`、多行 `if (` 头、`nonlocal` 报成“未运行” | 按语句行区间判断（`_stmt_spans`） |
| lane 在线程里调用模型，SIGALRM 硬超时不生效，服务器慢慢滴流返回时 lane 会被一直占住（40 s → 20 s） | 响应体总读取时间受限 |

## 提速

- 录制时行追踪改用 `sys.monitoring`（Python 3.12+），比 `settrace` 快 4–10 倍，行集合相同。
- runner 子进程不再导入 `urllib`（每次启动约省 20 ms）。
- `run_check` 每个模块只做一次 `pins_classes` 正则；`relevant_keys` 的模块级回退用按文件索引；
  `coverage_report`/`mentioned_files` 的解析结果按文件缓存；`_public_class_path` 缓存模块列表；
  `tmp_path` 检测不再调用 `inspect.signature`。
- 生成测试模块：去掉多余 deepcopy、`SAME_SRC` 只解析一次（输出逐字相同），`build_suite` 对未变化的模块直接复用。

## 清理

- 中途拼接进来的 `import`/模块 docstring（`_usage_math`、`_usage_sys`、`_fallback_name_os` 等别名）移到文件头。
- 删除无人调用的 `usage_metadata`、`usage_model`、`usage_has_quote`、`_bound_outside_comprehensions`、`_DYNAMIC_NAMES`，
  以及未使用的顶层 `import inspect`。
- `_cap_child_memory` 复用 `_child_as_cap`，不再重复计算。
- `start_lanes` 不再访问 `Thread._started` 私有属性；`parse_i1_runtime_layout` 改名 `_parse_layout_reply`；
  `_transcript` 加锁（lanes 并发写）。
- 去掉只含空格的行和多余空行（字符串字面量内不动）。

## 计费（之后追加）

- 新增环境变量 `TG_PRICE_AS`：用中转站别名测试时（如 `TG_MODEL=personal/gpt-6-luna`），设
  `TG_PRICE_AS=openai/gpt-6-luna`，按该模型的价格和返回的 token 数计费，忽略中转站自己报的 cost。
  不设时行为不变。原因：不在 `PRICES` 里的模型名按 $5/$25 每百万 token 计费，比 gpt-6-luna 贵 50 倍，
  一次 2 万输入 + 6 千输出的调用就记 $0.25，$0.29 预算一两次调用就用完。
- `[SETUP]` 日志行显示计费方式；模型没有价格时会提示。
- 回归检查 `relay_model_priced_as`：原文件失败，这一版通过；其余检查结果与改动前相同，
  e2e（`TG_LANES=1`）结果与改动前逐项相同。

## 验证

- `work/v213/regress.py` 中适用于这一版的 11 项检查：原文件 10 项失败（第 11 项是对照），重构版全部通过。
- `harness/run_e2e.py`（`harness/fake_llm_lanes.py` 按固定脚本回答）在 `harness/sample_repo` 上：
  - 关闭 lanes（`TG_LANES=1`，结果确定）：两版测试数 28、变异体 416、杀死/存活 338/76、模型调用 28 次完全一致；
    补丁除 `conftest.py` 外逐字相同；运行日志去掉时间后只有计时造成的差异。
  - 打开 lanes：两版都在同样两种结果之间随线程时序变化（46 个测试约 37 s，或 50 个测试约 59 s），无报错。
- 还没有用真实模型在 ridges-bench 上跑分：本环境访问不到模型接口。

## 没有做、但值得考虑的

- 自动修补坏块：这一版遇到一个语法错误块就整个文件不可用（示例里 `cases_durations` 的 12 个好 case 全丢），
  要等写手下一轮重发；另一条线（v2.13）会只删掉坏块。改了会改变行为，需要真实跑分验证。
- `refresh_mutants` 每次录制后都重新生成所有已覆盖文件的变异体；大库上每轮约 0.5 s，收益不大，没改。
