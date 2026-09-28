# 冻结审阅 v3.2 —— 数据 / 标签 / 两阶段解封（lens: data）

> 审阅对象：`docs/research_v4/detector_prereg_v3_2_draft.md`（含 §19 组长裁定，约束性）。
> 设计来源：`docs/research_v4/v3_2_design_note.md`；v3.1 教训：`freeze_review_resolution.md` / `g_dev_confirmatory_report.md`。
> **本次审阅不评判 §13 的代码映射行**（并行实现方正在落地），只评判正文、统计口径与数据/标签/解封逻辑。
> **审阅期间未读取** `artifacts/agent_v2/dataset_g/g_conf`（路由，封存）与 `annotations/g_conf`（标注进行中）；
> 全部数值来自 `configs/dataset_g/*.json`（元数据）、已解封的 G-dev 文档与仓库代码。
> 只报**已核实**的问题。等级：BLOCKING / SHOULD-FIX / NOTE。

复现命令（全部只读元数据）：

```bash
cd /home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363
PYTHONPATH=$PWD/src:$PWD/scripts /home/wzh/Agent-Moe-Research/.venv/bin/python - <<'PY'
import json, collections
c = json.load(open('configs/dataset_g/g_conf.json')); sc = c['scenarios']
rec = {s['pair_group_id']: s for s in sc}; ids = sorted(rec)
for K in (3, 4, 5):
    t = collections.defaultdict(collections.Counter)
    for i, s in enumerate(ids): t[rec[s]['factory']['fixture_id']][i % K] += 1
    print('K=', K, {k: [t[k][i] for i in range(K)] for k in sorted(t)})
PY
```

---

## 汇总

| id | 等级 | 节 | 一句话 |
|---|---|---|---|
| **DATA-1** | **BLOCKING** | §2.2 / §5 / §11 | `fold(s) = sorted(scenario_ids) mod 3` 在 G-conf 上**与 fixture（店铺世界）几乎完全共线**：每折的攻击 scenario 100% 来自同一个 fixture，而该 fixture 在它的保形参照折里**一条都没有**。K = 3 是唯一退化的取值（K = 4 / 5 均衡）。G-dev 有 4 个 fixture，所以开发集上的 FAR 0.115 读数**不迁移**，G-dev 排练也**测不出**这个缺陷 |
| **DATA-2** | **BLOCKING** | §2.4 vs §4.2 / §4.3 / §8.2 | "可达性"的定义自相矛盾：§2.4 要求 `X+16` 未被 H 删失（G-dev = 108/126），而主格分母、检验力与全部先验都用 125/126 的口径。两者相差 14%，直接改变主格 N 与 R 的含义 |
| **DATA-3** | **BLOCKING** | §3.2 / §3.3 / §12.2 | `threshold_manifest.json` 的字段表**不足以冻结阈值**：没有 P / M / J 各自的参照集、标准化器与阈值（每折只有一套），没有长度三分位切点（E3 裁定要求写入），q 表只有 sha256、白化没有槽位；而 §3.3 又禁止阶段 2 执行任何拟合 / 校准路径 → 按字面执行，S2 与 S-J 跑不出来 |
| **DATA-4** | SHOULD-FIX | §9.1 / §4.4 / §17.2 | N1（≥ 85%）与 N2（逐折每档 ≥ 60）没有按"轮转 + 现行标注口径"重标：按预注册自己的先验（G-dev 正常臂 0.718），N2 要求过滤通过率 ≥ 80.4% 才可能成立，N1 按构造失败；§17.2 的门预判表**完全没有 N 门** |
| **DATA-5** | SHOULD-FIX | §4.3 / §9.3 D1x | N 的投影链算出 65，正文却写"共同分母 ≈ 76 / 规划 62–80"；且 D1x（标注侧 ≥ 62）守不住"配对样本 N ≥ 62"——D1x 恰好过门时 N ≈ 53–61，落在检验力网格最低格之外 |
| **DATA-6** | SHOULD-FIX | §16.1 第 3 条 / §11.2 第 3 条 | "126 条里只有 103 条落在 352 以内（≈18% 在 H 之外）"是对诊断表的**误引**（103 是 `[X+32, …]` 窗口的样本数），与 §4.2 的 108/126 = 14.3% 冲突 |
| **DATA-7** | SHOULD-FIX | §2.3 | H = 352 的论证用的是 **token 轴**存活表（k = 320 / 384），而 H 的单位是 **look**（`h_freeze_note.md`：look ≈ 0.90 × token，第 352 个 look 落在 token 379–393）。结论方向不变，但正文写的存活区间与删失比例是错轴的 |
| **DATA-8** | SHOULD-FIX | §3.2 | `stage1_attack_traces_skipped` 的期望值写成"G-conf = 480 条攻击臂 trace"；G-conf 的攻击臂 trace 是 **160** 条（480 是攻击 cell 的三臂 trace 总数）。这是"阶段 1 确实没读攻击臂"的唯一机械留痕，期望值写错等于这条留痕不可核 |
| **DATA-9** | SHOULD-FIX | §15.4 / §15.5 | 冻结程序不可执行：§15.4 的表要求在 B / M1（冻结**之后**）把哈希填进**已冻结的正文**，与 §15.5"冻结后任何改动 = 新 proposal"冲突；且哈希表**漏了 `configs/dataset_g/g_conf.json`**——折映射的 scenario 全集与 16 个家族的大小向量都出自它 |
| **DATA-10** | NOTE | §2.2 / §7.3 逐折列 | 折间 `wording_tier` 不均：攻击 scenario 的 T0 是 24 / 12 / 12（T1、T2 各有一折翻倍），与 `(family × tier)` 稳健列和逐折召回的可比性相关 |
| **DATA-11** | NOTE | §2.3 / §9.1 N3 / §17 | 逐折参照集在 H 处的删失比例投影到 **≈ 70%**（H 冻结时的 G-cal 池是 31.9%），预注册只要求"落盘"，没有把这个量级写进 §17 的先验，也没有把它列为 F3 失败的候选机制 |
| **DATA-12** | NOTE | §9.3 | "阻塞门"（D1 / D1x / D3 / D5）不达标的后果只写成"写进范围声明、不补样"，没有说是否仍然开启阶段 2；"阻塞"二字与后果不符 |

