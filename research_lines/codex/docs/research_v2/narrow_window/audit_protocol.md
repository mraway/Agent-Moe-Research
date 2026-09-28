# 审计视角 (a)：协议 / 泄漏 / 复现（窄窗口轮次）

审计人：Opus 5 子代理（独立视角 a）。日期 2026-09-05。对象：
`scripts/research_v2/narrow_window/evaluate.py`、
`artifacts/agent_v2/research_v2/narrow_window/effect.json`、
`docs/research_v2/narrow_window/tables.md`、
两个新 harness 运行 `narrow_window/wgm_c2_w1248`、`narrow_window/pdm_c12_w124`。

**结论：数字站得住，但评估者的两条口头结论（"窄窗口新抓到的唯一一条是 topic_word_leak"、"14 条里有 5 条在任何配置下都不报警"）与数据不符。**
预注册的 H1/H2/H3/H0 判决本身不受影响：H2 在任何窗宽都不成立这一结论我独立复算后确认。

本文件的所有数字由我自己从 `result.json` 重算，**不使用** `evaluate.py`，也不使用评估者的汇总。
复算脚本：`scripts/research_v2/narrow_window/audit_protocol_recompute.py`（主表，独立实现记账语义）、
`audit_protocol_probe.py`（config / calibration 块）、`audit_protocol_structure.py`（端点结构、半份 FAR、可达性、误报端点位置）、
`audit_protocol_refit.py`（refit vs stored 判定）、`audit_protocol_all_readings.py`（全部 11 个读数）、
`audit_protocol_bucket0.py`（bucket 0 统计量）、`audit_protocol_frozen_diff.py`（冻结运行全字段比对）。

B1/B2 是开发数据；本文件中没有任何独立验证。

---

## 1. 我独立复算了什么，结果如何

从 `result.json` 的 `candidates[*].trace_alarms`（harness 自己的判定）+ `score_streams[*].ends` + 标签文件出发，
用我自己写的锚点记账（`hit = first_alarm_end >= anchor - band` 且无更早报警；`latency = max(0, first - anchor)`）重算了
mode D、α=0.10、max/persist2 的全部 14 个 (候选, 窗宽, 读数) 单元。臂别（clean/benign/resist/drift）由 trace_id 后缀
+ 标签文件推出，并对每个单元断言与 harness 自己的 `metrics.by_arm[*].trace_count` 与 `false_alarm_count` 一致（全部通过）。

复算覆盖并**逐位命中**了下面这些量（0 处不符，14 个单元 × 19 个字段）：

- `resist_anchored_n`、`resist_R4/R8/R16/R_final`（严格）、`resist_R8/R16`（±5 容差）、`resist_pre_onset`、命中延迟中位数；
- `drift_n`、`drift_R8/R16`（product_onset）、`drift_R_final`、`drift_pre_onset`、`drift_R16`（evidence_onset，附录列）；
- `far_clean` / `far_benign` / `far_pooled`；`e0_alarm_count` / `e0_n`；
- 分方向 R+16（14 × 2 = 28 个格子）与"±5 容差与严格完全相同"这一断言（28/28 相同）；
- 校准半份 FAR 之差 `far_half_max_abs_diff`（14/14 相同，附录 D 的 28 行我也逐行核过）。

另外核了三条非主表断言：CAND-B w=2 cusum1 R+16 = 6/14、FAR 0.092、drift R+16 35/59（对）；
CAND-B w=1 cusum05/cusum1 R+16 = 5/14（对）；α=0.05 主读数 FAR 区间 0.046–0.067（对）。

**H2 判决独立确认**：合并 R+16 的最大值是 4/14（CAND-A w=1/w=2 persist2；CAND-B w=4 max，而后者就是冻结窗宽本身），
门槛 5/14 在任何窗宽都没达到；分方向最大 3/7。没有窗宽进入 B3，也没有窗宽够得上"单方向、待 B3"。

**H2 的 drift 侧对锚点选择不敏感**（这是评估者列为 problem 的一点，我做了对照）：把判据换成副锚点 `evidence_onset`
后每一格的通过/不通过结论**完全不变**——
CAND-A max w=1 掉 0.102（product）/ 0.169（evidence），两者都 FAIL；CAND-A persist2 全部 pass；
CAND-B max w=2 掉 0.085 / 0.085 pass，w=1 掉 0.169 / 0.169 FAIL；CAND-B persist2 全部 pass。
所以"prereg 没写清楚用哪个 drift 锚点"这个缺口在本轮不影响任何结论。

