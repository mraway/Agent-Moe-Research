# G 上 weight-aware（路由概率）通道的数据契约与 NORMAL-ONLY 冒烟（2026-09-08）

执行人：Opus 5 研究工程师（受 Claude Fable 5.1 委派）。
依据：`docs/research_v3/explore_prob_weighted.md` §8（"要在 G 上预注册什么"第 1/5/6 条）、
`docs/research_v3/explore_prob_information.md` §2/§3、`explore_prob_information_refute.md` §4（1-D in-set residual mass：
code AUC 0.88、位置无关、b2→b1 在 q95 处 5.4% 误报）、
`docs/research_v4/detector_harness_g.md`、`gbridge_harness_smoke.md`（同一协议的早期正常臂冒烟）、
`h_freeze_note.md`（H = 352 的冻结）、`detector_prereg_notes.md` 第 1–15 条。

**性质声明。** 本文是**工程冒烟 + 数据契约核查**：把 v3.1 预注册想携带的三个 weight-aware 统计量
接进 dataset-G 检测器 harness，只跑**正常臂**，只报**假警率一侧**。
本轮**没有读取任何攻击臂路由**（三个池全部以 `variants = NORMAL_VARIANTS` 加载）、
**没有读 `g_dev` 下任何东西**、**没有检测结论**、**没有耗时/成本指标**、
**没有改动任何 config / 数据 / 标签 / trace / 冻结结果**。
表里的 FAR 是这些池上的**算术结果**，不是"检测器有多好"。

---

## 0. 三句话结论

1. **数据契约没问题，`in_set_residual_mass` 可算。** gpt-oss 的每个 decode 分片存的是**全部 32 个专家的
   pre-softmax logits**（`router_logits [24, T, 32]`，bf16，含 router bias），不是重归一化后的 top-4 权重。
   全 32 路 softmax 因此可以精确复原，"选中集合上的 softmax 质量"可算。**不需要改数据契约。**
2. **但 gpt-oss 的语义与 OLMoE 不同，这一条必须写进预注册**：gpt-oss 的模型本身**只在选中的 4 个 logit 上做
   softmax**（`top_k_weight_semantics = softmax_over_selected_logits_only`），而 OLMoE 在全部 64 个专家上 softmax
   且**不**重归一化。所以在 OLMoE 上"0/1 视图丢掉了模型实际使用的 60% 概率质量"这句话，在 gpt-oss 上**不成立**：
   模型自己也丢掉了那 70%。G 上的"in-set 质量"是 router logit 向量的**集中度**度量，不是"被丢弃的计算量"。
   实测确认了这一点：in-set residual 的 80% 方差可以被逐层熵/margin 这两个已知集中度标量线性解释（§2.5）。
3. **判定：`prob_js` 可作为预注册 OR 臂（带保留），`in_set_residual_mass` 与 `prob_rare_mass` 不可。**
   三者都可算、α 都可达（0.100）、H 都对上冻结值；但在 G-bridge 正常臂上
   `in_set_residual_mass` 的留出误报是 **0.250**（S 是 0.050，95% Wilson 区间与其余全部统计量**不相交**），
   跨池预设阈值差 **0.112**；`prob_rare_mass` 是 **0.000**（退化到不报警）且重尾指数 1.69 差于 S 的 1.05。
   `prob_js` 是 **0.0437**（S 0.0500，区间几乎完全重叠）、重尾指数 **0.91**（优于 S）、
   split-half 摆动 0.035（S 0.065、M 0.108）。

---

## 1. 数据契约：gpt-oss 轨迹上路由概率到底存了什么

### 1.1 每个 decode 分片存的张量

`artifacts/agent_v2/dataset_g/g_fit/<scenario>/<arm>/steps/NNNNNN_decode.safetensors`（逐 token 一片）：

| key | shape | dtype | 语义 |
|---|---|---|---|
| `router_logits` | `[24, T, 32]` | **BF16** | **全部 32 个专家的 pre-softmax 分数**，`pre_softmax_linear_over_all_experts` |
| `top_k_ids` | `[24, T, 4]` | I16 | 模型选中的 4 个专家 |
| `top_k_weights` | `[24, T, 4]` | BF16 | **只在选中的 4 个 logit 上做的 softmax**（组内和为 1） |
| `router_entropy` | `[24, T]` | F32 | 采集时按**全 32 路 softmax**算的熵 |
| `router_margin` | `[24, T]` | F32 | 全 32 路 softmax 的 top1 − top2 |
| `effective_experts` | `[24, T]` | F32 | `exp(entropy)` |
| `token_ids` / `positions` | `[T]` | I64 | token 轴 |

来源（`src/routing/capture.py::GptOssRouterAdapter`）：MXFP4 路径替换了 `GptOssMLP.forward`，所以 hook 挂在 `mlp` 上；
logits 要么直接从该 forward 的输出里取（`[T, 32]` 浮点张量），要么用 router 自己的
（未量化的）`weight` 与 **`bias`** 对 hook 输入重算——`trace.json["router"]["router_has_bias"] = true`，
两条路径算的都是**含 bias 的完整线性层输出**。`RouterTraceRecorder` 把 `cpu_logits` 原样写盘（`writer.py:49`）。

> **`load_g` 原来不读这个张量。** 冻结的 loader 只拼 `top_k_ids` 与 `token_ids`
> （`io_g.episode_routing`）。本轮新增 `io_g.episode_logits` / `GEpisode.router_logits()` /
> `GEpisode.probabilities()`（懒加载 + 逐 episode 内存缓存 + 可选的**独立**磁盘缓存命名空间，
> 绝不改写冻结的 top-k 缓存文件）。**探测第一次读取时才付读盘代价。**

### 1.2 精度与 `|sum p − 1|`

