# 冻结审阅 v3.3 · 代码视角（提交 A″ 之前，2026-09-08）

**审阅镜头**：**代码是否做了草案文本说的事**。凡预注册 §2 / §3 / §7 / §12 / §19 点名的每一个 flag、字段、键，
都去开代码、跑 `--help`、开 `tests/test_research_v4_v3_3.py`，并把推荐主格的两阶段命令**重跑进一个新的运行目录**。
A″ 之前不改代码，因此**凡不一致，默认由改预注册措辞解决**；只有第 C-1 条我认为需要组长在"改文本"与"改代码"之间裁定。
本文件只报**实际读过代码或实际跑过命令**验证到的结论。

**核验基准**：`HEAD = 279f115ccfc91c14223a690681c5a1a0a7c60ad0`；
工作树**不干净**（16 条：`src/agent_v3/factory/*` 与 `configs/dataset_g/*` 的 G-conf-2 在制品 + `?? artifacts`）。
**审阅对象**：`detector_prereg_v3_3_draft.md`（sha256 `29756b54f53f9be7b5c4cd45dfde7ca62fe5cfeb864792033053f3ff7ee5a173`，含 §20 裁定；
**A″ 一致性轮补记（2026-09-08）**：该草稿在冻结提交 A″ 上改名为 `docs/research_v4/detector_prereg_v3_3.md`，草稿名自 A″ 起作废，本文件记录的是审阅当时的草稿）；
代码 `src/research_v2/trm3_g.py`、`scripts/research_v4/{run_detectors_g,g_dev_data_gates,g_conf_seal,prereg_power_sim,v33_factorial}.py`；
测试 `tests/test_research_v4_v3_3.py`。

**数据纪律**：本轮**没有读取** `artifacts/agent_v2/dataset_g/g_conf` 与 `g_conf2` 的任何路由 / 标注 / trace，
也**没有读取** `v3_2_conf/` 的任何结果。G-conf-2 侧只读了 `configs/dataset_g/g_conf2.json`（元数据）。
重跑全部在 **G-dev**（已解封的开发数据）上，产物写进 scratchpad，**没有改动仓库内任何 artifacts**。

---

## 0. 三句话

1. **判定层复现**。推荐主格的阶段 1 + 阶段 2 从零重跑进新目录：阶段 1 的 `threshold_manifest.json`
   **除 `created_at` / 自哈希 / `code_commit` 外逐字段相同**；阶段 2 的
   **命中 114/126 = 0.9048、`far.filtered` 24/293 = 0.081911、`far.all` 0.075980、Δ̂ 0.3253968、
   家族聚类 CI [0.073333, 0.573529]、McNemar 2.4647306418e-10、b/c 44/3、匹配 α_P 0.0842105、
   R_P 0.579365、`x_beyond_h` 0、逐折 `far.filtered` .08421/.09574/.06731、`alpha_eff` .095238/.093750/.094737、
   `alpha_eff_w` 0.0945948、静默 5/40、场景级 0.130952、S1 [0.8015, 0.9797]、19 项清单守卫全 PASS** —— **全部逐位相同**。
   冻结的 **v3.2 注册命令**（`v3_2_a2_verify` 两阶段）在 HEAD 上重放：`result.json` 取值不同的字段 **0**、
   新增 **9** 键（8 个 v3.3 开关 + `calibration_design.v3_3`）、丢失 **0**，`threshold_manifest.json`
   除 `created_at` / 自哈希 / `code_commit` 外**零字段差异**，103/125、0.09804、0.11945、Δ̂ 0.264、
   CI [0.023529, 0.508333]、McNemar 2.4995097e-07、静默 5/40、`x_beyond_h` 18 逐位复现。
   §15.4 的 7 个 sha256、§8.2 / §8.3 的两张检验力表（60 格）**逐格逐位复现**；
   §2.2 / §4.1 / §4.4 / §8.1 的全部 G-conf-2 元数据实算（折 × fixture 32/31/31·31/31/31·31/31/31、
   逐折正常 episode 218/238/216、`n_kb` 53/132/95、`n_kb × fold` 正常 episode 44/42/38·100/112/100·74/84/78、
   16 家族 8×9+8×11=160、渠道 56/56/48、attack 216、`scenario_role` 144/16/120）**逐位复现**。
   `pytest tests/test_research_v4_v3_3.py` **49 passed**（49 = 9+7+7+7+13+6，与 §13 的六个类逐条对上）。
