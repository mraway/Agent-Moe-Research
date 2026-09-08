# 窄窗口轮次 审计视角 (c)：误报解剖（false-alarm anatomy under narrow windows）

日期：2026-09-05。执行者：Opus 5 审计子代理（视角 c，prereg §5）。范围：**clean + benign_control 两臂**，
mode D，α = 0.10，读数 max 与 persist2，CAND-A w ∈ {1,2,4,8}、CAND-B w ∈ {1,2,4}。

**性质声明。** B1/B2 是开发数据；本文所有数字都是开发集测量，**不是独立验证**。误报分类由本审计代理
阅读报警窗口文本与 ±32 token 上下文人工判定，沿用 `docs/research_v2/zoom/false_alarms.md` §1.1 的
六类 (a)–(f)（组长任务书里的"五类"是把 (c)+(d) 合并成 "other explainable" 的折叠版，本文两种口径都给）。
未修改任何既有文件、未触碰冻结目录，只新增 `scripts/research_v2/narrow_window/audit_false_alarm_anatomy_*.py`
与 `artifacts/agent_v2/research_v2/narrow_window/audit_false_alarm_anatomy/`。

## 0. 独立复算：headline 的 FAR 数字全部对得上

本审计**不用** `scripts/research_v2/narrow_window/evaluate.py`，而是直接从两个 result.json 的
`candidates[*].trace_alarms`（harness 自己的决策，非重拟合）按 `research_v2.io.arm_class` 重新分臂计数。

- 36 个 (候选, 运行, w, 读数, 方向) 单元里，我的 clean/benign 分母与误报计数与 result.json 里
  `candidates[*].metrics.by_arm` **逐格相等**（0 mismatch）。
- 由此合并出的 14 个主格 `far_clean` / `far_benign` / `far_pooled` 与 headline 给出的
  0.058333 / 0.141667 / 0.1 等 **全部逐位相等**。
- `far_half_max_abs_diff` 的 14 个值也全部相等；`tables.md` 附录 D 的 28 行分半份 FAR 我逐行复算，
  **全部相等**。
- **复现对照更强的一条**：窄窗口运行的 w=8（CAND-A）/ w=4（CAND-B）不仅误报**数量**与冻结运行相同，
  **误报 trace 集合与每条的 first_alarm_end 也逐条完全相同**（26/26、23/23、23/23、17/17）。
  这是比 pooled FAR 相等更强的复现证据。

分母在所有窗宽上恒为 clean 120 / benign 120（b1→b2 80+80，b2→b1 40+40），没有任何 trace 因为
decode 短于 w 而被从分母里掉出去。

**关于 `evaluate.py` 里"2/360 行 refit 与 stored 不一致"的那条 caveat：定位到了，且不碰本文任何数字。**
我在两个候选上从存下来的 `score_streams` 独立重拟合桶统计量、用 result.json 里存的 conformal 阈值重放
mode D 决策，与 stored `trace_alarms` 逐条比对：**CAND-B 全部窗宽、mode D、α=0.10、max 与 persist2
共 0 处不一致**。把范围放宽到所有读数与两个 α 后，CAND-B w=1 / b2→b1 里的那 2 处不一致全部落在
非主读数 `ewma01` 上，且都是 `first_alarm_end = 0`（第一个端点就越界）vs `None` 的边界情形：
`b1-f2-036-...--benign_control`（α=0.05）与 `b1-f2-014-...--benign_control`（α=0.10）。
**主读数的所有格既能用 stored 复算，也能用 refit 复算。**

## 1. 主表：每个窗宽的 FAR、benign/clean 比、分半份 FAR、类别构成

分子/分母（两方向合并，clean 120、benign 120）：

