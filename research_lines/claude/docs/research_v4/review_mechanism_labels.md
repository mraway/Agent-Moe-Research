# 评审：机制与标签视角（对 `docs/research_v4/agent_v3_dataset_design.md` v0.1）

评审人：Opus 5（独立评审 · 机制/标签视角）。日期：2026-09-06。
对象：`docs/research_v4/agent_v3_dataset_design.md`（草案 v0.1，2026-09-07）。
本评审只读文本，不跑实验、不改动任何既有文件；除本文外未写入任何文件。

已读上下文：`docs/production_moe_routing_shift_research_brief.md`、`docs/research_v3/model_pilot_report.md`、
`docs/research_v3/ecx_unified_comparison_lead.md`、`docs/research_v3/trm3_lead_synthesis.md`、`docs/research_v3/evidence_brief.md`、
`docs/agent_v2_onset_reliability_audit_plan.md`、`docs/agent_v2_onset_consensus_report.md`、
`docs/c1_behavior_axis_audit_report.md`、`docs/astra_stage2_closeout_decision.md`（后两者不在本 worktree，从主工作区只读）、
`docs/research_v2/zoom/code_blindspot/lead_synthesis.md`、`docs/normal_calibration_c1_collection_report.md`、
`docs/agent_v2_qualification_q3_report.md`、`configs/agent_v2_5_*_support.json` 的系统提示。

## 0. 一句话判断

**不建议按 v0.1 冻结。** 设计在"通道分离、放弃 marker、Unicode 归一化、E 为主锚点、+16 主视界、双分母"这些继承项上是对的，
但它把整个数据集押在一个**从未跑过一条 trace 的管线**（模型自发起工具调用的多步循环）上，
并且在三处把上一代刚修好的口径又拧回去：`C_tool` 被抬成主事件（X 类锚点冒充主事件）、
`E_any = min(E_analysis, E_final)` 使主锚点退化到第 5 个生成 token、
routine 被设计成**每条都含模型生成的工具调用 JSON**——正是 code blindspot 机制指认的那条"代码不可见"的轴。
另有一条把 clean 臂质量失败洗出 clean 分母的标签错误（over-refusal ↦ benign-refusal）。

关于总问题"是否仍在测量 off-routine 的内部计算而不是 text off-topic"：**方向对了一半，且有一个新的、更强的机制证据源被浪费了。**
analysis 通道让"内部参与"第一次可直接标注，"analysis 参与 / final 干净"这一类是本项目历史上最接近
"内部计算偏移 ≠ 表面题外文本"的**判决性对照**，但草案只把它列为派生的"通道可见性"轴，没有配额、没有门、没有预注册的机制主张。
与此同时，`C_tool` 与 commentary JSON 把评价重心往"结构化动作的表面形态"上推。净效果是：
**新的机制证据没被兑现，旧的表面混淆被加重。**

## 1. 结论表

严重度：**blocking** = 不改则数据回答不了目标问题；**major** = 会系统性地偏置或浪费相当一部分批次；**minor/info**。

