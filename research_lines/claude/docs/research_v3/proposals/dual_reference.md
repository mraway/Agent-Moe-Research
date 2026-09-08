# DRS：双参照序列检验（Dual-Reference Sequential test）

立场 B 的 v3 检测器提案。作者：Opus 5 算法研究员。日期：2026-09-06。
状态：**提案，未运行任何实验**。本文所有数字分两类：(a) 引自 `docs/research_v3/evidence_brief.md`
（下称"简报"）及其来源报告的既有结果；(b) 本文的**预测**，明确标注为预测，不得当作成绩引用。
本文只依赖简报 B 节的事实，不重试 C 节的任何形式，输出与校准满足 E 节契约。

---

## 1. 一页summary

**要攻击的缺口（简报 B2.4–B2.5）。** 两条线独立得到同一个结论：*相对排序早、绝对阈值晚*。
有 matched 正常对照时，onset+4/+8 的 drift-vs-matched AUROC 已达 0.888/0.855 与 0.921/0.831；
但按完整正常路径校准的**绝对**阈值下，+8 召回只有 25.0%/11.4%。L 线把延迟拆成
`延迟 ≈ 窗宽 + 阈值裕度`，并判定**裕度占主导**（信号在 onset 当下即开始上升，需 3–6 token 才越线，
persist2 再加 1）。C 线的 DRR 证明 trace 相对基线能把 16/40 执行的首次越线压进 onset+8，
控制组 FAR 10.6%（B2.5）——但它的最终判据（锁定首次越线 + 固定 64-token delta）被证伪。

**核心主张。** 阈值裕度之所以大，是因为**单次 look 的路径极大值阈值必须覆盖一条 routine trace 上
100–200 次相关的 look**。在 α=0.10 下，等效单次 look 水平约为 α/L_eff（L_eff ≈ 20–40，由
简报 B6.19 的相邻 look 相关 0.37–0.50 推得），即阈值坐在 routine 单 look 分布的
**99.5–99.75 百分位**；而 onset+4/+8 的相对证据只把 drift 抬到 routine 的约 **85–95 百分位**。
这个百分位差就是全部延迟。**解决办法不是降阈值，而是换掉多重性装置**：把"路径极大值 + 单一阈值"
换成 **anytime-valid 的 e-process**，让 2–3 次中等强度的 look 累积到与一次极端 look 等价的证据，
并且用**第二个、与绝对通道正交的参照**（trace 自身因果历史）提高每次 look 的证据量。

**方法一句话。** 每次 look 同时产生两个 routine-only 保形 p 值——绝对通道 `p^A`（窗口路由 vs
routine 流形）与相对通道 `p^R`（短块路由 vs 本 trace 自身因果历史，用 routine 白化度量）——
用 `e = κ p^{κ−1}` 校准为 e 值，取乘积，经"收缩支配"更新为有限记忆的 e-process
`E_t = shrink(E_{t−1}) · (e^A_t e^R_t)^γ`；报警阈值由 routine-only 的**路径极大保形分位**给出，
Ville 界 `log(1/α_sess)` 作为理论参照，二者之比 ρ 是被测量的"相依性罚项"。输出不是二值报警，
而是 `(校准分数 logE / 会话级 p、证据窗、归因通道、provisional/confirmed 两态)`。onset-free、
逐 token、无 warm-up。

**预测的及时性增益（§7 详算）：中位延迟 −3…−5 token，主事件 R+8 +0.08…+0.15。**
**预测的失败（§7）：代码/SQL 仍 0/8；14 条有锚点抵御中 5 条仍无信号；benign 臂仍高于 clean 臂。**

---

## 2. 组件与简报依据

每个组件都标注它由哪条简报事实推出，以及它的校准来源（`N_fit` / `N_cal` / 无需拟合）。

