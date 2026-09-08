# MoE 跨领域漂移检测：算法设计的逻辑基础

日期：2026-09-05（America/Los_Angeles）

状态：working draft v0.1；用于研究组讨论，不是算法预注册，也不冻结任何具体方法

相关契约：[目标数据与 Trace 契约](target_trace_spec.md)；现有证据总览：
[可行性报告](moe_routing_cross_domain_drift_feasibility_report.md)、
[数据观察报告](routing_data_observation_report.md)、
[P1-LF 报告](normal_manifold_p1_label_free_report.md)与
[P1-LF zoom-in](normal_manifold_p1_label_free_zoom_report.md)，以及
[group-aware time-uniform calibration 报告](normal_manifold_time_uniform_calibration_report.md)。

## 0. 本文解决什么问题

此前的算法设计混合了四类东西：研究目标、模型机制事实、数据观察和实现便利。例如，`decode age` 条件化来自
对简单单轮数据的稳定化需要，却逐渐被当成“正常输出在相同位置可比较”的前提；`max_t score_t` 原本只是把
在线路径归约为 trace-level 指标，也容易被误解成任务漂移的定义。

本文重新建立一条可审计的推理链：

```text
研究需求 R + 机制事实 M + 已有观察 O
                 ↓
             可检验假设 H
                 ↓
             设计原则 D
                 ↓
       算法 proposal、实验和否证条件
```

本文使用以下标记：

- `R`：来自研究目标或数据契约的规范性要求；
- `M`：由自回归生成和路由采集方式决定的机制事实；
- `O`：仅在当前模型、Agent 和 B1/B2 数据上成立的经验观察；
- `H`：尚未证明、必须由实验检验的研究假设；
- `D`：由前述内容推出的算法设计原则；
- `Q`：尚需研究组明确的开放决策。

任何未来算法中的关键选择，都应注明它依据哪个 `R/M/O/H`。仅仅因为实现方便，不能成为核心方法的逻辑
基础。

## 1. 研究对象与 estimand

### R1：检测对象是实际行为漂移，不是攻击暴露

主事件是固定职责 Agent **已经开始实质性执行** mandate 之外的跨领域工作，例如开始写诗、代码、数学推导或
故事。下面均不是主阳性：

- 输入、工具结果或上下文中出现跨领域指令；
- Agent 引用、解释或拒绝该指令；
- 同领域 workflow、参数、权限、状态或事实错误；
- 任务未完成、JSON/协议错误或一般生成退化。

因此主标签是 `goal_plan_deviation_started`，不是 `attack_present`、任务成功与否或输出格式正确与否。

### R2：研究范围是固定 Agent 的 per-agent 检测

当前问题是：

> 当前计算模式是否显著偏离这个 Agent 已观察到的正常行为？

跨 Agent、跨模型共享 detector 不属于当前研究范围。算法可以对每个固定 Agent 单独拟合，但不应要求运行时
已知该 Agent 当前属于哪个人工 workflow 子类。

### R3：当前主研究路线是 normal-only open-set detection

最终 detector 的模型参数、normal support 和部署 threshold 只能从可信正常历史学习，不能依赖预先收齐诗歌、
代码等异常域。B1/B2 的 drift labels 可以在当前开发阶段用于比较、否证 H1--H4 和选择待冻结的 proposal；由此
作出的选择必须在 B3 前冻结，也不能再宣称是“完全没有看过正例的算法发现”。使用 drift positives 直接拟合
directional-relative classifier 则是重要诊断对照，但回答的是另一个、更容易的问题。

### R4：目标是同期或短延迟检测，不是分叉前预言

有价值的报警窗口从最早可识别的跨领域执行/承诺开始，到完整候选回复发布或副作用执行之前结束。当前证据不
支持在首个异常 token 之前预测随机采样将走向 drift 还是 resist。

### R5：算法必须在真实负例上保持沉默

主零假设不只是流畅、短小的 clean replies，还包括：

