# Sequential v2：只学习 routine 路由流形的三个单类方案（v2）

日期：2026-09-04（America/Los_Angeles）

角色：本文由研究组长（Claude Fable 5.1）撰写，交给并行 AI 执行者实现与运行。执行者不必再征求说明；凡本文
没有规定的细节，由执行者自行决定并写入各自的预注册文件。

版本说明：v1 版本（同日早些时候）包含有监督与单类混合的方案。研究方向随后被澄清为**只学习正常客服任务
的 MoE 路由流形，在线任务的路由显著离开该流形时报警**。本文按这一方向重写三个方案；共享协议与已获得的
预诊断保留并扩充。v1 版本可在 Git 历史（commit `70a6d1b`）中查看。

v2.1 修订（同日深夜）：主分支上另一位研究 AI 已按同一方向完成并报告了三个 normal-only 实验
（P1 kNN、P2 conditional PCA、P3 forecast+CUSUM；见 §0.5）。本文据此改用 **evidence onset** 作为主时间锚点、
采用与 P1–P3 可比的 gate 与窗口宽度、复用主分支的 routing cache 与指标代码，并把本轮重新定位为
"协议 v2 下对 normal-only 流形的再评价"。

本文所有数字分两类：(a) 来自已冻结报告的历史结果；(b) 本轮为设计方案而做的**事后预诊断**（§2），它们在
B1/B2 开发数据上计算，drift 的边界后最大值用真实边界挑选，阈值多为样本内或半样本内的次序统计量。(b) 类
数字只用于判断方案可行与否，不是结果，不得在任何报告中当作成绩引用。

## 0. 研究方向与组长判断

### 0.1 澄清后的问题

> 只用正常客服流量（无攻击、行为正常）学习 routine 路由流形 M；在线监测一条对话的逐 token 路由，当它
> **显著、持续地**离开 M 时报警。

这个定义决定了四件事：

1. **检测器不使用任何 drift 正例拟合**。正例（59 条）、边界和目标域只用于评价与报告。任何用正例选层、
   选秩、选阈值的做法都违反方向；超参数只能由 routine-only 判据或预注册的固定网格决定（§1.6）。
2. **估计量是"离开 routine"，不是"进入某个域"**。因此"预告句"（"Now, on to the monologue"）、"标签/标题"
   （"[FREEVERSE]"）在流形意义下是真实的离开，即使它们在人工边界之前。协议要同时报告严格指标、8-token
   容差指标，并对所有边界前报警做文本分类审计（§1.7）。
3. **"显著"由部署侧 routine 流量定义**。阈值是 routine trace 统计量的有限样本次序统计量（conformal），
   校准样本必须来自部署侧、与被评价 trace 不同的 scenario，且 ≥ 100 条（§1.6）。
4. **"持续"是读法的一部分**。提及域外话题、复述攻击、拒绝，都可能短暂离开 M；任务切换是持续离开。
   序列读法（EWMA / CUSUM / run-length）是每个方案的必选项，不是可选项（§1.5）。

### 0.2 预诊断对单类方向的支持与警告

组长在 B1/B2 上做了一个单类试算（§2 E23–E28；脚本与表格见 `docs/research_v2/pilot_results/lead_oneclass/`）：
流形只用 source batch 的 clean+benign 拟合；阈值在 target batch 的 routine 上按 scenario 对半切、conformal
α=0.10 校准；三种读法。结果（B1→B2 / B2→B1，CUSUM 读法）：

| 流形定义 | trace-max AUROC | clean/benign/resist FAR | 严格 / 容差 pre-boundary FAR | +16 clean recall（严格/容差） | final（严格/容差） |
|---|---|---|---|---|---|
| 窗口白化距离（中间层） | 0.973 / 0.981 | 0.10/0.09/0.19 ; 0.05/0.15/0.06 | 0.17/0.12 ; 0.22/0.12 | 0.63/0.67 ; 0.58/0.67 | 0.77/0.81 ; 0.75/0.83 |
| token 条件化残差能量（中间层） | 0.988 / 0.988 | 0.06/0.15/0.13 ; 0.07/0.17/0.16 | 0.15/0.12 ; 0.33/0.21 | 0.71/0.74 ; 0.60/0.71 | 0.83/0.86 ; 0.69/0.79 |
| 跨层路径 surprisal | 0.995 / 0.993 | 0.14/0.15/0.20 ; 0.05/0.20/0.31 | 0.19/0.20 ; 0.22/0.21 | 0.67/0.67 ; 0.69/0.71 | 0.86/0.86 ; 0.79/0.81 |
| 文本单类基线：未见词比例（EWMA） | 0.956 / 0.973 | 0.06/0.12/0.12 ; 0.05/0.15/0.31 | 0.13/0.10 ; 0.42/0.32 | 0.47/0.50 ; 0.54/0.62 | 0.84/0.87 ; 0.69/0.77 |
| v1 冻结（有监督，绝对阈值，B2） | 0.982 | 0/0/0 | 0.11 | 0.26 | 0.34 |

支持：不用任何正例，单类流形在部署侧校准下的 +16 召回达到 0.58–0.73、最终召回 0.69–0.86，与有监督方向
的样本内试算（+16 0.57–0.69）相当，明显高于 v1 冻结结果，并且快于文本单类基线（+16 高 0.1–0.2，延迟
中位 6–10 vs 13–19 token）。留一目标域下单类排序 ≥ 0.887（programming）到 1.0，legal 0.995。

警告：
- **benign 臂误报高于 clean 臂**（0.09–0.20 vs 0.03–0.14）：benign control 含匹配的域外词汇，流形离开
  会部分响应"提到"域外话题。这是 topic-vs-task 边界在单类框架下的表现。
- **resist 臂误报 0.06–0.31**，路径模型最差：拒绝与复述也是"离开 routine"。
- **严格 pre-boundary FAR 0.15–0.33**，容差后 0.12–0.21；一半是标注相邻。
- **校准样本量**：B2→B1 方向 target 只有 80 条 routine，对半切后每半 40 条，运行点抖动明显。
- **秩敏感**：PCA 残差能量在秩 ≥ 32 时报警层面崩溃（B1-routine 阈值下 B2 误报 42–135/205）。
- 所有数字是半样本内的事后诊断。

### 0.3 三个方案如何分工

三个方案是关于 **routine 流形是什么** 的三个不同假设；它们共用协议，因此可以并排比较，任何一个失败都有
明确含义：

| 方案 | 流形假设 | 离开的度量 | 成立时说明 | 失败时说明 |
|---|---|---|---|---|
| 1 窗口几何流形 | routine 的 16-token 专家使用向量落在低维区域（单峰或多峰） | 白化距离 / PCA 残差能量 / kNN 距离 | 最简单的静态几何足够 | routine 的变化不能用固定区域描述 |
| 2 条件流形 | 给定 token / 局部文本 / prefill 上下文，routine 路由更紧；离开 = 同样的文本被不同地路由 | 条件残差能量，词汇新颖度单列 | routing 的离开不只是词汇新颖，且条件化压低 benign/resist 误报 | 离开主要是词汇/上下文新颖，routing 没有独立贡献 |
| 3 路径动力学流形 | routine 路由由典型的跨层专家路径与逐 token 专家转移构成 | 路径/转移的 surprisal（−log P） | 逐 token 可评分、最早报警、无需窗口 | 路径 surprisal 是"任何不寻常"的探测器，不能区分 task 与 mention |