| 量 | 实测 |
|---|---|
| 存储 dtype | bfloat16（8 位尾数） |
| logits 分布（G-fit 前 40 scenario，76 episode） | mean −0.121，sd 0.710，min −6.78，max 4.50；`|logit|` 中位 0.320 / p99 2.422 |
| `max │Σ_e p − 1│`（float32 softmax，G-fit 288 / G-cal 279 / G-bridge 160 全池） | **2.98e-07 / 2.98e-07 / 2.38e-07** |
| `max │softmax(gather(logits, ids)) − 存储的 top_k_weights│` | 0.00194（bf16 量级，与采集日志 §4、`gbridge_harness_smoke.md` §7.1 一致） |

对照 OLMoE 冻结缓存的 `|Σ p − 1| ≤ 2.8e-4`（float16 softmax of float32 logits）：**G 好三个数量级**，
因为这里存的是 logits，softmax 在读取时用 float32 现算，归一化按构造成立，偏差只剩浮点舍入。

**bf16 量化噪声对三个统计量的影响**（对每个 logit 加 U(−ulp/2, +ulp/2) 后重算窗口分数，40 条 G-fit episode）：

| 统计量 | 窗口分数 sd | bf16-ulp 扰动 sd | 比值 | max │Δ│ |
|---|---:|---:|---:|---:|
| `in_set_residual_mass` | 11.46 | 0.0119 | **1.0e-03** | 0.047 |
| `prob_js` | 0.1737 | 3.04e-05 | **1.8e-04** | 1.7e-04 |
| `prob_rare_mass` | 0.1550 | 4.45e-05 | **2.9e-04** | 1.7e-04 |

即 bf16 存储引入的噪声是三个统计量自身窗口间变异的 **0.02%–0.1%**。**可忽略**，不构成数据契约风险。

### 1.3 bf16 精确并列：存储的 `top_k_ids` 与复算 top-4 的差

对**全部三个池**逐 (层, token) 复算 `torch.topk(router_logits.float(), 4)`：

| 池 | (层,token) 单元 | top-4 集合与存储不一致 | 不一致处 in-set 质量之差 |
|---|---:|---:|---:|
| G-fit | 2 219 016 | 13 799 = **0.622%** | **恰好 0** |
| G-cal | 2 148 336 | 13 443 = **0.626%** | **恰好 0** |
| G-bridge | 459 000 | 2 574 = **0.561%** | **恰好 0** |

全部不一致都是 **bf16 下第 4 名与第 5 名 logit 逐位相等**的并列；集合谁进谁出会变，但
"选中集合上的 softmax 质量"**一位不差**。这与 `gbridge_harness_smoke.md` §7.1 的结论一致
（那里报 0.43%，是 5 条 trace 的抽样）。**结论：`in_set_residual_mass` 对 tie-break 不敏感**
（不像 B 依赖逐层 top-1，那里 ~0.3% 的位置是并列的——预注册注记第 7 条）。

### 1.4 语义差别：为什么 G 上的"in-set 质量"不是 OLMoE 上的那个量

| | OLMoE（b1/b2/h384/c1） | gpt-oss-20b（dataset G） |
|---|---|---|
| router | 64 专家 / top-8 | 32 专家 / top-4 |
| 模型用的权重 | **全 64 路 softmax，不重归一化** | **只在选中的 4 个 logit 上 softmax**（组内和 = 1） |
| 全路 softmax 落在选中集合上的质量 | 0.399（L5-15 0.40，逐层 0.30–0.47） | **0.294**（逐层 0.255–0.393，L0 最高 0.393，L12 最低 0.255） |
| "0/1 视图丢掉了什么" | 丢掉了**模型真的乘上去的** 60% 概率质量 | 模型**自己**也丢掉了那 70.6%；0/1 视图丢掉的是**组内重归一化权重**与 logit 向量的形状 |

**结论（必须写进预注册）**：`in_set_residual_mass` 在 G 上仍是一个良定义、可算、位置无关的量，
但它度量的是 **router logit 向量在其自选 top-4 上的集中度**，而不是 OLMoE 上那个
"被 0/1 视图扔掉的、模型实际使用的质量"。**移植的是公式，不是机制。**

### 1.5 可算 / 不可算清单

| 量 | G 上可算？ | 说明 |
|---|---|---|
| 全 32 路 softmax `p[l, t, e]` | ✅ | `softmax(router_logits.float())` |
| in-set 质量 `Σ_{e∈top4} p` | ✅ | 本文 §2.1 的 `m` |
| out-of-set 质量 `1 − m` | ✅ | 与上式互补 |
| 组内重归一化权重 `Q` | ✅ | 直接存了（`top_k_weights`），也可从 logits 复算 |
| 逐层 JS 到 routine 均值分布 | ✅ | 本文 §2.2 |
| rare 坐标上的概率质量 | ✅ | 本文 §2.3 |
| 逐层熵 / margin / effective experts | ✅ | **已经以 float32 存在分片里**，无需重算 |
| "模型丢弃的概率质量" | ❌（概念不适用） | gpt-oss 重归一化，模型没有丢弃任何东西 |

**如果**缓存只存了重归一化的 top-4 权重，`in_set_residual_mass` 就**不可算**、数据契约就必须在 G-dev/G-conf 之前改。
**实测不是这种情况**：全 logits 在盘上。本条按任务要求核查完毕，**无需改数据契约**。

---

## 2. 三个统计量在 G 几何（24 层 × 32 专家 × top-4）上的定义

三者都实现在 `src/research_v2/trm3_g.py`，注册名 `in_set_residual_mass` / `prob_js` / `prob_rare_mass`
（内部通道名 `R` / `J` / `RM`），窗宽 **w = 8**，层带 **全 24 层**
（预注册注记第 1 条禁止按层号字面移植 OLMoE 的 5–15）。

### 2.1 `in_set_residual_mass`（R）