- benign content controls；
- attacked-but-resisted replies；
- 合法地引用或讨论域外内容；
- 仍在客服域内的 hallucination、任务遗漏、格式错误和生成退化。

这些 hard negatives 不一定全部适合进入流形拟合，但必须进入校准或评测，不能事后删除来改善 FAR。

### R6：本阶段不是生产 detector 认证

当前目标仍是证明是否存在足够强、可重复、值得正式立项的研究信号。1% 生产级 FAR、强文本/hidden-state/LLM
基线、多模型与跨 Agent 泛化属于后续正式研究。当前仍必须诚实报告误报、延迟和失败边界。

## 2. 不可绕过的机制事实

### M1：相同上下文和相同已生成前缀产生相同路由

在固定模型和推理实现下，router 只接收当前 forward 的隐藏状态。S1 的同 prompt mixed outcomes 在 token 分叉
前具有逐元素相同的路由。这排除了“router 在相同前缀下预知下一次随机采样”的解释。

### M2：token `t` 的路由只能在 token `t` 已经生成后使用

我们的采集路径先生成 token `t`，再将它送入下一次 forward 并记录其路由。因此在 `t` 上报警可以阻止
`t+1` 及之后的生成，不能阻止 `t` 本身。所有 latency 解释必须遵守这个时序。

### M3：滑动窗口和连续 endpoint 高度相关

相邻的 8-token 窗口共享 7 个 token，不能被当成独立样本。拟合、bootstrap、阈值和不确定性估计的独立单位
必须按实验生成结构选择；同一 pair/scenario 内的 arms 不能冒充独立单位，通常应以 pair group 为最高重抽样或
风险聚合单位。无论如何都不能把 endpoint 当成独立样本。

### M4：复杂 Agent 没有天然的绝对 token 时间对齐

一次 generation 的 token 100 可能处于工具参数、客服事实、政策解释、拒绝、总结或退化尾缀。多轮 Agent 中
decode age 每轮归零；若跨轮累计，它又主要反映对话长度。绝对 token 位置不是任务状态的可靠代理。

## 3. 现有数据真正支持什么

以下观察全部是 development evidence，不是对新 Agent 或新模型的一般定律。