2. **有 1 条我认为需要组长裁定的实质缺口**：**`--debounce` 不在阈值清单里、不进 `cell_matches`、不进 19 项守卫**，
   所以"阶段 2 从 manifest 回放全部口径常量、CLI 上给错的值被忽略"（§3.3 / §12.2）对它**不成立**。
   我用**同一份**清单实测：`--debounce 2` 在 19/19 守卫 `ok: true` 的情况下把命中 114 → **108**、
   `far.filtered` 0.08191 → **0.04778**、`far.clean.all` 0.08333 → **0.04167**、静默 5 → **4**，
   即**硬门 F4 从余量 +0.0083 直接翻成 −0.0083 FAIL**，而清单一个字都没变。
3. **另有 6 条应当在 A″ 之前改掉的文本**（§12.2 在 `VAL1` 运行上注册的 `--expect-n-reference-folds` 是**空转**；
   §9 的"runner 不计算门"与代码写的 `gates` 块矛盾；§9.2 F1 汇总列的加权方式与代码相反；
   §10.3 `VAL1` 判据 (b) 没写分母，换分母会翻判；§9.2 F8 的"每格 15 行"在 `VAL1` 运行上是 45 行；
   §18 事实 1 / §19.1 说的"帮助文本没列 Z1"在 HEAD 上已经不成立）与 5 条 NOTE。

---

## 1. 复现记录（命令与读数）

| # | 做了什么 | 结果 |
|---:|---|---|
| R-1 | 推荐主格阶段 1（`--stage calibrate --normal-only-smoke --statistic S,Z1,P,M,J --force-h inf --debounce 1`）重跑进 `…/scratchpad/rerun/stage1_rr` | manifest 与 `v3_3_dev/stage1_hinf_nostrat_d1/threshold_manifest.json` 深度 diff：**唯一差异是 `code_commit`**（`1bb0ebd` → `279f115`），`created_at` / 自哈希按设计不同。**§20 (a) 的帮助文本修改确实是纯表面的** |
| R-2 | 推荐主格阶段 2（`--stage score --dev-smoke --compare-statistic P --recall-horizons 8,16,32,64,full --compare-horizons 32,64 --far-episode-census`）重跑进 `…/rerun/stage2_rr`，用 R-1 的新清单 | 见 §0 第 1 条：**16 个判定 / 门数值逐位相同**（含逐折 far、三分位、S1、家族 bootstrap 与 McNemar） |
| R-3 | 冻结的 v3.2 注册命令两阶段重放（按 `v3_2_a2_verify/*/result.json` 的 `args` 逐字） | 取值不同字段 **0**；新增 **9** 键；丢失 **0**；manifest **零字段差异**；核心读数逐位复现 |
| R-4 | v3.1 §19.7 命令（`runs_v3_1/primary_S_vs_P` 的 `args` 逐字，`--outputs primary`） | **非冒烟运行在当前脏工作树上被 `freeze_guard` 拒绝**（这正是 §15.2 第 2 条要求的行为）。改用 `--dev-smoke` 重放后：**丢失键 0**；所有 v3.1 的 far / positives / comparison / `horizon_H=352` / `n_reference=279` 断言**逐位相同**；唯一取值变化的 metric 是 `classes.silent_attack.*`（分母 128 → **40**，S 的分子 19 → 8）——**这是 v3.2 裁定改的 F4 分母，不是 v3.3 的漂移**。§13 的"v3.1 §19.7 逐字段不变（新增 8 键 / 取值不同 0）"是**改动前 vs 改动后**的口径，本轮无法在不做 git 操作的前提下物化"改动前"的源码树，故该条**未被独立复核** |
| R-5 | `--debounce 2` 打进 R-1 的**同一份**清单（`--stage score`） | 19/19 守卫 `ok: true`，清单自哈希 `eb536ac9…` 与 R-1 相同；命中 **108**、`far.filtered` **0.047782**、`clean.all` **0.041667**、静默 **4/40**，与因子轮的 `stage2_hinf_nostrat_d2_z1` 逐位相同 ⇒ **见 C-1** |
| R-6 | §8.2 / §8.3 的 12 × 4 与 12 × 4 两张表对 `prereg_power_v3_3/main/power_sim.json` 逐格比对 | **60 格全部逐位相同**（含 `power_mcnemar_only` 0.084 那一格与合取 0.013–0.031 的区间） |
| R-7 | §15.4 的 7 行 `sha256sum` | **7/7 相同**（`g_conf2.json` `0614908c…`、`manifest.json` `0877f9eb…`、两个 `power_sim.*`、三个 fixture / agent 配置） |
| R-8 | `configs/dataset_g/g_conf2.json` 元数据实算（折键、`n_kb`、家族、渠道、arm 集合） | §2.2 / §4.1 / §4.3 / §4.4 / §8.1 的每一个数**逐位复现**；`stage1_attack_traces_skipped` 的 G-conf-2 期望值 **160** = attack trace 数（G-dev 侧实测 264 = attack trace 数，口径一致） |
| R-9 | `run_detectors_g.py --help` / `g_dev_data_gates.py --help` / `g_conf_seal.py --help` 逐条对 §19.1 / §19.2 / §19.4 | §19.1 的 10 个开关、默认值、帮助原文**全部对上**（唯一例外见 T-6）；§19.2 的 40+ 个开关全部存在；`g_dev_data_gates.py` 的 `--h` 是 int 且默认 352、`THRESHOLDS["D1x_reachable_x_positives"] = 62` |

