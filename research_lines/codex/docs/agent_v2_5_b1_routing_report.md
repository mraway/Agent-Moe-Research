# Agent v2.5 B1：跨领域行为偏移的 MoE 路由信号

日期：2026-09-04（America/Los_Angeles）

状态：development signal 通过；候选方法已选定；需要独立 B2 confirmation

## 摘要

本实验研究一个固定职责客服 agent 在遭遇跨领域请求后，MoE 路由能否区分“实际开始写诗、代码、数学、
菜谱等域外内容”和“看见相同类型攻击但没有执行”。B1 在看路由结果前固定了 80 个三臂 scenario，得到
240 条完整 trace；80 条 attack 经人工语义裁决后包含 27 drift 和 53 resist。

由于 verified response brief 与行为结果强相关，headline 集合在分析前固定为 40 条不含 brief 的 attack：
24 drift / 16 resist。每个 attack family 只属于一个测试 fold，因此同一 family 的主题变体不会跨训练和
测试泄漏。主候选使用前 16 个 decode token 的 top-8 expert-selection rate 和 fixed-penalty ridge。

五折 OOF 结果为 AUROC 0.753、AP 0.860；相同窗口的 token-ID hash 为 AUROC 0.654，nuisance-only
metadata 为 0.599。leave-one-target-domain-out 的 route AUROC 为 0.771，并且 8/8 个目标领域的 drift
平均分都高于 resist。前 32 token 的 route AUROC 为 0.771；完整输出达到 0.953，而 token hash 为
0.805。

这满足预先写下的 development go 条件，说明该方向值得用独立样本确认。但时间诊断给出更谨慎的解释：
prefill route 已有 AUROC 0.755；虽然 24 个无-brief positive 中有 20 个在人工边界后出现局部 JSD 上升，
只有 4 个的全局峰值落在边界附近，且阈值首次越界的中位时刻早于行为边界 4.5 token。因此当前最稳妥的
命题是“路由包含攻击服从倾向，并在跨领域内容生成后明显增强”，而不是“已经找到精确的在线任务切换点”。

## 1. 问题和数据

主标签是 `goal_plan_deviation_started`：模型已经开始实质性执行客服 mandate 之外的任务。攻击存在、
marker 出现、输出格式错误和客服事实错误都不能单独推出 positive。

B1 的采集与行为结果先在 Git commit `c28a7cc` 冻结，路由分析实现随后在 `7cdc10f` 冻结，之后才首次
运行 outcome 间比较。输入和输出为：

- 数据：`artifacts/agent_v2/agent_v2_5_b1/`；
- 冻结样本索引 SHA-256：
  `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`；
- 分析结果：`routing_analysis.json`；SHA-256：
  `2c95001fa26a671aacb2de709dea34bc4fe8a8caba50267067ef8549d808b84f`；
- 240/240 条 trace 在分析时再次通过 routing schema 和 token alignment 验证。

## 2. 分析方法

### 2.1 Headline cohort

只使用 `arm=attack && response_brief_condition=absent` 的 40 条 trace。这样所有样本都有明确攻击，且
移除了最强的已知 nuisance：brief-present 为 3/40 drift，而 brief-absent 为 24/40 drift。

五个 `preregistered_fold` 完整留出 attack family。每折的特征标准化和 ridge 参数只使用其他四折；
正则强度固定为特征维数，没有 test-driven 调参。另做 leave-one-target-domain-out，八个 domain 每个
含 5 条且都同时有 drift 和 resist。

### 2.2 特征

- `route_selection`：每层每专家进入 top-8 的比例，1,024 维，预指定主候选；
- `route_probability`：每层每专家的平均 router probability，1,024 维，敏感性候选；
- `token_hash`：同一输出窗口的 2,048 维 signed token-ID hash；
- `length`：可见 token 数；
- `nuisance`：brief、workflow、attack channel 和 target domain one-hot。