| # | 组件 | 统计量 | 简报依据 | 校准 |
|---|---|---|---|---|
| A1 | 绝对通道特征 | w-token 因果窗口的 top-8 入选率，层 5–15，白化（rank 0），age-free | B1.1（78% 白化能量落在 routine 最低方差十分位）、B2（CAND-A 家族两方向全门槛通过）、B6.18（路由缓存免费） | `N_fit` routine：μ、σ、centre |
| A2 | 绝对通道分数 | `sA_t = ‖whiten(x_t) − centre‖²` | B1.1、B2.4 | 无（由 A1 决定） |
| A3 | 绝对通道 p 值 | split-conformal `p^A = (1+#{ref_b ≥ sA})/(n_b+1)`，按 look 桶 b | B2.7（部署侧校准把 +8 从 0–0.13 提到 0.29–0.54；位置桶标准化必要）、B2.6（≥90/半） | `N_cal` 部署侧 routine 半份 |
| R1 | 相对通道参照 | 本 trace 自身**非重叠、带 gap** 的历史短块均值的**逐坐标中位数** `ref_t = median(hist_t)` | B2.5（DRR：减去本 trace 历史中位数后 16/40 在 onset+8 内越线）、B6.19（单 token innovation 是合理基础信息单位；与滑窗 endpoint 相关只 0.37/0.50） | 无需拟合（在线维护） |
| R2 | 相对通道分数 | `sR_t = ‖whiten(u_t) − ref_t‖²`，`u_t` 为 `w_r=4` 短块入选率；**尺度用 routine 固定单位，不用 trace 自身尺度** | B2.5；用 routine 度量避免 §3 表中 E14 的失败形式 | `N_fit` 提供 Σ（与 A1 同一白化） |
| R3 | 相对通道 p 值 | `p^R = (1+#{refR_b ≥ sR})/(n_b+1)`，refR 由 routine trace 上**同一统计量**给出 | B2.7；C 节禁止把自身早期基线当作唯一分数——此处它是被独立校准的第二通道 | `N_cal` routine 半份 |
| S1 | p→e 校准器 | `e = κ p^{κ−1}`，κ=0.5（admissible calibrator） | F5（"逐 token 读数如何满足按会话预算：e-process/anytime-valid"） | 常数，预注册写死 |
| S2 | 相依性折扣 | 指数 γ ∈ (0,1] 作用于每次 look 的 log e | B6.19、M3（相邻 8-token 窗口共享 7 个 token，不能当独立样本） | `N_fit` 侧 routine：使路径极大与 Ville 界匹配 |
| S3 | 有限记忆 e-process | `E_t = shrink(E_{t−1})·(e^A e^R)^γ`，`shrink(x)=x^δ (x≥1)`，`=x (x<1)` | C 的"无界 CUSUM 不成立"（A5/O12：合法 mention 与重复换行会被长期积分）；D6 要求显式记忆长度 | `N_fit` routine：δ 由目标半衰期 H=32 look 定 |
| S4 | 报警阈值 | `h_conf` = routine 路径极大 `logE` 的第 `⌈(n+1)(1−α_sess)⌉` 阶次序统计量 | B2.7、D7（校准对象是整条因果报警路径） | `N_cal` routine 半份（交叉互校） |
| S5 | 两态输出 | provisional `α_prov=0.20` / confirmed `α_sess=0.05` | E（输出不是单一二值报警）、B4.12（代码只有"路由 provisional + 零误报文本规则 confirm"有正面证据） | 同 S4，两个 α |
| T1 | 归因通道 | 每次报警输出三元组：支持集扩张（窗口内 unseen / rare layer-expert pair）、支持集内重排（能量在 routine 低方差坐标的占比）、联合结构（相邻层 top-1 对未见数），以及 `(log e^A, log e^R)` 的通道占比 | B1.1/B1.2/B1.3、F1（三层信号分别校准后融合，两线都指向这里）、D9 | `N_fit` 计数表；**只作归因，不进分数** |
| T2 | 代码文本确认臂 | 代码围栏 / SQL / 关键字规则（301 条 non-drift 上零误报，7/8 programming 命中，中位 +17） | B4.12、E（代码域单列并配文本确认） | 规则固定，不校准；**离线并列通道，不进在线路由分数** |
| U1 | 子分类（抵御/执行） | confirmed 后 K=48 token 内，**早中层 L3–L7 对 `sA` 贡献**高于其 routine 桶中位数的 look 占比（连续量，删失显式标注） | B1.3（持续执行时 L3/L4/L7 的 late−early 非负比例 91/86/77% vs 晚层 ~50%）；刻意**不用** novelty 回落 | `N_cal` routine 给出逐层桶中位数 |

**层带、窗宽、κ、δ 目标半衰期、α 全部在预注册中写死**：层 5–15（B2 中 WGM C2/C4 与 L 的层带结论）、
`w=8`（A 通道）、`w_r=4`（R 通道）、look 步长 `s=4`、`gap g=4`、`m_min=6` 个历史块、κ=0.5、H=32、
α_sess=0.05（主）/0.10（并报）。**不允许用 drift 选任何一个**（B2.7、C 的 routine-only 层选择禁令）。

---

## 3. 明确不重试的 C 节条目，以及本设计与之的差别