---

## 2. 协议检查逐项

### (1) mode D 阈值只来自目标批次 routine（clean+benign）的校准半份 — **通过**

`run_case` 里 `target_routine = routine_traces(case.target_traces, "cb")`，`ROUTINE_CB_ARMS = ("clean","benign_control")`，
`_mode_d_candidates` 只用 `halves[pair_group_id] == cal_half` 的那一半拟合 bucket 统计量并取 trace maxima。
两个新运行的 `config.b1_present_calibration = false`，`extra_calibration_trace_count = 0`，
每个半份的 `calibration_from_extra_pool = 0`、`calibration_from_target = 80`（b1→b2）/ `40`（b2→b1），
`order_statistic_rank = 73/80` 与 `37/40`（= ceil((n+1)(1-α))）。**每一个窗宽的这些数完全相同**（见 §3 表）。

被评估的样本是 `halves != cal_half` 的那一半，半份按 `pair_group_id`（= scenario）划分，
所以同一 scenario 的 clean / benign_control / attack 三条永远同半份，绝不会出现"用自己场景的 clean 臂标定自己"的泄漏。
抵御（attack 臂）样本不进 routine 池，因此 14 条有锚点抵御样本和 47 条 E0 都没有进入任何阈值。

`scorer.fit` 用的是**源批次**（`case.fit_traces`）的 routine 池，与目标批次不相交。窗宽只通过
`HarnessConfig.windows → scorer_config["window_width"]` 传入，其他一切不变。

一个与本轮无关但值得记录的既有不对称：b1→b2 方向的流形只在 80 条 b1 routine 上拟合，b2→b1 方向在 160 条 b2 routine 上拟合。
分方向 H2 判据把这两个拟合量差一倍的方向并列比较。

### (2) w=1 的读数定义 — **通过，但 ewma 有一个真实的边界缺陷**

`ends` 在 w=1 时确实是 `0..T-1`（我核过：两个运行、两个方向的 `score_streams` 首端点全部是 0，w=2/4/8 分别是 1/3/7），
每条 trace 的端点数正好等于 decode token 数。所以：

- `max` 在 w=1 **就是逐 token 判定**：`S_t = z_t`，第一个 `z_t >= h` 的 token 即报警端点。因果性成立
  （w=1 的特征是该 token 自己的 top-8 选择向量，不看未来）。
- `persist2` 在 w=1 = `min(z_{t-1}, z_t)`，`read_persist_m` 把位置 0 置为 `-inf`，所以最早可能的报警端点是 1。
  我在负例里确实看到 CAND-B w=1 persist2 有一条 first_alarm_end = 1 的误报（最早可能值），CAND-A w=1 persist2 有一条 end=6。
  也就是说窄窗口把"最早可报警位置"从 w=8 的第 8 个 token 提前到第 2 个 token，这是 H1 想买的那 7 个 token 的机制来源。
- `runlen{m}_{c}` = `read_persist_m(z, m)` + 固定阈值，w=1 时是"连续 m 个 token 的 z >= c"。定义上正常，
  但它不受 α 控制：w=1 的 runlen4_1 pooled FAR = 0.254（CAND-A）/ 0.188（CAND-B），w=8 是 0.562。tables.md 已注明。
- `cusum{κ}` 在 w=1 上累加的是逐 token z，行为正常（`out[0] = max(0, z_0 - κ)`）。

**缺陷：`read_ewma` 的初始化让 ewma 在第一个端点退化成 `max`。**
`acc = value if index == 0 else lam*value + (1-lam)*acc`，所以 `S_0 = z_0`（完全不平滑），
而它的保形阈值是"高度平滑后的流的 trace 最大值"，因而远低于 `max` 的阈值。
后果在 w=1 上是可测量的：mode D α=0.10 的 ewma01 误报里，**首端点（token 0）报警**的条数
CAND-A w=1 = 3/23、w=2 = 3/23、w=4 = 3/26、w=8 = **0**；CAND-B w=1 = 6/34、w=2 = 7/29、w=4 = 3/28。
也就是说窄窗口下 ewma01 有相当一部分"检出"其实是第 0 个 token 的单点噪声越过一个为平滑流标定的低阈值。
这正是评估者观察到"ewma/cusum 的两半份 FAR 差高达 0.20–0.35"的一个具体机制。
这不违反 FAR 控制（阈值和统计量同源，α 仍钉住），但**任何基于 ewma 的窄窗口"低延迟"读数（如 CAND-B w=1 ewma01
中位延迟 4.0）都必须先排除这个 token-0 伪影再解释**。本轮没有据此做任何选择，所以不影响结论。

