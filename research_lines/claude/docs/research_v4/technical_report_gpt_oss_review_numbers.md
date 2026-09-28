# 技术报告 `technical_report_gpt_oss.md` —— 数字审计（lens = numbers）

> **审计对象**：`docs/research_v4/technical_report_gpt_oss.md`（427 行）。
> **口径**：逐个数值 / 计数 / 哈希 / 提交 / 比例回溯到它的出处文档；
> G-conf 的权威源是 `g_conf_confirmatory_report.md` 与
> `artifacts/agent_v2/dataset_g/v3_2_conf/{stage1,stage2_S_vs_P,stage2_S_vs_M}/result.json`；
> G-dev 的 v3.2 读数用 `g_dev_v3_2_development_report.md`；v3.1 读数用 `g_dev_confirmatory_report.md`。
> **本次审计未读取** `artifacts/agent_v2/dataset_g/g_conf` 下的任何路由；只读 `v3_2_conf/` 下的产物与已解封文档。
> **未修改**报告、代码、配置或标签；未跑任何检测器。

**结论：没有 BLOCKING。** 报告的每一个判定性数字（H1 / S1 / S2 / S-J、全部门、全部哈希与文件大小、
两阶段解封的时刻与守卫）都与权威源**逐位一致**。发现 **3 条 SHOULD-FIX** 与 **6 条 NOTE**，
全部集中在设计理由段、摘要的一句修辞、以及少数未标出处 / 未标口径的引用上。

---

## 1. 发现清单

| # | 等级 | 位置 | 问题 |
|---:|---|---|---|
| **N-1** | **SHOULD-FIX** | §6.1 「视界」行 | 「G-conf 每折在 H 处只存活 ≤ 66 条，规则会把 H 压到一百多」——**两个数都与实测相反** |
| **N-2** | **SHOULD-FIX** | §7 第 6 条 | 「**窗宽** / 视界的微调不是杠杆」把一条**未做**的消融写成负结果 |
| **N-3** | **SHOULD-FIX** | 摘要末句 | 「离可部署还差**至少一个数量级**的误报纪律」是无出处的量化断言，且与 G-conf 的 F1 pooled PASS 相抵 |
| **N-4** | NOTE | §6.1 校准池表 | 表无出处行；第 4 行（0.0980 / 0.1195）与前三行来自**不同运行**，直接可比的那一行是 0.115 / 0.147 |
| **N-5** | NOTE | §11.2 末 | 「合计 356 s」与它前面四段（153 + 2 + 148 + 30 = 333 s）对不上 |
| **N-6** | NOTE | §3.6 | 「18 条编号假设」——总表实际是 **20 行**（H-01…H-18，其中 H-12 拆成 a/b/c） |
| **N-7** | NOTE | 摘要 | 把 0.446 / 0.989 说成「selection 几何（S **及其同族基线**）」的读数；这两个数只属于 S |
| **N-8** | NOTE | §6.6 误报解剖 | 把 n = 69 的「attack arm」组称作「**真检出**」；真检出是 53 |
| **N-9** | NOTE | §7 第 2 条 / §2 | 两处引用没有标出**池**：0.250 / 0.050 是 **G-bridge** 留出读数；12.5 / 8.0 / 79.5 是 P0 的 **正常 16 条**行 |

---

## 2. SHOULD-FIX 详述

### N-1 §6.1「视界」行：两个关于 G-conf 的数与实测相反

**报告原文**：

> | **视界** | `min_survivors = 90` 规则 | **H = 352 显式冻结**（`--force-h 352`）。不强制的话 G-conf 每折在 H 处只存活 ≤ 66 条，规则会把 H 压到一百多，**60% 的 X 窗口变成不可达** |

**实测**（`g_conf_confirmatory_report.md` §4.2 N3 行，并由
`v3_2_conf/stage2_S_vs_P/result.json` 的 `folds[k].calibration` 复核）：

