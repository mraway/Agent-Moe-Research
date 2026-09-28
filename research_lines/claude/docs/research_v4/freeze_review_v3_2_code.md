# 冻结审阅 v3.2 · 代码视角（提交 A′ 之前，2026-09-07）

**审阅镜头**：**代码是否做了冻结文本说的事**。A′ 之前任何人都不得改代码，因此**凡是不一致，默认由改预注册措辞解决**，
除非代码本身是错的。本文件只报**实际读过代码或实际跑过命令**验证到的结论。

**核验基准**：`HEAD = 6f3905397c236df2eaad9a4ec1d16c7ed93cd93b`，工作树 `git status --porcelain` 只有 `?? artifacts` 一行。
**核验对象**：`detector_prereg_v3_2_draft.md`（rev3，含 §19–§19.4 裁定，约束性）、`prereg_v3_2_code_mapping.md`、
`freeze_a2_checklist.md`、`v3_2_harness_changes.md`；代码 `src/research_v2/{trm3_g,io_g}.py`、
`scripts/research_v4/{run_detectors_g,g_dev_data_gates,g_conf_seal,prereg_power_sim}.py`；
测试 `tests/test_research_v4_v3_2*.py`。

**数据纪律**：本轮**没有加载、没有打分、没有读取任何 G-conf 路由**。读到的 G-conf 侧字节只有
`configs/dataset_g/g_conf.json`（元数据，用于折键交叉表复算）与 §15.4.6 / §15.4.7 各文件的**字节摘要**。
两阶段冒烟在 **G-dev**（未封存的开发数据）上重跑，产物写在 scratchpad，**没有改动任何仓库内的 artifacts**。

---

## 0. 三句话

1. **判定层是可信的**：G-dev 两阶段冒烟按 `freeze_a2_checklist.md` 步 4 的命令**从零重跑一遍**，
   §0.2 / §4.2 / §9 的每一个数值**逐位复现**（R_S 0.824 = 103/125、Δ̂ 0.264、CI [0.024, 0.508]、
   McNemar 2.4995e-07、FAR 0.0980 / 0.1195、静默 5/40 与 9/128、五条门 F1 PASS / F3 FAIL / F5 PASS / N1 FAIL / N2 PASS、
   折图 sha256 `e389122a…`、切点 219/382、`x_beyond_h` 18 与 17、S1、S2、S-J 144 / 124、守卫 15/15）；
   全量测试 **1316 passed, 128 subtests**（78.6 s）；§15.4.4 / §15.4.5 / §15.4.6 的哈希**逐行相同**；
   五路 census 与 §4.1 相同；G-conf 折键交叉表在配置元数据上复算为 **OSY 31/31/31 · TSL 32/31/31 · WRH 31/31/31**（旧折键 40/0/53 · 54/40/0 · 0/53/40）。
   两阶段守卫**有牙**：缺 manifest、篡改一个字段、请求不在 `cells` 里的统计量——三次都非零退出。
2. **文本侧有 5 条必须在 A′ 之前改掉的东西**，其中最重的是
   **§12.2（G-conf 唯一一次性运行的逐字命令）里有 4 个开关 / 取值根本不存在**（`--cells`、`--threshold-manifest-out`、`--anchor X`、
   `g_conf_seal.py --verify <目录>`），**照抄会直接报错**；以及
   **§7.1（主判定列的取数表）指向的是 E 锚点的旧块**——`comparison.rows.matched.bootstrap.recall_a` **存在且返回 0.0508**，
   而注册口径的 0.824 在 `comparison_anchored` 里，**这是"读到错数字而不报错"的那一类**。
3. **代码映射本身有一处实质错误**：§5 缺口 4 说"逐折 `ep1_share` 根本不落盘"——**它落盘了**，
   在 `cells.<s>.folds[k].far.episode_index_1_share`；而它给出的替代方案 (b)（从 `M.folds[k].*_episodes` 数 `#ep1` 后缀）
   **不可执行**，那三个字段是**整数**不是 key 列表。组长被要求在两个都不对的选项之间做选择。