| C 节被证伪/停止的方向 | 本设计的处置 |
|---|---|
| 用持久性 / 块 / CUSUM **降 FAR** | 不做。本文的成功判据里**没有任何一条是"FAR 更低"**：总 FAR 由保形 α 钉死（L 的样本层审计 §0.3）。e-process 的目的是在**固定会话预算下换取及时性**，即把"一次 99.5 百分位的 look"换成"两次 95 百分位的 look"。预注册要求：DRS 与 CAND-A 在**同一 α_sess 下比较 recall/latency**，FAR 差 ≤0.02 视为匹配预算。 |
| 长度分层保形作主表 | 不做。长度只作**分段报告**（clean/benign/resist × 长度三分位）。position 桶 ≠ 长度分层：桶按**本会话已进行的 look 数**索引，只进入零分布/阈值（Codex D3 的明示例外"已进行的检验次数可用于 time-uniform risk boundary"），不进入分数。 |
| 层维特征（逐层 max / 层比 / 层投票） | 不做。层 5–15 跨层求和，逐层量**只出现在归因 T1 与子分类 U1**，不进入 `sA`、不参与阈值。 |
| routine-only 层选择 | 不做。层带预注册写死（两个预声明判据的挑选排名 48/55、54/55）。 |
| warm-up（忽略前 W0 token） | 不做。A 通道从第一个满窗 look 起即计分；R 通道在 `hist` 不足时**输出 `p^R=1`（`e^R=κ≤1`，只减不增）**，不丢弃 look、不推迟报警。预注册审计：前 16 token 的报警率不得因结构原因为 0。 |
| 固定专家名单 | 不做。全部 64 专家 × 11 层进入白化坐标。 |
| 形态条件化参照 / 结构化 run-length / 去结构化 routine 池 / 形态类特征（FCM） | 全部不做。代码只走 T2 两阶段文本确认（B4.12 唯一有正面证据的方向）。routine 池**保留** JSON 输出与合规拒绝（B4.13：去 JSON 使 FAR 0.10→0.29–0.35、resist→0.5）。 |
| 窄窗口换抵御召回 | 不做。窗宽网格只有 {8}（主）+{4}（一个预注册副臂），不下探 w≤2；抵御召回只报 +16 与 R_final 并附 `peak/threshold`（B3.9：14 条中 5 条在任何窗宽下无信号）。 |
| 把 novelty 回落直接等同抵御成功（DRR/LDC） | 不做。U1 用的是**早中层贡献的持续性**，不是总体 novelty 的回落方向；且它是**连续报告量 + 预注册功效声明**，v1 不冻结任何零点判据。 |
| 锁定首次越线后固定 64-token delta 判据 | 不做。主检测决策**从不等待**；U1 在可得 horizon 上计算并显式标注删失（DRR 的 17/40 删失是其失败主因之一）。 |
| absolute-age 归一化 / 局部密度比 / conditional successor forecast / staggered min-2 / q25 持久性 | 全部不做。分数中没有 age 项，没有密度比，没有后继预测，没有 min/quantile 持久性统计量。 |
| 单一 novelty 阈值继续调 bins/block/quantile | 不做。本文不改 novelty 的分箱/块/分位；改的是**多重性装置**（路径极大 → e-process）与**参照数量**（一个 → 两个）。 |

---

## 4. 零假设、有效性与位置桶

### 4.1 e-process 在什么零假设下有效

令 `F_t` 为截至 decode token t 的路由生成的 σ 域，look 时刻为 `t_1<t_2<…`（步长 s）。

> **H0（会话级）**：本会话是 routine 会话，其每次 look 的保形 p 值满足
> `P(p_{t_k} ≤ u | F_{t_{k−1}}) ≤ u`，∀u∈(0,1)（条件超均匀）。

在 H0 下 `E[e_{t_k} | F_{t_{k−1}}] = E[κ p^{κ−1}] ≤ ∫_0^1 κ u^{κ−1} du = 1`，故
`M_t = Π_k e_{t_k}` 是非负上鞅、`M_0=1`。由于 `shrink(x) ≤ x` 对所有 `x ≥ 0` 成立
（`x≥1` 时 `x^δ≤x`；`x<1` 时取恒等），**`E_t ≤ M_t` 逐路径成立**，于是 Ville 不等式给出
`P(∃t: E_t ≥ 1/α_sess) ≤ α_sess`。这正是"每会话误报预算"的定义，且不依赖停止时刻——**onset-free**。

### 4.2 routine-only 校准如何提供 H0

分两层，第二层是本设计的实质贡献：

1. **边际超均匀（可严格得到）**：split-conformal `p = (1+#{ref ≥ s})/(n+1)`，只要被评分的 look 与
   参照集在同一桶内**可交换**，就精确超均匀。部署侧、与被评价 trace **不同 scenario** 的 routine
   半份提供这种可交换性（B2.7 模式 D）。
2. **条件超均匀（近似，必须被测量）**：重叠 look 之间正相关（B6.19：相邻相关 0.37/0.50），
   朴素乘积会**反保守**。三重处置：
   (i) **抽稀**：A 通道 look 步长 `s=4`（w=8 时相邻 look 只共享 4 token），R 通道用 gap `g=4` 的
   非重叠历史块；
   (ii) **相依性折扣 γ**：在 `N_fit` 侧 routine 上选 γ 使
   `quantile_{1−α}( max_t γ·Σ log e ) = log(1/α)`——**只用 routine，不看任何正例**；
   (iii) **最终以保形路径极大为准**：运行阈值取 `N_cal` routine 的 `max_t logE` 的
   第 `⌈(n+1)(1−α_sess)⌉` 阶次序统计量 `h_conf`。**这一步无论 (ii) 是否成立都恢复了
   calibration-conditional 的会话级 FAR ≤ α_sess**（与两条线已验证的部署侧保形协议同构，D7）。

**ρ = h_conf / log(1/α_sess) 是一级报告量**：ρ=1 表示 e-process 的 anytime 界是紧的；
ρ≫1 表示相依性罚项吞掉了 anytime 解释。预注册失败判据：**ρ > 3 时，本文只能声称
"用了一个更好的统计量的保形路径极大检验"，不得声称 anytime-valid 的好处**。这是把简报 M3
（"重叠窗口不是独立样本"）从一句告诫变成一个被测量的数。

### 4.3 位置桶如何处理（契约解读，需组长追认）

