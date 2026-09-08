# 主分支（Codex）工作线在代码/SQL 类越界上的表现：对照记录

日期：2026-09-05（America/Los_Angeles）

作者：研究组长。数据来源：主分支各实验的 `artifacts/agent_v2/normal_manifold_*/result.json` 与
`normal_manifold_time_uniform_calibration/result.json` 中保存的逐 trace 报警记录（`pre_onset_alarm`、
`first_post_onset_alarm`、`evidence_onset`），由组长脚本统一重算；锚点为 evidence onset；"干净命中"= 无 onset 前
报警且首个 onset 后报警在 +k 内。目标样本：programming 域 8 条 drift（B1→B2 方向 5 条、B2→B1 方向 3 条）与
3 条通过工具调用产出交付物的 drift（P3）。全部为 B1/B2 开发数据上的事后对照。

## 1. Codex 工作线的最新进展（截至 commit `da987d1`）

| 实验 | 内容 | 主要结论（引自其报告） |
|---|---|---|
| P1 / P2 / P3 | kNN、条件 PCA、forecast+CUSUM，源批校准 | 三者均未过 gate；P1 漏检审计归因于 cell 经验尾部封顶 |
| P1-LF | 取消 workflow cell，连续 z | 表示层信号不依赖标签；源批校准跨批失稳（反向 FAR 22%） |
| Age-free ablation | 去掉绝对位置归一化 | 信号不依赖 decode age；主要问题是正常支持的跨批漂移 |
| Trajectory ablation | 有限记忆/转移残差 | 重叠窗口上的持续性没有独立证据 |
| Independent innovation | 单 token 路由新颖度、非重叠块 | 单 token 观测本身已含稳定的 post-drift 支持缺失（AUROC 0.995/0.998）；programming 是分离最弱的域（0.467/0.403） |
| **C1 采集** | 新采 160 组 / 320 条纯正常 trace（clean + benign） | 通过采集门；用于阈值校准（100 组）与留出正常误报（60 组） |
| Time-uniform calibration | 用 C1 校准单 token 新颖度的完整路径阈值与 risk-clock 边界 | 留出组 FAR 15.0%（刚好达标）；历史 drift 的 onset+8 召回 25%/11%，延迟 18/25；不启动 B3 |

Codex 的方向与本分支一致（normal-only、conformal 阈值），差异在：他们坚持**源批 / 独立正常组**校准并为此新采了
C1；本分支用**目标批 routine 对半**校准（部署侧）。C1 正是本分支综合报告所要求的"每校准半 ≥ 90 条 routine"的
数据，可直接作为本分支候选的校准池复用。

## 2. 代码/SQL 类越界的逐条对照

`+8/+16/final` = 干净命中数 / 该方向 programming drift 数。挑选每个实验中 programming 表现最好的行；完整
120 行表见 `artifacts/agent_v2/research_v2/codex_comparison/programming_hits.txt`（由本文附带脚本生成）。

| 工作线 / 方法（方向） | 总 FAR | programming +8 / +16 / final | P3 工具产出 final |
|---|---|---|---|
| Codex P1 kNN 主配置（B1→B2 / B2→B1） | 0.034 / 0.094 | 0/0/2 of 5 ; 0/0/1 of 3 | 0/2 ; 0/1 |
| Codex P1 selection_middle（B1→B2） | 0.102 | 0/0/3 of 5 | 1/2 |
| Codex P2 PCA probability 变体（B2→B1） | 0.115 | 0/2/2 of 3 | 0/1 |
| Codex P3 forecast probability 变体（B1→B2 / B2→B1） | 0.068 / 0.115 | 0/0/2 of 5 ; 0/0/2 of 3 | 1/2 ; 1/1 |
| Codex P1-LF endpoint z（B1→B2 / B2→B1） | 0.039 / 0.219 | 0/0/0 of 5 ; 0/0/1 of 3 | 2/2 ; 0/1 |
| Codex age-free raw kNN（B1→B2 / B2→B1） | 0.010 / 0.219 | 0/0/0 of 5 ; 0/0/1 of 3 | 2/2 ; 0/1 |
| Codex independent innovation：token_endpoint_z（B1→B2 / B2→B1） | 0.020 / 0.125 | 0/0/1 of 5 ; 0/0/1 of 3 | 1/2 ; 1/1 |
| Codex independent innovation：nonoverlap_token_mean8（B1→B2） | 0.068 | 0/0/3 of 5 | 1/2 |
| Codex time-uniform（C1 校准）token static（B2 / B1） | 0.073 / 0.104 | 0/0/2 of 5 ; 0/0/0 of 3 | 2/2 ; 1/1 |
| **Codex time-uniform（C1 校准）block mean-8 risk-clock（B2 / B1）** | 0.137 / 0.167 | **0/1/5 of 5** ; 1/1/2 of 3 | 2/2 ; 1/1 |
| 本分支 CAND-A（白化距离 5–15，w=8；B1→B2 / B2→B1） | 0.098 / 0.115 | 0/0/0 of 5 ; 0/0/0 of 3 | 0/2 ; 0/1 |
| 本分支 CAND-B（深度链 surprisal 5–11，w=4） | 0.078 / 0.083 | 0/0/0 of 5 ; 1/1/1 of 3 | 0/2 ; 0/1 |
| 本分支 FCM 最佳（未见转移 F6，越过误报容差） | 0.146 / 0.167 | 2/2 of 5 ; 1/1 of 3（+16） | – |
| 文本规则（代码围栏/SQL/关键字；零误报） | 0.000 | 0/0/7 of 8（首次命中中位 +17） | – |

## 3. 结论

1. **没有任何一条工作线在代码/SQL 上做到及时检测。** 在 Codex 的 8 个实验、约 120 个（方法 × 停止规则 × 方向）
   组合里，programming 的 onset+8 干净命中至多 1 条（出现在 4 个零散变体上），+16 至多 2/3 或 1/5。这与本分支
   两个候选（0/5、0–1/3）以及 FCM 的 12 个候选一致：代码盲区是两条线共同的能力边界，不是本分支特有。
2. **Codex 最新的"C1 校准 + 8-token 非重叠块均值 + risk-clock"在 B2 上最终检出了 5/5 条 programming**（+16 只
   1/5，延迟长），代价是总 FAR 0.137/0.167（超出其自己的 15% 门）。这是"代码最终可检、但不及时"的又一证据，
   与本分支 FCM 的分布层发现（未见转移率对代码窗口是 routine 的 6 倍）和文本规则（7/8 但延迟 +17）同向。
3. **两条线的机制诊断一致**：Codex 的 independent-innovation 报告把 programming 列为分离最弱的域；P1 审计把 SQL
   列为"表示层弱例"；本分支 FCM 证明盲区不是 agent 的 JSON 习惯、也不能靠形态条件化修复。
4. **Codex 最有价值的新资产是 C1**：320 条纯正常 trace、160 组。本分支综合报告要求的校准池规模（每半 ≥ 90 条、
   ≥ 45 组）已由它满足；下一步应把本分支的两个冻结候选在 C1 校准下重算（模式 D 改为"C1 100 组定阈、60 组留出
   + B1/B2 全部 drift 评价"），这样两条线在同一校准池上可比。

## 4. 复现

`scripts/research_v2/codex_comparison/programming_hits.py` 读取主分支各实验的 `result.json`，输出
`artifacts/agent_v2/research_v2/codex_comparison/programming_hits.txt`。