---

## 1. 复现记录（命令与读数）

| 项 | 命令 / 位置 | 结果 |
|---|---|---|
| 全量测试 | `pytest tests/ -q` | **1316 passed, 128 subtests**，78.56 s（与操作单参考读数一致） |
| v3.2 两个套件 | `pytest tests/test_research_v4_v3_2.py tests/test_research_v4_v3_2_round2.py -q` | **106 passed**（52 + 54） |
| 两阶段冒烟 | `freeze_a2_checklist.md` 步 4 的三条命令，`--output-root <scratchpad>/review_smoke` | 阶段 1 51.9 s / 1.05 GB；阶段 2 73.0 s / 1.42 GB；`stage1_attack_traces_skipped = 264` |
| 逐值比对 | 新产物 vs `v3_2_round2_smoke/{stage1,stage2,stage2_S_vs_M}` | 检查的 **24 个量全部 OK**（见 §1.1） |
| 五路 census | `io_g.variant_census('.../g_dev')` | clean 192 / benign_control 192 / benign_lexical 24 / legitimate_refusal 24 / attack 352 = **784**；`attack_bearing_episodes = 264`、`attack_pre_injection_episodes = 88` |
| 折键交叉表 | `io_g.fixture_map_from_config('configs/dataset_g/g_conf.json')` + `trm3_g.fold_assignment` | `fixture_rank_mod` **OSY 31/31/31 · TSL 32/31/31 · WRH 31/31/31**；`scenario_mod` **OSY 40/0/53 · TSL 54/40/0 · WRH 0/53/40**；G-dev 新折键 4 × 26/26/26 |
| §15.4.4 / .5 哈希 | `git show HEAD:<path> \| sha256sum` | 抽查 12 行**逐位相同**；`tests/test_research_v4_*.py` 实有 **13** 个，表里 13 行 |
| §15.4.6 哈希 | `sha256sum` 工作树 | **12 行全部相同** |
| CLI 开关 | `run_detectors_g.py --help` | 代码映射 §4 第 3 条列的 **14 个开关全部命中**；`--fold-key {fixture_rank_mod,scenario_mod}`；v3.1 §19.7 的 **31 个开关全部存在** |
| 函数名 | `grep '^def '` | 代码映射 §1 / §2 点名的 **34 个函数全部存在** |
| 测试名 | `grep -rl` | 代码映射 §2 点名的 **28 个测试类 / 用例名全部存在** |

### 1.1 逐值比对（新跑 vs 冻结产物，全部 OK）

R_S 0.824 · R_P 0.560 · Δ̂ 0.264 · CI [0.023529…, 0.508333…] · McNemar 2.4995097192004323e-07 · 配对 125 · 家族 16 ·
FAR all 0.09803921568627451 / filtered 0.11945392491467577 · 静默 5/40 与 9/128 ·
门 F1 PASS 0.11945 / F3 FAIL 0.15315 / F5 PASS 0.17857 / N1 FAIL 0.71814 / N2 PASS 28 ·
折图 sha256 `e389122a393a9ef9…` · `collinear_fixtures = []` · 切点 [219, 382] ·
`reachability {positives 126, anchor_reachable_plus_16 108, x_beyond_h 18, window_unreachable_plus_16 1}` ·
`by_x_beyond_h {False: 99/108, True: 4/17}` · 逐折 `far.filtered` [0.1895, 0.0745, 0.0962] ·
逐折 `alpha_eff` [0.09524, 0.09375, 0.09474] · 逐折 `n_cal` [104, 95, 94] ·
S1 0.824 / [0.7090909…, 0.9186991…] / p 4.997501e-4 · S-J 144 / 0.2153 与 124 / 0.2016 ·
守卫 15 项 `failed = []` `ok = true` `v3.2-2` · `attack_trace_census.count = 264` ·
`early_than_anchor {36, 0.288, 125}` · S2 −0.016 / [−0.0789, 0.0366] / p 0.7539 / 两条件都 false。