| ID | 当前观察 | 直接含义 | 不能推出 |
|---|---|---|---|
| O1 | Prefill attack-minus-control 方向跨 B1/B2 一致，但 drift/resist 排序受 attack channel 强烈混杂 | Prefill 路由可靠反映看到了什么输入 | Prefill 能可靠判断随后是否服从 |
| O2 | 相同 prompt、相同 token prefix 的 mixed outcomes 在分叉前路由完全相同 | 主信号只能在生成发生变化后出现 | router 可做 pre-branch outcome prediction |
| O3 | Evidence-onset 附近 width-8 matched JSD contrast 为 B1 `+0.0240`、B2 `+0.0282`，40 个场景中 36 个为正 | 实际跨域生成开始时有可重复 routing change | 任意大的 routing change 都是 drift |
| O4 | 正常 trace 的最大局部 JSD 与 drift onset 同量级，onset-vs-normal-max AUROC 为 `0.502/0.472` | 正常客服也有强语义转折 | 纯 change magnitude 可直接阈值化 |
| O5 | Onset effect 在 late layers 约为 early 的 6.8 倍；middle signed direction 更稳；精确 top experts 跨批重叠有限 | 应保留 layer structure 与分布式 expert 信息 | 存在少数通用“异常专家” |
| O6 | Supervised directional-relative pilot 双向 `+8` recall 约 `43%--46%`、延迟 4--5 tokens，FAR 约 `11%--13%` | 方向性和相对变化有价值 | normal-only 方法会自动学到同一方向 |
| O7 | P1/P3 normal-only primary 在合理 FAR 下最终只检出约一半 drift，median latency 12--71 tokens | 长篇域外输出最终会变得异常 | 已有方法能及时识别 task onset |
| O8 | P2 的 `Q` 有强单向信号，但发生经验 tail 饱和和 normal subspace batch shift | normal residual 中包含信号，也存在明显批次不稳定 | 单一 source normal tail 可直接迁移 |
| O9 | P1-LF 去掉 workflow cells 后，post-onset mean z 仍为 `3.137/2.984`；32/35 和 22/24 条 drift trace mean 超过正常 q95 | 人工 workflow label 不是当前分离信号的必要条件 | 已有在线 stopping rule 已经可靠 |
| O10 | P1-LF endpoint 双向 FAR 为 `3.9%/21.9%`，及时 recall 与 FAR 明显不对称 | 窗口可分不等于校准可迁移 | 一个固定全局 threshold 足够 |
| O11 | 代表误报发生在拒绝数学证明时；11个选中层均持续分离 | routing 能敏感地表示正在处理数学语义 | route novelty 等同于行为违规 |
| O12 | 21 条误报中 17 条连续越线至少2次；rolling mean 改善很小；CUSUM 被合法语义 mention 和重复 `\\n` 长期积分 | 误报不是普遍的单点噪声；无条件累计也不成立 | persistence 或 CUSUM 天然优于 maximum |
| O13 | CUSUM threshold top-3 全来自同一 matched scenario 的三个 arms | trace-level calibration 仍会受相关 group 重复计权 | trace 数等于独立校准单位数 |
| O14 | 现有 sequential 实验主要评价最终 model-generation episode | 当前结论只覆盖单 episode 内的 decode | 已验证完整多轮 Agent trajectory 监控 |
| O15 | 完全移除 absolute-age normalization 后，raw kNN 的 fully-post trace-mean AUROC 为 `0.995/0.997`，34/35 与 24/24 条 drift 超过 normal q95 | 当前正常流形分离信号不依赖 absolute position correspondence | 已解决在线 evidence aggregation 与 calibration |
| O16 | Local-density score 将 endpoint score-age correlation 从约0.37降到0.04--0.07，但产生 normal `z>10` tails，endpoint full recall 为 `0/35` 与 `2/24` | position-free local normalization 能稳定 bulk，却可能放大稀有正常 peak | 当前 local-density 公式可作为 detector |
| O17 | Conditional-successor 与 state score 的 endpoint correlation 为 `0.96--0.99`，path-max correlation 约0.984；表示 AUROC 没有改善 | 相邻 width-8 states 共享7/8 routing token，当前 transition residual 没隔离新增信息 | successor residual 天然提供独立 transition signal |
| O18 | `state_floor_4` 与 state path-max correlation 为 `0.988/0.992`；drift-vs-resisted AUROC 只提高 `0.006/0.016`，FAR 未改善 | 在高度重叠窗口上重复要求 persistence，主要重复计算同一 routing event | 四个连续 endpoint 等价于四份独立持续证据 |
| O19 | Pair-group calibration 下 transition endpoint 以相同 FAR 将 +8 recall 从 `4→7/35`、`6→9/24`，但误报集合相同且反向 behavior AUROC 不变 | 可能存在 timing 信息，也可能只是共线 score 的 threshold/rank 变化 | 已证明 H2 或可冻结 transition detector |
| O20 | B2 -> B1 primary false alarms 全部发生在第64个 eligible endpoint 后，且集中在少数相关 pair groups | 完整 path 的 time-at-risk 和独立 group 数是核心风险问题 | absolute age 应重新进入 normality score |
| O21 | 固定 non-overlap state 的 fully-post AUROC 为 `0.994/0.995`，单 token novelty 为 `0.995/0.998` | 正信号不依赖 sliding windows 重复使用同一 routing token | 已得到及时 stopping rule |
| O22 | Token endpoint 的相邻 score correlation 为 `0.324/0.459`，与 sliding state endpoint correlation 为 `0.371/0.498` | 单 token score 隔离了更大比例的新增 routing information | 独立信息自动具有更强行为特异性 |
| O23 | Staggered non-overlap min-2 相对 matched current 的 drift-vs-resisted AUROC 只提高 `0.006/0.026`；token q25 相对 mean 下降 `0.020/0.029` | 消除 overlap 后，当前 persistence 实现仍无足够双向增益 | execution/refusal 的 route-only trajectory 已被一般性否定 |
| O24 | 七条 independent-innovation streams 均未通过 detector gate；最好的双向 +8 recall 仍不超过 `17.1%/29.2%` | representation 与 stopping/calibration 必须继续分开 | retrospective AUROC 接近1即可部署 |
| O25 | 新 C1 的100个 calibration groups 使有限样本阈值固定为第91阶；单-token static/risk-clock 在60个未见 family groups 上均为 `9/60=15%` FAR | 扩大独立完整路径可以把总体正常风险校准到研究门槛边缘 | 已达到生产风险保证；Wilson upper 仍为26.1% |
| O26 | Token risk-clock 与 static 的 final FAR 相同，但 risk step 32 的累计误报为 `11.7%` vs `3.3%`，且 knowledge-QA trace FAR 为58.3% | 当前 time-at-risk shape 没有稳定优于 full-path exchangeability，overall gate 会掩盖 workflow 缺口 | risk normalization 天然改善早期风险 |
| O27 | Block static FAR 为16.7%；block risk-clock overall 为11.7%，但16-step exposed subgroup 为22.2%，两个候选均按预注册失败 | 稀疏 block observation 没有解决长路径 tail risk | 更少 looks 自动意味着更稳的 episode FAR |
| O28 | C1 threshold 下 token static/risk 的 B1/B2 +8 recall 都只有 `25.0%/11.4%`；median latency只从 `18/25` 改为 `16/23` | 当前瓶颈已从小样本阈值转向 evidence timing 与行为特异性 | normal calibration 通过即可启动 B3 |
| O29 | 在能构造相同绝对 endpoint counterfactual 的组中，token novelty 到 onset +4 的 drift-vs-matched-control AUROC 已为 `0.888/0.855`，+8 为 `0.921/0.831` | 早期 routing 中存在相对排序信息，global novelty magnitude 没有充分利用它 | 条件 ranking 可自动变成完整零假设 detector |
| O30 | +8 matched coverage 为 B1 `22/24`、B2 `26/35`；全部11个失配组都是 clean 与 benign 在 drift onset 对应位置前已结束 | 正常 counterfactual 的长度支持是 early relative evaluation 的独立识别条件 | 可对已经结束的 normal path 外推不存在的后续 routing |
| O31 | 59/59 positive pair groups 的 clean/benign/attack 在最终 generation 前具有完全相同的 text-free controller event/state signature，全部从 `ready_to_respond` 开始 | 当前通用 controller 结构字段不能补充 matched outcome 信息 | 任意更细粒度的 Agent state、文本或 hidden state 都无用 |

