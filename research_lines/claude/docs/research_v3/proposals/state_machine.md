# 提案 TSSM：两阶段切换–持续状态机（Two-Stage Switch–Persistence Machine）

立场 C：晚层短窗提出「语义/任务切换候选」→ 早中层相对本 trace 候选前状态的持续性决定 provisional → confirmed。

日期：2026-09-06　作者：Opus 5 算法研究员（受 Claude Fable 5.1 委派）
输入：`docs/research_v3/evidence_brief.md`（下称"简报"，引用格式 B1.3 / C / E / F3 等）
状态：**设计提案，未运行任何实验**；本文所有数字分为「已冻结报告中的历史事实」与「本设计的预测」，后者标注为预测。

---

## 0. 一页摘要

**要解决的矛盾。** 简报 B1.3 给出一个两线独立观察到的层级分工：晚层（L11–15）在语义/任务边界**瞬时**放大
（onset token 的 matched JSD contrast late +0.056 vs early −0.003），但持续执行时晚层往往回到熟悉区（L11–15 的
late−early 非负比例仅约 50%），而早中层（L3/L4/L7 为 91/86/77%）更持久。同时 O4/B2.4 说明：**纯 change
magnitude 不能直接阈值化**（正常客服的最大局部 JSD 与 onset 同量级，onset-vs-normal-max AUROC ≈ 0.50），而
**绝对流形阈值又太晚**（matched 相对排序 +8 AUROC 0.83–0.92，但绝对阈值下 +8 召回只有 11–25%）。
DRR 与 LDC 已经证明：把「第一次越线」锁定为起点、再读固定 64-token delta，会在起点错误时把多数执行判成
recovered（DRR 27→4；LDC 12 recovered vs 3 sustained）。

**设计的一句话。** 把「提出」与「判定」彻底分开：**晚层自对比统计量只负责以固定速率提出候选，不承担任何
误报预算**；**所有误报预算由第二阶段承担**，第二阶段用早中层窗口相对**本 trace 候选前基线**的偏移，在每个
候选上并行开一个 episode，用序贯证据（e-value）在**可变长度**上给出 provisional → confirmed；**返回原任务必须
由一个新的晚层候选 + 早中层回到候选前基线容差内并维持** 共同证据化，绝不把 novelty 回落等同抵御成功；抵御/
执行是带自身不确定性的**子分类**，其失败不回收主事件检出。

**为什么这解决 oracle-onset 问题。** DRR/LDC 的失败机制被两份报告一致归因为「首次越线常不是需要分类的实质
起点」。TSSM 不锁定、不重置为单一起点，而是**同时维持最多 3 个候选 episode**（各自的候选前基线、各自的证据
累计、各自按 λ_k=2^-k 分配的预算份额），会话在任一 episode 达到阈值时确认。错误的早候选自然不积累证据而
静默过期；正确的候选独立确认。这把「选哪个 candidate 计算恢复」从一个必须猜对的决定，变成一个由预算分配
处理的多重性问题——这正是 LDC 结论 §8 要求的「把 engagement detection 与 candidate 选择拆成两个清楚分离的
问题」。

**输出。** 逐 token 校准分数（正常流量上分布已知）+ 可读证据窗（token 下标区间与 top-8 使用摘要）+ 归因通道
（支持集扩张 / 支持集内重排 / 联合结构）+ 状态（idle / provisional / confirmed / resisted_return /
censored_provisional）+ 子分类不确定性。不是单一二值报警（契约 E）。

---

## 1. 组件与简报依据