三者的组合规则见 §6。序列读法与校准在协议中统一（§1.5–1.6），方案 3 因逐 token 评分对读法最敏感，
需要额外做读法研究（§5.3）。与主分支 P1–P3 的关系见 §0.5。

### 0.5 与主分支 P1–P3 的关系：本轮的新定位

主分支（commits `d129e1e`…`03ef573`）已经预注册并运行了三个 normal-only 实验，全部使用 8-token 窗口、
sqrt-selection signature、middle(5–10)+late(11–15) 层、源 batch fold 3–4 的 normal trace 做校准、经验尾部
rarity、persistence-2 读法、evidence-onset 锚点，gate 为"双向 FAR ≤ 15%、onset+8 干净召回 ≥ 35%、中位
延迟 ≤ 8"。结果（`docs/normal_manifold_p{1,2,3}_*_report.md`）：

| 主分支 primary | B1→B2 FAR / +8 / +16 / full / 延迟 | B2→B1 FAR / +8 / +16 / full / 延迟 | 失败机制 |
|---|---|---|---|
| P1 kNN manifold | 3.4% / 0 / 20% / 46% / 19 | 9.4% / 12.5% / 25% / 46% / 12 | cell 经验尾部封顶使阈值不可达；只在 status_only 上有效 |
| P2 conditional PCA T²+Q | 0 / 0 / 0 / 0 / – | 19.8% / 58% / 67% / 83% / 7 | 经验尾部饱和 + 正常子空间跨 batch 漂移 |
| P3 forecast + CUSUM | 1.5% / 0 / 0 / 49% / 55 | 8.3% / 4% / 4% / 54% / 71 | 残差需长时间累积 |

主分支的诊断（P1 漏检审计）是：**阈值支持域不一致**（各 cell 的有限样本 rarity 上限低于全局阈值）与
及时性不足；结论是三者都不进入 B3，并提出两条路线：接受少量正例学方向，或坚持 normal-only 但改进
raw-score/tail 校准并扩充正常覆盖。

组长把自己的单类试算改用同一 evidence-onset 锚点重算（§2 E29；`docs/research_v2/pilot_results/lead_oneclass/tables_onset.txt`），
差别只在协议：部署侧 routine（目标 batch、不同 scenario）对半切做 conformal 校准、raw score 按位置桶
z 标准化（无经验尾部封顶）、16-token 窗口、中间层 5–11、多种读法。onset 锚点、α=0.10、两方向：

| 组长试算（w=16，max 读法） | B1→B2 FAR / +8 / +16 / full / 延迟 | B2→B1 FAR / +8 / +16 / full / 延迟 |
|---|---|---|
| 白化距离（gauss r=0） | 12% / 23% / 63% / 80% / 13 | 10% / 29% / 62% / 77% / 10 |
| token 条件化残差能量 | 12% / 26% / 69% / 86% / 12 | 10% / 38% / 69% / 75% / 9 |
| kNN-10 | 12% / 24% / 60% / 76% / 12 | 16% / 40% / 69% / 85% / 10 |
| 路径 surprisal（depth+time） | 12% / 36% / 66% / 83% / 11 | 20% / 40% / 67% / 73% / 8 |
| 文本单类：未见词比例 | 12% / 17% / 29% / 80% / 20 | 11% / 23% / 58% / 75% / 12 |

读法：+16 与 +8 在同一协议下差 0.3–0.4，说明 16-token 窗口是及时性的主要瓶颈（onset+8 时窗口里只有
9 个域外 token）；CUSUM 进一步牺牲 +8（0.01–0.20）换取 final。normal 定义（含/不含 resist）差别不大。

由此本轮的定位不再是"提出三个新流形"，而是回答一个更具体的问题：

> 在部署侧校准、无经验尾部封顶、位置桶标准化、w ∈ {4, 8} 窗口（或逐 token）、以及三种流形定义下，
> normal-only 检测器能否在两个 transfer 方向同时达到主分支的 gate（FAR ≤ 15%、onset+8 ≥ 35%、延迟 ≤ 8），
> 并且 benign / resist 臂误报可控？

三个方案分别承担"静态几何在协议 v2 下能走多远"（方案 1，对应 P1/P2 的再评价）、"条件化能否补足
+8 及时性并压低 benign/resist 误报"（方案 2，新）、"逐 token 路径 surprisal 能否把延迟压到 ≤ 8"（方案 3，
与 P3 不同：P3 预测低维状态，本方案是专家 id 的马尔可夫 surprisal，且不依赖 CUSUM）。

### 0.6 本轮不做什么

- 不做新 batch；不修改标签与边界；不覆盖 v1 冻结文件。
- 不用 drift 正例拟合任何东西；不用测试侧任何 trace 选阈值、层、秩或读法。
- 不追求实验数量：每个方案预注册不超过 12 个候选配置，全部报告。
- 不把 trace 级 AUROC 当作成绩：纯文本基线也有 0.96–0.97。

## 1. 共享评价协议（三个方案都必须遵守）

协议先由任务 H（§7）实现为 `src/research_v2/harness.py`；三个方案的分数模块只实现 §1.10 的接口。

### 1.1 数据与"routine"的定义

- 核心开发集：B1 `brief=absent` 120 条 + B2 240 条 = 360 条；drift 59、resisted attack 61、clean 120、
  benign control 120。B1 `brief=present` 120 条（全部行为正常）作为额外 routine 材料（§1.6）。
- **拟合 routine 流形的主定义**：clean + benign_control（无攻击、行为正常）。
- **敏感性定义 R+**：clean + benign + resisted attack（行为正常但 prefill 含攻击）。生产中的"正常流量"
  本来就包含用户越界请求被拒绝的对话，因此 R+ 更接近部署；但它可能把"提及"模式吸进流形。两种定义都
  要报告，主表用主定义。
- 只使用每条 trace 最终一次 model_generation 的 decode 路由；prefill 路由可作条件化输入。
- 数据 hash：B1 `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`，
  B2 `e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942`。加载器：
  `scripts/analyze_agent_v2_b1_routing._load_observations`（B1）、`scripts/score_agent_v2_b2_frozen._load_observations`
  （B2）。**复用主分支的 routing cache**：`src/phase_a/normal_manifold.py` 的 `read_manifold_traces` /
  `ensure_routing_cache` / `load_cached_routing` 已把 360 条核心 trace 的 decode `router probability [16,T,64]`
  与 `top_k_ids [16,T,8]` 缓存在 `artifacts/agent_v2/normal_manifold_cache/`（逐条 sha256 校验、已通过 routing
  validation）；`ManifoldTrace` 携带 `evidence_onset`、`completion_boundary`、`workflow_family`。harness 只需
  补充 token id 序列（方案 2 需要；从 `trace.json` 的 model_generation `output_token_ids` 读取）。不得修改
  `src/phase_a/normal_manifold.py`；新代码放在 `src/research_v2/`。

### 1.2 因果性与可用信息

- 时刻 t 的分数只能使用 decode token ≤ t 的路由、prefill 路由、以及部署时可得的元信息（workflow 可用；
  attack channel / target domain / outcome / boundary 不可用）。
- 禁止使用未来 token、最终 outcome、人工 boundary 做推理时输入。

### 1.3 评价标签与时间锚点