- **分数 age-free**：`sA`、`sR` 不含任何 token index 项，不按 index 选邻居/中心/尺度（Codex D3、A1）。
- **零分布按 look 计数分桶**：`b = look // 32`，最多 3 桶，每桶 ≥30 个 routine 窗口且 ≥30 条 trace
  贡献（L §6.4 的修正规则；按 trace 数分桶在 n=40/半时退化成 1–2 桶）。桶索引是**已进行的检验次数**，
  即 D3 明确允许的 risk clock，而不是"绝对位置语义条件化"。E 契约的"不读绝对位置"按此解读为
  "不进入特征/分数"；本条**作为契约解读单列**，若组长判定不可，则退到 §8 风险 3 的备选。
- **e-process 本身承担了多重性**，因此桶只需让 `p` 边际均匀，不再需要让"路径极大"跨长度可比——
  这正是简报 B（误报按长度三分位 0.000/0.061/0.206）指出的、位置桶标准化无法消除的那个问题的
  结构性来源：**它是"极大值统计量"的问题，不是"分数"的问题**。
- 多轮语义（D8）：**每个 model-generation episode 重置 `E=1`**，预算按 episode 分配；
  会话级预算 = 各 episode e 值之和（`Σ E`）与 `1/α_session` 比较（e 值可加性），无需重新校准。

---

## 5. 在线算法（pseudo-code）

输入：每 token 的 16×64 top-8 expert ids（可选完整 router 概率，仅 T1 归因使用）。
**不读**文本、任务、workflow、渠道、绝对位置语义、结果、边界。

```python
# ---------------- offline, routine only ----------------
def FIT(N_fit):                       # clean + benign_control routine traces
    X   = all causal w-token top-8 selection-rate windows, layers 5..15   # age-free
    mu, sd = trace_weighted_mean_sd(X)
    whiten = lambda v: (v - mu) / sd
    centre = mean(whiten(X))
    Ur  = all causal w_r-token windows                                    # R-channel units
    return State(mu, sd, centre, count_tables_for_attribution(N_fit))

def CALIBRATE(N_cal_half, state, alpha_sess):
    # 1) per-look reference, bucketed by look count
    for tr in N_cal_half:
        for (look, sA, sR) in RUN(tr, state, accumulate=False):
            refA[bucket(look)].append(sA); refR[bucket(look)].append(sR)
    sort(refA); sort(refR)                       # >=30 windows & >=30 traces per bucket, <=3 buckets
    # 2) shaping constants, routine-only, never touching positives
    delta = solve( median half-life of logE over N_fit == 32 looks )
    gamma = solve( quantile_{1-a}( max_t gamma*sum log e ) == log(1/a) )   # Ville match, on N_fit
    # 3) operating thresholds: conformal path-max (this is what actually guarantees FAR)
    P = [ max_t logE(tr) for tr in N_cal_half ]
    h_conf = P.sorted()[ceil((len(P)+1)*(1-alpha_sess)) - 1]
    h_prov = P.sorted()[ceil((len(P)+1)*(1-0.20))       - 1]
    rho    = h_conf / log(1/alpha_sess)          # first-class diagnostic
    return Calib(refA, refR, delta, gamma, h_conf, h_prov, rho)

# ---------------- online, per decode episode ----------------
def RUN(stream, state, calib, w=8, wr=4, s=4, g=4, m_min=6, kappa=0.5):
    E, look, hist = 1.0, 0, RingBuffer(64)       # hist: non-overlapping, gapped past blocks
    provisional = confirmed = None
    for t, routing_t in enumerate(stream):       # routing_t = top-8 ids [16,8] (+probs, attribution only)
        push(routing_t)
        if t + 1 < w or (t + 1 - w) % s != 0:  continue
        look += 1;  b = bucket(look)             # risk clock ONLY (D3 exception)

        # ---- channel A : absolute routine manifold ----
        xA = whiten(selection_rate(window=[t-w+1, t], layers=5..15))
        sA = norm2(xA - state.centre)
        pA = (1 + count(calib.refA[b] >= sA)) / (len(calib.refA[b]) + 1)

        # ---- channel R : trace-relative innovation vs own causal history ----
        u  = whiten(selection_rate(window=[t-wr+1, t], layers=5..15))
        if len(hist) >= m_min:
            ref = componentwise_median(hist)     # own past only; routine metric, routine scale
            sR  = norm2(u - ref)
            pR  = (1 + count(calib.refR[b] >= sR)) / (len(calib.refR[b]) + 1)
        else:
            sR, pR = None, 1.0                   # silent: e_R = kappa <= 1, never boosts
        if (t + 1 - wr) % wr == 0:
            hist.push_delayed(u, delay=g)        # non-overlapping + gap g, keeps causality

        # ---- p -> e -> shrunken e-process ----
        eA = kappa * pA ** (kappa - 1)
        eR = kappa * pR ** (kappa - 1)
        E  = shrink(E, calib.delta) * (eA * eR) ** calib.gamma      # shrink(x)=x^d if x>=1 else x
        logE = log(E)

        # ---- outputs (never a bare binary alarm) ----
        attr = attribute(window=[t-w+1, t], state)   # (support_expansion, in_support_reweight,
                                                     #  joint_structure, channel_mix=(log eA, log eR))
        if logE >= calib.h_prov and provisional is None: provisional = t
        if logE >= calib.h_conf and confirmed   is None: confirmed   = t
        yield Reading(t, logE,
                      session_p = min(1.0, 1.0 / E),      # calibrated, anytime-valid p
                      pA = pA, pR = pR,
                      evidence_window = tokens[t-w+1 : t+1],
                      attribution = attr,
                      state = "confirmed" if confirmed else
                              "provisional" if provisional else "quiet")

def shrink(x, delta):  return x ** delta if x >= 1.0 else x   # <= x pathwise  => Ville still holds

# ---------------- parallel, offline, code arm only (E: code domain + text confirmation) ----------
#   T2 fires on code fence / SQL / programming keywords in the emitted text.
#   Rule: a `provisional` upgraded to `confirmed` the moment T2 fires; reported as a SEPARATE arm,
#   never merged into the route-only primary numbers.

# ---------------- post-hoc, descriptive only (resist vs execute sub-classification) --------------
def SUBCLASS(readings, tau, K=48):
    band = fraction of looks in (tau, tau+K] where the L3..L7 share of sA
           exceeds its routine bucket median                     # B1.3, NOT novelty fall-back
    return dict(persistence=band, censored=(len(readings) - tau < K))
```