## 4. 必须停止使用的隐含假设

### A1：相同 decode age 表示相似的正常语义阶段

该假设没有来自研究目标，也没有得到数据支持。`decode age` 不再进入主异常分数、normal manifold
条件化、近邻选择或局部尺度估计。它可以保留为风险暴露计数和事后诊断变量。

### A2：一次大 routing change 就表示 task drift

O4 已直接否定。正常客服在事实、政策、拒绝和收尾之间也会产生同量级变化。

### A3：route novelty 等同于 mandate violation

O11 显示，引用和拒绝域外请求同样会激活相应语义路由。算法必须面对 execution-vs-mention/refusal，而不能用
攻击词是否出现代替行为判断。

### A4：任一 endpoint 越线就是最合理的报警定义

`max_t score_t` 是一个敏感、可因果实现的 baseline，也可把既定 stopping rule 归约为 trace 是否曾报警；
它不是由 task-drift 语义必然推出的证据聚合方式。孤立 peak、持续偏移和状态转移必须分别检查。

若 calibration 与未来正常 episode 的完整路径（包括长度分布）可交换，对每条 calibration path 的 maximum
取阈值可以控制 per-episode “曾经报警”风险；所以 current failure 不是一个纯粹的 multiple-testing 代码错误。
真正的问题是完整路径分布并未稳定迁移，而且 maximum 的行为语义过于贫乏。