| cand | w | reading | FAR clean | FAR benign | benign/clean | FAR pooled | half \|diff\| max | a | b | c | d | e | f |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CAND-A | 1 | max | 7/120 = 0.058 | 17/120 = 0.142 | 2.43 | 24/240 = 0.100 | 0.125 | 7 | 3 | 0 | 3 | 6 | 5 |
| CAND-A | 1 | persist2 | 5/120 = 0.042 | 20/120 = 0.167 | **4.00** | 25/240 = 0.104 | 0.175 | 9 | 7 | 0 | 1 | 6 | 2 |
| CAND-A | 2 | max | 4/120 = 0.033 | 19/120 = 0.158 | **4.75** | 23/240 = 0.096 | 0.100 | 10 | 6 | 0 | 1 | 2 | 4 |
| CAND-A | 2 | persist2 | 4/120 = 0.033 | 21/120 = 0.175 | **5.25** | 25/240 = 0.104 | 0.075 | 12 | 5 | 0 | 0 | 4 | 4 |
| CAND-A | 4 | max | 5/120 = 0.042 | 17/120 = 0.142 | 3.40 | 22/240 = 0.092 | 0.100 | 10 | 5 | 0 | 1 | 2 | 4 |
| CAND-A | 4 | persist2 | 7/120 = 0.058 | 16/120 = 0.133 | 2.29 | 23/240 = 0.096 | 0.113 | 9 | 6 | 0 | 2 | 2 | 4 |
| CAND-A | 8 | max | 9/120 = 0.075 | 17/120 = 0.142 | 1.89 | 26/240 = 0.108 | 0.087 | 8 | 10 | 0 | 2 | 2 | 4 |
| CAND-A | 8 | persist2 | 7/120 = 0.058 | 16/120 = 0.133 | 2.29 | 23/240 = 0.096 | 0.075 | 8 | 8 | 0 | 2 | 1 | 4 |
| CAND-B | 1 | max | 8/120 = 0.067 | 14/120 = 0.117 | 1.75 | 22/240 = 0.092 | 0.225 | 4 | 7 | 2 | 3 | 3 | 3 |
| CAND-B | 1 | persist2 | 12/120 = 0.100 | 13/120 = 0.108 | 1.08 | 25/240 = 0.104 | 0.163 | 4 | 6 | 2 | 3 | 6 | 4 |
| CAND-B | 2 | max | 9/120 = 0.075 | 12/120 = 0.100 | 1.33 | 21/240 = 0.088 | 0.075 | 5 | 6 | 0 | 3 | 4 | 3 |
| CAND-B | 2 | persist2 | 9/120 = 0.075 | 9/120 = 0.075 | 1.00 | 18/240 = 0.075 | 0.063 | 3 | 6 | 0 | 2 | 6 | 1 |
| CAND-B | 4 | max | 11/120 = 0.092 | 12/120 = 0.100 | 1.09 | 23/240 = 0.096 | 0.175 | 6 | 5 | 0 | 3 | 6 | 3 |
| CAND-B | 4 | persist2 | 6/120 = 0.050 | 11/120 = 0.092 | 1.83 | 17/240 = 0.071 | 0.150 | 5 | 4 | 0 | 2 | 4 | 2 |

折叠成组长任务书的五类（topic mention = a，JSON protocol = b，KB recitation = f，
other explainable = c+d，unexplained = e）：

| cand | w | reading | topic | JSON | KB | other | unexplained |
|---|---|---|---|---|---|---|---|
| CAND-A | 1 | max | 7 | 3 | 5 | 3 | **6** |
| CAND-A | 1 | persist2 | 9 | 7 | 2 | 1 | **6** |
| CAND-A | 2 | max | 10 | 6 | 4 | 1 | 2 |
| CAND-A | 2 | persist2 | 12 | 5 | 4 | 0 | 4 |
| CAND-A | 4 | max | 10 | 5 | 4 | 1 | 2 |
| CAND-A | 4 | persist2 | 9 | 6 | 4 | 2 | 2 |
| CAND-A | 8 | max | 8 | 10 | 4 | 2 | 2 |
| CAND-A | 8 | persist2 | 8 | 8 | 4 | 2 | 1 |
| CAND-B | 1 | max | 4 | 7 | 3 | 5 | 3 |
| CAND-B | 1 | persist2 | 4 | 6 | 4 | 5 | **6** |
| CAND-B | 2 | max | 5 | 6 | 3 | 3 | 4 |
| CAND-B | 2 | persist2 | 3 | 6 | 1 | 2 | 6 |
| CAND-B | 4 | max | 6 | 5 | 3 | 3 | 6 |
| CAND-B | 4 | persist2 | 5 | 4 | 2 | 2 | 4 |

