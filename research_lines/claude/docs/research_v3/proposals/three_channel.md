# 三通道分解检测器（Three-Channel Decomposition, TCD）：v3 算法提案

立场 A：**三通道分解 + 分别校准 + 融合**。作者：Opus 5 算法研究员；提交给研究组长（Claude Fable 5.1）。
日期：2026-09-06。依据：`docs/research_v3/evidence_brief.md`（下称"简报"，引用格式 B1.1 / C / E / F1）。
状态：提案，未运行任何实验；所有预测数字为基于已冻结报告的**推断**，不是结果。

---

## 1. 一页summary

**问题。** 只用正常客服流量学习 MoE 路由流形，在线逐 token 输出校准分数；主事件 = 生成进入题外计算域
（drift ∪ 有锚点抵御），抵御/执行是子分类（简报 A）。

**核心主张。** 两条研究线独立收敛到同一个三层分解（B1.1–B1.3），但两条线的检测器都把这三层**压成一个标量**
——白化距离、路径 surprisal、band Hellinger 各自都是三层的一个特定加权和。这是当前性能天花板的结构性原因：
其他域漂移的白化能量 **78% 落在 routine 最低方差十分位**（B1.1），所以在单一标量里，"支持集内重排"（代码
所在的那一层）的贡献被稀释到噪声以下，永远不可能自己越过一个由 routine 全路径校准的阈值。同理，跨层链
统计量里 **79% 的代码超额来自边缘机会**（B1.3 / code_blindspot §1.5），因为 D1 深度链把边缘 surprisal 和联合
耦合混在一起算。

TCD 把这个标量**显式拆成三个近似正交的通道**，每个通道得到**自己的部署侧 routine-only 保形校准**、自己的
归因、自己的 FAR 预算，再用一个在任意依赖下都成立的融合规则合并到一个**按会话的误报预算**上：

| 通道 | 度量的机制 | 表示 | 层 | 窗 | 依据 |
|---|---|---|---|---|---|
| **S**（support expansion） | 征用 routine 罕用/未见专家 | top-8 落在稀有坐标集上的 −log q 之和 | 0–15 | 8 | B1.1（unseen8 29/40、surprisal8 37/40，clean 0–1/80） |
| **R**（within-support re-weighting） | routine 已用专家集合内的质量重排 | 白化欧氏能量，**只在频繁坐标上**，方差地板 | 5–15 | 8 | B1.2、B1.1（LDC 73% 报警窗全由已见专家组成） |
| **J**（joint structure） | 跨层组合的**边缘校正后**耦合新颖度 | 相邻层 top-1 对的 −log[P(a,b)/P(a)P(b)] | 5–11 | 4 | B1.3（相邻层 top-1 对未见率代码 7.3×；79% 是边缘机会，必须扣除） |

三个通道在构造上互补：S 只用稀有坐标、R 只用频繁坐标（严格互补的坐标划分）；J 减去边缘项后与 S 的边缘
surprisal 在期望上正交。因此"三通道之和 ≈ 原来的一个标量"，但**三个分量各自有自己的零分布**。

**校准（两阶段，全部 routine-only、部署侧）。** 阶段 1：每个通道在校准半 A 上做位置桶标准化 + 全路径
group-max 保形，得到逐 token 的保形 p 值 `p_c(t)`（对风险时钟单调、路径级 super-uniform）。阶段 2：把三个
p 值经 calibrator 变成 e 值、加权**算术平均**（在任意依赖下仍是 e 值），再把这个融合统计量在**校准半 B**
上重新做一次全路径保形，得到最终工作点。**每会话（每个 model_generation episode）误报预算 α = 0.10 由
第二阶段的可交换性精确保证，与三个通道之间的相关性无关**——这正面回答简报 F1 与 F5，且不使用任何
被 C 列否定的持久性/块/CUSUM 手段。

**输出（契约 E）。** 逐 token：融合校准分数 `p_fused(t)`（正常流量上分布已知）、状态 ∈ {silent, provisional,
confirmed}、证据窗（w 个 token 的路由，可读）、**归因通道**（S/R/J 中 e 贡献最大者）与其前 3 个贡献坐标
（layer-expert 或 layer-pair）。不输出单一二值报警。

