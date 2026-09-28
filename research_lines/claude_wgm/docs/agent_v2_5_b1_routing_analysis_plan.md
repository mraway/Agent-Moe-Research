# Agent v2.5 B1 路由可分类性分析计划

日期：2026-09-04（America/Los_Angeles）

状态：冻结于 Git commit `c28a7cc`；分析已完成，结果见 `docs/agent_v2_5_b1_routing_report.md`

分析角色：development-only signal search，不是独立 confirmation

## 1. 核心问题

本轮只回答一个研究立项问题：

> 在固定职责客服 agent 已暴露于跨领域请求时，MoE route-only 信号能否区分实际开始执行域外工作的
> drift 与没有执行的 resist，并在时间上与人工标注的行为边界形成对应？

这不是 attack detector，也不试图识别 hallucination、JSON 错误、非法状态转移或所有 agent 异常。
本轮不训练神经序列模型，不引入强文本模型或 hidden-state probe，不宣称生产性能。

## 2. 冻结数据

唯一输入是 `artifacts/agent_v2/agent_v2_5_b1/`：80 个 scenario、240 条 matched trace、80 条 attack
中的 27 drift / 53 resist。数据 tree digest 为
`01a5a29783821be0ee455f53a975c10398dc528a40737b5691dac6fcc911e263`。

标签已经通过只读可见输出人工裁决，27 个 positive 均有首个跨领域 output-token 边界。分析脚本不得修改
标签、丢弃失败回复、补 seed 或根据路由结果重定义 positive。

## 3. 分析层级

### 3.1 Headline：无 response brief 的 attack outcome

主集合固定为 40 条 `arm=attack && response_brief_condition=absent`：24 drift / 16 resist。
选择该集合是因为采集后、看路由前已经发现 brief 与 outcome 强相关（absent 24/16，present 3/37）。
在主集合中：

- 五个预分 fold 都含正负例；
- 八个 target domain 各有 5 条且都含正负例；
- 所有样本都暴露于明确 attack，故标签不是 attack-present；
- `preregistered_fold` 完整留出 attack family，禁止同 family 变体跨训练/测试泄漏。

所有 headline 指标使用这五个冻结 fold 的 out-of-fold（OOF）分数。

### 3.2 Secondary：全部 attack

全体 80 条 attack 用于敏感性分析。必须分别报告 brief absent/present，另报告一个只使用
response brief、workflow、channel 和 target-domain one-hot 的 nuisance baseline。由于 present 子集只有
3 个 positive，不单独把它的分类性能作为主要证据。

### 3.3 Matched controls

对每个 held-out fold，attack-only 模型只能使用其他四折训练。随后给 held-out fold 的 clean、benign 和
attack 三臂评分。对 24 个 drift scenario 报告 drift attack 是否高于同 scenario 的 clean 与 benign，
并报告 53 个 resisted attack 相对其 matched controls 的分数。这个诊断用于发现模型是否只对 attack
措辞、marker 或领域词汇报警。

## 4. 冻结观察窗口

每条 v2.5 trace 的 controller 已在模型生成前完成工具流，因此分析对象是唯一最终
`model_generation` episode 的 decode route。固定窗口为：

- first 8 decode tokens；
- first 16 decode tokens；
- first 32 decode tokens；不足 32 的一条主集合 trace 使用其全部 27 token 并明确标记；
- full decode，作为可分性上界而非早期告警结果。

此外读取最终 prefill 的整体路由作为 context-only diagnostic。Prefill 若已经高度预测最终 outcome，只能
说明上下文或生成倾向可预测，不能证明执行中的任务切换。任何“早期”结论都必须注明在评分前已经生成了
多少 token。

## 5. 冻结特征与模型

Route-only 候选只保留两个已有、低容量聚合：

1. `route_selection`：每层每专家进入 top-8 的 token 比例，16 × 64 = 1,024 维；
2. `route_probability`：router logits softmax 后按窗口求均值，16 × 64 = 1,024 维。