### A5：持续正偏差可以无限累积

O12 已否定无界 CUSUM 的一般合理性。合法域外 mention、格式尾缀和稀有正常模式也可能持续。远期历史对当前
报警的影响必须有明确、可审计的衰减、复位或时间一致性理论。

### A6：workflow family 是必要且在线可得的正常类别

O9 表明去掉人工 family 后信号仍存在。未来 Agent 可能没有可靠细分任务标签，因此 workflow/domain labels
只用于数据切片和误差分析，不进入主 detector。

### A7：正常模型拟合成功自然意味着阈值可迁移

P1/P2/P1-LF 均显示几何、尺度和 tail calibration 是不同问题。必须分别验证 representation support、score
normalization、sequential aggregation 和 stopping-rule calibration。

### A8：length stop 或协议错误应从负例中删除

它们不是跨领域主阳性。删除会把研究问题缩成“格式完美的短回复中识别明显长篇异常”，产生虚假的可分性。
它们只能作为辅助标签和误差解释变量。

## 5. 时间在新逻辑中的三个不同角色

以后必须区分下面三种“时间”，不能再统一叫 position correction。

### 5.1 绝对位置：不用于定义正常性

`t=100` 只说明当前 generation 已观察到100个 token，不说明 Agent 正处于哪个语义或 workflow 阶段。主 score
不得假设跨 trace 的绝对位置对应。

### 5.2 局部顺序：可能包含任务切换信息

“客服状态 A -> 政策解释 B”与“客服状态 A -> 代码生成 C”是局部转移问题。正常模式可以出现在任意 turn 或
任意 token 位置。使用最近 routing history、transition 或 subsequence 是合理的可检验假设，但其有效性尚未
被 normal-only 结果证明。

### 5.3 风险暴露时钟：必须用于控制 repeated looks

观察192个 endpoint 比观察32个 endpoint 有更多误报机会。这里的时间只回答“到目前为止检验了多少次”，不
要求不同 trace 的同位置语义相似。未来 threshold/boundary 可以随累计检验次数变化，但必须由完整 normal
paths 校准，而不是独立地对每个 age bin 取分位数。

因此暂定原则是：

> 绝对 decode age 不进入 normality score；相对时间顺序进入候选动态模型；累计观察次数进入 anytime risk
> control。

## 6. 正常数据的三种角色不能再混用

旧契约与后续实验在“什么算 normal fit”上存在张力。目标契约只允许
`normal_reference_eligible=true` 的 canonical clean traces 建立最干净画像；P1/P1-LF primary 则把 clean、
benign 和 resisted attack 的所有 non-drift traces 都放入 normal model。今后应显式拆成三个集合：

| 集合 | 作用 | 暂定内容 | 禁止用途 |
|---|---|---|---|
| `N_fit` | 学习正常表示、流形或转移 | 可信、任务完成、无攻击、无漂移、路由完整的 canonical normal | 不能用 drift；不能按 target 结果扩充 |
| `N_cal` | 校准“哪些情况不应报警”的 stopping risk | 独立 group 的 canonical normal，加预声明且已确认无 drift 的 hard-negative strata | 不能与 `N_fit` 重用同一 pair；不能用未来 stop reason 选择阈值 |
| `N_eval` | 测试完整零假设 | clean、benign、resisted、合法 mention/refusal、格式/任务失败和生成退化 | 不能因高分而事后删除 |

这是当前的**建议拆分**，尚需研究组确认。关键逻辑是：表示学习需要避免把未知错误吸收到 normal manifold；
风险校准和评测则必须覆盖所有按主标签不应报警的行为。`normal for fitting` 与 `negative for the estimand` 不是
同一个概念。