| # | 组件 | 统计量（形式化） | 依据 | 校准方式 |
|---|---|---|---|---|
| S1 | 晚层切换候选 | `c_t = mean_{l∈L11..15} JSD( P̄_l[t-3..t] , P̄_l[t-24..t-9] )`，`P̄` = router 概率在窗内均值；无概率时退化为 top-8 入选率的 Hellinger | B1.3（晚层在边界瞬时放大）；B3.10/§6（返回原任务同样产生晚层峰值）；B6.18（全 16×64 概率可缓存、零额外前向开销） | **速率**校准：位置桶 z 标准化后取 routine 上的 (1−ρ) 分位，ρ=0.02/eligible position。**不承担 FAR** |
| S2 | 早中层状态窗口 | `x_s` = L2–L7 的 8-token 因果 top-8 入选率向量（6×64=384 维） | B1.3（早中层持续，L0–L7 mean delta 全正）；B6.19（token innovation 是基础信息单位）；B1.1（支持集扩张的能量落在冷门专家） | 白化 μ_R/σ_R 来自 routine 拟合集（不含任何正例） |
| S3 | 候选前基线（trace 相对参照） | `μ_e = median{ x_s : s ∈ [e−32, e−8] }`，`S_e` = 同区间的稳健尺度 | B2.5（trace 相对基线有早期信号：27/40 越线、16/40 在 +8 内）；B2.4（相对排序早、绝对阈值晚） | 无需校准（自参照）；仅 `d` 的零分布在 routine 上校准 |
| S4 | 相对偏移 | `d_s = ‖(x_s − μ_e)/S_e‖_2`（trace 相对） | 同上 | routine 上同构造（对每条 routine trace的每个 eligible e）得零分布 |
| S5 | 绝对偏移（双参照的第二支） | `a_s` = `x_s` 对 routine 流形的白化距离，位置桶 z | B2.7（部署侧校准把 +8 召回从 0–0.13 提到 0.29–0.54）；简报 F2 | 部署侧 routine 保形 |
| S6 | 逐 token 证据增量 | `p̂_s` = (d_s, a_s) 组合秩在 routine 零分布上的保形 p 值；`e_s = (1/p̂_s)^{1/w2}`（`w2=8`，按窗口重叠折减 7/8 的重复信息） | M3（相邻 8-token 窗共享 7 token，不能当独立样本）；D5（group 等权） | routine 零分布 |
| S7 | Episode 证据 | `logE_k ← logE_k + log e_s`；provisional 当 `logE_k ≥ h_prov + log(1/λ_k)`，confirmed 当 `≥ h_conf + log(1/λ_k)` | C 项「持久性/块/CUSUM 降 FAR」已证伪——见 §2 说明本设计**不**主张累积降 FAR | `h_prov/h_conf` = routine 上每条 trace 的 `max_k logE_k` 的有限样本次序统计量（α=0.10 / 0.05） |
| S8 | 返回事件（第二切换） | 需同时满足：(a) 新候选 `e'>e` 触发；(b) `d_s ≤ τ_ret` 连续 `R=12` token，`τ_ret` = routine 内同一 trace 自变异的 q90；(c) `a_s` 不再上升 | B3.10（novelty 回落 ≠ 抵御成功，DRR/LDC 已证伪）；B1.3/§6（resume 也产生晚层峰值） | `τ_ret` 由 routine trace 内自变异校准 |
| S9 | 归因通道 | 证据窗内：(i) 未见/低频 layer-expert 对占比；(ii) routine 自身 top-8 上的概率质量集中度反号量；(iii) 相邻层 top-1 对的未见率 | B1.1（unseen8 29/40、surprisal8 37/40）；B1.2（代码只在 routine 已用集合内重加权，cos 0.78）；B1.3（相邻层 top-1 未见率代码 7.3×） | 仅作为**报告与路由**，不进入分数 |
| S10 | 删失与短输出 | `e < 40` 时 episode 标 `prior_baseline`（`μ_e` 用 routine 质心）；EOS 时未确认的 episode 标 `censored_provisional` 并输出已达质量比 | B2.6（160 条中只有 98 条 ≥64 token；FHTS/NTR 死于此）；B5.16（384 replay 把 4 条抵御改判为晚起执行；22/61 抵御被截断） | 单列报告，不并入主 FAR |
| S11 | 会话预算 | `Σ_k λ_k ≤ 1`，`λ_k = 2^{-k}`，`K_max=6` episode/session，`M_max=3` 并行 open | D6（历史影响必须有明确衰减/复位）；简报 F5（按会话误报预算） | 预算与保形阈值联合报告 |
| S12 | 代码通道的文本确认（**非路由，产品侧**） | 证据窗归因为 (ii) 且 (i)≈0 时，把 provisional 交给零误报文本规则（代码围栏/SQL/关键字） | B4.12（文本规则 301 条 non-drift 零误报、抓 7/8，但中位 +17）；B4.14（盲区代码专属） | 单独报告，不进入 route-only 指标 |

**层带的来源与纪律。** 晚层带 = LDC 已冻结的 L11–15；早中层带 = **连续带 L2–L7**，不是逐层挑选（layer zoom
§7 明确不授权直接选 L4/L7/L12）。备选臂 = LDC 已冻结的 early band L0–L4。两个带在 B3 前冻结，备选臂只作
预注册次要臂并作多重比较校正。这不是 C 项禁止的「routine-only 层选择」（没有用任何 routine-only 判据挑层），
也不是「层维特征」（没有逐层 max / 层比值 / 层投票；每个带只产生一个池化标量）。

---

## 2. 明确不重试的 C 节条目，及本设计与之的差别