---

## 2. 需要裁定的一条（C-1）

### C-1（BLOCKING） `--debounce` 不被阈值清单钉住，"阶段 2 只回放清单"对它不成立

**文本**：§3.3 「真正的保护是"阶段 2 把全部口径常量从 manifest 回放，CLI 上给错的值被**忽略**"」；
§12.2 「两个阶段 1 与三个阶段 2 都**从 manifest 回放全部口径常量**，CLI 上的口径开关……**不参与任何计算**」；
§5 把 **去抖 K = 1** 列为**冻结参数**。

**代码**：
- `threshold_manifest.json` 的 `cell` 块有 `force_h` / `h_min_survivors` / `rare_threshold` / `layers` / … ，
  **没有 `debounce`，也没有 `stratify_reference`**（实测键集：`alpha, alpha_extra, bucket_size, cal_filtered_only,
  fold_key, folds, force_h, h_min_survivors, layers, min_bucket_traces, min_channel_traces, min_channel_windows,
  or_arm, rare_threshold, standardise, statistics, tag_scope, view`）。
- `verify_manifest` 的 19 项里 `cell_matches` 只比 5 个键（`view/tag_scope/alpha/folds/fold_key`），
  没有任何一项看 `debounce`。
- `run_detectors_g.py:904` 在**打分时**从 CLI 读 `runs = int(getattr(args, "debounce", 1) or 1)`，
  `runs > 1` 时调用 `trm3_g.apply_debounce(...)` 改写 `p_fused` / `state`，
  `DecisionStream` 在改写**之后**构建 ⇒ 全部下游率（FAR、匹配 α 扫描、家族 bootstrap、McNemar）随之改变。
  对比：`--top-m` 与 `--force-h` 在 restore 分支确实被清单覆盖（`load_state` 带 `top_m`；
  `calibration_from_state` 带 horizon），所以**只有 `--debounce` 破例**。

**实测（R-5）**：同一份清单、19/19 守卫 `ok: true`、`threshold_manifest.sha256` 相同，仅把 `--debounce 1` 换成 `2`：

| 量 | `--debounce 1`（注册值） | `--debounce 2` |
|---|---|---|
| 命中 @+16 | **114 / 126** | **108 / 126** |
| `far.filtered` | 0.081911 | **0.047782** |
| `far.clean.all`（F4 阈值输入） | 0.083333 | **0.041667** |
| 静默攻击报警 | 5/40 = 0.125 | 4/40 = 0.100 |
| **F4** | PASS，余量 **+0.00833** | 阈值 0.091667 ⇒ **FAIL，余量 −0.00833** |

即：**一个不被任何守卫覆盖的 CLI 开关可以把硬门 F4 翻掉，而两阶段解封的全部机械留痕都显示"一切正常"。**

**连带**：§15.3 第 15 条的审阅者反向测试写的是「`args.debounce == 1`，**且该运行的 metrics 与不带该开关的运行逐字节相同**」——
在 G-conf-2 上**不存在**"不带该开关的运行"，而 §12.2 / §20 D-7 明确规定「任何第 4 次阶段 2 = 新 proposal」。
**这条审阅项按字面在封存批上不可执行。**