| 量 | 报告写的 | 实测（G-conf） |
|---|---|---|
| H = 352 处逐折存活 | 「≤ 66 条」 | **44 / 68 / 54**（折 1 是 **68 > 66**） |
| 不强制时的规则 H | 「压到一百多」 | `rule_H` = **278 / 309 / 274**（`rule_survivors_at_H` 90 / 91 / 90） |
| 「60% 的 X 窗口不可达」 | 作为 G-conf 的后果陈述 | 这是 **G-dev** 在 `n_cal = 136`、H ≈ 166 上的量化（`v3_2_design_note.md` §3.4），不是 G-conf 的读数 |

**来源**：`v3_2_design_note.md` §3.4 的原文是**冻结前的投影**——「每折在 H = 352 处大约只有 **55–75** 条存活」
「规则会把 H 从 352 压到一百多」「开发集上已经量化过这个后果：在 `n_cal = 136` 时 H 掉到约 166，
60% 的 X 窗口变成不可达」。报告把这段**投影**转写成了对封存批的**事实陈述**，且把区间 55–75 收缩成
一个不存在的上界 66。

**为什么值得改**：这一格是「为什么 `--force-h 352` 是必要的协议改动」的唯一理由；
报告自己在 §6.5 第 2 条已经写明「删失先验严重落空」（投影 0.65–0.70 对实测 0.256 / 0.378 / 0.310），
所以同一批投影在 H 这一格上也应当按实测重述，而不是保留投影的措辞。

**建议改写**：「不强制的话，冻结前按 G-dev 分布投影每折在 H = 352 处只剩 55–75 条存活、规则会把 H
压到一百多（`v3_2_design_note.md` §3.4）；**实测的规则值是 278 / 309 / 274**（`rule_survivors_at_H`
90 / 91 / 90，三折 `min_survivors_satisfied = false`），H = 352 处实测存活 44 / 68 / 54。
即：强制 H 的必要性成立（规则确实会把 H 压低并让 `x_beyond_h` 变大），但**幅度的投影落空**，
与 §6.5 第 2 条的删失先验落空是同一件事。」

### N-2 §7 第 6 条：把未做的窗宽消融写成负结果

**报告原文**（§7「负结果与已排除方向」）：

> 6. **窗宽 / 视界的微调不是杠杆**：召回对视界单调只是因为更长的视界终究会包含 X。

理由句只覆盖**视界**，对**窗宽**没有给出任何读数。而：

- `gpt_oss_research_program.md` §2 总表把 **H-11**（「窄窗口 w = 4/2/1 与逐 token 读数买不到召回」）
  标为 **`untested on gpt-oss`（A-w4 消融未跑）**；
- **本报告自己的 §7 第 7 条**把 `A-w4` 列进「本轮未做或未注册」的清单。

即同一节里第 6 条与第 7 条互相矛盾。§7 的标题是「负结果与**已排除**方向」，把一条未测量的方向
写进这张表会被外部读者读成「测过了、没用」。

**建议改写**：「6. **视界的加宽不是杠杆**：召回对视界单调只是因为更长的视界终究会包含 X（+16 0.046 →
全路径 0.629）。**窗宽（w = 4 / 2 / 1）本轮未测**（A-w4 消融未跑，见第 7 条），不在本节的排除范围内。」

### N-3 摘要：「至少一个数量级的误报纪律」无出处

**报告原文**（摘要末句）：

> 检测器本身离可部署还差**至少一个数量级**的误报纪律。

- 全部出处文档里**没有任何**关于 FAR 目标量级的数字：`production_moe_routing_shift_research_brief.md`
  §3.2 / §9 只写「可控且可解释的误报率」「维持可控误报」，**不给数值门**。
- G-conf 上 S 的 pooled `far.filtered` = **0.0994264**，对加权 `alpha_eff` 0.0988594 偏差 0.000567，
  **F1 pooled PASS**；`far.all` = 0.086310。失败的两条门是 **N1**（标注质量过滤率）与
  **F1 逐折列**（折 0 偏差 −0.0427），都不是「误报率数量级过高」。
