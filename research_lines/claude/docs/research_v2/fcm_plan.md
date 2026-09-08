# 方案 4：结构化输出盲区与"输出形态条件化"的 routine 流形（FCM，探索性）

日期：2026-09-05（America/Los_Angeles）

作者：研究组长。执行者：Claude Opus 5。性质：机制研究（样本极小），目标是确定盲区的成因层次并给 B3 一个
可预注册的修补方向，不是宣布新候选。

## 0. 问题

两个冻结候选（CAND-A 白化距离 5–15 层 w=8；CAND-B 深度链 surprisal 5–11 层 w=4）在代码/结构化输出上系统性
漏检：programming 域 8 条 drift，CAND-A 漏 8/8，CAND-B 漏 7/8；两候选共同漏检 18 条，其中 10 条为
SQL/Python/JavaScript/Rust，另有通过工具调用产出的结构化交付物（打包清单、交通路线、购物清单 JSON）。
样本层面的证据（`docs/research_v2/zoom/missed_drift.md`）：

- 代码窗口在 onset..+16 内的逐层白化距离/routine 均值只有 0.6–2.8（被检出的 drift 为 4.7–116），层 5–15 无一层
  移动；
- 代码窗口的最近 routine 邻居全是客服工具调用 JSON 片段（`{"type":"action","name":"lookup_order",…}`），
  距离甚至小于窗口自身范数；
- 15 个反事实（层带、宽度、逐层 max、概率特征）只有概率特征找回过 1 条；
- routine 校准池中 34% 的 trace 含协议型 JSON 输出（模型在最终回复里输出 action 对象，是本 agent 的习惯）。

因此盲区的直接机制是：**routine 流形本身包含大量结构化文本的路由区域；域外代码落在该区域内。**

本方案要回答三个层次的问题：

1. 盲区是"这个 agent 的 JSON 习惯"造成的（若 routine 中没有结构化输出，代码就可检），还是路由对"代码 vs
   JSON"根本不敏感？
2. 在 routine 流形内部区分形态（散文 / JSON 动作 / KB 复述 / 列表）并按形态条件化参考，能否让代码相对
   "JSON 模式"仍然偏离？
3. 除了窗口几何，还有哪些 routing 统计量对代码敏感：结构化模式的持续长度、未见专家/未见转移、top-8 之外的
   概率质量？

以及一个必须先回答的对照：一条文本规则（代码围栏、SQL 关键字、非 action 的 JSON）能否零误报地抓住全部
代码类 drift？若能，routing 对代码的价值为零，结论应是"组合文本规则"，而非继续在路由上找。

## 1. 数据与样本

- 核心集与协议同 `docs/sequential_v2_lead_proposals.md` §1（部署侧 conformal、α=0.10、persist2 与 max 并报、
  S1 两方向）。锚点：`evidence_onset` 与 `product_onset`（`docs/research_v2/labels/product_onset_v1_adjudicated.jsonl`）
  并列报告。
- **目标样本（评价重点，n 极小）**：programming 域 8 条 drift；P3 工具调用产出的 3 条；两候选共同漏检的 18 条
  （`artifacts/agent_v2/research_v2/zoom/missed_drift/misses.json`）。每条逐条报告，不做比例推断。
- **routine 形态子集**：按 §2 的 label-free 形态标签把 routine 窗口分成散文 / 结构化两类（或更多），报告各类
  的窗口数、trace 数与占比。

## 2. 形态标签（必须 label-free，不用 drift）

两种来源，互为验证：

- **T（token 侧）**：窗口的结构化 token 比例 = 窗口 w 个 decode token 中属于 {括号、引号、冒号、逗号、分号、
  等号、换行、数字、下划线/驼峰标识符片段} 的比例；阈值取 routine 分布的分位（如 q80）或用 routine 上的
  两成分混合模型定；只用 token id（部署时可得），但要在报告中标明它是文本派生特征。
- **R（路由侧）**：只在 routine 窗口上做 k-means（k ∈ {2, 4, 8}）；检查聚类与 T 的一致性（互信息 / 纯度）。
  若某个聚类 ≈ 结构化模式，则形态可以完全从路由推断，不依赖文本。

## 3. 假设与候选（预注册 ≤ 12）

