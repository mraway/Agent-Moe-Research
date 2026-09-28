# Claude 研究线归档索引

2026-09-27。主线固定在 `0c90f5be0b21f46862d5e96b5e7d77e5fddd1c4d`；
其后三个历史支线各自按最后提交导出。这里只整理既有材料，不重新解释实验或补做确认。
项目现已停止推进；所有计划、冻结与“进行中”状态均指原报告写作时。

## 主线：gpt-oss / Agent v3

| 要找的内容 | 入口 |
|---|---|
| 对外技术总结与限制 | [技术报告](research_lines/claude/docs/research_v4/technical_report_gpt_oss.md)、[五分钟摘要 HTML 源文件](research_lines/claude/docs/research_v4/g_series_brief.html) |
| 研究定位与数据设计 | [研究纲领](research_lines/claude/docs/research_v4/gpt_oss_research_program.md)、[Agent v3 / G 设计](research_lines/claude/docs/research_v4/agent_v3_dataset_design.md)、[场景工厂](research_lines/claude/docs/research_v4/scenario_factory.md) |
| 标注协议与数据接口 | [攻击标注指南](research_lines/claude/docs/research_v4/attack_annotation_guideline.md)、[正常标注指南](research_lines/claude/docs/research_v4/normal_annotation_guideline.md)、[G 入门包](research_lines/claude/docs/research_v4/dataset_g_onboarding_for_codex.md) |
| v3.1 的读数与失败机制 | [G-dev 报告](research_lines/claude/docs/research_v4/g_dev_confirmatory_report.md)、[主格诊断](research_lines/claude/docs/research_v4/g_dev_primary_diagnostics.md) |
| v3.2 的开发、冻结与确认 | [正式预注册](research_lines/claude/docs/research_v4/detector_prereg_v3_2.md)、[G-dev 开发报告](research_lines/claude/docs/research_v4/g_dev_v3_2_development_report.md)、[G-conf 确认报告](research_lines/claude/docs/research_v4/g_conf_confirmatory_report.md) |
| v3.2 的后续拆解 | [观察视界与延迟](research_lines/claude/docs/research_v4/zoom_v32_horizon_latency.md)、[改进空间](research_lines/claude/docs/research_v4/zoom_v32_improvement_space.md)、[统计量](research_lines/claude/docs/research_v4/zoom_v32_statistic.md) |
| v3.3 与 G-conf-2 最后状态 | [正式预注册](research_lines/claude/docs/research_v4/detector_prereg_v3_3.md)、[冻结清单 A3](research_lines/claude/docs/research_v4/freeze_a3_checklist.md)、[开发测量](research_lines/claude/docs/research_v4/v3_3_dev_measurements.md)、[标注运行日志](research_lines/claude/docs/research_v4/g_conf2_annotation_run_log.md) |

最后一条不能写成“v3.3 已完成确认”：该提交的 G-conf-2 日志仍记录裁决/复核未完成。
本次没有读取原始封存内容，也没有把旧运行日志中的续跑指令当作本轮执行任务。
v3.2 的确认报告和 v3.3 的预注册是不同证据状态，不以项目结项改写它们。

## 对应实现在哪里

- Agent、工具协议、数据工厂与 packet：[`src/agent_v3/`](research_lines/claude/src/agent_v3/)。
- 生成与采集：[`run_agent_v3.py`](research_lines/claude/scripts/research_v4/run_agent_v3.py)、
  [`factory_build_dataset_g.py`](research_lines/claude/scripts/research_v4/factory_build_dataset_g.py)。
- 标注共识：[`annotation_consensus.py`](research_lines/claude/scripts/research_v4/annotation_consensus.py)、
  [对应测试](research_lines/claude/tests/test_research_v4_annotation_consensus.py)。
- 评价 harness：[`run_detectors_g.py`](research_lines/claude/scripts/research_v4/run_detectors_g.py)、
  [`trm3_g.py`](research_lines/claude/src/research_v2/trm3_g.py)、
  [v3.3 测试](research_lines/claude/tests/test_research_v4_v3_3.py)。
- 路由采集：[`src/routing/`](research_lines/claude/src/routing/)。

## 三条早期独立支线

这些支线来自 OLMoE 阶段，保留完整的“所选代码/文档快照”，而非只摘录成功读数。
它们不是 gpt-oss 确认结果；共同文件虽有重复，但运行环境和来源不混合。

| 支线 | 来源提交 | 报告与代码 |
|---|---|---|
| WGM，窗口几何 | `47191352ccb87913a4ba79b618978c0b9174f338` | [报告](research_lines/claude_wgm/docs/research_v2/wgm_report.md)、[scorers](research_lines/claude_wgm/src/research_v2/scorers/) |
| CM，条件流形 | `372cc5e2b6a82cff093300aac3e16cec74e91ebc` | [报告](research_lines/claude_cm/docs/research_v2/cm_report.md)、[scorers](research_lines/claude_cm/src/research_v2/scorers/) |
| PDM，路径动力学 | `5fabd9725f9215726d429af53b0da77945272f35` | [报告](research_lines/claude_pdm/docs/research_v2/pdm_report.md)、[scorers](research_lines/claude_pdm/src/research_v2/scorers/) |

各来源目录、文件清单和 SHA-256 均见 [HANDOFF_MANIFEST.json](HANDOFF_MANIFEST.json)。
四个 Claude worktree 在导出前没有已跟踪修改或未提交代码；其唯一未跟踪项是共享
`artifacts` 符号链接，未沿链接导出数据。Claude 的分支和工作区未修改。