**建议（二选一，需组长裁定）**：
- **(a) 只改文本**：§3.3 / §12.2 的"全部口径常量"改成"除 `--debounce` 与 `--stratify-reference` 之外的全部口径常量"，
  把 `debounce` 写进 §13.1 的"已登记未落地的代码缺口"（与"`cell_matches` 只比 5 个键"并列），
  并把 §15.3 第 15 条改成**只查 `result.json.args.debounce == 1` 与 `calibration_design.v3_3.debounce == 1`**
  （这两个字段都落盘，实测存在），删掉"与不带该开关的运行逐字节相同"这半句。
- **(b) 改代码**（A″ 之前的代码改动需要单独裁定）：把 `debounce` 与 `stratify_reference` 写进 manifest 的
  `cell` 块并加进 `cell_matches` 的比较键集合。**这会改变 manifest 的自哈希**，因此 §11 因子轮的 8 份阶段 1 产物
  与 §17.3a 的补测**不再可复算**，回归成本明显高于 (a)。

---

## 3. 应当在 A″ 之前改掉的文本（T-1 … T-6）

### T-1（SHOULD-FIX） §12.2 在 `VAL1` 运行上注册的 `--expect-n-reference-folds` 是空转

§12.2 「**`--expect-n-reference-folds` 必须传**（不传时 `n_reference` 断言的 `expected = None` 恒真、空转）」，
并在运行 2c 上写 `--expect-n-reference-folds <m0,m1,m2>`。

代码 `run_detectors_g.py:2778`：`fold_args.expect_n_reference = expected[fold] **if label is None else None**`。
`stratum_groups` 在 `--stratify-reference n_kb` 下**永远不会**返回 `label is None` 的组，
所以运行 2c 的每一条 `n_reference` 断言的 `expected` 都是 `None`、`ok` 恒真。
实测 `v3_3_dev/stage2_hinf_nkb_d1_z1` 的 45 行断言里三层的 `n_reference.expected` 全是 `None`。

**改法**：§12.2 在运行 2c 上注明"该开关在分层运行上不生效（逐层断言无期望值），逐层 `n_reference` 只作记录"，
或直接从 2c 的注册命令里删掉它。**M1b 的 `<m0,m1,m2>` 仍应抄进运行日志作人工核对。**

### T-2（SHOULD-FIX） §9 说"runner 不计算也不断言 F1–F8"，但 runner 计算了 5 个门

§9 首段（逐字继承 v3.2 §9.2）：「runner 只强制 §13 的冻结断言，**不计算也不断言 F1–F8**」。

代码写了 `result.json.gates.<s>.{statistic, alpha_eff_weighted, gates[]}`，其中 `gates[]` 有 **5 行带 `status`**：
`F1_pooled_holdout_far_vs_alpha_eff` / `F3_worst_length_tertile_far` / `F5_matched_group_far` /
`N1_filter_pass_rate` / `N2_filtered_normals_per_fold_per_tertile`（实测阶段 1 与阶段 2 的 `result.json` 都有，
G-dev 上 F1 PASS dev 0.012684、F3 PASS medium 0.10084、F5 PASS 0.13095 对 0.264420556890764、N1 FAIL、N2 PASS）。
"不断言"是对的（不 `SystemExit`），"不计算"是错的。
**且 §19.3 的"产物键"表里根本没有 `gates`** ——A″ 的审阅者按 §19.3 逐条 grep 不会发现它，
报告方会手算一遍本来已经落盘的门值。

**改法**：§9 首段改成"runner **计算** F1 / F3 / F5 / N1 / N2 并落盘 `gates.<s>.gates[*]`（带 `status`），
但**不断言**任何门；其余门与本表的最终判定仍在报告侧"；§19.3 补一行 `gates.<s>.gates[*].{gate,value,threshold,status}`。

### T-3（SHOULD-FIX） §9.2 F1 汇总列的加权方式与代码相反（v3.2 的旧账，逐字继承）

§9.2 F1：「**另**报按**留出的过滤后正常 episode 数**加权的汇总列」；§11.2 引的 `alpha_eff_w` = **0.094595**。