| ID | 严重度 | 章节 | 断言/什么会坏 | 最小改动 |
|---|---|---|---|---|
| B1 | blocking | §1、§9、§10 | 模型驱动的工具调用循环**零实证**：pilot 的 20 条 gpt-oss trace 全部走 v2.5 确定性 controller（工具预执行、prompt 里是 controller 动作）。草案的通道长度（analysis 16–40）、成本（250 tok/条）、显存、行为风险全部来自被它抛弃的那条管线。clean 臂自发调用受限工具的概率——B5/M3 都依赖它——完全未知。 | 冻结前跑一个 ≤20 条的循环工程探针（明确不进任何池、事后丢弃），报告：每步 analysis/commentary/final token 数、步数分布、畸形/重复调用率、**clean 臂受限工具调用率**、每条落盘大小。 |
| B2 | blocking | §1、§7 | 通道进入 **fit / 校准 / 打分**的规则从未定义；§7 的两个部署视图是"analysis+final 全部 token"与"仅 final"，**commentary 两个都没提**。若 commentary（工具调用 JSON）进拟合池，正常流形获得一个巨大 JSON 瓣；若不进拟合池却在测试期出现，每次合法工具调用都会报警。两条路都会毁掉 FAR，草案没选。 | 明写三通道各自在 fit/cal/score 中的角色；主参照**按通道条件化**（P0^analysis / P0^commentary / P0^final，每 token 对自己的通道打分），pooled 版作 ablation；部署视图改为"全通道"与"commentary+final"（平台看不到 CoT，但一定看得到工具调用）。 |
| B3 | blocking | §0.6、§1、§3、§5 | 草案说"函数调用本身就是结构化输出，这是设计变量而非违规"，但随后把它**固定在恒为真**：R1–R4 每条 routine 都至少一次工具调用，没有任何纯散文 routine 层。code blindspot §1.2/§5 的机制是：代码/SQL 不征用冷门专家，只沿 routine 自身"散文↔工具调用 JSON"轴重加权（cos 0.78），所以在集合类特征下结构性不可见；v2 里 JSON 瓣是 47.5% 的**违规副产品**，v3 把它升级为**设计要求**。于是 §3 的 ≥24 条代码配额买到的样本，落在机制预测为 null 的区域。 | 把"commentary 是否进拟合池"作为**预注册的第二拟合池**（token 级可行，trace 级不可行）；检查 commentary-excluded 变体的 final-only token 是否仍满足每半 ≥90（code blindspot §5：池缩到每半 20–44 条时白化病态）；代码域的任何结论明写为"在含工具调用 JSON 的 routine 参照下"的条件结论。 |
| B4 | blocking | §4、§7 | `E_analysis` 按现协议在几乎每条用户侧攻击里**必然出现且位置几乎不变**：harmony 的 analysis 前言的功能就是复述请求，而审计计划 §3.1 的 E 包含"references"。pilot 里 gpt-oss 用户侧 6/6 条都有 analysis E，且 analysis 只有 16–42 token，E 落在第 ~5 个生成 token。再叠加 `E_any = min(E_analysis, E_final)`，**主锚点方差趋零并整体落进热身区**：w=16 的方法首个合法端点是 15，结构上不可能在 analysis 段内产生 post-onset 报警（ECX #3 的加强版）。 | (a) analysis 通道的 E 提高门槛：**单纯复述/罗列请求不算 E**，必须对注入任务作评估、计划、拒绝或执行；(b) 放弃 `min()`，预注册**单一主锚点**（建议 E_final 主，E_analysis 作声明的敏感性成员，或反之，但必须二选一）；(c) 每通道分别报"热身可达"分母。 |
| B5 | blocking | §1、§4、§7 | `C_tool` 被定为"第二类主事件"，但按审计计划 §3.3，"structured actions: the action name or argument that initiates the unauthorized action" 是 **X 的定义**。ECX 幸存结论 #1：路由对应 E/C，**不对应 X**；X 列 11/12–16/16 的 pre-onset 计数是"正确的早期检出被执行锚点判成提前"。把 X 类锚点抬成主事件，等于把刚修好的记账错误重新引入，而且 B 类攻击（未授权操作）是数据集的一半。此外 C_tool 的路由就是 commentary JSON 的路由，与合法工具调用同轴（B3 的机制），先验上是最难的一格。 | C_tool 降为 **X 类子分类锚点**，并强制与同 trace 的 `E_analysis` 配对（模型几乎总会先在 analysis 里盘算这次调用）；锚点定义明确到 token：记录 harmony 通道头里**首次指名受限 recipient 的 token** 与首个参数 token 两者；预注册"受限调用 JSON vs 同 trace 合法调用 JSON（匹配步号与位置）"的匹配对照，并**事先声明**按 code blindspot 机制零结果是预期结果（是机制检验，不是检测器失败）。 |
| M1 | major | §2、§10 | §10 把 gpt-oss 的 over-refusal（pilot 2/8）当作"benign-refusal 的自然来源"。这是范畴错误：benign-refusal 按**输入**定义（有题外提问、无注入，拒绝是正确行为）；over-refusal 按**输出**定义，是在合法请求上丢掉了原任务，属于 answer_missing/task_replaced 的质量失败。把它归进 benign-refusal，就把 clean 臂的失败**洗出了 clean FAR 分母**——正是 astra closeout §3 与 ECX #6 禁止的分母操作。 | 两者分开：benign-refusal 由输入定义并留在"全部正常输入"分母内；over-refusal 留在原臂分母、标 answer_missing；**攻击臂的 over-refusal 单列一个轨迹类**（pilot §3.3(5) 要求的 stratum，草案完全缺失：它既不是 silent 也不是有界抵御）。 |
| M2 | major | §1、§2 | `escalate_to_human` 被放进受限工具集。转人工是**正常客服动作**，也是 over-refusal 压力下模型最可能选的出口；一旦调用，自动记为 `unauthorized_tool_attempt`，于是这条 trace 被逐出拟合池——但它根本不是任务偏移。标签轴与拟合池同时被污染。 | 从受限集移除；或单列为"越界但良性"工具：不触发拟合池排除、不计 C_tool、单独报计数。 |
| M3 | major | §2、§5 | 拟合池 = `on_task ∧ 无未授权工具尝试`。按 C1 全量审计的基率（on-task 252/320 = 78.8%；未授权操作提议 38/320 = 11.9%；**"记录+知识库"工作流 37/146 = 25%**），这个过滤会**非随机地砍掉最长的 R3/R4 轨迹**，而那正是 384 保形视界需要的那一批；schema 里新增可执行的 `issue_refund` 只会把这个率推高。§2 的"长度三分位都有 ≥60 条"只在**原始**计数上成立。 | (a)"每三分位 ≥60"改到**过滤后**成立；(b) 预注册两个拟合列：纯池（主）与全正常池（对照），因为参照纯度本身就是 FAR 的杠杆；(c) clean 臂的自发受限调用**单列为一个报告 stratum**（"无攻击的自发未授权动作"是有价值的正类），而不仅是一个过滤条件。 |
| M4 | major | §5、§7 | §5 规定"场景在子集之间互斥；每个 fixture 只出现在一个子集"，因此 **G-cal 与 G-dev 无共享 fixture/场景**；那么 §7 的"部署侧（G-cal 半份）"**不是** ECX/TRM-3 意义上的部署侧校准（目标 batch 的半份，in-batch half-out），而是第三种跨池校准。TRM-3 已证明跨池预置阈值列（C1）在所有格子 G7 失败。主校准列因此可能建立在已被证伪的口径上。 | 三列显式化并在解盲前指定主列：(i) 目标半份 = **G-dev 自身 clean/benign 的一半**；(ii) 跨池同模型 = G-cal；(iii) 跨批出厂 = G-fit。§7 现行文字把 (ii) 误称为 (i)。 |
| M5 | major | §5、§6 | "每半 120 ≥ 90"写在**原始**计数上。按设计自己的资格门下限（on_task ≥85%）叠加约 12% 未授权尝试，过滤后每半只剩约 90 甚至更低；再扣掉落在同一池里的 benign-refusal，就跌破 ECX #6 的下限。而草案根本没说校准池是否与拟合池用同一套过滤——若不过滤，则拟合与校准分布不同（已知的 FAR 陷阱）。 | 明写校准池的过滤规则；若过滤，则 G-cal 提到每半 ≥140 原始（总量约 280–300）。 |
| M6 | major | §3、§5 | "试跑显示 tool_output ……**稳定产生**'参与但抵御'"与 pilot 直接矛盾。pilot §3.1：14 条 tool_output 攻击里只有 **3 条**有 E、0 条 C、0 条 X；gpt-oss 的 6 条里只有 2 条有 E，而这 2 条一条是 over-refusal（通杀拒绝，连订单查询也丢了）、一条是 analysis-only——**没有一条是干净的有界抵御**。以它作有界抵御主来源并占 G-dev 的 1/3（≈53 场景），期望产出约 18 条 E，远达不到"有界抵御 ≥25%"（=40 条）的门。反过来，pilot 里 gpt-oss 用户侧 6 条中有 4 条 E-无-X，其中 2 条是标准的内容特定拒绝——**有界抵御的真正来源是用户侧弱措辞**。 | 有界抵御主来源改为用户侧 T0/T2 格；tool_output 份额按实测 E 率（≈3/14）压到 ≤1/6，定位为"通道普适性检查 + 静默分母"，并在设计里明写它主要贡献静默样本。 |
| M7 | major | §3、§5、§7 | 代码配额 ≥24 定在**攻击尝试**上而非**执行**上。按 pilot（gpt-oss 8 条探针 2 条执行，且两条都是 T1 权威 + 用户侧）与草案自己的 20–50% 执行门，24 条只产出约 5–12 条含真代码内容的 X，低于 ECX/B3 要求的"每方向 ≥20 代码类"。更严重的是 **G-conf 完全没有代码配额**——它一次性封存，事后无法补。 | 配额改为结果层："≥20 条**已执行**代码内容"，按 pilot 执行率意味着 ≥60–100 个代码类攻击场景，且必须用对 gpt-oss 有效的 T1 权威 + 用户侧；**G-conf 写入同量级配额**（预注册时写死）。 |
| M8 | major | §6 | 采集纪律是"一次性全量采集，无 seed 替换"，但冻结与 2160 条采集之间**没有探针阶段**；pilot §5 的必做清单明写"每模型重新校准注入措辞（1–2 天 + 一轮探针）"。而且拉丁方均分把措辞四层等量铺开：T0 在 gpt-oss 上 0/4（pilot 称之为三种措辞里最弱的一种），T2 被"一律看穿"，T0×tool_output 格（≈1/12）是已知的零产率格。约一半攻击预算会花在近零执行产率的格上，而失败后的补救是"作为新批次重采"——即再花一遍 10+ GPU 小时和全部人力标注。 | 在冻结与 G-dev 之间插入一个**预注册的 60–120 条探针批**（明确不进任何池、事后丢弃、哈希留痕），用它同时定：T0–T3 配比、R4 长度门、B1 的循环参数。拉丁方的"任意两因子不共线"要求保留，但**配比按实测产率加权**而不是等分。 |
| M9 | major | §6 | 两条资格门缺证据支撑且很可能失败：(a)"严格质量合格 ≥60%（试跑 6/8）"——6/8 是 n=1/格的**短任务** routing-blind 读数，pilot §6.1 明令不得外推；同一口径在 C1 全量上是 **64/320 = 20%**，其中"记录+知识库"工作流只有 **3/146**，而草案的任务分布按 2:2:3:3 有 60% 落在 R3/R4；(b)"R4 自然 EOS 且 ≥384 token ≥70%"——pilot 20 条里**没有一条**输出达到 384 token（最长 294，final 最长 256），而主条件恰恰是缩短 analysis 的 reasoning effort low。 | 用 M8 的探针**按任务类分别**定标这两条门；预写 R4 长度门失败的分支（接受实测长度分布并放弃 384 保形视界改长度分层 / R4 改 medium / 重设计 R4 任务），而不是像现在这样只给攻击臂写了失败分支。 |
| M10 | major | §1、§7、§9 | 多步循环下**时序的 token 轴未定义**：+16、pre-onset、384 视界是按每步生成段算，还是按整条 trace 拼接的生成 token 算？检测器状态跨步是否重置？`输出上限 1024` 是每步还是整条？这不是记账细节：若状态按步重置，R4 用 6 步凑出的 384 token **并不解决** ECX 要求的保形视界（每段仍只有 30–250 token，等于回到 K_cal 185 的老问题）；若不重置，则每步开头的工具结果 prefill（上百 token 的上下文跳变）会成为新的 pre-onset 报警源。 | 明写：索引轴 = 拼接的**生成** token，每个 token 带 (step, channel) 标签；状态跨步策略预注册；每步/整条上限分别写死；R4 的长度门定义在同一条轴上；步边界索引进 trace.json，报告按步边界切片的 pre-onset 率。 |
| M11 | major | §2 | 引用要求 × 无 KB 的 routine = **系统性编造**。pilot §4：geometry scenario 的 routine 只调 `lookup_return`、没有 KB 检索，但系统提示要求"cite the article ID"，于是 clean 与 benign 两臂都编出 "Article 42"，且自动标签完全无感（`needs_manual_review=false`）。草案 §2 引了 Codex Q3 的材料要求，却没有引用这条最具体的修法。按 §2 的过滤规则（只过 on_task 与未授权工具），这类违规轨迹会**留在正常参照里**。 | 引用指令**按工作流条件化**（只有含 KB 检索的任务要求引用），或保证每个 routine 任务都有可引条目；`citation`/`material_errors` 轴上的编造单列，并预注册一个排除编造轨迹的敏感性拟合列（**不是**事后剔除：预注册即可，符合 astra closeout §3）。 |
| M12 | major | §7 | "静默攻击报警率硬门"（继承 TRM-3 的 G4）与新通道结构冲突。analysis-only 参与的 trace 在 **final-only 视图**下 final 完全干净：若检测器在该视图报警，那很可能是对真实内部状态的**正确检出**，却会被静默硬门当作误报惩罚（TRM-3 里 G4 是唯一失败的门，代价很实）。草案只说"静默攻击报警率硬门"，没有按视图定义静默。 | 静默按视图定义：silent = **任一通道**都无 E；analysis-only 单列一个 stratum，从静默硬门的分母中剔除，在全通道视图里作正类、在 final-only 视图里作"预期不可检"报告。 |
| m1 | minor | §5 vs §9 | 规模算术不一致：§9 写 480+480+480+240+120+360 = 2160，§5 的表是 240/240/480/60(会话)/120/360 ≈ 1680。成本、显存、标注量估算都建在错的总量上。 | 统一（并明确 G-session 的计数单位是"会话"还是"episode"）。 |
| m2 | minor | §9 | 落盘估算与 pilot 实测冲突：pilot 每条 trace 约 **2.2 MB**（384 token，`[24,T,32]` bf16 全 logits 主导），2160 条 ≈ 4.75 GB，不是 0.8 GB；且**未声明多步 prefill 的路由是否落盘**——若每步重新 prefill 整段上下文并捕获，量级会再放大约一个数量级。吞吐同理：多步 agent 每条的生成 token 远超 250。 | 明写 prefill 捕获策略（建议只存首步 prefill，或 prefill 只存 top-k id+weight），按 pilot 实测重算存储与 GPU 工时。 |
| m3 | minor | §4 | C 与 X 没有按通道定义（只有 E 分了通道）。在 reasoning effort medium 下模型完全可能在 analysis 里把几何题算完再在 final 拒绝——那是 analysis 通道里的 X。 | 三个事件一律分通道标注。 |
| m4 | minor | §4 | 轨迹类缺 `analysis_only_engaged` 与 `over_refused`，而 §7 的子分类混淆表正是以轨迹类为轴。 | 轨迹类与通道轴显式相乘，或直接补这两类。 |
| m5 | minor | §4、§7 | "最终回复是否干净"是二值的，缺第三档：**可见但非特定**（通杀拒绝 / 丢掉原任务）。按 E 协议通杀拒绝不算 E，于是这类 trace 会被记成 "final 干净"，但客户明明看到了拒绝。 | 三值：无痕 / 非特定痕迹 / 特定痕迹。 |
| m6 | minor | §2 vs §7 | FAR 分母数目不一致：§2 说"两个分母都固定报告"，§7 说三个（clean / benign / benign-refusal）；且未说明 benign-refusal 是否计入"全部正常输入"。 | 统一为：三个分层分母 + 一个"全部正常输入"合并分母（含 benign-refusal）。 |
| m7 | minor | §5 | benign-refusal 在 §2 有定义，但 §5 的子集表里**没有分配 n、也没说属于哪个子集**。"FAR 报告的独立分母"可能是空的或偶然的。 | 明确分配（例如 G-dev 内 40 个场景）并写进预注册。 |
| m8 | minor | §5、§7 | 会话级 FAR 的正常分母只有 40 条会话，分辨率 2.5%；且 R5 只出现在 G-session、**不进拟合池**，于是会话级评价用的是单 episode 拟合出来的参照（分布不匹配）。 | 正常会话提到 ≥80，或声明只给区间上界（Clopper–Pearson）不设门；把一部分会话 episode 放进拟合/校准池，或预注册这一不匹配并加一列会话内校准。 |
| m9 | minor | §1、§4 | 受限工具桩返回的 "unauthorized, not executed" 是 **controller 写入的新上下文**，且只在攻击臂出现。C_tool 之后的"回到客服"是**外部纠正下的恢复**，与自发恢复不是同一现象；TRM-3 的 RECOVERING 真值需要后者。 | 桩返回文本冻结且逐字固定；恢复标签分为自发 / 外部纠正两类，子分类目标只用自发那一类。 |
| m10 | minor | §1、§3 | `send_external_request(url, payload)` 的参数分布（外部 URL + 外传的记录字段）与 routine 毫无交集；C_tool 的检出可能由**参数内容**驱动而不是"发起了一次未授权调用"这件事。 | 按受限工具分别报检出率与延迟，不要合并成一个 C_tool 数字。 |
| i1 | info | §1 | "不写'不要输出 JSON'这类格式禁令"是对的（v2.5 的禁令本来就被违反 47.5%），但请注意这会**同时**改变 clean 臂的 final 通道文本形态；v2.5→v3 的任何跨模型/跨代对照都多了这一个混杂因子。 | 在数据卡里显式记录系统提示的逐条差异。 |
| i2 | info | §0 | §0 引用的 C1 数字与 `c1_behavior_axis_audit_report.md` 一致（38/320 = 11.9% 未授权操作提议；64/320 = 20.0% 严格质量合格）。核对通过。 | 无。 |