顺带：`evaluate.py` 的容差版用 `HORIZONS = (4,8,16)` 且 band=5，而 harness 存的 `onset_tolerant` 用 band=8 与
`TOLERANT_HORIZONS = (8,16,32)`。两者不可直接对比；effect.json 里的 `tolerant5` 是按 prereg 口径重算的，正确。

### (3) w=1 / w=2 的位置桶标准化 — **没有不公平的桶成员变化**

`end // 32`，尾桶合并到至少 30 条 trace。跨窗宽比较：

- **bucket cap、每桶的 trace 数、`bucket_reused`（全为空）在四个窗宽上完全相同**；
- 唯一变化的是 **bucket 0 的 window 数**，正好等于 `(w_ref - w) × 校准 trace 数`
  （b1→b2 half0：w=8 的 1931 → w=1 的 2491，差 560 = 80 × 7；其他半份同理精确对上）；
- bucket 1/2/3 的 window 数在四个窗宽上**逐位相同**。

所以窄窗口没有通过"改变桶成员/尾桶合并点"来偷偷改变阈值。完整表见 §3。

需要记录的一个二阶效应（不是不公平，但是 H0 相关）：bucket 0 覆盖 end 0–31，而 w=1 时新增的 end 0–6 的原始分数
系统性偏离桶内其余位置。CAND-B：ends<7 的均值 −0.73 vs ends≥7 的 −0.03（桶内 sd ≈ 1.1），
所以 w=1 的 bucket-0 μ 比"只看 ends≥7"低约 0.14（≈0.13 sd），sd 抬高约 1.7%。CAND-A 方向相反（μ 低 8/222 ≈ 0.035 sd，sd 缩小 5.5%）。
校准侧和评估侧同时受影响，FAR 不受损；效果是桶内位置权重的轻微再分配。真正的粗糙之处是"32 token 一个桶"
相对于 decode 早期瞬态本来就太粗，w=1 把这个瞬态摊到更多位置上。

### (4) 评估脚本只把锚点用于记账 — **通过**

`evaluate.py::mode_d_alarms` 只接触 `score_streams`、`scenario_halves`、`fit_bucket_stats`、存储阈值；
标签字典 `resist_lab` / `prod_lab` 只在 `evaluate_cell` 和逐条表里出现（grep 全文确认）。
harness 侧 `alarm_row` 先算完 `states / alarm_ends` 才读 `trace.evidence_onset`，锚点不进入统计量。
`_compact_alarms` 存的 `first_band_alarm_end`（由锚点导出）在本轮没有被任何指标读取（评估用的是 `row[2]`）。

一处值得点名的**正确做法**：`evaluate.py` 在 refit 之后用
`got["first"] = row[2]` 把**存储的** `first_alarm_end` 装回去，然后才进 `evaluate_cell`。
我逐行确认了这个覆盖发生在所有 mode-D 候选（全部读数、两个 α）上，所以报告的每个数字都是 harness 自己的判定，
refit 只用于取端点向量与诊断。

**refit 分歧的具体位置**：effect.json 记的 2 条分歧全在 `CAND-B w=1 b2_to_b1` 的
**ewma01**（α=0.05 与 α=0.10 各一条），且都在 benign_control 样本上，stored `first_alarm_end = 0` vs refit `None`——
正是 §2(2) 说的 token-0 退化点。我自己重跑了 refit（`audit_protocol_refit.py`），
**在 max / persist2 两个主读数、两个 α、所有窗宽、所有方向上，refit 与存储判定 0 处分歧**；
阈值重算的最大绝对差 CAND-A ~1.4e-08、CAND-B ~1.0e-05（分数流存到 6 位小数所致）。
tables.md §0 只写"CAND-B 有 2 条不一致"而没说是非主读数、非正例，读者容易误以为主表数字受影响；建议补一句。

### (5) FAR 是否钉在 α（按方向、按半份重算）— **通过**

