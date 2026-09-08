# Step 0：相关工作调研

日期：2026-09-02；2026-09-03 按收缩后的研究问题复核
课题：能否用 MoE 专家路由轨迹作为运行时信号，检测并拦截 agent（参考场景：客服 agent）偏离预定任务的行为。

调研方式：按五个方向并行检索（A 路由编码了什么；B 路由作为信号与 MoE 安全；C 稠密模型的激活监测；D agent 运行时安全与任务偏离；E 可用模型与工具链）。每个方向只收录能打开原文页面并核对过标题、作者、年份的条目，共约 150 条。五份原始笔记在 [docs/lit_notes/](lit_notes/)。其中决定本课题新颖性的 8 篇核心论文我又单独打开摘要页复核过一遍（RouteScan、RASET、InnerExpert、MASCing、Chen 2026、TaskTracker、Myth of Expert Specialization、kNNGuard）。

---

## 0. 一句话结论

猜想成立的证据比我最初预期的更强，但文献把它的适用边界画得很清楚：**路由是由话题驱动的，不是由意图驱动的**。客服 agent 被拐去写代码属于话题切换，正是路由最敏感的情形；同一话题下从"合规"变成"违规"，路由几乎不动。截至 2026-09-03 的检索，没有发现把 **MoE 逐 token 路由轨迹**作为固定职责生产 agent 的在线行为传感器，检测其实际生成是否从 routine work 转向跨领域工作，并明确区分“接触攻击”与“服从攻击”的工作。这个研究问题和潜在技术方向的较窄空白目前仍成立；但“路由可识别任务类别”“内部状态可检测 task/conversation drift”和“MoE 信号可做逐 token 异常检测”都已有直接先行工作，因此不应再使用更宽泛的“没有任何工作做过”表述。

---

## 1. 路由到底编码了什么

### 1.1 反方证据：路由主要反映 token 身份、语法和几何

- **Mixtral of Experts**（Jiang et al., 2024）：在 Pile 各子集上按专家使用比例统计，"没有观察到基于话题的明显模式"，路由跟随语法（Python 的 self、英文的 Question 落到同一专家），且相邻 token 倾向共享专家。这是经典的负结果。
- **ST-MoE**（Zoph et al., 2022）：encoder 专家有浅层特化（标点、连词、专有名词），decoder 专家特化"明显更弱"，多语模型中"没有语言特化的证据"，负载均衡迫使所有专家处理所有语言。
- **Part-Of-Speech Sensitivity of Routers**（Antoine et al., COLING 2025）：六个 MoE 上，仅用逐层专家 ID 序列预测词性可达 0.75 到 0.89，但"每个词形取最常见词性"的基线是 0.91。也就是说路由不是纯 token 查表，但大部分信息与词形重合。
- **Layer-wise MoE Routing Locality**（Hayashi et al., 2026，Qwen3.5-35B-A3B）：相同 token 的路由 Jaccard 相似度 0.649，不同 token 0.175；输入层同 token 相似度高达 0.83，但在中间层（L14 到 L20）同 token 相似度下降、不同 token 相似度上升，即中间层由上下文主导。
- **The Myth of Expert Specialization**（Wang, Hayou & Nalisnick, 2026）：router 是线性映射，隐状态相似是专家使用相似的充要条件；负载均衡通过抑制共享方向来维持路由多样性。实证上，两个模型解同一道题的专家重合率约 60%，与一个模型解不同题相同；gpt-oss-20b 末层在 prefill 时语义无关的序列可以激活完全相同的专家；**prompt 级路由不能预测生成阶段的路由**。
- **Towards an empirical understanding of MoE design choices**（Fan et al., 2024）：token 级路由产生语法特化，序列级路由才产生弱话题特化。

### 1.2 正方证据：路由携带话题、任务和上下文信息

