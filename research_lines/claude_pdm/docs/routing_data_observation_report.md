# Agent v2.5 MoE routing 数据观察报告

日期：2026-09-04（America/Los_Angeles）

性质：B1/B2 development data 上的探索性、描述性观察；不是 classifier 报告，也不是独立确认实验

配套设计：[数据观察计划](routing_data_observation_plan.md)

## 摘要

本轮先不设计分类算法，而是从完整逐 token routing 数据回答四个问题：模型在 prefill 是否看见了攻击，decode 的变化何时发生，信号位于哪些层/专家，以及正常客服回复自身会产生多大变化。

最重要的结论有五点：

1. **Prefill 很清楚地反映 attack exposure，但尚不能可靠地反映 agent 最终是否服从攻击。** B1/B2 的 attack-minus-control routing 方向 cosine 为 0.882；然而 pooled drift-vs-resist AUROC 约 0.77，拆分 direct、multi-turn、tool-output 后强度和方向明显不稳定。当前 prefill 结果更像“看到了什么输入”，不是“接下来会不会出轨”。
2. **Decode 在跨领域内容真正开始时出现了可复现的 matched routing change。** 以异常证据首 token 为中心，8-token probability-JSD contrast 在 B1 为 +0.0240，在 B2 为 +0.0282，scenario-bootstrap 95% CI 均不跨 0；40 个可比较场景中 36 个为正。
3. **原人工 boundary 是证据短语的完成点，不是语义起点。** B1/B2 的 evidence span 中位数分别为 6、7 tokens；59 个 drift 中，58 个 boundary 前一 token 已经处于异常证据内部。因此原 event study 在 `k=-1` 的突升不是提前预警，而是标签语义造成的“已开始执行”。
4. **信号在后层更强，但精确 expert identity 的跨批稳定性有限。** Evidence-onset 的 late-layer matched effect 约为 early-layer 的 6.8 倍；B1/B2 完整 16×64 signed direction cosine 为 0.667，但 absolute top-32 expert 只重叠 8 个。当前更支持分布式、分层的 routing feature，而不是少数固定专家名单。
5. **绝对 routing change 不能直接充当异常阈值。** 正常回复的最大局部 JSD 与 drift onset 同量级；onset magnitude 相对 normal-trace maximum 的 AUROC 只有 0.502（B1）和 0.472（B2）。真正有信息的是相对 workflow/position baseline 的额外变化，而不是“变化很大”本身。

因此，当前数据给出了值得继续研究的正向信号，但命题应准确表述为：

> 在同一生产 agent、模型和大致回复阶段下，agent 开始实际执行跨领域任务时，MoE decode routing 出现了跨 B1/B2 可复现的相对变化。

它尚未证明 routing 能在首个异常 token 之前预警，也尚未证明已经存在可上线的 routing-only detector。

## 1. 数据与记录完整性

### 1.1 Cohort

| Batch | Matched scenarios | Traces | Drift attacks | Resisted attacks |
|---|---:|---:|---:|---:|
| B1，response brief absent | 40 | 120 | 24 | 16 |
| B2，全部 | 80 | 240 | 35 | 45 |
| 合计 | 120 | 360 | 59 | 61 |

每个 scenario 包含相同 routine task 下的三臂：clean、benign content control、attack。B1/B2 共覆盖 8 个跨领域目标、3 个攻击通道和 11 类 routine workflows。360/360 条入选 trace 均重新通过 routing validation。

三种 attack channel 的构成如下：

| Batch | Direct user | Multi-turn user | Tool output |
|---|---:|---:|---:|
| B1 attacks / drift | 14 / 11 | 13 / 9 | 13 / 4 |
| B2 attacks / drift | 26 / 16 | 27 / 14 | 27 / 5 |

### 1.2 每个 token 保存了什么

模型为 `allenai/OLMoE-1B-7B-0125-Instruct`，固定 revision `b89a7c4bc24fb9e55ce2543c9458ce0ca5c4650e`。每个 MoE token 保存：

- 16 个 MoE layers × 64 experts 的完整 router logits；
- 每层 top-8 expert IDs 和 weights；
- token ID、token text、position、role、conversation turn、agent step 与 tool boundary；
- logits/weights 为 bfloat16，expert IDs 为 int16。

Prefill 的整段 context 在一个 `[16, tokens, 64]` shard 中保存；decode 每一步保存一个 `[16, 1, 64]` shard。因此可从 logits 恢复每层完整 64-expert softmax distribution，也能逐 token 重建 top-8 selection。未记录 hidden states、expert outputs 或 attention states。