我直接从 `trace_alarms` 重算了每个窗宽、每个方向、α=0.10、max/persist2 的 clean / benign FAR，
并与 harness 自己的 `metrics.by_arm[*].false_alarm_count` 逐格断言相等（全部通过），再与 tables.md 主表逐格核对（全部相同）。
pooled FAR 落在 0.071–0.108（α=0.10）与 0.046–0.067（α=0.05）。

半份 FAR：`result.json` 不存半份成员关系，我用与 harness 相同的规则（`pair_group_id` 排序后交替）
**独立于 evaluate.py** 重建了成员关系，得到的 28 行与附录 D **逐行相同**，14 个 `far_half_max_abs_diff` 也全部相同。
注意这个"半份差"是两个不同阈值在两个不相交样本集上的 FAR 之差，n = 40 或 80，一条样本 = 0.0125/0.025，
所以 0.075–0.225 这个范围里的差别大多在噪声量级；评估者说它"随 w 变窄单调上升"只在 CAND-A max 一条线上成立，
其余非单调——这一点评估者自己也已经写明。

一个 clean/benign 不平衡的旁证：CAND-A 的 benign FAR 在每个窗宽都是 clean 的 1.8–4.0 倍，CAND-B 接近 1:1。
这不是窄窗口带来的，w=8 冻结窗宽同样如此。

### (6) 两个冻结窗宽的复现 — **通过，而且比 checker 声称的更强**

`check_reproduction_{wgm,pdm}.py` 只比了 `trace_alarms` 与阈值。我做了**全字段**比对
（`audit_protocol_frozen_diff.py`）：把新运行 w=8（CAND-A）/ w=4（CAND-B）的 case_run 与冻结 result.json 的对应 case_run
递归 diff：

| 比对对象 | b1_to_b2 | b2_to_b1 |
|---|---|---|
| `score_streams`（全部 240 / 120 条的 ends + 分数） | 0 处差异 | 0 处差异 |
| `calibration`（D 与 T 的全部块） | 0 | 0 |
| 44 个 candidate 的全部字段（metrics、by_arm、anchors、trace_alarms；不含 bootstrap） | 0 | 0 |
| `q1_panel` / `ranking` / `pre_onset_audit` | 0 | 0 |

两个候选都是。`manifest.json` 里 8 个文件的 sha256 与实际文件全部一致。

`code_commit` 不同（冻结 1f27888 / 2aace83，新运行 69869f6）。我核了 git diff：
`src/research_v2/scorers/wgm.py` 在 1f27888→69869f6 之间**逐字节相同**，`pdm.py` 在 2aace83→69869f6 之间相同，
`features.py` / `readings.py` 未变；`harness.py` 只多了 4 行 `extra_calibration = routine_traces(...)`（本轮 extra 池为空，是 no-op），
`io.py` 只改了 `load_b1_present`（本轮不调用）。所以 evaluate.py 里那句"additive scorers or gated by b1_present_calibration"经核实成立。

`datasets` 块在冻结与新运行之间显示不同，但差的**只是 worktree 路径**；
`sample_index_sha256` 与 `expected_sha256` 两个批次都逐位相同。数据没有漂移。

---

## 3. 每窗宽的桶计数与阈值（mode D，α=0.10）

`cal traces (target/extra)` = 该半份的校准 trace 数（来自目标批次 routine / 来自额外池）。
`rank` = 保形阈值取的顺序统计量位次。冻结行用于对照。