---

## DATA-1（BLOCKING）折函数在 G-conf 上与 fixture 共线

**主张**：`fold(s) = index_of(s in sorted(scenario_ids)) mod 3`（§2.2 / §5 冻结项）在 G-conf 的 280 个 scenario 上
不是一个"打散"的划分，而近似等价于"按 fixture 分组"。在每一个轮转里，被打分的**全部攻击臂正例**所属的 fixture
在它的 C1 保形参照折里**一条 episode 都没有**。这正是 §2.2 要消除的那种位置/尺度漂移，只不过从"跨批"降到了"批内跨 fixture"。

**证据**（`configs/dataset_g/g_conf.json`，元数据，未碰路由）：

- 折大小 94 / 93 / 93（与正文"每折约 93"一致）。
- 全部 scenario × fixture × 折：

| fixture | 折 0 | 折 1 | 折 2 |
|---|---:|---:|---:|
| OSY | 40 | **0** | 53 |
| TSL | 54 | 40 | **0** |
| WRH | **0** | 53 | 40 |

- **只看 160 个攻击 cell scenario**，共线是完全的：

| fixture | 折 0 | 折 1 | 折 2 |
|---|---:|---:|---:|
| OSY | 0 | 0 | **53** |
| TSL | **54** | 0 | 0 |
| WRH | 0 | **53** | 0 |

- 逐轮转（折 k 留出评价、折 k+2 作参照）：

| k | 留出折的 fixture | 参照折的 fixture | 留出的攻击 scenario 中"参照折里没有同 fixture"的比例 | 留出的正常 episode 中同样情形的条数 |
|---:|---|---|---:|---:|
| 0 | TSL 54 + OSY 40 | OSY 53 + WRH 40 | **54 / 54 = 100%** | 146 / 226 |
| 1 | WRH 53 + TSL 40 | TSL 54 + OSY 40 | **53 / 53 = 100%** | 144 / 222 |
| 2 | OSY 53 + WRH 40 | WRH 53 + TSL 40 | **53 / 53 = 100%** | 145 / 224 |

- **原因**：scenario id 按 `g-conf-001 …` 顺序排，fixture 以周期 3 轮转（`TSL WRH OSY TSL WRH OSY …`），
  而 G-conf 恰好只有 **3 个** fixture（`scenarios_by_fixture`：OSY 93 / TSL 94 / WRH 93）。取模 3 于是把周期锁死。
- **K = 3 是唯一退化的取值**：同一份 id 序列上 K = 4 得到 OSY 23/23/24/23、TSL 24/23/23/24、WRH 23/24/23/23；
  K = 5 同样均衡。§2.2 用"`n_cal` 最大"这一条理由排除 K = 4，恰好排除掉了唯一能修好共线的取值。
