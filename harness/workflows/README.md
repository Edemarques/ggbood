# 35 道多语言高难度 test-generation 题：交接说明

这些材料不依赖 Claude。任何能在服务器上执行命令、读写文件的编程 AI 都能照着做，比如 Codex CLI、Gemini CLI、OpenHands、Aider，或者你自己的 agent。

## 现在的进度

| 部分 | 状态 |
|---|---|
| 多语言出题与判分工具 `harness/tgm.py` | ✅ 完成并验证：本地判分和 Docker 判分都已跑通 |
| Python 语言运行器 `harness/tgm_templates/python/` | ✅ 完成 |
| 其他 9 种语言的运行器（JS、TS、Go、Rust、Java、Ruby、PHP、C、C++） | ⏳ 未做，由每种语言的第 1 题顺带完成 |
| 出题规范 `harness/TASK_AUTHORING.md` | ✅ |
| 35 道题的计划 `ridges_hard_plan.json`，以及每道题的提示词 `prompts/` | ✅ |
| 35 道题本身（放到 `ridges_hard/`） | ⏳ 未开始 |

## 第 1 步：准备服务器（Ubuntu 22.04 / 24.04，root）

```bash
git clone https://github.com/edemarques/ggbood.git /root/ggbood
git clone https://github.com/ridgesai/ridges-bench /root/ridges-bench   # 官方样题，出题时要参考
cd /root/ggbood
bash harness/setup_server.sh all      # Docker 和各语言工具链（只判题的话用 docker 参数就够）
export RUSTUP_HOME=/opt/rustup
RIDGES_BENCH=/root/ridges-bench bash harness/tgm_selftest.sh --docker   # 两次都应输出 reward 1
```

`setup_server.sh` 只在 Claude 的云端环境里做过语法检查，没有在真机上完整跑过。如果某一步失败，把报错交给你的 AI 修。

## 第 2 步：让 AI 出题

`prompts/` 下每道题一个目录（`01_...` 到 `35_...`），每个目录里有 5 个提示词文件，按顺序交给 AI：

1. `1_build.md`：出题。完整做完一道题，包括选库版本、写题面、写参考答案、写 12 到 16 个 mutant 和 4 到 5 个 decoy、本地判分、Docker 判分。
   **每种语言的第 1 题（09、13、17、21、25、29、32、34、35）还要先写这种语言的运行器模板。**这些题必须先做完，同语言的其他题才能开始。Python 的模板已经有了，01 到 08 可以直接开始。
2. `2_probe.md`：盲测。**换一个新的 AI 会话**，只给它看题面，让它自己写测试，测这道题到底有多难。
3. `3_harden_if_probe_solved_it.md`：只有盲测拿了满分，或者抓住了全部 mutant 时才用。把盲测结果的 JSON 贴进提示词里的占位处。
4. `4_review.md`：对抗审查。**再换一个新会话**，把出题结果和盲测结果贴进去。
5. `5_fix_if_review_found_blocking_issues.md`：审查发现阻塞问题时才用，把审查结果贴进去。

几点建议：

- 出题、盲测、审查最好用**不同的会话**，否则盲测会偷看答案，审查也容易手下留情。
- 用最强的模型出题和审查。设计「隐蔽但合规」的 mutant，以及检查 decoy 有没有改变规定范围内的行为，都很吃模型能力。
- 能并行就并行，16 核可以同时做 4 到 6 道题。编译型语言（Rust、Java、C++）的判分比较慢。
- 仓库路径或样题路径和上面不一样的话，重新生成提示词即可：
  `node harness/workflows/gen_prompts.mjs harness/workflows/ridges_hard_tasks.js harness/workflows/ridges_hard_plan.json harness/workflows/prompts <仓库路径> <ridges-bench路径> <并行数>`

如果以后又用回 Claude Code，可以直接跑整套流水线：用 Workflow 工具，`scriptPath` 设为 `harness/workflows/ridges_hard_tasks.js`，`args` 设为 `ridges_hard_plan.json` 的内容，再加上 `repo`、`samples` 等参数。

## 第 3 步：用这些题测试你的 agent

```bash
cd /root/ggbood
export TG_BENCH=$PWD/ridges_hard TG_ROOT=/opt/tgm
python3 harness/tgm.py prepare -j 8                           # 准备所有题
python3 harness/tgm.py solution -j 8                          # 用参考答案自检，每道题都应是 reward 1
python3 harness/tgm.py agent /path/to/agent.py <题目> /tmp/out -j 8   # 跑你的 agent，然后判分
python3 harness/tgm.py docker <题目> /tmp/out/patch.diff       # 在题目自带的镜像里判分（和 Ridges CLI 一样）
```

每道题也是标准的 Harbor 任务目录，可以直接用 `ridges miner run-local --task-path ridges_hard/<题目>` 跑。

## 难度标准（每道题都要达到）

- 12 到 16 个 mutant（官方样题 2 到 8 个），4 到 5 个 decoy（官方 2 到 3 个）。
- mutant 必须覆盖这些类别：边界至少 3 个；选项组合至少 2 个；错误处理至少 2 个；状态与顺序至少 2 个；默认值至少 1 个；冷门但有文档的功能至少 1 个。
- decoy 专门用来坑「测得太细」的测试，比如改报错文案、改对象的字符串表示、改内部结构、改题面声明为未规定的行为、换一种等价的算法写法。
- 参考答案连续 3 次 reward 1，Docker 判分也是 1；盲测不能轻松拿满分。

详见 `harness/TASK_AUTHORING.md`。