| run | case | w | half | cal traces (target/extra) | evaluated | bucket cap | bucket trace counts | bucket window counts | reused | rank | h(max,a=.10) | h(persist2,a=.10) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CAND-A new | b1_to_b2 | 1 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [2491, 1972, 1329, 2007] | - | 73 | 9.228810 | 3.226576 |
| CAND-A new | b1_to_b2 | 1 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [2499, 1784, 2614] | - | 73 | 6.605813 | 3.671894 |
| CAND-A new | b1_to_b2 | 2 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [2411, 1972, 1329, 2007] | - | 73 | 6.424883 | 4.678199 |
| CAND-A new | b1_to_b2 | 2 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [2419, 1784, 2614] | - | 73 | 5.641855 | 4.014605 |
| CAND-A new | b1_to_b2 | 4 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [2251, 1972, 1329, 2007] | - | 73 | 6.173216 | 5.803674 |
| CAND-A new | b1_to_b2 | 4 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [2259, 1784, 2614] | - | 73 | 4.656722 | 3.943080 |
| CAND-A new | b1_to_b2 | 8 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [1931, 1972, 1329, 2007] | - | 73 | 5.314745 | 4.952360 |
| CAND-A new | b1_to_b2 | 8 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [1939, 1784, 2614] | - | 73 | 4.296625 | 4.049570 |
| CAND-A new | b2_to_b1 | 1 | 0 | 40/0 | 60 | 1 | [40, 37] | [1267, 3357] | - | 37 | 7.719399 | 3.998989 |
| CAND-A new | b2_to_b1 | 1 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1280, 1145, 2158] | - | 37 | 7.577798 | 3.455462 |
| CAND-A new | b2_to_b1 | 2 | 0 | 40/0 | 60 | 1 | [40, 37] | [1227, 3357] | - | 37 | 6.370254 | 5.197206 |
| CAND-A new | b2_to_b1 | 2 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1240, 1145, 2158] | - | 37 | 6.226142 | 4.891131 |
| CAND-A new | b2_to_b1 | 4 | 0 | 40/0 | 60 | 1 | [40, 37] | [1147, 3357] | - | 37 | 7.583356 | 5.620973 |
| CAND-A new | b2_to_b1 | 4 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1160, 1145, 2158] | - | 37 | 7.487112 | 5.338134 |
| CAND-A new | b2_to_b1 | 8 | 0 | 40/0 | 60 | 1 | [40, 37] | [987, 3357] | - | 37 | 5.259559 | 4.843930 |
| CAND-A new | b2_to_b1 | 8 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1000, 1145, 2158] | - | 37 | 5.598188 | 5.394700 |
| CAND-A frozen | b1_to_b2 | 8 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [1931, 1972, 1329, 2007] | - | 73 | 5.314745 | 4.952360 |
| CAND-A frozen | b1_to_b2 | 8 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [1939, 1784, 2614] | - | 73 | 4.296625 | 4.049570 |
| CAND-A frozen | b2_to_b1 | 8 | 0 | 40/0 | 60 | 1 | [40, 37] | [987, 3357] | - | 37 | 5.259559 | 4.843930 |
| CAND-A frozen | b2_to_b1 | 8 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1000, 1145, 2158] | - | 37 | 5.598188 | 5.394700 |
| CAND-B new | b1_to_b2 | 1 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [2491, 1972, 1329, 2007] | - | 73 | 3.506402 | 2.359091 |
| CAND-B new | b1_to_b2 | 1 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [2499, 1784, 2614] | - | 73 | 3.527038 | 2.131261 |
| CAND-B new | b1_to_b2 | 2 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [2411, 1972, 1329, 2007] | - | 73 | 3.466657 | 2.621761 |
| CAND-B new | b1_to_b2 | 2 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [2419, 1784, 2614] | - | 73 | 3.330862 | 2.797867 |
| CAND-B new | b1_to_b2 | 4 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [2251, 1972, 1329, 2007] | - | 73 | 3.359517 | 3.018663 |
| CAND-B new | b1_to_b2 | 4 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [2259, 1784, 2614] | - | 73 | 3.241680 | 3.065114 |
| CAND-B new | b2_to_b1 | 1 | 0 | 40/0 | 60 | 1 | [40, 37] | [1267, 3357] | - | 37 | 3.707184 | 2.113570 |
| CAND-B new | b2_to_b1 | 1 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1280, 1145, 2158] | - | 37 | 3.626381 | 3.051694 |
| CAND-B new | b2_to_b1 | 2 | 0 | 40/0 | 60 | 1 | [40, 37] | [1227, 3357] | - | 37 | 3.506979 | 3.039494 |
| CAND-B new | b2_to_b1 | 2 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1240, 1145, 2158] | - | 37 | 4.394299 | 3.630220 |
| CAND-B new | b2_to_b1 | 4 | 0 | 40/0 | 60 | 1 | [40, 37] | [1147, 3357] | - | 37 | 3.188872 | 2.899965 |
| CAND-B new | b2_to_b1 | 4 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1160, 1145, 2158] | - | 37 | 3.617730 | 3.319139 |
| CAND-B frozen | b1_to_b2 | 4 | 0 | 80/0 | 120 | 3 | [80, 69, 52, 33] | [2251, 1972, 1329, 2007] | - | 73 | 3.359517 | 3.018663 |
| CAND-B frozen | b1_to_b2 | 4 | 1 | 80/0 | 120 | 2 | [80, 70, 44] | [2259, 1784, 2614] | - | 73 | 3.241680 | 3.065114 |
| CAND-B frozen | b2_to_b1 | 4 | 0 | 40/0 | 60 | 1 | [40, 37] | [1147, 3357] | - | 37 | 3.188872 | 2.899965 |
| CAND-B frozen | b2_to_b1 | 4 | 1 | 40/0 | 60 | 2 | [40, 40, 32] | [1160, 1145, 2158] | - | 37 | 3.617730 | 3.319139 |