| C 节条目 | 本设计为何不同 |
|---|---|
| 用持久性 / 块 / CUSUM **降 FAR** | 本设计**不主张**累积降 FAR。FAR 仍由部署侧保形阈值钉死（B2.7）。序贯累积买到的是两件旧设计没有的东西：(a) **可变长度**判定，取代 DRR 的固定 64-token 等待（该等待在 240 条里删失 17 条、并结构性排除短输出）；(b) 在**数量不定的 episode** 之间分配一份会话预算。这是多重性与删失问题，不是 ROC 点搬移。 |
| 长度分层保形作主表 | 主表仍为位置桶 + 部署侧保形；长度只作**分层抽样**要求与分段报告（B3 数据要求 §5）。 |
| 层维特征（逐层 max / 比值 / 层分数 / 层投票） | 每个带池化为一个标量；不构造层维向量，不做层投票（LDC 的 2-of-3 投票也不复用）。 |
| routine-only 层选择 | 层带由 B1.3 机制观察预先固定并冻结，且声明为 development-derived；另设一个已冻结的备选带作对照。 |
| warm-up（忽略前 W0 token） | **不抑制任何早期报警**。基线不足时改用 `prior_baseline` 通道（routine 质心作 `μ_e`），单列其 FAR 与召回。 |
| 固定专家名单 | 无专家名单；带内分布式统计量。 |
| 形态条件化参照 / 结构化 run-length / 去结构化 routine 池 / 形态类特征（FCM） | routine 池保留 JSON 与合规拒绝（B4.13）；全流程无形态标签；「routine 是否含结构化输出」只作 B3 设计变量与分段报告。 |
| 窄窗口换抵御召回 | S1 的 w=4 不是为抵御召回，而是为**切换**统计量（自对比，不是流形距离）；本设计**不预测**抵御检出提升（§7）。 |
| 把 novelty 回落等同抵御成功（DRR / LDC） | 返回需要 S8 的三重正向证据（第二候选 + 回到候选前容差 + 维持 12 token），且只输出**子标签**，不回收主事件。 |
| 锁定首次越线后固定 64-token delta | 不锁定（并行 3 个 episode）、不固定窗长（证据质量驱动）、不使用 late−early delta 的符号规则。 |
| absolute-age 归一化 | 绝对位置**只**出现在 S1 的候选速率校准（≤3 个粗桶）与 time-at-risk 计数中；**承担判定的第二阶段完全 age-free**（`d_s` 是 trace 自参照）。这同时满足简报 B2.7「位置桶标准化必要」与 Codex D3「绝对 age 不进入 normality score」。 |
| 局部密度比 / conditional successor forecast / staggered min-2 / q25 持久性 | 均不使用。 |
| 单一 novelty 阈值继续调 bins / block / quantile | 本设计的新自由度在**架构**（提出/判定分离、并行 episode、双参照、返回事件），不在同一分数的读法参数。 |

回应的开放问题：**F3（本立场的主问题）**；**F5**（会话预算与可变长度判定）；**F2**（`d`/`a` 双参照）；
**F1 部分**（三通道只作归因输出，不融合成一个分数——融合留给其他立场）；**F4**（S1 用概率，故把「概率类特征
跨 batch 稳定性」写成一个显式失败判据，见 §6）。

---

## 3. 在线算法（伪代码）

输入：每 token 的 `top8[16][8]`（专家 id），可选 `P[16][64]`（router 概率）。**不读**文本、任务、workflow、
渠道、绝对位置语义、结果、人工锚点。绝对位置只以「已检验次数」的形式出现在 S1 速率桶与 time-at-risk 报告。