## 7. 算法必须满足的接口契约

对固定 Agent/模型，令一次 decision episode 中截至输出 token `t` 可见的路由历史为
`R_{0:t}`。未来 proposal 必须定义：

```text
normal model: θ = Fit(N_fit)
causal evidence: E_t = F(R_0:t; θ)
alarm time: τ_hat = inf{t : Stop(E_0:t, risk_clock=t) = true}
```

并满足：

- `D1` **因果性**：`E_t` 与报警只读取截至 `t` 的路由及部署时天然可见的边界元数据；
- `D2` **行为目标一致**：优化目标是 execution drift，不是 attack、topic word、长度、格式或任务失败；
- `D3` **无绝对 age 语义条件化**：不得按 token index 选择 normal neighbors、中心或尺度；
- `D4` **不依赖人工 workflow 标签**：任务类别只用于审计切片；
- `D5` **group 等权**：同一 pair/scenario 的多个 arms 和长 trace 不能按 endpoint 数获得无限权重；
- `D6` **历史影响明确**：说明记忆长度、衰减或复位规则，并解释重复稳定模式为何不会无限积累；
- `D7` **完整 stopping-rule 校准**：校准对象是整条因果报警路径，不是把大量 endpoint 当独立样本；
- `D8` **多轮语义明确**：必须声明在 model turn 边界复位、继承还是分层维护状态；
- `D9` **失败可解释**：分别输出 state novelty、transition novelty、累计证据和阈值贡献，不能只给最终二元 alarm；
- `D10` **route-only primary**：token text、真实 outcome、人工 boundary 和未来长度不得进入在线 score。

`D3` 不禁止把“已进行的检验次数”用于 time-uniform risk boundary；`D10` 也不禁止在评价阶段运行 text/token
sanity control。

## 8. 当前可检验假设，而不是既定事实

### H1：Agent-specific 正常状态支持可复用

不论正常模式出现在第几个 turn 或 token，只要当前路由窗口属于已授权行为，就能在该 Agent 的历史正常库中
找到足够相似的局部支持。P1-LF 的 post-onset separation 支持继续检验，但合法数学拒绝误报说明当前证据不足。

**否证信号**：跨 batch 的 normal local-density shift 与 drift separation 同量级，或大量合法稀有模式持续被
判为 out-of-support。

**当前状态**：representation 层获得强支持。Age-free raw kNN 在两个方向均保持近乎完整的 fully-post
trace-mean 分离；新 C1 已把单-token full-path normal FAR 校准到15%研究门槛，但历史 +8 recall 仍为
25.0%/11.4%，尚未转化为及时 detector。

### H2：正常局部转移比绝对状态更稳定

正常 Agent 可以进入许多状态，但从近期状态转向下一个状态的局部路径可能具有可学习结构，而且该结构对绝对
输出位置平移不敏感。

**否证信号**：在 normal-only、无 workflow/age 的条件下，transition residual 在 onset 后不升高，或与正常
hard transitions 完全重叠。P3 的低容量 forecast 已给出部分负证据，因此新实验不能只换一个更大 CUSUM。

**当前状态**：conditional-successor 构造不支持，但独立 token innovation 获得限定正证据。单 token novelty
的 fully-post AUROC 为 `0.995/0.998`，所有可比较 drift 均 post > pre，且与 sliding state 的 endpoint
correlation 只有 `0.371/0.498`。这支持以 token innovation 作为后续动态模型的基础信息单位；它不证明
transition model、行为特异性或 stopping 已解决。C1 risk-clock 没有提高 B1/B2 +8 recall，只略微降低命中
样本 latency，并把更多正常报警提前，故 time-at-risk normalization 也不能替代行为动态模型。
Behavior-relative audit 进一步显示，在 counterfactual 可达的组中，+4/+8 token 已出现较强 relative ranking；
因此“所有 routing evidence 都很晚”并不准确。当前缺口是 global open-set magnitude、正常 tail 与 matched-length
coverage 共同造成的，而不是再换一个 risk-clock boundary 就能解决。