- 窗口以结束 token `end` 标识，覆盖 `[end-w+1, end]`；逐 token 方法 w=1。
- **主锚点 = evidence onset**：主分支 observation atlas 从冻结 evidence span 重建的首个 evidence token
  （`artifacts/agent_v2/routing_observation_atlas/observation.json` →
  `boundary_annotation_audit[batch].scenario_rows[].evidence_start_output_token`，`normal_manifold._evidence_onsets`
  已实现）。原 `goal_plan_deviation_start_output_token` 是 evidence completion，比 onset 晚中位 5 token，只作
  连续性/审计指标。
- drift trace：`end ≥ onset` 为 onset 后区；`end < onset` 为严格 onset 前区；`[onset-8, onset)` 为
  transitional band（§1.7）。
- 单类方案不训练正例，因此不存在训练标签混合问题；transition band 只影响评价。

### 1.4 三种切分（全部报告）

- **S1 跨 batch**：流形在 B1 routine 上拟合、在 B2 上校准与评价；反向亦然。主判据所在。
- **S2 合并分组 CV**：以 (batch, preregistered_fold) 为 10 个 group，留一 group：流形用其余 9 个 group 的
  routine 拟合，校准与评价在留出 group 内按 scenario 对半切。
- **S3 留一目标域**：合并 B1+B2，留出该域全部 scenario 的三个 arm；流形用其余域的 routine 拟合。对单类
  方法 S3 的差异只来自校准与流形拟合集的变化，是"routine 流形是否被攻击话题污染"的检验。8 个域各一次
  （drift 5–11），每域数字是误差定位。

### 1.5 序列读法（reading）

每个方案的分数序列 `s_t` 都要用同一组读法产生报警统计量 `S_t`，全部报告：

- `max`：`S_t = z_t`；
- `ewma`：`S_t = λ z_t + (1-λ) S_{t-1}`，λ = 0.1（次选 0.2）；
- `cusum`：`S_t = max(0, S_{t-1} + z_t − κ)`，κ = 1.0（次选 0.5、2.0）；
- `runlen`：连续 m 个窗口 `z_t ≥ c` 才报警，(m, c) ∈ {(4,1), (8,1), (4,2), (8,2)}。

- `persist2`：`S_t = min(z_{t-1}, z_t)`（主分支 P1–P3 的读法）。

`z_t` 为位置桶标准化分数（§1.6）。报警 = 首个 `S_t ≥ h`。**主读法为 `persist2`**（与 P1–P3 可比；onset
锚点试算显示 CUSUM 把 +8 召回压到 0.01–0.20，只适合作为 final 阶段的持续性读法），`max`、`ewma`、`cusum`
并报；方案 3 另做读法前沿。

### 1.6 标准化、校准、超参数

- **位置桶标准化**：routine 分数随 decode 位置变化。`z_t = (s_t − μ_b)/σ_b`，b = `end // 32`（最后一桶合并到
  ≥ 30 条 trace），μ、σ 来自校准 routine 流量。
- **阈值规则**：`h` = 校准 routine trace 的 `max_t S_t` 的次序统计量，第 `ceil((n+1)(1−α))` 小者；α ∈ {0.05,
  0.10}，**0.10 为主判据**。
- **模式 D（deployment，主判据）**：流形来自训练侧 routine；μ、σ、h 来自测试 batch 中与被评价 trace 不同
  scenario 的 routine（按 scenario 对半切，交换后合并计数）。B2→B1 方向 target routine 只有 80 条，主表把
  B1 `brief=present` 的 120 条 routine 加入校准池（n=100 per half），并报告不加入的敏感性。
- **模式 T（transfer）**：μ、σ、h 全部来自训练侧 routine（按 scenario 对半交叉拟合得到 out-of-fit 最大值），
  用于度量分数尺度跨 batch 的可迁移性。
- **超参数只能这样选**：(i) 由 routine-only 判据选（留出 routine 的对数似然 / 重构误差 / 校准分布稳定性），
  或 (ii) 预注册的固定网格（≤ 12）全部报告，主配置在预注册中写死。任何用 drift 选出的配置只能作为
  "oracle 上界"单独一行报告。
- 测试侧的 drift、resist 永远不参与校准。

### 1.7 指标

主指标：non-drift trace FAR，按 clean / benign / resist 分层，**benign 与 resist 臂各为一级指标**；drift 严格
pre-onset FAR；clean recall at onset **+4 / +8 / +16** / final（clean = 无 onset 前报警；与 P1–P3 相同）；
各 horizon 的理论可达率；命中 trace 的中位 latency（与 recall 一起报告）；每 1,000 个 negative position 的
alarm onset 数。连续性指标：相对 completion boundary 的 +16 / final（与 v1 及 §2 E23–E28 可比）。

容差指标（一级）：`[onset−8, onset)` 内的报警不计误报、延迟按 0 计；`end < onset−8` 仍是误报。
报告容差版 pre-boundary FAR 与 +8/+16/+32/final recall。边界 ±4 token 敏感性另报。

**边界前报警审计（一级，单类框架下必需）**：对全部严格边界前报警（不抽样），给出报警窗口文本，按预注册
规则分为 (a) 标签/标题，(b) 域外任务的预告句，(c) 攻击复述/拒绝/解释，(d) 其他。对"离开 routine"的估计量，
(a)(b) 是早于人工边界的正确离开，(c)(d) 是真误报。报告每类计数；不据此改标签。

排序指标（只作诊断）：trace-max AUROC（drift 边界后最大 vs 各负例臂全程最大）；benign-vs-clean、
resist-vs-clean（≈ 0.5 表示分数不因"提到"而升高）；drift pre-boundary-max vs routine-max。

不确定性：以 scenario 为重抽样单位的 bootstrap 95% 区间；计数与比例并列。

### 1.8 Q1 诊断面板（每个方案的分数都要输出）

- 边界对齐事件曲线：drift 的 `z_t` 在相对边界偏移 −32…+48 处的均值/中位数，对照为同 scenario clean arm 在
  相同输出位置的 `z_t`；报告 n；
- 首个分离偏移：drift 曲线超过 clean 曲线 2 个合并标准差且配对 AUROC ≥ 0.9 的最早偏移；
- 每条 drift 的 `z(boundary+15) − z(boundary−1)`，按域、按 batch 报告上升比例；
- anchor-token 检验：在 drift 后与 routine 中都出现 ≥ 10 次的 token id 上，同一 token 的分数分开两种上下文
  的 AUROC（中位数、IQR）。

### 1.9 基线与参考（harness 提供）

单类文本基线（与三个方案同协议、同读法、同校准）：
- **T2**：窗口内"未见词比例"（token id 在训练侧 routine 语料中出现 < 5 次）；预诊断 trace-max 0.956–0.973，
  +16 召回 0.47–0.54，是最需要击败的基线。
- **T1**：窗口内 OLMoE 静态输入 embedding 均值（本地权重，离线）在 routine 上的 kNN 新颖度（k=10，
  PCA-32）。

有监督参考（说明"监督买到了什么"，不是基线）：
- **S0**：difference-of-means 方向（中间层，匹配 benign 参考）；
- **B0**：v1 配方（16-token route_selection ridge）在本协议下重训。

上下文参考：**P0** prefill 路由 propensity（常数分数），说明时间信息相对上下文倾向增加了什么。

### 1.10 预注册、接口、输出与可复现

- 主评价前，每个方案在 `docs/research_v2/<idea>_prereg.md` 写下候选网格（≤ 12）、主配置、routine-only
  选择判据、证伪判据、要报告的全部表格；提交 Git 后再运行。