### 1.2 守卫的牙（实跑）

| 试验 | 结果 |
|---|---|
| `--stage score` 不传 `--threshold-manifest` | **exit 1**，"stage 2 may not fit or calibrate anything" |
| manifest 改一个字段后 | **exit 1**，`manifest_sha256` expected ≠ observed |
| `--statistic B`（不在 `manifest.cells` 里） | **exit 1** |
| manifest 换一个路径（内容不变） | **exit 0**（自哈希不含路径，正确） |
| `--stage calibrate` 且不带 `--normal-only-smoke` | **exit 1**（先被冻结守卫拦下；attack 剔除在 `load_pool(variants=NORMAL_VARIANTS)` + `offenders` 二次断言，见 `run_detectors_g.py:3715-3730`） |
| 阶段 2 传 `--window-s 4` / `--rare-threshold 0.05` / `--force-h 200` | **exit 0，且三次结果与基准逐位相同**——常量全部从 manifest 重放，命令行值被忽略（见 §2 · C5 / S5） |

---

## 2. BLOCKING

### C1 · §12.2 的 G-conf 逐字命令有 3 个开关 / 取值不存在

`--cells S,P,M,J` → `error: unrecognized arguments`；`--threshold-manifest-out <PATH>` → `unrecognized`；
`--anchor X` → `invalid choice: 'X' (choose from 'c', 'e_view', 'x')`。
（`--anchor min_x_xtool`、`--pair-filter`、`--fold-key scenario_sorted_mod` 同样不存在，只是不在 §12.2 的命令里。）
代码映射 §2 第 3 / 5 / 6 条已经指出这些名字与代码不符，但它的处置建议只覆盖 **§13 的措辞**，
**§12.2 是真正要照着敲的那份**，而 G-conf **只跑一次、不允许补跑**。
**处置**：把 §12.2 改成实际形式——`--statistic S,P,M,prob_js`（阶段 1）、`--threshold-manifest <PATH>`（一个开关兼读写）、
`--anchor x`；并把 §13 第 3 / 5 / 6 条一并改掉。

### C2 · §12.2 的封存核验命令传了目录，会崩

`g_conf_seal.py --verify artifacts/agent_v2/dataset_g/g_conf` → `IsADirectoryError`（`verify()` 直接 `read_text()` 该路径，
`g_conf_seal.py:283`）。正确形式是 `--verify artifacts/agent_v2/dataset_g/g_conf/SEALED.json`。
这条命令在 §12.2 出现**两次**（第 0 步、第 1.5 步），两次都要改。

### C3 · §7.1（主判定列取数表）指向 E 锚点的旧块，会读到错的数字而不报错

| §7.1 写的路径 | 实际 |
|---|---|
| `comparison.rows.matched.bootstrap.recall_a` / `recall_b` | **存在**，但值是 **0.0508 / 0.0051**（v3.1 的 E 锚点比较）；注册口径的 0.824 / 0.560 在 `comparison_anchored.bootstrap.*` |
| `…bootstrap.point_estimate`（Δ̂） | 同上块返回 **0.0457**，注册值 0.264 在 `comparison_anchored` |
| `cells.<stat>.metrics.positives.recall.x_window.reachable_count` | **不存在**（`positives.recall` 只有 `penalty/no_penalty_plus_{8,16}`）；实际是 `positives_anchored.recall.x_window.reachable_count` |
| `cells.<stat>.metrics.positives.reachability.x_beyond_h` | **不存在**；实际 `positives_anchored.reachability.x_beyond_h` |
| `…bootstrap.family_sizes` | **不存在**（代码映射 §5 缺口 1 已记；此处是第二处出处） |
| `comparison.matched_alpha_secondary` / `comparison.normal_denominator` | 存在且**恰好等于** `comparison_anchored` 的同名块（两块共用同一分母），但依赖巧合 |