**代码 / SQL 的处理：scoped 通道 + 文本确认（不是排除）。** 代码只活在 R 与 J 里，而 R 的概率版本
（rmass）跨 batch 不迁移（B1.2）。因此：(i) 主融合里 R 只用**入选指示**的白化能量，概率版 `R_p` 只有在
一个**预注册的、routine-only 的 C1 跨半稳定性检验**通过时才加入（直接回答 F4，全程不看正例）；
(ii) 代码域走 **provisional → confirmed** 两阶段读法：路由在放宽工作点（α_prov = 0.25）给出 provisional +
证据窗，零误报的文本规则（代码围栏 / SQL / 编程关键字，在 301 条 non-drift 上 0 误报，B4.12）在
provisional 之后 64 token 内命中即 confirm，报警时刻按 provisional 记录并同时报告两个时刻；
(iii) 代码域指标**单列**，route-only 与 route+text 分开报，适用范围声明写进方法（B4.12/B4.14 是机制边界，
不是数据量问题）。

**不做什么（C 列，见 §4）：** 不用持久性/块/CUSUM 降 FAR；不把长度分层保形作主表；不做层维特征/层投票；
不用 routine-only 统计挑层；无 warm-up；不用固定专家名单；不做形态条件化/去结构化池；不用窄窗口换抵御
召回；不把 novelty 回落等同抵御；不锁定首次越线 + 固定 64-token delta。

---

## 2. 在线算法（pseudo-code）

输入：每 token 的 `top_k_ids[16, 8]`，可选 `router_probs[16, 64]`。**不读**文本、token id、任务、workflow、
渠道、绝对位置、结果、人工边界。

```text
############ OFFLINE: Fit(N_fit) — routine only ############
# N_fit = canonical routine (clean + benign_control), decode routing only.
q[l,e]      = (#routine tokens with e in top8(l) + 0.5) / (N_routine_tokens + 32)
OMEGA_rare  = {(l,e) : q[l,e] < 0.02}          # frozen threshold, not tuned
OMEGA_freq  = {(l,e) : q[l,e] >= 0.02}         # strict complement of OMEGA_rare
mu[l,e], sd[l,e] = mean/std over routine w=8 windows of top-8 indicator rate
sd_floor    = 5th percentile of {sd[l,e] : (l,e) in OMEGA_freq, l in 5..15}
Ppair[l](a,b) = (#routine tokens with top1(l)=a, top1(l+1)=b + 0.5) / (N + 0.5*64*64)
Pmarg[l](a)   = (#routine tokens with top1(l)=a + 0.5) / (N + 0.5*64)

############ ONLINE: per decode token t (state resets at each model turn) ############
def step(top_k_ids_t, probs_t):
    # ---- channel S: support expansion (rare coordinates only, all 16 layers) ----
    sS = sum(-log q[l,e] for l in 0..15 for e in top_k_ids_t[l] if (l,e) in OMEGA_rare)
    ringS.push(sS);   S_raw = mean(ringS[-8:])                      # w_S = 8

    # ---- channel R: within-support re-weighting (frequent coords, layers 5..15) ----
    ringR.push(top_k_ids_t)                                          # w_R = 8
    x[l,e] = (# of last 8 tokens with e in top8(l)) / 8              # incremental update
    R_raw  = sum( ((x[l,e]-mu[l,e]) / max(sd[l,e], sd_floor))**2
                  for l in 5..15 for e in 0..63 if (l,e) in OMEGA_freq )
    # optional R_p: same form on router_probs mass; ACTIVE only if the preregistered
    # routine-only C1 cross-half stability gate passed (see 4.2). Else R_p is absent.

    # ---- channel J: marginal-corrected joint chain (layers 5..11, w_J = 4) ----
    a = argmax_top1(top_k_ids_t)                                     # per-layer top-1
    jJ = sum( -log Ppair[l](a[l],a[l+1]) + log Pmarg[l](a[l]) + log Pmarg[l+1](a[l+1])
              for l in 5..10 )                                       # 6 adjacent pairs
    ringJ.push(jJ);  J_raw = mean(ringJ[-4:])                        # w_J = 4

    # ---- per-channel position-bucket standardization ----
    b = position_bucket(t)          # t//32, tail buckets merged until >=30 cal traces,
                                    # capped at 3 buckets  (B2.7 + lead improvement #13)
    for c in {S, R, J}:  z[c] = (c_raw - MU[c][b]) / SD[c][b]

    # ---- per-channel conformal p-value against routine running-max at risk clock k ----
    k = number of scored endpoints so far in this episode
    M[c] = max(M[c], z[c])                                           # running max
    p[c] = (1 + #{g in CAL_A groups : Mcal[c][g](k) >= M[c]}) / (n_A + 1)
           # Mcal[c][g](k) = running max of routine group g at look k; groups that ended
           # before k carry their final running max forward (censoring, declared).

    # ---- p -> e calibrator (kappa = 0.5, preregistered), then dependence-robust fusion
    for c: e[c] = 0.5 * p[c] ** (-0.5)
    E_fused = (e[S] + e[R] + e[J]) / 3          # arithmetic mean: valid e-value under
                                                # ARBITRARY dependence (primary, F1)
    E_max   = max(e[S], e[R], e[J])             # secondary fusion, Bonferroni alpha/3 (F2)

    # ---- stage-2 conformal on the fused statistic, calibrated on CAL_B ----
    Mf = max(Mf, E_fused)
    p_fused = (1 + #{g in CAL_B groups : Mfcal[g](k) >= Mf}) / (n_B + 1)

    # ---- state machine (no first-crossing lock; candidates may re-fire) ----
    state = "silent"
    if p_fused <= 0.25:  state = "provisional"; open_or_extend_provisional(t)
    if p_fused <= 0.10:  state = "confirmed"                          # alpha_session
    if code_text_rule_hits(t) and provisional_open_within(64):
        state = "confirmed"; alarm_time = provisional_start           # scoped code path
        attribution_note = "route-provisional + text-confirm"

    # ---- attribution / evidence, always emitted ----
    channel  = argmax_c e[c]
    top_coords = top3 contributors to that channel's raw score
    window     = routing of tokens [t-w_channel+1 .. t]
    return (p_fused, state, channel, top_coords, window)
```