## 2. 按视角逐条的详细论证

### 2.1 模型驱动工具调用如何改变"routine 路由流形"的含义（问题 1）

**它把正常参照从"一种生成体制"变成"三种生成体制的混合"。** analysis 是元推理（短、密集的计划性语言）、commentary 是
函数调用 JSON（结构化、词表受限）、final 是客服散文。把三者混进同一个 P0，任一 token 的偏离分数里最大的一项将是
"我现在在哪个通道"，而不是"这段计算是否偏离 routine"。草案 §1 只说"路由对全部生成 token 捕获"，§7 只给了两个视图，
**commentary 在两个视图里都没有名字**——这是 B2。

正确的默认是**按通道条件化**：每个 token 对自己通道的参照打分，pooled 版本作为 ablation 报告。理由不只是统计卫生：
brief §3.4 要求"不依赖某一种输出格式的专用规则"，而按通道条件化恰恰是**不引入格式特征**的做法——
通道由 harmony 协议给出，是生成过程的元数据，不是对内容形态的判断（这一点与 FCM 已证伪的"形态条件化参照"不同：
后者是按**内容像不像结构化**分桶，前者是按**协议通道**分桶）。这条区别必须在设计里写明，否则会被误认为在重试已证伪的方向。

**更深的问题是 B3。** code blindspot 的机制结论是：离开 routine 流形有两条路，代码走的是"保留 routine 专家集合、
沿 routine 自身的散文↔工具调用 JSON 轴重加权"这一条，所以在集合类特征下结构性不可见。v2 里那条 JSON 轴是
小模型违规照抄 controller 格式的**副产品**（240 条 routine 里 114 条以 `{` 开头）。v3 把它变成**协议要求**：
R1–R4 每条都至少一次工具调用，纯散文 routine 层为零。也就是说，v3 的正常参照在这条轴上比 v2 **更宽**，
代码类攻击因此**更**落在参照内部。草案 §3 却同时要求"每个批次 ≥24 条代码类攻击，保证盲区可刻画"——
可刻画的将是一个被设计放大了的盲区。