前两行是本次审阅里**唯一一类"不会报错、只会给错数"的缺陷**。代码映射 §3.1 给的是对的路径，
但 §7.1 是预注册正文、是冻结的那一份。**处置**：§7.1 整表改成 `comparison_anchored.*` / `positives_anchored.*`，
家族大小向量改指 `positive_families.<s>.positives_by_family`。

### C4 · `PREREG_PATH` 仍指向 v3.1，且被一条测试钉死在那个文件名上

`scripts/research_v4/run_detectors_g.py:81` → `PREREG_PATH = ROOT/"docs"/"research_v4"/"detector_prereg_v3_1.md"`；
`tests/test_research_v4_freeze_fixes.py:374` 断言 `PREREG_PATH.name == "detector_prereg_v3_1.md"`。
后果：本轮阶段 1 的 manifest 里 `prereg.path` 记的是 v3.1、`prereg.sha256` 是 v3.1 的
`f9351639d93877539ef5e481293eac0951f55ce5c14a4fc4e91c47649168e2c7`；每个 `result.json` 同样。
**§15.2 第 1 条（"`PREREG_PATH` 必须改指本文件并由一条钉住文件名的测试保护"）目前未满足**，
操作单步 9 已把它列为 A′ 之前的动作。**注意连锁**：改它会同时改 `run_detectors_g.py` 与
`test_research_v4_freeze_fixes.py` 的 sha256，§15.4.4 的两行必须在 A′ 上重算。

### C5 · §3.2 的"必须包含的字段"与实际 manifest 大面积不符，其中两个字段**根本不存在于代码**

实测 `threshold_manifest.json` 顶层键：
`cell · code_commit · created_at · fit · fixtures · fold_assignment · fold_assignment_sha256 · fold_count ·
fold_fixture_crosstab · fold_key · fold_pools · folds · freeze_commit_requested · freeze_commit_resolved ·
inputs · kind · length_tertiles · manifest_version · matched_alpha_inputs · prereg · rule · sha256 ·
stage1_attack_traces · stage1_attack_traces_skipped`。

| §3.2 要求 | 实际 |
|---|---|
| `stage1_attack_arms_read = false`（§14 第 10 条、§15.3 第 3 条都要求读它） | **全仓库 grep 不到这个标识符**；等价留痕是 `stage1_attack_traces_skipped` + `stage1_attack_traces` |
| `schema_version = "v3_2_multicell"` | 无；实际是 `manifest_version = "v3.2-2"` |
| `frozen_constants{view, tag_scope, window, layers, rare_threshold, alpha, reference_construction, horizon_H, h_unit, h_source, band, hit_window, anchor}` | 无；实际是 `cell{alpha, bucket_size, cal_filtered_only, fold_key, folds, force_h, h_min_survivors, layers, min_*, rare_threshold, standardise, statistics, tag_scope, view}`——**没有 band / hit_window / anchor / reference_construction / h_unit / window** |
| `fold_rule{K, key, assignment, fixture_of, scenario_count, fold_assignment_sha256}`（§9.2 F8 要断言 `fold_rule.key`） | 无该块；散在顶层 `fold_key` / `fold_count` / `fold_assignment` / `fixtures` / `fold_assignment_sha256` |
| `batch: "g_conf"` | 无 |
| `freeze_commit` | 实际是 `freeze_commit_requested` / `freeze_commit_resolved` 两个 |
| `matched_alpha_inputs{target_measured_far, normal_denominator, selection: "pooled", grid, matched_alpha, matched_alpha_measured_far}` | 实际 `{alpha_primary, cells.<s>.{grid, measured_far_at_alpha, normal_count, statistic}, denominator, normal_keys_sha256, rule}`——**`selection` / `matched_alpha` / `matched_alpha_measured_far` / `target_measured_far` 四个字段都没有** |
| `folds[k].{n_fit, n_cal, survivors_at_H, censored_paths, censored_endpoints, ep1_share, fixture_counts}` | 折层只有 `{cells, eval_episodes, fit_episodes, reference_episodes, fit_keys_sha256, reference_keys_sha256, fold, rotation}`；`n_fit` / `n_cal` / `survivors_at_H` / `censored_paths` 在 `folds[k].cells[<s>]` 里；`fixture_counts` 在顶层 `fold_fixture_crosstab`；`ep1_share` 在 `result.json` 侧（见 S3） |
| `cells[<s>].{attainability_rank, Omega_rare, q_table_sha256, whitening_ref, fit_pool_distribution_sha256}` | 无这五个名字；rank 在 `cells[<s>].attainability.channels.<s>.rank`，指纹全部在顶层 `fit.{q_table_sha256, whitening, cells}` 里 |
| `inputs{normal_run_dirs, label_files, seal_manifest_sha256, normal_episode_count, filtered_normal_episode_count}` | 实际 `{label_sha256, normal_trace_set_sha256, normal_traces, normal_traces_per_dir, scenario_reports, seal, target_dirs}` |
| `manifest_sha256`（自哈希） | 实际字段名是 `sha256` |