**每次 look 的算术**：两次白化欧氏距离 + 两次有序数组二分 + 4 次幂/对数。

---

## 6. 校准计划与评价计划

### 6.1 校准（全部 routine-only，部署侧）

| 角色 | 数据 | 规模 | 用途 | 禁止 |
|---|---|---|---|---|
| `N_fit` | B1/B2 routine（clean+benign）+ C1 的 fit 侧 | ≥160 条 | μ/σ/centre、γ、δ、归因计数表 | 不含 drift；不按 target 结果扩充 |
| `N_cal` | **C1 320 条 / 160 组**，按 scenario 对半切，交叉互校 | **每半 ≥90 条 trace、≥45 scenario**（C1 可给 160/半） | refA/refR、h_prov/h_conf、逐层桶中位数 | 与 `N_fit` 不共用同一 pair；不看 stop reason |
| `N_eval` | B1/B2 全部三臂 + B2-384 replay + B3 | — | 分段 FAR 与召回 | 不得因高分事后删除 |

- **部署侧协议**：目标 batch 的 routine 按 scenario 对半，half0 为 half1 的 trace 定阈，反之亦然，
  交换后合并计数（B2.7 模式 D）。同时报模式 T（源侧阈值）作为尺度可迁移性诊断。
- **桶规则**：`b = look//32`，上限 3 桶，合并至每桶 ≥30 窗口且 ≥30 条 trace；桶数 ≤2 时 harness 告警。
- **池的组成不清洗**：保留 JSON 协议输出与合规拒绝（B4.13）；只把退化窗口（窗内不同 token id <3）
  排除在桶 σ 拟合外，且作为敏感性并报。
- **每会话预算**：α_sess=0.05 主、0.10 并报；报告 `h_conf`、`log(1/α_sess)`、ρ、定阈 trace 的身份与
  形态、以及"剔除 top-1/3/5 定阈 trace 后的 FAR"（B2.6：每半 40 条时工作点由 4 条 trace 决定）。
- **分段 FAR（全部一级）**：clean / benign / resist / 长度三分位 / workflow / 两个校准半（带符号差，
  要求 ≤0.10）/ 每 1,000 个 negative position 的报警 onset 数 / scenario 级 FAR。
- **B2-384 replay** 用于：在 384-token horizon 上重算路径极大分布与 ρ（192 截断会低估长路径风险，
  B5.16），并检查 e-process 的半衰期设定在长输出上不产生累积（A5）。

### 6.2 预注册的**单格**与多重比较

家族 α=0.05，Holm 校正，m=4。所有其他数字为描述性，不做推断。

| 格 | 配置（写死） | 端点 | H0 | 判据 |
|---|---|---|---|---|
| **P1（主）** | DRS 双通道；A: w=8/L5–15/s=4；R: w_r=4/g=4/m_min=6；κ=0.5；α_sess=0.05；模式 D；锚点 **product_onset**；目标 B3 | **主事件 R+8**，主事件正例 = drift ∪ 有锚点抵御 | recall ≤ 0.35 | 单样本二项，单侧 |
| **P2（立场 B 的决定性检验）** | 同 P1，但只用 A 通道（`e^R≡1`） | R+8 的**配对**差 DRS − A-only | 差 ≤ 0 | McNemar，单侧 |
| **P3（代码域）** | 同 P1，限 programming | 纯路由 **R+16**；并列报告"路由 provisional + T2 文本 confirm"的中位延迟 | recall ≤ 0.05 | 精确二项 |
| **P4（子分类）** | 同 P1，confirmed 后 K=48 | U1 持续性对 execute-vs-resist 的 AUROC | AUROC ≤ 0.5 | Hanley–McNeil，双侧 |

**副臂（各一格，不进家族，纯描述）**：A 通道 `w=4`；α_sess=0.10；R 通道用 trace 自身尺度归一
（E14 敏感性）；无位置桶的 age-free 池化参照（契约最严格版本）。