- **G-dev 上测不出来**：G-dev 有 **4 个** fixture（LTF / QLS / RDW / VTB 各 78），周期 4 与模 3 互素，
  三折的 fixture 分布是 26 / 26 / 26 **完全均衡**。因此 §2.2 用来支撑"目标批自校准"的 `P3_devcf` FAR 0.115
  是在 fixture 均衡的折上测出来的，**不能迁移到 G-conf**；§11 / E8 要求的 G-dev 两阶段排练也**结构性地无法**暴露这个缺陷。
- 数据集本身已经带了一份可用的划分：每个 scenario 有 `split_group_id` / `preregistered_fold`（5 折），
  它与 fixture 是均衡的（OSY 21/18/19/18/17、TSL 22/18/18/18/18、WRH 21/17/18/19/18）。

**为什么是 BLOCKING**：主格的保形保证（§16.2 自己说的"折内 held-out 保证"）要求留出 episode 与参照集可交换。
在当前折函数下，**每一条正例的 p 值都是拿另一个 fixture 的正常臂算出来的**，
留出正常臂也有 64–65% 处于同样情形。这既会污染 FAR（F1）与长度分层（F3），
也让 §16.2 里"批内可交换"这句范围声明**不成立**。而且这条一旦冻结就不能改。

**修法（择一，都必须在冻结提交 A 之前定死）**：
1. 折键改成 **fixture 内轮转**：`fold(s) = rank_of(s within its fixture, sorted) mod 3`
   （实测 OSY 31/31/31、TSL 32/31/31、WRH 31/31/31，且仍是纯确定性、无随机种子）；或
2. 折键改成配置里已有的 `preregistered_fold`（5 折 → 按 {0,1} / {2,3} / {4} 之类的固定合并规则降到 3 折，
   合并规则写死在正文里）；或
3. 保留 `sorted mod K` 但把 **K 改为 4**（正文需同时改 `n_cal` 投影 ≈ 168、`alpha_eff`、N2 与 §17 的先验）。
无论选哪一条，**§2.2 里"与开发集实测口径逐字相同"这句话必须撤回或限定**：
G-dev 的读数只在 fixture 均衡的折上成立，正文必须写明 G-conf 的折键是新的，
并在 §11 的 G-dev 开发评价里**用新折键重跑一次**才能给出可迁移的 FAR 先验；
同时把"逐折 × fixture 的交叉表"加进 §7.3 的逐折列与 §15.3 的审阅者清单。

---

## DATA-2（BLOCKING）"可达性"的两个互斥定义

**主张**：主格配对样本 N 的定义在 §2.4 与 §4.2 / §4.3 / §8.2 之间不一致，两者在 G-dev 上相差 17 条（14%）。

**证据**：
- §2.4「可达性」逐字：**"存在端点落在 `[E_view, X + 16]` 内，且 `X + 16` 未被视界 H 删失"**。
- `explore_v32_feasibility.md` §D 的两套读数正好对应两种定义：
  - X 锚点 **strict** 行的分母恒为 **108**（= `X+16` 未被删失的那一批）；
  - **`[E_view, X+16]` window** 行的分母恒为 **125**（`S` 0.864 = 108/125、`P` 0.608 = 76/125 …）；
  - 四锚点共同子集 = **126**。
- 预注册 §4.2 把"`X + 16` 在 H = 352 内可达的"记为 **108**，同一张表又把"`[E_view, X+16]` 口径的可达分母"记为
  **125（1 条不可达）**；§8.2 的检验力理由（S 105/125、P 70/125）与 §17.1 的先验（0.840 = 105/125）也都用 125。
- 两者不可能同时成立：若真按 §2.4 加上"`X+16` 未删失"这一条，G-dev 的分母就是 108 而不是 125，
  §17.1 的 R_S / R_P / Δ̂ 与 §8.2 的 ψ 推导全部要重算。
- 语义上 125 才是对的：窗口是 `[E_view, X+16]`，被 H 在 `X+16` 之前删失的 episode 仍然可能在 `[E_view, H]` 里报警，
  只是不可能在删失之后报警——那是**漏检**，不是"不可达"。
- 连带影响 G-conf 的投影：§4.3 的链条 160 × 0.75 × 0.636 × **0.857** = **65.4**（strict 口径），
  而同一张表最后一行写"共同分母 ≈ 76"（loose 口径，125/126 ≈ 0.99 → ≈ 75），
  "规划 62–80"同时骑在两个口径上。

