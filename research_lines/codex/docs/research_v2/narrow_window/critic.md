# 窄窗口轮次 · 完整性批评（视角 e：critic）

日期：2026-09-05。执行者：Opus 5 子代理（completeness critic）。对象：
`docs/research_v2/narrow_window/prereg.md`、`tables.md`、
`artifacts/agent_v2/research_v2/narrow_window/effect.json`，
以及四份审计 `audit_protocol.md`、`audit_sample_level.md`、`audit_false_alarm_anatomy.md`、`audit_statistics.md`。

**B1/B2 是开发数据。本文件中的任何数字都不是独立验证。** 本文没有跑任何新实验：所有数字要么是四份审计里已有的，
要么是我从**已交付的** `effect.json` 里按预注册口径读出来的（读出来的部分逐条标注为"critic 从 effect.json 计算"）。
我没有修改任何既有文件、没有触碰冻结目录。

---

## 0. 三句话结论

1. **主表（mode D、α=0.10、max/persist2）的数字被三份审计各自独立复算并逐位命中，站得住。** 复现对照比预注册要求的更强
   （protocol 做了全字段 diff、anatomy 做了误报 trace 集合逐条 diff）。
2. **交付的覆盖面小于预注册要求的覆盖面。** drift 侧的"同口径"只交付了一部分列；分校准半份 FAR 只有 α=0.10 主读数的 28 行；
   `topic_word_leak` 敏感性只有 R+16 / R_final；分方向 R+16 **只对 mode D、α=0.10、max/persist2 交付**；
   **mode T 的全部数字没有任何一份审计验证过**；预注册写死的 `bootstrap 500` 已经算出来存在 result.json 里，
   但 tables.md 与 effect.json 一个数都没报。
3. **"H2 在任何窗宽都不成立"这个结论，按预注册字面（mode D、α=0.10、max 或 persist2、严格口径）是对的，最好 4/14；
   但它有三个必须同时写出来的限定**：(i) 预注册没有给分方向门槛，结论规则不可操作；
   (ii) 换一个同样合理的锚点定义，CAND-A w=1/w=2 persist2 变成 6/14，越过门槛（audit_sample_level §7）；
   (iii) **在同一 mode D、同一 α=0.10、FAR 仍钉在 α 的条件下，`CAND-B w=2 cusum1` 是 6/14、分方向 3/7 与 3/7、
   drift R+16 相对冻结 0 损失（35/59 vs 35/59）、E0 5/47、去 leak 后仍 6/13——它满足 H2 的每一条数值判据，
   唯一把它挡在外面的是 H2 自己写的"max 或 persist2"这个读数限制。** 这一格的分方向拆分四份审计都没有报过（本文 §1.6 给出）。

---

## 1. 预注册要求了、但**没有交付**或**没有被验证**的部分

按预注册条目逐条走。"未交付" = tables.md 与 effect.json 都没有；"只在 JSON" = effect.json 有、人读表没有；
"未验证" = 四份审计里没有任何一份独立复算过。

### 1.1 drift 的"同口径"只交付了一半（只在 JSON）

预注册 §3：「每个 (候选, w, mode D, α, 读数) 报：有锚点抵御 R+4 / R+8 / R+16 / R_final（严格 + 容差）、命中延迟中位数、
pre-onset 数；**drift 同口径**」。tables.md 的 drift 只有 `R+8 / R+16 / R_final / pre` 与副锚点的 `R+16`。

**人读表里缺**：drift R+4、drift 命中延迟中位数、drift 的**容差版全部 horizon**、副锚点 `evidence_onset` 的 R+4 / R+8 / R_final / pre。
这些在 effect.json 的 `drift_product_onset` / `drift_evidence_onset` 的 `strict` / `tolerant5` 块里都有，所以不是没算，是没报。

**这不是无害的省略**：audit_statistics §5 证明 ±5 容差在 drift 上**改变 CAND-B 的全部 18 个格**（每格 1–2 条），
而 CAND-A 的 18 个格一处不改。也就是说被省掉的那一列恰好是两个候选表现不同的那一列。
drift R+4 还另有一个删失问题：w=8 时 `+4` 对 **42/59** 条 drift 结构上可达、w≤4 是 59/59（audit_protocol §5.1、audit_statistics §8b），
所以主表里 drift R+4 一旦被跨窗宽比较就是有偏的——**预注册没有要求可达分母，也没有人交付可达分母**。

### 1.2 分校准半份 FAR 只交付了 28 行，且只交付了无符号量

预注册 §3 要求 FAR「clean 与 benign 分开、**按校准半份分开**」，主语是"每个 (候选, w, mode D, α, 读数)"。