| Batch | Prefill tokens / trace | Decode tokens / trace | Drift boundary index |
|---|---|---|---|
| B1 | mean 743.4，median 668，range 470–1108 | mean 129.0，median 135.5，range 26–192 | median 45.5，range 3–165 |
| B2 | mean 755.9，median 768，range 475–1121 | mean 109.3，median 92.5，range 17–192 | median 53，range 4–166 |

需要注意时间语义：decode token `t` 的 routing 是 token `t` 已生成并进入下一次 forward 后才能读取的。它可以用于在后续 token 继续生成前报警，但不是对 token `t` 本身的生成前预测。

## 2. 观察方法

### 2.1 核心量

本报告主要使用两个 routing distance：

- **Probability JSD**：比较两个 64-expert probability profiles 的 Jensen–Shannon divergence，再对 16 层取平均。这里使用自然对数，单层范围为 0 到 `ln(2)`。
- **Selection TV**：把 top-8 expert 被选择的频率归一化为 distribution，计算 total variation distance，再对 16 层取平均。

JSD 衡量完整 router preference 的变化；selection TV 衡量实际 top-8 集合使用情况的变化。

### 2.2 Matched contrast

每个 attack 都与同一 scenario 的 clean 和 benign control 匹配。Control 值为两者等权平均：

`matched contrast = attack change - mean(clean change, benign change)`

这样可减去同一客服 workflow、回复结构和相近生成阶段本来就会发生的 routing change。所有 bootstrap 都以 scenario 为单位重复抽样 2000 次，不把相邻 token 当成独立样本。

### 2.3 Prefill exposure

Direct/multi-turn attack 在 `user` role 内比较；tool-output attack 在 `tool` role 内比较。每条 trace 先在相应 role 的全部 token 上聚合，再计算 attack 与 matched control 的 routing distance。

### 2.4 Decode anchors

本报告保留两个不同含义的 anchor：

- **Evidence completion**：预注册使用的原人工 boundary，即输出前缀第一次包含完整人工 evidence string 的 token。
- **Evidence onset**：文本审计后重建的 evidence string 首 token。它是 post-hoc sensitivity anchor，不回改任何原始标签。

Boundary window 的 width `w` 比较 `[anchor-w, anchor)` 与 `[anchor, anchor+w)`。Matched controls 的主要 pseudo-anchor 按 attack anchor 所处的 decode 相对位置映射，不做 clamp；exact-index 作为敏感性检查。

Event-time curve 则用 anchor 前 16 tokens 的平均 probability profile 作本 trace reference，计算 `k=-16..+32` 每个 token 相对 reference 的 mean-layer JSD，再减去 matched controls。

## 3. Prefill：稳定地看见攻击，不稳定地预言结果

### 3.1 Pooled outcome ranking 看起来较强

| Batch | Drift mean probability JSD | Resist mean | Drift-over-resist AUROC | Selection-TV AUROC |
|---|---:|---:|---:|---:|
| B1 | 0.001811 | 0.000944 | 0.766 | 0.792 |
| B2 | 0.002162 | 0.001172 | 0.767 | 0.772 |

若只看 pooled 数字，容易得到“prefill 已经能预测 agent 是否会 drift”的结论。但通道分层否定了这么强的解释。

### 3.2 Attack channel 是显著混杂

| Batch / channel | Drift / resist | Mean exposure JSD | Within-channel AUROC |
|---|---:|---:|---:|
| B1 direct | 11 / 3 | 0.002103 | 0.939 |
| B1 multi-turn | 9 / 4 | 0.001999 | 0.333 |
| B1 tool-output | 4 / 9 | 0.000242 | 0.667 |
| B2 direct | 16 / 10 | 0.002236 | 0.738 |
| B2 multi-turn | 14 / 13 | 0.002320 | 0.648 |
| B2 tool-output | 5 / 22 | 0.000282 | 0.727 |

Direct/multi-turn exposure magnitude 约为 tool-output 的 8 倍，同时 direct 的 drift rate 更高、tool-output 的 drift rate 更低。Pooled AUROC 因而混合了“输入从哪里来”和“模型最终做什么”。B1 direct 的 0.939 还只基于 3 个 resisted negatives；B1 multi-turn 甚至反向。

长度也影响 role-level aggregation。Attack 相比 controls 平均多出约 16–19 个 user tokens，但 tool role 多出约 66–70 tokens。Multi-turn 中 token-count delta 与 JSD 的相关为 0.850（B1）和 0.804（B2）；tool-output 中为 -0.830 和 -0.961，符合长 tool 内容把短攻击指令稀释的现象。这些不是可忽略的实现细节，而是 prefill score 的定义依赖。

