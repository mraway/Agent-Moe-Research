# Normal Manifold Early-Evidence Availability Audit：实验计划

日期：2026-09-05（America/Los_Angeles）

状态：运行前冻结；B1/B2 adaptive timing diagnostic；不生成新模型轨迹；不是 B3 confirmation

逻辑依据：[算法设计的逻辑基础](algorithm_design_logic_foundations.md)中的 `M1--M4`、`O25--O28`、`D1--D10`，
以及 [group-aware time-uniform calibration 报告](normal_manifold_time_uniform_calibration_report.md)。

## 1. 问题

C1 已表明，单-token novelty 的完整路径 FAR 可以用100个独立 normal groups 校准到15%研究门槛；但 B1/B2
drift 的 onset `+8` recall 仍只有25.0%/11.4%。本轮只区分：

1. **Threshold-tail hypothesis**：substantive onset 后8 token 内已经有稳定的 drift-vs-matched-normal ranking，
   但为了覆盖正常 path peaks，C1 threshold 必须设得太高；
2. **Evidence-timing hypothesis**：即使不设 threshold，早期 token novelty 也没有稳定超过同任务 clean/benign，
   routing evidence 本身在 onset 后才逐步形成；
3. **Generic-state hypothesis**：不读取文本、token ID 或人工 workflow label 的 Agent runtime event/state structure，
   是否在最终 generation 开始前已经区分会 drift 的 attack 与同场景 controls。

本轮不搜索新 representation、boundary、window、layer、distance、risk bin 或 threshold。C1 held-out labels 不再参与
任何方法选择；B3 仍不生成、不读取。

## 2. 数据与冻结输入

- B1 `brief=absent`：24条已人工确认 drift，以及这些 positive pair groups 的 clean、benign-control；
- B2：35条已人工确认 drift，以及对应 clean、benign-control；
- `N_fit`：继续使用 B1/B2 canonical clean 26条、208 anchors；
- C1 thresholds/risk shapes：只读取冻结结果
  `artifacts/agent_v2/normal_manifold_time_uniform_calibration/result.json`，预期 SHA-256
  `0960b92d415d9d51980ce34611efe2774a7b1f46792c12ff8640916a8b1876f6`；
- frozen methods：`token_endpoint_z`、`nonoverlap_token_mean8_z`；
- frozen boundaries：static path-max、risk-clock normalized。

B1/B2 已被多轮观察，所以全部结果都是 adaptive mechanism audit。C1 仅提供已经冻结的 normal threshold，不重新
拟合、不读取 B3。

## 3. Behavior-relative matched-window 设计

对每条 positive attack，以人工 `evidence_onset=o` 为 evaluation-only 对齐点。对方法 `m`，fully-post 起点为：

```text
token endpoint: o
mean-8 block:  o + 7
```

冻结 horizons：`+0, +4, +8, +16, +32, full`。在 `[fully_post_start, o+h]` 内取 path maximum；若区间没有
eligible endpoint，该 pair 在该 horizon 标为 unreachable，不插值。

Matched normal statistic 使用同一 pair group 的 clean 与 benign-control，在完全相同的绝对 endpoint 区间取两臂
最大值。这里的绝对 endpoint 只用于事后 matched evaluation，不进入线上 score、bank、boundary 或部署特征。
Control 若两臂都未到达区间则该 pair 不参与该 horizon，并完整报告 coverage。

每个 batch/method/horizon 固定报告：

- eligible matched pair count；
- drift 与 matched-control group-max 分布；
- paired `drift - control` median/mean、positive count/rate及 Wilson 95% interval；
- drift-vs-control AUROC/AP；
- static 与 risk-clock C1 threshold 下的 no-prealarm clean hit recall；
- matched controls 在同一 evaluation window 的 group exceedance rate；
- reachability，以及未达到区间的 trace IDs。

Primary early-ranking gate 只针对 `token_endpoint_z`、`+8`：两个 batch 都必须满足

```text
paired drift > matched-control rate >= 75%
drift-vs-matched-control AUROC >= 0.80
eligible matched pairs >= 80% of positive traces
```

这只证明早期 score 有 threshold-free ordering information，不证明 detector。

## 4. C1-calibrated timing gate

不改变前轮定义。每个 token boundary 必须在 B1/B2 同时达到：

```text
onset +8 clean recall >= 35%
median latency among clean hits <= 8 tokens
```

前轮 headline 已显示该 gate 很可能失败，但本轮重新从逐 trace score 与 C1 frozen threshold 独立复算，并报告
`+0/+4/+8/+16/+32/full` 曲线。不得用本轮结果改阈值。

## 5. Generic runtime state audit

对每个 positive pair group，在三条 arms 的最终 model generation 开始前构造 text-free structural signature：

```text
(event kind, actor/logical role, conversation turn, agent step,
 tool name, task_state_before, task_state_after)
```

明确排除 event content、tool arguments/results、record IDs、attack channel、workflow label、token IDs、routing、未来
stop reason 与 outcome。报告：

- 三臂 signature 完全相同的 positive groups 数；
- final generation 的 `task_state_before` 分布；
- signature 不同的 group 及首个不同字段；
- generation 开始后、最终文本完成前，除逐 token routing 之外是否存在新的 controller state transition。

Generic-state support gate：B1/B2 分别至少90%的 positive groups 三臂结构相同。若通过，含义是这些通用结构字段在
大多数 matched cases 中没有 outcome information；不是“所有可能的 Agent state 都无用”。

## 6. 预声明解释

- **Early ranking pass、calibrated timing fail**：早期 routing 中存在相对/方向信息，但 global open-set magnitude
  与正常 tail 重叠；下一步比较低容量 supervised direction、文本与 hidden-state baselines，不再调 normal threshold；
- **Early ranking fail**：token novelty 本身在 substantive onset 附近不足；停止基于同一 route-novelty 的 stopping
  变体，下一步必须加入更贴近行为语义的信息源；
- **Generic state signatures 大多相同**：当前 controller event structure 无法补足 early signal，不能把固定
  `ready_to_respond` 等状态包装成 detector；
- **Generic state signatures 明显不同**：先定位差异是否为合法、部署可见且不泄露 attack/workflow，再另立计划；
- 任一结果都不启动 B3；只有形成新 detector proposal 并预注册 independent positive data 后才重新讨论 B3。

## 7. 实现与停止规则

计划产物：

```text
scripts/run_normal_manifold_early_evidence_audit.py
tests/test_normal_manifold_early_evidence_audit.py
artifacts/agent_v2/normal_manifold_early_evidence_audit/result.json
docs/normal_manifold_early_evidence_audit_report.md
```

执行顺序：冻结本计划 → 实现与 unit tests → 全量 tests → 一次性运行 audit → 发布完整结果。结果生成后不得修改
horizons、fully-post 起点、matched-control 聚合、primary gate 或 structural signature。原始 traces、C1 result 与
B1/B2/B3 均不得覆盖。