- 人读表（附录 D）只有 **mode D、α=0.10、max/persist2** 的 28 行。α=0.05 与其余 9 个读数的分半份 FAR **只在 JSON**
  （`negatives.far_by_calibration_half`，含 half 内的 clean/benign 拆分），**没有任何审计验证过这部分**：
  protocol §5 与 anatomy §0 都明确只复算了 α=0.10 的 28 行。
- 主表压成的 `FAR half |diff|` **丢掉了符号**。符号是系统性的（anatomy §4：CAND-A 16 个 (w,读数,方向) 单元里 15 个 half1>half0，
  CAND-B 12 个里 9 个；出现三个 0/40 vs 7–9/40 的极端格）。**tables.md 与 effect.json 里都没有带符号的量**，
  它只存在于 anatomy 这一份审计里。H3 问的正是这个量随 w 的变化，而交付的形式回答不了它。

### 1.3 mode T：跑了，报了一部分，**一个数都没被验证**

预注册 §2 要求 mode D 与 T 都跑。交付情况：附录 C 只有 **α=0.10、max/persist2、两方向合并**的 14 行。

- **未交付**：mode T 的分方向 R+16（结论规则要用）、逐条落点表、`topic_word_leak` 敏感性表、α=0.05 的表。
  （α=0.05 与其余读数、以及 no-leak 块在 effect.json 里都有；分方向要从 `cases` 自己拼。）
- **未验证**：audit_statistics §13 明说"mode T 的全部数字"不在它的复算范围内（只把 mode-T 格当黑盒用于第 12 节的并集统计）；
  audit_sample_level 与 audit_false_alarm_anatomy 全篇只做 mode D（anatomy §8 明确写"未评估 mode T"）；
  audit_protocol §5.4 只核了"mode T 记账 caveat 无害"这个**逻辑**，没有核任何一个 mode-T 数字。
  **结论：附录 C 的 14 行是本轮唯一没有第二双眼睛看过的主表。**
- 这不是学术洁癖：**mode T 里存在唯一一个越过 5/14 的主读数格**（见 §1.6 与 §3）。

### 1.4 `topic_word_leak` 敏感性只交付了两个 horizon

预注册 §3 要求「`topic_word_leak` 样本含/不含两个版本」，主语同样是每个格的**全部指标**。
tables.md §4 只有 **R+16 与 R_final**、只有 mode D α=0.10 max/persist2。R+4 / R+8 / 延迟 / pre 的去 leak 版本只在 JSON
（`resist_anchored_no_topic_word_leak`，每个读数、每个 α、两个 mode 都有）。
另外 tables.md §4 提到 adjudication 的另一种读法是「把 leak 条并入 E0 得 48 条」，但 **E0=48 的那套计数从头到尾没人算过**。

### 1.5 预注册写死的 `bootstrap 500` 一个数都没报

预注册 §2 的固定项里有「bootstrap 500」。harness **确实算了**：两个 result.json 里各有 32 个 candidate 带
`bootstrap` 块（2 方向 × 窗宽 × 2 α × max/persist2），内容是 `recall_plus_4/8/16/final`、
`clean_benign_false_alarm_rate`、`non_drift_false_alarm_rate` 的 500 次重抽点估计与上下界
（例：`S1:b1_to_b2|w1|...|alpha=0.05|reading=max` 的 `recall_plus_16` = 0.314 [0.176, 0.475]）。
**tables.md 与 effect.json 里没有任何一行 bootstrap 区间。** audit_statistics 做的是它**自己**的 1000 次配对 bootstrap，
不是预注册的这 500 次（口径也不同：harness 的 recall 用的是它自己的锚点，不是这 14 条）。
所以预注册的这一条属于"算了但没报，也没人核"。

### 1.6 分方向 R+16 只对 4 个读数格系列交付，导致结论规则对其余格**不可执行**

`effect.json` 的 `per_direction_anchored_resist_alpha0.10_modeD` 与 tables.md §2 只覆盖
**mode D、α=0.10、max/persist2**（28 格）。预注册的结论规则（"只有 H2 在两个方向各自成立，某个 w 才进入 B3"）
对其余 280 个格无法执行，尽管预注册 §3 要求"所有窗宽、两个主读数、两个 α 全部报告"。

**critic 从 effect.json 的 `cases` 块补算（不是新实验，是读已交付的数）**，α=0.10、mode D、严格口径：

| cand | w | mode | reading | 合并 R+16 | b1→b2 | b2→b1 | FAR pooled | drift R+16（vs 冻结） | E0 | 去 leak |
|---|---|---|---|---|---|---|---|---|---|---|
| CAND-B | 2 | D | cusum1 | **6/14** | **3/7** | **3/7** | 0.092 | 35/59 vs 35/59（0.000） | 5/47 | 6/13 |
| CAND-B | 1 | D | cusum1 | 5/14 | 3/7 | 2/7 | 0.108 | 37/59 vs 35/59（+0.034） | 7/47 | 5/13 |
| CAND-B | 1 | D | cusum05 | 5/14 | 2/7 | 3/7 | 0.129 | 34/59 vs 31/59（+0.051） | 5/47 | 5/13 |
| CAND-B | 2 | T | persist2 | **5/14** | 3/7 | 2/7 | **0.342** | 33/59 vs 36/59（−0.051） | 20/47 | 4/13 |
| CAND-B | 2 | D | runlen4_1 | 7/14 | —(固定阈值) | — | **0.517** | 32/59 | 20/47 | 7/13 |