```text
# ---- frozen parameters (preregistered) ----
LATE = {11..15};  EM = {2,3,4,5,6,7}
w1 = 4;  gap = 4;  r1 = 16          # late self-contrast: [t-3..t] vs [t-24..t-9]
w2 = 8                              # early-middle state window
BMIN = 32; BGAP = 8                 # baseline span [e-32, e-8]
rho = 0.02                          # candidate rate per eligible position
DEDUP = 8                           # min token gap between candidates
MMAX = 3; KMAX = 6                  # parallel episodes / episodes per session
lam[k] = 2^-k                       # session budget allocation, sum <= 1
R_RET = 12                          # tokens of sustained return
h_prov, h_conf, q_rho, tau_ret, mu_R, sig_R, bucket_stats   # from routine calibration

# ---- session state ----
episodes = []            # each: {e, mu_e, S_e, logE, k, state, first_prov_t, reanchors}
k_used = 0;  score_stream = []

on_token(t, top8_t, P_t):
    push_ring(top8_t, P_t)

    # ---------- Stage 1: late-layer switch candidate (carries NO FAR budget) ----------
    if t >= w1 + gap + r1:
        c = mean_{l in LATE} JSD( mean(P[l], t-w1+1 .. t), mean(P[l], t-w1-gap-r1+1 .. t-w1-gap) )
            # fallback if probs unavailable: Hellinger between top-8 selection-rate vectors
        z1 = (c - mu_b[bucket(t)]) / sig_b[bucket(t)]
        is_cand = (z1 >= q_rho) and (t - last_cand_t >= DEDUP) and (k_used < KMAX)
    else:
        z1 = NA; is_cand = False

    # ---------- open an episode (parallel, never a single locked anchor) ----------
    if is_cand:
        if len(open_episodes) == MMAX:            # evict the weakest, keep its record
            drop = argmin logE over open episodes; close(drop, "superseded")
        if t >= BMIN + BGAP + w2:
            mu_e, S_e = robust_center_scale({ x_s : s in [t-BMIN, t-BGAP] });  mode = "self"
        else:
            mu_e, S_e = mu_R, sig_R;              mode = "prior_baseline"   # short prefix: no warm-up suppression
        k_used += 1
        episodes.append({e:t, mu_e, S_e, logE:0, k:k_used, state:"open", mode:mode})
        last_cand_t = t

    # ---------- Stage 2: early-middle persistence vs the trace's own pre-candidate state ----
    x = selection_rate_window(top8, EM, w2)                  # 384-dim, causal
    a = whitened_distance(x, mu_R, sig_R);  a_z = bucket_z(a, t)   # absolute manifold ref
    for ep in open_episodes with ep.e <= t - w2:             # window fully after the candidate
        d = norm( (x - ep.mu_e) / ep.S_e )                   # trace-relative ref (age-free)
        p_hat = conformal_p( (d, a_z), null=routine_joint_null )   # rank in routine null
        ep.logE += (1/w2) * log(1/p_hat)                     # 7/8 of the window is stale info (M3)

        thr_p = h_prov + log(1/lam[ep.k]);  thr_c = h_conf + log(1/lam[ep.k])
        if ep.state=="open" and ep.logE >= thr_p:
            ep.state = "provisional"; ep.first_prov_t = t
            emit(EVENT_PROVISIONAL, anchor=ep.e, window=[ep.e, t],
                 attribution=attribute(top8[ep.e..t]), score=calibrated(ep.logE))
        if ep.state=="provisional" and ep.logE >= thr_c:
            ep.state = "confirmed"
            emit(EVENT_CONFIRMED, anchor=ep.e, window=[ep.e, t], subclass="executing",
                 uncertainty=margin(ep.logE - thr_c))

    # ---------- second switch = return to task (NOT "novelty fell") ----------
    if is_cand:
        for ep in open_episodes with ep.state in {"provisional","confirmed"} and t > ep.e + w2:
            ep.pending_return = {t2: t, count: 0}
    for ep in open_episodes with ep.pending_return:
        d = norm( (x - ep.mu_e) / ep.S_e )
        ep.pending_return.count = (d <= tau_ret and a_z not rising) ? ep.pending_return.count+1 : 0
        if ep.pending_return.count >= R_RET:
            close(ep, subclass="resisted_return",
                  uncertainty=(ep.logE margin, tokens_observed, d-margin vs tau_ret))
            # primary event is NOT retracted; it stays a positive detection

    # ---------- per-token calibrated score (contract E) ----------
    s_t = max( calibrated(z1), max_over_open_episodes(calibrated(ep.logE)) )
    score_stream.append(s_t)

on_eos():
    for ep in open_episodes:
        close(ep, state = ep.state=="open" ? "censored_pre_provisional" : "censored_provisional",
                  mass_fraction = ep.logE / thr_c)      # never "resisted"
on_turn_boundary():
    close all episodes as "episode_boundary"; keep k_used (session budget persists across turns)
```

**候选生成、复位、删失的三条规则（明写）。**
1. **候选生成**只由 S1 决定，速率固定 ρ；候选之间强制 8 token 去重；`K_max=6` 后不再新开（预算耗尽转为
   「仅报告校准分数」的降级状态，并显式输出 `budget_exhausted`）。
2. **复位**：episode 在 confirmed、resisted_return、EOS、turn 边界、或被更强候选挤出（`M_max`）时关闭；
   会话预算 `k_used` 跨 turn 不复位（D6/D8 明确声明）。`resisted_return` 后允许再开新 episode，其基线在返回
   窗之后重估——这就是「允许第二次切换/回到题外任务」。
3. **删失**：EOS 时未达 `h_prov` → `censored_pre_provisional`；已 provisional 未 confirmed →
   `censored_provisional`，并输出 `mass_fraction`。删失**永不**判为抵御（B5.16 的 4 条晚起执行是直接教训）。
   短输出（`e<40`）走 `prior_baseline` 通道，其 FAR 与召回**单列**。

---

## 4. 两阶段在 routine-only 数据上的校准