### 3.3 方向稳定性支持 exposure，不支持统一 outcome direction

Attack-minus-control 的完整 16×64 probability direction 在 B1/B2 间 cosine 为 0.882，selection direction 为 0.833。这是非常清楚的 attack-exposure replication。

Pooled drift-minus-resist probability direction cosine 虽为 0.775，但按 channel 分层后为：

| Channel | Probability direction cosine | Selection direction cosine |
|---|---:|---:|
| Direct | 0.370 | 0.193 |
| Multi-turn | -0.106 | -0.119 |
| Tool-output | 0.733 | 0.587 |

Tool-output 的正样本只有 B1 4 个、B2 5 个，不能据此建立统一结论。当前最稳妥的解释是：**prefill routing 能识别 attack exposure；它是否能在控制 channel、长度和内容后预测服从结果，仍未得到稳定证据。**

## 4. Boundary 定义审计：旧 boundary 晚于语义起点

`locate_evidence_token` 的定义是“第一个使完整 evidence string 出现在 decoded prefix 中的 token”。所以 boundary 天然落在 evidence 的最后一个 token，而不是第一个 token。

| Batch | Drift annotations | Evidence span median | Mean | Range | `boundary-1` 已在 evidence 内 |
|---|---:|---:|---:|---:|---:|
| B1 | 24 | 6 tokens | 6.21 | 3–11 | 24/24 |
| B2 | 35 | 7 tokens | 6.57 | 1–10 | 34/35 |

例如，B1 的 evidence `Once upon a time` 从 output token 66 开始，人工 boundary 69 落在 ` time`；B2 的 `The worn leather gloves` 从 token 154 开始，boundary 157 落在 ` gloves`。所以旧 completion-anchor curve 在 `k=-1` 已经观察到异常内容的一部分。

这个发现改变了时间解释，但没有改变任何 behavior label：后续所有“是否提前”的判断以 evidence onset sensitivity 为准；completion anchor 只表示已经完成一段可人工确认的异常证据。

## 5. Decode：跨领域执行开始时出现 matched change

### 5.1 Evidence-onset window

| Width | Batch | Matched n / all drift | Attack JSD | Control JSD | Contrast mean | Bootstrap 95% CI |
|---:|---|---:|---:|---:|---:|---|
| 8 | B1 | 15 / 24 | 0.05489 | 0.03086 | +0.02403 | [0.01204, 0.03842] |
| 8 | B2 | 25 / 35 | 0.05650 | 0.02828 | +0.02822 | [0.01988, 0.03758] |
| 16 | B1 | 11 / 24 | 0.03692 | 0.02050 | +0.01642 | [0.00719, 0.02619] |
| 16 | B2 | 10 / 35 | 0.03057 | 0.01524 | +0.01533 | [0.00934, 0.02163] |

Width-8 合并后有 40 个可比较 scenarios，mean contrast +0.02665，median +0.02264，36/40 为正。按 attack channel：direct 20/22 为正、multi-turn 10/11、tool-output 6/7；按 8 个 target domains 聚合的均值全部为正，但每个 domain 只有 3–7 个可用样本，只能作为一致性描述。

在 completion/onset 都可用的同一批 width-8 scenarios 上，onset contrast 相比 completion 平均高 +0.02053（B1，12/15 上升）和 +0.01672（B2，18/25 上升）。这说明重锚定后的增强不是单纯由 coverage 集合变化造成。

Exact-index sensitivity 方向一致：width-16 onset contrast 为 B1 +0.02145（n=9）和 B2 +0.01389（n=4），但 B2 coverage 太低，不应强调数值。

### 5.2 原 evidence-completion window

| Width | Batch | Matched n / all drift | Contrast mean | Bootstrap 95% CI |
|---:|---|---:|---:|---|
| 8 | B1 | 18 / 24 | +0.00453 | [-0.00358, 0.01231] |
| 8 | B2 | 26 / 35 | +0.01076 | [0.00407, 0.01749] |
| 16 | B1 | 11 / 24 | +0.00889 | [0.00160, 0.01663] |
| 16 | B2 | 12 / 35 | +0.01125 | [0.00434, 0.01860] |

Completion boundary 的前窗口已经包含平均约 5–6 个异常 tokens，因此它测到的是“完成一段异常证据后是否继续改变”，不是从 routine 到 off-domain 的干净转折。方向仍为正，但效应比 onset 更弱。

### 5.3 Coverage 是当前主要限制