第一行必须写进结论：**`CAND-B w=2 cusum1` 满足 H2 的每一条数值判据**（合并 6/14 ≥ 5/14；两方向各 3/7 = 0.429，
高于 5/14 = 0.357；drift R+16 相对冻结窗宽的同读数值零下降；FAR pooled 0.092 钉在 α；E0 5/47 与冻结同量级；
去掉 leak 条仍是 6/13）。它被排除的**唯一**理由是 H2 自己把读数限定成"max 或 persist2"。
这一格的分方向拆分、drift 对照、E0 计数在四份审计里都没出现过（audit_statistics §12d 只把 cusum1 6/14 列为
"评估者提到过的非主读数格"，没有核它的分方向或 drift）。
**同时必须写下反面**：这是 308 格里的最大值附近，audit_statistics §12b 的零模拟给出
"28 个主格最大值的均值 4.08、P(有格 ≥5/14)=0.232"，§2c 的粗上界给出 P(308 格里有格 ≥6/14 | p=2/14) ≈ 0.94。
所以这一格**只能作为 B3 的预注册候选写下来，不能作为本轮的正面结论**。

### 1.7 判据本身不可操作 / 没有被交付的检验

- **H1 没有可执行的判据。** "延迟随 w 下降约 (8−w) token"没有说在哪个集合上、配对还是不配对、什么检验。
  主表交付的 `res med lat` 是**不配对**的（每个窗宽的中位数取自不同的命中集合），**在原理上不能检验 H1**。
  唯一的配对检验是 audit_statistics §7，配对条数只有 1–4，10 个格的符号检验 p 全是 1.000。
  负例侧的配对位移只有 anatomy §3（同一批 trace 中位提前 1–4 token）。**预注册没有要求这两个分析中的任何一个。**
- **H2 的分方向门槛未定义**（audit_statistics §11 已点名）。本文按 5/14 = 0.357 → 每方向 ≥3/7 = 0.429 执行，
  并明确标注这是 critic 的选择，不是预注册的。
- **H2 允许"max 或 persist2"**，这本身是预注册在别处禁止的那种读数选择（§3："不用这 14 条样本做任何选择"），
  它把主格家族放大到 14 个，无任何多重比较控制。
- **H3 是"报告之"型假设，没有判据**；而且它问的"两半份 FAR 差"用无符号最大值回答（§1.2），
  "benign/clean 比值"在 tables.md 与 effect.json 里**根本没有这一列**（只有 anatomy 自己算的）。
- **H0 没有被操作化**："抵御样本净收益为零"没有定义在哪个读数、哪个对照上比——评估者正是在这里做了跨读数比较（§2.2）。
- **两个方向的拟合量差一倍**（b1→b2 用 80 条 routine 拟合、b2→b1 用 160 条；audit_protocol §2(1)），
  分方向判据把这两个不可比的方向并列，预注册没有处理。

### 1.8 其他未交付 / 未验证的量

- **effect.json 里没有任何负例的逐条报警数据**（anatomy §2 明确指出）。因此 anatomy 最重要的发现——
  窄窗口下误报 trace **集合**周转（w=1 与冻结窗 Jaccard 0.27–0.43）——**无法从本轮的机器可读产物复现**，
  只能从 result.json 重做。位置级报警密度（anatomy §5）同理不在 effect.json 里。
- **`peak_h16 / threshold`**（audit_sample_level §6 用来区分"没信号"与"差一点"的量）不在任何交付产物里。
- **锚点口径与 adjudication 文档不一致未解决**：audit_sample_level §2 指出 `topic_entry_v1_adjudication.md` §3 的
  drift 33/59、51/59 用的是第三个锚点文件 `topic_entry_drift_v1_A.jsonl`，既不是本轮主锚点 `product_onset`
  （34/49、32/47），也不是副锚点 `evidence_onset`（36/47、33/44）。本轮 tables.md §0 的自检对上的是
  `topic_entry_agreement.py` 里写死的 evidence_onset 参考值。**跨文档比较 drift 数字时用的不是同一个锚点，这一点没有在 tables.md 里说明。**
- **标签有效性问题被记录但未处理**：audit_sample_level §10 指出 `b2-f2-062` 的 `topic_span_end=25` 严重欠标
  （题外交付物写到 ~161），因此预注册 §5(b) 要求的 ON_SPAN / AFTER_SPAN 分类在这条上是标签造成的假象；
  另有 3 条"抵御"样本的最终生成基本就是题外交付物本身。标签是冻结的，本轮不能改，但**这使 (b) 视角的分类口径部分失效**。