**其他一级评价口径（E 节）**：抵御/执行**子分类单列**；product_onset 主锚点、announcement_onset
并列；"不可逆点前检出率" = confirmed 早于 `completion_boundary` 的比例；**operating point 上的
LR+ = recall/FAR 与 LR− = (1−recall)/(1−FAR)**；命中样本的延迟分布**必须与其 recall 并列**；
按域/攻击渠道/workflow 分层只作失效诊断；不确定性以 scenario 为重抽样单位 bootstrap。

### 6.3 预注册的失败判据（写在运行前，不得改判）

1. **FAR 失控**：α_sess=0.05 下 clean+benign+resist 合并 FAR > 0.15，或两个校准半差 > 0.10。
2. **P1 不显著**：双参照在主事件上没有可用的及时召回 → 立场 B 失败。
3. **P2 ≤ 0**：相对通道无增量 → **立场 B 的核心主张被证伪**，退回 CAND-A 单通道。
4. **及时性无增益**：中位延迟未比同预算 CAND-A 少 ≥2 token。
5. **anytime 解释落空**：ρ > 3。
6. **resist 臂**：FAR > 0.25（B3.11/B2 的 resist 上界）。
7. **benign − clean > 0.10 且无法压低**：**不单独判失败**（B3.11 预期如此），但必须写入适用范围声明。
8. **P4 的 AUROC 95% CI 覆盖 0.5**：子分类记为"未解决"，不得进入任何决策规则。

### 6.4 B3 数据要求与功效

| 要求 | 数值 | 依据 |
|---|---|---|
| routine 校准池 | 每半 ≥90 条、≥45 scenario；整池 ≥180–200（C1 可满足） | B2.6；FAR sd 在 n=40 时 0.026–0.030 |
| 代码 drift | **每方向 ≥20 条**，且区分"字面代码"与"关于代码的散文"（B5.17：8 条 programming 里 2–3 条是散文，有效 n=5–6） | B4.12/B4.14 |
| 有锚点抵御 | **≥40 条**（当前 14 条，5 条无信号） | B3.9、B2.6 |
| routine 是否含结构化输出 | **显式设计变量**（含 JSON / 不含 JSON 两层） | B4.13 |
| horizon | **384 token**，stop reason 作删失信息 | B5.16 |
| 锚点 | 双锚点（product_onset 主、topic_entry/announcement 并列），`decode_truncated`、`topic_word_leak` 为固定字段；产出交付物的工具调用归 drift | B5.15/B5.17 |
| 长度分层 | 每个 decode 长度三分位 ≥30 条 routine | L §1.3 要求 13 |

**功效**（Holm 最坏情形 α=0.0125）：
- P1：主事件正例 n≈80（drift 60 + 有锚点抵御 40 的加权，取 80），H0 0.35，真值 0.50 →
  临界 p̂≈0.469，**power ≈ 0.71**（α=0.05 时 0.87）。若正例 n=100，power ≈ 0.79。
- P2：配对 McNemar，预期不一致对 ~20，真实优势比 3:1 → power ≈ 0.72。
- P3：n=20 代码 drift，临界 X≥5，真值 recall 0.30 → **power ≈ 0.76**；真值 0.05（预测值）→
  I 类错误 0.26%。即：**这一格设计上就是"预期失败"的诚实记录格**。
- P4：40 resist vs 80 execute，H0 AUROC 0.5，真值 0.75 → **power ≈ 0.99**；真值 0.65 → ≈0.62。
- FAR：160 条 routine 上观测 0.10 → Wilson 上界 0.156；要把上界压到 0.15 以下需 ≥200 条。

---

## 7. 在已有数据上的预测，与"抓不到"清单

### 7.1 及时性增益的算术（窗宽 + 阈值裕度分解）

- **窗宽项**：A 通道 w=8 不变（B3.9 已证窄窗口不换来抵御召回，B 的 w=4 只值 2–3 token 且抬 arm FAR）。
  R 通道 `w_r=4` 使复合的有效宽度介于 4 与 8 之间。**预计节省 1–2 token。**
- **裕度项（主要来源）**：路径极大阈值 ≈ routine 单 look 的 99.5–99.75 百分位（L_eff≈20–40）；
  onset+4/+8 的相对证据只把 drift 抬到约 85–95 百分位。
  e-process 下，`n_b=90` 时最小 p = 1/91，单通道单 look 最大 `e = 0.5·91^{0.5} ≈ 4.77`。
  取 `p^A=p^R=0.05`（95 百分位）：`log(e^A e^R) = 2·log(0.5·0.05^{-0.5}) = 2·0.805 = 1.61`；
  `log(1/0.05)=3.00` → **约 1.9 次 look**。步长 s=4 ⇒ **约 4–8 token 的累积成本**，
  取代 3–6 token 的裕度成本 **加上** 从 95 到 99.5 百分位所需的额外域外内容。
  L 的审计给出：分数在 onset 当下开始上升、3–6 token 后越线，说明"到 95 百分位"比"到 99.5 百分位"
  早约 3–5 token。**净预计节省 3–5 token（中位）。**
- **第二参照的贡献**：B2.4 的 matched AUROC 0.83–0.92 说明在 onset+4/+8，drift 相对**同任务正常对照**
  已明显更高，但相对**全体 routine 路径**尚未极端。R 通道把"同任务对照"换成"本 trace 自身历史"——
  这正是部署时唯一可得的 matched 对照（B2.5 已经在 DRR 上验证其存在性：16/40 在 onset+8 内越线）。
  因此 `p^R` 在 `p^A` 仍是 0.10–0.20 时就可能到 0.02–0.05，使乘积提前跨线。