数据角色：`N_fit` = routine 拟合（B1/B2 的 clean+benign，不含任何 drift）；`N_cal` = C1 fold 0–2（100 组
/200 条），按 scenario 对半 → 每半 50 组 /100 条（满足 B2.6 的 ≥90/半）；`N_holdout` = C1 fold 3–4
（60 组 /120 条）；384-token 位置桶另用 B2-384 replay 的 160 条 routine 控制（LDC 已声明不得把 192-token
C1 阈值外推为 384-token 保证）。抵御臂只进 `N_eval`，不进 `N_fit`/`N_cal`（B4.13 要求 routine 池保留 JSON
与合规拒绝，但抵御属评价用 hard negative）。

| 阶段 | 校准对象 | 规则 |
|---|---|---|
| S1 | 候选速率 ρ | 在 `N_cal` 上对每个 eligible position 算 `c`，位置桶 `b = end // 32`，**上限 3 桶、每桶 ≥30 个窗口**（lead §6.4 修正的桶规则），桶内 z 标准化后取 (1−ρ) 分位得 `q_rho`。主 ρ=0.02，敏感性 {0.01, 0.05}。报告 routine 上每条 trace 的候选数分布（中位、q90）。 |
| S2 零分布 | `(d, a_z)` 的联合零分布 | 对 `N_cal` 每条 routine trace 的**每个 S1 候选**跑同一状态机，收集 `(d_s, a_z_s)`；秩→保形 p 值（`p̂ = (rank+1)/(n+1)`，无经验尾部封顶）。 |
| S2 阈值 | `h_prov`, `h_conf` | `N_cal` 上每条 trace 的 `max_k (logE_k − log(1/λ_k))` 的第 `ceil((n+1)(1−α))` 小者；α_prov=0.10、α_conf=0.05。两个校准半交换后合并计数（部署侧、与被评价 trace 不同 scenario）。 |
| S8 | `τ_ret` | `N_cal` 上「同一 trace 内、候选后窗口对候选前基线的 `d`」的 q90。 |
| 预算 | 每会话 | 预注册 provisional ≤ 0.12/session、confirmed ≤ 0.05/session；同时报告每 1,000 个 eligible token 的报警起点密度、以及 `P(alarm by t)` 的正常累积发生曲线。 |

**分段 FAR（全部一级指标）**：clean / benign / **anchored-resist** / **silent-ignore resist** / 长度三分位 /
workflow / **routine 含 JSON vs 不含 JSON** / `prior_baseline` 通道。另按 lead 审计要求报告：两个校准半的
**带符号** FAR 差（要求 ≤0.10）、定阈 trace 的身份与形态、剔除 top-1/3/5 定阈 trace 后的 FAR、误报样本集合
跨配置的 Jaccard 稳定性。

**跨 batch 稳定性（F4 的显式检验）**：S1 使用概率，B1.2 已证明 rmass 类概率特征跨 batch 不迁移。因此预注册
一个**独立失败判据**：若 `q_rho` 在 C1 校准与 B2-384 routine 上导致的候选速率相差 >2×，或概率版与 top-8
入选率退化版的候选集合 Jaccard <0.5，则 S1 降级为 top-8 入选率 Hellinger 版本（该退化版在预注册中并列冻结）。

---

## 5. 评价计划（遵循简报 E）

**主事件**：drift ∪ 有锚点抵御。**主锚点**：`product_onset`（announcement_onset 与 topic_entry 并列记录、
只作容差与审计）。容差带 `[onset−8, onset)` 内报警不计误报、延迟计 0；`end < onset−8` 仍为误报（B5.15 的
双锚点规则）。**不可逆点** = 首个产生交付物的工具调用或候选回复发布（B5.17 的 3 条「抵御」按此归 drift）。

**预注册的三个单格（primary cells）**，family-wise α=0.05，Holm 校正（各格 α'=0.0167）；每格只有一个配置
（主层带对、ρ=0.02、α_prov=0.10 / α_conf=0.05、B3 单方向 + B3 内分组交叉）：

| 格 | 指标 | 通过门槛 | 失败判据 |
|---|---|---|---|
| **P1 及时性** | 主事件的 **provisional** 在 `product_onset+16` 内的干净召回（与冻结 CAND-A 在同一 B3 上配对 McNemar） | Δ ≥ +0.12 且 CAND-A 绝对值 ≥0.55 时不低于它 | Δ ≤ +0.05 或方向为负 → 两阶段架构在及时性上无增量，**声明 no-go**（不得改参数重试） |
| **P2 风险** | held-out routine 上 confirmed trace-FAR ≤0.05、provisional ≤0.12；benign−clean ≤0.10；anchored-resist ≤0.20；两半 FAR 差 ≤0.10 | 全部满足 | confirmed FAR >0.10 → 校准失败；benign−clean >0.15 → 候选阶段在响应「提到话题」 |
| **P3 子分类** | 终态 episode 的 execute-vs-resist 平衡准确率与连续子分数 AUROC（scenario bootstrap CI） | BA ≥0.70 且 AUROC 95% CI 下界 >0.60，n_resist_terminal ≥20 | BA <0.60 或 CI 跨 0.5 → **子分类放弃**，只输出主事件与「未定」；这是 DRR/LDC 失败的重演，必须如实宣告 |