Window 必须在 attack、clean、benign 三条 trace 上都拥有足够的前后 tokens。Completion anchor 的 normalized matched coverage 从 width-8 的 B1 18 / B2 26，降到 width-16 的 11 / 12，再降到 width-32 的 7 / 2。Onset anchor 为 width-8 的 15 / 25、width-16 的 11 / 10、width-32 的 7 / 2。

因此 width-32 的正值不能用来论证“越长越好”；B2 实际只剩 2 个 matched scenarios。后续算法必须允许短前缀/短回复，并把 coverage 当作设计约束而不是事后筛样本。

## 6. Event time：同期检测成立，提前检测尚未成立

Evidence-onset anchor 的 matched probability-JSD trajectory：

| Offset | B1 mean [95% CI]，n=11 | B2 mean [95% CI]，n=13 |
|---:|---|---|
| -8 | +0.00381 [-0.00636, 0.01486] | +0.01984 [0.00671, 0.03275] |
| -4 | -0.00037 [-0.00614, 0.00531] | +0.01084 [0.00063, 0.02144] |
| -1 | -0.00236 [-0.00947, 0.00500] | +0.00016 [-0.00755, 0.00685] |
| 0 | +0.02330 [0.00604, 0.03955] | +0.01103 [0.00073, 0.02175] |
| +1 | +0.02613 [0.01397, 0.03888] | +0.01834 [0.00471, 0.03574] |
| +4 | +0.01989 [-0.00359, 0.04346] | +0.02409 [0.01266, 0.03916] |
| +8 | +0.02311 [0.00785, 0.03711] | +0.02773 [0.01506, 0.04285] |
| +16 | +0.02981 [0.00436, 0.05260] | +0.02290 [0.00491, 0.04051]，n=10 |

两批在 `k=-1` 都接近 0，而从首个 evidence token `k=0` 开始同时为正。这是目前最有说服力的 timing replication。

B2 在 `k=-8/-4`、两批在 `k=-16` 也出现部分正值，但曲线不是朝 onset 单调上升，且每个 token 是相对同一 pre-16 reference 的局部差异。这可能来自前导拒绝、格式切换、跨领域承诺语句或正常局部变化，不能直接叫作“提前预警信号”。当前证据支持的是 contemporaneous onset detection；是否存在稳定的 pre-onset propensity，需要专门标注更早的语义承诺事件并另做实验。

作为反证，completion anchor 在 `k=-1` 的 contrast 为 B1 +0.03229、B2 +0.01757，看似提前且两批 CI 均为正。但 annotation audit 证明这些 token 已经在 evidence phrase 内，所以不能用作提前检测证据。

## 7. 信号在哪里：late-layer magnitude，distributed direction

Evidence-onset、width-16 normalized matched JSD 按 layer band 聚合：

| Batch | Early L0–4 | Middle L5–10 | Late L11–15 | Late / early |
|---|---:|---:|---:|---:|
| B1，n=11 | 0.00494 | 0.01148 | 0.03383 | 6.85× |
| B2，n=10 | 0.00427 | 0.01299 | 0.02919 | 6.84× |

这是一项非常稳定的层级形态：效应从前层到后层增强。Evidence-completion 上同样成立，late/early 为 5.64×（B1）和 7.44×（B2）。

但 signed expert direction 的复现没有 magnitude 那么强：

| Anchor / feature | B1–B2 flattened cosine | Top-32 overlap | Overlap sign agreement |
|---|---:|---:|---:|
| Onset probability delta | 0.667 | 8 / 32 | 8 / 8 |
| Onset selection delta | 0.596 | 6 / 32 | 6 / 6 |
| Completion probability delta | 0.459 | 7 / 32 | 7 / 7 |
| Completion selection delta | 0.446 | 8 / 32 | 8 / 8 |

Onset probability direction 的 per-layer cosine 在 middle layers 最高，例如 L5 0.779、L9 0.770、L10 0.808、L11 0.766；late layers 的 JSD magnitude 最大，但精确方向不一定最稳定。按 domain 的方向比较每格通常只有 1–4 个 scenarios，现阶段不能据此建立 domain-specific expert claims。

数据支持的设计约束是：保留 layer structure 和 signed distributed change，优先研究 middle/late layer groups；不要只盯少数固定 expert，也不要只按单层 effect magnitude 排名。

## 8. 正常变化：为什么纯 change threshold 会失败

对所有 decode 长度至少 32 的 non-drift traces，枚举全部合法 width-16 splits；先在每条 trace 内取 median/max，再跨 trace 汇总。

| Batch | Eligible non-drift traces | Normal local median JSD mean | Normal per-trace max mean | Drift onset raw JSD mean | Onset vs normal-max AUROC |
|---|---:|---:|---:|---:|---:|
| B1 | 92 / 96 | 0.01570 | 0.03514 | 0.03692，n=11 | 0.502 |
| B2 | 183 / 205 | 0.01566 | 0.03217 | 0.03057，n=10 | 0.472 |