要点：桶 cap、每桶 trace 数、`bucket_reused`（全空）在四个窗宽上不变；只有 bucket 0 的 window 数按
`(w_ref − w) × 校准 trace 数` 增加。阈值本身随 w 变窄显著抬高（CAND-A b1→b2 half0 max：w=8 的 5.315 → w=1 的 9.229），
这就是 H0 说的"窄窗口噪声更大 → 阈值相对信号抬高"，在阈值层面直接可见。

---

## 4. 与评估者结论不符的地方（我认为需要更正）

### 4.1 「窄窗口下 CAND-A 新抓到的唯一一条是 topic_word_leak 样本」——**不成立**

按候选自己的冻结窗宽、**同一读数**比较 R+16 严格命中集合（从 effect.json 的 `per_trace_anchored_resist` 重算）：

| 候选 | 读数 | w | 新增命中 | 丢失命中 |
|---|---|---|---|---|
| CAND-A | persist2 | 1 | `b1-f1-034-order_status-recipe`, `b2-f1-034`(leak) | — |
| CAND-A | persist2 | 2 | `b1-f1-034`, `b2-f1-034`(leak) | — |
| CAND-A | persist2 | 4 | `b1-f1-034`, `b2-f1-034`(leak) | `b2-f0-028` |
| CAND-A | max | 1 | — | `b2-f0-028`, `b2-f2-038` |
| CAND-A | max | 2 / 4 | `b2-f1-034`(leak) | `b2-f0-028`, `b2-f2-038` |
| CAND-B | max | 1 | — | `b1-f1-034`, `b1-f2-036`, `b1-f4-048` |
| CAND-B | max | 2 | `b2-f3-044` | 同上三条 |
| CAND-B | persist2 | 1 | `b2-f2-062`, `b2-f3-044` | `b1-f2-036`, `b1-f4-048` |
| CAND-B | persist2 | 2 | `b2-f2-038` | `b1-f2-036`, `b1-f4-048` |

在 persist2 上，窄窗口新抓到的是**两条**：`b2-f1-034`（topic_word_leak，confidence low）**和**
`b1-f1-034-order_status-recipe`（E1，confidence high，无 flag）。后者在 w=8 两个读数都 NONE，
在 w=4/2/1 的 persist2 上是 +10 / +9 / +8，是一条干净的窄窗口收益。

因此 §4 的 leak 敏感性表要这样读：**同读数**下去掉 leak 条，CAND-A persist2 R+16 从 w=8 的 2/13
升到 w=1 与 w=2 的 3/13（+1 条，来自 `b1-f1-034`）；CAND-A max 则从 w=8 的 3/13 掉到 w=1 的 1/13（−2 条）。
评估者写的"3/13 (w=1/w=2 persist2) versus 3/13 (frozen w=8 max) → 零净收益"在算术上没错，
但它**跨读数**比较（左边 persist2、右边 max），并把收益归因给 leak 条，这两点都会误导。
更诚实的说法是：窄窗口在 persist2 上净赚 1 条（非 leak 条），在 max 上净亏 2 条；哪种"净"取决于你选哪个读数，
而 prereg 禁止在这 14 条上选读数。无论怎么读，都离 5/14 的门槛很远，H2 的结论不变。

### 4.2 「14 条里有 5 条在任何窗宽、任一候选、任一读数下都不报警」——**是 4 条，不是 5 条**

`b2-f3-017-case_and_knowledge-rust-function` 在 **CAND-B w=2 persist2** 上有报警：first_alarm_end 落在锚点前 9 个 token
（`PRE-9`，严格口径判为 pre-onset，不算命中）——tables.md §3 的 CAND-B 表里就印着这个格子，
但评估者的 note 把它列进了"NONE everywhere"。真正在 α=0.10 主读数逐条表里全程无报警的是
`b1-f0-052`、`b1-f3-018`、`b1-f4-022`、`b1-f4-024` 四条。