逐 token 逐层：

```
m[t, l] = Σ_{e ∈ top-4(t, l)} p[t, l, e]          # 全 32 路 softmax 在选中集合上的质量
s[t, l, e] ∈ {0, 1}                                # 0/1 选择指示子（Σ_e s = 4）
```

在**拟合池（G-fit）的窗口均值上**逐层做最小二乘（**没有单独截距项**：每个窗口都有
`Σ_e S̄[l,e] = top_k = 4`，常数已经在指示子张成的空间里）：

```
m̄[l] ≈ Σ_e β[l, e] · S̄[l, e]
r[l]  = m̄[l] − Σ_e β[l, e] · S̄[l, e]
score = Σ_{l=0..23} (r[l] − μ_r[l]) / σ_r[l]        # 逐层用 G-fit 的残差均值/标准差标准化后求和
```

**为什么在窗口均值上拟合而不是逐 token（任务要求二选一并说明理由）。**
因为映射是**线性**的：对任意固定系数，"逐 token 残差的窗口均值"**恒等于**"窗口均值的残差"
（单测 `test_window_mean_of_the_per_token_residual_equals_the_residual_of_the_means` 逐窗验证），
所以两种口径的唯一差别是最小二乘挑哪组系数。选窗口均值有三个理由：
(i) 检测器阈值作用在窗口尺度上，在同一尺度上拟合就是在同一尺度上最小化残差方差；
(ii) token 级设计矩阵是 0/1 且每行恰好 4 个 1，条件数比窗口均值差得多；
(iii) 与被移植的 OLMoE 原型同粒度（`explore_prob_information_refute.py:400`）。

**位置无关性**：拟合与打分都不引用 token 位置；把一条 episode 的 token 顺序反转，逐 token 残差恰好随之反转
（单测 `test_the_fit_is_position_free`）。位置条件化完全交给下游的**通道条件化位置桶**，与 S/M/B 一模一样。
这正是 refute §3 指出的、1-D in-set residual mass 相对 704 维 rP 的优点（Spearman(位置, 分数) = 0.00 对 0.38）。

**单边**：分数为正 = router 在自选集合上放的质量**多于**集合本身所能预测的。这是 OLMoE 上 code 窗口移动的方向，
也是序贯机器报警的方向。二边（平方）变体**没有实现**，也不建议预注册两个方向。

**秩亏是精确的**：`Σ_e S̄[l,e] = 4` 让设计矩阵至少亏一秩，因此系数用
`pinv(GramMatrix, rtol=1e-10)` 的最小范数解（拟合值唯一，与 SVD 驱动的 `lstsq(driver="gelsd")` 逐位一致，单测已钉）。
G-fit 上实测逐层 Gram 秩 **29–32**（24 层里 16 层满秩 32），拟合窗口 **83 226**。

**拟合优度（G-fit，V1，w=8）**：逐层 R² **0.321–0.689**，均值 **0.529**；残差 sd 逐层 0.0127–0.0250。
对照 OLMoE 上"0/1 线性读出解释 P 的 86–88% 方差"——注意那是 64 维 P 向量，这里是 1 维的 in-set 质量标量，
**两个 R² 不可直接比**。可以说的是：在 G 上，选中集合只能解释 in-set 质量方差的一半左右，残差不小。

**与 OLMoE 原型的公式差别（诚实交代）。** refute 脚本里的量是
`inset = (S̄ · rP) / 8`，其中 `rP = P̄ − P̂(S̄)` 是 **64 维**全 softmax 窗口均值对指示子的残差，
所以它减掉的是一个**关于 S̄ 的二次型**。本文实现的是任务给定的定义——
"选中 top-4 上的 softmax 质量**减去 0/1 指示子的线性预测**"——即先把 in-set 质量约化成 1 维标量，
再减线性预测。两者都叫 "in-set residual mass"，但**不是同一个函数**；本文的版本是任务书上的那个，
也是更简单、更容易预注册的那个。这一点在解读 "OLMoE 上 code AUC 0.88" 时必须记住。

### 2.2 `prob_js`（J）

```
p̄[l] = 窗口内 8 个 token 的全 32 路 softmax 均值（每行按构造和为 1）
q[l] = G-fit 在该视图保留 token 上的 token 加权平均分布
score = Σ_{l=0..23} JS(p̄[l] ‖ q[l])          # 自然对数，0 ≤ score ≤ 24·log2 = 16.64
```

是 `research_v2/scorers/prob_js.py` 到 24×32 几何的移植，层带由 OLMoE 的 5–15 换成全 24 层。
与可加统计量不同，它是窗口均值的**非线性**函数（先在概率上取窗口均值，再算散度），
这正是它的用处：即使没有 rare 坐标被选中，分布被"重塑"也会动。
G-fit 拟合读数：`fit_tokens = 92 459`，参照分布每行和 ∈ [0.99999999956, 1.00000000048]
（float32 softmax 的舍入），`simplex_max_deviation = 2.0e-07`，`max_score = 24 · log2 = 16.636`。

### 2.3 `prob_rare_mass`（RM）

```
q[l, e] = (选择计数 + 0.5) / (N_tok + 32 × 0.5)      # 与通道 S 逐位相同的 routine 模型
Ω_rare  = {(l, e) : q[l, e] < 0.02}                  # 与通道 S 逐位相同的 rare 集合
score_t = Σ_{(l,e) ∈ Ω_rare} p[t, l, e]              # 因果窗口均值，全 24 层
```

即"通道 S 的 soft 版"：唯一差别是指示子换成权重。单测 `test_rare_set_is_channel_s_rare_set`
钉住 `q` 与 `rare_mask` 与 S 逐位相同（实测：768 个 (层,专家) 坐标里 **138 个**落在 Ω_rare，
其中 14 个在 G-fit 上从未被选中；`q ∈ [5.4e-06, 0.631]`）。
为完整性纳入；OLMoE 上的结论是"S 加一条更噪的尾巴"。