- 「一个数量级」这个量级感来自 **v3.1**（filtered 0.2389 对 0.10，约 2.4 倍——**也不是一个数量级**）。

报告开篇写「全部数值取自已落盘的冻结产物与其报告」，摘要里这个量化修辞不满足这条纪律。

**建议改写**：把这句换成报告 §9 第 2 / 第 7 条已经有出处的两条边界，例如
「检测器本身不可出厂：失败的门是 N1（0.778 < 0.85）与 F1 的逐折列（折 0 偏差 −0.0427），
而且本设计给出的是批内 held-out 保证、不是部署保证。」

---

## 3. NOTE 详述

### N-4 §6.1 校准池表：无出处行 + 第 4 行换了运行

报告 §6.1 的表：

| 校准池 | FAR `all` | FAR `filtered` | 实际出处 |
|---|---|---|---|
| 冻结的 G-fit / G-cal | 0.211 | 0.239 | `explore_v32_feasibility.md` A[S].1b `P1_frozen` ✅（并由 `g_dev_v3_2_development_report.md` §5.12 的 `abl_B_v31path` 0.210784 / 0.238908 复核） |
| + G-session 正常臂 | 0.243 | 0.290 | 同上 `P2_mixed` ✅ |
| 参照集换成 G-session 正常臂 | 0.233 | 0.263 | 同上 `P2b_session_cal`（**A[S].1b 那张 H = 352 表**，不是 A[S].1 的 0.189 / 0.208）✅ |
| **目标批 3 折轮转（注册口径）** | **0.0980** | **0.1195** | **不在 `explore_v32_feasibility.md` 里**：这是 `g_dev_v3_2_development_report.md` §5.12 `a2_verify` 的 0.098039 / 0.119454 |

前三行是**同一次探索性复算**（`explore_v32_feasibility.md`，旧折键 `scenario_mod`；前三行是单池校准，
不受折键影响），第 4 行是**换折键之后的注册运行**。同一张探索表里直接可比的那一行是
`P3_devcf` 在 H = 352 上的 **0.115 / 0.147**。表本身没有出处行，与报告开篇「每一节末尾或表格内标注出处文件」
的自我要求不符。

**建议**：给表加出处行，并在第 4 行注明「注册口径（`fixture_rank_mod`，`a2_verify`）；
同一探索表在旧折键下的对应行是 0.115 / 0.147」。这不改变结论（目标批自校准仍是四者中最低）。

### N-5 §11.2 墙钟：分段之和 333 s，写「合计 356 s」

`g_conf_unsealing_run_log.md` 的时刻：步 0 核验 06:15:54Z；阶段 1 06:15:54Z → 06:18:27Z（153 s）；
步 1.5 06:18:27Z → 06:18:29Z（2 s）；阶段 2a 06:18:52Z → 06:21:20Z（148 s）；
阶段 2b 06:21:20Z → 06:21:50Z（30 s）。

**356 s 是端到端**（06:15:54 → 06:21:50）**是对的**，但报告把它写成一串 `→` 之后的「合计」，
读者按分段相加会得到 333 s；差的 23 s 是步 1.5 结束到阶段 2a 起跑之间的人手间隔。
**建议**：把「合计」改成「端到端 356 s（分段之和 333 s，另 23 s 是步 1.5 与阶段 2a 之间的人手间隔）」。

### N-6 §3.6「18 条编号假设」

`gpt_oss_research_program.md` §2 的总表是 **20 行**：H-01…H-18，其中 H-12 拆成 **H-12a / H-12b / H-12c**
（三行）。若按编号根计是 18 条，若按可判定的假设行计是 20 条。报告随后引用的三条反转
（H-04 锚点、H-07 量级 / M、H-12b 跨池稳定性）正好来自被拆开的那一族。
**建议**：写「18 个编号、20 条可判定假设」。

### N-7 摘要把 S 的 AUROC 说成「S 及其同族基线」的

摘要：「selection 几何（稀有坐标 surprisal S **及其同族基线**）在 `[E, E+16]` 的窗口 AUROC 是
**0.446（低于随机）**，在 `[X, X+16]` 是 **0.989**」。