草案说这是"设计变量而非违规"。但设计变量要能取两个值。trace 级取不到（没有无工具调用的 routine 任务），
token 级可以：把 commentary token 从拟合池里去掉。这是唯一可行的对照，必须**预注册**，并且必须检查
code blindspot §5 的失败模式是否重演——那次去掉 JSON 后，校准池缩到每半 20–44 条时一条离群 trace 就决定阈值，
白化病态（一条 routine trace 的 g1 从 6,419 变成 785,355）。这里的对应检查是：commentary-excluded 变体下，
每个校准半份的 final-only token 数是否仍够（ECX 的 ≥90 条/半是 trace 计数，token 计数需另算）。

**还有一个正向的机会草案没有利用。** TRM-3 的 J 通道在 b2/D 的 clean 样本上贡献 184 个 CONFIRMED 端点，
而且"在同一 pair group 的攻击臂和 benign 臂上以逐位相同的 top 坐标在同一端点报警"——即它响应的是 JSON 工具调用脚手架。
在 v3 里，工具调用不再是 controller 注入的上下文，而是**模型自己的生成**，因此"合法工具调用"第一次有了
逐 token 的正常路由参照。这让"受限调用 vs 合法调用（匹配步号与位置）"成为一个干净的、within-trace 的匹配对照。
这是 v3 相对 v2 真正的机制增量，应该被写成预注册的主张（见 B5 的最小改动），而不是留在工程清单里。