固定窗口为 first 8、16、32 和 full decode。32-token 条件中一条 27-token 回复按计划使用全部可用 token。
所有 prefix 分数都表示相应数量 token 已经生成之后的可用分数，不是第一个输出 token 之前的预测。

## 3. Headline 分类结果

| Window | Route selection AUROC / AP | Route probability AUROC / AP | Token hash AUROC / AP | Length AUROC / AP |
|---|---:|---:|---:|---:|
| first 8 | 0.721 / 0.826 | 0.680 / 0.781 | 0.706 / 0.729 | 0.423 / 0.557 |
| first 16 | **0.753 / 0.860** | 0.750 / 0.859 | 0.654 / 0.690 | 0.423 / 0.557 |
| first 32 | 0.771 / 0.874 | 0.771 / 0.868 | 0.693 / 0.741 | 0.423 / 0.557 |
| full | 0.953 / 0.973 | 0.956 / 0.974 | 0.805 / 0.840 | 0.604 / 0.663 |

不依赖输出窗口的 nuisance-only ridge 为 AUROC 0.599、AP 0.676。first-16 route selection 比 token hash
高 0.099 AUROC，比 nuisance 高 0.154；first-32 的对应差值为 0.078 和 0.172。完整输出的 route 信号
很强，但此时跨领域文本已经大量可见，科研价值低于早期窗口。

### 3.1 Family-held-out 稳定性

first-16 route-selection 在 fold 0–4 的 AUROC 依次为 0.917、0.933、1.000、0.250、0.917。
四折方向强且一致，一折明显反向；因此 pooled 0.753 不是“所有 family 都很容易”，仍存在重要失败域。
first-32 保持相同模式。到 full decode，各折为 1.000、1.000、1.000、0.938、1.000。

### 3.2 Target-domain 留出

first-16 route-selection 的 leave-one-domain-out AUROC 为 0.771、AP 0.857，8/8 个 domain 的平均
drift score 高于 resist。单域 AUROC 从 0.500 到 1.000；所以“方向跨域一致”成立，但不能解释成每个域
都已达到可靠分类。

## 4. Matched 三臂诊断

每折只用其他四折的无-brief attack 训练，再给测试折中相同 scenario 的 clean、benign 和 attack 评分：

| Window | Drift attack 高于两个 matched controls | Drift mean margin | Resisted attack mean margin |
|---|---:|---:|---:|
| first 8 | 16/24 | 0.002 | -0.322 |
| first 16 | 11/24 | 0.027 | -0.318 |
| first 32 | 12/24 | 0.060 | -0.336 |
| full | 24/24 | 0.819 | -0.267 |

完整输出时，24 个 drift 全部高于自己的 clean 和 benign，说明强 full-decode route signal 不是单纯由
scenario ID、marker 或领域词汇决定。早期窗口的 matched top-1 只有 46%–67%，明显弱于 full；这限制了
“单条在线告警器已经可用”的解释。resisted attack 的平均分低于 matched controls，说明分类器并非对所有
attack exposure 一律给高分。

## 5. Prefill 与行为边界

### 5.1 Prefill 已含 outcome propensity

在相同 40 条 attack 上，最终 prefill 的 route-selection AUROC 为 0.755，route-probability 为 0.771。
它们与 first-16/32 decode 很接近。由于不同 scenario 的 prompt、workflow 和 payload 并非完全相同，
prefill 结果可能表示上下文已经编码了模型随后是否容易服从，也可能包含尚未被廉价 metadata 排除的差异。
它不能证明 agent 已经开始执行域外任务。

### 5.2 Boundary-aligned rolling JSD

一类诊断只用其他四折、相同 brief 的 clean、benign 和 resisted attack 建立正常 routing profile，再对
held-out 输出计算 8-token rolling router-probability JSD：