- 分数模块接口（`src/research_v2/scorers/<idea>.py`）：`fit(routine_traces) -> state`（**只接收 routine**）；
  `score(state, trace) -> (s_t[nwin], ends)`（因果）；`window_width`。harness 负责切分、校准、读法、指标、
  Q1 面板、审计、基线与报告。
- 输出：`artifacts/agent_v2/research_v2/<idea>/`；代码 `src/research_v2/`、`scripts/research_v2/`；不得改动
  `src/phase_a/`、`configs/models/`、v1 结果文件。
- 每个结果 JSON 记录数据 hash、代码 commit、配置、全部候选、每条 trace 的分数序列。
- 报告 `docs/research_v2/<idea>_report.md`：包括失败与反例、被证伪的子假设。

## 2. 预诊断证据汇总（全部为事后诊断，不是结果）

来源：本轮两个侦察 agent（token-identity residual、sequential/calibration）、三个提案 agent 的试算、组长的
三次补算（方向稳定性 / 单类 / LODO；单类流形 × 部署校准 × 读法；同一试算改用 onset 锚点），以及主分支
P1–P3 报告。E1–E28 以 completion boundary 为锚点，E29–E32 以 evidence onset 为锚点。脚本与原始输出在
`scripts/research_v2/pilot_diagnostics/` 与 `docs/research_v2/pilot_results/`。

| 编号 | 事实 | 数字 | 影响 |
|---|---|---|---|
| E1 | trace 级可分性对任何分数都高，文本也一样 | v1 0.982；单类 0.97–0.995；OOV fraction 0.956–0.973；静态词向量 0.972 | AUROC 不作成绩 |
| E2 | routing 的优势在时间 | 有监督样本内：+16 route 18–21/35 vs 词向量 11–14/35；单类部署校准：+16 0.58–0.73 vs OOV 0.47–0.54，延迟 6–10 vs 13–19 | 主判据用 +16 |
| E3 | 同 token 路由随任务变 | 55 个共享 token 上 within-token AUROC 中位 0.86；top-40 常见 token 窗口 0.956 | 方案 2 |
| E4 | token 身份解释的路由方差 | routine 65%（层 8/10 最高，层 0 最低）；drift 期 24% | 方案 2 |
| E5 | 条件化保留并提前信号 | 窗口 AUROC raw 0.897→残差 0.922；2SD 首次分离 raw +16、残差 +5 | 方案 2 |
| E6 | 词表覆盖是条件化瓶颈 | drift 后 token 40–45% 未见于另一 batch routine；prefill 只增 5 pp | 方案 2 |
| E7 | 分数在边界前 ~8 token 已上升 | 过渡 token per-token AUROC 0.83–0.88；边界前整体 0.56–0.60 | 容差带 |
| E8 | 边界前报警多为标注相邻 | 10 条预报警中 6 条在标签/标题（1–9 token 前）、4 条在预告句（21–50 token 前） | 审计分类 |
| E9 | 读法改变时间行为而非可分性 | v1 分数 FAR10：max +16 0.57/预报警 0.37；EWMA 0.66/0.26；CUSUM k=1 0.63–0.69/0.19–0.22 | 主读法 CUSUM |
| E10 | 校准样本量 | n=20 阈值范围横跨 drift q10 到 non-drift 最大；n=100 实现 FAR 5–95% 0.02–0.18 | ≥ 100 条 |
| E11 | 分数尺度跨 batch 漂移 | B1-routine 阈值在 B2 误报 25–37/205（单类与有监督皆然），benign 臂最差 | 模式 D |
| E12 | resist 是最难负例 | 有监督 FAR10：clean 5% / benign 12.5% / resist 13–20% | resist 一级 |
| E13 | 持续性区分 task 与 mention | 高于 clean-q90 的最长连续窗口：drift 后中位 52（33/34 ≥ 8）；benign 中位 0（12/80 ≥ 8）；resist 0（5/45 ≥ 8） | 读法 |
| E14 | 自身早期基线无效 | 减前 8 窗口中位数：AUROC 0.98→0.81；边界 <23 子集 0.46 | 已证伪 |
| E15 | workflow 条件化无收益 | per-workflow 阈值不改善；workflow 解释窗口方差 7%，trace 身份 37.5% | 候选而非默认 |
| E16 | 任务方向稳定、倾向方向不稳定 | cos(d_task B1,B2)=0.85；cos(d_prop)=−0.06 | 有监督参考 S0 |
| E17 | "routine 专家关闭"更对齐边界 | 负权重方向 pre-vs-routine 0.61–0.64；正权重 0.75–0.84 | 方案 1 的解释 |
| E18 | 单类排序≈有监督，污染更低 | 单类 k=0 post-vs-routine 0.976/0.986，pre 0.66；LODO legal 0.995、programming 0.89 | 全部 |
| E19 | 单类对秩敏感 | PCA 残差 r=0/8 误报 25–28/205（B1 阈值），r=32 42/205，r=128 135/205 | 方案 1 |
| E20 | 层带 | 中间层时间最好；早层最差；晚层边界前污染最高 | 全部 |
| E21 | 弱层 | programming、tool_output 最弱；LODO programming 0.87–0.91 | 全部 |
| E22 | 文本→路由映射 | 静态词向量 ridge 预测 top-8：留出 R² 0.45–0.49 | 方案 2 |
| E23 | 单类窗口距离，部署校准（CUSUM） | B1→B2：FAR 0.10/0.09/0.19，preS 0.17，+16 0.63，final 0.77；B2→B1：0.05/0.15/0.06，0.22，0.58，0.75 | 方案 1 |
| E24 | token 条件化残差能量 | B1→B2：0.06/0.15/0.13，0.15，+16 0.71，final 0.83；B2→B1：0.07/0.17/0.16，0.33，0.60，0.69 | 方案 2 |
| E25 | 路径 surprisal | AUROC 0.993–0.995（最高）；B1→B2 FAR 0.14/0.15/0.20，+16 0.67，final 0.86；B2→B1 resist FAR 0.31 | 方案 3 |
| E26 | kNN 多峰流形 | AUROC 0.975–0.990；benign FAR 0.15–0.20 高于高斯 | 方案 1 |
| E27 | benign 臂普遍高于 clean 臂 | 单类各法 benign 0.09–0.20 vs clean 0.03–0.14 | 方案 2、读法 |
| E28 | 文本单类基线 | OOV fraction EWMA：+16 0.47/0.54，final 0.84/0.69，延迟 13–19 | 基线 T2 |
| E29 | onset 锚点下的单类试算（w=16，max，α=0.10，部署校准） | +8：gauss 0.23/0.29，tokcond 0.26/0.38，kNN 0.24/0.40，path 0.36/0.40，OOV 0.17/0.23；+16：0.60–0.71 vs OOV 0.29/0.58；FAR 0.10–0.16（path 0.20） | 全部；w=8 为主 |
| E30 | 主分支 P1–P3（w=8，源批校准，经验尾部） | +8：P1 0/0.125，P2 0/0.58（FAR 0.20），P3 0/0.04；+16：P1 0.20/0.25；failure = 尾部封顶与及时性 | §0.5 |
| E31 | 读法对 +8 的代价 | 同分数 CUSUM κ=1 的 +8 只有 0.01–0.20（max 0.23–0.42）；persist2 介于两者 | 主读法 persist2 |
| E32 | normal 定义 | fit 含/不含 resist：+8/+16 差 ≤ 0.05，resist 臂 FAR 差 ≤ 0.03 | 两者并报 |