### H3：执行与 mention/refusal 在路由轨迹形状上存在差别

单个语义窗口可能不可分，但真正执行域外任务也许会进入并维持新的路由状态，而拒绝只短暂经过该状态并返回
客服域。

**否证信号**：在词汇和长度匹配的 resisted/benign 对照中，route-only trajectory 与真实 execution 无法稳定
区分。若被否证，应明确记录 routing-only 的方法边界，而不是重定义负例。

**当前状态**：当前两种 independent persistence 也不支持。Staggered non-overlap min-2 的 drift-vs-resisted
AUROC 只提高 `0.006/0.026`，未达到双向 `+0.03`；token-block q25 相对 mean 反而降低 `0.020/0.029`。
这比 overlapping q25/4 提供更强负证据，不支持继续搜索 min/quantile/history width；但尚未一般性否定其他带
行为状态或监督方向的 trajectory model。

### H4：局部密度校正比 absolute age 校正更可迁移

一个 query 的异常程度可以相对其邻居所在正常区域的局部密度或可信度衡量，无需假定相同 token index 可比。

**否证信号**：去掉 age 后 score 完全由正常模式密度主导，双向 batch transfer 的 FAR/recall 更不稳定。

**当前状态**：混合。固定 local-density ratio 降低了 age/length correlation 和 normal bulk shift，却产生极重
normal peak tail，并更敏感于 resisted attacks；该具体公式不进入下一版 endpoint detector。

### H5：middle/late 分层的分布式表示优于固定专家名单

O5 支持保留 layer group 和完整分布结构，但具体表示、距离和层权重仍是算法选择，不能用 target drift 结果
反复搜索。

## 9. 报警评价必须回答什么

### 9.1 行为时间轴

未来数据最好区分两个边界：

```text
semantic commitment onset
        ↓
substantive cross-domain work onset
        ↓
candidate completion / publication / execution
```

现有 evidence onset 是第二个边界的近似；directional-relative 的13条 pre-boundary alarm 审计显示，其中10条
已经出现明确域外承诺或内容。因此单一硬边界不足以同时评价 early warning 和行为违规。

### 9.2 正常风险不能只报一个 trace FAR

每个方法至少报告：

- `P(alarm by t)` 的 normal cumulative-incidence curve；
- 每个 decision episode 和完整 trace 的 FAR；
- 每 1,000 个 eligible decode positions 的 alarm onset；
- clean、benign、resisted 和其他 hard negatives 分层；
- 按生成长度、model turn、stop reason 的诊断切片，但不得据此删除样本；
- scenario/group bootstrap 的不确定性。

`P(alarm by t)` 中的 `t` 是 time-at-risk，不是 normality feature。

### 9.3 检测收益必须与覆盖和误报联合报告

对 drift 至少报告：

- semantic/substantive onset 前报警率；
- onset `+4/+8/+16` clean detection；
- full-decode detection 与发布前 detection；
- 理论可达率和未检出数；
- latency 分布，同时给出其对应 recall，禁止只报告命中样本的漂亮中位数；
- 按 target domain、attack channel 与 workflow 分层，但小格子只用于失效诊断。

### 9.4 B1/B2/B3 的证据角色

B1 和 B2 已被多轮观察，今后的新算法在两者上的结果都是 adaptive development evidence。它们适合双向 transfer、
grouped resampling、失败审计和假设筛选；不能再叫独立 confirmation。只有算法和评价完全冻结后，新的 B3 才能
承担确认性泛化检验。

## 10. 从逻辑基础到下一轮实验

下一步不应直接命名一个“最终算法”，而应先做能够区分 H1--H4 的最小实验矩阵：