- **OLMoE**（Muennighoff et al., 2024）：从头训练的细粒度 MoE 有强领域特化，某第 0 层专家几乎接收全部 arXiv token；相比之下 upcycle 得到的 Mixtral "几乎没有领域特化"。路由在训练 1% 时已有约 60% 固定，40% 时约 80%，所以指令微调版继承预训练路由。
- **Probing Semantic Routing in Large MoE Models**（Olson et al., EMNLP 2025 Findings）：在 DeepSeek-R1 等百 B 级模型上，同一个词在不同词义下专家重合度显著更低（p < 0.001），语义路由随规模出现。
- **Your MoE LLM Is Secretly an Embedding Model For Free**（Li & Zhou, 2024）：把各层路由权重拼接起来直接当句子嵌入，在 MTEB 20 个数据集上与隐状态互补，"对提示措辞更鲁棒、聚焦高层语义"。这是"仅从路由能恢复多少语义"最直接的正面答案。
- **Expert Selections Reveal (Almost) As Much As Text**（Nuriyev & Kulp, 2026）：仅用专家选择序列重建输入 token，transformer 解码器 top-1 达到 91.2%。路由在信息量上足以支撑检测。
- **Does the Same Token Mean the Same State?**（Chen et al., 2026，gpt-oss 与 Qwen3-MoE）：同一 token id 的路由状态仍能区分任务上下文、轨迹历史和推理强度；在代码围栏等锚点 token 上用加权 Jaccard 比较路由状态做样本选择，效果与多数投票相当。这是消除 token 身份混淆的正确对照方法。
- **Polysemantic Experts, Monosemantic Paths**（Ye, Yuan & Sharkey, 2026）：隐状态可分解为驱动路由的"控制子空间"和 router 看不见的"内容通道"，语言、token 身份、位置等表层特征主要在内容通道里；单个专家多义，但跨层路由路径单义。这说明应监测的对象是跨层轨迹而不是单个专家。
- **Ban&Pick**（Chen et al., 2025，Qwen3-30B-A3B）：存在按任务分化的热点专家，L29E91 在代码上激活率 66.9%，L16E95 几乎只在数学上激活。
- **Multilingual Routing in MoE**（Bandarkar et al., ICLR 2026）与 **Understanding Multilingualism in MoE LLMs**（Chen et al., 2026）：早层和晚层做语言特定处理，中间层是语言无关的"能力枢纽"。

### 1.3 最关键的一篇：RASET

**RASET: Router-Agnostic Safety-Critical Expert Tuning**（Zhang et al., EMNLP 2026）在 OLMoE、DeepSeek-V2-Lite、Qwen3-30B-A3B-2507、Phi-3.5-MoE、gpt-oss-20b 五个模型上测量路由权重向量的 JS 散度：

| 对比条件 | JS 散度 |
|---|---|
| 话题改变 | 0.3379 |
| 话题固定，加拒绝前缀 | 0.0098 到 0.0346 |
| 拒绝 vs 合规的 teacher-forced 续写 | 0.0054 到 0.0434（top-8 重合 7.18 到 7.92 / 8） |
| 配对的有害 vs 良性 prompt | 0.1006 |
| 随机跨话题对 | 0.2282 到 0.2362 |

结论原文："aligned MoE LLMs 的路由模式主要由话题驱动，而安全行为可以在几乎不改变内在路由路径的情况下被改变。"作者据此认为路由不是可靠的安全信号。对本课题这是双刃剑：**任务偏离（客服到代码、注入了新指令）是话题级变化，是路由最敏感的情形；同话题下的意图变化路由几乎不响应。**

### 1.4 层级规律与模型依赖

综合上述工作，反复出现的层级图景是：早层 = 表层（token 形态、语言），中间层 = 任务和上下文，末层 = 在 prefill 阶段可能塌缩成与话题无关的路由。Mixtral 上安全相关路由在第 8 到 15 层最有选择性（Siddiky, 2026）。

话题信号强弱高度依赖模型：从头训练的细粒度模型（OLMoE、Qwen3-MoE）远强于 upcycle 的 Mixtral；**DBES**（Wang et al., 2026）指出 Qwen 系列呈模块化高隔离特化，DeepSeek 和 GLM 呈分布式协作；**MoEcho**（Ding et al., CCS 2025）用专家负载直方图分类 prompt 属性，在 60 到 64 专家模型上准确率 74% 到 100%，在 8 专家 Mixtral 上明显更差。负载均衡损失会模糊路由方向（Guo et al., NeurIPS 2025；Ahrac et al., 2026）。

---

## 2. 路由作为信号：最近的先行工作

没有找到把路由用于 agent 任务偏离在线检测的工作。最接近的有四篇，加上一批用对比激活频率定位"行为专家"的工作。