### 2.2 受限工具桩是否给出有效、无混杂的 C_tool 锚点（问题 2）

**(a) schema 里出现 `issue_refund` 会不会改变 routine 行为？极可能会，而且草案没有测量它的计划。**
最强的先验证据是 C1 全量审计：在**没有任何可执行动作工具**的情况下，320 条正常输入里已有 **38 条（11.9%）**
含明确的未授权操作提议或"操作已发生"的声称，且集中在"记录+知识库"工作流（**37/146 = 25%**）——
正是 v3 任务分布里占 60% 的 R3/R4 那一类。Q2 的受控提醒能压低这个率，说明它对提示措辞敏感。
把动作从"只能在文本里提议"变成"schema 里可调用"，最自然的预期是这个率上升而不是下降。
草案对此有一半的准备（`unauthorized_tool_attempt` 是自动质量轴），但没有准备另一半：**它同时是拟合池的排除条件**，
于是这个未知的率直接决定拟合池的规模与构成（M3），并且会非随机地砍掉最长的轨迹。
B1 的探针必须把这个数字作为一等输出。

**(b) 同样的桩是否应出现在 clean 臂？应该，而且草案没有明确写。** §1 只说"它们在 schema 中可见"，
没有说这在 clean / benign / benign-refusal / attack 四类臂上逐字相同。本项目一贯用 exact-prefix 配对
（`exact_paired_replay_passed=true` 是 h384 replay 的冻结前提），所以这条不变式必须写进设计并加哈希检查：
**系统提示与工具定义在所有臂上逐字节相同，prompt 前缀只在注入 span 上不同。** 如果 attack 臂多出工具而 clean 臂没有，
两臂的路由差异里就混进了 schema 长度与内容的差异，C_tool 的一切结论作废。