代码 `_weighted_alpha_eff`（docstring 逐字写着 "gate F1 compares against the **n_cal-WEIGHTED** mean of alpha_eff"）
按 **`n_cal`**（= 折 `k+2` 参照折的规模）加权：G-dev 权重 (104, 95, 94) ⇒ **0.09459480**（= 文中的 0.094595）。
按文本说的"留出折的过滤后正常 episode 数"加权是 (95, 94, 104) ⇒ **0.09458277**。
**文本写的是一个口径，文本引的数字是另一个口径。**
G-dev 上差 1.2e-5（对 ±0.03 的带毫无影响），但 G-conf-2 的三折更不均（218/238/216），差值会略大，且这是**门的定义**。

**注**：`freeze_review_v3_2_statistics.md` §0 第 4 条曾把这条判为"**F1 的 `alpha_eff` 加权方式是对的**……
权重就是留出折的过滤后正常 episode 数"——那是对**数学**的核对，不是对**代码**的核对；
代码从 v3.2 起就是 `n_cal` 加权。**这是一条 v3.2 复发的缺陷。**

**改法**：§9.2 F1 与 §11.2 一律改成"按 **`n_cal`**（参照折规模）加权的 `fold_summary.alpha_eff_weighted`"，
并在 §13.1 记一条"文本口径与代码口径在 v3.2 已经不一致，v3.3 以代码为准"。

### T-4（SHOULD-FIX） §10.3 `VAL1` 判据 (b) 没写分母；换分母会翻判

§10.3 判据 **(a)** 明写 `far.filtered`；判据 **(b)**「**条件 FAR 的 max/min 比 ≤ 2.5**」**没有写分母**，
而它的标定来源（§10.3 收益行与 `v3_3_dev_measurements.md` §5.4 的 7.22× → 2.36×）用的是 **`all` 分母**
（81 + 204 + 123 = 408 = 未过滤正常并集）。

我用注册命令自己产出的 `far_episode_census` 连 `g_dev.json` 的 `n_kb` 图复算了同一批格：

| 格 | `all` 分母跨度 | `filtered` 分母跨度 |
|---|---|---|
| `Z1 · ∞ · d1` 无分层 | 7.235× | 5.894× |
| `Z1 · ∞ · d1` **n_kb 分层** | **2.370×** | **1.247×** |
| `S · 352 · d1` 无分层 | 3.176× | 2.859× |
| `S · 352 · d1` **n_kb 分层** | 2.025× | **2.566×（> 2.5 ⇒ 会判 FAIL）** |

即：**同一条 2.5× 阈值在两个分母上会给出不同判定**（`S·352` 那一格就跨过去了），
而 (a) 用 `filtered`、(b) 的标定用 `all`。

另有两条同址的可执行性问题：
- (a) 写的是"**该层该折**的可达 α"，(b) 用的却是**并折**的条件 FAR。两者的聚合层级不同，文本没有说明。
- **并折的逐层 FAR 不是落盘字段**：落盘的只有 `cells.<s>.folds[k].strata.per_stratum["n_kb=j"].far.{all,filtered}`（逐折），
  并折值必须由报告方相加，**无分层的那一侧（运行 2a）更是只能靠 `--far-episode-census` 的行 join `n_kb` 图重算**。
  §19.3 的"逐层读数……逐层落盘"因此是**半对的**。

**改法**：§10.3 (b) 写死分母（建议 `filtered`，与 (a) 一致）并把阈值按该分母重新声明；
(b) 写明是"并三折"的；§19.3 补一行说明并折逐层 FAR 由 `far_episode_census` + `stratify_config` 重算，
并把 `--far-episode-census` 在 §12.2 运行 2c 上的必要性从"O-1 敏感性"升级为"`VAL1` 判据 (b) 的唯一输入"。

### T-5（SHOULD-FIX） §9.2 F8 的"每格 15 行"在 `VAL1` 运行上是 45 行；§12.3 的门清单漏了 F2a / F8

§9.2 F8 (i)：「`assertions.by_statistic.<s>[]` **每格 15 行 = 5 项 × 3 折**」。
分层运行下 `frozen_assertions` 是**逐折逐层**调用的（`run_detectors_g.py:2779`，每行盖 `stratum` 戳），
实测 `stage2_hinf_nkb_d1_z1` 的 `by_statistic.Z1` 是 **45 行 = 5 项 × 3 折 × 3 层**。
运行 2c 照 §9.2 F8 的字面判定会判 FAIL。