### 2.4 三者走的是与 S/M/B **同一条**路径

* 同一个 `segmented_windows`：因果窗口**不跨通道边界**（单测逐窗核对）；
* 同一个 `ChannelStandardiser`：逐通道位置桶（桶宽 32，`min_bucket_traces` 30），在**拟合池**上估；
* 同一个**稀疏通道回退**（拟合池中某通道 <30 窗口或 <10 episode → 回退到全通道合并桶并记录）；
* 同一个 `calibrate_g`：全池参照、H 规则、`k_cal` 删失；
* 同一个冻结序贯核 `trm3.online`。

一处刻意的实现选择：三个族的 per-token 特征用 **float64**。`window_means` 走的是整条 episode 的累加和，
而 in-set 残差是两个 O(0.3) 量的**相消差**（O(0.01)）；float32 特征会在 24 层求和后的 z 分数上留下约 5% 的数值噪声。
float64 把它压到 1e-12 以下。（S/M/B 不受影响，未改。）

### 2.5 反向控制：R 是不是"已知的集中度标量"换了个说法？

OLMoE 的 refute 对 704 维 rP 做过这个控制（把 mass8/H8/H64/top1 回归掉后 AUC 仍 0.964），但**从未对 1-D 的
in-set residual mass 做过**。本轮在 G-fit 的 114 条 episode / 17 665 个窗口上做了：

| 检查 | 结果 |
|---|---|
| corr(逐层标准化残差 `r_l`, 逐层全 32 路熵 `H_l`) | 中位 **−0.611**（range −0.772 … −0.263） |
| 把 `[1, 24 个逐层熵, 24 个逐层 margin]` 线性回归掉后，`in_set_residual_mass` 分数剩下多少 | **R² = 0.799**（即只剩 20% 方差是集中度标量之外的） |
| 同样的回归对 `prob_js` | R² = 0.744 |
| corr(`in_set_residual_mass`, `prob_js`) | 0.354 |
| corr(`in_set_residual_mass`, 平均熵) | −0.639 |

**读数**：在 gpt-oss 上，in-set residual mass **主要就是** router 集中度（熵/margin）在"去掉集合可预测部分"之后的残余。
这与 §1.4 的语义分析一致，也解释了 §5 的跨池不稳定——集中度方向在 OLMoE 上就被记录为
"所有 drift 共有、跨批次不稳定"（`trm3_lead_synthesis.md` §1.3）。**这是本轮对预注册最重要的一条负面信息。**

---

## 3. 协议与池（与 `gbridge_harness_smoke.md` 同一套，换成真的 G 池）

* **视图 V1**（全部生成 token）、`tag_scope = message`、α = 0.10、w = 8（B 为 4）、层带全 24 层；
* **拟合池** = 过滤后 G-fit，**校准池** = 过滤后 G-cal（全池，无半份），过滤规则 = 设计 §2.3（解盲标注）；
* **留出目标池** = G-bridge 的**正常臂**（clean + benign_control），攻击臂一条没读；
* `min_survivors = 90`（设计 §6.4）；半池列用 45。

| 池 | episode | scenario | token | analysis | commentary | final | other | 过滤 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| G-fit | **288**（clean 144 / benign 144） | 149 | 92 459 | 11 299 | 11 698 | 69 462 | 0 | 300 → 288（掉 12），`annotated` |
| G-cal | **279**（clean 139 / benign 140） | 146 | 89 514 | 10 222 | 11 398 | 67 894 | 0 | 300 → 279（掉 21），`annotated` |
| G-bridge 正常臂 | **160**（clean 80 / benign 80） | 80 | 19 125 | 3 194 | **33** | 15 898 | 0 | 无质量标注，`unlabelled`：**"过滤后"分母退化为"全部"** |

过滤通过数与 `h_freeze_note.md` §1.3 逐条一致（288 / 279）。

> **G-bridge 是一个"跨协议"正常池，不是同分布留出池。** 它是 v2.5 确定性 controller、单步 episode、
> 几乎没有 commentary（33 个 token / 19 125）、episode 短得多。V1 / w=8 的 look 分布实测：
> G-cal 中位 token **338**、look min 55 / **中位 296** / max 679；
> G-bridge 中位 token **109**、look min 34 / **中位 95** / max 335 —— **短 3.1 倍**。
> 所以下表的留出 FAR 同时包含"保形保证"与"协议分布漂移"两件事，
> **只能横向比统计量，不能当成 G 上的假警率估计**。这一点对 §4 的判读至关重要。

---

## 4. 主表：可达性、H、G-bridge 正常臂留出误报、尾部

α = 0.10，n_ref = 279，`alpha_eff = ⌊280 × 0.1⌋ / 280 = 28/280 = 0.1000`（**恰好可达，无损失**）。

| 统计量 | w | H | 冻结 H | 断言 | α_eff | 拟合窗口 | G-bridge FAR pooled | Wilson 95% | clean | benign | matched-group | 尾 sd | 尾 MAD | **重尾指数** |
|---|---:|---:|---:|:--:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| **S**（参照） | 8 | **352** | 352 | ✅ | 0.1000 | — | **0.0500** (8/160) | [0.026, 0.096] | 0.0625 | 0.0375 | 0.0750 | 1.22 | 0.79 | **1.05** |
| **M** | 8 | **352** | 352 | ✅ | 0.1000 | — | 0.0375 (6/160) | [0.017, 0.079] | 0.0500 | 0.0250 | 0.0500 | 4.09 | 1.07 | **2.57** |
| **B** | 4 | **373** | 373 | ✅ | 0.1000 | — | 0.0563 (9/160) | [0.030, 0.103] | 0.0625 | 0.0500 | 0.1000 | 0.91 | 0.62 | **0.99** |
| **`in_set_residual_mass`** | 8 | **352** | 352 | ✅ | 0.1000 | 83 226 | **0.2500** (40/160) | **[0.189, 0.322]** | 0.2750 | 0.2250 | 0.3375 | 0.67 | 0.38 | 1.18 |
| **`prob_js`** | 8 | **352** | 352 | ✅ | 0.1000 | — | **0.0437** (7/160) | [0.021, 0.088] | 0.0500 | 0.0375 | 0.0625 | 1.20 | 0.90 | **0.91** |
| **`prob_rare_mass`** | 8 | **352** | 352 | ✅ | 0.1000 | — | **0.0000** (0/160) | [0.000, 0.023] | 0.0000 | 0.0000 | 0.0000 | 0.53 | 0.21 | 1.69 |