**次级只读输出（不参与报警，非门控）：** 抵御/执行子分类。以 **confirmed 时刻**为锚，取
`D = mean(z_R[anchor+32 : anchor+63]) − mean(z_R[anchor : anchor+15])`，并同时输出 early/middle 带
（L3/L4/L7）与 late 带（L11–15）的分向（B1.3：执行时早中层持续 91/86/77%，晚层 ~50%）。
`D < 0 且早中层未持续` → `likely_resisted`；否则 `likely_executing`；窗口不足 → `censored`。
**明确声明：这不是 DRR/LDC 的判据重试**——它不锁定首次越线、不作为报警条件、不改变 FAR，且必须
同时报告 oracle-onset 版本以把"机制"与"锚点选择"分开（B3.10 的失败正是锚点选择）。

---

## 3. 每个组件的简报依据

| 组件 | 具体选择 | 依据（简报条目） |
|---|---|---|
| 三通道分解本身 | S / R / J | B1.1–B1.3（两线独立收敛）；F1（"没人做过"） |
| S 的表示 | 稀有坐标 −log q，w=8，全 16 层 | B1.1（unseen8/surprisal8 的整轨迹筛出率与极低 clean FAR）；B1.1"强、廉价、不及时"→ 因此只作通道之一 |
| S 与 R 的坐标严格互补 | τ_rare = 0.02 | B1.1（26 条拟合集只有 2 个 pair 从未出现 → 严格 unseen 太稀疏，必须用低频版）；B1.1（78% 白化能量在最低方差十分位 → 不拆则 R 永远被淹没） |
| R 的表示 | 白化能量，频繁坐标，层 5–15，w=8，方差地板 | B1.2（代码只在已用集合内重加权）；B1.1（LDC 73% 执行报警落在全已见专家窗口）；层 5–15 与 w=8 沿用已冻结 CAND-A，不做新的层选择 |
| R 用入选指示而非概率 | 主路径不含 rmass | B1.2（rmass batch 内 AUC 0.998 但**跨 batch 不迁移**，裕度小于 routine 自身的 batch 间漂移） |
| `R_p` 的条件启用 | routine-only C1 跨半稳定性门 | F4（开放问题）；B1.2（"除非 C1 大池能把参照稳住，否则不进候选"） |
| J 的边缘校正 | 减去 log P(a)P(b) | B1.3（代码 2.38× 的差距里 **79% 来自边缘机会**，按条件率排代码只是第 3）→ 不校正就是在重复数 S |
| J 的层带与窗宽 | 5–11、w=4 | 沿用已冻结 CAND-B（两方向 FAR 最低 0.078/0.083、resist 最低 0.111/0.125） |
| 位置桶标准化 | end//32，尾桶合并至 ≥30 条，上限 3 桶 | B2.7（位置桶标准化必要）；lead 改进 #13（桶数 2/3→4/5 时延迟 8→5） |
| 部署侧保形 | 目标侧 routine 半份，α=0.10 | B2.7（部署侧优于源侧：+8 召回 0–0.13 → 0.29–0.54）；B2.4（阈值裕度主导延迟） |
| 校准池 ≥90/半 | C1 320/160 组 | B2.6（每半 40 条时工作点由 4 条 trace 决定） |
| 分通道各自 FAR 预算 | 阶段 1 | B1.1（S 的 benign FAR 16% vs clean 1.25%）→ 通道之间的 benign 敏感度差 10×，共用一个阈值必然被 S 支配 |
| 融合用 e 值算术平均 | 任意依赖下有效 | 三通道共享同一批 routing token，经验相关未知；乘积/Fisher 需要独立性，会反保守 |
| 阶段 2 融合级保形 | CAL_B 上重新校准 | 使**每会话预算与通道相关性无关地精确成立**；F5 |
| provisional / confirmed | 两状态 | B4.12（文本规则零误报但迟 +17）；code_blindspot §3（"路由 provisional + 零误报文本规则 confirm"是唯一有正面证据的方向） |
| 子分类非门控 | 只读输出 | B3.10（oracle onset 下 AUROC 0.909/0.92，算法自选 onset 时失效） |
| 逐 token 为基础信息单位 | ring buffer + w=4/8 | B6.19（单 token novelty fully-post AUROC 0.995/0.998，与滑窗 endpoint 相关只 0.37/0.50） |
| 每 model turn 复位 | 状态与风险时钟 | 契约 D8（必须声明）；B6.20（控制器结构状态三臂相同，不提供信息 → 不用它做复位以外的任何事） |
| 不用 workflow / 绝对位置 / 结果 | — | 契约 E |