| 工作 | 目标标签 | 阶段 | 粒度 | 多轮 / agent | 与本课题的差别 |
|---|---|---|---|---|---|
| **RouteScan**（Lv et al., 2026） | 有害 prompt | 仅 prefill | 每个 prompt 一个向量 | 否 | 用 GPU 线程数做路由代理，逐层专家负载直方图 + 逻辑回归，四个开源 MoE 上未见有害领域 AUROC > 0.91，可迁移到未见 jailbreak 模板。证明聚合路由统计 + 轻量分类器可行。 |
| **InnerExpert**（Fonseca et al., 2026） | 幻觉 | 解码阶段 | 逐 token | 否 | router 熵、专家使用分布、Gini、有效专家数等六个路由信号 + XGBoost，OLMoE 与 Gemma-4-26B-A4B 上 answer 级 AUROC 0.88 / 0.91，token 级 0.76；Gemma 上仅 router 熵就有 0.884。最接近的方法论模板。 |
| **MASCing**（te Lintelo et al., 2026） | 拒绝 / 合规 | 序列 | LSTM 读全层路由 logits 序列 | 多轮 jailbreak | 用于生成 steering mask 而非独立检测器，七个 MoE 上多轮 jailbreak 防御 52.5% 到 83.9%。证明路由 logits 序列能预测下游行为。 |
| **Chen et al. 2026**（RAD） | 答案选择 | 解码阶段 | 锚点 token 路由状态 | 否 | 提供加权 Jaccard 轨迹距离和锚点 token 对照。 |
| **Task-Conditioned Routing Signatures**（Avinash, 2026） | 四类任务类别 | prompt + 每条 32 个 generation token | 每段序列一个跨层聚合向量 | 否 | OLMoE 上仅用 routing signature 的逻辑回归达到 92.5% ± 6.1% 四分类准确率；直接证明“任务可由路由识别”，但未研究生成中的在线变点、攻击、agent、服从/抵抗或拦截。 |

相关的对比频率方法：**SteerMoE**（Fayyaz et al., ICLR 2026）用配对数据的激活率差找到行为相关专家并在推理时开关，安全 +20%，去激活可使安全性下降 41%；**SAFEx**（NeurIPS 2025）在 Qwen3-30B-A3B 的 6144 个专家中找出稳定的安全关键专家，关掉 12 个拒绝率降 22%；**GateBreaker**（2025）用"gate 级画像"找有害专家做攻击；**RouteHijack**（Xu et al., 2026）用路由感知的对抗后缀在七个 MoE 上达到 69.3% ASR，并在讨论里**明确提出"对 pre-softmax router logits 做轻量异常检测"作为未实现的防御**。

这些工作共同说明三点：路由统计 + 轻量分类器对多种标签都有效；对比激活频率是构造"任务画像"的现成方法；路由既是信号也是攻击面，检测器必须面对路由感知的自适应攻击。

---

## 3. 稠密模型的激活监测：要借鉴的方法论与基线

- **TaskTracker**（Abdelnabi et al., SaTML 2025）：定义"任务漂移"（外部数据导致 LLM 偏离用户指令），用处理外部数据前后同一探针位置的残差流激活差做线性分类，OOD 测试集上 ROC AUC 接近完美，不需要在攻击上训练即可泛化到注入、jailbreak；开源 50 万实例、六个模型。**这是与本课题问题定义最接近的工作**，路由方法必须与它比较，并可复用其数据。
- **SecMCP: Quantifying Conversation Drift in MCP via Latent Polytope**（Shi et al., 2025）：把 MCP 工具污染和间接注入造成的 hijacking 定义为 latent trajectory 的 conversation drift，用激活向量的 latent polytope 做跨轮异常检测，在 Llama3、Vicuna、Mistral 上报告 AUROC > 0.915。它已经覆盖“agent/MCP + 跨轮漂移 + 内部状态检测”的问题组合，但不用 MoE 路由，也不研究逐 token 提前量或路由相对残差流的增量价值。
- **PIShield**（Zou et al., 2025）与 **InstructDetector**（Wen et al., EMNLP 2025 Findings）：中间层隐状态线性探针检测注入指令，后者域内 99.6%、泛化 96.9%，BIPIA 上 ASR 降到 0.03%。
- **Attention Tracker**（Hung et al., NAACL 2025 Findings）：利用前向传播已有的注意力分数（"分心效应"）零额外推理检测注入。与"复用推理副产物"的论证同构，其"重要头"选择方法可类比为选择敏感的路由层。
- **kNNGuard**（Abdelfattah et al., 2026）：50 条标注 prompt，多层隐状态 kNN，做跨领域的不安全和**离题**检测，F1 与微调 guardrail 相当且快 2.7 倍。这是离题检测上最接近的内部信号基线。
- **Simple probes can catch sleeper agents**（Anthropic, 2024）、**Detecting Strategic Deception Using Linear Probes**（ICML 2025）：差值均值探针、中间层信号最强、prompt 阶段即可检测；后者确立"良性流量 1% FPR 下报召回率"的操作点。
- **Detecting High-Stakes Interactions with Activation Probes**（NeurIPS 2025）：探针以低约六个数量级的算力达到中等 LLM 监测器的效果，探针 + LLM 级联在同算力下最优。**Building Production-Ready Probes for Gemini**（2026）：短输入上训练的探针在长上下文、多轮的生产环境下失效，需要多样化训练分布。
- **Latent Adversarial Detection**（Kulkarni, 2026）：把多轮攻击视为激活轨迹，路径长度特征使检测从 76.2% 升到 93.8%（合成数据），真实数据只有 47% 到 71%。唯一把内部状态当作跨轮轨迹处理的工作。