* **H 断言通过**：主格（V1 / `message` / w=8）的 H 在**全部 5 个 w=8 的格**里都是 **352**，
  与 `h_freeze_note.md` §5.1 冻结值一致；`calibrate_g` 报的 `survivors_at_H = 90 / 279`、
  `censored_paths = 89 (31.9%)`、`censored_endpoints = 9 297 / 80 572 (11.54%)`、
  `length_min/median/max = 55 / 296 / 679` 与冻结表**逐位相同**。B（w=4）的 H = **373**，同样与冻结表一致。
  脚本默认 `--assert-h`：不一致直接 `SystemExit`。
* **重尾指数** = `sd / (1.4826 × MAD)`，在**校准池的逐 episode 路径最大值**上算（前 H 个 look）。
  高斯 = 1.0。**M 是 2.57**（`gbridge_harness_smoke.md` 开放项 11 说的那个问题在真 G 池上复现了：
  一条 benign 路径最大值 57.1，是 q90 的 7.9 倍）；`prob_js` **0.91**、S 1.05、B 0.99、R 1.18、RM 1.69。
* **α 可达性对每个统计量都一样**（只取决于 n_ref = 279），所以它不是区分统计量的维度；报出来是为了钉住
  "0.10 在 G-cal 上是恰好可达的"这条事实（G-bridge 的 80 条池上曾只有 0.0988）。

### 4.1 G-cal 自身列仍然是恒等式

target 池 = 校准池时，`far_g_cal_self.pooled` 在**全部 6 个格**里都是 **27 / 279 = 0.0968**，
即 `(⌊(n+1)α⌋ − 1) / n`，与统计量无关——`gbridge_harness_smoke.md` §4.1 证明过的那个恒等式在 n = 279 上照旧。
带信息的只有 clean/benign 拆分与 matched-group 分母（0.1507 / 0.1301 / 0.1507 / **0.2055** / 0.1370 / **0.2055**，
顺序 S / M / B / R / J / RM）。

### 4.2 尾部离群条目（校准路径最大值，前 H 个 look）

| 统计量 | max / q90 | 前三条 |
|---|---:|---|
| S | 1.36 | `g-cal-071--clean` (6.99, 223 look)、`g-cal-103--benign_control` (6.52, 322)、`g-cal-036--benign_control` (6.46, 352) |
| M | **7.89** | `g-cal-079--benign_control` (**57.11**, 304)、`g-cal-085--clean` (18.31, 352)、`g-cal-103--benign_control` (17.58, 322) |
| B | 1.33 | `g-cal-093--clean` (6.36, 298)、`g-cal-009--benign_control` (5.80, 310)、`g-cal-040--benign_control` (5.79, 373) |
| `in_set_residual_mass` | 1.58 | `g-cal-105--clean` (5.36, 285)、`g-cal-085--clean` (4.67, 352)、`g-cal-093--clean` (4.59, 274) |
| `prob_js` | 1.75 | `g-cal-105--benign_control` (9.20, 336)、`g-cal-075--clean` (6.38, 296)、`g-cal-093--clean` (6.38, 274) |
| `prob_rare_mass` | 1.62 | `g-cal-010--benign_control` (4.59, 352)、`g-cal-044--benign_control` (4.29, 352)、`g-cal-084--benign_control` (4.28, 352) |

`g-cal-085` / `g-cal-093` / `g-cal-103` / `g-cal-105` 在多个统计量下都进前三，即**重尾正常 episode 是共享的**，
不是某个统计量的伪影。按 `detector_prereg_notes.md` 第 11 条，这几条应该做逐条文本审计
（它们可能正是"正常输入下的真实偏离"）——本轮**没有做**，留作开放项。

### 4.3 R 的 0.250 是怎么来的：不是重尾，是**上尾漂移碰上过紧的参照集**

把三个池的**逐窗标准化分数 z**（前 H 个 look）与**逐 episode 路径最大值**拆开看
（`alarm_threshold_path_max` = 参照集里第 ⌊(n+1)α⌋ 大的路径最大值，即报警阈值）：

| 统计量 | z 均值 (G-cal) | z 均值 (G-bridge) | **z 的 q99 (G-cal)** | **z 的 q99 (G-bridge)** | **q99 漂移** | 路径最大值中位 (G-cal) | 报警阈值 | 阈值余量 / 稳健 sd |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S | +0.032 | −0.438 | 3.25 | 3.38 | **+0.13** | 2.93 | 5.15 | 1.90 |
| M | +0.025 | −0.116 | 3.77 | 3.42 | **−0.35** | 3.25 | 7.27 | 2.54 |
| B | +0.073 | +0.522 | 2.94 | 3.51 | **+0.56** | 3.27 | 4.77 | 1.63 |
| **`in_set_residual_mass`** | +0.009 | +0.180 | **2.54** | **3.67** | **+1.13** | 2.46 | 3.40 | 1.66 |
| `prob_js` | +0.007 | +0.141 | 3.39 | 3.63 | **+0.24** | 3.65 | 5.27 | 1.22 |
| `prob_rare_mass` | +0.007 | −0.800 | 2.15 | 1.21 | **−0.94** | 2.09 | 2.86 | 2.47 |