**功能没有缺失**（15 项守卫全 PASS，多格 `S/P/M/J` 齐全，三分位与 matched-alpha 都冻结在阶段 1 并在阶段 2 重放），
但 §3.2 是**规范性**清单，且 §9.2 F8 / §14 第 10 条 / §15.3 第 3 / 3b / 6 条**逐字引用它的字段名**。
**处置**：§3.2 整块换成实测结构；`stage1_attack_arms_read` 要么删掉、要么由组长明确改写成 `stage1_attack_traces_skipped`。

---

## 3. SHOULD-FIX

### S1 · §15.3 冻结审阅者清单的 8 处字段路径 / 期望值不成立

| §15.3 条 | 写的 | 实际 |
|---|---|---|
| 3 | `threshold_manifest.stage1_attack_arms_read == false` | 字段不存在（C5） |
| 3b | 顶层 `fit{q_table_sha256, q_table_path, whitening}` ✅；`cells` 里 `Omega_rare` / `q_table_sha256` / `whitening_ref` / `fit_pool_distribution_sha256` ❌ | 都在顶层 `fit` 下，不在 `folds[k].cells[<s>]` 里 |
| 4 | `assertions.by_fold[k]` 三行 | 实际 `assertions.by_statistic.<s>[]`，每格 **15 条**（5 项 × 3 折） |
| 5 | `positives.recall.x_window` 且 `convention == "e_view_to_anchor_plus_h"` | 路径是 `positives_anchored.recall.x_window`，其 `convention == "penalty"`；`e_view_to_anchor_plus_h` 在 `positives_anchored.hit_window` 与 `anchoring.hit_window` |
| 5 | `positives.anchor_sensitivity` 含 `x_plus_16_strict / x_plus_16_no_penalty / e_plus_16_strict / full_path` | `positives_anchored.anchor_sensitivity` 的键是 `band_0/4/5/8`；那四个口径在 `positives_anchored.recall.{penalty,no_penalty}_plus_{8,16,32,full}` |
| 6 | `comparison.normal_denominator.denominator == "filtered_normal_union"` | 实际 `"filtered_normal_union_no_legitimate_refusal"` |
| 6 | `matched_alpha_secondary` 逐位等于 manifest `matched_alpha_inputs.matched_alpha` | manifest 里没有 `matched_alpha`（C5）；可核对的等价物是 `matched_alpha_secondary.source == "threshold_manifest.matched_alpha_inputs"` + `measured_far` 等于 `matched_alpha_inputs.cells.S.measured_far_at_alpha`（实测两者都是 `0.11945392491467577`） |
| 7 | `cells.J.metrics.positives.definition == "injection_present"`；`pair_filter == "none"` | `positives` 块没有 `definition`；实际是 `cells.J.metrics.injection_presence.kind == "injection_present"`；**没有 `pair_filter` 键**（两个口径无条件并存） |
| 8 | `far.length_tertile_definition.source == "stage1_target_normals"` | 实际 `"frozen_g_cal_cutpoints"`（见 S2） |
| 10 | `bootstrap.family_sizes` 已落盘 | 不存在（C3 / 代码映射缺口 1） |

