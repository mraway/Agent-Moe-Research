# 当前实验执行记录

目标：继续完成当前实验计划。上一阶段为实际进展：Q1 36 条已生成、逐例复核、报告与测试完成；不是仅提出方案。
本记录不替代预注册或冻结文件，不作为正在运行的进程存在证明。

## 当前批次交付检查

- [x] Q2 实施配置、只正常臂入口、输入一致性测试、运行前冻结。
  - 冻结 `artifacts/agent_v2/astra_stage2_q2_prep/freeze.json`。
  - 两条件共 32 个 pre-brief prompt 与 Q1 基线逐字符相同；最长新增 prompt 1884+1024<4096。
- [x] boundary-only 16 条已完成；已保存完整 `run_summary.json`。
- [x] task-echo 16 条生成完成；两个 GPU 运行均已正常退出，32/32 实际干预已核对。
- [x] Q2 全部 32 条语义复核、与 Q1 16 条长控制的完整配对比较、报告。
  - `docs/astra_stage2_q2_report.md`：两组长度改善，但联合质量各 0/16，均 no-go。
- [x] C1 全量 320 条 packet 和分轴 rubric 已冻结。
  - 冻结 `artifacts/agent_v2/c1_behavior_axis_audit_v1/freeze.json`；31069 个文件。
- [x] C1 320/320 逐例任务遵循、题外参与、未授权操作、事实与完成质量标注、校验与汇总已完成。
- [x] C1 分轴汇总、代表性证据、边界案例和独立复核 handoff。
  - `docs/c1_behavior_axis_audit_report.md`；`docs/c1_behavior_axis_reviewer_handoff.md`。
  - 252 on-task / 38 明确操作偏离 / 29 无答案 / 1 语义不明；原始分母不变。
- [x] 根据 Q2 门槛与 C1 审计结论决定后续正常生成/校准、抵御与恢复素材的处理；不以此记录自动授权 B3。
  - `docs/astra_stage2_closeout_decision.md`：本批 no-go；不扩量校准、不追加攻击凑样本、不启动 B3。
- [x] 最终检查覆盖上述全部事项，旧文件与控制分母保留，测试通过；未完成项不得被阶段性结果替代。
  - 336 tests passed；54217 个去重保护文件 hashes 一致；closeout 二次重算与交付 hashes 校验通过。
  - `artifacts/agent_v2/astra_stage2_closeout_v1/closeout.json`。
  - SHA256 `26129fef2db7209b1d20d048306fdbf42f135fdd17f5d65f8194071196417ab8`。

## 交付之外仍未完成的研究事项

本次有限 Q2/C1 执行批次已完成，不等于整个研究项目完成或资格通过。
独立 Reviewer-B/人类复核尚未执行；新的可靠长正常 fit/calibration/holdout、足量抵御/恢复素材、同前缀机制对照、文本对照、流式成本和 B3 确认仍待后续阶段。
Q3 基础可回答性/材料一致性对照仅给出方向，尚未冻结或生成；不得把建议当作正在运行的任务。

## 约束

只在主工作区新增/修改本轮文件，Claude 工作树和 branch 不写入。
不改 Q1、第一轮或 C1 原始标签、输入、缓存和结果；不查看本轮 detector score，不启动 B3。
单 AI 复核不称独立共识；独立 Reviewer-B/人类另行安排，本轮不自行启动子代理。
任务偏移、事实质量、输出长度和删失分开；不把一般错误都变成攻击阳性，也不删除困难正常样本改善 FAR。
