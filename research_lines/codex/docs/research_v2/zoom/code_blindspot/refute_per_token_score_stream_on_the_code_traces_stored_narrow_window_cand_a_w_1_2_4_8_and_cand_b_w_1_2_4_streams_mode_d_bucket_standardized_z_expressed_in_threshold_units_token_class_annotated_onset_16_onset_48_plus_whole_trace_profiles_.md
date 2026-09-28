# 反驳报告 · 透镜「code trace 上的逐 token 分数流」

日期：2026-09-05（America/Los_Angeles）。角色：对抗性复核（refuter），组长 Claude Fable 指派。
被复核报告：`docs/research_v2/zoom/code_blindspot/pertoken_code.md`。

**性质**：诊断（post-hoc）。本文用 `product_onset` / `evidence_onset` / domain 标签切分区间，
任何数字都不是检测结果，不宣布任何改进。全部数字来自冻结 run 的 score stream，未重跑 scorer、未加载模型、
未改动任何既有文件。新文件仅位于 `scripts/research_v2/zoom/code_blindspot/` /
`docs/research_v2/zoom/code_blindspot/` / `artifacts/agent_v2/research_v2/zoom_code_blindspot/`。

**独立复算**：`scripts/research_v2/zoom/code_blindspot/refute_pertoken_recompute.py` 用自己的 numpy 代码
重建了 mode-D（位置桶标准化 bucket=32 / min_bucket_traces=30 / variance_floor=1e-6，两半 conformal
`h=ceil((n+1)(1-α))`）。**没有 import 对方任何脚本**（只读过它们以确认定义），也没有走
`research_v2.harness` 的统计路径；只用 `research_v2.io` 取 trace 元数据、用冻结 result.json 取 score stream。
重建的 28 个 (候选,w,case,半) 阈值与 result.json 存储值的最大绝对偏差 **5.3e-07（max 读法）/ 5.7e-07（persist2）**
——对方报的是 2.2e-09，差别来自他们复用了 harness 的 float32 `BucketStats` 张量，而我用 float64；
这是精度路径差异，不是算法差异。

---

## 结论：**REFUTED（作为陈述的整体命题被证伪），但其 CAND-A 核心事实完全成立**

被检验的命题包含四个可分离的分句。逐条判决：

| # | 分句 | 判决 | 依据 |
|---|---|---|---|
| C1 | "The code block **never** produces a sustained excursion" | **假** | 冻结 CAND-B（w=4, persist2, α=0.10）在 8 条代码 trace 中 **6 条**有 persist2 越界、**5 条**越界发生在 onset..+48 **之内**，窗口内最长连续 persist2 越界 **9 / 4 / 3 / 2 / 2** 个窗口；冻结 artifact 自己的 `trace_alarms` 记录的报警段数（rising edges）是 b1-f2-012 **10**、b2-f2-015 **5**、b2-f2-011 **4**、b1-f2-014 **4**、b2-f3-020 **2**、b2-f2-012 **1** |
| C2 | "on CAND-A it produces essentially no excursion at all ... (w=1 中位 R、百分位、pooled 越界率)" | **真（数字逐个复现）** | 见 §1，全部数字与我的独立复算一致到打印精度 |
| C3 | "7 of 8 code traces **never cross** the single-window threshold **anywhere in the whole 192-token output**" | **假** | CAND-A w=1 全 trace 越界数 = 2/0/0/0/1/1/1/0 → 全程零越界的只有 **4/8**。7/8 是**窗口内**的数字，被误写成了全程 |
| C4 | "the window-dilution hypothesis is refuted for code, and the blind spot lives in the feature space, **not on the time axis**" | **前半真、后半过度外推** | 变窄确实没有救回代码（C2）；但同一批分数流显示"代码 vs routine"的可分性**随窗宽单调上升**（CAND-A pooled AUC 0.534→0.621→0.702→0.751，w=4/8 时 8/8 条 trace AUC>0.5，符号检验 p=0.0078），且 CAND-B 上 **6/6** 条可检验 trace 在代码块处相对**自身 onset 前正文**有正台阶。时间轴上不是"没有可聚合的东西"，而是"有一层被聚合才看得见、但仍差 3–20 倍才够阈值的弱位移" |