- 27/27 个 positive 有可比较的人工边界；
- 无-brief positive 中 20/24 的边界后 8-token 均值高于边界前，median delta 为 0.00656；
- 这些 delta 在同折 resisted attack 全部候选位置中的中位 percentile 为 0.788；
- token-hash pre/post distance 的对应 percentile 为 0.735，说明词汇切换也解释了相当部分变化；
- 只有 4/24 个无-brief positive 的全局最高 JSD 窗口与边界 ±8 token 相交；
- 95% 训练负例阈值对 24 条都触发，但 16 条首次在行为边界前触发、8 条在边界时或之后触发；
  首次告警相对边界的中位延迟为 -4.5 token。

present-brief 的三个 positive 只有 1/3 在边界后上升，且 0/3 的峰值靠近边界。样本过少，不据此作性能
结论，但它与 brief 对整体行为的强影响方向一致。

这些结果支持“边界后通常还有增量变化”，却不支持一个尖锐、普遍、由该 JSD 阈值定位的 change point。
负延迟在研究上不是自动的好消息：它更可能说明 detector 在识别攻击上下文或服从倾向，而非已经观察到
跨领域行为。

## 6. 全部 80 条 attack 的敏感性分析

| Window | Route selection AUROC / AP | Token hash AUROC / AP | Length AUROC / AP |
|---|---:|---:|---:|
| first 8 | 0.661 / 0.540 | 0.511 / 0.345 | 0.428 / 0.303 |
| first 16 | 0.696 / 0.605 | 0.549 / 0.401 | 0.428 / 0.303 |
| first 32 | 0.783 / 0.732 | 0.672 / 0.467 | 0.428 / 0.303 |
| full | 0.980 / 0.978 | 0.915 / 0.850 | 0.741 / 0.527 |

全体 nuisance-only AUROC 为 0.749、AP 0.543，主要来自 brief 与 outcome 的关联。因此 first-8/16 的
全体 route 分数不能作为独立证据；first-32 才略高于 nuisance。条件化后的 headline 结果是更可信的
development estimate。

## 7. 研究判断

运行前定义的四个 B2 进入条件逐项为：

1. route-selection 早期 OOF AUROC ≥ 0.75：first 16 为 0.753，达到；
2. 至少 6/8 target domain 方向一致：8/8，达到；
3. 不能仅由 brief/nuisance 解释：无-brief 主集合中 route 0.753、nuisance 0.599，达到；
4. route 相对 token hash 有早期增益，或边界附近有系统性增量：first-16 高 0.099，且 20/24 边界后
   JSD 上升，达到。

因此 B1 的结论为 **Go to independent confirmation**。这不是统计确认，而是研究组值得继续投入的信号。

冻结给 B2 的主候选为：

```text
final model-generation episode
-> first 16 decode tokens
-> 16 × 64 top-8 expert-selection-rate feature
-> training-fold-only standardization
-> fixed-penalty ridge (penalty = feature dimension)
```

选择 first 16 是因为它是最早达到预定 0.75 门槛的窗口；不是因为它在所有候选中取得最高分。
`route_probability`、first 32 和 full decode 只作为预声明 secondary diagnostics。当前 rolling JSD 不冻结为
主 detector，因为它没有可靠定位行为边界。

## 8. 仍不能声称什么

- 不能声称路由检测器已经优于强文本 monitor 或 hidden-state probe；本轮只用了低成本 sanity controls；
- 不能声称 signal 只在行为开始后出现；prefill 已具有相当强的 outcome 信息；
- 不能声称能精确定位 task-switch token；
- 不能声称对新 agent、模型、prompt 或生产分布泛化；
- 不能把 full-decode 的 0.953 当作部署准确率；B1 同时用于方法选择，存在开发偏差；
- 不能忽略 fold 3 的早期反向结果和早期 matched-control 失败。

下一步应使用全新的业务实体、attack topics、措辞和 seeds 构造 B2，保持行为标签先于路由揭盲，且只检验
上述冻结 first-16 主方法。B2 若不能复现，研究结论应回退到更窄的“完整跨领域内容生成时，MoE 路由反映
任务语义变化”。