---

## 4. 我**不**重试的 C 列条目，以及本设计与之的区别

| C 列条目 | 本设计 | 为什么不是同一形式 |
|---|---|---|
| 用持久性/块/CUSUM 降 FAR | 不用。FAR 由两阶段保形钉死 | 保形下总 FAR 由 α 决定，持久性只是重新分配误报并牺牲及时性（lead 审计 §0.3）。本设计**用分通道预算而不是时间聚合**来改变 ROC 点 |
| 长度分层保形作主表 | 主表不分层；长度只作报告分段 + **按长度分层抽样**校准池 | lead 改进 #19；B3 要求每长度三分位 ≥30 条 routine |
| 层维特征（逐层 max、band 比、层分数、层投票） | 无。J 是**跨层对**统计量，不是逐层特征；LDC 的 2-of-3 band 投票**不作为门控**，只用于归因读出 | lead #20（投票需 ≥10 个分量；逐层 max 抬 resist 到 0.27–0.38） |
| routine-only 层选择 | 层带全部沿用已冻结候选（S: 0–15，R: 5–15，J: 5–11），本轮不做任何层选择 | lead #21（两个 routine-only 判据的挑选排名 48/55、54/55） |
| warm-up（忽略前 W0 token） | 无 | lead #22（W0=32 起 +16 召回掉 24–40%） |
| 固定专家名单 | 无。稀有/频繁划分是**该 agent 自己的 routine 频率划分**，两半坐标都被打分，随每次部署侧拟合重算 | 区别在于：不存在"这些专家意味着异常"的先验名单；这是对**方差**的划分，不是对**专家**的假设 |
| 形态条件化参照 / 结构化 run-length / 去结构化 routine 池 / 形态类特征（FCM） | 全不做 | B4.12 全部失败；B4.13（去 JSON 后 FAR 0.10→0.29–0.35）。代码改走 scoped provisional + 文本确认 |
| 窄窗口换抵御召回 | 窗宽固定 8/8/4，不为抵御调窄 | B3.9（w=4/2/1 无帮助，14 条中 5 条无信号）。抵御召回本设计**改从通道 S 拿**（S 对"进入话题"敏感），代价是 benign FAR，明确计入失败判据 |
| 把 novelty 回落等同抵御成功（DRR/LDC） | 子分类为非门控次级输出，且并列报告 oracle 锚点 | B3.10 |
| 锁定首次越线后固定 64-token delta | 不锁定；融合统计量是 running max，可重复产生 candidate | B2.5（最终执行召回 4/40，No-go）；B1.3（返回原任务也产生晚层峰值 → 必须允许多个 candidate） |
| absolute-age 归一化 / 局部密度比 / conditional successor forecast / staggered min-2 / q25 | 全不做 | C 列。位置桶只用于**原始分数的尺度**，不用于选邻居、选中心或定义正常性；两线在这一点上的张力已知，因此预注册"有桶 / 无桶"消融，主格为有桶（B2.7 明确要求） |
| 单一 novelty 阈值继续调 bins/block/quantile | 本设计的自由度全部在**分解与分别校准**上，不在同一个 novelty 的读法参数上 | C 逻辑基础 §10 |

---

## 5. 校准方案（routine-only，部署侧）