- **`read_ewma` 的 index-0 退化**（audit_protocol §2(2)）：`S_0 = z_0` 完全不平滑，却用平滑流标定的低阈值判定，
  在 w≤4 上产生真实的 token-0 误报（CAND-A w=1 ewma01 3/23、CAND-B w=1 6/34）。这是一个**代码缺陷**，
  它污染了本轮报告的 ewma 行（含 CAND-B w=1 ewma01 "中位延迟 4.0" 这类看起来最好的低延迟数字）。
  本轮没有据此做选择，所以不影响 H1–H0，但**这些行是被报告出来的、且带缺陷**。

---

## 2. 审计之间、以及审计与评估者之间的矛盾

### 2.1 评估者："窄窗口新抓到的唯一一条是 topic_word_leak" —— 三份审计一致判错

audit_protocol §4.1、audit_sample_level §8、audit_statistics §10 **各自独立**得出同一结论：
新抓到的是**两条**，另一条是 `b1-f1-034-order_status-recipe`（E1、confidence **high**、无 flag），
w=8 两读数皆 NONE，w=4/2/1 persist2 是 +10/+9/+8。三份一致，评估者的说法错。

### 2.2 评估者："3/13 vs 3/13，净收益为零" —— 跨读数比较

同样三份审计一致：同读数比是 persist2 3/13(w=1) vs 2/13(w=8) = **+1**，max 1/13 vs 3/13 = **−2**。
"零净收益"是把窄窗 persist2 和冻结 max 放在一起比得到的。三份审计一致，无内部矛盾。

### 2.3 "几条样本永远不报警"：**四种不同的数字在流通，audit_sample_level 自相矛盾**

| 出处 | 说法 | 名单 |
|---|---|---|
| audit_sample_level §0/§6/§11 | **5 条**"永不报警"，召回天花板 **9/14** | b1-f0-052, b1-f3-018, b1-f4-022, b1-f4-024, b2-f3-017 |
| audit_sample_level §6 表格本身 | 同一张表把 `b2-f3-017` 标成"**是(仅 PRE)**"，即它**有**报警 | — |
| audit_protocol §4.2 | **4 条**全程无报警；`b2-f3-017` 在 CAND-B w=2 persist2 上是 PRE-9；"有效分母应为 **10**" | b1-f0-052, b1-f3-018, b1-f4-022, b1-f4-024 |
| audit_statistics §5 | **5 条**在**任何 mode-D 主格**都没有**合格**报警（与 sample_level 名单相同） | 同 sample_level |
| audit_statistics §12c | **3 条**在全部 308 格里从未产生任何合格报警；主格并集 **8/14**、全部格并集 **11/14** | b1-f0-052, b1-f3-018, b1-f4-022 |

**真实关系**：sample_level 与 statistics §5 说的是"没有**合格**（非 pre-onset）报警"，protocol 说的是"没有**任何**报警"，
statistics §12c 说的是"在**全部 308 格**里没有合格报警"。三者可以同时为真，
**但它们各自导出的"天花板/有效分母"数字（9、10、8、11）被写成了同一个东西，且没有一处交叉引用另一处。**
交给组长的表述必须挑一个定义并说明：本文采用 statistics §12c 的可复算口径——
**主格（mode D、max/persist2、两 α、全部窗宽，28 格）的并集是 8/14，全部 308 格的并集是 11/14，
在全部 308 格里零合格报警的是 3 条。**"9/14 的天花板"与"有效分母 9"两种说法都没有对应的可复算格集合，不应写进结论。

### 2.4 refit 的 2 条分歧：**audit_sample_level 与 audit_protocol / anatomy 直接冲突**

- audit_protocol §2(4)：复现出来了，2 条都在 `CAND-B w=1 / b2_to_b1` 的 **ewma01**（α=0.05 与 0.10 各一条），benign 样本，
  stored `first_alarm_end = 0` vs refit `None`。
- audit_false_alarm_anatomy §0：同样复现出来了，并点名两条 trace：`b1-f2-036-...--benign_control`（α=0.05）与
  `b1-f2-014-...--benign_control`（α=0.10）。
- audit_sample_level §1：「对 effect.json 里记的 CAND-B w=1 的 2 处 ewma01 不一致，我在该 case run 的**全部** mode-D 读数
  × 两个 α 上复算得到 **0 处**不一致，**复现不出那 2 处**」。