必须纳入的失败模式：

- **Obfuscated Activations Bypass Latent-Space Defenses**（Bailey et al., ICLR 2026）：联合优化可让探针召回从 100% 降到 0% 同时保留约 90% 的 jailbreak 率。离散 top-k 路由是否更难混淆是未解问题。
- **RL-Obfuscation**（Gupta & Jenner, 2025）：均值池化的 token 级监测器容易被绕过，max 池化和注意力池化更鲁棒。序列级路由特征应避免单纯均值。
- **Linear probes rely on textual evidence**（Boxo et al., 2025）：去掉文本证据后探针 AUROC 掉 10 到 30 点。"写代码"引起的路由变化可能只是词汇变化，需要"合规讨论代码 vs 离题写代码"这类泄漏对照。
- **When AUC 0.998 Is Not Enough**（Li et al., 2026）：探针的高 AUC 大部分可由文本标量基线解释，标签反映的是"注入是否存在"而非"攻击是否成功"。必须区分这两者并加入长度、指令类 token 比例等平凡基线。
- **Impact of Off-Policy Training Data on Probe Generalisation**（Kirch et al., ACL 2026）：合成正样本训练的探针在意图类行为上泛化最差。

---

## 4. Agent 场景、攻击分类与评测协议

### 4.1 场景底座

- **tau2-bench**（Barres et al., 2025，MIT）：retail 115、airline 50、telecom 114 个任务，含政策文档、工具 schema、用户模拟器和五种判定（数据库比对、状态断言、自然语言断言、沟通信息检查、动作匹配）。通过 LiteLLM 接任意模型，PyPI 包 tau2 2.4.0。用户模拟器默认用 API 模型，这是一个需要决定的成本点（可换成本地第二个模型）。
- **Language Model Agents Under Attack: Profit-Seeking Behaviors in Customer Service**（Zhang, 2025）：10 个客服领域、100 个攻击脚本、五类直接注入（角色扮演/权威、混淆、载荷拆分、对抗后缀/施压、指令操纵），双 LLM 评分。航空领域约 56% 成功率，是最脆弱的领域。
- **FraudBench**（Pai & Xian, 2026）：政策约束的银行客服 agent 面对多轮自适应欺诈，150 个场景，每个场景标注可观察证据、禁止动作和干预点。
- **AgentDojo**（NeurIPS 2024 D&B）：629 个通过工具输出注入的测试用例，环境状态确定性判定；**InjecAgent**、**LLMail-Inject**（20 万条自适应攻击提交，含 TaskTracker 作为防御之一）可提供间接注入载荷。
- 动机事件：Chevrolet of Watsonville 车行聊天机器人被诱导写 Python、以 1 美元"有法律效力"卖车；Moffatt v. Air Canada 判航空公司对客服机器人的错误陈述负责。

### 4.2 偏离分类（对应 OWASP 2026 Agentic Top 10 的 ASI01 目标劫持，按信任边界划分）

1. 直接劫持：用户要求写代码、做作业等。来源：Zhang 2025 五类脚本、RealGuardrails 覆盖尝试、GovTech 离题 prompt 集。
2. 多轮社工：逐步施压或伪装身份。来源：FraudBench 链式攻击、TRAP 的说服原则。
3. 间接注入：工具返回值或检索内容中的指令。来源：AgentDojo、InjecAgent、LLMail-Inject。
4. 同话题意图偏离：在客服话题内做出违规让步（如违反退款政策）。这是 RASET 预言路由会失效的对照组，必须单独设置。

### 4.3 标签与评测协议

