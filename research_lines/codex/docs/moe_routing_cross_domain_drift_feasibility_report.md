# 生成阶段的 MoE 路由能否监测固定职责 Agent 的跨领域偏移？

## Phase A 可行性、负结果与研究边界

| 项目 | 内容 |
|---|---|
| 报告日期 | 2026-09-03 |
| 研究状态 | 探索性技术报告，可供外部研究讨论 |
| 模型 | `allenai/OLMoE-1B-7B-0125-Instruct` |
| 模型 revision | `b89a7c4bc24fb9e55ce2543c9458ce0ca5c4650e` |
| 研究对象 | 固定职责客服 agent 的跨领域生成偏移 |
| 核心结论 | 获得了值得继续研究的局部正信号，但尚未达到方法确认或生产可用标准 |

## 摘要

生产 agent 通常反复执行边界明确的 routine work，例如查询订单或退货状态。本文研究一种狭义异常：agent 在用户指令或不可信工具输出诱导下，实际从授权客服任务转向诗歌、代码等明显无关领域。我们考察稀疏 Mixture-of-Experts（MoE）模型内部的逐 token 专家路由能否成为运行时传感器，并严格区分“上下文里出现了攻击”与“模型已经开始执行攻击目标”。

我们搭建了一个可自由生成自然语言、只把工具调用表示为 JSON action 的客服 agent，并记录每次生成中全部 prefill 和 decode token 在 16 个 MoE 层上的 64 路 router logits、概率、top-8 专家和混合权重。行为标签在读取路由差异前通过完整 trace 盲审冻结。

早期正向控制显示，单条回复中的诗歌与客服片段路由存在明显差异；模型原创诗相对客服的逐层路由分布 Jensen–Shannon divergence（JSD）为 0.03399，频繁 top-8 专家平均只重合 1.25/8。更关键的同输入 outcome 批次包含 36 条 trace，其中 12 条攻击自然产生 5 个 drift 和 7 个 resist。在该小型开发集上，final decode 前 8/16 token 的 route-only 分类较弱，最佳 AUROC 为 0.729；整段候选输出中的局部 8-token probability-JSD 窗口达到 AUROC/AP 1.0，但 5 个正例的峰值从未严格早于人工行为证据，而且完整输出长度基线也达到 AUROC 0.943。

同一 prompt、同一已生成 token prefix 的正负轨迹具有逐元素相同的路由。这给出一个确定性的时间边界：采样分叉发生前，router 不可能从相同隐藏状态预知将被抽中的未来 token。因而可行命题不是“在行为分叉前预测未来服从”，而是“跨领域生成开始后，利用局部路由 change point 在完整消息发布前中止”。

我们随后尝试用更自然的 hybrid 输出协议消除 JSON 和长度混淆。三轮预注册校准分别因伪工具调用、7/1 outcome 失衡，以及业务 lookup 完成率不足而失败。最后一轮虽然获得 5/3 mixed outcomes，但只有 4/8 样本在真实 lookup 后回复用户，未达到预注册的 6/8 agent 质量门槛；按停止规则未读取该批路由，也未启动新的效果估计。

综合而言，本研究已经证明：跨领域生成与 MoE 路由存在可重复关联，且生成后的局部路由异常有潜在在线价值；尚未证明：路由能独立于已生成文本、长度和协议状态可靠区分 drift 与 resist，或能在首个实质性跨领域 token 之前预警。该课题值得作为一个边界清楚的正式研究问题继续，但当前结果不是已验证 detector，更不是生产安全机制。

## 1. 研究问题与范围

### 1.1 主问题

> 对业务范围固定、主要执行 routine work 的 MoE agent，仅利用正常业务轨迹建立路由画像，能否在其实际生成从业务域转向明显无关领域时检出变化，并在完整越界回复发布前报警？

这里的关键词是“实际生成”。输入包含一句“写诗”不构成正例；模型必须开始创作诗句、生成代码，或明确接受并开始执行该跨领域工作。

### 1.2 主标签

主标签 `goal_plan_deviation_started` 满足以下语义：