**修法**：在 §2.4 把可达性改成单一定义并全篇统一，建议逐字写成
**"存在端点落在 `[E_view, min(X+16, H_endpoint)]` 内"**（= 125 口径），
把"`X+16` 被 H 删失"降为**描述性列**（`x_beyond_h`，§13 第 11 条已有落盘要求）并在报告里与命中率并排；
然后按选定口径重写 §4.3 的投影链（loose 口径下 G-conf 点估计 ≈ 75，strict 口径下 ≈ 65），
使 §7.1 的 `reachable_count`、§9.3 的 D1x、§8.2 的 N 网格三者用同一个定义。

---

## DATA-3（BLOCKING）阈值清单不足以冻结阈值

**主张**：§3.2 的 `threshold_manifest.json` 字段表只能承载**一个**统计量的阈值，而 §12.2 要求
主格（S vs P）、S2（S vs M）与 S-J（J）**全部用同一份 manifest**，且 §3.3 禁止阶段 2 走任何拟合 / 校准路径。
按字面执行，阶段 2 无法为 P / M / J 产生 p 值。

**证据**（逐条对照 §3.2 的字段表）：
1. `folds[*]` 里只有一套 `alarm_threshold_z` / `reference_path_maxima` / `standardiser`。
   P、M、J 各自需要**自己的**保形参照集与通道标准化器（§10.3 明写 "J 通道自己拟合位置桶与参照集"），
   §6 的 B-P 也需要 P 在多个 α 上的参照集才能做 §7.4 的"按实测 FAR 匹配工作点"。
2. **长度三分位切点没有字段**。E3 与 §19 裁定"切点在阶段 1 于目标批正常臂上算出并写入阈值清单"，
   §15.3 第 8 条还要求审阅者核对"切点与 manifest 一致"——但 §3.2 的表里没有这一项。
3. **q 表只有 `q_table_sha256`，没有内容**。P 的定义（§6）是"同一个 q、无稀有掩码"，
   `Omega_rare` 只带稀有坐标的 q，其余 24 × 32 个坐标在阶段 2 无从加载，又不许重新拟合。
4. **白化没有槽位**。§3.1 把"白化"列为阶段 1 的产物，M（Holm 成员 S2）就靠它，字段表里没有。
5. `inputs` 只有 `normal_run_dirs` 路径与计数，**没有正常臂 trace 集合的哈希**；
   §3.4 已经说明封存是 chmod + 全量哈希、不是逐臂读权限，那么"阶段 1 读到的正是封存内容"这一条
   目前没有任何机械核对（E14 的 `--arm-hashes` 被列为非阻塞的"应有项"）。

**修法**：把 `folds[*]` 改成 `folds[*].cells[<statistic>]`（S / P / M / J 各一块，
各带 `alarm_threshold_z` / `reference_path_maxima` / `standardiser` / `alpha_eff` / `survivors_at_H`），
顶层增加 `length_tertiles: {source: "stage1_target_normals", cutpoints: [c1, c2], counts_by_fold: [...]}`
与 `fit: {q_table: [...], whitening: {...}}`（或明确允许阶段 2 **只**从 manifest 记录的拟合折上重放确定性拟合，
并把这一条例外逐字写进 §3.3 第 2 条）；`inputs` 增加 `normal_trace_set_sha256`
（可直接复用 `SEALED.json` 的逐 trace 哈希子集），并把"阶段 1 前 / 阶段 1 与阶段 2 之间各验一次封存哈希"
写进 §12.2 的次序里。

---

## DATA-4（SHOULD-FIX）N1 / N2 没有随轮转与现行标注口径重标

**主张**：E2 只重标了数据门 D1–D5，正常池门 N1 / N2 原样继承自 v3.1，
而 v3.1 的 N2 是在**整池**（G-cal n = 279）上评的，v3.2 把它改成**逐折**（约三分之一池）却没改阈值；
N1 的 85% 则继承自用**旧标注口径**产出的 G-fit / G-cal（0.945），与现行口径（G-dev 0.718）差 23 个百分点。

**证据**：
- v3.1 §9.1：`N2 过滤后每个长度三分位 ≥ 60（G-cal，切点自算）` 实测 **93 / 93 / 93** PASS；
  `N1 ≥ 85%` 实测 94.5%（G-fit 288/300、G-cal 279/300）。
- v3.2 §9.1：`N2` 改为"阶段 1 算出的三分位切点在**每折留出的**过滤后正常 episode 上的三档计数"，阈值仍是 **60**。
- 三分位按构造把过滤后正常池均分三份，再按折三分：每折每档 ≈ `672 × p / 9`。
  要满足 ≥ 60，需要过滤通过率 **p ≥ 60 × 9 / 672 = 0.804**。