- 优先确定性标签（tau2 数据库比对、动作匹配、攻击者工具是否被调用），LLM 评审只做补充且用两个独立评审。
- 三层标签分离：注入是否存在、agent 是否开始做偏离任务、攻击者目标是否达成（WASP 的区分）。攻击失败的样本是关键对照。
- 指标：AUROC、良性流量 1% FPR 下的召回、按攻击族留出的泛化、检测延迟（偏离开始到报警的 token 数或步数，PrefixGuard 和 ESN+CUSUM 的做法）、拦截前泄漏的偏离内容量、额外开销。
- 自适应攻击必须做：静态基准已饱和（Bhagwatkar et al., NeurIPS 2025 指出 AgentDojo 等基准的度量缺陷），Gemini 的防御在自适应攻击下 24 项中 16 项失守。

### 4.4 需要对比的基线（按成本分层）

1. 文本分类器：PromptGuard 2、GovTech 离题分类器、同窗口 bag-of-tokens。
2. 激活探针：TaskTracker 风格残差流线性探针（上界）、kNNGuard。
3. LLM 评审监测器：LlamaFirewall 的 AlignmentCheck、AI Control 式可信监测。
4. 重执行 / 轨迹分析：MELON、AgentArmor、PrefixGuard（tau2 上 AUPRC 0.710）。

---

## 5. 模型与工具链（可行性结论）

| 模型 | 参数 | 路由网格 | 显存 | 路由获取 | 工具调用 |
|---|---|---|---|---|---|
| OLMoE-1B-7B-0125-Instruct | 6.9B / 1B | 16 层 × 64 专家，top-8 | bf16 13.8 GB | 原生 output_router_logits；hook `.mlp.gate` 返回 (logits, scores, indices) | 未说明 |
| gpt-oss-20b | 20.9B / 3.6B | 24 层 × 32 专家，top-4 | MXFP4 约 16 GB（bf16 约 48 GB 放不下） | MXFP4 路径不经过 router 模块，需在 `.mlp` 上加 pre-hook 用 router 权重重算 logits；不要开 use_kernels（issue #40104） | 原生 function calling（harmony 格式） |
| Qwen3-30B-A3B-Instruct-2507 | 30.5B / 3.3B | 48 层 × 128 专家，top-8 | bf16 61 GB，必须 4-bit | transformers v5 的融合专家参数无法被 bitsandbytes 量化（issue #1849，修复 PR #1965 仍未合并）；vLLM `--enable-return-routed-experts` 只返回专家 id 不返回概率 | 是 |

其他要点：transformers 的 `generate()` 不返回 router logits（issue #31722），解码阶段必须用 hook 或自写解码循环；文档里的"post-softmax"描述有误，实际记录的是 pre-softmax logits；gpt-oss 不能用 SDPA，需 eager 或 flash attention；SGLang 有 `--expert-distribution-recorder-mode per_token` 可记录逐 token 专家 id。环境：RTX 5090 需 cu129 或 cu130 的 torch 2.13；bitsandbytes 0.50 已支持 sm_120；WSL2 需在 .wslconfig 调大 memory 与 swap，HF 缓存放在 ext4 上并 `device_map="cuda"` 直接加载到显卡。

推荐顺序：先用 OLMoE 搭通并验证记录管线（有作者自己的路由分析可对照），再用 gpt-oss-20b 作为真实 agent；Qwen3-30B-A3B 作为备选，通过 vLLM + 社区 AWQ/GPTQ 量化运行。

---

## 6. 对猜想的重新评估

比第一次评估更强的支持：

- 路由在信息量上几乎等价于文本（91.2% token 重建），聚合后是可用的语义嵌入（MoEE），同一 token 的路由随任务上下文和轨迹历史而变（Chen 2026）。"路由只是花哨的 tokenizer"这个担忧已被部分否定，但仍需在自己的模型上量化。
- 路由统计 + 轻量分类器已在有害 prompt（RouteScan）、幻觉（InnerExpert）、拒绝行为（MASCing）三个标签上被证明有效，且用的正是我们准备用的模型（OLMoE、gpt-oss-20b）。
- 攻击方（RouteHijack）已把"router logits 异常检测"点名为防御方向。

更精确的限制：