同址：§12.3 的门重评清单是「F1 / F3 / F4 / F5 / F6 与 N1–N7」，**F2a 与 F8 不在里面**，
但 §9.2 给 F2a 写了预判 PASS、给 F8 写了 v3.3 专属的改动。

**改法**：F8 (i) 改成"每格 5 项 × 3 折 ×（分层数，主格 = 1）行，逐行 `ok`"；§12.3 的清单补上 F2a 与 F8。

### T-6（SHOULD-FIX） §18 事实 1 与 §19.1 说"`--statistic` 帮助文本没有列出 Z1"，HEAD 上已经不成立

§0 change log 第 15 条说「按 §20 (a) 给 `--statistic` 的帮助文本补列 `Z1`」**已执行**（commit `279f115`），
实测 `--help` 里 `--statistic` 的原文现在是
「Selection families: S (rare-coordinate surprisal), **Z1 (rare-coordinate CONCENTRATION … top_m = 1 is the v3.3 primary form)**, P …」。
但 **§18「另外三条事实」第 1 条**与 **§19.1 的 R2 行**仍然写着"帮助文本没有列出 Z1（见 §18 事实 1）"，
且 §18 第 1 条结尾还写着"A″ 之前是否修由组长定"——**组长已经定了，正文没同步**。

**改法**：删掉 §18 事实 1，§19.1 的 R2 行改抄现在的帮助原文。

---

## 4. NOTE（不阻塞，建议顺手改）

| # | 位置 | 事实 |
|---:|---|---|
| **N-1** | §2.4 / §5 / §7.1 | 「`x_beyond_h` 在 `H = ∞` 下**恒为空**」「代码判据 `max(grid) < X` 在全路径栅格上**恒为假**」是**实测结论，不是代码恒等式**。代码是 `block["x_beyond_h"] = (last_end is None) or (last_end < x)`，`grid` 是 **V1 的端点栅格**（有洞），原则上可以在 X 之前结束。G-dev 上 126 条正例的 `last_end − x` **最小值是 +38 token**（余量很大，注册期望值 0 是安全的）。建议把措辞改成"实测恒为 0，最小余量 +38 token；仍作分层落盘并在报告里复述" |
| **N-2** | §13.1 第 7 条 | 「`positives_anchored.per_episode[*]` 的 **41** 个字段」是 v3.2 的计数。注册命令带 `--recall-horizons 8,16,32,64,full`（5 个视界）后实测是 **45** 个字段（v3.2 的 4 视界确实是 41）。仍然**没有** `fold` 字段，该条的结论不变 |
| **N-3** | §7.3 / §19.1 的 `--far-episode-census` 行 | 帮助文本与正文都写字段是 `scenario`，**实际落盘的键是 `pair_group_id`**（整行是 `key, pair_group_id, episode_index, arm, channel, token_count, endpoint_count, filter_pass, alarm, first_alarm_end, min_p_fused`）。做 T-4 的 `n_kb` join 时必须用 `pair_group_id` |
| **N-4** | §9.2 F3 的 G-dev 投影列 | F3 的定义是"**两个分母各判**"，但投影只给了 `all` 分母的一组（short .04494 / medium .10084 / long .09910）。`filtered` 分母实测是 short **.04082** / medium **.11224** / long **.09278**，最差档仍是 medium、仍 PASS，但两组数都应写进去 |
| **N-5** | §9.3 的"已知表面缺陷"注 | 该注说脚本会把 G-conf-2 的 **D1 / D3 / D4** 打成 FAIL。**D2 有同样的暴露**（硬编码 40，G-conf-2 阈值 20）。另一方面 **D1x 不受影响**：`THRESHOLDS["D1x_reachable_x_positives"]` 已经是 **62**，与 §9.3 的 G-conf-2 阈值逐位相同，值得在注里点名以免审阅者误判 |

---

## 5. 复核通过、不构成 finding 的项（逐条列出，供 A″ 审阅者复用）