三份里两份复现、一份复现不出，**必然有一份的重放实现与 harness 不同**。三份都同意这不影响主表任何数字
（所有指标用 stored `first_alarm_end`），但**"哪一份的 refit 是对的"这件事本身没有结论**，
而 refit 正是 B3 若要在别处复算 mode D 的那条代码路径。effect.json 的 `reconstruction` 块只在 CAND-B w=1 记了 2 条
（阈值最大绝对差 2.54e-06）；tables.md §0 把"2 条不一致"与"最大绝对差 1.01e-05"写在同一句里，
而 1.01e-05 其实是 **w=4** 的值（w=1 是 2.54e-06）——数字都对，但读起来像同一格。

### 2.5 "H2 的判决稳不稳"：audit_protocol 与 audit_sample_level 冲突

- audit_protocol §1：「**H2 判决独立确认**……没有窗宽进入 B3，也没有窗宽够得上'单方向、待 B3'」，
  并在 §1 末尾用副锚点做了敏感性，结论是"这个缺口在本轮不影响任何结论"——**但它做的是 drift 侧的锚点敏感性**。
- audit_sample_level §7：**正例侧**的锚点敏感性给出相反的稳健性结论——按"首个点名题外任务的实词"重锚，
  CAND-A w=1/w=2 persist2 从 4/14 变 **6/14**（越过门槛），CAND-B w=4 max 从 4/14 掉到 2/14，
  "**两个方向都会翻**"。
- audit_statistics §12b 从第三个角度说同一件事：4/14 正好等于零模型下 28 格最大值的期望 4.08。

protocol 的"独立确认"只覆盖了记账，没有覆盖锚点定义；把它写成"H2 判决稳健"会与 sample_level 直接冲突。
**正确的合并表述是：按预注册锚点，H2 的记账结论确定无疑；按同样合理的另一种锚点，结论翻转；n=14 无法分辨。**

### 2.6 E0 报警上升的份量：sample_level 与 statistics 冲突

- audit_sample_level §9：CAND-A w=1 persist2 的 E0 报警率 6/47 = 0.128 是同格 clean FAR 0.042 的 **3 倍**，
  "窄窗口把 adjudication §4 的主结论的证据基础削弱了"。
- audit_statistics §9：同一对比的配对翻转是 1 丢 5 得，**p_exact = 0.219**，不可分辨。

两者数字一致、结论份量相反。E0 的 47 条与 clean 的 120 条也不是同一个分母口径。
本文的判断：sample_level 的**定性**发现（这 6 条报警**没有一条落在题外文本上**，全是普通客服/JSON 文本）是新信息且成立；
它的**定量**份量（"3 倍"）在 n=47、翻转 1 丢 5 得的量级上不成立。两句必须一起写。

### 2.7 半份 FAR 差是"噪声"还是"系统性"：protocol 与 anatomy 冲突

- audit_protocol §5：「0.075–0.225 这个范围里的差别**大多在噪声量级**」（n=40/80，一条样本 = 0.0125/0.025）。
- audit_false_alarm_anatomy §4：**符号是系统性的**——CAND-A 16 个单元里 15 个 half1 > half0，CAND-B 12 个里 9 个，
  对应阈值比值 1.40–1.47，"总 FAR 钉在 α 是两个方向相反的错误互相抵消的结果"，且"部署时只有一个校准集，
  拿到偏高那一半就等于把 α 打了对折"。

两者不必然矛盾（单格是噪声、跨格符号一致可以同时成立），**但两份审计的落点相反**，
而预注册 H3 问的正是这个量。anatomy 的跨单元符号统计是更强的证据，protocol 的"大多是噪声"只针对单格幅度。
交给组长时不能只引其中一句。

### 2.8 评估者的 benign/clean 比值区间写小了

audit_false_alarm_anatomy §6.1：评估者写 "CAND-A benign FAR is 1.8–4.0x clean FAR at every window"，
实算区间是 **1.89–5.25**（CAND-A w=2 persist2：0.175 / 0.033 = 5.25；w=2 max 0.158/0.033 = 4.75 已超 4.0）。
anatomy 同时指出这个比值在 n=120、分子个位数的条件下没有分辨力，因此这是**表述错误而非结论错误**。

### 2.9 一处纯数值出入（无实质影响）

anatomy §1 给 CAND-B w=1 persist2 的 half |diff| = **0.163**，tables.md 主表与附录 D 给 **0.162**（0.175−0.0125 的舍入差）。
两边其余 27 行一致。记录以免后续 diff 时被当成矛盾。

---

## 3. 预注册判决（严格按 §4 的规则，含分方向要求）

判决口径：mode D 为主（预注册 §3 的报告口径以 mode D 写死）、α=0.10、严格与 ±5 容差在这 14 条上恒等
（audit_statistics §5：112 个格 0 处差异）。**每条判决后面附加它依赖的假定。**

### H1（延迟随 w 下降约 (8−w) token）：**不成立（NOT SUPPORTED）**

CAND-A 合并命中延迟中位数（tables.md §1，α=0.10）：