- **RASET 划定了边界**：路由检测的是"任务或话题变了"，不是"同一任务做得不对"。客服到代码是有利情形；违反政策的让步是不利情形。这应作为课题的核心对照实验而不是回避。
- **prefill 路由不能预测生成路由**（Myth 论文），末层在 prefill 时会塌缩：必须监测解码阶段，末 1 到 2 层单独处理，特征取中间层。
- **文本证据泄漏**（Boxo）与**注入存在 vs 偏离发生**（Li 2026）：这两个混淆决定实验是否可信，对应的对照必须从一开始就设计进去。
- 自适应攻击（Bailey、RouteHijack）会是审稿人的第一个问题。

经 2026-09-03 的补充检索后，问题层的新颖性应表述为：**尚未发现工作研究能否把生成阶段逐层逐 token 的 MoE 路由轨迹作为固定职责生产 agent 的运行时信号，检测其实际行为是否从 routine work 转向跨领域工作，并区分攻击暴露、自然抵抗与实际服从。** 已有工作分别证明聚合的 prompt + generation 路由可识别任务类别（Task-Conditioned Routing Signatures）、路由遥测可识别有害 prompt（RouteScan）、路由序列可预测并 steer 安全行为（MASCing）、MoE 信号可逐 token 检测幻觉（InnerExpert），以及跨轮激活轨迹可检测 MCP conversation drift（SecMCP）。因此 novelty 来自尚未研究的问题交集与潜在技术方向，不要求当前阶段已经提出具体检测方法；也不能单独把“路由携带任务信息”或“内部轨迹检测任务漂移”作为 novelty。

仍未发现被覆盖的、更可防守的贡献点是：生成过程中任务内变点的逐 token 定位；多轮与工具调用边界上的路由状态跟踪；路由在文本、隐藏状态和注意力之外的 **增量信息**；达到固定低 FPR 时的检测提前量与实际拦截收益；以及离散 top-k 路由对自适应混淆攻击的相对鲁棒性。这里的“未发现”是系统检索结论而非绝对不存在证明，投稿前仍需对当期 arXiv 与会议 proceedings 做一次滚动更新。

---

## 7. 对实验计划的修正

相对上一轮的六步方案，文献带来这些具体改动：

1. **模型与记录**：OLMoE 用 forward hook 于 `.mlp.gate`，gpt-oss-20b 用 `.mlp` pre-hook 重算 logits；解码阶段用自写循环记录，同时保存 pre-softmax logits、top-k 索引和全分布概率；记录消息角色（system / user / tool / assistant）和轮次。
2. **可分性检查扩展为三组对照**：话题改变 vs 格式改变（同话题改成 JSON 或 markdown）vs 意图改变（同话题合规 vs 违规），复现 RASET 的 JS 散度测量；加入 token 身份匹配对照（Chen 2026 的锚点 token 法、Antoine 的逐词形最常见专家基线）。
3. **场景改用 tau2-bench**（airline + retail），不自造；攻击集按 4.2 的四类从现成来源移植；标签用 tau2 的确定性判定。
4. **特征**：中间层的逐层专家分布、router 熵、有效专家数（InnerExpert）、加权 Jaccard 轨迹距离（Chen 2026）、MoEE 风格的路由权重嵌入；窗口聚合用 max 或注意力池化而非均值（RL-Obfuscation）。
5. **检测器**：无监督距离（对正常轨迹建画像）、逻辑回归探针、LSTM 序列模型（MASCing）；上界用同层残差流探针（TaskTracker 风格）。
6. **决定性实验**增加两项：同话题意图偏离组上路由是否失效（量化 RASET 的边界）；路由感知的自适应攻击（RouteHijack 式后缀）下检测器的退化程度。
7. **报告口径**：1% FPR 下召回、按攻击族留出、检测延迟（token 数）、注入存在与偏离发生分开报、平凡文本基线并列。

需要用户决定的两件事：tau2-bench 的用户模拟器用 API 模型还是本地第二个模型；第一阶段是否只做 OLMoE 上的可分性检查再决定是否投入 gpt-oss-20b 的 agent 搭建。

---

## 8. 精选参考文献

完整清单（约 150 条，含摘要与相关性评级）见 [docs/lit_notes/](lit_notes/)。