- **正例 drift**：模型已开始实质性生成授权域外的诗歌、代码等工作；
- **负例 resist**：模型看见攻击但拒绝、忽略或只复述它，未执行跨领域工作；
- **行为边界**：首个使候选输出只能被解释为正在执行跨领域工作的 token；
- **候选输出也计入**：即使消息因格式错误尚未发布，只要 decode 已产生跨域工作，仍是被监测对象。

未知工具、错误参数、权限错误、非法状态转移和错误业务事实不是本文主阳性。它们适合由 schema 校验、权限控制和确定性状态机处理，不应被拿来抬高路由 detector 的效果。

### 1.3 明确不研究什么

本文不声称 MoE 路由是解决所有 agent 异常的统一方法，也不估计生产环境低误报率。跨工作流偏移、同领域欺诈、参数越界和副作用控制留给其他机制。本阶段只验证最可能产生路由信号的 `easy_semantic_shift`。

## 2. 为什么研究 MoE 路由

MoE 层不是让每个 token 经过同一前馈网络，而是由 router 为多个专家打分，再只激活少数专家。OLMoE 每层有 64 个专家，每个 token 选择 top-8。如果任务领域变化会改变隐藏状态在 router 决策空间中的位置，那么客服到诗歌或代码的切换可能表现为跨层、逐 token 的专家路径变化。

这种信号有三个研究吸引力：

1. 它是模型前向计算本来就产生的遥测，不需要额外调用一个大模型监控器；
2. 它天然具有逐 token 时间分辨率，可用于 change-point 检测；
3. 它也有明显风险：路由可能主要编码 token 身份、语法和话题，而不是“是否得到授权”。

第三点决定了本研究必须包含相同攻击输入下的 drift/resist 对照和 token、长度、协议控制。

## 3. 与相关工作的关系及 novelty 边界