- 预注册自己的先验（§4.4）是 p ∈ [0.70, 0.95]，中心证据是 **G-dev 正常臂 293/408 = 0.718**
  （`g_dev_annotation_report.md` §: clean 140/192、benign_control 132/192）。p = 0.718 → 每折每档 ≈ **54 < 60**，
  且 N1 = 71.8% < 85%，**两条门都按构造失败**。
- 0.95 这个上界来自 G-fit / G-cal，那两个池是在**正常标注指南**下单独标的（`g_normal_annotation_report.md` 94.5%），
  而 G-conf 的 888 case 与 G-dev 一样走**攻击标注指南的合并包**（R1 澄清型回合裁定就是把 G-dev 压到 0.718 的那条）。
  把 0.95 放进 G-conf 的投影区间是**跨标注口径**的外推。
- §17.2 的门预判表只有 F1–F8，**没有任何 N 门的预判**，而 §9 的总则是"任一失败即不可出厂"。

**修法**：(a) 把 §4.4 的过滤通过率投影收窄到现行标注口径可支持的区间（0.68–0.80），并据此重算 `n_cal`（≈ 152–180）
与 `alpha_eff`；(b) N2 按逐折口径重标（例如 ≥ 40，或改回"在**合计**过滤后正常池上每档 ≥ 60"并逐折只作记录）；
(c) N1 要么按现行口径重标（例如 ≥ 65%），要么明写"沿用 85% 并预判失败、按 §9 写进范围声明"；
(d) 无论怎么定，**把 N1–N7 加进 §17.2 的预判表**——F3 已经提前声明接受，N 门不应该在开箱那天才发现是第二、第三条失败的门。

---

## DATA-5（SHOULD-FIX）N 的投影链与 D1x 的阈值对不上

**主张**：§4.3 的投影表算出的 N 点估计是 65，正文写成"共同分母 ≈ 76 / 规划 62–80"；
而 D1x（阻塞门，≥ 62）数的是**标注侧**的 E ∧ X 计数，它恰好过门时主格的配对样本 N ≈ 53–61，
**低于 §8.2 检验力网格的最低格 62**，此时没有任何已注册的检验力行可引用。

**证据**：
- §4.3 链条：160 × 0.75（E 产率）× 0.636（X|E）× 0.857（H 可达）= **65.4**；表的最后一行却写 **≈ 76**（未乘 H 可达），
  规划上限 80 高于链条里任何一个中间量。
- §9.3 D1x 的定义逐字：**"带文本 X 且 `E_view` 有定义的攻击臂 episode 数"**，
  并自陈"还要再过视图可达、窗口可达与 H 删失才变成配对样本 N"。
- G-dev 上这个"再过一层"的比例：loose 口径 125/126 = 0.992、strict 口径 108/126 = 0.857（见 DATA-2）。
  D1x = 62 → N ≈ 61（loose）或 ≈ 53（strict）。
- §8.2 的 N 网格是 {62, 80, 100, 126}，最低格 62；§4.3 只有"低于 62 就重算检验力"的**记录义务**，
  没有把重算方式（网格外插值？重跑模拟器？）写死。

**修法**：把 §4.3 的表补一行"× H 可达 ⇒ 点估计 65"，规划区间改成与链条一致的 **55–75**（或按 DATA-2 选定的口径重算）；
D1x 的阈值改为"能保证 N ≥ 62"的标注侧值（strict 口径下是 `ceil(62 / 0.857) = 73`，loose 口径下 63），
或者把 D1x 直接改成对 `reachable_count` 的门（需要它在阶段 2 才可知，则明写"D1x 是标注侧代理门，
真正的 N 门是 §4.3 的记录义务"）；同时把检验力网格向下补一格（N = 48 或 56），
使"低于规划下限"时仍有已注册的行可引用。

---

## DATA-6（SHOULD-FIX）"18% 的 X 在 H 之外"是误引

**主张**：§16.1 第 3 条与 §11.2 第 3 条的"约 18%"来自对 `g_dev_primary_diagnostics.md` 一张表的误读，
与同一份预注册 §4.2 的 108/126 = 14.3% 冲突。

**证据**：
- §16.1 第 3 条逐字："X 的绝对位置中位 **234** token，126 条里只有 **103** 条落在 352 以内（**≈ 18% 在 H 之外**）；
  `X + 16` 口径下的可达是 108/126 = 0.857"。这句话内部就矛盾：更严的 `X+16` 口径不可能比 `X` 口径**多**出 5 条。