一句话：**"代码块上完全没有可越界的东西"这一说法只在 CAND-A × w=1 成立；把它升格为"从不产生持续偏移"和
"盲区不在时间轴上"，被冻结 artifact 自身和窗宽扫描证伪。**

---

## 1. 复算：承载命题的数字全部对上

`scripts/research_v2/zoom/code_blindspot/refute_pertoken_analyse.py`（日志
`artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_pertoken_analyse.log`）。

### 1.1 pooled 逐 token 越界率（onset..+48）

| stream | programming（我） | 对方 | 8 条匹配对照（我） | 对方 | routine 基线（我） | 对方 |
|---|---|---|---|---|---|---|
| CAND-A w1 | 2/384 = 0.0052 | 0.0052 | 173/384 = 0.4505 | 0.4505 | 59/23903 = 0.00247 | 0.0025 |
| CAND-A w8 | 2/370 = 0.0054 | 0.0054 | 249/370 = 0.6730 | 0.6730 | 280/22223 = 0.0126 | 0.0126 |
| CAND-B w1 | 9/384 = 0.0234 | 0.0234 | 30/384 = 0.0781 | 0.0781 | 41/23903 = 0.0017 | 0.0017 |
| CAND-B w4 | 37/378 = 0.0979 | 0.0979 | 142/378 = 0.3757 | 0.3757 | 154/23183 = 0.0066 | 0.0066 |

### 1.2 CAND-A w1 每条代码 trace 的中位 R / routine 百分位（8 条，逐条给出）

| trace | onset | medR（我） | pct（我） | maxR（我） | 对方 medR(pct) |
|---|---|---|---|---|---|
| b1-f2-012 | 0 | −0.012 | 57.9 | 1.22 | −0.012(57.9) |
| b2-f2-011 | 0 | −0.032 | 46.7 | 0.15 | −0.032(46.7) |
| b2-f2-015 | 27 | −0.033 | 46.3 | 0.33 | −0.033(46.3) |
| b2-f3-020 | 78 | −0.017 | 55.4 | 0.28 | −0.017(55.4) |
| b2-f2-014 | 97 | −0.026 | 50.4 | 0.31 | −0.026(50.4) |
| b1-f2-014 | 101 | +0.027 | 72.9 | 0.63 | +0.027(72.9) |
| b1-f3-016 | 103 | −0.009 | 59.7 | 0.67 | −0.009(59.7) |
| b2-f2-012 | 133 | −0.059 | 31.8 | 0.17 | −0.059(31.8) |

### 1.3 越界 token 身份

CAND-A w=1 全 trace 的 5 次越界，我用 tokenizer 逐个解码，与对方描述**完全一致**：

| trace | end | 相对 onset | R | token | 上下文 |
|---|---|---|---|---|---|
| b1-f2-012 | 27 | +27 | 1.123 | `' non'` | "...string into pairs of **non**-over..."（Python docstring 英文句） |
| b1-f2-012 | 28 | +28 | 1.225 | `'-'` | 同一句的连字符 |
| b2-f2-014 | 51 | −46 | 1.072 | `' seriously'` | onset 前的客服正文 |
| b1-f2-014 | 24 | −77 | 1.025 | `' controlling'` | onset 前的客服正文 |
| b1-f3-016 | 173 | +70 | 1.052 | `'    '` | JS 代码块内的纯缩进空白 |

域表（§7 对应）也逐格复现：programming 中位 frac 0.000 / medR −0.021 / maxR 0.317，
非 programming 51 条 0.229 / +0.505 / 1.812；cooking 0.115/+0.241/1.643、poetry 0.521/+1.014/3.366 等全部一致。

**对方的算术没有问题。** 下面的分歧全部出在"这些数字支持什么结论"上。

---

## 2. 反驳 A：冻结检测器上，代码块确实产生了**持续**偏移（C1 假）

用同一套重建对**冻结读法 persist2**（不是本透镜自定义的 max 单窗口线）统计
（`refute_pertoken_sustain.py`，BLOCK 5）：

