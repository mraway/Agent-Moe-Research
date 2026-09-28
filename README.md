# Agent-Moe-Research — 结项归档

本项目探索 **MoE 路由是否包含可用于 Agent 任务偏移监测的通用信号**。
研究团队在后续验证后决定停止推进；本仓库于 **2026-09-27** 更新为代码与文档归档。
这项结项决定不等于证明所有路由监测方法无效，也不把历史探索升级成已验证的生产方案。

## 阅读入口

1. [Claude 线归档索引](CLAUDE_ARCHIVE.md)：gpt-oss / Agent v3、数据工具链、v3.1–v3.3、三条早期算法支线。
2. [Claude 对外技术报告](research_lines/claude/docs/research_v4/technical_report_gpt_oss.md)：
   包含确认性结果、失败门和限制；是该分支当时的报告，不是本次重新评价。
3. [Codex 机制探索 M15](research_lines/codex/docs/research_v4/codex_g_mech_m15_report.md)、
   [算法探索 A03-R](research_lines/codex/docs/research_v4/codex_g_alg_a03r_report.md)、
   [隐藏激活重采小样本验证](research_lines/codex/docs/research_v4/codex_g_hidden_replay_smoke_report.md)。
4. [历史研究交接](research_lines/codex/docs/moe_agent_task_shift_research_handoff_v2.md)、
   [发布核验记录](HANDOFF_VALIDATION.md)、[来源与逐文件 SHA-256 清单](HANDOFF_MANIFEST.json)。

OLMoE / Agent v2.x 的结果只作历史探索与假设来源；不能迁移为 gpt-oss / Agent v3 的正式证据。
历史文档中的“下一步”“进行中”“冻结”等表述保留原样，不表示项目现在仍在运行。

## 五份独立来源

| 发布目录 | 原分支 tip | 定位 |
|---|---|---|
| [`research_lines/codex/`](research_lines/codex/) | `3344937ecbaa` | Codex / Astra 主线，已纳入关闭前未提交的代码文档 |
| [`research_lines/claude/`](research_lines/claude/) | `0c90f5be0b21` | Claude gpt-oss / Agent v3 主线、G 工具链与报告 |
| [`research_lines/claude_wgm/`](research_lines/claude_wgm/) | `47191352ccb8` | 早期 WGM 窗口几何支线 |
| [`research_lines/claude_cm/`](research_lines/claude_cm/) | `372cc5e2b6a8` | 早期 CM 条件流形支线 |
| [`research_lines/claude_pdm/`](research_lines/claude_pdm/) | `5fabd9725f92` | 早期 PDM 路径动力学支线 |

这些是分别导出的源码/文档快照，**不是合并后的单一实现**。
运行时从对应研究线目录执行，分别配置环境，不混用多个 `src/`。
原始主检出与四个 Claude worktree 均未因发布而改写；原提交哈希仅作来源定位，
不要求它们在这个净化后的 GitHub 仓库中是可 checkout 的提交。

GitHub 原 `main` 是 2026-09-07 的独立 handoff 快照（`d46b00e635d6`），
与原研究仓库没有共同提交。本次沿用其目录和数据排除原则，以后继提交更新，
不强推、不合并两条原始研究历史。完整旧 Git 历史只保留在本地恢复包中。

## 保留与排除

保留源代码、叙述性报告、预注册与审计、依赖版本、声明式 Agent/模型配置，
以及 CM/WGM/PDM 的小型统计量参数 JSON。这些参数文件是算法定义，不含样本或学习到的权重。

不上传模型或检测器权重、原始 Agent 轨迹、路由/隐藏激活张量、逐样本标注、
生成的场景/知识库配置、原始诊断 JSON/CSV、环境缓存、助手会话、凭据或旧仓库 Git 对象。
研究文档中本来就有的少量例子和聚合统计保留。

完整原始数据删除后，**代码和配置不保证逐字节重建原实验**。
历史文档指向被排除的 JSON、PDF、模型或数据目录的链接可能不可用。
Codex 的 `docs/archive/2026-09-27/` 描述的是较宽的本地保留范围；GitHub 发布边界以本页为准。

## 检查与复用

`HANDOFF_MANIFEST.json` 记录每个源分支、导出路径、原 Git blob、原 SHA-256 和发布 SHA-256。
研究实现保持原字节；仅发布副本中的导航和归档上下文说明可能调整，并逐文件登记。
未修复或重新运行历史研究算法，未打开 G-conf / G-conf-2 原始轨迹、路由或标签。

从仓库根目录检查已提交文件：

```bash
python3 -B tools/check_handoff.py
```

准备以后更新时：

```bash
git add <已审阅的代码或文档路径>
python3 -B tools/check_handoff.py --index
```

检查覆盖实际 Git blob 的允许路径、总尺寸、单文件尺寸、常见凭据特征、
Python 语法及完整来源清单；不替代人工保密审核或运行时验证。
本次五线归档将总内容上限从 30 MiB 调整到 **50 MiB**，单文件仍不得超过 **1 MiB**。
实际尺寸与检查结果见发布核验记录，远小于原始模型和数据占用。

数据工厂代码和旧运行命令仍保留，但这不是自包含数据发布：
部分历史测试依赖已排除的知识库、标签、配置、缓存或 tokenizer。
未宣称 clone 后全套测试通过，未验证新机器安装或 GPU 兼容性，也未新增数据采集。

当前仓库为公开可见；本次未修改仓库可见性，未添加开源许可证。
使用与再分发须自行核对第三方代码、模型和材料的权利边界。