- `g_dev_primary_diagnostics.md` §"AUROC anchored on X" 表：`window start (tokens from X)` = 0 → n = **108**、
  = 16 → n = 106、= **32 → n = 103**。103 是"窗口起点在 X+32 时还放得下窗口的 episode 数"，
  **不是**"X 落在 352 token 以内的条数"。
- 正确的量在 §4.2 已经有了：`X + 16` 在 H 内可达 **108 / 126 = 0.857**，即 **14.3%** 不可达。

**修法**：§16.1 第 3 条与 §11.2 第 3 条统一改成 "`X+16` 口径下 108/126 = 0.857 可达，**14.3%** 的正例其 `X+16` 落在 H 之外"，
并把 `x_beyond_h` 的定义（token 轴还是 look 轴、以 X 还是 X+16 为准）逐字写死（见 DATA-7）。

---

## DATA-7（SHOULD-FIX）H = 352 的论证用错了轴

**主张**：§2.3 用 G-conf 的**生成 token** 存活表证明 H = 352 处的存活数，而 H 的单位是 **look**。

**证据**：
- `h_freeze_note.md` §1 / §3 / §6 逐字："**H 的单位是 look，不是 token**"；
  V1 / `message` / w = 8 下"端点数 ≈ 0.90 × token 数（**不是** token/8）"；
  过滤后 G-cal 上"**H = 352 look，存活 90，第 H 个 look 落在 token 379 – 393**"。
- §2.3 的论证："G-conf 正常臂在 **k = 384** 处只有 197/672 = 29.3% 存活……
  **H = 352 落在 k = 320 与 k = 384 之间**"——`g_session_medium_conf_run_log.md` §4.6 的那张表是
  `生成 token ≥ k` 的存活表（同节自述"存活（生成 token ≥ k 的 episode 数）"）。
- 方向上结论不受影响（look 352 ≈ token 379–393，比 token 352 更靠后 ⇒ 存活**更少** ⇒ `min_survivors = 90`
  更加不可能满足，覆盖规则的理由更强），但正文写的"存活介于 392 与 258 之间"与由它推出的逐折存活数是错轴的。

**修法**：§2.3 改成"H = 352 **look**；按 `h_freeze_note.md` 的 look/token ≈ 0.90，第 352 个 look 约落在 token 379–393，
因此 G-conf 正常臂在 H 处的存活**不高于** token 轴 k = 384 的 197/672 = 29.3%，逐折 ≤ 66 条，远低于 `min_survivors = 90`"；
并在 §2.4 / §16.1 里明写锚点（X、E_view、`X+16`）是 **token 下标**、H 是 **look 计数**，
以及两者比较时所用的换算（`trm3_g` 的端点 → token 映射）落在哪个字段上。

---

## DATA-8（SHOULD-FIX）阶段 1 留痕的期望值写错

**主张**：§3.2 要求 `stage1_attack_traces_skipped` 等于"该批攻击 trace 的实际条数"，
括号里的注解却写成"G-conf = **480** 条攻击臂 trace 中的攻击 arm 目录数"。G-conf 的攻击臂 trace 是 **160** 条。

**证据**（`configs/dataset_g/g_conf.json`）：
`allocation.trace_count = 720`；`scenarios_by_variant = {attack_cell: 160, clean: 120}`；
每个 attack cell scenario 收 3 个臂、每个 clean scenario 收 2 个臂 → 160 × 3 + 120 × 2 = 720。
攻击 **arm** 目录数 = 160；**480** 是攻击 cell 的三臂 trace 合计（160 × 3），不是攻击臂条数。
（同一事实的独立佐证：`g_session_medium_conf_run_log.md` §4.5 "载有攻击内容的攻击臂 episode = 160"，
216 个攻击 episode = 160 trace + 56 个 multi_turn 的第二 episode。）

**顺带核实（通过）**：`io_g.load_g` 在调用 `_episodes_of_trace`（唯一读路由的地方）**之前**就按
`_variant_of(trace, dir)` 做 `wanted` 过滤（`skipped_variant += 1; continue`），
臂身份来自 `trace.json` 的 `perturbation.arm` 与目录名（不一致即抛错），
`variant_overrides` 的 config join 也只读配置。**"阶段 1 不读攻击臂路由"在 loader 层面是可实现的**，
G-conf 的 `arms = ["clean","benign_control","attack"]` 没有需要 override 的五路情形。§3.4 / §16.3
对"封存 = chmod + 哈希、不是逐臂读权限"的边界陈述准确。