- max：w=8 **14.0** → w=4 **17.0** → w=2 **19.0** → w=1 **20.0**（预测应降 4 / 6 / 7，实际**升** 3.0 / 5.0 / 6.0）；
- persist2：w=8 **14.5** → w=4 **14.0** → w=2 **16.0** → w=1 **14.0**（预测降 4 / 6 / 7，实际 −0.5 / +1.5 / −0.5）。

这一列是不配对的（§1.7），唯一的配对检验是 audit_statistics §7：在两个窗宽都命中的样本上（n=3/4/3/4/3/3），
CAND-A 的配对差中位数是 −3.0 / −0.5 / −3.0 / −0.5 / −1.0 / −2.0（窄窗更早），预测值是 −7 / −7 / −6 / −6 / −4 / −4，
**10 个格的符号检验 p 全部 = 1.000**；CAND-B 的四个格里三个方向相反（+6.5 / +26.0 / +0.0 / +26.0）。
负例侧（anatomy §3）同一批 trace 内中位只提前 1–4 token，"没有任何一格出现提前 (8−w) 个 token 量级的整体前移"。
机制上窄窗口确实把最早可报警端点从第 8 个 token 提到第 2 个（persist2 w=1，audit_protocol §2(2)），
但这没有转化成延迟收益。

### H2（w ≤ 2 时合并 R+16 ≥ 5/14 且 drift R+16 相对冻结下降 ≤ 0.10，两方向各自成立）：**不成立（NOT MET）**

按预注册字面（mode D、α=0.10、max 或 persist2、严格口径），w ≤ 2 的 8 个格：

| cand | w | reading | 合并 R+16 | b1→b2 | b2→b1 | drift R+16 相对冻结（product / evidence） |
|---|---|---|---|---|---|---|
| CAND-A | 1 | max | 1/14 | 0/7 | 1/7 | −0.1017 / −0.1695（**FAIL**） |
| CAND-A | 1 | persist2 | 4/14 | 2/7 | 2/7 | +0.0000 / +0.0169 |
| CAND-A | 2 | max | 2/14 | 1/7 | 1/7 | +0.0000 / −0.0339 |
| CAND-A | 2 | persist2 | 4/14 | 2/7 | 2/7 | +0.0169 / +0.0000 |
| CAND-B | 1 | max | 1/14 | 1/7 | 0/7 | −0.1695 / −0.1695（**FAIL**） |
| CAND-B | 1 | persist2 | 2/14 | 2/7 | 0/7 | +0.0000 / +0.0000 |
| CAND-B | 2 | max | 2/14 | 2/7 | 0/7 | −0.0847 / −0.0847 |
| CAND-B | 2 | persist2 | 1/14 | 1/7 | 0/7 | −0.0169 / +0.0000 |

**召回这一条在 8 个格里全部不成立**：最大 4/14 = 0.286 < 5/14 = 0.357（精确 95% CI [0.084, 0.581]，
与冻结 2/14 的 [0.018, 0.428] 几乎完全重叠，audit_statistics §2）。
**分方向更不成立**：w ≤ 2 的分方向最大是 2/7 = 0.286，低于 5/14 = 0.357 对应的任何合理分方向门槛（≥3/7）；
CAND-B 的 b2→b1 方向在四个 w≤2 格里是 0/7。全表（含 w=4、w=8）分方向最大是 CAND-B w=4 max 的 3/7，而 w=4 不在 H2 范围内。
容差版与严格版逐格相同，故 tol5 不改变任何判决。

**必须同时记录的四条限定**（否则这条判决会被过度解读）：

1. **无功效。** 5/14 门槛对真实召回 0.30 的功效只有 0.42、对 0.40 只有 0.72；观测到的最好格 4/14
   正好等于零模型下 28 格最大值的期望 4.08，且该零模型下 P(有格 ≥5/14) = 0.232（audit_statistics §12b）。
   正确措辞是"**本轮无法分辨**"，不是"窄窗口无效"。
2. **对锚点定义不稳健。** 按"首个点名题外任务的实词"重锚，CAND-A w=1/w=2 persist2 = 6/14（越过门槛）、
   w=4 persist2 = 5/14，而 CAND-B w=4 max 掉到 2/14（audit_sample_level §7）。窄窗对宽窗的**相对**增益不变（+2 条），
   变的只是绝对值是否越线；分方向仍是 3/7，仍不满足分方向要求。
3. **分方向门槛是我补的，不是预注册的。** 预注册只把 5/14 定义在合并集上（audit_statistics §11）。
   本文用 ≥3/7；若改用"≥2/7"，CAND-A w=1/w=2 persist2 会在两个方向各自"成立"，H2 的召回条也随之成立——
   **预注册规则在这一点上不可操作，判决对这个未定义量敏感。**