1. **Z1 走通两阶段清单**：manifest `folds["k"].cells.Z1.statistics.**Z1**` 带 `kind = "rare_concentration"` 与 `top_m = 1`
   （注意 §19.3 写的路径 `…cells.Z1.statistics` 比实际**少一层**，键在 `statistics.Z1` 下）；
   阶段 2 `cells.Z1.folds[k].restored_from_manifest == true`；`load_state` 覆盖 `top_m`，
   ⇒ **阶段 2 的 `--top-m` 确实不参与计算**（与 §3.3 的说法一致）。
2. **五格清单**：`cell.statistics` 与三折 `folds["k"].cells` 的键集合实测都是 `["J","M","P","S","Z1"]`
   （`prob_js → J` 由 `STATISTIC_ALIASES` 归一）。别名 `z1 / rare_concentration / concentration / s_top1` 四个都在。
3. **`--force-h inf` 是对称的**：`force_h_arg` 把 `inf/infinity/unbounded/full` 解析成 `int` 哨兵 `10^9`；
   `calibrate_g` 用 `z[:limit]`（limit = 10^9 ⇒ 参照全路径）且 `k_cal = limit`（⇒ 目标端点永不被 `horizon_censored`）。
   实测 `horizon.{mode="unbounded", n3_gate="n/a", censored_paths=0, censored_endpoints=0, rule_H=113/89/107, H_effective=552/628/668}`，
   manifest `cell.force_h = 1000000000`、`h_min_survivors = 90`。`--expect-h` 在 unbounded 下 `ok = (args.expect_h is None)`
   ⇒ **传 `--expect-h 352` 会判 FAIL**（§9.2 F8 / §18 D-10 正确）。
4. **`--debounce 1` 是恒等**：`apply_debounce` 在 `runs <= 1` 时 `applied = False` 直接返回，
   runner 只在 `runs > 1` 时调用它 ⇒ metrics 与不带开关逐字节相同（`DebounceTest::test_k_equal_to_one_is_a_no_op`
   与 `RunnerV33Test::test_debounce_1_is_byte_identical_to_the_run_without_the_switch`）。
   （**内部小瑕疵，不影响管线**：`trm3_g.debounce_p(..., runs=1)` 返回的是 `max(p_running, p_inst)` 而不是 `p_running`，
   与帮助文本"1 = the frozen single-look rule"不符；因为 `apply_debounce` 先短路，这条分支在管线里到不了。）
5. **分层清单键与阶段 2 硬拒绝**：无分层键是 `"<channel>"`，分层键是 `"<channel>@<stratum>"`；
   restore 时按 `f"{name}{suffix}"` 取，取不到就 `SystemExit`（消息里带 "did stage 1 use the same --stratify-reference?"）。
   **两个方向都拒绝**（清单不分层 + 阶段 2 分层 → 找不到 `Z1@n_kb=0`；清单分层 + 阶段 2 不分层 → 找不到 `Z1`）。
   注意这条拒绝**发生在 `verify_manifest` 的 19 项之后**（19 项里没有分层检查），是"键取不到"式的拒绝，不是守卫项。
6. **`VAL1` 的逐层落盘齐全**：`folds[k].strata.{key,count,per_stratum}`，每层带
   `n_fit / n_cal / n_reference_episodes / H / horizon / alpha_eff / attainability / attainable_rank / far{all,clean,benign_control,benign_lexical,filtered,episode_index_1_share}`；
   折层 `attainability.ok = all(每层 ok)`、`attainable_rank = 最差层`；折层 `alpha_eff` 在分层时是
   `_eval_weighted_alpha_eff`（实测 G-dev 分层格 `fold_summary.alpha_eff_weighted = 0.089215`，不分层 0.094595）
   ⇒ §19.3 的"键名需 grep 确认"两行**已确认**：`cells.<s>.folds[k].alpha_eff` 与 `cells.<s>.fold_summary.alpha_eff_weighted`。
7. **19 项守卫齐全**：实测 `threshold_manifest.verification.checks` 恰 **19** 项，名字与 §3.3 逐字相同，
   `failed == []`、`ok == true`；`created_at_ordering` 比四个时刻。