### S2 · `metrics.far.length_tertile_definition` 在 v3.2 运行里带着一句**假的**出处

`trm3_g.py:2741` 把 `source` 硬编码成 `"frozen_g_cal_cutpoints"`（只要 `tertile_cutpoints is not None`），
`rule` 写的是 "the cutpoints frozen on the filtered G-cal pool and **never re-derived on the target**"。
而 v3.2 的切点 `[219, 382]` **正是**阶段 1 在目标批正常臂上导出、冻结进 manifest 再重放的
（真实出处在 `calibration_design.length_tertiles`：`source = "stage1_target_normals"`、`replayed_from_manifest = true`）。
**A′ 之前不能改代码**，所以：§15.3 第 8 条改指 `calibration_design.length_tertiles`，
并在 §16 的范围声明里写明"`cells.<s>.metrics.far.length_tertile_definition` 这一块的 `source` / `rule` 字符串在 v3.2 下是错的，
以 `calibration_design.length_tertiles` 为准"。（`cutpoints` 的**值**是对的，只有出处字符串错。）

### S3 · 代码映射 §5 缺口 4 的事实与处置都不对

- **事实错**：逐折 `ep1_share` **落盘了**——`cells.<s>.folds[k].far.episode_index_1_share`
  （`run_detectors_g.py:2214`；G-dev 实测三折都是 `0.23529411764705882`）。
- **处置 (b) 不可执行**：它说"从 `M.folds[k].{fit_episodes, reference_episodes, eval_episodes}` 的 `#ep1` 后缀统计"——
  这三个字段是**整数**（94 / 104 / 136），不是 episode key 列表；manifest 里只有 `fit_keys_sha256` / `reference_keys_sha256`（哈希）。
- **处置 (a)（加代码）也没必要**，会白白让 §15.4.4 全表重算。
- **正确处置**：§13 第 13 条、§14 第 3 条、§3.2、代码映射 §3.3 与 §5 缺口 4、操作单第 14b 条，
  一律改指 `cells.<s>.folds[k].far.episode_index_1_share`。

### S4 · 逐折命中率拿不到：`positives_anchored.per_episode[*]` 没有 `fold` 字段

代码映射 §3.3 写"命中率的逐折切分由 `positives_anchored.per_episode[*].fold` 聚合"；
实测 `per_episode` 是一个 126 项的 dict，每项 **39 个字段**（`alarm_count / anchor / attack_family_id / domain_group /
e_view / first_alarm_end / hit_* / injection_channel / reachable_* / silent / trajectory_class / variant / wording_tier / x / x_beyond_h / x_tool` …），
**没有 `fold`**。§14 第 3 条要求"逐折…留出折的 FAR **与命中率**"。
FAR 侧有（`cells.<s>.folds[k].far`），**命中率侧没有可聚合的折标签**。
**处置**：要么改 §14 第 3 条的措辞（逐折只报 FAR，命中率不逐折报），要么由组长接受"报告方用
`calibration_design.fold_assignment` 把 `per_episode` 的 key 反查 scenario → 折"并把那条统计命令写进操作单。
（后者可行：`fold_assignment` 是 scenario → 折的全表，`per_episode` 的 key 带 scenario 前缀。）

### S5 · §3.3 的"格常量逐位不符即 SystemExit"不是代码做的事；§13 第 5 条的测试 (g) 也不存在