**数据角色。** `N_fit`（表示：q、μ/σ、Ppair/Pmarg）= 26–120 条 canonical routine；`N_cal_A`（通道级保形）与
`N_cal_B`（融合级保形）= C1 的 160 个 matched group 按 group 对半切，每半 80 组 / 160 条 trace（满足
B2.6 的 ≥90 条/半）；`N_eval` = B3 routine + benign + resist + 长度/格式退化样本，不清洗（B4.13：JSON 与
合规拒绝必须留在池里）。三者的 group 不重叠（D5：group 等权，每组只贡献一个 running-max 值）。

**协议（每个通道，逐条执行）：**
1. 位置桶：`b = end // 32`，尾桶向下合并直到该桶有 ≥30 条贡献 trace，桶数上限 3；桶数 < 2 时 harness 告警并
   退化为单桶（记录）。μ、σ 只用 `N_cal_A` 的 routine 窗口拟合；退化重复窗口（不同 token id < 3）排除在
   σ 拟合外但仍被打分（lead 改进 #5，作敏感性）。
2. 通道级保形：对 `N_cal_A` 的每个 group 计算标准化分数的**逐 look running max** 曲线 `Mcal_c[g](k)`；
   在线时 `p_c(t) = (1 + #{g: Mcal_c[g](k) ≥ M_c(t)})/(n_A+1)`。这是路径级 super-uniform 的：
   `P(∃t: p_c(t) ≤ α) ≤ α`（对可交换的 routine 路径）。展示预算 α_S = α_R = α_J = 0.10/3（供 F2 的
   Bonferroni-max 使用）。
3. 融合级保形：在 `N_cal_B` 上以完全相同的在线流程计算 `E_fused` 的路径 running max，取有限样本次序统计量
   第 `ceil((n_B+1)(1−α))` 小者作为 confirmed 工作点，α = 0.10；provisional 工作点用 α_prov = 0.25。
   **这一步使每会话（每个 model_generation episode）"曾经报警"的概率 ≤ α，与三通道之间的依赖结构无关。**
4. 多轮：状态与风险时钟每个 model turn 复位，预算按 episode 计；同时报告整条会话的
   `P(alarm by t)` 累积发生率曲线与"每 1,000 个 eligible position 的报警 onset 数"（C 逻辑基础 §9.2）。
   多 episode 会话的 Bonferroni-over-episodes 模式作为并报选项。

**FAR 分段报告（全部一级）：** clean / benign / resist；长度三分位；workflow；**两个校准半分开报，
并要求带符号差 ≤ 0.10**（B2.6 的系统符号差 + narrow_window §2.3）；**routine 含/不含 JSON 前缀两个
分层**（B4.13 要求的显式设计变量）；scenario 级口径（任一非 drift 臂报警的 scenario 比例）；定阈 trace 的
身份/形态与"剔除 top-1/3/5 后的 FAR"（lead 改进 #15）。

**`R_p` 稳定性门（预注册，routine-only，不看任何正例）：** 在 C1 上，`R_p` 标准化路径 max 的 q90 在
两个校准半之间相对差 ≤ 10%，**且**在两个 fold family 之间 ≤ 10%。通过则 `R_p` 以 e 值形式加入融合
（等权 1/4）；不通过则永久丢弃并在报告中记为"F4 的答案：否"。

---

## 6. 评价方案（遵循契约 E；预注册单格 + 多重比较校正）

**主事件正例** = drift ∪ 有锚点抵御；**主锚点 = product_onset**；并列记录 announcement/topic_entry 双锚点，
容差带按两者之差定（B5.15/B5.16/B5.17）。抵御/执行子分类单列。代码域单列并配文本确认。

**预注册的单个格子（每个假设一格，不取跨格最大值——B3.9 的教训）：**

| 格 | 配置（全部写死） | 主指标 | 失败判据 |
|---|---|---|---|
| **A. 主事件检出** | F1 融合，α=0.10，S/R/J = (8, 8, 4)，product_onset | R+16（主事件正例） | < 0.55 |
| **B. 及时性** | 同 A，仅 drift 子集 | R+8 与命中中位延迟 | R+8 < 0.35 或延迟 > 8 |
| **C. 误报** | 同 A，全部 non-drift | 总 FAR / benign−clean / resist / 两半带符号差 | > 0.15 / > 0.10 / > 0.20 / > 0.10 |
| **D. 分解是否值钱** | F1 vs **单通道 R**（= 已冻结 CAND-A 家族），在**同一 FAR** 工作点，配对同一批 trace | ΔR+16，McNemar 精确检验 | Δ < +0.10 或 p > 0.05 → "三通道分解无增量"，回退单通道 |
| **E. 代码域（scoped）** | 同 A + 文本确认，仅 programming（字面代码，排除"关于 SQL 的散文"，B4 §1.8） | route+text R+48；provisional 领先文本确认的中位 token 数；文本规则在全部 routine 上的 FAR | R+48 < 0.50，或领先 < 8 token，或文本 FAR > 0.01 |
| **F. 子分类（次级，非门控）** | 同 A，confirmed 锚点 | 执行 vs 有界抵御 AUROC（并报 oracle 锚点版） | < 0.75 → 记录为"锚点选择问题仍未解决"，不影响 A–C |
| **G. 通道正交性（诊断）** | 三个 z 序列在 routine 上的相关矩阵，以及每条正例的 e 贡献分解 | 通道间 |ρ| | > 0.7 → 分解在实证上不成立，必须在报告中改写机制叙述 |