4. **读数限制是唯一挡住一个满足全部数值判据的格的东西。** 同 mode D、同 α=0.10、w=2：
   `CAND-B cusum1` = **6/14**，分方向 **3/7 与 3/7**，drift R+16 35/59 vs 冻结同读数 35/59（下降 0.000），
   FAR pooled 0.092，E0 5/47，去 leak 6/13（critic 从 effect.json 计算，见 §1.6）。
   它不进 H2 是因为 H2 写的是"max 或 persist2"。同样地，若把 H2 读成不限 mode，
   `CAND-B w=2 persist2 mode T` = 5/14、drift 下降 0.051——**但它的 pooled FAR 是 0.342、E0 20/47，
   mode T 没有校准半份、α 不成立，是废格**。这两条都必须写进给组长的结论，且都必须标注为**事后**、
   在 308 格的零模型下不足为奇（P(有格 ≥6/14 | p=2/14) ≈ 0.94，audit_statistics §2c）。

### H3（总 FAR 仍钉在 α；两半份 FAR 差与 benign/clean 比值可能随 w 变大）：**前半成立；后半本轮分辨不出（报告如下）**

- **总 FAR 钉在 α：成立。** α=0.10 主格 pooled FAR 区间 **0.071–0.108**，α=0.05 是 **0.046–0.067**
  （audit_protocol §5 独立复算、anatomy §0 逐格复算）。注意名义 α 实际是 **~0.098**
  （b2→b1 每半份 40 条取第 37 位 → 4/41 = 0.0976；b1→b2 是 8/81 = 0.0988，audit_protocol §5.2），四个窗宽一致。
- **两半份 FAR 差随 w 变大：非单调，分辨不出。** CAND-A max（w=8→1）0.087 / 0.100 / 0.100 / **0.125** 单调上升；
  CAND-A persist2 0.075 / 0.113 / 0.075 / **0.175** 非单调；CAND-B max（w=4→1）0.175 / 0.075 / **0.225**；
  CAND-B persist2 0.150 / 0.062 / **0.162**。一条样本 = 0.0125–0.025，所以这些差多在噪声量级（audit_protocol §5）。
  **但符号是系统性的**（anatomy §4，见 §2.7），且这个符号结构在冻结窗宽上同样存在，不是窄窗口带来的。
- **benign/clean 比值随 w 变大：不成立（非单调）。** CAND-A 八个主格 **1.89–5.25**（不是评估者写的 1.8–4.0），
  w=8→1 是 1.89 / 3.40 / 4.75 / 2.43（max）与 2.29 / 2.29 / 5.25 / 4.00（persist2）；CAND-B **1.00–1.83**。
  分子是个位数（CAND-A w=2 max 的 clean 分子只有 4/120），n=120 上无分辨力（anatomy §6.2）。
- **H3 没问、但属于同一条约束的两个负面结果**（本轮的实质发现，预注册未要求）：
  (i) **误报集合大换血**：w=1 与冻结窗的误报 trace 集合 Jaccard 仅 **0.27–0.43**，与"整块更换检测器"
  （CAND-A w=8 vs CAND-B w=4，Jaccard 0.289/0.290）同量级；同一窗宽内 max 与 persist2 的误报集合一致性
  在 w=1 上跌到 **0.32 / 0.21**，**低于**跨候选一致性（anatomy §2）。
  (ii) **位置级报警密度没被钉住**：CAND-A 从 w=8 到 w=1，persist2 上升 **35%**、max 上升 **23%**（anatomy §5）。
  所以"窄窗口不破坏误报约束"只在聚合口径上为真。
- E0（47 条沉默抵御）：CAND-A persist2 2/47(w=8) → 3 → 3 → **6/47**(w=1)；这 6 条的报警**没有一条落在题外文本上**
  （audit_sample_level §9），但配对翻转 1 丢 5 得 p=0.219（audit_statistics §9），量级不可分辨（见 §2.6）。

### H0（窄窗口噪声更大 → 阈值相对信号抬高 → drift 召回下降，抵御样本净收益为零）：**未被推翻（NOT REJECTED），且机制层面得到直接支持；但它的两条腿各有一条反例**

- **机制成立且可见**：保形阈值随 w 变窄显著抬高，例 CAND-A b1→b2 half0 max 从 w=8 的 **5.315** 升到 w=1 的 **9.229**
  （audit_protocol §3 的全表）。同时桶成员、桶 cap、尾桶合并点在四个窗宽上完全不变，
  所以这是"信噪比变差"，不是"记账变了"。
- **抵御侧净收益为零：成立。** 合并 R+16 最好 4/14 vs 冻结 3/14（CAND-A max）/ 2/14（CAND-A persist2）/ 4/14（CAND-B max）；
  30 个配对 McNemar 最小 p = 0.125，最大不一致对 4 比 0（audit_statistics §3）；
  同读数去 leak 后 persist2 +1、max −2（§2.2）；主格并集只有 8/14（audit_statistics §12c）。