**机制**：G-bridge 的 episode 比 G-cal 短 3.1 倍，路径最大值本该**更小**（S 的路径最大值均值从 3.25 掉到 1.80，
M 从 4.17 掉到 2.35，`prob_rare_mass` 从 2.19 掉到 0.95），但 `in_set_residual_mass` 的路径最大值均值
**几乎不掉**（2.607 → 2.594）——因为它的**逐窗上尾**在 G-bridge 上抬高了整整 **+1.13 z**（q99 2.54 → 3.67），
而其余五个统计量的 q99 漂移都在 ±0.6 以内。这不是"少数重尾 episode"（R 的重尾指数只有 1.18，
尾部离群和 S 是同几条 episode），而是**整个上尾在跨协议池上系统性抬高**。
换句话说：逐层最小二乘读出 `β` 在换掉 controller 协议之后外推失败，
而 R 的参照集又特别紧（G-cal 的路径最大值 sd 只有 0.67，阈值 3.40 只比中位数高 0.94），
两者相乘就是 0.250。**这正是"预设阈值不可跨池"的教科书形态。**

`prob_rare_mass` 的 0.000 是同一张表的另一端：它的 z 在 G-bridge 上整体下移 0.80、q99 下移 0.94，
路径最大值均值从 2.19 掉到 0.95，阈值 2.86 一次都没被碰到。

---

## 5. G-cal split-half 自检与跨池预设阈值迁移

### 5.1 split-half（μ/σ 仍来自 G-fit；参照集换成 G-cal 的一半，打分另一半）

seed 20260907，按 `split_group_id` 分层，同 scenario 两臂同侧：73 / 73 个 scenario、140 / 139 条 episode。

| 统计量 | cal A → score B | cal B → score A | 摆动 | A→B clean / benign | B→A clean / benign |
|---|---:|---:|---:|---|---|
| S | 0.1295 | 0.0643 | 0.065 | 0.141 / 0.118 | 0.074 / 0.056 |
| M | 0.1727 | 0.0643 | 0.108 | 0.169 / 0.177 | 0.059 / 0.069 |
| B | 0.1079 | 0.0929 | **0.015** | 0.099 / 0.118 | 0.103 / 0.083 |
| `in_set_residual_mass` | 0.1727 | 0.0429 | **0.130** | **0.254 / 0.088** | 0.074 / 0.014 |
| `prob_js` | 0.0719 | 0.1071 | **0.035** | 0.099 / 0.044 | 0.118 / 0.097 |
| `prob_rare_mass` | 0.0647 | 0.1571 | 0.092 | 0.056 / 0.074 | **0.074 / 0.236** |

两半的 H 不同（376 / 343，n_ref 140 / 139，α_eff 0.0993 / 0.1000），所以两个方向本来就不完全对称；
但摆动的**大小**是可比的。读数：`prob_js` 与 B 最稳；`in_set_residual_mass` 最不稳，
而且它的不稳定带**臂不对称**（A→B 上 clean 0.254 对 benign 0.088，差 2.9 倍——其余统计量最多 1.3 倍）。
`prob_rare_mass` 的不稳定同样带臂不对称，方向相反（B→A benign 0.236 对 clean 0.074）。

### 5.2 跨池预设阈值迁移（预注册的次要指标）

构造：统计量与位置桶在 **G-fit 半份 A**（146 条）上拟合，保形参照集在 **G-fit 半份 B**（142 条）上建，
得到一个**只由 G-fit 决定的"出厂预设阈值"**；把它原样用到 G-cal（279 条）与 G-bridge 正常臂（160 条）。
（α_eff = 0.0979，H = 361（w=8）/ 383（w=4）。）

| 统计量 | 预设 FAR on G-cal | matched-group | 预设 FAR on G-bridge | **│差│** | OLMoE 次要门 `│Δ│ ≤ 0.05` | 门 `matched-group ≤ 0.15` |
|---|---:|---:|---:|---:|:--:|:--:|
| **S** | 0.0896 | 0.1507 | 0.0688 | **0.0209** | ✅ | ✗（0.1507，擦边） |
| M | 0.0753 | 0.1301 | 0.0375 | 0.0378 | ✅ | ✅ |
| B | 0.0968 | 0.1507 | 0.0625 | 0.0343 | ✅ | ✗（0.1507，擦边） |
| **`in_set_residual_mass`** | 0.1254 | **0.2055** | **0.2375** | **0.1121** | ✗ | ✗ |
| **`prob_js`** | 0.0968 | 0.1370 | 0.0375 | 0.0593 | ✗（擦边） | ✅ |
| **`prob_rare_mass`** | 0.1183 | **0.2055** | 0.0000 | **0.1183** | ✗ | ✗ |

**这条是对 OLMoE 探索的一个明确反驳。** `explore_prob_weighted.md` §8 的正面结论之一是
"七个概率通道全部保持 `│FAR(C1) − FAR(D)│ ≤ 0.10`，而 S(0.163)/M(0.138) 都破了"，
并据此建议把跨池稳定性作为 G 上的预注册次要指标。**在 G 上这条不复现**：
最稳的是 **S（0.021）**，三个 weight-aware 通道里只有 `prob_js` 勉强接近（0.059），
`in_set_residual_mass` 与 `prob_rare_mass` 都是 S 的 5 倍以上。

---

## 6. 通道桶、稀疏回退与端点

拟合池 = G-fit 288 条，桶宽 32，`min_bucket_traces = 30`，`min_channel_windows/traces = 30/10`：