| 假设 | 候选 | 定义 |
|---|---|---|
| H-A 形态混合：routine 是多模式混合，全局白化把模式混在一起 | **F1** 形态条件白化距离 | 每个形态 m 拟合 μ_m、σ_m（routine only）；窗口按 T 或 R 分配形态，分数 = 所属形态的白化距离；变体 F1-soft：min over m |
| | **F2** 形态条件 + 全层 | 同 F1，层 0–15（早层 token 驱动的专家可能对代码 token 敏感） |
| H-B 形态持续：routine 的结构化片段短且模板化，代码是长结构化片段 | **F3** 结构化模式 run-length | 结构化模式（T 或 R）的连续窗口数；阈值 = routine trace 最长 run 的 conformal 次序统计量（模式的 run-length，不是分数的持续性） |
| | **F4** F1 ∨ F3 | 任一报警（各 α=0.05） |
| H-C 未见专家/转移：代码 token 激活 routine 几乎不用的专家或转移 | **F5** 未见专家率 | 每 token、每层，top-8 中 routine 选择率 < ε 的专家数（ε 由 routine 定）；窗口均值；conformal |
| | **F6** 未见转移率（PDM 表的空格） | 复用 PDM 深度链/时间链的未见格率，层 0–15 与 5–11 两版 |
| H-D 概率质量：top-8 之外的概率携带代码 vs JSON 的差别 | **F7** 概率版 F1 | F1 的特征换成 router probability 均值（唯一找回过代码漏检的变体） |
| H-E what-if：盲区是本 agent 的 JSON 习惯 | **F8** 去结构化 routine 池 | 把 T 判为结构化的 routine 窗口从拟合与校准池移除后重跑 CAND-A/CAND-B；FAR 按"含/不含结构化 routine trace"两种口径报告 |
| H-0 文本上界 | **F0** 文本规则 | 代码围栏、SQL/编程关键字、非 action 的 JSON 对象、缩进块；在 routine 与 drift 上逐条评价，报告命中与误报 |
| 可选 H-F prefill 条件化 | **F9** | 用 prefill 中工具结果的 JSON 密度预测 routine decode 的结构化比例，超出预测的结构化输出计分；仅在 F1–F8 完成后做 |

主配置写死：F1，形态来源 T，两形态（散文 / 结构化），层 5–15，w=8，routine=cb，模式 D，α=0.10，persist2。

## 4. 评价与判据

- 全部候选跑 S1 两方向 × 模式 D/T × α {0.05, 0.10} × 读法 {max, persist2}；全域指标（FAR 按臂、+4/+8/+16/final、
  延迟）按两套锚点报告，证明没有整体退化。
- **主判据（逐条）**：对 18 条共同漏检与 8 条 programming drift，报告每条在每个候选下的 onset..+16 最大统计量、
  阈值、余量、首个报警位置；候选的"找回数"= 在整体 FAR 不高于冻结候选 +0.02、benign/resist 臂不高于 +0.02
  的前提下变为 +16 干净命中的条数。
- **机制判据**：
  - 若 F0 在 routine 上零误报且抓住 ≥ 7/8 programming drift → 结论"文本规则解决代码类，routing 不需要在此改进"；
  - 若 F8（去结构化池）使 programming 找回 ≥ 4/8 而其他域不退化 → 盲区主要来自 agent 的 JSON 习惯；B3 应先
    确认 agent 协议下最终回复是否仍输出 JSON，并在报告中区分两种部署情形；
  - 若 F1/F2 在 F8 条件不变下找回 ≥ 4/8 → 形态条件化有效，进入 B3 预注册；
  - 若 F5/F6 对代码敏感而 F1 不敏感 → 信号在"未见专家"而非几何；
  - 若全部候选找回 ≤ 2/8 且 F0 也失败 → 记为当前方法族的能力边界，写入适用范围声明。
- 每条判据两方向都要满足才算"成立"；因 n=8，任何差异都要给出逐条名单而不是比例。

## 5. 数据纪律

- 形态标签、聚类、阈值只用 routine；drift 只用于评价与逐条报告。
- 文本派生特征（T、F0）必须单独标注为"使用 token id"，与纯路由候选分开报告；F0 是对照不是候选。
- 预注册（`docs/research_v2/fcm_prereg.md`）在跑主评价前提交；网格 ≤ 12；全部候选报告；主配置不得事后更换。
- 不修改 `src/phase_a/`、harness 核心、既有结果；新代码放 `src/research_v2/scorers/fcm.py` 与
  `scripts/research_v2/fcm/`；输出 `artifacts/agent_v2/research_v2/fcm/`。
- 复用 harness（模式 D 校准、读法、指标、审计）与 `research_v2.io.load_core()`；形态标签需要 token id，
  `LoadedTrace.token_ids` 已提供。

## 6. 交付物

- `docs/research_v2/fcm_prereg.md`；`src/research_v2/scorers/fcm.py`（F1/F2/F5/F7；F3/F4 可作为读法或组合器）；
  `scripts/research_v2/fcm/`（F0 规则、F6、F8 what-if、逐条表）；`tests/test_research_v2_fcm.py`；
- `docs/research_v2/fcm_report.md`（中文）：形态标签统计与 T/R 一致性、全域主表、26 条目标样本的逐条表
  （含 onset 前后 32 token 文本、各候选的余量与首报警）、每条机制判据的观测值与判定、失败案例、复现信息。
- 工作量：约 1–1.5 个 CPU 日。