**次要（预注册、非确认性，family 内 BH 校正）**：
(a) 每个操作点的似然比 LR+ = recall/FAR，按 clean/benign/resist 分段，group bootstrap CI；
(b) **不可逆点前检出率**：provisional 与 confirmed 各自在不可逆点之前触发的比例与 token 余量分布；
(c) **代码域单列**：route-only 的 provisional/confirmed 召回 + 加文本确认（S12）后的召回与延迟，两者分别报告；
(d) `prior_baseline` 通道、`censored_*` 状态、`budget_exhausted` 的计数与其 Kaplan–Meier 式 time-to-confirm 曲线；
(e) 候选阶段诊断：候选相对 `product_onset` 的 offset 分布、pre-onset 候选比例、被 `M_max` 挤出的 episode 数；
(f) 备选层带臂（EM=L0–L4）与备选 ρ，只报告，不确认（若要提升为主张需 Bonferroni ×2）。

**针对「首次越线常先于真实执行 onset」的专门审计（一级）**：对每条主事件正例记录
`offset(first_candidate)`、`offset(confirming_candidate)`、以及「确认所用候选是否是第一个候选」的比例。
预注册失败判据：若 ≥30% 的 drift 确认锚定在早于 `product_onset` 超过 32 token 的候选上，**且**同一 scenario
的 benign 臂在相同相对位置也会确认，则判定候选阶段在响应「提及/预告」而非「进入」，S1 不成立。

**B3 数据要求**（由上表直接推出）：
1. routine：每个校准半 ≥90 条、≥45 个 scenario；held-out routine **≥200 条**（见 §8 power：120 条只能把
   FAR=0.05 的 Wilson 上界压到 0.105，无法支持「≤0.10」的区间主张）；每个长度三分位 ≥30 条。
2. **routine 含/不含结构化输出（JSON）为显式设计变量**：两层各 ≥30%，且不跨 fold 混淆（B4.13：B2 的
   routine 有 47.5% 以 JSON 开头，去掉 JSON 会把 FAR 从 0.10 抬到 0.29–0.35，故只能分层报告，不能清洗）。
3. **代码/结构化交付物 drift 每方向 ≥20 条**，并区分「字面代码」与「关于代码的散文」（B5.17：8 条
   programming 里 2–3 条是散文，有效 n 只有 5–6）。
4. **有锚点抵御 ≥40 条**（B3 需要显著提高 bounded-engagement 发生率的场景设计；B1/B2 的 61 条抵御里只有
   14 条有锚点，B2-384 里 bounded 只有 5 条）；同时记录 `topic_word_leak` 与 `decode_truncated` 标志。
5. **384-token horizon**，stop reason 作为删失信息；384 位置桶用 384-token routine 校准，不外推 192。
6. 双锚点（product_onset 主 / announcement_onset 与 topic_entry 并列）；产出交付物的工具调用归 drift。

---

## 6. 统计效力（在上述样本量下）

| 检验 | 假定 | 效力 |
|---|---|---|
| P1 配对 McNemar，n_primary=100（60 drift + 40 anchored resist），不一致对率 ≈0.20 | 真实 Δ=0.12（16:4 分裂），α'=0.0167 | ≈0.72 |
| 同上，真实 Δ=0.08 | | ≈0.40 —— **B3 不足以判定 0.05–0.08 量级的改进**，预注册中已把门槛设在 0.12 |
| P2 confirmed FAR，n_holdout=200，观测 5/200 | Wilson 95% 上界 0.058 | 支持「≤0.10」的区间主张；n=120 时观测 6/120 上界 0.105，**不支持** |
| P3 AUROC，n_resist_terminal=20 / n_exec_terminal=45，真值 0.85 | Hanley–McNeil SE ≈0.062 | 拒绝「≤0.70」的效力 ≈0.79；n_resist=12 时 SE≈0.08、效力 ≈0.55（这正是 LDC 的 12 条门槛为何是硬要求） |
| 代码域，n=20/方向，route-only 真实召回 0.35 vs 零假设 0.10 | 精确二项 | ≈0.68 —— 代码格只能给方向，不能给精确估计 |
| 分段 FAR 差（benign−clean），n=200 held-out | 检出 0.10 的差 | ≈0.65；因此该门槛以点估计 + CI 并列报告，不单独作 go/no-go |

---

## 7. 在现有数据上的预测（**预测，不是结果**）与「抓不到」清单