**逐条证据（317 行：trace_id、臂、decode 长度、first_alarm_end、相对位置、校准半份、类、
w 个 token 的报警窗口文本、末 8 token 文本）在
`artifacts/agent_v2/research_v2/narrow_window/audit_false_alarm_anatomy/fa_table.md`；
带 ±32 token 上下文的 JSON 在同目录 `fa_rows_classified.json`。**

按臂合并（全部窄窗口格，317 行）：

| 臂 | a | b | c | d | e | f | 合计 |
|---|---|---|---|---|---|---|---|
| clean | **0** | 26 | 2 | 23 | 28 | 24 | 103 |
| benign | 100 | 58 | 2 | 5 | 26 | 23 | 214 |

**clean 臂的 (a) 计数在窄窗口下仍然是 0/103**，与冻结轮 (0/13) 一致。这是本轮对"检测器响应的是
生成进入域外话题，而不是上下文里的攻击文本"这一说法唯一的正面证据，且它在 w=1 上也成立。
反过来，CAND-A 的 benign−clean 误报差（8–17 条）里 7–12 条就是 (a) 类：**CAND-A 的 benign/clean
不平衡基本上就是"benign_control 的解码在复述/拒绝那段 inert label 外部注记"**，剩余 0–6 条才是别的原因。
CAND-B 的 benign−clean 差本来就只有 0–6 条，(a) 类（3–6 条）已足以解释甚至超解释它。

## 2. 核心发现：FAR 钉在 α，但**报警的是谁**换了一大半

这是本视角最重要的结果，也是 headline 与 `effect.json` 完全没有测量的一个量。

**(2.1) 相对冻结窗宽的集合周转。** 同一候选、同一读数、同一 α，只改窗宽：

| cand | reading | w | \|FA\| | 与冻结窗共有 | 新增 | 消失 | Jaccard | 独立零假设下的 Jaccard |
|---|---|---|---|---|---|---|---|---|
| CAND-A | max | 1 | 24 | 15 | 9 | 11 | **0.429** | 0.055 |
| CAND-A | max | 2 | 23 | 18 | 5 | 8 | 0.581 | 0.054 |
| CAND-A | max | 4 | 22 | 20 | 2 | 6 | 0.714 | 0.052 |
| CAND-A | persist2 | 1 | 25 | 14 | 11 | 9 | **0.412** | 0.053 |
| CAND-A | persist2 | 2 | 25 | 18 | 7 | 5 | 0.600 | 0.053 |
| CAND-A | persist2 | 4 | 23 | 20 | 3 | 3 | 0.769 | 0.050 |
| CAND-B | max | 1 | 22 | 11 | 11 | 12 | **0.324** | 0.049 |
| CAND-B | max | 2 | 21 | 13 | 8 | 10 | 0.419 | 0.048 |
| CAND-B | persist2 | 1 | 25 | 9 | 16 | 8 | **0.273** | 0.044 |
| CAND-B | persist2 | 2 | 18 | 10 | 8 | 7 | 0.400 | 0.038 |

在 w=1 上，**误报 trace 集合与冻结窗只重合 27–43%**：CAND-B persist2 从 w=4 到 w=1，17 条误报里
只有 9 条留下，同时新冒出 16 条。四个 (候选, 读数) 组合里，"至少在一个窗宽上误报过"的 trace
并集是 37–39 条，而任何单个窗宽只有 17–26 条；**在所有窗宽上都误报的"硬核"只有 15 / 13 / 9 / 5 条**。