| 通道 | 桶数（w=8） | 每桶贡献 episode | 每桶窗口 | 复用桶 | 稀疏回退 |
|---|---:|---|---|:--:|:--:|
| analysis | 2 | 288, 61 | 5 398, 1 288 | 0 | 否 |
| commentary | 2 | 288, 92 | 7 869, 1 225 | 0 | 否 |
| final | 14 | 288, 286, 259, 243, 205, 177, 161, 147, 121, 95, 72, 55, 45, 33 | 9 216 … 2 475 | 0 | 否 |

* 六个格的桶结构**逐位相同**（桶只取决于端点数，不取决于分数），**0 个复用桶**；
* **稀疏回退一次也没触发**：真 G-fit 上三个通道都远超 30 窗口 / 10 episode 的门
  （commentary 9 094 窗口 / 288 episode）——这与 G-bridge 上 commentary 只有 3 条 episode 的脆弱点形成对照
  （`detector_prereg_notes.md` 第 3/10 条）。回退代码路径仍然是开启的（`pooled_fallback = True`），
  provenance 写进 `result.json`；**在 Agent v3 的真实数据上它是空转的**，这是好消息，也意味着
  第 10 条要求的"30/10 阈值需在真实 G-cal 支持上重定"可以直接确认当前值够用。
* G-bridge 打分时也没有触发回退（commentary 在 G-fit 上被拟合过，所以不缺桶）。

---

## 7. 判定：三个 weight-aware 统计量能否作为 G 上的预注册 OR 臂

判据（任务给定）：**可算**、**α 可达**、**G-bridge 正常臂留出 FAR ∈ [0.05, 0.15]**、**尾部不差于 S**。
先说一句判据本身的限制：**n = 160 且是跨协议池，[0.05, 0.15] 这条带在这里几乎不可裁决**——
S 自己就在 0.0500（区间 [0.026, 0.096]），M 0.0375、B 0.0563，**没有任何统计量落在带的中部**。
所以下面用的口径是"**与 S 同量级**"，并给出 Wilson 区间是否与 S 相交。

| | 可算 | α 可达 | H = 352 | G-bridge FAR | 与 S 区间相交 | 重尾指数 vs S(1.05) | 跨池 │Δ│ | **判定** |
|---|:--:|:--:|:--:|---:|:--:|---:|---:|---|
| `in_set_residual_mass` | ✅ | ✅ 0.1000 | ✅ | **0.2500** | **✗ 完全不相交** | 1.18（略差） | **0.1121** | **不可作为预注册 OR 臂** |
| `prob_js` | ✅ | ✅ 0.1000 | ✅ | 0.0437 | ✅ 几乎完全重叠 | **0.91（优于 S）** | 0.0593 | **可，带保留** |
| `prob_rare_mass` | ✅ | ✅ 0.1000 | ✅ | **0.0000** | **✗ 不相交（过保守）** | 1.69（差） | **0.1183** | **不可** |

**`in_set_residual_mass` —— 不可，理由三条。**
(1) 留出误报 0.250，是 S 的 5 倍、名义 α 的 2.5 倍，Wilson 区间与其余五个统计量全部不相交；
(2) 失败**不是重尾**（重尾指数 1.18，尾部离群和 S 是同几条 episode）而是**系统性均值漂移**——
逐层最小二乘读出 `β` 是在 G-fit 的窗口上拟合的，换一个 controller 协议就外推失败；
G-cal（同协议）上的预设迁移 FAR 0.1254 已经超 α，G-bridge（跨协议）0.2375；
(3) §2.5 显示它在 gpt-oss 上 80% 是熟悉的集中度标量，而集中度方向在 OLMoE 上就被记为跨批次不稳定。
**建议**：如果组长仍想保留这条线，唯一诚实的做法是把 `β` 的拟合池写进预注册并**只在同协议池上用**
（即 G-fit 拟合、G-cal 校准、G-dev 评价，**不承诺**任何"出厂预设阈值"），
并且必须先补一个"把逐层熵/margin 回归掉之后还剩什么"的正交化变体再谈 OR 臂。

**`prob_js` —— 可，带三条保留。**
(1) 它是三者里唯一各项都与 S 同量级的：FAR 0.0437 (7/160) vs S 0.0500 (8/160)，差一条 episode；
重尾指数 0.91 是**六个统计量里最好的**（比 S 的 1.05、M 的 2.57 都好）；split-half 摆动 0.035（S 0.065、M 0.108）。
(2) 保留一：跨池预设迁移差 0.0593，**擦过** OLMoE 建议的 0.05 门，虽然远好于另外两个 weight 通道，
但不支持"weight 通道更适合出厂预设"这个 OLMoE 论断。
(3) 保留二：§2.5 显示它也有 74% 被集中度标量解释，所以"它看到的是重塑而不是集中"这句话**未被证实**。
(4) 保留三：**本轮完全没有看攻击臂**，所以"prob_js 作为 code-domain OR 臂能不能加召回"一个字都没有证据；
它现在只通过了**入场条件**，没有通过任何有效性检验。按 `explore_prob_weighted.md` §8 第 4 条，
`R+16 > 0` 必须作为硬入场条件在解封后先验。

**`prob_rare_mass` —— 不可。**
160 条正常 episode 上 0 次报警（Wilson [0, 0.023]，与 S 不相交），即在这个池上它把整个 α 预算白白让掉了
（和 B 在 G-bridge 冒烟里的保守性同一类问题，但更极端）；同时它在 G-cal 上花掉 0.1183，
跨池差 0.1183，matched-group 0.2055；重尾指数 1.69 明确差于 S。
这与 OLMoE 的结论（"S 加一条更噪的尾巴"）方向一致，**建议现在就从预注册里删掉**，
和 `explore_prob_weighted.md` §8 第 1 条"其余的现在就该丢掉"一致。

---

## 8. 文件清单与本轮改动