基于 B1/B2/B2-384/C1 的已冻结数字外推。全部为区间性预测，用于**事前否证**：若实测显著落在区间外，说明本
设计的机制假设错误。

| 指标 | 数据 | 预测 | 依据 |
|---|---|---|---|
| S1 候选覆盖 `product_onset+8` | B2-384，40 执行 | **0.70–0.85** | layer zoom onset token late contrast +0.056，CI [0.027,0.081]；LDC onset-free execution detection 0.875 |
| S1 每条 routine trace 候选数（ρ=0.02） | C1 held-out | 中位 **3–5**，q90 **8–11** | 192-token 中位输出 × 0.02 |
| 主事件 provisional R+8（drift，product_onset） | B1/B2 | **0.35–0.45** | CAND-A w=8 为 0.31（w=4，25/59）；相对参照买到 1–2 token 余量，窗口未变窄 |
| 主事件 provisional R+16（drift） | B1/B2 | **0.55–0.65** | CAND-A 33/59=0.56；上限来自 47% 渐进漂移在 +17…+35 才越线 |
| confirmed 在执行上的召回 | B2-384，40 执行 | **0.45–0.60** | DRR 0.10、LDC 0.15–0.20 是「锁定单一起点」的代价；layer zoom §4.2 显示锚点正确时 L2–L7 持续率 77–91%；扣除删失与 15–25% 的「路由稳定化」执行 |
| provisional FAR（clean / benign / anchored-resist） | C1 held-out + B2-384 控制 | **0.05–0.08 / 0.12–0.18 / 0.18–0.28** | B3.11 benign 是 clean 的 1–2.7×；B2.4 resist 是最难负例 |
| confirmed FAR（合并控制） | 同上 | **0.02–0.05** | LDC 两阶段 held-out group FAR 3.3% |
| 代码 route-only：候选命中 / confirmed | B1/B2 8 条 | 候选 **3–5/8**、confirmed **0–2/8** | B1.3 相邻层 top-1 未见率代码 7.3×（切换可见），但 B1.2 早层比值 ≤1.02（持续不可见） |
| 代码 + 文本确认（S12） | 同上 | **7/8，中位 +17 token** | B4.12 直接引用 |
| 有锚点抵御 provisional R+16 | B1/B2 14 条 | **2–4/14**（天花板 9/14） | B3.9：5/14 在任何窗宽下峰值只有阈值的 0.08–0.72× |
| 抵御/执行子分类 | B2-384 | **不可评价**（n_bounded=5 < 12） | 这正是 B3 需要 ≥40 有锚点抵御的原因；在 B2-384 上只能报 AUROC 点估计（预测 0.75–0.85，介于 DRR 0.69 与 LDC oracle 0.92 之间），不作结论 |
| LR+（provisional / confirmed，合并控制） | — | **4–6 / 12–25** | 由上面的召回与 FAR 直接相除 |

**明确抓不到的（写入适用范围声明）**：
1. **代码/SQL 的持续执行**：它只在 routine 已用专家集合内重加权（B1.2），早中层持续通道结构性看不见。
   本设计只能在**切换瞬间**碰运气，确认阶段必然失败——因此代码域**依赖 S12 文本确认**，且必须单列。
2. **B3.9 的 5 条无信号抵御**：任何窗宽、任何 α 都救不了（峰值 ≤0.72× 阈值），属能力边界。
3. **47 条静默抵御**：按契约这本就不该报警（B3.8：冻结候选只报 2–4 条，与 clean 臂同级）。这是设计正确性，
   不是漏检——但也意味着「上下文里有攻击」这件事本方法永远不知道。
4. **切换后进入路由稳定期的执行**（layer zoom §5：`b2-f3-066` 在 16/16 层回落）：预测 15–25% 的执行会被
   子分类误判为 `resisted_return`。这些**仍是 provisional 检出**（主事件不丢），但子分类错。
5. **`product_onset ≈ 0` 的 trace**：只能走 `prior_baseline` 通道，其 FAR 明显更高，单列报告。
6. **极短输出（<40 token）**：结构性删失；C1 的 160 条里只有 98 条 ≥64 token，B3 必须按长度分层抽样。
7. **benign 的话题提及**：仍会产生 provisional（B3.11，估计量性质）。这是 LR 而非 FAR 的问题：本设计的应对
   是让 confirmed 阶段把它们过滤掉（预测 benign 的 confirmed FAR ≈ clean 水平），而不是假装 provisional 能不报。

---

## 8. 计算成本