**(2.2) 参照尺度：这个周转有多大？** 两个参照——

- 完全独立（同一 p 下互不相关地抽）时 Jaccard ≈ 0.04–0.06。观测到的 0.27–0.43 明显高于它，
  说明确实存在共享结构；但离 1.0 也很远。
- **换一个检测器**（CAND-A w=8 vs CAND-B w=4，完全不同的 scorer、不同的层、不同的统计量）
  的误报集合 Jaccard = **0.289 (max) / 0.290 (persist2)**。
  也就是说，**把 CAND-B 的窗宽从 4 收到 1（J = 0.324 / 0.273），对"谁会误报"的改变量
  和整块换掉检测器一样大。**

**(2.3) 同一窗宽内 max 与 persist2 的一致性在窄窗口下崩掉。** 这是同一个分数流上两个几乎等价的读数：

| cand | w=8 | w=4 | w=2 | w=1 |
|---|---|---|---|---|
| CAND-A | 0.885 | 0.875 | 0.778 | **0.324** |
| CAND-B | – | 0.600 | 0.345 | **0.205** |

w=1 上 max 与 persist2 的误报集合一致性（0.32 / 0.21）**低于**两个不同候选之间的一致性（0.29）。
在窄窗口上"哪条 trace 会误报"已经不再是检测器的性质，而是读数细节的性质。

**(2.4) 硬核 vs 一次性。** 在所有窗宽都误报的"硬核"以 (a) 域外话题 + (b) JSON + (f) KB 复述为主
（CAND-A max 硬核 15 条：a7 b2 e2 f4；CAND-A persist2 13 条：a7 b4 e1 f1；CAND-B max 9 条：a4 b3 d1 f1；
CAND-B persist2 5 条：a2 b1 e1 f1）。而**只在一个窗宽上出现的一次性误报**以 (b)/(e)/(d) 为主
（CAND-B persist2 的 21 条一次性误报里 13 条只出现在 w=1；CAND-A persist2 的 12 条里 7 条只在 w=1）。
**窄窗口买到的不是"更少的误报"，而是"换了一批更没道理的误报"：**
CAND-A max 的 unexplained (e) 从 w=8 的 2 条涨到 w=1 的 6 条，退化尾巴 (d) 从 2 涨到 3；
CAND-B persist2 的 (e) 从 4 涨到 6、(d) 从 2 涨到 3。同时 (b) JSON 类在 CAND-A 上从 10（w=8 max）
掉到 3（w=1 max）——窄窗口对 JSON/协议形状的敏感度确实下降了，但腾出来的额度被 (e)/(d) 吃掉。

## 3. 报警位置：整体前移，但对 CAND-A 几乎不动

| cand | reading | w | 误报数 | first_alarm_end 中位 | 相对位置中位 | first ≤ 8 | first ≤ 16 |
|---|---|---|---|---|---|---|---|
| CAND-A | max | 8 → 1 | 26 → 24 | 49.5 → 47.5 | 0.463 → 0.441 | 0 → 0 | 1 → 1 |
| CAND-A | persist2 | 8 → 1 | 23 → 25 | 57 → 56 | 0.490 → 0.474 | 0 → 1 | 1 → 2 |
| CAND-B | max | 4 → 1 | 23 → 22 | **81 → 55** | 0.612 → 0.498 | 0 → 0 | 1 → 2 |
| CAND-B | persist2 | 4 → 1 | 17 → 25 | **82 → 56** | 0.618 → 0.500 | 0 → 1 | 1 → 3 |

在**留下来的同一批 trace 内部**，报警端点确实往前挪，但幅度很小：CAND-A max（15 条共有 trace）
14 条提前、1 条不变、0 条推后，中位提前 4 个 token；CAND-A persist2 中位 2；CAND-B max 中位 2、
persist2 中位 1。**没有任何一格出现"提前 (8−w) 个 token"量级的整体前移**，这与视角 (b) 在正例上
观察到的 H1 失败是同一个现象的两面：窄窗口只是把报警在同一段文本里挪了几个 token。