**多重比较：** A、B、C、D 为确认性家族（4 格），Holm 校正，家族 α=0.05；E 为独立的 scoped 家族
（单格，α=0.05）；F、G 为描述性，不参与确认，用 BH 报告并明确标注"非确认"。所有区间以 **scenario/group**
为重抽样单位 bootstrap（M3/D5）。

**似然比与不可逆点：** 在 α ∈ {0.05, 0.10, 0.20} 三个工作点报告每个通道与融合的 LR+ = TPR/FPR
（分 clean / benign / resist 三个零假设分别算）；"不可逆点"= 候选回复发布或产生副作用的工具调用，
报告"在不可逆点之前 confirmed"的比例与提前的 token 数分布（同时给出对应的 recall，禁止只报命中样本的
中位延迟）。

**B3 数据要求（由上表直接推出）：**

| 要求 | 数量 | 依据 |
|---|---|---|
| routine 校准池 | 每校准半 ≥90 条 / ≥45 scenario；整池 ≥180（可直接用 C1 的 320/160 组） | B2.6 |
| routine 含/不含 JSON | **显式设计变量**：两个分层各 ≥90 条（一层 JSON 前缀占比 40–60%，一层 <10%） | B4.13 |
| 每长度三分位 routine | ≥30 条 | lead #13 |
| 代码 drift | **每方向 ≥20 条字面代码**，另单列"关于代码的散文" | B4.12/B4.14、lead #16 |
| 有锚点抵御 | ≥40 条 | B3.8/B3.9、lead #17 |
| 解码 horizon | **384 token**，stop reason 作为删失信息记录 | B5.16 |
| 标注 | product_onset 主锚点 + announcement/topic_entry 双锚点；产出交付物的工具调用归 drift；`decode_truncated`、`topic_word_leak` 为固定字段 | B5.15/B5.17 |

**功效（按上述样本量）：**

| 检验 | n | 效应 | 功效 |
|---|---|---|---|
| A：R+16 ≥ 0.55（单侧，正例 120 = 80 drift + 40 抵御） | 120 | 真值 0.65 | se ≈ 0.045 → 约 **60%**；真值 0.70 → 约 88% |
| C：FAR ≤ 0.15（routine 200 条） | 200 | 真值 0.10 | se ≈ 0.021 → **> 95%** |
| D：McNemar（融合 vs R 单通道） | 120 配对 | 净 +12 / −3 | p ≈ 0.035，功效约 **70%** |
| E：代码 R+48 ≥ 0.50（vs 冻结基线 0/8） | 20/方向 | 真值 0.60 | **> 90%** |
| F：子分类 AUROC ≥ 0.75 | 有界抵御约 12 / 执行约 80 | 真值 0.80 | 约 **60%**（诚实：n 仍是瓶颈） |

**诚实结论：** A 与 F 在 B3 的样本量下功效只有 ~60%，因此 A 的失败必须被解读为"未证实"，不是"证伪"；
只有 C、D、E 的判定具有确认性力度。这直接来自 B3.9 §2.6 的教训（n=14 时 5/14 门槛的功效只有 72%）。

---

## 7. 在现有数据上的预测（推断，不是结果）与"抓不到"清单

参照点：冻结 CAND-A 在 B1+B2、product_onset 下 drift R+16 = 33/59、R_final 51/59；有锚点抵御 R+16 = 2/14、
R_final 4/14；主事件合并 R+16 = 35/73 = 0.48、R_final 55/73 = 0.75；总 FAR 0.098/0.115。