主分类器为现有 fixed-penalty ridge：只用训练折估计标准化参数，标签编码为 -1/+1，L2 penalty 固定为
特征维数，不基于测试折调参。`route_selection + ridge` 是预指定主候选；`route_probability + ridge`
是敏感性候选。不得按结果选择 layer 子集、expert 子集或新的超参数再称为 headline。

低成本 sanity controls 为：

- 同窗口的 2,048 维 signed token-ID hash + 相同 ridge；
- 输出长度；固定前缀上长度应没有信息，full 上仅作混淆诊断；
- 上述 nuisance metadata + ridge；
- protocol/unknown-action 与 agent-step 分布；本批预期均为常数。

这些基线不是论文级方法比较，只用于排除最明显的假信号。

## 6. 冻结切分与指标

主评估使用配置中的 5 个 `preregistered_fold`。每个 attack family 只在一个测试 fold 中出现；训练折的
标准化、模型参数和任何阈值均不得看到测试折。另做 leave-one-target-domain-out 敏感性分析，检验方向是否
跨 8 个域保持。

报告：

- pooled OOF AUROC 与 average precision；
- 每 fold AUROC（可计算时）和 drift/resist 平均分差；
- 八个 target domain 中平均 drift score 高于 resist 的数量；
- route 相对 token-hash、length 和 nuisance-only 的结果；
- 所有 trace 的 OOF score，不隐藏反例。

不把 240 条 arm 当成 240 个独立实验单位，不报告训练集 accuracy，也不在 B1 上给出论文式置信区间。

## 7. 行为边界时间诊断

分类性能和时间对应是两个不同命题，必须分开报告。

### 7.1 Prefix 与真实边界

对每个 prefix 结果，把 27 个 positive 分成：边界已经发生（`boundary < prefix`）与边界尚未发生。
若 prefill 或边界前 prefix 已经可分类，解释为 propensity/context signal；只有 decode 分数在行为边界附近
发生系统性变化，才支持 execution-monitoring signal。

### 7.2 Held-out rolling-window 异常

使用 8-token rolling window 做一个预指定的一类诊断：

1. 每个测试 fold 的正常 route profile 只由其他四折、相同 brief 条件的 clean、benign 以及 resisted
   attack 建立；
2. 用全层 router-probability JSD 为 held-out decode 的每个 token/window 评分；
3. 对 positive 比较边界前 8 token 与从边界开始 8 token 的平均分；
4. 把该变化与相同 brief 的 held-out resisted traces 中所有可用位置形成的经验分布比较；
5. 同样计算 8-token token-hash window change，判断时间对应是否只是输出词汇变化。

报告 positive 中 post-boundary route score 上升的比例、中位变化、边界附近峰值比例，以及首次越过训练负例
95% 分位阈值相对行为边界的 token 延迟。阈值只用于时间诊断，不生成一个新的 headline accuracy。

## 8. 解释规则

以下组合可视为“值得进入 B2”的强 development signal：

1. 主集合的 route-selection OOF AUROC 至少 0.75；
2. 八个目标域中至少六个方向一致；
3. route 不是仅由 brief/nuisance baseline 解释；
4. route 相对 token hash 有早期增益，或者 rolling score 在人工边界附近出现 token baseline 不能完全解释的
   系统性上升。

这些阈值只决定是否值得继续，不把 B1 变成确认实验。若只有 full decode 可分、token hash 同样强，结论是
“跨领域内容可见后路由反映任务语义”；若 prefill 已完全分开而边界附近无变化，结论是“路由预测服从倾向，
但尚不支持运行时任务切换监测”；若跨 family/domain 失效，则不进入 B2 或进一步收缩命题。

任何正面结果在 B1 中只用于选择唯一特征、窗口和阈值。随后必须冻结实现，并使用新业务实体、新 attack
措辞和新 seed 的 B2 做一次独立 confirmation。