8. **`comparison_anchored` 的判定块齐全**：`two_condition.{ci, ci_excludes_zero, ci_lower, direction_positive, mcnemar_p, point_estimate, pair_count, family_count}`、
   `bootstrap.{recall_a, recall_b, point_estimate, ci, mcnemar, pair_count, family_count, robustness_48_cluster}`、
   `matched_alpha_secondary.source == "threshold_manifest.matched_alpha_inputs"`、
   `normal_denominator.denominator == "filtered_normal_union_no_legitimate_refusal"`、`primary_row == "matched"`；
   `holm_s1_one_sample.<s>`、`injection_pairing.J.{primary_unfiltered, sensitivity_filtered_negatives}`（两块都带 `two_condition`）、
   `positive_families.<s>.{positives_by_family, dropped_families}`、`comparison_anchored_horizons.{32,64}`、
   `run_once_guard.{allow_overwrite:false, enforced:true, existing:[]}`、
   `calibration_design.{v3_3, length_tertiles{source:"stage1_target_normals", replayed_from_manifest:true}}` **全部存在**。
9. **§17.3a 的 S2 先验逐位复现**（读 `v3_3_dev/stage2_Z1_vs_S/result.json`）：
   Δ̂ **+0.02380952**、CI **[−0.00877193, +0.07894737]**、McNemar **0.5078125**、b/c **6/3**、both 108 / neither 9、
   稳健列 [−0.018018, +0.075]（35 簇）、α_S **0.09473684**、两侧实测 FAR **0.08191126** 逐位相等、
   R_Z1 114/126 · R_S 111/126、`S` 的 `far.filtered` 0.09215017、`S` 折 0 的 F1 偏差 **+0.0416**、
   `S` 的 long 档 **0.15315 FAIL**、+32 (Δ +0.0397, b/c 5/0, p 0.0625) / +64 (Δ +0.0317, CI [0.00676, 0.06957], b/c 4/0, p 0.125)、
   注入在场 264 对 (Δ +0.037879, CI [0.010949, 0.069498], b/c 13/3, p 0.021271)。
   该运行的 `Z1` / `S` 两格 metrics 与因子轮 `stage2_hinf_nostrat_d1_{z1,s}` **深度 diff 为 0** ⇒ §12.1 的"逐位相同"成立。
10. **`g_conf_seal.py --verify` 的行为与 §13.1 第 1 / 2 条一致**：输出只有 `{seal, trace_count, mismatches, verified}`（**无时间戳**），
    只重算 `traces[*].trace_json_sha256` / `manifest_jsonl_sha256` 与 `packet` / `private_mapping`，
    **不覆盖** `frozen_inputs` / `run_configs` / `extra_files`、不枚举新增文件、不看只读位；参数是 `SEALED.json` 文件本身。
11. **`--stage calibrate` 的正常臂限制有牙**：`normals_only = stage == "calibrate" or --normal-only-smoke`，
    loader 只取 `io_g.NORMAL_VARIANTS`，之后再扫一遍非正常 episode 并 `SystemExit`；
    `refuse_sealed_pools` 拒绝**两个**冒烟开关指向带 `SEALED.json` 的池。
12. **`--recall-horizons` 强制含 16**（`SystemExit`），`--compare-horizons` 自动剔除 16，两者只加描述性块。
13. **测试计数**：`tests/test_research_v4_v3_3.py` **49 passed**，六个类的分布 9 / 7 / 7 / 7 / 13 / 6 与 §13 表逐条一致。

---

## 6. 给 A″ 执行者的两条操作提醒

1. **工作树现在不干净**（`src/agent_v3/factory/*` 与 `configs/dataset_g/*` 的 G-conf-2 在制品共 16 条）。
   §15.2 第 2 条与 `freeze_guard` 都要求非冒烟运行在干净树上跑，**§13 的"A″ 上重跑一次 v3.2 / v3.1 回归"因此必须排在
   G-conf-2 工厂改动提交之后**；本轮的 R-4 就是因为这一条被拒绝，只能改用 `--dev-smoke` 复核。
2. **§12.2 的阶段 1a / 1b 是非冒烟运行**（不传 `--normal-only-smoke`，靠 `--stage calibrate` 限制臂），
   所以它们同样受"干净树 + `HEAD == --freeze-commit` + `--prereg-sha256` / `--labels-sha256` 对得上"的约束。
   注册命令里的 `--prereg-path docs/research_v4/detector_prereg_v3_3.md` 必须**在 A″ 上确实存在该文件名**
   （现在只有 `_draft` 后缀的那份）。

---

*本文件由冻结审阅 agent（代码视角）撰写。全部结论均来自本轮实跑或实读；未跑到 / 未读到的项已在正文点名（R-4 的"改动前源码树"）。*
