# P1-LF：无任务标签的单 Agent 正常流形实验计划

日期：2026-09-04（America/Los_Angeles）

状态：post-hoc exploratory development design；B1/B2 已被反复观察，不能作为独立确认

## 1. 研究问题

本实验把检测目标收缩为：

> 对一个固定 Agent，只使用其历史正常 decode routing，在线判断当前计算模式是否显著且持续地离开该
> Agent 已观察到的正常行为。

检测器不读取 workflow、workflow family、攻击渠道、目标域、token ID、token 文本或 drift 标签。一个 Agent
内部的订单、退款、工单和知识问答等正常模式共同组成一个可能多模态的正常集合。workflow family 只允许在实验
完成后用于审计近邻组成和误报分布。

P1-LF 是原 P1 的替代实验，不是独立确认。它直接针对 P1 全漏报审计发现的两个方法问题：人工 workflow-family
条件化，以及小经验 cell 的分数上限与全局阈值不兼容。

## 2. 固定数据角色

- B1：`brief=absent` 的 120 traces；B2：全部 240 traces；
- 两个探索方向仍为 B1 -> B2 和 B2 -> B1；
- source fold 0--2 中所有 non-drift traces 建立正常 reference bank；
- source fold 3--4 中所有 non-drift traces只校准完整 trace 报警阈值；
- drift trace 整条不进入 reference、位置校正或阈值校准；
- target batch 不重新拟合或重新校准。

Normal 仍定义为 `goal_plan_deviation_started=false`，包含 clean、benign control 和 resisted attack。

## 3. Label-free 正常流形

### 3.1 路由表示与距离

保持原 P1 的表示以隔离核心变量：

- decode-only causal 8-token window；
- 每层 top-8 expert-selection frequency，逐层归一化后做平方根变换；
- middle L5--L10 与 late L11--L15 分别计算 Hellinger distance 后等权；
- 每条 normal-fit trace 均匀贡献最多 8 个 reference anchors；
- raw nonconformity 是当前窗口到**整个单 Agent 正常库**的第 5 近邻距离。

近邻搜索不按 workflow 或 age 切分。reference anchor 的 leave-one-trace-out raw distance 同样对整个正常库计算。

### 3.2 只用 decode age 的连续稳健校正

正常 routing distance 可能随生成位置自然变化。位置在线可观测，因此允许用于 nuisance correction；但不再建立
硬 age cell。对 query endpoint `t`：

1. 将位置表示为 `log2(t + 1)`；
2. 从 normal-fit reference scores 中选择位置最近的 128 个；
3. 计算这些 score 的 median 与 robust scale `IQR / 1.349`；
4. scale 不低于全局 robust scale 的 25%，防止局部样本偶然塌缩；
5. 定义无上限的标准化距离：

   ```text
   z_t = (raw_t - local_median_t) / local_scale_t
   ```

这里没有经验 p-value，因而不存在 `log(N+1)` 的有限 cell ceiling。

## 4. 持续证据统计

完整 trace 的报警控制仍需要检查在线 score 是否曾越过阈值，但被取最大值的对象不再限于无记忆的单点 rarity。
本轮同时比较五种预先固定的因果 score stream：

1. `endpoint_z`：`z_t`，作为无记忆基线；
2. `rolling_mean_4`：最近 4 个 `z` 的均值；
3. `rolling_mean_8`：最近 8 个 `z` 的均值；
4. `cusum_0_5`：`C_t=max(0, C_{t-1} + z_t - 0.5)`；
5. `leaky_cusum_0_95_0_5`：`C_t=max(0, 0.95*C_{t-1} + z_t - 0.5)`。

其中 leaky CUSUM 是本轮 primary：它保留持续偏移的面积信息，同时让很久以前的证据衰减。其 `rho=0.95` 和
allowance `0.5` 在读取本轮 target metrics 前固定，不用 drift 样本选择。其余四项用于判断效果来自 label-free
几何、短时平滑还是长期累计，不能被描述成独立假设检验。

每种 stream 分别令每条 source normal-calibration trace 贡献 `max_t score_t`，使用 `alpha=0.10` 的有限样本
上尾 order statistic 得到严格 `score > threshold` 的阈值。这里的 maximum 只是把一个已经包含历史的在线状态
转换为“整条 trace 是否曾报警”，不会丢掉 CUSUM 内部已经累计的持续信息。

## 5. 评价与诊断

每个方向和每种 stream 报告：

- target non-drift trace FAR，以及 clean、benign、resisted-attack 分层；
- drift pre-onset alarm；
- 无 pre-onset alarm 条件下 evidence-onset +4/+8/+16 和 full-decode recall；
- clean hit median latency 与每 1,000 个 normal endpoints 的 alarm-onset 数；
- 按 workflow family/domain 的事后切片；
- 全部 target endpoint 的 raw distance、位置校正量和标准化 `z`；
- 第 5 近邻中与 query 同 workflow family 的比例，仅作为“无标签近邻是否自然形成模式”的审计，不进入 score。

与旧 P1 primary 并列比较，但不把 B1/B2 上更好的方法追认为 independent validation。若形成稳定候选，才冻结后
进入未来 B3。

## 6. 产物

- scorer：`scripts/run_normal_manifold_p1_label_free.py`；
- tests：`tests/test_normal_manifold_label_free.py`；
- result：`artifacts/agent_v2/normal_manifold_p1_label_free/result.json`；
- report：`docs/normal_manifold_p1_label_free_report.md`。

不得覆盖原 P1 result、routing cache、trace、标签或 boundary。