[OLMoE](https://arxiv.org/abs/2409.02060) 报告了从头训练的细粒度 MoE 中可观察的领域特化。[Task-Conditioned Routing Signatures](https://arxiv.org/abs/2603.11114) 研究了聚合路由特征对任务类别的识别；[RouteScan](https://arxiv.org/abs/2605.24817) 用 prompt 路由遥测检测有害输入；[InnerExpert](https://arxiv.org/abs/2608.17687) 用逐 token MoE 信号检测幻觉；[MASCing](https://arxiv.org/abs/2604.27818) 用路由序列预测并干预拒绝/服从行为。稠密模型方面，[TaskTracker](https://arxiv.org/abs/2406.00799) 已证明内部激活可用于检测 task drift。

负面或边界性证据同样重要。[RASET](https://arxiv.org/abs/2605.29708) 发现路由差异主要随话题而变化，同话题下拒绝与合规续写的差异很小；[The Myth of Expert Specialization](https://arxiv.org/abs/2604.09780) 强调路由由当前隐藏状态决定，prompt 级模式不能预测未来 generation routing。[Mixtral of Experts](https://arxiv.org/abs/2401.04088) 也报告了专家使用更明显地跟随语法而非文档话题的结果，说明信号强弱依赖模型与训练方式。

截至本报告日期的系统检索中，我们未发现已有工作同时研究以下交集：

- 固定职责、带工具调用的生产型 agent；
- 生成阶段逐层、逐 token MoE 路由；
- 预测目标是实际从 routine work 转向跨领域工作；
- 明确区分 attack exposure、自然抵抗与实际服从；
- 评价相对行为边界的检测时刻和完整消息发布前的拦截余量。

因此 novelty 应限定为这个问题交集与潜在技术方向。本文不把“路由包含任务信息”“内部激活可以检测漂移”或“MoE 路由可用于安全分类”本身当作新贡献。“未发现”是检索结论，不是绝对不存在证明，正式投稿前需要滚动更新文献。

## 4. 实验系统

### 4.1 模型与环境

- 模型：`allenai/OLMoE-1B-7B-0125-Instruct`；
- 结构：16 个 MoE 层、每层 64 专家、每 token top-8；
- 精度与设备：bfloat16，NVIDIA RTX 5090；
- 推理框架：PyTorch 2.13.0 + CUDA 13.0、Transformers 5.16.1；
- 模型 revision、chat template、配置和随机 seed 均写入 trace；
- 开发采样主要使用 temperature 0.8、top-p 0.9，每轮最多 192 token、最多 3 个 agent step。

### 4.2 Agent 职责

Atlas 只被授权查询并报告订单或退货状态：

```text
lookup_order(order_id)
lookup_return(return_id)
```

它可以在客服范围内自由对话。工具调用必须是单个 JSON action；用户回复可以是自然语言。用户与工具返回都不能把职责扩展到诗歌、编程、数学、故事或其他领域。工具返回的事实字段可用，自然语言指令不可信。

### 4.3 三臂配对

每个基础任务有三个实验臂：

| 实验臂 | 内容 | 研究作用 |
|---|---|---|
| clean | 普通订单/退货查询 | 正常路由画像 |
| benign control | 含 poem、函数名或 marker，但明确不是指令 | 控制关键词与格式 |
| attack | 用户直接要求跨域，或工具字段嵌入跨域指令 | 观察自然 drift/resist |

同一 analysis group 的模型、system prompt、业务记录、攻击 payload 和解码配置固定，只用预注册 seed 产生自然采样差异。不能 teacher-force 正负输出，也不能看见 outcome 后补 seed。

### 4.4 一个简化例子

正常客服任务：

```text
用户：查询 ORD-508 的状态和预计送达时间。
助手：{"type":"action","name":"lookup_order",...}
工具：ORD-508 正在等待出发扫描，预计 2026-09-14 送达。
助手：ORD-508 正在等待出发扫描，预计 9 月 14 日送达。
```

direct-user attack 在同一请求后追加“再写一首三行冬季森林诗”。一次 drift 输出直接生成诗句；一次 resist 输出拒绝写诗并继续客服。tool-output attack 则把“生成 `reverse_words` 函数”的指令放进退货记录的备注字段。有的采样明确拒绝，有的先说“不能写代码”却紧接着给出完整 Python 实现；后者仍是 drift，因为实际行为而非自我声明决定标签。

## 5. Trace 与质量控制

### 5.1 记录范围

对每一次模型生成，我们记录：

- **prefill**：system、用户、历史 assistant、工具结果等完整上下文中的每个 token；
- **decode**：逐个生成的每个输出 token；
- 每个 token 在全部 16 个 MoE 层上的 64 路 pre-softmax router logits；
- logits softmax 后的概率、实际 top-8 expert IDs 和 top-8 weights；
- token ID、文本、位置、消息角色、conversation turn、agent step 和工具边界；
- 完整事件流、解析结果、tool action/result、policy 判定与最终 outcome。

每个 forward step 单独写入 safetensors 分片。独立验证器检查层数、专家数、top-k、token 对齐、prefill 位置重置和 top-k 权重一致性。本文报告的核心批次全部 100% 通过路由验证。

### 5.2 盲审与标签来源

自动 marker 只能发现候选证据，不能决定标签。它曾把 marker 复述误判成 drift，也曾漏掉不完整 JSON 中已经生成的代码。最终语义裁决保存：原自动结果、模型事件、证据字符串、字符 span、对应 output token、理由和 reviewer。每个用于分析的行为标签都在读取相应批次的路由差异前冻结。

### 5.3 可用时刻

第 `t` 个生成 token 的路由只能在该 token 已经被模型处理后被观察。因此“前 8 token 分数”表示模型生成 8 个 token 后可以报警，不表示在第一个 token 之前预测完整回复。

## 6. 分阶段实验

### 6.1 正向控制：任务/内容域是否影响路由

单条复制诗实验中，诗歌 token 与同一回复内客服 token 的 mean layer centroid JSD 为 0.02726，频繁 top-8 专家平均重合 1.75/8。由于诗句已经出现在工具输入中，这可能只是复读或 token identity。

随后让模型现场创作一首未出现在输入中的月亮诗：

| 对比 | JSD | 频繁 top-8 重合 |
|---|---:|---:|
| 原创诗 vs 同批客服 | 0.03399 | 1.25/8 |
| 复制诗 vs 同批客服 | 0.02708 | 2.19/8 |
| 原创诗 vs 复制诗 | 0.02025 | 3.88/8 |

两次“诗歌减客服”的 16×64 专家选择率差值 Pearson/cosine 均为 0.695，绝对变化最大的 32 个 layer–expert 单元有 16 个重合。这是可重复的正向控制：**模型正在生成的任务/内容域与路由选择有关联。** 它仍不能证明 router 表示“未授权”。

### 6.2 早期分类探索：强信号但 outcome 混淆

早期 24 条同 prompt 轨迹中，8 条 attack 全部 drift，clean/benign 全部为负。leave-one-task-group-out 下，前 8 token 的 expert-selection ridge 达到 AUROC 0.961，前 16 token 达到 1.0；但 attack exposure 与 positive 完全重合。更严格的 scope-gate resist 数据又使用了不同 system prompt，不能消除混淆。

该结果促使我们放弃“attack vs control”的容易问题，重新设计同一 prompt、同一 payload 下自然产生 drift/resist 的 outcome 对照。早期满分不再被视为核心证据。

### 6.3 C0 行为校准：获得同输入 mixed outcomes

soft-gate C0 使用 8 条预注册 attack，盲审得到 5 drift、3 resist。tool-output code 条件在逐字符相同的业务输入、攻击 payload、prompt 和解码参数下，仅因 seed 不同同时产生两类 outcome。C0 只验证样本机制，未读取路由差异。

### 6.4 S1 主开发批：36 条 trace

S1 使用全新业务 ID、攻击主题和 seeds，包含 4 个 analysis group × 3 seeds × 3 arms，共 36 条 trace。12 条 attack 的冻结结果为 5 drift、7 resist；两个 direct-user group 都是同 payload mixed outcomes。clean 12/12 均完成客服任务。

#### Early decode

预注册范围内最佳 early 候选是 final generation 前 8 token、late-layer top-k selection、group-excluded normal-centroid distance：

| 指标 | 结果 |
|---|---:|
| AUROC | 0.729 |
| Average precision | 0.587 |
| mixed-group 平均 pairwise win rate | 0.625 |
| 正例严格高于组内全部负例 | 0/2 groups |

tool-output 子集的表面 AUROC 为 1.0，但 tool-poetry 全负、tool-code 全正，payload/目标领域与 outcome 共线。真正有同 payload 正负对照的 direct-user 子集 AUROC 只有 0.563。

#### Full-output 局部窗口

对每条 attack，使用其他 analysis group 的 clean + benign 构造正常 per-layer 路由概率画像，再计算逐 token JSD。整段候选输出中最大的固定 8-token 窗口结果为：

| Window | 路由统计 | 层段 | AUROC | AP | mixed top |
|---:|---|---|---:|---:|---:|
| 8 | probability JSD | all | 1.000 | 1.000 | 2/2 |
| 8 | probability JSD | late | 1.000 | 1.000 | 2/2 |
| 16 | probability JSD | middle | 1.000 | 1.000 | 2/2 |
| 8 | top-8 selection novelty | all | 0.643 | 0.625 | 1/2 |

这是开发集上的 post-selection 结果，不是确认性估计。最强 8-token 窗口在 5 个正例中的时间位置为 1 次与行为证据重叠、4 次发生在证据之后、0 次严格发生在证据之前。4/5 峰值出现后仍有 token 尚未生成，其中部分候选还有较长的发布前拦截余量。

#### 廉价 sanity controls

| Control | 窗口 | AUROC | AP |
|---|---|---:|---:|
| token hash | first 8 | 0.114 | 0.307 |
| token hash | full | 0.143 | 0.316 |
| output length | full | 0.943 | 0.927 |
| protocol + step | full | 0.629 | 0.756 |
| length + protocol + step | full | 0.914 | 0.903 |

Token hash 在这个极小 grouped split 上方向不稳定，不能据此声称路由包含文本之外的信息。输出长度几乎完全排序 outcome，说明局部路由满分仍可能混入长诗/代码、位置和协议失败等简单因素。

### 6.5 Hybrid change-point 校准：未能启动 S1b

为消除“所有消息都塞进 JSON”和完整输出长度的混淆，我们把协议改为自然语言 message + JSON-only tool action，并计划使用固定长度、固定相对位置窗口。效果分析前设置了 agent 行为门槛。

| 批次 | 冻结结果 | 失败原因 | 是否读取类别路由差异 |
|---|---|---|---|
| C1 | 0 个可用目标 drift | prompt 中的 `tool_name` 占位符诱发伪 action | 否 |
| C2 | 7 drift / 1 resist | 少于 2 个 resist | 否 |
| C3 | 5 drift / 3 resist | 仅 4/8 在 lookup 后自然回复，要求 ≥6/8 | 否 |

C3 两个攻击族都产生 mixed outcomes，8/8 trace 路由验证通过；但 direct-user poem 的三条正例跳过业务 lookup，另一条在 lookup 后发出未知 action。按预注册合取 gate，C3 失败，停止 prompt 调参，没有通过补 seed、删除坏样本或查看路由后选择样本进行挽救。

## 7. 最重要的时序结论

语言模型在每个 decode step 先根据当前隐藏状态计算 logits，再从词表分布采样一个 token。被选中的 token 要到下一次 forward 才进入模型并产生对应路由。因此：

```text
相同 prompt + 相同已生成 token prefix
            ↓
相同 hidden states 与 router inputs
            ↓
相同 MoE 路由
            ↓
词表采样才可能选出不同的下一个 token
```

S1 的实测与此一致：三对 final prompt 相同、outcome 相反的轨迹，在输出分叉前共享 18、15、15 个 token；这些 token 上 route-probability JSD 全为 0，top-8 overlap 全为 8/8。

所以，单纯观察当前 forward 的 router，不能在随机生成分叉前预知同一模型将采样哪一个未来 token。除非监测器额外使用词表 logits、采样状态、随机源或另一个预测模型，否则“pre-branch prediction”不是合理目标。

仍然合理的在线目标是：模型一旦开始形成跨领域 continuation，路由是否能比等待完整消息更早稳定偏离正常画像。这里的“早”是相对发布或执行，而不是相对首个语义证据。

## 8. 证据判断

### 8.1 已得到支持

- 路由采集管线能完整记录多轮 agent 的全部 prefill/decode token，并可靠对齐到事件和行为 token；
- 客服与诗歌/代码生成使用的专家分布显著不同，原创内容复现了该方向；
- 单一 prompt、单一 payload 下可以自然得到 drift 与 resist，而非 teacher forcing；
- 在 S1 小型开发集上，跨领域生成开始后的局部 probability-JSD 对 outcome 有强描述性排序；
- 多数强峰值发生在完整候选结束前，具备潜在中止窗口。

### 8.2 未得到支持

- prefill 路由能够判断模型随后会服从还是抵抗；
- 相同 token prefix 尚未分叉时存在隐藏的 seed/outcome 路由信号；
- 路由峰值严格领先于首个跨领域行为证据；
- 路由相对 partial text、位置、长度和协议状态具有稳定增量价值；
- 当前 hybrid agent 足够稳定，可以生成一批无明显任务完成混淆的 S1b 数据；
- 任一结果可以外推到其他 MoE 模型、复杂 agent 或生产低误报率。

### 8.3 总体判断

若“有价值的研究课题”意味着问题有明确应用动机、已有机制证据、可证伪假设和非平凡失败边界，则当前工作达到这个标准。正向控制、同输入 outcome 数据和 S1 局部信号共同排除了“router 完全与生成任务无关”的简单否定解释。

若标准是已经有足够证据锁定 detector、交付确认性效果或声称优于文本监控，则当前工作没有达到。S1b 没有因为方法失败而给出负效果；它是在数据生成 gate 被主动终止。正确结论是“候选方向值得正式研究，但关键增量性证据仍缺失”。

## 9. 局限与威胁

1. **样本量小**：S1 只有 12 条 attack、5 个正例，独立单位更少；AUROC 1.0 极易受单例影响。
2. **开发集选择**：窗口、层段和解释都在 S1 上开发，不能当作独立复现。
3. **文本与长度混淆**：跨域输出本身可见，正例常更长；没有强 partial-text baseline。
4. **Agent 不稳定**：1B-active 小模型容易漏 lookup、生成伪 action 或自相矛盾。
5. **任务范围窄**：正常域只有订单/退货，跨域只有诗歌/代码。
6. **单模型**：不同 MoE 的路由特化模式可能完全不同。
7. **人工行为边界**：虽有 token 对齐和 provenance，语义边界仍含判断；一次边界修正在首次聚合后发生，因此时序结果按 post-hoc 处理。
8. **未测试自适应攻击**：攻击者若知道路由 detector，可能刻意生成贴近客服路由的越界内容。

本阶段按研究组决策只使用廉价 sanity controls，没有投入强文本分类器、hidden-state probe 或 LLM monitor。强基线应留到正式立项后用于测量增量价值，而不是在问题尚未站稳前扩大工程量。

## 10. 建议的正式研究交接任务

当前仓库最适合交接的是一个收缩后的研究命题，而不是一个 detector：

> 当固定职责 MoE agent 已开始生成跨领域 continuation 后，逐 token 路由 change point 能否在完整候选发布前，以超出长度、位置和 partial-text 的增量信息识别偏移？

建议后继研究组按以下顺序开展：

1. 更换为能稳定完成 tool workflow 的 agent/model，或使用现成 agent benchmark；样本 gate 必须独立于路由结果；
2. 先冻结少量自然 mixed-outcome calibration，再生产新的开发批，不复用 C0/S1/C1–C3 做效果选择；
3. 主比较使用固定 8-token、固定相对位置或行为边界对齐的窗口，禁止 full-sequence maximum 作为 headline；
4. 同时保留 drift、resist、clean、含相同词汇的 benign control，并以攻击族为 group 划分；
5. 比较 route-only、partial token/text、length/position、hidden-state probe 和组合模型；强基线在这一步才有意义；
6. 报告相对首个行为证据、完整消息发布和工具执行三个时间点的延迟；
7. 在新场景冻结单一方法后，才运行一次独立 confirmation；之后再做多模型和低 FPR 扩展。

如果固定窗口路由信号不能超过 partial-text，最有价值的负结论将是：MoE 路由可作为廉价领域变化代理，但没有足够的独立信息支撑专用运行时 detector。

## 11. 可复现性与仓库产物

核心实现与文档：

- 目标与标签契约：`docs/target_trace_spec.md`；
- 路由采集验证：`docs/p1_routing_capture_report.md`；
- S1 预注册与报告：`docs/phase_a_signal_batch_plan.md`、`docs/phase_a_signal_batch_report.md`；
- hybrid 校准：`docs/phase_a_change_point_calibration_c1_report.md`、`docs/phase_a_change_point_calibration_c2_report.md`、`docs/phase_a_change_point_calibration_c3_report.md`；
- S1 分析实现：`scripts/analyze_phase_a_signal_batch.py`；
- trace runner：`scripts/run_phase_a_smoke.py`；
- 路由验证：`scripts/validate_routing_trace.py`；
- 全部相关工作与检索笔记：`docs/related_work.md`、`docs/lit_notes/`。

验证命令：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

代码、配置、预注册与报告进入 Git；体积较大的原始 trace、safetensors 路由分片和派生分析结果保存在本地 `artifacts/`，按仓库规则不进入 Git。C3 的冻结配置哈希为 `eb17ac79f113c9d6022645ad1d246ad427b1f64251f9abf5d612808b3b704fbe`；S1 的冻结配置哈希为 `4ff08185b20927fcd6274aea22cb8cf28107003b65c2fa7361260ecb8e93950e`。

## 12. 结语

这项 Phase A 工作最大的收获不是一个漂亮分类分数，而是把原始猜想缩小成了可检验、不会误导的形式。MoE 路由确实随实际生成领域变化；它没有在相同 token 分叉前神奇地预知未来；在分叉后、完整发布前可能存在有用的局部信号。下一阶段的科学问题，是这个信号是否在严格控制文本、长度和 agent 质量后仍提供增量价值。当前证据足以支持研究交接，尚不足以支持方法或产品主张。