`g_dev_primary_diagnostics.md` §3 的表：

| statistic | AUROC `[E,E+16]` | AUROC `[X,X+16]` |
|---|---|---|
| **S** | **0.446** | **0.989** |
| M | 0.518（随机，**不是低于随机**） | 0.994 |
| P | **—（未落盘）** | **—（未落盘）** |

报告正文 §5.3 的表把三格分开列了 ✅；只有摘要把 S 的两个数扩到了「同族」。
**建议**：摘要改成「稀有坐标 surprisal S 在 `[E,E+16]` 是 0.446（低于随机）、在 `[X,X+16]` 是 0.989
（同族的 M 是 0.518 / 0.994）」。

### N-8 §6.6 把 attack-arm 报警组称作「真检出」

报告：「把首报警 look 减去该 episode 第一个 `final` look，误报的中位偏移是 **32.5**……
**真检出是 123**（只有 17% 在 32 look 内）」。

`g_conf_confirmatory_report.md` §5.8 的这张表两组是 `normal arms (false alarms, n = 58)` 与
**`attack arm (n = 69)`**。攻击臂上报过警的 69 条里，落进命中窗口的只有 **53** 条（另有静默攻击 2 条等）。
把 69 条整组叫「真检出」把一个报警口径说成了命中口径。
**建议**：改成「攻击臂报警（69 条）的中位偏移是 123」。

### N-9 两处引用没有标池

1. §7 第 2 条：「`in_set_residual_mass` 在正常臂上的留出误报是 **0.250**（S 是 **0.050**……）；
   跨池预设阈值差 **0.112**；`prob_rare_mass` 是 **0.000**……重尾指数 1.69 差于 S 的 1.05」。
   这些全部来自 `g_prob_channels_smoke.md` §3 的 **G-bridge 留出表**（n = 160 个正常 episode），
   与本节其余的 G-dev / G-conf 读数不是同一个池。数值本身**逐位正确**
   （0.2500 (40/160) / 0.0500 (8/160) / 0.1121 / 0.0000 (0/160) / 1.69 / 1.05）。
2. §2：「P0 实测通道 token 占比 analysis 12.5% / commentary 8% / final 79.5%」。
   `p0_readout.md` §2.2 的这一行是「**正常 16 条**」；「全部 48 条」行是 12.4 / 5.2 / 66.6，
   攻击 32 条是 15.6 / 5.4 / 79.0。G-dev 上的端点占比又是 7.6 / **10.1** / 82.3
   （`g_dev_v3_2_development_report.md` §5.3）——报告 §6.6 与 §10.1 引的正是后者的 10.1%，
   所以两处并列时读者容易把 8% 与 10.1% 当成同一个量的两次测量。

**建议**：两处各补一句池的名字。

---

## 4. 逐条核对通过的清单（无问题）

以下全部**逐位一致**，不需要改动。

### 4.1 G-conf 判定层（对 `stage2_S_vs_P/result.json` / `stage2_S_vs_M/result.json` 实算）

