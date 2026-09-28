# Agent v2.5 MoE Routing 数据观察计划

日期：2026-09-04（America/Los_Angeles）

状态：exploratory observational design；在运行本轮 atlas 前固定

## 1. 目标

本轮不设计、训练或选择新的 detector。目标是使用 B1/B2 已保存的逐 token 完整 routing 数据，回答：

1. prefill routing 主要反映 attack exposure，还是也与最终 drift/resist outcome 有关；
2. decode routing 的变化何时相对人工行为边界出现，是否超过正常回复同阶段的变化；
3. 信号集中在哪些 MoE layers / experts，方向能否在 B1 与 B2 间复现；
4. 哪些数据形态和混杂因素应约束下一阶段算法设计。

这是已经查看过 B1/B2 结果后的 exploratory analysis，不是独立确认。B3 不参与。

## 2. Cohort 与统计单位

- B1：只使用 `response_brief_condition=absent` 的 40 个 matched scenarios、120 traces、24 drift；
- B2：全部 80 个 matched scenarios、240 traces、35 drift；
- 合计：120 scenarios、360 traces、59 drift attacks；
- 每个 scenario 的 clean、benign control、attack 三臂始终一起分析；
- bootstrap、effect 和 matched contrast 以 scenario/trace 为单位，不把相邻 token 当作独立样本。

原始 trace、行为标签和 `goal_plan_deviation_start_output_token` 不修改。所有分析使用同一模型 revision 下的
16 layers × 64 experts；expert identity 只在同一 layer 内比较。

## 3. 数据完整性与覆盖

重新验证所有 360 条入选 trace，并报告：

- prefill / decode token count 分布；
- prefill token role 及 attack channel 覆盖；
- drift boundary、domain、workflow、channel 分布；
- 8/16/32-token boundary window 与 event-time 的可用样本数；
- B1/B2 sample-index hash 和 routing validation 数量。

## 4. Prefill：attack exposure 与 outcome

### 4.1 Matched exposure contrast

按 prefill token role 分段。对每个 scenario，attack-bearing role 固定为：

- `direct_user` / `multi_turn_user`：`user`；
- `tool_output`：`tool`。

在该 role 内分别计算 clean、benign、attack 的 per-layer full router-probability profile 和 top-8 selection
distribution。matched control profile 为 clean 与 benign profile 的等权平均。

每个 scenario 报告 attack 与 matched-control 的：

- per-layer probability JSD；
- per-layer selection total variation；
- entropy 和 top-two margin 差；
- 16-layer 平均 exposure magnitude。

按 batch、channel 和最终 drift/resist 分层。用单变量 AUROC 仅描述 exposure magnitude 对 outcome 的排序，
不将其称为 classifier。

### 4.2 跨 batch 方向稳定性

计算每个 scenario 的 `attack profile - matched-control profile`。分别对 B1/B2 求均值，并报告：

- flattened 16×64 cosine / Pearson correlation；
- per-layer cosine；
- absolute top-32 `(layer, expert, sign)` overlap 与重叠项 sign agreement；
- drift-minus-resist exposure direction 的相同稳定性指标。

若 exposure 很强但 outcome 方向不复现，应解释为看见攻击，而不是执行漂移。

## 5. Decode：boundary-centered event study

### 5.1 Drift boundary 与 matched pseudo-boundary

对每个 drift attack 使用冻结人工 boundary。其 clean / benign matched controls 使用两种 pseudo-boundary：

1. exact-index：与 attack 相同 output-token index；
2. normalized-position：按 `boundary / attack_decode_length` 映射到 control decode length。

不为提高覆盖率而 clamp；不满足窗口要求的样本记为 unavailable。主要 matched observation 使用
normalized-position，exact-index 用于敏感性检查。

### 5.2 Boundary window contrasts

对 width 8、16、32（主要报告 width 16），比较 boundary 前后两个不重叠 block：

- per-layer probability JSD；
- per-layer selection TV；
- entropy、router margin、effective-expert-count 的 post-minus-pre；
- 16×64 probability / selection signed delta。

对每个 scenario 计算：

`drift change - mean(clean pseudo-change, benign pseudo-change)`。

报告 matched difference 的均值、中位数、scenario bootstrap 95% CI、逐层结果及 B1/B2 分开复现。

### 5.3 Event-time trajectory

对 boundary 前 16 token 的 mean probability profile 建立同 trace reference。对相对位置 `k=-16..+32` 的
每个 token，计算其 routing probability 与 reference 的 mean-over-layer JSD。

matched controls 使用 normalized pseudo-boundary 和相同计算。每个 batch / k 报告：

- drift median、IQR、mean 和 coverage；
- 每个 scenario 的 drift-minus-mean-control difference 的同类统计；
- `k={-16,-8,-1,0,1,4,8,16,24,32}` 的 bootstrap mean 95% CI。

如果变化在 `k<0` 已出现，不能自动解释为 detector 泄漏；需结合文本检查它是 semantic commitment、正常
过渡，还是 boundary 标注偏晚。

## 6. Layer / expert 稳定性

使用 width-16 matched probability delta contrast：

`(drift post-pre) - mean(control post-pre)`。

对 B1/B2 分别聚合并报告：

- flattened cosine / Pearson correlation；
- 16 个 layer 的 cosine、effect magnitude 和 matched JSD difference；
- absolute top-32 `(layer, expert)` overlap、sign agreement；
- 按 target domain 的 cross-batch direction cosine（明确小样本）；
- B1/B2 共同高贡献 layer/expert，而不是只在单 batch 排名靠前的项。

## 7. Normal-variation 与解释边界

额外计算 non-drift trace 内所有可用 width-16 split 的 probability JSD，先在每条 trace 内汇总 median/max，
再跨 trace 报告分布。drift boundary magnitude 与 normal trace maxima 的比较只作为 descriptive ranking。

报告必须区分：

- attack exposure；
- boundary 前 propensity / semantic commitment；
- boundary 后 executed drift；
- 正常客服回复的局部语义变化。

本轮没有 sequence-aware text representation，不能声称 routing 独有；没有新的独立 batch，不能声称泛化已
确认；大量 expert 比较只用于生成假设，不进行逐项显著性宣称。

## 8. 产物

- machine-readable observation JSON；
- event-time CSV；
- layer summary CSV；
- 一份可独立阅读的中文分析报告，包含主要数据洞察、反例、限制以及对后续算法设计的约束。