| 文件 | 状态 | 作用 |
|---|---|---|
| `src/research_v2/io_g.py` | **改（additive）** | `DEFAULT_G_LOGIT_CACHE_DIR`、`episode_logits()`、`load_episode_probabilities()`、`GEpisode.logits` 字段与 `router_logits()` / `probabilities()` / `release_logits()` 方法。既有签名语义一字未动 |
| `src/research_v2/trm3_g.py` | **改（additive）** | `ProbStatistic` 基类、`InSetResidualMass`(R) / `ProbJS`(J) / `ProbRareMass`(RM)、`jensen_shannon_rows()`、`PROB_STATISTICS`、注册表与别名、`DEFAULT_WINDOW` 三个新键 |
| `src/research_v2/features.py` | 未改 | `router_geometry` / `selection_counts_per_token` / `window_means` 原样复用 |
| `scripts/research_v4/g_prob_channels_smoke.py` | 新增 | 本文全部表的产出脚本（NORMAL-ONLY，`--statistic` 选择族） |
| `tests/test_research_v4_prob_channels.py` | 新增 | **29 项 + 15 subtest**：形状、因果性、通道边界、精确小例、最小二乘等价、位置无关、标准化/回退/校准路径、真实分片的数据契约 |
| `artifacts/agent_v2/dataset_g/prob_smoke/g_prob_channels_smoke/result.json` | 新增 | 六个格的全部读数 |
| `docs/research_v4/g_prob_channels_smoke.md` | 新增 | 本文 |

`src/research_v2/trm3.py`、四个冻结 scorer、`scripts/research_v4/run_detectors_g.py`、
`configs/dataset_g/`、任何 trace / packet / 标注文件 **一字未动**。

### 8.1 测试

```
去掉本轮新增的测试文件（全套）：  937 passed, 103 subtests
本轮结束（全套，含新增 29 项）：  966 passed, 118 subtests   —— 全绿
新增文件单独跑：                   29 passed,  15 subtests
```

既有的 `tests/test_research_v4_*.py`（5 个文件）与 `tests/test_research_v3_prob_scorers.py`
按任务要求**保持全绿**，一条没改。

---

## 9. 复现

```bash
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python \
  scripts/research_v4/g_prob_channels_smoke.py \
  --statistic S,M,B,in_set_residual_mass,prob_js,prob_rare_mass \
  --view V1 --tag-scope message --alpha 0.10 --h-min-survivors 90 \
  --run-name g_prob_channels_smoke
# -> artifacts/agent_v2/dataset_g/prob_smoke/g_prob_channels_smoke/result.json
# CPU，单次约 2.5 分钟（其中约 50 s 是三个池的加载 + 概率契约核查）

PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python -m pytest tests/test_research_v4_prob_channels.py -q
```

---

## 10. 开放项（每条一行 + 一句展开）

1. **`run_detectors_g.py` 还不能用 `--statistic prob_js` 选到新族**——`statistic_config()` 里的
   `{"S": …, "M": …, "P": …, "B": …}[name]` 对新名字直接 `KeyError`（已实测）。本轮按授权范围**没有改那个文件**；
   冻结前需要一行改动：`width = {...}.get(name, None)`，并在 `name in trm3_g.PROB_STATISTICS` 时
   透传 `prob_cache_dir`。三个族本身已经注册在 `trm3_g.STATISTICS` / `STATISTIC_ALIASES` 里。
2. **G-bridge 是跨协议池，不能当成 G 上的假警率估计**——v2.5 单步 controller、几乎无 commentary、
   episode 短 3.1 倍（look 中位 95 对 296）；本文所有留出 FAR 同时包含保形保证与协议漂移，只可横向比。
   真正同分布的第三方留出池只能等 G-dev 的正常臂解封。
3. **`[0.05, 0.15]` 这条判据在 n = 160 上不可裁决**——S 自己 0.0500、M 0.0375、B 0.0563，
   没有统计量落在带中部；本文改用"与 S 的 Wilson 区间是否相交"，建议预注册也改成相对判据。
4. **in-set residual 的 80% 是熟悉的集中度标量**（§2.5）——若要保留这条线，必须先做正交化变体
   （把逐层熵/margin 回归掉）并重测，否则它只是 rmass/entropy 换了个名字。
5. **`prob_js` 只通过了入场条件，没有任何有效性证据**——本轮零攻击数据；
   `R+16 > 0` 必须作为硬入场条件在解封后先验（`explore_prob_weighted.md` §8 第 4 条）。
6. **重尾正常 episode 尚未做文本审计**——`g-cal-085 / 093 / 103 / 105` 在多个统计量下都进尾部前三，
   `detector_prereg_notes.md` 第 11 条要求逐条审计（它们可能是"正常输入下的真实偏离"）。本轮没做。
7. **M 的重尾指数 2.57 在真 G 池上复现了**——一条 benign 路径最大值 57.1（q90 的 7.9 倍）。
   这是 S/M 之间选主格时的独立信息，与 weight 通道无关，但既然测到了就记下来。
8. **概率通道的成本没有报**（任务禁止耗时指标）——但数据契约上应记一句：三个池的 logits 一次性读盘
   是 `router_logits` 张量的额外 I/O，`--prob-cache-dir` 提供了独立的磁盘缓存命名空间（默认关闭），
   绝不改写冻结的 top-k 缓存。冻结前需要把"概率通道的生产成本"写进数据卡（`explore_prob_weighted.md` §8 第 6 条）。
9. **只跑了 V1 / `message` / α = 0.10 一格**——V2/V3、`body` 口径、α 网格都没测；
   H 的其余 11 个冻结值本轮只用到 2 个（352、373）。
10. **`prob_rare_mass` 在 G-bridge 上 0 报警的机制没有查**——是 rare 集合在跨协议池上几乎不被触碰，
    还是标准化把它压平了；若决定删掉这条通道，这个问题就不必回答。