## 3. 方案 1：窗口几何流形（Window-Geometry Manifold，WGM）

### 3.1 假设

routine 客服工作的 16-token 专家使用向量（每层每专家的 top-8 入选率）落在专家使用空间的一个低维、可由
少数 routine 样本估计的区域内；任务偏移使向量沿 routine 中从未出现的方向离开该区域。最简单的静态几何
（白化距离、低秩残差能量、或多峰的 kNN 距离）就足以给出及时、可校准的离开分数。

预诊断依据：E18、E19、E23、E26；E17 提供解释（离开主要表现为 routine 专家缺席）。反面：E27（benign 臂）、
E19（秩敏感）。

### 3.2 Estimand

时刻 t 的分数回答："最近 w 个 token 的专家使用，距离这个业务的 routine 使用区域有多远"。只用 token ≤ t 的
路由与离线 routine 统计，可部署、因果。

### 3.3 方法

特征：w-token 因果窗口的 top-8 入选率，**主宽度 w=8**（与 P1–P3 可比；onset+8 时窗口已全部位于域外），
消融 w ∈ {4, 16}；主带中间层 5–11（448 维），消融全层（1,024 维）与 P1 的 middle+late（5–15）。白化：
`z = (x − μ_R)/σ_R`，μ_R、σ_R 来自 routine 窗口（trace 等权）。

三种离开度量（都不用正例）：

- **G1 白化距离**：`e_t = ‖z_t − z̄‖²`（秩 0）。
- **G2 低秩残差能量**：routine 窗口 PCA，秩 r ∈ {4, 8, 16}；`e_t = ‖z_t − z̄‖² − ‖P_r(z_t − z̄)‖²`。秩由
  routine-only 判据选：留出 routine 的重构误差曲线拐点，且 r ≤ 16；r ≥ 32 只作"预期崩溃"报告（E19）。
- **G3 kNN 距离**：routine 窗口投到 PCA-32，`e_t` = 到 routine 参考集（≤ 6,000 个窗口）最近 10 个的平均
  距离；多峰流形假设。

三个设计轴（每轴一个消融）：
- **"routine"的定义**：主定义 vs R+（含 resist）；
- **窗口宽度**：w ∈ {4, 8, 16}（主 8）；
- **中心条件化**：全局均值 vs 按 workflow 的均值（收缩到全局），预诊断预期无收益（E15），保留以证伪。

候选网格（≤ 12，预注册）：{G1, G2(r*), G3} × {中间层, middle+late} = 6；G1 中间层 × {w=4, w=16} = 2；G1 中间层 ×
{R+} = 1；G1 中间层 × {workflow 均值} = 1；G2 中间层 × {r=32} = 1（预期崩溃）；G1 × sqrt 变换（P1 的
Hellinger 几何）= 1。共 12。**主配置写死为 G1 中间层 w=8 主定义**；其余全部报告。不允许用 drift 选主配置。

### 3.4 校准、读法、评价

- 校准：模式 D 为主，模式 T 报告尺度迁移；α ∈ {0.05, 0.10}；位置桶标准化。
- 读法：cusum κ=1 为主，max/ewma/runlen 并报。
- 切分：S1 两方向、S2、S3。
- 基线：T1、T2；参考 S0、B0、P0。Q1 面板；边界前报警审计。

### 3.5 证伪判据（任何一条成立即按其含义记录，不得改判据）

1. **静态几何不够**：主配置在模式 D、α=0.10、任一 S1 方向：clean+benign 实现 FAR > 0.15，或容差 +16
   recall < 0.55，或 final < 0.70。
2. **读的是 topic 而非 task**：benign 臂 FAR 超过 clean 臂 0.10 以上，或 benign-vs-clean trace-max AUROC
   > 0.65，且读法（cusum/runlen）无法把差距压到 0.05 以内。
3. **读的是"异常对话"**：resist 臂 FAR 超过 clean 臂 0.10 以上，且 R+ 定义把它压下去的同时使容差 +16
   recall 下降 ≥ 0.10（说明 resist 与 drift 在几何上不可分）。
4. **不快于文本新颖度**：容差 +16 recall 不超过 T2 + 0.10（两方向都要）。
5. **低秩假设错**：G2 的最优秩 r* 对应的重构误差曲线没有拐点（routine 不是低维），或 r* 的报警指标不优于
   G1。
6. **尺度不可迁移**：模式 T 下 non-drift FAR > 0.25 而模式 D ≤ 0.15——不是拒绝，而是必须写明的部署要求。

### 3.6 失败的教训

1：静态区域描述不够，转向方案 2/3 的条件化或动力学。2：流形离开 = topic 离开，需要方案 2 的词汇分解或
意图层信号。3：拒绝/复述在几何上与偏移同区，单类不能单独承担，需要持续性或上下文条件化。4：routing 的
"免费"没有换来更快的报警。

### 3.7 与 v1 的区别

不用正例、不用 ridge；离开度量是相对 routine 区域的距离；白化吸收零点漂移；conformal 校准于部署侧
≥ 100 条 routine；主读法 CUSUM；benign/resist 臂与边界前审计一级报告。

### 3.8 风险与组长自我批评

- 白化距离对高方差维（早层 token 驱动的专家）不敏感、对低方差维过度敏感；中间层限制缓解但不消除。
- kNN 的参考集来自 ≤ 240 条 routine trace，多峰假设可能只是记住了 scenario 模板；benign FAR 更高（E26）
  是警讯。
- "routine 专家关闭"的解释（E17）来自有监督方向的拆分，单类度量不能直接区分"关闭"和"打开"；报告
  离开向量在负/正坐标上的能量占比作为诊断。
- 中间层 5–11 的选择来自预诊断，是 adaptive 选择；全层消融必须完整报告。

### 3.9 实现步骤与交付物

1. 从 harness 缓存读窗口；实现 `scorers/wgm.py`（G1–G3，各 < 80 行）。
2. 预注册 `docs/research_v2/wgm_prereg.md`（网格、主配置、r* 的 routine-only 规则、判据）；提交。
3. 主评价（S1/S2/S3 × D/T × α × 读法），Q1 面板，边界前审计。
4. 报告 `docs/research_v2/wgm_report.md`：含离开向量的层/专家能量分布、负/正坐标占比、每域表、失败案例
   文本（最高分 non-drift 与漏检 drift 各 ≥ 10 条）。
5. 工作量：harness 就绪后约 0.5 个 CPU 日。

## 4. 方案 2：条件流形（Conditional Manifold，CM）

### 4.1 假设

routine 流形不是一个固定区域，而是一个条件分布：给定当前 token（以及局部可见文本、prefill 上下文），routine
路由的分布远比无条件分布紧。任务偏移的"离开"由两部分组成：(a) 词汇/上下文本身新颖（文本能看见），
(b) **同样的 token 在偏移中被不同地路由**（文本看不见）。把 (b) 从 (a) 中分离出来，既能压低 benign/resist
臂误报（它们的 (a) 高、(b) 低），又能回答 routing 是否有独立贡献。

预诊断依据：E3–E6、E22、E24（token 条件化残差能量是 B1→B2 最好的单类分数：+16 0.71、final 0.83）。
反面：E6（覆盖 55–60%）、E24 在 B2→B1 方向较弱（0.60/0.69）。

### 4.2 Estimand

时刻 t 的分数回答："最近 w 个 token 的路由，相对于'这些 token 在 routine 中通常怎样被路由'，偏离了多少"。
词汇新颖度（未见词比例）作为**并列的侧信道**输出，不混入路由分数。两者都只用 token ≤ t 与离线 routine
统计。