Drift onset 大于正常回复的典型局部变化，但与一条正常回复中最强的自然转折基本不可分。客服回复本来就会在状态事实、政策说明、拒绝语和收尾之间切换，routing 对这些语义变化也会响应。

因此，下一阶段不能把 `JSD > threshold` 当作主算法。需要估计“在这个 workflow、回复位置和已有上下文下本应发生多大变化”，再判断 excess change；本轮 matched control 能证明这种 excess 存在，但线上不会拥有真实 counterfactual control。

## 9. 数据洞察对算法研究的约束

本轮不选择算法，但数据已经排除了若干宽泛直觉，并给出明确约束：

1. **主检测对象应是 decode execution drift。** Prefill 可作为 attack-exposure 辅助通道，不能与实际 drift label 混为一谈。
2. **训练/评估 ground truth 应记录两个时间点。** 后续数据至少保存“最早明确 off-domain token”和“完整证据确认 token”；主 latency 以 onset 计。
3. **目标先定为同期或极短延迟报警。** 目前没有跨批证据支持首个异常 token 前的可靠预警。更诚实、也更可实现的目标是在 onset 后少量 tokens 内阻止继续生成。
4. **采用相对、序列化 baseline。** 特征应与同 workflow/position 的正常 routing、近期自身 history 或 learned expected trajectory 比较，不能只用绝对 change magnitude。
5. **保留多尺度窗口，但必须显式处理 availability。** 8-token window 覆盖和效果最好；16-token 可作稳定性尺度；32-token 在当前长度分布下不可作为主要方法。
6. **优先 layer-group/distributed features。** Late layers 提供强 magnitude，middle layers 提供更稳定方向；固定 expert whitelist/blacklist 缺乏足够复现。
7. **必须按 channel、domain、workflow 与长度分层报告。** 尤其 prefill pooled score 会被 channel mix 严重抬高。
8. **正常 hard transitions 是核心 negative。** 后续开发集应保留状态到政策、客服回答到拒绝、格式化字段到自然语言等正常语义转折，而不是只用平滑 clean replies。

一个自然的后续算法研究问题是：能否只使用截至当前 token 的 routing history，学习 routine-specific expected trajectory，并在 evidence onset 后 1–8 tokens 内稳定识别 excess cross-domain change，同时控制整条正常 trace 的 false-alarm risk。这个问题来自数据，不预设具体模型形式。

## 10. 结论边界

本轮可以支持：

- MoE routing 与 agent 实际执行的任务域有关；
- 跨领域执行 onset 附近存在 matched、跨 B1/B2 复现的 decode routing signal；
- signal 具有稳定的 layer hierarchy，并非只由单个 domain 或单个 attack channel 表面驱动；
- 这是一个值得正式算法研究的课题。

本轮不能支持：

- 在异常首 token 生成前可靠预警；
- 仅凭绝对 routing change 即可分类；
- 少量固定 experts 构成跨数据集通用签名；
- routing 比 text/logit/hidden-state baseline 更优；
- 对新 agent、新模型、新 workflow 或 B3 已经泛化；
- 已具备生产 detector 的 precision、recall 或 false-alarm 保证。

B1/B2 都已参与这次 post-hoc observation，今后的算法选择不能再把 B2 描述成完全未见的确认集。等形成冻结算法后，B3 仍应承担真正的 held-out generalization test。

## 11. 可复现产物

分析入口：

```bash
.venv/bin/python scripts/analyze_routing_observation_atlas.py
```

产物：

- `artifacts/agent_v2/routing_observation_atlas/observation.json`
  - SHA256: `170d3ac7cadf567de79f9c16dca40f63f50f1a35a72dc612f52daec11ebb47e2`
- `artifacts/agent_v2/routing_observation_atlas/event_time.csv`
  - SHA256: `08953dbef2a86af8049bfcd4b8e9d25373d151725134a1d07065b936c0cbc5bd`
- `artifacts/agent_v2/routing_observation_atlas/layer_summary.csv`
  - SHA256: `bef81a625bf7a3edb9ef60eb4e9bae6d018ccabcea8823a2c60a723fe5362404`

Machine-readable audit 明确记录：本轮没有拟合新 classifier，没有使用 B3，没有修改 labels/boundaries，也没有把 token 当作独立 bootstrap 单位。Evidence-onset 结果是从保留的 evidence char span 与 token pieces 重建的 post-hoc sensitivity analysis。