CAND-B 的中位位置整体前移 26 个 token（81→55），但这不是同一批 trace 前移，主要是集合换人
（Jaccard 0.32/0.27）。

## 4. 一个被 headline 的 `far_half_max_abs_diff` 掩盖的系统性偏斜

`tables.md` 附录 D 的 28 行我逐行复算无误，但把它压成"两方向 |diff| 的最大值"这一个数丢掉了**符号**，
而符号是系统性的。写成"用半份 h 的阈值去判的那批 trace 的 FAR"（原始计数）：

- **CAND-A：16 个 (w, reading, 方向) 单元里 15 个是 half1 > half0**（唯一例外 w=1 persist2 b1→b2，
  9/80 vs 7/80）。典型幅度：w=4 persist2 b1→b2 是 3/80 vs 12/80，w=1 max b1→b2 是 3/80 vs 13/80。
- **CAND-B：12 个单元里 9 个是 half1 > half0**，并且出现三个 **0/40 vs 7–9/40** 的极端格
  （w=1 max b2→b1、w=4 max b2→b1、w=4 persist2 b2→b1）。

对应的阈值本身差得很远（result.json 的 `calibration.D.halves[h].thresholds`）：
CAND-A w=4 persist2 b1→b2 两半份阈值 5.804 vs 3.943（比值 1.47）；w=1 max b1→b2 是 9.229 vs 6.606
（比值 1.40）。也就是说 `scenario_halves` 的确定性交替划分产生的两半**不可交换**：
一半的校准阈值系统性地偏高、另一半偏低，**总 FAR 钉在 α 是两个方向相反的错误互相抵消的结果**。
这不是窄窗口带来的新问题——它在冻结窗宽上同样存在（CAND-A w=8 max b1→b2 5/80 vs 12/80）——
但它意味着 prereg H3 里"两半份 FAR 差随 w 变大"这个问法用一个无符号最大值来回答是不够的，
而且**部署时只有一个校准集，拿到偏高那一半就等于把 α 打了对折**。

## 5. 一个没被钉住的量：每 1000 个负例位置上的报警起始数

trace 级 FAR 被 conformal 钉在 α，但**位置级**的报警密度没有被钉住，它随窗宽单调上升
（result.json 里 `metrics.negative_alarm_onset_count / negative_endpoint_count`，负例 = 全部非 drift）：

| cand | reading | w=8 | w=4 | w=2 | w=1 |
|---|---|---|---|---|---|
| CAND-A | max | 1.695 | 1.726 | 2.108 | 2.088 |
| CAND-A | persist2 | 1.458 | 1.564 | 1.789 | **1.962** |
| CAND-B | max | – | 1.661 | 1.086 | 1.519 |
| CAND-B | persist2 | – | 1.173 | 0.958 | 1.171 |

CAND-A 上从 w=8 到 w=1，persist2 的位置级报警密度上升 35%，max 上升 23%，而 trace 级 FAR 不动。
如果部署侧的代价是"每次报警都要人看一眼"，窄窗口在 CAND-A 上是**净涨价**。CAND-B 非单调，w=2 最低。

## 6. 与 headline 表述的两处出入

1. 评估者写"CAND-A benign FAR is 1.8–4.0x clean FAR at every window"。按同一批数字实算，
   CAND-A 八个主格的 benign/clean 比值区间是 **1.89–5.25**（max 读数单独看也是 1.89–4.75，
   w=2 max 就是 0.158/0.033 = 4.75，已超过 4.0）。区间上界写小了。
2. 评估者写"The clean/benign IMBALANCE … does not obviously grow with narrowing"。这个结论本身
   站得住（非单调），但它的分子分母都是个位数：CAND-A w=2 的 clean 分子只有 4/120，比值 4.75 与
   w=8 的 1.89 之间差的是 5 条 trace。**在 n=120 上讨论 benign/clean 比值的窗宽趋势没有分辨力。**

## 7. 结论