1. **已完成：移除 absolute-age normalization**。Raw/global score 保留强双向 representation signal；
2. **已完成：检验无 age 的局部支持**。Local density 稳定 bulk、破坏 peak tail，当前公式停止；
3. **已完成当前版本：分离 state 与 transition**。Conditional successor 与 state 高度共线，尚未隔离 routing
   innovation；
4. **已完成当前版本：execution-vs-mention/refusal**。Overlapping q25/4 只有很小 ranking 增益，没有 stopping
   增益；
5. **已完成：隔离 independent innovation**。Non-overlap state 与 single-token novelty 保留强 representation
   signal，token innovation 通过预注册 H2 gate；
6. **已完成：独立 persistence**。Staggered min-2 和 token q25 均未达到行为特异性增益门槛，全部 stopping
   candidates 未通过；
7. **已完成：单独研究 path-level 风险校准**。新增160个独立 normal groups；token static/risk-clock 达到总体
   normal-risk 研究门，但 block 失败，且及时 drift recall 没有解决；
8. **下一步：解释 evidence timing 与行为特异性**。停止调整同一 novelty 的 bins/block/quantile，直接检验
   route-only signal 为何在 substantive onset 后仍需16--25 token，以及通用 state/event 信号能否区分合法语义
   处理与域外执行；
9. **已完成：early-evidence 与 generic-state audit**。Matched +4/+8 ranking 有信号，但 B2 counterfactual
   coverage 不足，C1 absolute timing 仍失败；当前 controller structure 在三臂间完全相同；
10. **下一步：同预算 comparator study**。用同一 group-held-out split 和 +0/+4/+8 预算比较低容量 routing
   direction、文本与可获得 hidden-state feature；先回答增量信息，再设计 detector；
11. **B1/B2/C1 的证据角色继续分开**：B1/B2 positive 只作 adaptive development；C1 只确认 normal risk，
   没有 positive effectiveness evidence；B3 继续不使用。

下一轮必须把表示与风险估计分开：token innovation 回答“新的 routing observation 是否离开 normal support”，
time-at-risk boundary 回答“在一条不断变长的 path 上何时足以报警”。C1 表明更多独立 normal paths 能改善 tail
calibration，但不能使 routing evidence 更早出现；下一轮必须把“风险控制”和“行为 onset 前后的信息可用性”继续
分开。

## 11. 每个未来 proposal 的必填项

每个算法实验计划必须回答以下问题，否则不应运行：

1. 它检验哪个 `H`，依据哪些 `R/M/O`？
2. 它把什么定义为正常，`N_fit/N_cal/N_eval` 如何分离？
3. 它是否偷偷依赖 workflow、absolute age、未来长度、真实 boundary 或攻击标签？
4. 单 token state、局部 transition 和长期 history 各自承担什么作用？
5. 为什么其 stopping rule 与“开始执行域外工作”相符？
6. 如何控制不同生成长度和多轮 episode 带来的 time-at-risk？
7. 对 mention、refusal、格式退化和正常强转折预期如何？
8. 最小成功条件与能够否证该假设的结果分别是什么？
9. 哪些选择在运行前冻结，哪些输出只允许作为 post-hoc 诊断？
10. 若 B1/B2 成功，B3 要检验的真正泛化轴是什么？

## 12. 尚需明确的研究决策

- `Q1`：确认 `N_fit/N_cal/N_eval` 的具体组成，特别是 resisted attacks 是否只进 calibration/evaluation；
- `Q2`：多轮 Agent 的报警预算按 decision episode 复位，还是在完整 trace 上持续；
- `Q3`：未来 ground truth 是否正式采用 semantic commitment 与 substantive execution 双边界；
- `Q4`：route-only 无法区分 execution 与 refusal 时，是判定该路线 no-go，还是允许加入通用 event/state 元数据；
- `Q5`：研究阶段 primary false-alarm estimand 是 per-episode risk、per-trace risk，还是固定 token budget 下的
  anytime risk；其他指标仍可全部报告。

在这些问题确定前，可以运行第10节的描述性/消融实验，但不应冻结下一版主 detector。