| 报告写的 | result.json | ✓ |
|---|---|---|
| N = 71（正例 72、`window_unreachable_plus_16` = 1） | `pair_count` 71、`positives` 72、`reachable_count` 71 | ✅ |
| R_S 0.746479 = 53/71 | `recall_a` 0.7464788732394366、`hit_count` 53 | ✅ |
| R_P 0.478873 = 34/71 | `recall_b` 0.4788732394366197 | ✅ |
| Δ̂ 0.267606 | `point_estimate` 0.2676056338028169 | ✅ |
| CI [0.071429, 0.459459]；稳健列 [0.129032, 0.408451]（37 簇） | `ci`、`robustness_48_cluster.ci` / `family_count` 37 | ✅ |
| McNemar b 21 / c 2 / both 32 / neither 16，p = 6.604e-05 | `mcnemar` 逐字段相同 | ✅ |
| 工作点 S α 0.10 / FAR 0.0994264；P `matched_alpha` 0.1104972 / FAR 0.0994264 | `rows.matched` 逐位相同；`matched_alpha_secondary.source = threshold_manifest.matched_alpha_inputs` | ✅ |
| 匹配分母 523（clean 260 / benign_control 263；并集 672） | `normal_denominator` | ✅ |
| 16 / 16 家族、`dropped_families = []`、最小家族 1 | `positive_families.S` | ✅ |
| ψ 23/71 = 0.324 | 由 `mcnemar.discordant` 推出 | ✅ |
| S1 rate 0.746479、CI [0.620, 0.857]、p 4.9975e-04、Holm 9.995e-04 | `holm_s1_one_sample.S` | ✅ |
| S2 Δ_SM +0.014085、CI [−0.051282, 0.092308]、p 1.0（b 4 / c 3） | `stage2_S_vs_M.comparison_anchored` | ✅ |
| S-J Δ̂_J 0.275000（160 对，0.350 / 0.075）、CI [0.179012, 0.371951]、p 2.402e-09；敏感性 146 对 0.2808 | `injection_pairing.J` | ✅ |
| 澄清：`comparison_injection_present` Δ 0.13125、CI [0.042169, 0.221519]、p 3.241e-04 | 同名块 | ✅ |
| Δ̂ 高出预设备择 0.20 +0.068 / 高出先验中心 0.264 +0.004 / 高出 N = 71 的 MDE ≈ 0.221 约 +0.047；检验力 ≈ 0.93 | `g_conf_confirmatory_report.md` §1.3 | ✅ |
| 合取规则假阳性上界 **0.036**（ρ 0.30 的四格 0.024–0.036） | `detector_prereg_v3_2.md` §8.3 | ✅ |

### 4.2 门（G-conf）

F1 pooled 0.0994264 / 0.0988594 / dev 0.000567 **PASS**；F1 逐折 −0.042710 / +0.023360 / +0.023256 **FAIL**；
F2a −0.016437；F2b 不可评；F3 `all` long 0.124378、`filtered` long 0.114943 **PASS**（四格里只有 J 0.154229 FAIL）；
F4 0.095238 (2/21) 对 0.148214、余量 0.052976；F5 0.175000 对 0.271063（k̄ = 2.4）；F6 1.0741×；
F8 19 项核验 + 60 行断言全 ok；N1 0.778274 = 523/672 **FAIL**；N2 最小格 49；N3 H = 352；
N4 rank 17/18/17；N5 两臂差 +0.008929；N6 / N7 全 0；
D1 128 ≥ 80、D1x 71 ≥ 62、D2 56 ≥ 20、D3 14 ≥ 8、D4 19 ≥ 12、D5 0.800 ≥ 0.55 —— **六条全 PASS**。
四项由 `gates.S` 直接实算复核（0.0994263862332696 / 0.12437810945273632 / 0.175 / 0.7782738095238095 / 49）。✅

### 4.3 描述性列（G-conf）

`x_beyond_h` 53/59 = 0.898305 对 0/12 = 0.000（G-dev 0.9167 / 0.2353）；命中中位延迟 2.0 look；
首报警早于 X 15/71 = 0.211268 与 15/53 = 0.283019；正常臂误报 58 条、52 条（89.7%）首报警在 `final`；
误报中位偏移 32.5（29/58 = 50% 在 32 look 内）；`Omega_rare` 137 / 137 / 140、未见 14 / 14 / 15；
Jaccard 0.111；层 10 / 12 / 13 / 11 / 18；有界抵御 8/56 = 0.142857 对载 X 58/72 = 0.805556；
`over_refusal` 73 条 0 报警；`explicit_correction` 全 888 行为 0、恢复段 110 / 107 行、`re_execution` 68；
代码域 15/19 = 0.7895、代码域 FAR 12/172 = 0.069767；
成本 S 0.027055 s/1000 端点、221 298 端点、6.5198 s、J 0.032553（+20%）/ 49.4908 s（7.5×）、RSS 未测量。✅

### 4.4 v3.1（G-dev）