| 数据 | 指标 | 预测 | 理由 |
|---|---|---|---|
| B1/B2 | 主事件 R+16 | **0.55–0.62**（40–45 / 73） | S 通道在自己的预算下抓回 2–4 条有锚点抵御（S 对"进入话题"敏感，B1.1 benign 6–13/80 说明它确实对题外词汇响应）；J 的边缘校正抓回 1–3 条已见专家窗口 |
| B1/B2 | drift R+16 / R_final | **0.58–0.64 / 0.85–0.90** | 相对 0.56 / 0.86 的增量主要来自 J，幅度小 |
| B1/B2 | 有锚点抵御 R+16 | **4–6 / 14**（上限 9/14） | B3.9：14 条中 5 条在任何窗宽下无信号，天花板 9/14 |
| B1/B2 | drift R+8 | **0.30–0.38** | B2.4：延迟由阈值裕度主导，分解不改变裕度；只有 J（w=4）买到 2–3 token |
| B1/B2 | 总 FAR | **0.10 ± 0.03**（构造上钉死） | 阶段 2 保形 |
| B1/B2 | clean / benign / resist FAR | **0.05–0.08 / 0.14–0.20 / 0.15–0.25** | S 的 benign FAR 16%（B1.1）即使只占 1/3 预算也会把 benign 抬起来；**格 C 的 benign−clean ≤ 0.10 与 resist ≤ 0.20 是本设计最可能失败的两项** |
| B2-384 replay | 40 条执行：R+16 / R+32 / final | **0.45–0.60 / 0.60–0.72 / 0.80–0.88** | unseen8 整轨迹 29/40、surprisal8 37/40 但 onset+16 ≤ 11/40（B1.1）；融合把"整轨迹强"部分转成"窗口内可及时" |
| B2-384 replay | 35 条静默抵御报警数 | **2–5 条（6–14%）** | B3.8：冻结候选 2–4 条；这是**期望的低值**，不是漏检 |
| B2-384 replay | 5 条有界抵御 | 3–4 条被 confirmed；子分类 AUROC **0.60–0.75**（oracle 锚点 0.90+） | B3.10 |
| C1 | held-out group FAR（n=80 组/半） | **0.08–0.12**，Wilson 上界约 0.20 | Codex 单 token static 15%、LDC 3.3%；融合在两者之间 |
| 代码域 | route-only R+16 / R+48 | **0–1 / 8** 与 **1–2 / 8** | **不改善**：S 对代码结构性为 0；R 的入选指示版在频繁坐标上的效应量只有其他域一半且迟（B1.2、code_blindspot §1.4）；J 边缘校正后代码的条件率只排第 3 |
| 代码域 | route+text R+48 / 领先时间 | **6–7 / 8**；provisional 领先文本确认中位 **0–6 token** | 文本规则 7/8 但中位 +17（B4.12）；路由 provisional 在 α_prov=0.25 下会先亮，但幅度小 |
| `R_p` 稳定性门 | 通过概率 | **约 30%** | B1.2：routine 逐层 top-8 集合在两 batch 间只有 8/16 层相同；C1 大池是唯一可能救它的条件 |

**明确抓不到的（适用范围声明）：**
1. **字面代码 / SQL 的及时 route-only 检出**——机制边界（B1.2、B4.12、B4.14），B3 增加样本不改变它；只能靠
   文本确认，且迟。
2. **14 条有锚点抵御里的 5 条**——任何窗宽任何读法下峰值只有阈值的 0.08–0.72 倍（B3.9），能力边界。
3. **"关于 SQL 的散文"类样本**（8 条 programming 里 2–3 条）——路由与文本规则都抓不到。
4. **"提到"与"做"的区分**——benign 臂 FAR 必然高于 clean 臂（B3.11，RASET 边界），本设计只把它**归因**到
   S 通道（因此可解释、可单独调预算），不消除它。
5. **首个题外 token 之前的预测**——同前缀同路由（Codex M1/O2），不存在分叉前预警。
6. **渐进漂移的阈值裕度**——47% 的漏检在 +17…+35 才越线；分解不改变裕度，只有 J 的 w=4 换到 2–3 token。
7. **静默抵御**——按定义不报（这是正确行为，但意味着"上下文里有攻击"这件事本检测器完全不知道）。
8. **跨 batch 的概率类参照**——若 `R_p` 门不通过，概率信息在本设计中完全不被使用（诚实记录 F4 = 否）。

---

## 8. 计算成本