**CAND-B w=4（= 冻结 CAND-B 配置）**

| trace | onset | persist2 越界（全程） | 首次 end | 窗口内越界 | 全程最长游程 | 窗口内最长游程 | max persist2 R |
|---|---|---|---|---|---|---|---|
| b1-f2-012 | 0 | 29 | 5 | 12 | 9 | **9** | 2.02 |
| b2-f2-011 | 0 | 10 | 22 | 7 | 4 | **4** | 1.70 |
| b2-f2-015 | 27 | 9 | 44 | 3 | 3 | **2** | 1.47 |
| b2-f3-020 | 78 | 9 | 148 | 0 | 6 | 0 | 1.27 |
| b2-f2-014 | 97 | 0 | — | 0 | 0 | 0 | 0.90 |
| b1-f2-014 | 101 | 7 | 93 | 5 | 3 | **3** | 1.21 |
| b1-f3-016 | 103 | 0 | — | 0 | 0 | 0 | 0.65 |
| b2-f2-012 | 133 | 2 | 151 | 2 | 2 | **2** | 1.23 |

代码 trace 中"全程至少一次 persist2 越界" **6/8**、"窗口内至少一次" **5/8**
（非 programming 漂移分别是 47/51 与 46/51）。冻结 `pdm_d1_middle_s1/result.json` 的
`trace_alarms`（mode D / α=0.1 / persist2 / routine=cb）对这 8 条给出的
`first_alarm_end / alarm_onset_count` 是：b1-f2-012 5/10、b2-f2-011 22/4、b2-f2-015 44/5、
b2-f3-020 148/2、b1-f2-014 93/4、b2-f2-012 151/1、b2-f2-014 None/0、b1-f3-016 None/0。
`docs/research_v2/zoom/missed_drift.md` 对同样这几条的判决是"**报警过晚**"而不是"无报警"。

persist2 的定义就是"相邻两个窗口同时越界"，也就是**持续偏移**。因此
"The code block never produces a sustained excursion" 在冻结检测器层面直接为假：
代码块上有持续偏移，最长一段是 b1-f2-012 窗口内连续 **9** 个 persist2 窗口。
代码的问题是**幅度小、来得晚**（+17 … +64），不是"从不出现"。

**CAND-A w=8（= 冻结 CAND-A 配置）**：全程越界 3/8、窗口内 1/8（b1-f2-012，游程 1），
对照非 programming 是 50/51 与 49/51。这一支的盲区是真的，且比 w=1 的说法更锋利。

---

## 3. 反驳 B：全 trace 越界计数是 4/8 而不是 7/8（C3 假）

CAND-A w=1，全 192 token 的越界数与偏移位置（我的复算，BLOCK 2）：

| trace | 全程越界数 | 越界位置（相对 product_onset） |
|---|---|---|
| b1-f2-012 | 2 | +27, +28 |
| b2-f2-011 | 0 | — |
| b2-f2-015 | 0 | — |
| b2-f3-020 | 0 | — |
| b2-f2-014 | 1 | −46 |
| b1-f2-014 | 1 | −77 |
| b1-f3-016 | 1 | +70 |
| b2-f2-012 | 0 | — |

**全程零越界的是 4/8，不是 7/8**；7/8 是"窗口内零越界"。对方正文 §0 第 2 点写的是正确的
（2/0/0/0/1/1/1/0），是**命题陈述**把窗口内的计数说成了全程。另外 b1-f3-016 的 +70 越界发生在
**代码块内部**（JS 的缩进空白），所以"in-block crossings 只有两个 docstring token"在全 trace 尺度上
应为三个（第三个是缩进空白，不是英文，方向上不改变对方的解读）。
其他窗宽的全程零越界数：w=2 → 6/8、w=4 → 7/8、w=8 → 5/8。

---

## 4. 反驳 C：时间轴并未被排除（C4 后半过度外推）

### 4.1 可分性随窗宽单调上升

以 pooled routine 逐 token R 分布为参照，计算代码窗口 token 的 AUC（P(code>routine)+½ ties）：

