# Agent-Moe-Research

研究主题：**探索 MoE 路由中与 Agent 任务偏移有关的通用信号**。机制发现优先，简单、低成本的在线算法用于验证；路由只是监测信号之一，不作为攻击成功或阻断的唯一判据。

这是截至 **2026-09-07 的私有研究交接快照**，不是完整数据集发布，也不是已经验证可部署的安全产品。研究代码、协议与历史报告一并交接；原始实验数据和模型由研究员在各自环境准备或重新生成。

## 从这里开始

1. [研究交接与双研究员执行计划](research_lines/codex/docs/moe_agent_task_shift_research_handoff_v2.md)：研究目标、数据和算法历史、论文方向、A/B 分工及阶段交付。
2. [Codex / Astra 研究线原始 README](research_lines/codex/README.md)：OLMoE、Agent v2.5、路由捕获与历史算法。
3. [Claude 研究线原始 README](research_lines/claude/README.md)、[GPT-OSS 研究纲领](research_lines/claude/docs/research_v4/gpt_oss_research_program.md)、[Agent v3 数据设计](research_lines/claude/docs/research_v4/agent_v3_dataset_design.md)。
4. [本次交接验证记录](HANDOFF_VALIDATION.md)与[来源及文件哈希清单](HANDOFF_MANIFEST.json)。

两条研究线分别位于 `research_lines/codex/` 和 `research_lines/claude/`，各自保留 `src/`、`scripts/`、`tests/`、`configs/`、`docs/`。它们有共同历史，但并非合并后的单一实现。**从相应研究线的目录运行脚本，分别配置环境；不要混用两个 `src`。**

## 交接范围与来源

- Codex：以 `9e6ad56ba2169ec9727337bdb6b8d4d4059a534a` 为基础的当前代码/文档快照，包括尚未提交的 Astra 研究文件与最新交接计划。
- Claude：只导出已提交的 `62be82be4d100b46df9bbf28a6ab37ad69cc997d`；不包含正在编辑的文件或临时子工作树。
- 两个原工作区和分支均未修改。本仓库使用全新的 Git 历史，不携带原历史中的数据或已训练权重。
- 研究代码未修改；只在发布副本中修正了 12 个跨研究线的 Markdown 导航链接。文件级哈希见 manifest。
- 保留源代码中的任务生成模板、合成单元测试样例、声明式 Agent/模型配置，以及 Markdown 中的历史结果和少量说明性例子。
- **不包含**模型/检测器权重、Agent 轨迹、路由张量、逐样本标注、生成的场景配置、知识库/业务记录数据文件、原始诊断输出、虚拟环境及缓存。

历史文档中的日期、结果、旧路径、标签定义与预注册状态保持原样，属于当时记录，并非本次重新验证。部分旧文档的本地路径和数据链接会不可用；从本页的交接计划导航。标为 draft、事后探索或封存的资产，不因交接而变成已确认结论或新测试集。

## 本地环境与无模型检查

先选择一条研究线，例如：

```bash
cd research_lines/claude
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export PYTHONPATH="$PWD/src"
python -B -m unittest discover -s tests -p test_agent_v3_harmony.py -v
python -B scripts/research_v4/factory_build_dataset_g.py --dry-run
```

依赖文件记录的是原实验环境，其中 PyTorch 使用 CUDA 13 wheel；**本次未在新机器上验证依赖安装或 GPU 兼容性**。根据自己的硬件确认环境并记录变更，不把安装成功当作模型/任务资格通过。

Codex 线的无模型路由捕获检查：

```bash
cd research_lines/codex
# 使用这条研究线自己的环境，从本目录执行。
python -B -m unittest discover -s tests -p test_routing_capture.py -v
```

以上检查不下载模型、不运行 Agent 采集。完整历史测试集中的部分测试需要已排除的数据/配置/权重，不承诺仅 clone 后即可全部通过。

## 数据如何重新准备

Agent v3 的 G 数据有源代码工厂。以下命令在 `research_lines/claude/` 目录执行，只构建本地场景、fixture 和配置文件，不生成模型输出：

```bash
export PYTHONPATH="$PWD/src"
python -B scripts/research_v4/factory_build_dataset_g.py --summary
```

产物位于 `configs/dataset_g/`，默认被 Git 忽略。采集前阅读数据设计、资格门和 collection plan；模型需按配置中的版本单独准备。不要直接无筛选地跑所有场景/arms，也不要把 G-conf 默认用于新方法调参。重新生成同一批输入并不使其成为独立确认集。

**已有构建器不等于所有历史批次都能从此快照完整重建。** OLMoE/Agent v2 的部分构建脚本仍依赖被排除的基础知识库、业务记录和场景配置；历史精确复算还需要原标签、路由缓存及权重。对于下一阶段，依据协议与 schema 重新构造输入、冻结新数据版本并采集；不要将新采集冒称历史逐字节复现。

保留代码中的已知路径问题：部分 `scripts/research_v4/` 入口按 `parents[2]` 定位根目录，实际会落到 `scripts/`。上面的 `PYTHONPATH` 可解决导入，但不能修复所有默认文件路径。需要其他入口时，请在自己的分支核对根路径及参数，再做 CPU 小检查；本次没有改动历史研究实现。

## 保持仓库轻量

发布门槛为总文件内容 **30 MiB**、单文件 **1 MiB**；这是本项目的保守门槛，不是 GitHub 平台限制。初始研究文件共 1,049 个、约 13.65 MiB，另有小型交接文档和清单。

`.gitignore` 阻止常见数据、权重与缓存文件，也默认忽略新增 JSON；现有的少量声明式配置已明确纳入版本控制。新增配置先人工确认只含定义、不含场景/样本，再决定是否强制加入。

提交前检查实际暂存内容：

```bash
git add <已确认的代码或文档路径>
python3 -B tools/check_handoff.py --index
```

检查当前提交：`python3 -B tools/check_handoff.py`。检查覆盖允许路径、尺寸、常见密钥模式、Python 语法及初始导入文件哈希；不替代人工保密审查。研究代码后续合法修改时，需同时更新或版本化来源清单，再审核新快照。不要使用 `git push --mirror` 将旧研究仓库的全部历史带入这里。

仓库初始为私有，尚未指定开源许可证；公开发布、第三方材料许可与数据共享由项目主管另行确认。