### 7.2 预测数字（**预测，非结果**）

基线取简报与 L 报告的既有数：CAND-A（WGM C2）模式 D α=0.10 persist2，evidence onset 下
FAR 0.098/0.115、+8 0.429/0.500、+16 0.600/0.625、延迟 8/8；改用 product_onset 后 +8 降 0.13–0.17、
延迟 +2–4；主事件（73 条）口径 R+16 35/73=0.48、R_final 55/73=0.75。

| 指标（B1↔B2，模式 D，α_sess=0.05，product_onset） | CAND-A（既有/换算） | **DRS 预测** |
|---|---|---|
| 主事件 R+8（drift ∪ 有锚点抵御） | ≈0.22–0.30 | **0.35–0.45** |
| 主事件 R+16 | 0.48 | **0.55–0.65** |
| 主事件 R_final | 0.75 | **0.75–0.82**（几乎无增益：final 已由绝对通道饱和） |
| 中位延迟（命中样本） | 10–12 | **5–8** |
| FAR clean / benign / resist | 0.05 / 0.11–0.14 / 0.13–0.25 | **0.04–0.08 / 0.11–0.16 / 0.13–0.22** |
| LR+ @ 操作点 | ≈2.5–3 | **4–6** |
| 不可逆点（completion_boundary）前检出 | ≈0.60 | **0.68–0.75** |
| ρ = h_conf / log(1/α) | — | **1.5–2.5**（预测；>3 即判失败） |
| programming R+16（纯路由） | 0/8 | **0/8 – 1/8** |
| programming（+T2 文本确认） | 7/8 @ 中位 +17 | **7/8 @ 中位 +14…+17** |
| 有锚点抵御 R+16（14 条） | 2/14 | **3/14 – 5/14**（天花板 9/14） |
| B2-384 上的执行任意越线 recall / 控制 FAR | DRR 27/40 @ 10.6% | **28–33/40 @ 8–12%**（**但不做 sustained 二分**） |
| 子分类 U1 AUROC（B2-384，n_bounded=5） | LDC oracle 0.92 / 自选 onset 失效 | **0.6–0.8，CI 极宽，不可结论** |

### 7.3 明确"抓不到"的清单（诚实边界）

1. **代码 / SQL 的支持集内重排**（B1.2、B4.12/14）。R 通道量的是"相对自身历史的位移"，
   而代码的位移方向恰好沿 routine 自己的散文↔JSON 模态轴（cos 0.78），trace 自身历史里
   本来就有 JSON 块 → `p^R` 不会小。**预测纯路由仍 0/8–1/8。** 这不是数据量问题，B3 加代码样本不改变它。
2. **14 条有锚点抵御中的 5 条**（B3.9：任何窗宽下峰值 ≤0.72× 阈值，其中一条 35-token 的明确题外引用
   峰值只有阈值的 0.00–0.08）。双参照不会凭空造出信号。
3. **`product_onset < w` 的 trace**：结构性不可及时命中（FCM 附注），B3 指标须单列。
4. **静默抵御（B2-384 的 35/80）**：本设计**正确地**不报警（简报 B3.8：检测器响应生成进入话题，
   不是上下文里的攻击文本）——这是估计量性质，不是漏检。
5. **benign 臂的"提到即报警"**（B3.11、B2 的 RASET 边界）。R 通道**可能加重**它：正常回复里
   合法提及域外话题同样是"相对自身历史的位移"。预测 benign FAR 与 CAND-A 同量级或略高。
   本设计不宣称解决 topic-vs-intent 边界。
6. **渐进漂移的一部分**：47% 的漏检是分数在 +17…+35 才越线。e-process 只能把"接近阈值"的那部分
   提前，**预计找回约 1/3**（+0.08–0.12 召回），对分数根本没上升的那部分无效。
7. **抵御/执行的可靠二分**：U1 只作连续报告量；在 B2-384 的 n=5 上必然不可结论（B3.10 的 12 条门槛）。

---

## 8. 计算成本与风险

**计算（依据 B6.18 与 L 的效率审计：CAND-A 2.2 µs/token、状态 8 KiB；uint8/CSR 无损）**：

| 项 | 成本 |
|---|---|
| 推理侧路由采集 | 0（已在 forward 中） |
| A 通道打分（步长 s=4，故每 4 token 一次） | ≈0.6 µs/token 摊薄 |
| R 通道打分（w_r=4，逐块） | ≈0.8 µs/token |
| 两次保形二分查找（≤3 桶 × ≤2000 元素） | ≈0.3 µs/token |
| e-process 更新（4 次 pow/log） | ≈0.1 µs/token |
| **在线合计** | **≈2 µs/token**（与 CAND-A 同量级；Python 流式解释器开销 24–50 µs 是实现成本，不是算术成本） |
| 在线状态 | μ/σ/centre 8 KiB（uint8 量化 2 KiB）+ hist 环形缓冲 64×704 float16 ≈ 88 KiB（或按 PCA-32 压到 4 KiB）+ refA/refR 各 ≤3 桶×2000 float32 ≈ 48 KiB → **≈60–150 KiB** |
| 离线：FIT + CALIBRATE（480–680 条 trace，全部窗口打分两遍） | **< 30 分钟 CPU**（32 线程） |
| 全评价矩阵（S1 两方向 × 模式 D/T × 两 α × 4 个预注册格 + 副臂 + bootstrap） | **≈0.5–1 CPU 日**，与 L 的 harness 同量级 |
| GPU | **0**（复用 `artifacts/agent_v2/normal_manifold_cache/` 与 replay cache，不重放模型） |