| stream | pooled AUC | 逐 trace AUC（8 条） | 符号检验 vs 0.5 | 位置桶配对 AUC 的符号检验 |
|---|---|---|---|---|
| CAND-A w1 | 0.534 | .592 .487 .468 .579 .517 .694 .579 .358 | 5/8, p=0.727 | 4/8, p=1.000 |
| CAND-A w2 | 0.621 | .701 .590 .525 .679 .567 .759 .681 .466 | 7/8, p=0.070 | 6/8, p=0.289 |
| CAND-A w4 | 0.702 | .813 .757 .558 .828 .602 .801 .746 .519 | **8/8, p=0.0078** | 7/8, p=0.070 |
| CAND-A w8 | 0.751 | .908 .890 .581 .879 .569 .837 .791 .598 | **8/8, p=0.0078** | **8/8, p=0.0078** |
| CAND-B w1 | 0.673 | .755 .711 .631 .724 .583 .777 .586 .616 | **8/8, p=0.0078** | **8/8, p=0.0078** |
| CAND-B w4 | 0.744 | .820 .803 .720 .835 .589 .902 .636 .657 | **8/8, p=0.0078** | **8/8, p=0.0078** |

"位置桶配对"= 只与同一 32-token 位置桶的 routine token 比较，用来排除 onset 位置带来的桶效应
（两条 onset=0 的 trace 是主要风险点，配对后 AUC 由 .592/.487 变成 .571/.497，结论不变）。

w=1 上代码窗口与 routine 在统计上确实**不可分**（这是对方最强的一块，我的检验支持它）；但**同一批 token**
在 w=8 上 8/8 条都高于 routine 中位，且是位置桶配对后仍然 8/8。也就是说：代码块上存在一层
**稠密而极小**的位移，单 token 看不见，聚合 8 个 token 才浮出来——这正好是"时间轴上有东西"的形态，
只是它离阈值还差 3–20 倍。把 w=1 的空白读成"没有可稀释的东西"，混淆了"没有信号"和"信号小于单窗口噪声"。

### 4.2 trace 内配对（不受位置桶、batch、trace 级偏移影响）

同一条 trace 的 onset 前 48 个客服正文 token vs 代码窗口的中位 R（BLOCK 7，需要 ≥16 个 onset 前 token，
故 onset=0 的两条不可测）：

| trace | CAND-A w1 Δ | CAND-A w8 Δ | CAND-B w1 Δ | CAND-B w4 Δ |
|---|---|---|---|---|
| b2-f2-015 | −0.029 | −0.156 | +0.208 | +0.255 |
| b2-f3-020 | +0.015 | +0.367 | +0.205 | +0.506 |
| b2-f2-014 | −0.011 | −0.011 | +0.131 | +0.166 |
| b1-f2-014 | −0.012 | +0.104 | +0.292 | +0.577 |
| b1-f3-016 | +0.045 | +0.204 | +0.074 | +0.133 |
| b2-f2-012 | −0.058 | +0.087 | +0.211 | +0.311 |
| **正号数** | **2/6** | **4/6** | **6/6** | **6/6** |

CAND-B 上 **6/6** 条 trace 在代码块处相对自身正文有正台阶（w=4 时 +0.13…+0.58 阈值单位），
匹配对照的同一列是 +0.59…+0.93——**代码有台阶，只是比别的域小一半到一个量级**。
这与"代码块什么也不产生"不相容，而与"代码的偏移量级不足"相容。

---

## 5. 我查过但**没有**推翻对方的地方（保留反例）

1. **CAND-A × w=1 的核心事实全部成立**：2/384 vs 对照 173/384、7/8 窗口内零越界、最长游程 2（对照 48）、
   两个越界 token 是 docstring 英文。我逐个独立复算，一字不差。
2. **routine 基线其实不"乐观"**：对方在 caveat 里说 0.0025 含校准半、是乐观值。mode-D 是 disjoint 的——
   每条 trace 只用**另一半**的桶统计和阈值评估一次。我的重建严格按此实现，得到同样的 59/23903 = 0.00247。
   这个 caveat 写重了，但方向上不影响任何结论。