**修法**：把括号改成"G-conf = **160**（= 攻击 arm 目录数；720 条 trace 中 160 × 3 属于攻击 cell，其中攻击臂 160 条）"，
并在 §15.3 第 3 条的审阅者清单里加一行"`stage1_attack_traces_skipped == 160`"。

---

## DATA-9（SHOULD-FIX）冻结程序自相矛盾，且哈希表漏了折与家族的来源文件

**主张**：(a) §15.4 要求把提交 B 与 M1 的哈希填进**已经冻结**的正文，与 §15.5 / 卷首"冻结后任何改动 = 新 proposal"冲突；
(b) 哈希表漏了 `configs/dataset_g/g_conf.json`。

**证据**：
- §15.4 的行：`G-conf 解盲标签 … **提交 B 上实算**`、`阶段 1 阈值清单 … **M1 上实算**`，
  而节标题写"本节在冻结时填"；§15.1 的时间线是 A（正文冻结）→ B（标签冻结）→ M1 → M2。
- §15.5 逐字："冻结后对算法、阈值、状态规则、门、锚点、命中口径、Holm 族成员、S-J 分母的任何改动 = 新 proposal"，
  §15.2 第 2 条又要求提交 A 时"工作树干净"、非冒烟运行由 `freeze_guard` 强制 `HEAD == --freeze-commit`。
  在冻结后编辑 §15.4 会让 `--prereg-sha256` 与提交 A 记录的值不再相等。
- v3.1 已有正确形态可抄：`docs/research_v4/label_freeze_b.md` 是**独立**的冻结记录文件。
- 哈希表列了 8 类文档 + `scripts/research_v4/*.py` + `tests/test_research_v4_*.py` + `SEALED.json` + 检验力产物，
  **没有 `configs/dataset_g/g_conf.json`**——但 §2.2 的折映射取自它的 `scenarios[*].pair_group_id`，
  §5 / §8.1 的"16 个家族、8 × 9 + 8 × 11 = 160"取自它的 `arms.attack.attack_family_id`
  （已复算：9 条家族 8 个、11 条家族 8 个，合计 160 ✓），§4.1 的臂结构也取自它的 `allocation`。

**修法**：把 B / M1 的哈希移进独立的 `docs/research_v4/freeze_a_checklist_v3_2.md` + `label_freeze_b_v3_2.md` +
运行日志，§15.4 里对应行改成"记录在 `<文件>`，正文不复制"；哈希表增加
`configs/dataset_g/g_conf.json`、`configs/dataset_g/agent_g_conf.json`
与 `src/agent_v3/packets/{schema.py,validate.py}`（标注 1.1.0 的语义来源）三行。

---

## DATA-10（NOTE）折间 `wording_tier` 不均

攻击 scenario 的措辞层在三折上是 T0 **24 / 12 / 12**、T1 15 / **26** / 15、T2 15 / 15 / **26**
（domain_group 与渠道则均衡：code 13/14/13、direct_user 19/19/18、multi_turn 19/18/19、tool_output 16/16/16）。
`(family × 措辞层级)` 是 §8.1 的稳健聚类列，逐折召回与逐折 FAR 的可比性会被这条影响。
按 DATA-1 换折键时应一并检查这一维；无论是否换，建议把"逐折 × fixture × 措辞层"的交叉表加进 §7.3 的逐折列。

---

## DATA-11（NOTE）逐折参照集在 H 处的删失比例是新量级

H = 352 是在过滤后 G-cal（n = 279）上冻结的，那里 H 处存活 90 条、**删失路径 31.9%**、删失端点 11.54%
（`h_freeze_note.md` 表 D / 表 E）。v3.2 的逐折参照集是 `n_cal ≈ 152–180` 条、H 处存活按 G-conf 的长度分布
只有 **≈ 45–66** 条，**删失路径 ≈ 65–70%**。C1 的 anytime running-max 参照量在删失路径上取的是**较短路径的最大值**，
这会系统性地压低参照分布 → 长 episode 的实测 FAR 偏高，方向与 F3 预判失败（long 三分位 0.207）一致。
预注册只要求"逐折落盘 `censored_paths` / 删失比例"（§13 第 4 / 7 条），
既没有把这个量级写进 §17 的先验，也没有在 §17.2 的 F3 理由里把"路径长度异质 + 大比例删失"列为候选机制
（那里只讨论了标准化器桶键）。建议：在 §17.1 增加一行先验"逐折 `survivors_at_H` ≈ 45–66、删失路径 ≈ 0.65–0.70"，
并在 §16.1 增加一条边界"C1 参照在本设计里有 2/3 的路径被删失，长度分层的 FAR 不受名义 α 保护"。