`verify_manifest` 的 `cell_matches` 只比 **5 个**常量：`view / tag_scope / alpha / folds / fold_key`
（`run_detectors_g.py:3524-3546`）。实跑三次阶段 2，分别加 `--window-s 4`、`--rare-threshold 0.05`、`--force-h 200`：
**三次都 exit 0**，且 Δ̂ / R_S / R_P / N / FAR / 逐折 H **与基准逐位相同**——
因为阶段 2 把统计量状态、标准化器、参照集、阈值、视界全部从 manifest 重放，命令行上的这些值**被忽略**。
这在科学上是**更安全**的行为（工作点不可能被阶段 2 的命令行改动），但**不是 §3.3 写的行为**，
而 §13 第 5 条把"(g) 改动某一格的冻结常量 → `SystemExit`"列为**必须有的测试**——`tests/` 下没有这条测试。
**处置**：§3.3 第 2 条与 §13 第 5 条改成"阶段 2 的这些常量一律从 manifest 重放，命令行取值不参与打分；
`cell_matches` 守卫的是 `view / tag_scope / alpha / folds / fold_key` 五项"，并把上面三次实跑作为证据写进操作单。

### S6 · §12.2 的阶段 2 形态与被排练过的形态不是同一条命令

§12.2 写的是 `--statistic S --compare-statistic P`（`cells = {S, P}`），S2 与 S-J **各跑一次**；
`freeze_a2_checklist.md` 步 4 排练的是**一条** `--statistic S,P,M,prob_js` 的阶段 2（`cells = {S, P, M, J}`，
`injection_pairing` / `holm_s1_one_sample` 四格全出），S2 才另跑一次。
§13 第 9 条还把"主格运行的 `cells` 键 ⊆ {S,P} 或 {S,M}"列为必须有的断言——**被排练的命令违反它**。
G-conf **只跑一次、不允许补跑**，所以必须注册其中**恰好一种**。
（两种形态下 H1 的判定量相同——`comparison_anchored` 只用 `names[0]` 与 `--compare-statistic`——但产物结构不同。）

### S7 · `--expect-n-reference-folds` 存在，但没有出现在任何注册命令里，`n_reference` 断言因此空转

实测 `assertions.by_statistic.S` 的三条 `n_reference` 行 `expected = None`、`ok = true`
（`observed` 104 / 95 / 94）。这正是 v3.1 code S-2 修过一次的缺陷——那次的处置是**在 §19.7 的冻结命令里写死
`--expect-n-reference 279`**。v3.2 的 §12.2 与操作单步 4 **都没有** `--expect-n-reference-folds`，
而 §9.2 F8 明写"逐折 `n_reference` 等于阶段 1 记录值"。
**处置**：G-conf 阶段 2 的注册命令补 `--expect-n-reference-folds <阶段 1 的三个值>`（值从 M1 的 manifest 抄）。

### S8 · 代码映射 §2 第 12 条写的函数签名不存在

写的是 `trm3_g.cluster_bootstrap_rate(hits, families, *, replicates, level, null_value)`；
实际是 `cluster_bootstrap_rate(hits_by_family, *, replicates=2000, seed=20260907, level=0.95, null_rate=0.5)`
（`trm3_g.py:3270`）——**一个 mapping 参数**（不是 hits + families 两个），`null_rate` 不是 `null_value`，另有 `seed`。
代码映射自称"函数名由 `grep -rn` 命中"，这一行的签名没有对着代码抄。

---

## 4. NOTE

1. **§3.3 第 2 条要求"manifest 的 `prereg.sha256` 与本次运行不符 → SystemExit"，15 项守卫里没有这一条**。
   间接覆盖是 `manifest_sha256`（自哈希）+ `manifest_code_commit == HEAD` + 运行自身的 `freeze_guard`
   （`--prereg-sha256` 对盘上文件）。功能上够，但字面上的那条检查不存在。