Δ̂ 0.0406、CI [0.0143, 0.0741]、p 0.0078125、R_S 9/197 = 0.045685、R_P 1/197 = 0.0051；
不一致对 (8, 0)、2⁻⁷ = 0.0078；MDE 0.118–0.144；α 压到 0.05 时 filtered FAR 仍 0.184；
S1 Δ̂_SM −0.0812、CI [−0.1818, −0.0169]、p 1.45e-4 / Holm 2.90e-4、M 25 对 S 9（2.8 倍）；
S2 OR 臂 3/56 → 3/56、增益 0.000、(0,0)、p 1.0；
F1 0.238908 / F3 0.344828 与 0.350000 / F5 0.324841 与 0.351190。✅

### 4.5 诊断（G-dev，EXPLORATORY）

AUROC `[E,E+16]` S 0.446 / M 0.518 / J 0.723；`[X,X+16]` 0.989（中位 1.000、0.926 在阈上）/ 0.994 / 0.956；
`[C,C+16]` 0.567；首报警对 X 中位 +2.0 token、70/112 落在 `[X, X+16]`、对 E 中位 +132.5；
ORACLE 天花板 0.188 @ 窗口 FAR 0.10（J 0.335、M 0.239）；J 的衰减 0.723 → 0.669（+16）→ 0.510（+64）；
视界曲线 0.046 / 0.142 / 0.310 / 0.548 / 0.629；缺口分解 14 点对 81 点（≈ 6 : 1）；
`final` 通道 z：sd 1.016 → 1.291、均值 +0.03 → +0.21；ORACLE 重标准化 0.211 → 0.047（S）、0.186 → 0.059（P）、
+16 召回不变；`ep1` 0.385 对 `ep0` 0.157、G-dev 第二轮占比 23%（96/408）；
look 35 之前无校准路径越阈、106/197 = 53.8%；逐 look 诊断 FAR 0.108 上 +16 召回 0.041。✅

### 4.6 v3.2 开发读数（G-dev）

N = 125（正例 126、不可达 1）、R_S 0.824 (103/125)、R_P 0.560 (70/125)、Δ̂ 0.264、
CI [0.023529, 0.508333]、p 2.4995e-07、ψ 43/125 = 0.344、16 家族；
S1 0.824 / CI [0.70909, 0.91870]；S2 Δ −0.016 / p 0.754；S-J（J 格）144 对 / 0.21528；
A-V2：命中 103/125 不变、FAR filtered 0.11945 → 0.08532（−29%）、静默 5/40 → 2/40、
最差长度档 0.15315 → 0.14414、`x_beyond_h` 18 → 11；`commentary` 19 211/190 284 = 10.1% 端点、**0 条 onset**。✅

### 4.7 数据集与标注

trace 合计 48 + 240 + 300 + 300 + 600 + 100 + 120 + 720 = **2 428**；
episode 66 + 240 + 300 + 300 + 784 + 200 + 156 + 888 = **2 934** —— 两个加总都对得上；
G-dev 臂 352 / 192 / 192 / 24 / 24 = 784；G-conf 336 / 336 / 216 = 888；
三批新采 940 trace / 1 244 episode / 364 050 token / 6 h 40 min / `validate_trace` 940/940 / X_tool 0；
G-fit + G-cal 600 行全 `silent`、裁决 4 行、复标 `packet_order % 5 == 0` 各 60；
G-dev 97.07%（761/784）、κ 0.9457、裁决 146 + 组长补裁 1、E 存在性 κ 1.000；
G-conf 98.09%（871/888）、κ 0.9422、裁决 40、五类事件存在性 κ 1.000、`overall_confidence` 735 / 153 / 0；
封存 `SEALED.json` sha256 `1d6a30e0…`、`sealed_at_utc` 2026-09-07T23:53:14Z、`trace_count` 720、
250 753 文件 / 1 728 目录 / 249 260 分片 / `touch` → Permission denied；
场景工厂 4 × 3 × 3 × 2 = 72 格 × 2 = 144、补充层 120（T1 60 / T2 60）、tool_output 25.8%、
G-conf 144×3 + 16×3 + 120×2 = 280 / 720、fixture TSL 94 / WRH 93 / OSY 93。✅