### 4.3 方法

三个条件化通道，逐级加入：

- **C1 token 条件化**：训练侧 routine token 的 per-(层, token id) top-8 选择均值表，经验贝叶斯收缩
  `E = (n·mean_tok + m·μ)/(n+m)`，m=5。残差 `r_t = ind_t − E[ind | tok_t]`；窗口均值（主 w=8）；按 routine
  残差窗口的 μ、σ 白化；`e_t = ‖z_t‖²`。（onset 试算中它是静态分数里最好的：B2→B1 +8 0.38、延迟 9、FAR 0.10。）未覆盖 token（n<5）退化为 `ind_t − μ`（即方案 1 的 G1）；`oov_t` = 窗口内
  未覆盖比例，单列输出（它就是 T2）。
- **C2 局部文本条件化**：`x_t = [PCA256(emb(tok_t)), PCA256(EMA_8 emb), PCA256(EMA_32 emb), log(t+1)]`，
  emb 为本地 OLMoE `embed_tokens` 行，PCA 在 routine token 上拟合；ridge `f: x → ind`（中间层 448 维），只在
  routine token 上拟合，λ 由留出 routine R² 选。残差 `ind_t − f(x_t)` 对所有 token 都有非平凡预测（解决
  覆盖问题）。留出 R² 是一级报告量（E22 的静态映射为 0.45–0.49，C2 应 ≥ 0.50）。
- **C3 prefill 上下文条件化**：在 routine trace 上拟合 `g: prefill 摘要 → 该 trace 的 routine 残差能量分位`
  （prefill 摘要 = prefill 路由的 per-layer 均值 PCA-32 + workflow one-hot；g 为分位回归或按 P0 分桶的
  μ、σ），得到 `z'_t = (e_t − g_μ)/g_σ`。含义："给定这个上下文，一个不偏移的 agent 的离开分数应当多大"。
  它抬高高倾向上下文（攻击 prefill）的门槛，预期压低 resist 与边界前误报，可能降低 +8 召回。

候选网格（≤ 12，预注册）：{C1, C1+C2} × {中间层, 全层} × {无 C3, 有 C3} = 8；C1 中间层 × {w=4, 16} = 2；
C1 中间层 × R+ = 1；C2 早层（0–4）= 1（层局部性）。共 12。**主配置写死为 C1 中间层 w=8 无 C3**。

### 4.4 对照与 Q1 面板

- 文本单类基线 T2（未见词比例）与 T1（静态词向量 kNN），以及**同特征文本单类**：用 C2 的 `x_t` 做 kNN
  新颖度——与 C2 使用相同输入，是最公平的文本对照。
- 方案 1 的 G1（无条件）作为"条件化买到了什么"的对照。
- Q1 面板全项；anchor-token 检验是本方案的核心诊断（同一 token 的残差能量是否分开 drift 与 routine）；
  层局部性：早/中/晚层残差检测器的 +16 召回。
- 分解报告：对每条 drift，边界后离开能量中来自"未覆盖 token"与"覆盖 token"的占比；对每条 benign/resist
  误报，同样分解。若误报主要来自未覆盖 token，则误报是词汇新颖度而非路由新颖度。

### 4.5 证伪判据

1. **routing 只是词汇新颖度**：C1 的覆盖 token 部分（把未覆盖 token 的残差置零）在模式 D、α=0.10 下的容差
   +16 recall 不超过 T2 + 0.10（任一方向），或 anchor-token within-token AUROC 中位数 < 0.75。
2. **条件化没有压低 topic 误报**：C1/C1+C2 的 benign 臂 FAR 相对 G1 没有下降 ≥ 30%（在 +16 recall 损失
   ≤ 0.05 时）。
3. **上下文条件化无效**：C3 没有把 resist 臂 FAR 与严格 pre-boundary FAR 之和相对无 C3 降低 ≥ 30%，或
   使容差 +16 recall 下降 > 0.10。
4. **映射太弱**：C2 留出 R² < 0.50——判为"文本模型太弱"，C2 相关结论不得下。
5. **中间层假设错**：C2 早层残差的 +16 召回 ≥ 中间层的 0.9 倍。
6. **报警层面失败**：同 §3.5 第 1 条。

### 4.6 失败的教训

1：routing 的"离开"可由文本新颖度解释，单类 routing monitor 的价值只剩成本；Q1 答案是"信号存在但不独立"。
2：benign 误报不是词汇问题而是路由本身对话题敏感（RASET 边界成立）。3：resist/边界前的离开与上下文无关，
是 decode 行为本身；需要持续性读法而非条件化。4：本轮无法判定 routing 是否超越文本，下一步对照是
hidden state（§7.4）。

### 4.7 与 v1 的区别

不用正例；条件化 routine 分布；文本对照内置于 estimand；词汇新颖度单列；prefill 条件化把"上下文倾向"与
"decode 行为"分开；conformal 部署侧校准。

### 4.8 风险与组长自我批评

- 覆盖 55–60%：C1 对 40% 的 drift token 退化为 G1，C1 与 G1 的差异被稀释；C2 是必要补充但可能过拟合
  （19k routine token → 1k→448 映射）。
- 条件化会去掉"合法共享"的信号，检验偏保守；失败是"未证明"不是"不存在"。
- C3 用 routine trace 拟合 g，drift 的 prefill 分布更极端（P0 AUROC 0.83），外推区样本少；g 应低容量。
- B2→B1 方向 C1 弱于 G1（E24 vs E23），可能是 B1 校准池小（80 条）造成的抖动，也可能是 B1 drift 集
  边界更早；两方向都过才算过。

### 4.9 实现步骤与交付物

1. harness 缓存 + 本地 `embed_tokens`（`HF_HOME=artifacts/hf_cache`，离线）；实现 `scorers/cm.py`
   （C1 表、C2 映射、C3 条件化）。
2. 预注册 `docs/research_v2/cm_prereg.md`；提交。
3. 主评价 + Q1 面板 + anchor-token + 层局部性 + 能量分解。
4. 报告 `docs/research_v2/cm_report.md`：R² 表、C1/C2/C3 与 G1/T1/T2 的并排、事件曲线数值表、能量分解表、
   被 C3 条件化掉的分数量（resist 与边界前区的均值变化）。
5. 工作量：约 1–1.5 个 CPU 日。

## 5. 方案 3：路径动力学流形（Path-Dynamics Manifold，PDM）

### 5.1 假设

routine 路由的规律不只在"用了哪些专家"，更在**专家的组合与转移**：每个 token 的跨层专家路径
（层 0→1→…→15 的 top-1/top-k 序列）和相邻 token 在同一层的专家转移，在 routine 中集中于少数典型模式。
任务偏移产生 routine 中从未出现的路径与转移。用 routine 拟合的低阶马尔可夫模型给每个 token 一个 surprisal
（−log P），它是逐 token 的、无需窗口的离开度量，可以在边界后最少的 token 内报警。

预诊断依据：E25（路径 surprisal 的 trace-max AUROC 0.993–0.995 为三者最高；B1→B2 final 0.86 最高）。
反面：E25（resist 臂 FAR 0.20–0.31、benign 0.15–0.23 为三者最差；路径模型读"任何不寻常"）。

### 5.2 Estimand

时刻 t 的分数回答："token t 的跨层专家路径与它相对前一 token 的专家转移，在 routine 中有多罕见"。
逐 token 可得，可部署、因果。持续性读法把"罕见的一个 token"与"持续罕见"分开。