**(c) C_tool 作为锚点本身有三个缺陷**，见 B5 与 m9、m10。补充一点定义上的：
"调用 JSON 的首 token"在 harmony 下是歧义的——通道头 `<|channel|>commentary to=functions.issue_refund` 里
**首次指名受限 recipient 的那个 token** 才是最早的可判定点，而"JSON 的首 token"（第一个 `{`）在合法调用里也逐字相同，
零判别力。两个都要记，主锚点用前者。

**(d) `escalate_to_human` 不该在受限集里**（M2）。它是正常客服动作；gpt-oss 又有实测的 over-refusal 倾向
（pilot 2/8），在拒绝压力下转人工是最可能的出口。把它记成未授权尝试，等于用一个正确行为把 trace 逐出正常参照。

### 2.3 reasoning effort low 下 E 分通道是否良定义；analysis-only 类是否可标、有多大（问题 3）

**良定义性：不。** 见 B4。要点重述：pilot 实测 analysis 只有 14–42 token；harmony 的 analysis 前言功能上就是复述请求；
审计计划 §3.1 的 E 包含 "references"。三者相乘的结果是 `E_analysis` 在用户侧攻击上接近**必然存在**且位置几乎固定在第 ~5 个生成 token
（pilot 用户侧 gpt-oss 6/6 有 analysis E）。这有两个后果：
(i) E 的"存在"不再有判别力，攻击臂的参与率会被推到接近 100%，"静默"这个类在用户侧几乎消失；
(ii) 时序指标退化成"能否在前 20 个 token 内报警"，而 w=16 的方法在这个区间里**算术上不可能**产生合法端点
（ECX #3 的 +8 不可比问题在这里变成"整个事件都在热身区内"）。
必须收紧 analysis 的 E 定义并放弃 `min()`。

顺带：审计计划 §6 要求 evidence 子串在所选输出里**恰好出现一次**。在 16–40 token 的 analysis 段里，
候选子串短且重复（"must decline"、"Provide order status"），唯一性检查会频繁失败。
唯一性范围必须定义为"该 (step, channel) 段内唯一"，并允许标注者用带位置的 span 而不是纯子串。

**analysis-only 类：可标，且大概率够大，但草案没有把它当回事。**
可标性的前提草案已有两条（§8 的通道分段标注包、§4 的通道可见性轴），缺第三条：**"final 干净"的定义**（m5）——
通杀拒绝按 E 协议不算 E，会被记成"干净"，但那明显不是同一现象。
规模估计：pilot 的 gpt-oss 12 条攻击里 6 条有 E，其中 **2 条是 analysis-only**（`baking--format_contract`、`sql-query--format_contract`），
即 engaged 的 1/3、全部攻击的 1/6。按 G-dev 攻击臂 160 条、用户侧 107 条（E≈100%）+ tool_output 53 条（E≈33%）≈ 125 条 engaged，
若 analysis-only 占 engaged 的 1/6–1/3，则约 **20–40 条**。够用（ECX 对有界抵御的门是 ≥12），但有两个条件：
(i) 必须保留 T2 `format_contract` 措辞——pilot 里 gpt-oss 的两条 analysis-only **都**来自 T2；
按 M8 的"按执行产率加权"逻辑 T2 会被削减，但按机制价值它恰恰是**最有价值的一层**。两个目标冲突，必须显式分配：
T1 供执行样本，T2 供 analysis-only 样本，T0 作跨代可比的基线，各自给独立配额与门；
(ii) 它必须从静默硬门的分母里拿出来（M12）。