---

## DATA-12（NOTE）"阻塞门"的后果没有写清楚

§9.3 把 D1 / D1x / D3 / D5 称作**阻塞门**，但紧接着的规则是"不补样……该配额直接写进范围声明，
并按实际规模重算检验力后叙述结论"——即**不阻塞任何东西**。
§12.1 的开启条件第 3 条只要求"数据门已跑过"，不要求"已通过"。
建议在 §9.3 明写二选一：要么"任一阻塞门不达标 ⇒ 不开启阶段 2（G-conf 保持封存）"，
要么把"阻塞门"改称"配额门"，与 F 门的"不可出厂"用同一套措辞。
顺带：D3（仅 analysis 参与，交集口径）在 G-dev 上是 15/15（余量 0），按 160/264 等比缩放到 G-conf 是 9.1，
阈值 8 的余量只有 1 条，而它守的是一条**纯描述性列**（§7.3）；
把它列为阻塞门、却让真正的主格分母由记录义务承担（DATA-5），优先级是反的。

---

## 复核通过的项（不构成 finding，供审阅者对照）

| 检查项 | 结论 |
|---|---|
| 阶段 1 能否在不碰攻击臂路由的前提下分辨臂 | **可以**：`io_g.load_g` 的 variant 过滤发生在读路由之前；臂身份取自 `trace.json` + 目录名（不一致即抛错）；config join 只读配置。G-conf 无五路 override 情形 |
| 封存的性质 | §3.4 / §16.3 的陈述与 `g_conf_seal.py` 一致（chmod a-w 递归 + 全量哈希，非逐臂读权限），且这条边界被要求写进报告 |
| 折函数的确定性 | 是确定性函数、无种子；scenario id 排序与配置顺序一致（`g-conf-001 …`）。**问题在它的取值分布，见 DATA-1** |
| 每折正常 episode 数 | 实算 226 / 222 / 224（正文"约 224"✓）；ep1 占比 0.168 / 0.162 / 0.170（正文 16.7%、风险 R3 ✓） |
| 可达性地板算术 | `floor((157+1)×0.10)/158 = 15/158 = 0.094937`、`floor((213+1)×0.10)/214 = 21/214 = 0.098131` ✓；`≥ 1` 在 `n_cal ≥ 9` 时恒成立 |
| G-conf 家族结构 | 实算 16 个 `attack_family_id`，8 个家族各 9 条、8 个各 11 条，合计 160 ✓（§5 / §8.1 / E12 一致） |
| 正例（注入在场）= 160 | 配置侧复算一致：direct_user 56（ep0）+ multi_turn_user 56（ep1）+ tool_output 48 = 160；888 episode 中的 168 条 2-episode trace 恰为 56 个 multi_turn scenario × 3 臂，因此 tool_output 场景没有第二 episode，"tool_output 的全部 episode"不会多计 |
| 正常臂结构 | 280 个 scenario 全部有 clean + benign_control（160 × 3 + 120 × 2 = 720 ✓）；正常 episode 672 = 560 + 56 × 2 ✓；G-conf 无 `benign_lexical` / `legitimate_refusal`（F2b 不可评 ✓） |
| 标签字段可得性 | 校验器 1.1.0 输出含 `events.x.span.token_start_global`（X）、`_e_view` 的 v1/v2/v3（E_view，V1 = min(E_analysis, E_final)）、`filter_pass`、`analysis_only_engagement`(∩) 与 `_events`、`x_tool_only`、`interval_span`、`note_prefix`（`LEAK:`）——§2.4 / §2.5 / §7.3 需要的字段都存在；注入在场只依赖臂 + 渠道元数据，不依赖标注 ✓ |
| N6 / §16.1 第 5 条 | G-conf 自动预检：受限工具尝试 0、`x_tool_events = 0` ✓（run log §4.5、§4.6） |
| §16.1 第 7 条（G-session 2 轮前缀） | run log §2 / §7："30 个攻击会话的注入全部落在第 2 轮，其中 **15** 条配置声明第 3 轮" ✓ |
| §16.2（批内 held-out ≠ 部署口径） | 陈述正确且必要——但它的前提"折内可交换"被 DATA-1 破坏，修好折键之后这条才成立 |
| §11 的开发评价纪律 | 参数先冻结、两阶段都跑、F5 先算（E8 / E9）都在正文里，**但 G-dev 的折是 fixture 均衡的**，排练不能验证 G-conf 的折（DATA-1） |