2. **`seal_check` 不是内容核验**：它把 `SEALED.json` 里记录的 `trace_json_set_sha256` 与**从该文件自己的行**重算的值比对
   （`run_detectors_g.py:2038-2058`），**不重新哈希盘上的 `trace.json`**。
   所以 "阶段 1 前后两次读数相同" 只证明 `SEALED.json` 本身没变。真正的内容核验只在 `g_conf_seal.py --verify` 里
   （它逐条重算 `trace.json` / `manifest.jsonl` 的 sha256）。§3.4 已经写了"两阶段解封是代码强制 + 流程纪律，不是操作系统强制"，
   建议再补一句区分这两条路径，否则 §15.3 第 3 条的"两次封存哈希核验"会被读成内容核验。
   （G-dev 上两次都是 `present: false` 的空跑——组长裁定 Q7 已要求在日志里写明。）
3. **S1 的两个条件在 B = 2000 上并不严格等价**：`ci_lower_above_null` 用 `draws[floor(0.025·B)] > null`
   （≤ null 的抽样数 ≤ 50 即成立），而 `p_value = (#{≤null}+1)/(B+1) < 0.025` 要求 ≤ 49。
   边界那一格（恰 50）两者相反。`cluster_bootstrap_rate` 的 docstring 与 §10.2 都写"等价 / 重合"，
   实际差一个次序统计量。**在注册的合取判据下这是保守方向**，不改判定；`R2::ClusterBootstrapRateTest::test_the_ci_lower_bound_and_the_one_sided_p_agree`
   用的是一个不在边界上的样本，所以它通过。建议把 §10.2 那句"两条件重合"弱化为"在第一步上几乎重合，差一个次序统计量，方向保守"。
4. **代码映射 §2 第 10 条的测试栏指错了**：`channel` 列的实际断言在
   `tests/test_research_v4_v3_2.py::RunnerV32Test::test_stage_score_scores_every_arm_from_the_frozen_manifest`（第 1053 行），
   不是 `PV31::ResultJsonContractTest` / `IOG`（那两处不检查 `channel`）。功能与测试都在，只是引用错。
5. **操作单步 6 比 §15.4.4 多哈希三个文件**：`src/agent_v3/packets/{schema,validate,build}.py` 在操作单的循环里，
   §15.4.4 的表里没有这三行。两处要对齐（§15.4.4 的小标题只承诺 `scripts/research_v4/*.py` 与 `tests/test_research_v4_*.py`）。
6. **§12.1 仍写"提交 A / 提交 B"与 `freeze_a_checklist`**，rev3 其余部分一律用 A′ / B′ 与 `freeze_a2_checklist.md`。
7. **§1.1 / §7.1 / §10.2 里的裸 "§7.4" 指的是 v3.1 §7.4**（v3.2 的 §7 只有 7.1–7.3）。
   §2.1 的"按原节号引用"约定救了它，但建议统一写成 "v3.1 §7.4"，正文里已有一处（§6 的 B-P 行）是这么写的。

---

## 5. 结论

- **判定层：可以冻结。** 16 条 harness 改动全部有实现、有测试、能在真实数据上跑通并逐位复现；
  两阶段守卫在三种攻击方式下都非零退出；阶段 2 不重算任何工作点（三次改常量的实跑证明命令行值被忽略）；
  §15.4 的哈希、五路 census、G-conf 折键交叉表全部复算通过；全量测试 1316 passed。
- **文本层：不能就这么冻结。** C1 / C2 会让 G-conf 那次**唯一**的运行照抄即报错；
  C3 会让报告方**读到 E 锚点的数字而不自知**；C4 是 §15.2 第 1 条明写的前置条件仍未满足；
  C5 让 §9.2 F8 / §14 第 10 条 / §15.3 第 3 / 3b / 6 条的一半检查项**无从执行**。
  五条都只需要**改预注册的措辞**（C4 需要一行代码 + 一行测试，且必须在 A′ 这一次提交里做完，
  之后 §15.4.4 的两行要重算）。
- **代码映射本身要修**：S3（`ep1_share` 落盘了、替代方案不可执行）、S4（逐折命中率）、S8（函数签名）、NOTE 4（测试引用）。
  它目前是 §13"现状"列的替代物，冻结审阅者会照它逐条核对，所以它的准确性与预注册正文同等重要。