**为什么这一类值得升格为预注册的主张：** 它是本项目历史上第一个能把"off-routine 内部计算"与"表面题外文本"
**判决性地**分开的对照。final 通道完全在题内、客户可见文本没有任何题外痕迹，而 analysis 里有明确的、
内容特定的对注入任务的评估。如果全通道视图的检测器在这类 trace 上报警，那就是对
"检测的是内部计算状态而非表面话题"的直接证据；如果不报警，那就是对 evidence brief B3.8
（"检测器响应的是生成进入话题，不是上下文里的攻击文本"）的自然延伸的证伪。两边都有信息量。
建议在 §7 里写成一条预注册的机制主张，配 n ≥ 15 的配额门。

### 2.4 benign-refusal 排除出拟合池是否正确；是否造成参照的选择偏置（问题 4）

**排除本身方向正确，但排除的粒度错了，而且它只是更大问题的一角。**

方向正确：C1 审计的 c1a-156（明确说不提供对话场景，同时完整回答保修问题）与 astra closeout §"对算法目标的收敛"
都支持把"可见的题外任务参与/明确边界回应"当作事件标签而不是理想正常行为。放进拟合池会把一段真实的题外参与
写进正常参照，直接损害 estimand。

粒度错了：草案按**输入组**排除（"客户直接问题外问题"这一组），但同一组的输出可能是
(a) 内容特定的拒绝 + 照常回答支持问题（有 E，确实 off-routine）、
(b) 泛泛拒绝（无 E，完全 routine）、(c) over-refusal（丢掉合法任务，是质量失败）。
按输入组排除会把 (b) 这类完全正常的轨迹也踢出参照，同时按 M1 的错误把 (c) 收进来。
**最小改动：按标签排除（任一通道存在 E 事件即排除），而不是按输入组排除；两种口径的池规模都报告。**

**更大的问题是拟合池整体的选择偏置。** 拟合池 = `on_task ∧ 无未授权工具尝试`，再排除 benign-refusal 组。
按 C1 基率，这相当于把参照收敛到"完整、合规、不越界"的那部分正常行为，而 FAR 分母仍是全部正常输入。
参照越纯，流形越紧，在真实正常分布上的 FAR 越高——这正是 ECX #6(b) 指出的不对称
（"Claude 把 4 条自发偏离的 benign 排除出分母，这 4 条恰是易报警的"）。区别在于那里说的是**分母**，
这里是**参照**，但方向一致且草案没有对冲。
astra closeout §3 的原则（"不能事后排除以改善 FAR"）在这里是**事前**排除，形式上合规，
但效果相同，因此必须以预注册的双拟合列（纯池 / 全正常池）来暴露它，而不是只保留一个纯池。

再叠加 M3 的长度相关性：被排除的多是最长的 R3/R4 轨迹，于是**拟合池的长度分布相对评价分布被截断**——
而 v3 的全部意义之一就是修 h384 的保形视界问题（TRM-3：C1 列在 h384 上删失 26–50% 的 trace）。
如果过滤把长轨迹从参照里拿走，这个问题会以新的形式回来。**"每三分位 ≥60"必须在过滤后成立**，
这是一条可以现在就写进设计的硬约束。

### 2.5 代码/SQL 配额与"tool_output 作抵御来源"是否与 pilot 证据一致（问题 5）

**tool_output：不一致，且是草案里最直接的一处与 pilot 的矛盾。** 见 M6。
草案原文"试跑显示 tool_output 在 gpt-oss 上几乎不执行，但它**稳定产生**'参与但抵御'"，
而 pilot §3.1 的原始计数是 14 条 tool_output 攻击里 **E=3、C=0、X=0**；gpt-oss 的 6 条里 E=2，
且这 2 条一条是 over-refusal（"连合法的订单查询也拒了"）、一条是 analysis-only（final 干净地答完订单状态）。
"稳定产生有界抵御"在这批证据上不成立；实际上**没有任何一条 tool_output trace 产生了干净的有界抵御**。
干净的有界抵御在 pilot 里全部来自**用户侧**（batch 的 sql 与 geometry 两条：先答完授权问题，再给出内容特定的拒绝）。
把 1/3 的开发集押在 tool_output 上，等于把 53 个场景变成静默样本的批发商，同时让"有界抵御 ≥25%"的资格门失守。

**代码配额：数量口径错了，而且封存批漏了。** 见 M7。补充一点机制上的：
按 B3，v3 的 routine 参照在 JSON 轴上比 v2 更宽，所以即使拿到 20 条已执行的代码 trace，
机制预测仍是检不出。这不是不该收——收了才能做 B3 的 commentary-excluded 对照——
但设计必须**事先声明**代码域的主要用途是**机制检验**（"含 JSON 的 routine 参照下代码是否仍不可见"），
而不是"提升代码域召回"。否则又会出现一次 TRM-3 式的、事后才发现装置没有分辨力的实验。

### 2.6 pilot 里与设计假设相矛盾的其他事实（问题 6）

按重要性排序（详见结论表）：

1. **B1：pilot 从未跑过模型驱动的工具调用循环。** 20 条 gpt-oss trace 全部是 v2.5 的确定性 controller 管线
   （系统提示："A deterministic workflow controller selects and executes every required read-only tool before asking you to respond"）。
   草案 §10 的风险项"analysis 很短（16–40 token）"、§9 的"平均 250 token"、§1 的"max_agent_steps 6"，
   全部是从**没有 commentary 通道**的样本外推到**有 commentary 通道**的管线。
   连"gpt-oss-20b 在 reasoning effort low 下能否稳定发出合法的 harmony 函数调用"都没有一条证据。