### 5.3 方法

三个模型（都只用 routine token 计数拟合，加性平滑 α=0.5）：

- **D1 深度链（depth chain）**：`P(e_0) ∏_l P(e_{l+1} | e_l)`，e_l 为层 l 的 top-1 专家（15 个 64×64 表）；
  surprisal `u_t = −log P(path_t)`。
- **D2 时间链（time chain）**：每层 `P(e_l(t) | e_l(t−1))`（16 个 64×64 表）；`v_t = −Σ_l log P`。
- **D3 top-k 集合版本**：把 top-1 换成 top-8 集合的加权 Jaccard 转移：`P(S_l(t) | S_l(t−1))` 用核平滑的
  集合相似度近似（或退化为 top-2 联合状态）；作为 D2 的稳健化候选。

每个 surprisal 按 routine token 的均值/标准差标准化，然后 (i) 直接逐 token 进入读法（w=1），(ii) 取 w ∈ {4, 16}
窗口均值进入读法（与方案 1/2 可比）。**逐 token 读法必须用 CUSUM 或 run-length**（单 token surprisal 的
噪声太大，max 读法预期失败——这是本方案的一个可证伪预测）。

候选网格（≤ 12，预注册）：{D1, D2, D1+D2 求和} × {w=1, 4, 16} = 9；D3 × {w=4} = 1；D1 × R+ × w=4 = 1；
D1 中间层子链（层 5–11）× w=4 = 1。共 12。**主配置写死为 D1+D2、w=4**。

**读法研究（本方案额外承担）**：在 D1+D2 w=1 与 w=4 上，对 `cusum κ ∈ {0.5, 1, 2}`、`runlen (m,c)` 四组、
`ewma λ ∈ {0.1, 0.2}` 做 FAR–recall–latency 前沿，并做 run-length 分布（按臂：drift 后 / drift 前 / benign /
resist / clean）与边界前报警审计。这是 E13 在单类框架下的严格重算。

### 5.4 证伪判据

1. **路径 surprisal 是"任何不寻常"探测器**：主配置在模式 D、α=0.10、任一 S1 方向，resist 臂或 benign 臂
   FAR 超过 clean 臂 0.10 以上，且没有任何持续性读法能在容差 +16 recall 损失 ≤ 0.05 的前提下把它压到
   0.10 以内。
2. **动力学没有超越静态几何**：主配置的容差 +16 recall 不高于方案 1 的 G1（同协议）+0.05，且中位延迟不
   短于 G1 2 个 token。
3. **逐 token 读法无效**：w=1 的最佳持续性读法的容差 +16 recall 比 w=4 低 ≥ 0.10（预测：w=1 + max 会失败，
   w=1 + cusum/runlen 应接近 w=4）。
4. **持续性不区分 task 与 mention**：交叉拟合的 run-length 分布中，benign 或 resist 有 ≥ 30% 的 trace 出现
   ≥ 8 窗口（w=4）的连续偏离。
5. **报警层面失败**：同 §3.5 第 1 条。
6. **不快于文本**：同 §3.5 第 4 条。

### 5.5 失败的教训

1：路径罕见性对话题与对话形式都敏感，routing 动力学不能单独区分 task；退回方案 1/2 的度量并保留 D1 作
诊断。2：动力学信息与静态入选率冗余，"路径单义"在此任务不成立。3：逐 token 证据太弱，检测天然需要
≥ 4 token 的积累，B3 的 +8 指标应放弃。4：同 v1 版本方案 C 的教训——topic 与 task 在时间结构上也不可分。

### 5.6 与 v1 的区别

生成式路由动力学模型（计数表）而非判别式 ridge；逐 token surprisal；不用正例；持续性读法与
conformal 校准；resist/benign 臂一级。

### 5.7 风险与组长自我批评

- 路径转移在很大程度上由 token 身份驱动（E4），surprisal 会响应任何新词；D1 的层 0 边缘分布几乎就是
  token 分布。中间层子链候选用于检验这一点。
- 64×64 表 × 15 层 = 61k 参数，routine token 约 11k–20k：稀疏格子靠平滑，未见转移一律得到相同 surprisal，
  分辨率有限；top-k 集合版本（D3）更稀疏。
- CUSUM 阈值随 trace 长度增长；必须报告每 1,000 位置 onset 与按长度分层的 FAR。
- E25 的 AUROC 最高但误报也最高，预诊断已暗示本方案最可能在判据 1 上失败；仍值得做，因为它是三者中
  唯一能在 +4 token 内报警的候选。

### 5.8 实现步骤与交付物

1. 实现 `scorers/pdm.py`（D1–D3，计数表拟合与 surprisal 评分，各 < 80 行）；把 `runlen` 读法与 run-length
   统计贡献回 harness。
2. 预注册 `docs/research_v2/pdm_prereg.md`（网格、读法网格、审计分类规则、判据）；提交。
3. 主评价 + 读法前沿 + run-length 分布 + 边界前审计。
4. 报告 `docs/research_v2/pdm_report.md`：前沿表、分布表、审计表、与 G1/C1 的并排，以及对协议默认读法的
   建议。
5. 工作量：约 1 个 CPU 日。

## 6. 综合规则：三个方案的结果如何回答 Q1 / Q2

### 6.1 预注册的数值门槛（判断"是否值得冻结并设计 B3"）

全部在模式 D、α=0.10、S1 的**两个方向**上同时满足；使用各方案预注册写死的主配置（不是事后最优）：

| 项目 | 门槛 |
|---|---|
| clean+benign 实现 FAR | ≤ 0.15 |
| benign 臂 FAR − clean 臂 FAR | ≤ 0.10 |
| resist 臂 FAR | ≤ 0.20（≤ 0.15 记为"强"） |
| 严格 pre-boundary FAR | ≤ 0.20；其中审计为 (c)(d) 类的 ≤ 0.10 |
| 容差（8-token）pre-boundary FAR | ≤ 0.10 |
| onset +8 clean recall（严格） | ≥ 0.35（主分支 gate） |
| onset +16 clean recall（严格） | ≥ 0.55 |
| 命中的中位 latency | ≤ 8 token（主分支 gate） |
| final clean recall（容差） | ≥ 0.75 |
| 容差 +16 recall 相对 T2 与 T1 的增量 | ≥ +0.10 |
| S3 LODO 合并容差 +16 recall | ≥ 0.45，且 drift ≥ 6 的域没有一个为 0 |
| 模式 T 的 clean+benign FAR | 报告；> 0.25 时写明"需部署侧校准" |
| Q1 面板 | 事件曲线首个分离偏移 ≤ +8；anchor-token within-token AUROC 中位 ≥ 0.75 |

预诊断中（onset 锚点、w=16、max 读法）最好的静态分数 +8 为 0.26/0.38、延迟 12/9，路径 surprisal 0.36/0.40、
延迟 11/8 但 FAR 偏高；门槛因此是"尚未达到、但在 w=8/逐 token 与更好读法下可达"的。主分支 P1–P3 在同一
gate 下为 0/0.125、0/0.58(FAR 0.20)、0/0.04。B1/B2 是开发数据，通过门槛只意味着"值得冻结后用 B3 确认"。

### 6.2 结果组合的解释