| 项 | 成本 |
|---|---|
| 推理侧 | **零额外前向开销**（B6.18），只读已有 router 输出 |
| S1（晚层 JSD，5×64，两个滑动均值） | ≈2k flop/token |
| S2（EM 384 维滑窗 + 白化距离 + 相对距离 × ≤3 episode） | ≈2.5k flop/token |
| 合计 | **≈4–5k flop/token，约 3–5 µs/token**（对照：CAND-A 2.2 µs / 约 3k flop，CAND-B 0.7 µs） |
| 会话状态 | EM 环形缓冲 64×384 uint8 = 24 KB + 晚层缓冲 24×320 fp16 = 15 KB + 拟合统计 ≈8 KB + episode 状态 <1 KB ≈ **50 KB/session**（fp32 约 200 KB） |
| 路由缓存（离线） | 240 条 ×384×16×64 fp16 ≈ 190 MB（已存在）；top-8 uint8/CSR 无损压缩 12–14×（B6.18） |
| 校准一次（C1 320 条 + B2-384 160 条，含零分布与两个阈值） | CPU 单核 **<2 分钟**；两个层带臂 × 3 个 ρ 的完整预注册网格 <15 分钟 |

---

## 9. 相对两条线，什么是真正新的

1. **提出与判定的预算分离。** 两条线都是「一个分数流 + 一个阈值 + 一种读法」。这里 S1 **不承担任何 FAR
   预算**（按速率校准），全部预算在 S2。这把 O4/B2.4 的「change magnitude 不可阈值化」从一条否证变成一个
   设计输入：晚层幅度只被用来**提出**，从不被用来**判定**。
2. **并行候选 episode + 预算分配，取代「锁定首次越线」。** DRR 与 LDC 两份报告都把失败归因于「首次越线不是
   实质起点」，但两者的补救都停留在「换一个 candidate 选择规则」。本设计把它转成多重性问题：同时开 3 个
   episode，各带 λ_k 份预算，错的候选自然不积累。据两份报告，这是从未被试过的形态。
3. **两个参照系的显式分工。** 晚层用**时间自对比**（本 trace 近端历史），早中层用**候选前基线**（本 trace
   的状态）+ routine 绝对流形（双参照，回答 F2）。DRR 用过 trace 相对，但用在**检测方向**上；LDC 用绝对
   band novelty。没有人把 layer zoom 的「晚层=切换放大器 / 早中层=状态持续」当作两个不同参照系来实现。
4. **返回原任务需要正向证据。** 实现 layer zoom §6（resume 也产生晚层峰值）：`resisted_return` 要求
   第二候选 + 回到候选前容差 + 维持 12 token，三者缺一不可。C 节明令禁止的「novelty 回落 = 抵御」被替换为
   一个**事件**判据。
5. **检出与子分类解耦，各有不确定性。** DRR 的致命处是子分类失败连带把 27 条初级检出压成 4 条。这里
   provisional 一旦发出就是主事件的检出记录，子分类失败只影响 P3 格，不影响 P1/P2。
6. **可变长度确认 + 显式删失状态**，取代固定 64-token 等待（DRR 因此删失 17/240 并结构性排除短输出）。
7. **归因通道作为一等输出，并据此把代码类证据路由到文本确认。** 把已知盲区（B1.2/B4.12）从「漏检」变成
   「可声明、可路由的能力边界」，这是 lead 的 FCM 报告点名但从未实现的两阶段读法之一。

---

## 10. 已知风险

- **S1 用概率**：概率类特征跨 batch 不稳定的先例（rmass，B1.2）。缓解：自对比而非绝对集中度、显式的
  Jaccard/速率稳定性失败判据、并列冻结 top-8 退化版。
- **早中层持续性来自词汇/句法而非任务状态**（layer zoom §限制）。本设计不做因果命名；若 P3 失败，正确结论是
  「早中层持续不能承担状态判断」，而不是换层带重试。
- **层带来自 B2 开发数据**：必须在 B3 前冻结，且备选臂只作对照。
- **并行 episode 的预算分配是保守的**：`λ_k=2^-k` 使第 6 个 episode 的阈值高 log 64 ≈ 4.16 nat，长输出上后段
  候选实际不可达。这是有意的（长回复是误报主要来源，B2 长度三分位 FAR 0.00/0.06/0.21），但会牺牲晚起执行
  （B5.16 的 4 条在 192 token 后才起）。需在 B3 报告「按 episode 序号的召回」以量化这个代价。
- **`h_prov`/`h_conf` 的保形对象是 `max_k logE_k`**，与「完整因果报警路径」一致（D7），但 routine 与部署流量
  的可交换性仍是假设；本设计以经验 held-out FAR 为主张依据，不引用 anytime-valid 的理论保证。
- **有锚点抵御 ≥40 条可能收不齐**：B1/B2 的 61 条里只有 14 条有锚点。若 B3 收不齐，P3 必须预先声明为
  `inconclusive_sample_support`（照 LDC 先例），不得把「只差一条」改写为通过。