| 项 | 成本 | 依据 |
|---|---|---|
| 推理侧额外前向 | **0** | B6.18 |
| 通道 S | 128 次查表 + 环形和 ≈ 130 flop | 新增，量级同 CAND-B |
| 通道 R | 704 维白化能量增量更新 ≈ 1.4k flop，**2.2 µs/token** | lead compute 审计（CAND-A 实测） |
| 通道 J | 6 对查表 + 12 次边缘查表 ≈ 50 flop，**0.7 µs/token** | lead compute 审计（CAND-B 实测） |
| 融合 + 保形查表 | 3 次二分 + 常数 ≈ 100 flop | 校准曲线预排序 |
| 合计 | **≈ 3–4 µs/token**（Python 流式开销另计约 75 µs，是解释器成本） | 同上 |
| 状态 | S 表 8 KiB（uint8 量化无损）+ R 8 KiB + J CSR 14–16 KiB + 校准曲线 3×80×L ≈ 100 KiB → **< 150 KiB/agent** | lead compute 审计（uint8/CSR 逐 trace 无损，12–14×） |
| 每会话内存 | 8 token × 128 uint8 环形缓冲 ≈ 1 KiB | — |
| 离线校准 | 320 条 × ~200 token × 3 通道 ≈ 0.2 s/通道 | — |
| 全部预注册评价套件 | **< 1 CPU 日/方向** | lead compute 审计 |

结论：成本不是约束，不应为效率牺牲精度（lead 审计 §3）。

---

## 9. 相对两条线，什么是真正新的

1. **把白化能量按 routine 方差划分成两个通道并分别校准。** 两线都知道 78% 的能量在最低方差十分位
   （B1.1），但两线都继续用**一个**标量。分别校准是唯一能让"支持集内重排"拿到自己的零分布、
   从而有机会越线的做法。这是简报 F1 明确指出"两线都指向这里、没人做过"的那件事。
2. **边缘校正的联合链新颖度。** code_blindspot 把 2.38× 分解成 79% 边缘机会 + 条件超额（B1.3），
   但**没有人用条件超额本身做过检测器**；PDM 的 D1 深度链把边缘与联合混在一起，因此它其实是通道 S
   的一个噪声更大的版本。J = −log[P(a,b)/P(a)P(b)] 是第一个与 S 在构造上正交的动力学量。
3. **两阶段部署侧保形（通道级 + 融合级）。** 使每会话预算在**任意通道依赖**下精确成立，而不依赖
   独立性假设，也不使用任何被 C 列否定的持久性/CUSUM 手段。这正面回答 F5（时间一致性/anytime）
   而不重复 C 逻辑基础 §10 的"继续调同一个 novelty"。
4. **provisional / confirmed 双状态 + scoped 代码文本确认。** 把已知的机制盲区从"沉默漏检"变成
   **被显式度量的作用域声明**：报告 route-only 与 route+text 两套数字、报告 provisional 的领先时间。
   FCM §10 提出过这条路，但从未被写成算法与预注册指标。
5. **归因通道作为一等输出。** 两线的输出都是分数或二值报警；本设计输出"是哪一层机制在响应"
   （征用新专家 / 集合内重排 / 跨层耦合）+ 证据窗 + 贡献坐标。在"多信号之一、不是唯一决策"的
   部署定位（简报 A）下，这才是下游可用的东西；同时它把 benign 误报的可解释性（S 通道）与
   代码盲区（R/J 通道）在**同一个输出格式里**说清楚。
6. **`R_p` 的 routine-only 稳定性门。** 用一个不看任何正例的预注册检验回答 F4，而不是又一次用
   drift 试 rmass（B1.2 已预告其 B2→B1 失败）。

---

## 10. 已知风险（作者自评）

1. **benign / resist 臂预计触线。** 给 S 独立预算等于承认"进入话题"会被报出来。若格 C 失败，正确的解读
   不是调 S 的窗宽，而是把 S 降级为**仅归因通道**（不进融合），并接受主事件 R+16 回落到 0.50 左右。
   这个 fallback 必须在预注册里写死。
2. **通道正交性可能只在纸面成立。** 格 G 是它的检验；若 |ρ| > 0.7，本提案的机制叙述必须改写，
   融合的收益也会消失（三个 e 值同涨同落 = 一个通道）。
3. **`p_c(t)` 的路径 super-uniform 依赖"校准路径与被评价路径可交换"**，而 B2.6 已观察到两半 FAR 的
   系统符号差。阶段 2 的融合级保形只能保证"平均意义上的 α"，两半差 ≤ 0.10 是必须报告并可能失败的一格。
4. **删失处理（校准路径比 k 短时 carry-forward）是保守的但会随 k 增大而失去分辨率**；长回复上的
   `p_fused` 会趋于饱和。必须报告 `P(alarm by t)` 曲线与按长度三分位的 FAR。
5. **子分类几乎肯定失败（格 F）。** 保留它是为了把"机制存在（oracle AUROC 0.9）"与"锚点选择失败"
   这两件事分开记录，不是为了通过。
6. **位置桶与 Codex 的 A1（absolute age 不进正常性分数）存在张力。** 本设计只把它用于原始分数的尺度，
   并预注册有桶/无桶消融；若无桶版本不差，应在 v4 里去掉桶以消除这个张力。