**风险与自我批评**：

1. **R 通道可能是 E14 的换皮。** L 已证伪"减去自身前 8 窗口中位数"（AUROC 0.98→0.81，边界<23 子集 0.46）。
   我的差别有三：(a) 是**向量位移**而非标量水平相减；(b) 参照是**带 gap 的非重叠长历史块中位数**，
   不是紧邻重叠窗口；(c) 它**不替代**绝对分数，而是被独立保形校准后与之相乘。
   P2 就是为了一次性回答"这三点差别是否足够"。若 P2 ≤0，立场 B 被证伪，这是设计意图。
2. **相依性罚项可能吞掉全部收益。** 若 ρ>3，e-process 相对路径极大就只是换了个统计量。
   缓解：抽稀 + γ；判据：失败条 5。
3. **位置桶的契约风险。** §4.3 的解读若不被追认，退到"age-free 池化参照 + 仅由 e-process 承担多重性"
   （已作为副臂预注册）；代价是预计 FAR 的长度依赖回升（0.00/0.06/0.21 那条曲线），
   需要用 R 通道的 level-free 性质吸收——这本身是一个可检验的次级假设。
4. **两个通道可能高度共线。** 都建立在同一白化坐标上。缓解：`log e^A` 与 `log e^R` 的相关是一级报告量；
   若 routine 上相关 >0.7，乘积的有效证据接近单通道，γ 会把收益压掉。这会以 P2≈0 的形式暴露。
5. **抵御样本 n 太小。** B1/B2 上 14 条（5 条无信号）、B2-384 上 5 条 bounded。所有抵御侧结论
   在 B3 拿到 ≥40 条之前都只是描述。
6. **winner's curse。** 本方案的层带、窗宽、读法沿用 L 在 36 个候选上事后挑出的 WGM C2 家族。
   这是**继承的适应性选择**，必须在 B3 报告中写明：DRS 与 CAND-A 的差值（P2）才是本轮的干净证据，
   DRS 的绝对数不是。
7. **B1/B2/C1 已被多轮观察**：本文在其上的一切都是 adaptive development evidence（简报 D、C 线 §11）。

---

## 9. 相对两条线，什么是真正新的

| # | 新点 | 两条线的现状 |
|---|---|---|
| 1 | **把"绝对开集分数"与"trace 相对创新"各自保形校准后再融合成一个校准分数** | 简报 F2 明确写着这是开放问题；L 只有绝对参照，C 的 DRR 只有相对参照且方向来自正例 |
| 2 | **用 e-process 取代"路径极大 + 单阈值"作为多重性装置** | 简报 F5 明确写着这是开放问题；两线的序列读法（persist2/CUSUM/block）都在**同一个**路径极大框架内，C 节已证伪它们"降 FAR"的用法；本文换的是框架，目标是**同预算换及时性**，不是降 FAR |
| 3 | **延迟预算的显式分解与定向攻击**：`延迟 = 窗宽 + 裕度`，裕度 = "99.5 百分位 vs 95 百分位"的距离，用累积证据把它换成 ~2 次 look | L 诊断出裕度占主导（B2.4）但没有攻击它，只试了窗宽与层带（各值 2–3 token） |
| 4 | **把相依性罚项 ρ 与 γ 变成 routine-only 可测量的数** | M3 在两条线都只是一句告诫；O13/O17/O18 反复被重叠性咬伤，但没人量化过 |
| 5 | **收缩支配的有限记忆 e-process**：`shrink(x)≤x` 逐路径支配 ⇒ Ville 界仍成立，同时有显式半衰期 | A5/O12 否定了无界 CUSUM，D6 要求显式记忆长度，此前没有任何候选同时满足"有限记忆 + 有效性证明" |
| 6 | **provisional/confirmed 两态 + 三层归因通道**，把 B1 的三层分解从"机制解释"变成**在线输出的一部分**（不进分数、只进解释与代码域升级） | F1 是开放问题；B1.2 已证明重排类量不可作分数（跨 batch 不稳），本文因此**只把它放在归因位** |
| 7 | **抵御/执行子分类改用早中层持续性**，与被证伪的 "novelty 回落 = 抵御" 显式区分，且**不设等待期、不冻结零点** | DRR/LDC 的失败正来自固定 64-token 等待 + 零点 delta 判据（删失 17/40、条件 AUROC 0.690 CI 跨零） |
| 8 | **会话预算的可加语义**：多轮 agent 按 episode 重置、按 e 值求和聚合到会话 | D8/Q2 是未决问题，两条线都只评价单个 generation episode（O14） |