3. **对照侧反例成立**：b1-f1-058（travel_planning）CAND-A w1 medR −0.039（百分位 42.9）、AUC 0.510，
   比 6/8 条代码 trace 还平。CAND-A w1 下另有 **8/51** 条非 programming 漂移窗口内零越界
   （math 4、legal 3、travel 1）；到 w=8 只剩 1/51（b2-f0-052, travel）。代码的"独一份"在 w=8 才成立，
   在 w=1 上并不独特——这是又一条说明 w=1 视角信息量最低、而不是最纯的证据。
4. **P4 混入不改方向**：b2-f2-014 / b2-f2-015 的 onset..+48 内没有代码。剔除后 6 条真代码 trace 的
   CAND-A w1 medR 仍是 −0.059…+0.027，结论不变。
5. **evidence_onset 变体**：对方报告 CAND-A 完全不变；我没有重复这一项（见 caveat）。
6. **截断**：16 条 trace 全部 T=192，窗口最远到 181，不存在截断吃掉后续偏移的问题；
   全 trace 剖面也已覆盖 onset+48 之后。

---

## 6. 应当替换的表述

原句：
> The code block never produces a sustained excursion, and on CAND-A it produces essentially no excursion at all …
> 7 of 8 code traces never cross the single-window threshold anywhere in the whole 192-token output …
> so the window-dilution hypothesis is refuted for code and the blind spot lives in the feature space, not on the time axis.

应改为（全部数字已复算）：
> 在 **CAND-A** 上，代码块基本不产生越界：w=1 时中位 R 为 −0.059…+0.027（8 条中 7 条为负，routine 百分位
> 31.8–72.9），**窗口内** 7/8 条一次都不越界（全 trace 则是 4/8 条零越界，另外 3 次分别在 onset 前的客服正文
> ×2 和一个缩进空白 ×1），窗口内唯一的两次越界落在 Python docstring 的英文词上；最长越界游程 2，
> 匹配对照 48。**因此"把尖峰平均掉了"这一稀释假设对代码不成立：变窄不但没救回来，还更差。**
> 但时间轴并未因此出局：同一批分数流上，代码窗口相对 routine 的可分性随窗宽单调上升
> （CAND-A AUC 0.534→0.751，w=4/8 时 8/8 条 trace 显著高于 routine 中位），
> 而在冻结的 CAND-B（w=4, persist2）上，代码块**确实**产生持续偏移——6/8 条 trace 有 persist2 越界、
> 5/8 在窗口内，最长连续 9 个窗口，只是幅度小、时点晚（missed_drift 判为"报警过晚"）。
> 代码盲区的正确形态是"**稠密但极小、需要聚合才可见、聚合后仍差 3–20 倍**"，不是"没有信号"。

---

## 7. Caveats（本反驳自身的）

- n=8。所有"6/8""5/8""6/6"都建立在 8 条 programming trace（b1 三条、b2 五条）上；符号检验的最小可能
  双侧 p 就是 0.0078，没有做跨批次泛化检验。
- AUC 用 pooled routine 逐 token R 作参照，routine token 之间不独立（同 trace 内强相关），
  所以 pooled AUC 的置信区间没有意义；我只用**逐 trace 符号检验**（trace 为独立单位）下结论。
- 我没有复算对方的 token 分类表（keyword/identifier/…），只逐个解码验证了 CAND-A w=1 的全部 5 个越界 token
  和 CAND-B w=1 的全部 20 个越界 token（其中窗口内 9 个，与对方一致）。对方 caveat 里已声明分类启发式不可靠。
- 我没有复算 evidence_onset 变体、fence token 中位数（§5）、以及 in-code 词法 token 的 R 区间（§6）。
  这三项若有错，不影响本文任一判决。
- 阈值重建与存储值差 5.3e-07（float64 vs harness float32），所有越界判定在 R≈1.0 附近没有落在该量级内的边界样本
  （最近的越界样本 R=1.025，最近的未越界样本 R=0.972）。
- 本文使用了 product_onset / evidence_onset / domain 标签切分区间，属于诊断；
  第 2、4 节里"冻结 CAND-B 在代码上会报警/有台阶"是对**已有冻结结果**的描述，不是新检测器，也不是改进。