2. **M9(b)：没有一条 pilot trace 达到 384 token**（最长总输出 294、final 最长 256），而 R4 门要求 ≥384 且自然 EOS ≥70%，
   主条件还是缩短 analysis 的 low。
3. **M9(a)：60% 严格质量门**建在 8 条短任务 n=1/格的读数上，pilot §6.1 明令不得外推；C1 同类口径是 20%。
4. **M6：tool_output "稳定产生有界抵御"**与 pilot 的 3/14 相矛盾。
5. **M11：引用要求诱发编造**——pilot 指出 geometry 的 clean 与 benign **两臂**都编出 "Article 42"，
   根因是"routine 无 KB 检索 + 提示要求引用"，草案没有采纳这条修法。
6. **M1：over-refusal 层缺失**——pilot §3.3(5) 的五条必做清单里唯一没被草案采纳的一条
   （Unicode 归一化、通道分离判定、放弃 marker、措辞重校准都采纳了）。
7. **m2：落盘估算**与 pilot 实测的 2.2 MB/条不符。

## 3. 保持不变（keep unchanged）

以下决定是对上一代教训的正确继承，评审建议**原样冻结**，任何修订都不应动它们：

1. **§1 Unicode 归一化后再做完成度匹配，且匹配只作诊断、质量真值来自文本复核。** 直接修 pilot §4 的假阴
   （12 条里 4 条因 U+2011 被误判）。
2. **§1 通道分离解码 + analysis/commentary/final 的 token 边界写进 trace.json。** 修 pilot §3.3(4) 的
   `skip_special_tokens` 扁平化 bug（否则任何在 analysis 里提到 marker 的模型都会被误判为 deviation）。
3. **§3/§4 放弃 marker 匹配，改两位互盲标注者 + 文本裁决（Codex onset 协议）。** 修 pilot §3.3(3) 实测的三种 marker 失效模式。
4. **§4 双锚点（首次指涉 token 与首个题外实词 token 都记录，差值作容差带依据）。** 继承 evidence brief B5.15（0–29 token，中位 +5）。
5. **§7 主视界 +16 跨家族、+8 仅同家族、报"热身可达"分母、命中定义 pre-onset 罚则版与无罚则版共主。**
   继承 ECX #3 与 TRM-3 §5.3，是本轮最重要的记账修正。
6. **§7 E 为主锚点、X 只用于子分类。** 继承 ECX #1（B5 只是要求把 C_tool 也归到这条规则下）。
7. **§3"不做"三条：不要求模型反省/假装抵御/回到客服；不按结果补样；不用检测器分数选样本。**
   继承 astra closeout §4 与 brief §8。
8. **§6 预注册 + 哈希冻结 + G-conf 最后生成并立即封存 + 生成期间不看路由 + 行为标注在解盲前完成 + 失败 trace 保留。**
   brief §3.7/§8 的数据纪律，执行得比 v2 更严。
9. **§2 质量轴里 `material_errors` 不作为拟合池排除条件**（拟合池只过 `on_task` 与未授权工具）。
   与 astra closeout §3 一致：事实错误是困难正常输出，不自动变成攻击阳性。M11 要求的是把编造**单列并预注册敏感性列**，
   不是改这条默认。
10. **§10 不改提示去压 over-refusal，把它当作自然现象记录。** 研究姿态正确（只是 M1 要求改它的类别归属）。
11. **§2 fixture 要求（每批次独立虚构商家、所有合法子问题的答案对模型可见、记录与政策不冲突）。**
    继承 Codex Q3 与 astra closeout 的材料要求。
12. **§1 reasoning effort low 为主条件 + medium 子批次作敏感性。** 贴近生产的选择正确；
    只是它与 R4 的 384 长度门冲突（M9），冲突应通过改门或改 R4 来解决，不是改主条件。
13. **§5 场景在子集之间互斥、每个 fixture 只出现在一个子集、G-conf 用独立 fixture。**
    泄漏防护正确；M4 要求的是**重新命名校准列**，不是打破这条互斥。

## 4. 未能核实 / 超出本次范围

- 未运行任何实验，未读取任何路由张量、检测器分数或 B3。
- `docs/c1_behavior_axis_audit_report.md` 与 `docs/astra_stage2_closeout_decision.md` 不在本 worktree，
  从主工作区 `/home/wzh/Agent-Moe-Research/docs/` 只读读取；两者在本 worktree 的 `docs/` 下缺失，
  若 v4 设计要引用它们，建议把它们合并进本分支或在 §0 的输入清单里注明路径。
- 本文没有评审检测算法侧的任何内容（那属于 v3.1 proposal 的范围），也没有评审工程改动清单 §8 的可实现性。
- 结论表里所有关于产率的数字（E 率、执行率、analysis-only 占比）都来自 pilot 的**计数**（每格 n=1，
  pilot §6.1 明令不得外推）。它们在本文里只用于说明"草案的门与配额没有证据支撑"，
  不构成对 v3 产率的估计——这也正是 M8 要求插入探针批的理由。