**路由编码了什么**
- Jiang et al., 2024. Mixtral of Experts. https://arxiv.org/abs/2401.04088
- Muennighoff et al., 2024. OLMoE: Open Mixture-of-Experts Language Models. https://arxiv.org/abs/2409.02060
- Zoph et al., 2022. ST-MoE. https://arxiv.org/abs/2202.08906
- Lo et al., NAACL 2025 Findings. A Closer Look into Mixture-of-Experts in LLMs. https://arxiv.org/abs/2406.18219
- Fan, Messmer & Jaggi, 2024. Towards an empirical understanding of MoE design choices. https://arxiv.org/abs/2402.13089
- Antoine, Bechet & Langlais, COLING 2025. Part-Of-Speech Sensitivity of Routers in MoE Models. https://arxiv.org/abs/2412.16971
- Olson et al., EMNLP 2025 Findings. Probing Semantic Routing in Large MoE Models. https://arxiv.org/abs/2502.10928
- Li & Zhou, 2024. Your MoE LLM Is Secretly an Embedding Model For Free. https://arxiv.org/abs/2410.10814
- Bandarkar et al., ICLR 2026. Multilingual Routing in Mixture-of-Experts. https://arxiv.org/abs/2510.04694
- Chen et al., 2025. Ban&Pick. https://arxiv.org/abs/2509.06346
- Wang, Hayou & Nalisnick, 2026. The Myth of Expert Specialization in MoEs. https://arxiv.org/abs/2604.09780
- Hayashi et al., 2026. Layer-wise MoE Routing Locality under Shared-Prefix Code Generation. https://arxiv.org/abs/2604.17182
- Chen et al., 2026. Does the Same Token Mean the Same State? MoE Routing as Signal for Reasoning Control. https://arxiv.org/abs/2606.22798
- Ye, Yuan & Sharkey, 2026. Polysemantic Experts, Monosemantic Paths. https://arxiv.org/abs/2604.17837
- Nuriyev & Kulp, 2026. Expert Selections In MoE Models Reveal (Almost) As Much As Text. https://arxiv.org/abs/2602.04105
- Wang et al., 2026. DBES: Benchmark and Metric Suite for Expert Specialization. https://arxiv.org/abs/2605.18498
- Guo et al., NeurIPS 2025. Advancing Expert Specialization for Better MoE. https://arxiv.org/abs/2505.22323
- Lasy, Cai & Ayonrinde, ICLR 2026 workshop. RouterInterp. https://openreview.net/forum?id=9a5i2vyMwN

**路由作为信号与 MoE 安全**
- Lv et al., 2026. RouteScan: Auditing MoE LLMs Safety via Expert Routing Telemetry. https://arxiv.org/abs/2605.24817
- Fonseca et al., 2026. Mixture-of-Expert Blocks Contain Strong Hallucination Detection Signals (InnerExpert). https://arxiv.org/abs/2608.17687
- Avinash, 2026. Task-Conditioned Routing Signatures in Sparse Mixture-of-Experts Transformers. https://arxiv.org/abs/2603.11114
- te Lintelo et al., 2026. MASCing: Configurable MoE Behavior via Activation Steering Masks. https://arxiv.org/abs/2604.27818
- Zhang et al., EMNLP 2026. RASET: Router-Agnostic Safety-Critical Expert Tuning. https://arxiv.org/abs/2605.29708
- Siddiky, 2026. Safety-Oriented Routing Analysis of Mixtral MoE. https://arxiv.org/abs/2605.24270
- Fayyaz et al., ICLR 2026. Steering MoE LLMs via Expert (De)Activation (SteerMoE). https://arxiv.org/abs/2509.09660
- Lai et al., NeurIPS 2025. SAFEx. https://arxiv.org/abs/2506.17368
- Wu et al., 2025. GateBreaker. https://arxiv.org/abs/2512.21008
- Xu et al., 2026. RouteHijack. https://arxiv.org/abs/2605.02946
- Ding et al., CCS 2025. MoEcho. https://arxiv.org/abs/2508.15036
- Hayes et al., 2024. Buffer Overflow in Mixture of Experts. https://arxiv.org/abs/2402.05526
- Yona et al., 2024. Stealing User Prompts from Mixture of Experts. https://arxiv.org/abs/2410.22884