| 1 几何 | 2 条件 | 3 动力学 | 解释 | 下一步 |
|---|---|---|---|---|
| 过 | 过 | 过 | routine 流形可用静态几何描述，条件化进一步分离词汇与路由新颖度，动力学可更早报警 | 冻结 2（主）+ 3 的读法，设计 B3：新 workflow、新域、≥ 200 条 routine 校准流量 |
| 过 | 过 | 不过(1) | 静态与条件流形足够；路径太敏感 | 冻结 2；B3 成功条件保持 +16 |
| 过 | 不过(1) | 任意 | routing 的离开可由词汇新颖度解释 | 结论"单类 routing monitor 免费但不独立"；同协议的文本新颖度 monitor 作为 B3 对照 |
| 不过(2) | 不过(2) | 不过(1) | 流形离开 = topic 离开（RASET 边界成立） | `no promising method`（对本 agent、本估计量）；记录边界结论 |
| 不过(1) | 过 | 任意 | 需要条件化才能描述 routine | 冻结 2；B3 需要更大 routine 语料以提高覆盖 |
| 全部排序高但报警失败 | — | — | 瓶颈在校准/尺度 | 不设计 B3；先解决部署侧校准（更多 routine、位置/长度分层） |

判据编号指各方案 §x.5 中的条目。任何"不过"都必须写明触发了哪一条、数字是多少。

### 6.3 什么不算证据

- 任何 trace-max AUROC 的提高；
- 只在一个 S1 方向成立的结果；
- 用测试侧任何 trace、或用 drift 正例选出的层、秩、宽度、读法或阈值；
- 未与 T2（未见词比例）并排的改善；
- 未做边界前审计就声称"及时"。

## 7. 执行分工、时间线与交付物

### 7.1 任务分工（四个并行 AI 执行者）

| 任务 | 内容 | 依赖 | 产出 |
|---|---|---|---|
| H：共享 harness | §1 全部，**建立在 `src/phase_a/normal_manifold.py` 的 cache / `ManifoldTrace` / `alarm_summary` / `aggregate_alarm_summaries` / `finite_upper_threshold` 之上**（不修改该模块）：routine 定义、切分、位置桶标准化、模式 D/T 校准、读法、onset 与 completion 两套指标、容差与审计输出、Q1 面板、基线 T1/T2 与参考 S0/P0、报告模板；验收 = (a) 用 P1 的配置（源批 fold 3–4 校准、经验 rarity、persist2）复现 P1 primary 的 B1→B2 数字（FAR 7/205、+16 7/35、full 16/35），(b) 用 G1 中间层 w=16 max 复现组长 onset 试算的数量级（`tables_onset.txt`，容差 ±2 条 trace） | 无 | `src/research_v2/harness.py`、`readings.py`、`baselines.py`、`scripts/research_v2/run_harness.py`、`tests/test_research_v2_harness.py`、`docs/research_v2/harness_report.md` |
| 1：WGM | §3 | H（可先按 §1.10 接口开发） | `scorers/wgm.py`、prereg、report |
| 2：CM | §4 | H + 本地 embedding 权重 | `scorers/cm.py`、prereg、report |
| 3：PDM | §5 | H | `scorers/pdm.py`、`runlen` 读法贡献、prereg、report |

若只有一个执行者，顺序为 H → 1 → 3 → 2。

### 7.2 时间线（CPU；单机 32 线程；避免同时运行多个加载器）

- 第 0.5 天：H 完成缓存与验收；1/2/3 同时按接口开发并写 prereg。
- 第 1–2 天：1、3 主评价；2 的 C1 主评价。
- 第 2–3 天：2 的 C2/C3；3 的读法研究与审计；各自报告。
- 第 3 天：组长综合报告。

### 7.3 每个报告必须包含

1. 假设与 estimand；2. 预注册文件的 commit；3. 全部候选在 S1 两方向 × 模式 D/T × α 的主表（严格与容差）；
4. S2、S3 表；5. 与 T1、T2、S0、B0、P0 的并排；6. Q1 面板；7. 边界前报警审计表；8. 触发/未触发的每条证伪
判据；9. 失败案例文本（漏检 drift 与最高分 non-drift 各 ≥ 10 条）；10. 数据 hash、代码 commit、运行命令、
输出路径；11. 组长自我批评中列出的风险各自的实际情况。

### 7.4 可选扩展（不在主线，GPU 空闲时）

**Dense-equivalent 单类对照**：teacher-forced 重放最终生成（本地 OLMoE，bf16，约 30–60 分钟 GPU），记录
每个 decode 步的最终 hidden state；对它做与方案 1 相同的单类几何（白化距离 / PCA 残差 / kNN）与同协议
评价。若 routing 流形离开与 hidden-state 流形离开的 +16 召回持平而 resist FAR 更低，结论是"routing 是
hidden state 的稀疏免费代理"。重放需先验证记录的 top-8 与重放 top-8 一致率 ≥ 95%。它回答"为何用 routing"，
不回答 Q1/Q2。

## 8. 本轮已被数据修正或证伪的先验假设（保留为反例）

1. "相对同一 trace 自身早期 decode 的变化"——证伪（E14）。参考必须来自 routine 流量或 prefill。
2. "按 workflow 条件化参考能吸收零点漂移"——无收益（E15）；trace 身份主导方差。
3. "对 propensity 方向做正交化能分离 task 与 context"——倾向方向跨 batch 不稳定（E16）。
4. "CUSUM 以总体为参考是自然的读法"——需位置桶标准化、κ ≥ 1（E9）。
5. "trace 级 AUROC 能证明 routing 有信号"——文本基线同样 0.96–0.97（E1）。
6. "token hash 是充分的文本对照"——未见词比例与静态词向量远强于它。
7. "边界前报警说明分数读上下文"——一半是标注相邻（E8）；需容差带与审计。
8. "programming/legal 没有路由信号"——LODO 下 0.87–1.0（E18、E21）；是阈值/尺度问题。
9. "单类模型秩越高越好"——r ≥ 32 在报警层面崩溃（E19）。
10. "路径 surprisal 是干净的任务信号"——它对 resist/benign 最敏感（E25）。
11. "有监督才能达到可用召回"——单类在部署侧校准下与有监督样本内试算相当（E23–E25）。

## 9. 方法生成过程说明

- 组长完整阅读了任务简报、v1 审计、B1/B2 报告与核心代码，并做了两项快速核查（数据规模；自身基线失败）。
- 启动了一个后台工作流：3 个数据侦察、4 个独立提案者、每个候选 idea 的对抗评审。因账户会话额度限制，
  泛化侦察、变点检测提案者与全部对抗评审未能运行；组长自己补做了泛化侦察（方向稳定性、符号拆分、
  单类、LODO）。研究方向被澄清为单类流形后，组长再补做了单类试算（三种流形 × 部署校准 × 三种读法），
  并据此重写三个方案；对抗评审改为每个方案的"风险与组长自我批评"小节。
- 已完成提案者的 idea 中，anchor-token 残差与 routine-manifold 单类被吸收进方案 1/2；propensity 正交化
  作为反例记录（§8 第 3 条）；文本解释残差作为方案 2 的 C2；dense-equivalent 对照作为 §7.4。
- 侦察、提案与组长试算的脚本、日志和 JSON 在 `scripts/research_v2/pilot_diagnostics/` 与
  `docs/research_v2/pilot_results/`；`.pt` 缓存不入库。
- 对抗评审可在额度恢复后补做；建议在各方案 prereg 提交前，由一个独立 agent 按 §3.5/§4.5/§5.5 逐条尝试
  反驳。