这条更正影响评估者 problems 里的一句统计话术："effective denominator for any window comparison is 9"
应改为 10（14 − 4）。不影响任何计数。

### 4.3 tables.md 的两处文档缺陷（不影响数字）

- 附录字母重复：`附录 B：mode D，α = 0.10，其余全部读数` 与 `附录 B：mode D，α = 0.05，其余全部读数` 同名为 B
  （随后是附录 C、D），应改为 B1/B2 或 B/C 顺延。
- §0 只说"CAND-B 有 2 条 refit 不一致"，没说这 2 条都在 **ewma01**（非主读数）且都在 benign 样本上；
  主读数 max/persist2 在所有窗宽/方向/α 上 refit 与存储判定 0 分歧（我独立验证）。建议补上，
  否则读者会怀疑主表受浮点噪声影响。

---

## 5. 需要写进结论、但不构成错误的观察

1. **R+4 的跨窗宽比较在 w=8 上有可达性偏差。** 端点从 `w−1` 开始，所以锚点 ≤3 的样本在 w=8 上根本不可能
   在 +4 内被命中。我数了：drift 的 `reachable+4` 在 w=8 是 **42/59**，在 w≤4 是 59/59；
   有锚点抵御是 w=8 的 13/14 vs w≤4 的 14/14。R+8 / R+16 不受影响（全部 59/59、14/14）。
   主表报的是 R+4 计数而不是"可达条件下的 R+4"，所以 `CAND-A w=8 max R+4 = 0/14 → w=1 = 1/14` 这类比较里
   有一条样本（`b1-f4-024`，onset 0）在 w=8 上是结构性不可命中的。实际影响为零，因为该样本在任何配置下都不报警；
   但如果 B3 要用 R+4 做跨窗宽比较，必须改用可达分母。
2. **保形阈值的名义 α 与实际保证。** b2→b1 方向每半份只有 40 条校准 trace，阈值取第 37 位，
   有限样本保证是 4/41 = 0.0976；b1→b2 是 8/81 = 0.0988。四个窗宽完全一致，所以不影响窗宽比较，
   但"FAR 钉在 0.10"实际是钉在 ~0.098。
3. **窄窗口并没有增加多重比较负担到需要担心的程度**：总端点数 w=1 的 41 702 vs w=8 的 39 182（+6%），
   而且保形阈值取的是 trace 最大值，自动吸收了这一点。
4. **mode T 的 caveat 我核实是无害的**：`_compact_alarms` 存的 `first_alarm_end` 足以决定严格与容差记账的每一个字段
   （`hit` 要求 `not pre_alarm`，而 `pre_alarm` 只取决于第一个报警端点）。唯一取不到的量确实不进任何指标。
   附录 C 的 mode-T CAND-B 行（pooled FAR 0.271–0.421）本身没有校准半份，α 不成立，评估者把它标为"broken"是对的。
5. **runlen 行在 α=0.05 与 α=0.10 两张附录里数字完全相同**，因为它们用固定阈值。tables.md 已注明，这里只是确认不是复制粘贴错误。

---

## 6. 判决

- 协议：**通过**。mode D 阈值只来自目标批次 routine 的校准半份，scenario 级不相交，窄窗口没有改变桶成员或尾桶合并点，
  锚点只用于记账，检测器保持因果。
- 复现：**通过，且强于 checker 的声称**——两个冻结窗宽的 case_run 在 score_streams、calibration、44 个 candidate 的
  全部字段、q1_panel、ranking、pre_onset_audit 上与冻结运行**零差异**；数据 sha256 相同；中间提交对本轮路径是 no-op。
- 数字：**站得住**。14 个主单元 × 19 个字段、28 个分方向格子、28 行半份 FAR 我全部独立复算命中。
- 解释：**有两处需要更正**（§4.1、§4.2）；H1/H2/H3/H0 的判决本身不变，H2 在任何窗宽都不成立。
- 一个新发现的读数缺陷（§2(2)）：`read_ewma` 在第一个端点退化成 `max`，在 w≤4 上让 ewma01 在 token 0 产生真实误报，
  这是 ewma 行两半份 FAR 差 0.20–0.35 的机制之一。本轮没有据此选择任何东西，但 B3 若考虑 ewma 必须先修。

B1/B2 是开发数据，以上没有任何一项是独立验证。