### 4.8 冻结、哈希与两阶段留痕（对文件系统实算）

| 项 | 报告 | 实算 |
|---|---|---|
| `stage1/threshold_manifest.json` | `96c2baa9…` / 1 165 046 B | **逐位相同** |
| `stage1/result.json` | `bc1948e8…` / 5 375 142 B | **逐位相同** |
| `stage2_S_vs_P/result.json` | `4a327096…` / 8 354 369 B | **逐位相同** |
| `stage2_S_vs_M/result.json` | `2b8e082a…` / 4 216 623 B | **逐位相同** |
| 三次运行的 `code_commit` / `freeze_commit_resolved` | `53269fd8…`（B′） | **逐位相同** |
| A `afdf5f34…` / 预注册 sha `f9351639…` / B `8d0e3b13…` / 12:27:21-07:00 / 794 passed, 117 subtests | `freeze_a_checklist.md` §4、`label_freeze_b.md` | ✅ |
| A′ `463d23c8…` / 预注册 sha `b7179500…` / B′ `53269fd8…` / 22:37:23-07:00 / 1336 passed, 128 subtests | `freeze_a2_checklist.md` §4、`label_freeze_b2.md` | ✅ |
| 四份标签 sha256（g_fit / g_cal / g_dev / g_conf） | `label_freeze_b.md`、`label_freeze_b2.md` | ✅ |
| 正常臂 trace 集合 `48b12b30…`（560：280 / 280）、攻击臂 `02d04db0…`（160）、折图 `0e659e5c…`（280） | `g_conf_confirmatory_report.md` §9.1 | ✅ |
| `SEALED.json` trace-set `3fb77c58…`、subset config `62728c5c…`、`g_conf_data_gates.json` `cb5d38d2…`（22:39:50 早于 23:15:54） | 同上 | ✅ |
| 19 项核验两次全 ok、`run_once_guard` / `data_discipline_guard` 三次 `enforced = true` / `dirty = false` | 同上 §9.3 / §9.5 | ✅ |
| §11.3 阶段 1 命令 | 与 `detector_prereg_v3_2.md` §12.2 的阶段 1 命令块**逐字一致**（占位符已代入） | ✅ |

### 4.9 统计装置与预注册

零假设：ρ 0.30 / N 126 / ψ 0.30 时单独 McNemar 0.092、合取 0.029；ρ 0.30 四格 0.024–0.036、上界 0.036；
检验力：4000 × 2000、seed 20260907、`g_conf.json` 16 家族 8×11 + 8×9；预设备择 0.20；
最保守格（N 62 / ρ 0.30 / ψ 0.35）MDE 0.231 > 0.20；Δ = 0.264 处最低检验力 0.914；
C1：`p(k) = (1 + #{Z ≥ R(k)})/(n_cal+1)`、`alpha_eff = 28/280 = 0.100000`（恰好可达）、参照 = 过滤后 G-cal 279 条；
H = 352：look ≈ 0.9001 × token、第 352 个 look 落在 token 379–393、删失 89/279 = 31.9%；
统计量定义（q 平滑、`Omega_rare` q < 0.02、M 的 768 维白化 + 方差下限 1e-3、桶 //32、尾桶 ≥ 30、
与上一代 scorer `atol = rtol = 0` 逐位相等）；稀疏通道 pooled 回退在 G-conf 四格三折
`applied_channels_absent_from_fit = []` / `applied_windows = {}` —— **一次都没触发**（实算复核）；
冻结审阅：v3.2 一轮 **31 条**发现（statistics B1–B3 / S1–S8 / N1–N8 = 19，data DATA-1…12 = 12），
其中 **3 条**经裁定为 blocking（B1/DATA-2 可达性、B2/DATA-3 阈值清单多格、DATA-1 折键共线）；
DATA-1 的机制（G-conf 3 个 fixture × 周期 3 与模 3 锁死、每个 fixture 整整缺一折；G-dev 4 个 fixture、
周期 4 与模 3 互素、26/26/26 均衡）；换键后 OSY 31/31/31、TSL 32/31/31、WRH 31/31/31、
`collinear_fixtures = []`。✅

