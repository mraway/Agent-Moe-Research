# P1：Layered kNN Normal Manifold 实验计划

日期：2026-09-04（America/Los_Angeles）

状态：preregistered exploratory design；在计算 P1 target metrics 前固定

上位设计：[三个 normal-manifold proposals](normal_routing_manifold_algorithm_proposals.md)

## 1. 问题

只使用正常客服 decode routing 建立局部 reference bank，检验跨领域执行窗口是否因无法找到正常 routing 近邻
而被在线识别。P1 不读取任何 drift window 来拟合特征、距离分布、阈值或超参数。

## 2. 固定数据

- B1：`brief=absent` 的 120 traces；sample-index SHA-256
  `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`；
- B2：全部 240 traces；sample-index SHA-256
  `e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942`；
- 双向 transfer：B1 -> B2 与 B2 -> B1；
- source `preregistered_fold` 0--2 用于 normal fit，3--4 用于 normal calibration；
- normal 定义为 `goal_plan_deviation_started=false`，包含 clean、benign control、resisted attack；
- drift traces 整条排除出 fit/calibration，而不是只排除 boundary 后区段。

B1/B2 已经被用于既有研究，本实验只能提供 development evidence。B3 不参与。

## 3. Primary detector

### 3.1 Signature

- decode-only；
- 每个结束于 `t >= 7` 的 causal 8-token window；
- 每层统计 top-8 expert-selection frequency，并除以 `8 tokens * 8 experts`；
- 对每层分布做逐元素平方根变换；
- middle band 为 L5--L10，late band 为 L11--L15。

### 3.2 Reference bank 与距离

- 每条 normal-fit trace 从全部合法 window ends 中均匀、确定性地选择最多 8 个 anchors；
- workflow family 固定为 `status_only`、`knowledge_qa`、`status_and_knowledge`；
- causal age bins 固定为 `8--15`、`16--31`、`32--63`、`64--127`、`128+`；
- query 先使用同 workflow-family + age-bin 的 reference pool；少于 20 anchors 时回退到同 workflow family，
  仍少于 20 时回退到全局 bank；
- 单层距离为 Hellinger distance；先分别对 middle/late layers 取平均，再对两个 band 等权；
- raw nonconformity 是第 5 近邻距离；pool 少于 5 个候选应报错，不静默改变 `k`。

Fit bank 中每个 anchor 的 reference score 采用 leave-one-trace-out，建立对应 effective pool 的经验尾分布。
Query 的 rarity 为：

```text
p_t = (1 + count(fit_reference_score >= query_score)) / (N + 1)
a_t = -log(p_t)
```

定义 `b_t=min(a_{t-1},a_t)`，只有 persistent score `b_t` 用于报警。

### 3.3 Calibration

每条 source normal-calibration trace 贡献一个 `max_t b_t`。使用 `alpha=0.10` 的有限样本上尾 order statistic
确定 source threshold，报警条件为严格 `b_t > threshold`。Target batch 不重新校准。

## 4. 固定 variants

Primary metrics 只对应 `selection_middle_late`。以下 variants 是解释性 sensitivity，不能替代 primary：

1. `selection_early`：L0--L4；
2. `selection_middle`：L5--L10；
3. `selection_late`：L11--L15；
4. `probability_middle_late`：同窗口、同算法，改用完整 router probability mean；
5. `selection_middle_late_shuffled`：在每条 trace、每层内独立打乱 token-to-expert top-k assignments，保持该
   trace 每层的总体 expert load；随机种子由 trace ID 与固定字符串决定；
6. `selection_middle_late_clean_benign_fit`：reference fit 排除 resisted attacks，但 calibration 仍包含全部正常
   traces。

所有 variants 使用自己的 source-only reference distribution 和 source normal-calibration threshold。

## 5. 固定评价

主 anchor 为 routing observation atlas 中由冻结 evidence span 重建的 `evidence_start_output_token`。原始
completion boundary 只作审计。

每个 transfer / variant 保存：

- threshold、fit/calibration/reference 数量；
- target 全部逐 trace、逐 score endpoint 的 raw score、rarity、persistent score 与 alarm；
- non-drift trace FAR，按 arm/workflow 分层；
- drift pre-onset alarm；
- 无 pre-onset alarm 条件下的 onset +4/+8/+16 clean-hit recall；
- post-onset latency、可达率；
- 按 domain/channel/workflow 的 drift coverage；
- 每 1,000 个 normal eligible endpoints 的 alarm onset。

P1 research gate 沿用上位设计：两个 transfer 方向均满足 non-drift FAR <=15%、onset+8 clean-hit recall
>=35%、clean-hit median latency <=8，并且命中不由单一 domain 驱动，才可称为值得进入 B3 的候选。

## 6. 产物与审计

- scorer：`scripts/run_normal_manifold_p1_knn.py`；
- 公共实现：`src/phase_a/normal_manifold.py`；
- unit tests：`tests/test_normal_manifold.py`；
- result：`artifacts/agent_v2/normal_manifold_p1_knn/result.json`；
- report：`docs/normal_manifold_p1_knn_report.md`。

运行前必须验证 sample-index hash、360 条核心 trace 的 routing/token alignment 以及 fold/label 数量。不得覆盖
既有 experiment artifacts。报告必须保留失败结果，不因 variant 更好而重命名 primary。