**激活监测**
- Abdelnabi et al., SaTML 2025. Get my drift? Catching LLM Task Drift with Activation Deltas (TaskTracker). https://arxiv.org/abs/2406.00799
- Shi et al., 2025. Quantifying Conversation Drift in MCP via Latent Polytope (SecMCP). https://arxiv.org/abs/2508.06418
- Zou et al., 2025. PIShield. https://arxiv.org/abs/2510.14005
- Wen et al., EMNLP 2025 Findings. Defending against Indirect Prompt Injection by Instruction Detection. https://arxiv.org/abs/2505.06311
- Hung et al., NAACL 2025 Findings. Attention Tracker. https://arxiv.org/abs/2411.00348
- Abdelfattah et al., 2026. kNNGuard. https://arxiv.org/abs/2607.02072
- Kulkarni, 2026. Latent Adversarial Detection. https://arxiv.org/abs/2604.28129
- MacDiarmid et al., 2024. Simple probes can catch sleeper agents. https://www.anthropic.com/research/probes-catch-sleeper-agents
- Goldowsky-Dill et al., ICML 2025. Detecting Strategic Deception Using Linear Probes. https://arxiv.org/abs/2502.03407
- McKenzie et al., NeurIPS 2025. Detecting High-Stakes Interactions with Activation Probes. https://arxiv.org/abs/2506.10805
- Kramár et al., 2026. Building Production-Ready Probes For Gemini. https://arxiv.org/abs/2601.11516
- Bailey et al., ICLR 2026. Obfuscated Activations Bypass LLM Latent-Space Defenses. https://arxiv.org/abs/2412.09565
- Gupta & Jenner, 2025. RL-Obfuscation. https://arxiv.org/abs/2506.14261
- Boxo et al., 2025. Linear probes rely on textual evidence. https://arxiv.org/abs/2509.21344
- Li et al., 2026. When AUC 0.998 Is Not Enough. https://arxiv.org/abs/2606.22864
- Kirch et al., ACL 2026. The Impact of Off-Policy Training Data on Probe Generalisation. https://arxiv.org/abs/2511.17408
- Arditi et al., NeurIPS 2024. Refusal in Language Models Is Mediated by a Single Direction. https://arxiv.org/abs/2406.11717
- Greenblatt et al., ICML 2024. AI Control. https://arxiv.org/abs/2312.06942

**Agent 安全与场景**
- Yao et al., 2024. tau-bench. https://arxiv.org/abs/2406.12045
- Barres et al., 2025. tau2-bench. https://arxiv.org/abs/2506.07982
- Debenedetti et al., NeurIPS 2024. AgentDojo. https://arxiv.org/abs/2406.13352
- Zhan et al., ACL 2024 Findings. InjecAgent. https://arxiv.org/abs/2403.02691
- Abdelnabi et al., 2025. LLMail-Inject. https://arxiv.org/abs/2506.09956
- Zhang, 2025. Language Model Agents Under Attack: Profit-Seeking Behaviors in Customer Service. https://arxiv.org/abs/2512.24415
- Pai & Xian, 2026. FraudBench. https://arxiv.org/abs/2608.18136
- Korgul et al., 2026. It's a TRAP. https://arxiv.org/abs/2512.23128
- Evtimov et al., NeurIPS 2025. WASP. https://arxiv.org/abs/2504.18575
- Bhagwatkar et al., NeurIPS 2025. Indirect Prompt Injections: Are Firewalls All You Need, or Stronger Benchmarks? https://arxiv.org/abs/2510.05244
- Chennabasappa et al., 2025. LlamaFirewall. https://arxiv.org/abs/2505.03574
- Zhu et al., ICML 2025. MELON. https://arxiv.org/abs/2502.05174
- Huang et al., 2026. PrefixGuard. https://arxiv.org/abs/2605.06455
- Chua et al., 2024. Off-Topic Prompt Detection (GovTech). https://arxiv.org/abs/2411.12946
- Mu et al., 2025. A Closer Look at System Prompt Robustness (RealGuardrails). https://arxiv.org/abs/2502.12197
- Greshake et al., 2023. Not what you've signed up for. https://arxiv.org/abs/2302.12173
- OWASP, 2025. Top 10 for Agentic Applications 2026. https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- VentureBeat, 2023. Chevrolet of Watsonville chatbot incident. https://venturebeat.com/ai/a-chevy-for-1-car-dealer-chatbots-show-perils-of-ai-for-customer-service

**工具链**
- transformers issue #31722（generate 不返回 router logits）https://github.com/huggingface/transformers/issues/31722
- transformers issue #40104（use_kernels 绕过 router）https://github.com/huggingface/transformers/issues/40104
- bitsandbytes issue #1849 / PR #1965（融合专家无法 4-bit 量化）https://github.com/bitsandbytes-foundation/bitsandbytes/issues/1849
- vLLM `--enable-return-routed-experts` https://docs.vllm.ai/en/stable/configuration/engine_args/
- transformers MXFP4 文档 https://huggingface.co/docs/transformers/main/en/quantization/mxfp4