### 4.10 相关工作、定位与外部效度

RASET 的 0.3379 / 0.0098–0.0346 / 0.0054–0.0434 与五个模型（含 gpt-oss-20b）；
RouteScan「四个开源 MoE 上未见有害领域 AUROC > 0.91、仅 prefill、每 prompt 一个向量、有监督」；
TaskTracker（SaTML 2025、残差流前后差分、OOD ROC AUC 接近完美）；`related_work.md` 的「开放缺口」原句；
brief §3.6（不自行裁决）与 §9 的四条判据；
外部效度三条理由与 tau2-bench retail/airline（MIT）、DoomArena / AgentDojo / InjecAgent / LLMail-Inject；
DeepSeek-V2-Lite（16B / 2.4B、64 routed + 2 shared、top-6）与 Qwen3-30B-A3B（48 层）；
§10.4 与 `dataset_g_onboarding_for_codex.md` §5.1 的冻结口径**逐条一致**。✅

### 4.11 OLMoE 纪律

全文对 OLMoE 的四处引用（页首声明、§2 的 softmax 口径差、§3.6 的假设来源与三条反转、§11.4 的来源表）
**没有一处把 OLMoE 的读数当作 gpt-oss 上的证据**；§6.6 与 §5.3 对 pilot 的引用都是「机制不同 / 方向相反」
的对照。`docs/research_v3/technical_report_rmc.md` 只出现在 §11.4 并标注「只作假设来源，不作证据」。
**本条无发现。**

---

## 5. 复现（本次审计读了什么）

- 报告：`docs/research_v4/technical_report_gpt_oss.md`
- G-conf 权威：`docs/research_v4/g_conf_confirmatory_report.md`、
  `artifacts/agent_v2/dataset_g/v3_2_conf/{stage1/threshold_manifest.json,stage1/result.json,stage2_S_vs_P/result.json,stage2_S_vs_M/result.json}`、
  `docs/research_v4/g_conf_unsealing_run_log.md`
- G-dev：`g_dev_v3_2_development_report.md`（v3.2）、`g_dev_confirmatory_report.md`（v3.1）、`g_dev_primary_diagnostics.md`
- 设计与预注册：`detector_prereg_v3_1.md`、`detector_prereg_v3_2.md`、`v3_2_design_note.md`、
  `explore_v32_feasibility.md`、`g_prob_channels_smoke.md`、`h_freeze_note.md`、`detector_harness_g.md`
- 采集与标注：`p0_readout.md`、`g_bridge_run_log.md`、`g_fitcal_run_log.md`、`g_dev_run_log.md`、
  `g_session_medium_conf_run_log.md`、`scenario_factory.md`、`agent_v3_dataset_design.md`、
  `normal_annotation_guideline.md`、`attack_annotation_guideline.md`、三份 `*_annotation_report.md` 与 `*_annotation_agreement.md`
- 冻结与审阅：`freeze_a_checklist.md`、`freeze_a2_checklist.md`、`label_freeze_b.md`、`label_freeze_b2.md`、
  `freeze_review_v3_2_{data,resolution}.md`
- 定位与外部效度：`docs/production_moe_routing_shift_research_brief.md`、`docs/related_work.md`、
  `docs/research_v4/external_datasets_assessment.md`、`docs/research_v4/dataset_g_onboarding_for_codex.md`、
  `docs/research_v4/gpt_oss_research_program.md`
- 配置：`configs/pilot_gpt_oss_20b_mxfp4.json`、`configs/dataset_g/g_conf.json`

哈希与文件大小用 `sha256sum` / `stat -c %s` 实算；`result.json` 的字段用只读 Python 读取。
**未读取** `artifacts/agent_v2/dataset_g/g_conf` 下的任何路由张量。