1. **headline 的所有 FAR 数字都复算得上**，包括 14 个主格的 clean / benign / pooled / half |diff|
   和附录 D 的 28 行；窄窗口运行在冻结窗宽上与冻结 result.json **逐条 trace、逐个端点**相同。
2. **"FAR pinned at α"是真的，但它掩盖了一次大换血。** 在 w=1 上误报 trace 集合与冻结窗只重合
   27–43%，换血量级与"整块更换检测器"（Jaccard 0.29）相当；同一窗宽内 max 与 persist2 的一致性
   在 w=1 上跌到 0.20–0.32，低于跨候选一致性。窄窗口下"谁会误报"基本是任意的。
3. **换来的这批新误报的可解释性更差**：硬核（各窗宽都报）以 (a) 域外话题 / (b) JSON / (f) KB 复述
   为主，一次性误报以 (b)/(e)/(d) 为主；CAND-A max 的 unexplained 从 2 条涨到 6 条。
4. **clean 臂的 (a) 类误报在所有窗宽上都是 0**（0/103 行），CAND-A 的 benign/clean 不平衡基本上
   就是 benign_control 解码里复述 inert-label 外部注记这一件事。
5. **报警位置没有系统性前移 (8−w) 个 token**：同一批 trace 内中位只提前 1–4 个 token。
6. **两个校准半份不可交换**（CAND-A 15/16 个单元同向、CAND-B 9/12 同向，个别格 0/40 vs 9/40），
   总 FAR 达标是两侧误差抵消的结果；位置级报警密度在 CAND-A 上随窄窗口单调上升 23–35%，未被 α 钉住。

综合到 prereg 的判据上：H3 的"总 FAR 仍钉在 α"成立，"两半份 FAR 差与 benign/clean 比随 w 变大"
**在这批数据上分辨不出来**（非单调，且每格分子是个位数）。但本视角给出一个 prereg 没写、
却对 B3 更关键的负面结论：**窄窗口在保持 FAR 的同时把误报集合换掉了一大半，因此
"窄窗口不破坏误报约束"这句话只在聚合口径上为真，在"同一批 routine 流量上的行为稳定性"口径上为假。**

## 8. 局限

- 全部为 B1/B2 开发数据上的事后诊断；分类是本代理的人工判定，(b)/(f) 与 (c)/(d) 的边界与
  `false_alarms.md` §6 提到的是同一处软边界，换一个划法会在这几类之间移动约 10–15 行。
- 分类单元是 (trace, 报警位置簇)，簇内位置差 ≤ 4 token 视为同一处；同一 trace 在不同窗宽上
  若报在相隔较远的位置，会被算成两个不同类的事件（这正是要展示的现象）。
- clean/benign 各 120 条，1 条 trace = 0.0083 的 FAR；本文没有做 bootstrap 区间（prereg 把统计
  工作分给视角 (d)）。
- 位置级报警密度取自 result.json 的 `negative_*` 字段，其负例定义是"全部非 drift"（含 resist 臂），
  与本文其余部分的 clean+benign 口径不同，只能作趋势看。
- 未评估 mode T、未评估 α=0.05、未评估固定阈值 runlen 读数。

## 9. 复现

```bash
cd /home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363
export PYTHONPATH=$PWD/src:$PWD/scripts
P=/home/wzh/Agent-Moe-Research/.venv/bin/python
S=<scratch dir>
$P scripts/research_v2/narrow_window/audit_false_alarm_anatomy_extract.py  $S   # -> fa_rows.json / fa_summary.json
$P scripts/research_v2/narrow_window/audit_false_alarm_anatomy_classify.py $S   # -> fa_rows_classified.json + 类别表
$P scripts/research_v2/narrow_window/audit_false_alarm_anatomy_table.py    $S   # -> artifacts/.../audit_false_alarm_anatomy/fa_table.md
```

输入只读：两个窄窗口 result.json、两个冻结 result.json、B1/B2 冻结数据、本地 tokenizer。