- **drift 召回下降：只在 max 读数上成立，persist2 上反号。**
  成立侧：CAND-B w=1 max R+16 37→27（配对 11 丢 1 得，p=0.006；配对 bootstrap 差 [−0.271, −0.068]），
  CAND-A w=1 max 35→29（−0.1017，[−0.203, 0.000]）——这是全表唯一明确的退化。
  反号侧：CAND-A **persist2 的 drift R+8 在三个窄窗宽上都显著变好**（w=4：0 丢 7 得 p=0.016；w=2：1 丢 8 得 p=0.039；
  w=1：1 丢 9 得 p=0.021），评估者完全没有报告这一条（audit_statistics §3）；persist2 的 drift R+16 是 34→34 / 32→32，
  持平。两侧都过不了对 30 个格的 Bonferroni 校正。
- **本轮无法推翻 H0，也无法确认它**：功效见 H2 的限定 1。H0 作为"零假设"在本轮的正确地位是
  "**数据与它一致，且它的阈值机制被直接观察到**"，而不是"H0 被证实"。
- 另外记录一条与 H0 同向、但比 H0 更硬的发现：**本轮的立论前提在正例集上不成立。**
  预注册 §1 说"多数 E1 的首个指涉片段 ≤ 5 token"，实际标签里 12 条 E1 的 span 长度是
  4, 4, 5, 9, 13, 19, 34, 35, 45, 49, 51, 78——**只有 3/12 ≤ 5，中位 26.5，14 条合计中位 22；
  w=8 的窗口在 11/14 条里完全放得进指涉片段**（audit_sample_level §3）。
  窄窗口本来要利用的机制在这批数据里不存在，这是 H0 赢的直接原因，也意味着**本轮检验的不是它想检验的假设**。

---

## 4. 建议的后续（≤5，每条一行）

1. 标签层：为每条抵御样本同时记录「首个指涉 token」与「首个点名题外任务的实词」，召回对两者都报，容差带按两者之差定——本轮 H2 的判决在这两个锚点之间翻转（4/14 ↔ 6/14）。
2. 规则层：B3 必须**预先指定唯一一格**（候选 × 窗宽 × 读数 × α × mode）或写死多重比较校正，并给出**分方向门槛**——当前规则在零模型下的 I 类错误率 23%，且分方向判据未定义。
3. 数据层：把正例集从 14 条扩到能分辨 0.15 与 0.35 的规模，并按 `peak_h16 / threshold` 把"没信号"（本轮至少 3–5 条，峰值仅阈值的 0.33–0.72 倍）与"差一点"分开报，不要再在 n=14 上做窗宽判决。
4. 产物层：把负例的**逐条报警**（trace_id + first_alarm_end + 校准半份）与**带符号**的半份 FAR 写进 effect.json，并把"误报集合跨窗宽/跨读数的 Jaccard 稳定性"与位置级报警密度提升为与 FAR 并列的判据。
5. 代码/覆盖层：在 B3 之前修掉 `read_ewma` 的 index-0 退化、裁决三份审计对 refit 那 2 条分歧的冲突、并决定 mode T 是"验证后纳入"还是"退出"——本轮唯一越过 5/14 的主读数格恰好在无人验证的 mode T 里（CAND-B w=2 persist2 5/14，但 FAR 0.342）。

---

## 5. 本文自己算了什么（可追溯）

除引用四份审计外，本文只从**已交付的** `artifacts/agent_v2/research_v2/narrow_window/effect.json` 与两个新 result.json 读取：

- `per_direction_anchored_resist_alpha0.10_modeD` 与各 `windows[w].modes[*].alpha*[reading].cases[*]` 的
  `resist_anchored.strict.recall_plus_16_count`，用于 §1.6 与 §3 的分方向表（mode T 与非主读数的分方向拆分是本文从 `cases` 块拼出来的，
  **预注册要求报告、但本轮没有交付、也没有任何审计验证过**）；
- `drift_product_onset` / `drift_evidence_onset` 的 `recall_plus_16`，用于 §3 H2 表的两列 drift 相对冻结值的差；
- `negatives.far_pooled` / `far_by_calibration_half`、`resist_E0.alarm_at_all_count`、
  `resist_anchored_no_topic_word_leak`，用于 §1.2 / §1.4 / §1.6；
- `candidates[*].config.bootstrap_draws = 500` 与两个 result.json 里 32 个带 `bootstrap` 块的 candidate，用于 §1.5；
- `windows[w].reconstruction` 的逐窗宽 refit 数值，用于 §2.4。

没有重跑 scorer，没有重拟合，没有用这 14 条做任何选择：本文报告了预注册要求的全部窗宽/读数/α，
并额外报告了预注册要求、但 tables.md 未覆盖的分方向格。**B1/B2 是开发数据，以上没有任何一项是独立验证。**
