# 预注册 v3.2 → 代码映射（冻结提交 A′ 之前的核验，2026-09-07）

作者：Opus 5 研究工程师（受组长 Claude Fable 委派）。
对象：`docs/research_v4/detector_prereg_v3_2_draft.md`（rev3）的 **§13 十六条 harness 改动**、
**§14 十二条报告义务**里的每一个量，以及 §15.2 第 4 / 6 条的冻结前置条件。

**本文件替代预注册 §13 的"现状"列**；冻结版应把 §2 与 §3 整表并回正文或以本文件的 sha256 引用。
形式沿用 `docs/research_v4/prereg_v3_1_code_mapping.md`。

**核验基准**：HEAD **`ea93668`**，工作树 `git status --porcelain` 只有 `?? artifacts` 一行。
**核验方式**：本文件的每一行都是**当轮对着代码复核**的，不是沿用上一轮的自述——
函数名与 `result.json` 键由 `grep -rn` 在 `src/research_v2/` 与 `scripts/research_v4/` 下命中，
CLI 开关由 `python scripts/research_v4/run_detectors_g.py --help`（以及 `prereg_power_sim.py --help`、
`g_dev_data_gates.py --help`、`g_conf_seal.py --help`）的**实际输出**命中，
落盘键由第二轮冒烟的真实产物
`artifacts/agent_v2/dataset_g/v3_2_round2_smoke/{stage1,stage2,stage2_S_vs_M}/result.json` 命中。
**凡是本文件写 `MISMATCH` 的行，都是"§13 的措辞与落地形式不一致"，不是"功能缺失"**——
每一行都写明了实际的落地形式。

**范围声明（数据纪律）**：本轮**没有加载、没有打分、没有读取任何 G-conf 路由**。
唯一读到的 G-conf 侧字节是 `configs/dataset_g/g_conf.json`（元数据）、
`artifacts/agent_v2/dataset_g/g_conf/SEALED.json` 与 `annotations/g_conf/*.jsonl` 的**字节摘要**（sha256）。
本轮**没有修改任何代码、配置或标签**，也没有在任何批次上跑过检测器。

---

## 0. 三句话

1. **§13 的 16 条全部有落地实现且有实际存在的测试**（16/16，判定层无缺失），
   但**其中 6 条的"flag / 字段名"与预注册写的不一致**（第 3 / 5 / 6 / 11 / 13 / 16 条），
   逐条的实际形式在 §2 的"MISMATCH"注里；**冻结提交 A′ 之前必须二选一：改预注册的措辞，或改代码的开关名**。
   我的建议是**改预注册的措辞**——代码的形式更好（例如 S-J 的两个口径**无条件同时落盘**，比 `--pair-filter` 开关更难被误用）。
2. **§14 的 12 条报告义务里，判定层的每一个量都读得到**；两处要改读法、一处真缺：
   **(i)** `…bootstrap.family_sizes` 不存在（§13 第 16 条的字段名），家族大小向量要读
   `positive_families.<stat>.positives_by_family`（配对 bootstrap 块只落 `family_count`）；
   **(ii)** "报警的通道分布"只在 `outputs*.jsonl` 的 `channel` 列里，`result.json` 没有汇总；
   **(iii) 逐折 `ep1_share` 根本不落盘**（只在一行日志里），这是本文件找到的**唯一一个描述性列的真缺口**，
   见 §5 缺口 4——**它不影响任何判定**，但 G-conf 只跑一次，日志是唯一副本。
3. **两条只能由流程保证、代码保证不了的**：`--arm-hashes` 不在 G-conf 阶段 1 之前跑（组长裁定 Q6）、
   阶段 1 只跑一次且哈希当场记录（Q10——manifest 自哈希含 `created_at`，重跑会换哈希）。
   两条都进了 `docs/research_v4/freeze_a2_checklist.md`。

---

## 1. 文件与改动面（第二轮之后的状态）

| 文件 | v3.2 的改动 | 性质 |
|---|---|---|
| `src/research_v2/trm3_g.py` | `fold_assignment` / `fold_of_episodes` / `fold_fixture_crosstab`（折键与交叉表）、`anchored_positives`（X 锚点、窗口命中、可达性、`x_beyond_h` 分层）、`injection_presence_block`（S-J 正例）、`cluster_bootstrap_rate`（S1 的单样本聚类 bootstrap）、`group_hits_by_cluster`、`matched_alpha_by_measured_far`、`evaluate_g` 的两个静默分母、`ProbJS.state_dict` / `load_state` 的参照分布指纹 | 全部 additive；v3.1 的签名与默认行为保留 |
| `src/research_v2/io_g.py` | `fixture_map_from_config` / `fixture_map` / `subset_config_for_run`（fixture 元数据的唯一来源，只读配置） | additive |
| `scripts/research_v4/run_detectors_g.py` | 两阶段（`run_cell_v32` / `build_manifest` / `verify_manifest` / `manifest_cells` / `manifest_matched_alpha`）、`fold_pools` / `fold_far_block`、`compare_cells_anchored` / `two_condition_block`、`injection_pairs` / `compare_injection_pairs` / `compare_injection_presence`、`one_sample_rate_block`、`gate_block`、`positive_family_census`、`attack_trace_census`、`seal_check`、`normal_trace_manifest`、`matched_alpha_inputs` | additive 且 flag-gated；不带任何 v3.2 开关时走的就是冻结的 v3.1 代码路径 |
| `scripts/research_v4/g_dev_data_gates.py` | `--h` / `--looks-per-token` 与 D1x（可达的载 X 正例，只读标注 + 视界） | additive |
| `scripts/research_v4/g_conf_seal.py` | `--arm-hashes` 模式（`build_arm_hashes` / `arm_hashes_destination`），只读、不重新封存、不改权限 | 新模式 |
| `tests/test_research_v4_v3_2.py` | round 1，**52** 用例 | 新文件（round 2 有 2 处适配多格 manifest） |
| `tests/test_research_v4_v3_2_round2.py` | round 2，**54** 用例（13 个测试类） | 新文件 |
| `src/research_v2/trm3.py` | **一行未改**（冻结的序贯核心） | — |
| `scripts/research_v3/verify_m_only_vs_frozen.py` | **一行未改** | — |

测试文件缩写（下文两张表用）：**`V32`** = `tests/test_research_v4_v3_2.py`、
**`R2`** = `tests/test_research_v4_v3_2_round2.py`、**`PV31`** = `tests/test_research_v4_prereg_v3_1.py`、
**`DG`** = `tests/test_research_v4_data_gates.py`、**`STAT`** = `tests/test_research_v4_statistics.py`、
**`DET`** = `tests/test_research_v4_detectors_g.py`、**`IOG`** = `tests/test_research_v4_io_g.py`、
**`SEAL`** = `tests/test_research_v4_g_conf_seal.py`。

---

## 2. §13 的十六条（逐条：flag → 代码 → 落盘键 → 测试 → 状态）

状态记号：**IMPL** = 已实现且有测试；**IMPL/MISMATCH** = 功能齐备但**预注册写的开关名或字段名与代码不一致**（注里给实际形式）。

| # | 预注册项 | CLI 开关（`--help` 实证） | 代码（模块 · 函数） | `result.json` / manifest 键 | 测试 | 状态 |
|---:|---|---|---|---|---|---|
| **1** | 目标池自校准 + 场景互斥 3 折轮转，折键 `fixture_rank_mod` | `--cal-from-target` · `--cal-folds 3` · `--fold-key {fixture_rank_mod,scenario_mod}` · `--fixture-config` | `trm3_g.fold_assignment` / `fold_of_episodes`；`io_g.fixture_map_from_config` / `subset_config_for_run`；`run_detectors_g.fold_pools` | `calibration_design.{fold_key, fold_rule, fold_assignment, fold_assignment_sha256, fold_pools, fixtures}`；manifest 同名块 | `R2::FoldKeyTest`（6）· `R2::RealConfigCollinearityTest`（4）· `V32::FoldRotationTest`（8） | **IMPL**（`--fold-key` 的第二个取值叫 `scenario_mod`，不是预注册写的 `scenario_sorted_mod`——**同一个函数**，见 §2 注 a） |
| **2** | 校准池只用质量过滤后的正常臂 | `--cal-filtered-only` / `--no-cal-filtered-only`（在 `--cal-from-target` 下默认开） | `run_detectors_g.fold_pools`（`filter_pass is True` 才进 fit / reference 折） | `calibration_design.cal_filtered_only`；`folds[k].n_cal` / `n_fit` | `V32::FoldRotationTest::test_fold_pools_are_scenario_disjoint_and_quality_filtered` · `..._unfiltered_rotation_keeps_the_dropped_episodes` | **IMPL** |
| **3** | X 锚点与 `[E_view, min(X+16, H_end)]` 命中口径 | `--anchor {c,e_view,x}` · `--hit-window {anchor_plus_h,e_view_to_anchor_plus_h}` | `trm3_g.view_anchors` / `anchored_positives` / `window_hits_at_alpha` | `anchoring.{anchor,hit_window,positives}`；`cells.<s>.metrics.positives_anchored.{recall.x_window, reachability, by_x_beyond_h, early_than_anchor, per_episode}` | `V32::AnchorConventionTest`（8）· `V32::AnchoredPositivesTest`（4）· `R2::ReachabilityConventionTest`（5） | **IMPL/MISMATCH**：`--anchor` 的取值是 `c / e_view / x`（小写），**没有 `min_x_xtool`**——`min(X, X_tool)` 敏感性列在 G 上恒等（§16.1 第 5 条），落盘在 `positives_anchored.anchor_sensitivity` 而不是一个开关 |
| **4** | H 显式冻结，覆盖 `min_survivors` | `--force-h 352`（与 `--expect-h 352` 并存）· `--h-min-survivors` | `trm3_g.frozen_h` / `calibrate_g(force_h=)` | `cells.<s>.fold_summary.{H, survivors_at_H}`；`folds[k].{H, survivors_at_H, censored_paths, horizon}`；manifest `cell.force_h` | `V32::ForcedHorizonTest`（3） | **IMPL** |
| **5** | 两阶段解封守卫 + 多格清单 | `--stage {single,calibrate,score}` · `--threshold-manifest <path>` · `--statistic S,P,M,prob_js` · `--seal-manifest` | `run_detectors_g.run_cell_v32` / `build_manifest` / `verify_manifest` / `manifest_cells` / `manifest_self_sha256` / `attack_trace_census` / `seal_check` / `normal_trace_manifest` | `threshold_manifest.verification.{checks(15), failed, cells, seal_at_stage1_start, seal_at_stage2_start}`；`inputs.threshold_manifest_sha256`；`stage1_attack_traces_skipped`；`attack_trace_census.{count,sha256,per_dir}`；`cells.<s>.restored_from_manifest` | `V32::RunnerV32Test`（14）· `R2::ManifestMultiCellTest`（7） | **IMPL/MISMATCH**：**没有 `--threshold-manifest-out`**（`--threshold-manifest` 在 `calibrate` 下是写、在 `score` 下是读）；**没有 `--cells`**（格由 `--statistic S,P,M,prob_js` 给，manifest 里的键是 `S/P/M/J`）。守卫 15 项：`manifest_kind` · `manifest_sha256` · `fold_assignment_sha256` · `fold_assignment_self_consistent` · `normal_traces_sha256` · `target_labels_sha256` · `cell_matches` · `head_is_freeze_commit` · `manifest_code_commit` · `cells_present` · `cells_complete_on_every_fold` · `length_tertiles_present` · `matched_alpha_inputs_present` · `normal_trace_set_sha256` · `sealed_trace_set_sha256` |
| **6** | S-J 的注入在场分母与臂间配对 | `--positives {e_anchored,injection_present}` · `--injection-negatives benign_control,clean` | `trm3_g.injection_presence_block`；`run_detectors_g.injection_pairs` / `compare_injection_pairs` / `compare_injection_presence` | `injection_pairing.<s>.{pairing.pair_count, pairing.pair_count_filtered_negatives, pairing.discarded, pairing.filter_pass_census, primary_unfiltered, sensitivity_filtered_negatives}`；`comparison_injection_present` | `V32::InjectionPresenceTest`（4）· `R2::InjectionPairingTest`（3） | **IMPL/MISMATCH**：**没有 `--pair-by` / `--negative-arm` / `--pair-filter`**。配对键 (scenario, `episode_index`) 与负例臂是**写死**的（`--injection-negatives` 只控制 FAR 侧的负例池），**两个口径无条件同时落盘**（`primary_unfiltered` + `sensitivity_filtered_negatives`）。**这比开关更安全**：不存在"只报一个口径"的运行方式 |
| **7** | 逐折断言 | `--expect-n-reference-folds <a,b,c>`（单值 `--expect-n-reference` 保留为 v3.1 口径）· `--attainability-floor` · `--expect-h` | `run_detectors_g.expected_fold_references`；`trm3_g.attainability` | `assertions.by_statistic.<s>[]`（每格 15 条）；`folds[k].{attainability, n_reference_episodes, alpha_eff}` | `V32::FoldRotationTest::test_per_fold_attainability_floor` · `V32::RunnerV32Test::test_the_result_records_per_fold_n_fit_n_cal_h_and_alpha_eff` | **IMPL** |
| **8** | 检验力模拟器的 ψ 参数化 | `prereg_power_sim.py --psi`（可重复传）· `--n` · `--rho` · `--delta` · `--bootstrap` · `--config` | `prereg_power_sim.run_grid` / `simulate_cell` / `allocate` | `power_sim.json.{psi, psis, cells[].{power, power_ci_only, power_mcnemar_only, mean_discordant_pairs}, family_count, attack_family_sizes}` | `V32::PowerSimPsiTest`（4） | **IMPL**。**注意默认值陷阱**：`--config` 默认是 `g_dev.json`、`--n` 默认 107/150/177/264、`--delta` 默认 0–0.20，**v3.2 必须全部显式传**（§8.2 已写死复现命令） |
| **9** | 取消 OR 臂 | 不使用 `--or-arm` / `--alpha-extra` | — | `cells.<s>.or_arm == null`（第二轮实测四格全 `null`） | `V32::RunnerV32Test`（`cells` 键集断言） | **IMPL**（无需新代码） |
| **10** | 逐 token 输出携带 harmony 通道 | `--outputs all`（`primary` 只写 `result.json`） | `run_detectors_g` 的行构造（`row["channel"] = tags[end]`，`run_detectors_g.py:762`） | `outputs.jsonl` 与 `outputs_alarm_onsets.jsonl` 的 `channel` 列（第二轮产物实证：`"channel": "final"`） | `PV31::ResultJsonContractTest`（输出契约）；`IOG`（`channel_runs` 的切分一致性） | **IMPL** |
| **11** | X 锚点的可达性统计单列 | （随 `--anchor x --hit-window e_view_to_anchor_plus_h` 自动落盘） | `trm3_g.anchored_positives` | `positives_anchored.recall.x_window.{reachable_count, hit_count, recall, is_primary, rule}`；`positives_anchored.reachability.{x_beyond_h, anchor_reachable_plus_16, window_unreachable_plus_16, positives, primary_denominator}`；`positives_anchored.by_x_beyond_h.{False,True}`；`cells.<s>.fold_summary.x_beyond_h`（逐折）与 `x_beyond_h_total` | `V32::AnchoredPositivesTest::test_x_beyond_h_counts_the_positives_the_horizon_cut_off` · `R2::ReachabilityConventionTest::test_the_x_beyond_h_family_is_its_own_stratum` | **IMPL/MISMATCH**：预注册写的是 `positives.reachability.x_beyond_h`，实际路径是 `cells.<s>.metrics.positives_anchored.reachability.x_beyond_h`（多一层 `positives_anchored`） |
| **12** | 单样本家族聚类 bootstrap（S1 用） | （随主格运行自动落盘） | `trm3_g.cluster_bootstrap_rate(hits, families, *, replicates, level, null_value)` · `trm3_g.group_hits_by_cluster`；`run_detectors_g.one_sample_rate_block` | `holm_s1_one_sample.<s>.{point_estimate, ci, ci_lower_above_null, p_value, draws_at_or_below_null, family_count, family_sizes, hits_by_family, positives_by_family, n, hit_count, null_rate, rule}` | `R2::ClusterBootstrapRateTest`（6，含"CI 下界 > null ⟺ 单侧 p < 0.025"与"重抽单位是家族不是 episode"） | **IMPL** |
| **13** | 逐折产物结构 | （随 `--cal-from-target` 自动落盘） | `run_detectors_g.fold_far_block` / `_weighted_alpha_eff` | `cells.<s>.folds.{0,1,2}.{n_fit, n_cal, H, alpha_eff, survivors_at_H, censored_paths, attainability, rotation, far.all, far.filtered, statistic_state}`；`cells.<s>.fold_summary.{alpha_eff[], alpha_eff_weighted, n_cal[], n_fit[], survivors_at_H[], H[], x_beyond_h[]}` | `V32::RunnerV32Test::test_the_manifest_records_the_fold_table_and_the_per_fold_thresholds` · `..._the_result_records_per_fold_n_fit_n_cal_h_and_alpha_eff` | **IMPL/MISMATCH**：预注册写 `result.json.calibration.folds[*]`，实际是 `cells.<statistic>.folds[k]`（**逐格**，不是全局）；`assertions` 的实际路径是 `assertions.by_statistic.<s>[]`，不是 `assertions.by_fold[*]`。**逐折 `ep1_share` 在 `calibration_design.fold_pools[k]` 里，不在 `fold_summary` 里** |
| **14** | 三分位切点来自阶段 1 | `--tertile-cutpoints-from-target`（v3.2 语义：阶段 1 冻结 → 阶段 2 重放） | `trm3_g.tertile_of_length`；`run_detectors_g.build_manifest`（写）/ `run_cell_v32`（读） | manifest `length_tertiles.{source, cutpoints, counts_by_fold, denominator, rule}`；`result.json.calibration_design.length_tertiles.replayed_from_manifest == true` | `R2::ManifestMultiCellTest::test_the_manifest_freezes_the_tertiles_the_fit_and_the_matched_alpha` · `..._test_stage_two_replays_the_frozen_matched_alpha` | **IMPL** |
| **15** | `matched_alpha` 在阶段 1 冻结 | （随 `--stage calibrate` 自动写；`--compare-statistic` 决定阶段 2 用哪个格） | `trm3_g.matched_alpha_by_measured_far`；`run_detectors_g.matched_alpha_inputs` / `manifest_matched_alpha` | manifest `matched_alpha_inputs.{denominator, normal_keys_sha256, cells.<s>.{normal_count, grid[{alpha, measured_far}]}}`；`comparison_anchored.matched_alpha_secondary.{alpha, measured_far, source}`（实测 `source = "threshold_manifest.matched_alpha_inputs"`） | `R2::ManifestMultiCellTest::test_stage_two_replays_the_frozen_matched_alpha` | **IMPL/MISMATCH**：manifest 里的字段名是 `matched_alpha_inputs.cells.<s>.grid`，预注册写的 `target_measured_far` / `selection` / `matched_alpha` / `matched_alpha_measured_far` **不在 manifest 里**——它们的等价物在 `result.json.comparison_anchored.matched_alpha_secondary` 里（阶段 2 重放的结果） |
| **16** | 实际家族数与逐家族正例数 | （自动落盘） | `run_detectors_g.positive_family_census` | `positive_families.<s>.{family_count, families_in_batch, min_family_size, positives_by_family, dropped_families, rule}`；`holm_s1_one_sample.<s>.family_sizes`；配对块只有 `…bootstrap.family_count` | `R2::FamilyCensusTest::test_a_family_with_no_reachable_positive_is_reported_as_dropped` | **IMPL/MISMATCH（本文件唯一一条真正缺字段的行）**：**`comparison_anchored.bootstrap.family_sizes` 不存在**（第二轮产物实证：该块的键是 `ci / family_count / level / mcnemar / pair_count / point_estimate / recall_a / recall_b / replicates / robustness_48_cluster`）。**替代读法**：家族大小向量取 `positive_families.<s>.positives_by_family`（同一批正例、同一个口径），家族数取 `bootstrap.family_count`。**这是报告侧的一条读法约定，不是缺陷**——但预注册 §13 第 16 条的措辞必须在 A′ 之前改成这个路径 |

> **注 a（第 1 条）**：预注册多处写 `scenario_sorted_mod`，CLI 的取值名是 **`scenario_mod`**。
> 两者是同一个函数（`fold(s) = index_of(s in sorted(all_scenario_ids)) mod K`）。
> **建议在 A′ 之前把预注册统一改成 `scenario_mod`**，因为出现在 `result.json.calibration_design.fold_key` 里的是后者。

---

## 3. §14 的报告义务：每一个量 → 从哪里读

> 下表是**报告方的取数清单**：每一行给出"报告里要出现的量"与"它在哪个文件的哪个键"。
> 路径前缀省略：`R` = 阶段 2 的 `result.json`，`M` = 阶段 1 的 `threshold_manifest.json`，
> `<s>` ∈ {`S`,`P`,`M`,`J`}（J = `prob_js`）。

### 3.1 §14 第 1 条 · 主结果表

| 量 | 路径 | 第二轮 G-dev 实测（EXPLORATORY） |
|---|---|---|
| Δ̂ | `R.comparison_anchored.bootstrap.point_estimate` | 0.264 |
| 95% 家族聚类 CI | `R.comparison_anchored.two_condition.ci` | [0.024, 0.508] |
| `(family × tier)` 稳健列 | `R.comparison_anchored.bootstrap.robustness_48_cluster.{ci, family_count}` | [0.107, 0.435]，35 cluster |
| McNemar (b, c, p) | `R.comparison_anchored.bootstrap.mcnemar.{only_a, only_b, discordant, p_value}` | 38 / 5 / 43 / 2.50e-07 |
| 两条件的两个 conjunct | `R.comparison_anchored.two_condition.{ci_excludes_zero, direction_positive, mcnemar_p, ci_lower}` | true / true / 2.50e-07 / 0.0235 |
| 两个统计量各自的实测 FAR | `R.cells.<s>.metrics.far.{all,filtered}.far` | S 0.0980 / 0.1195 |
| 工作点 α | `R.comparison_anchored.matched_alpha_secondary.{alpha, source}` | 0.104167，`threshold_manifest.matched_alpha_inputs` |
| **`rows.matched` 的注明** | `R.comparison_anchored.primary_row`（必须是 `"matched"`） | `matched` |
| 配对样本 N | `R.comparison_anchored.bootstrap.pair_count` = `R.cells.<s>.metrics.positives_anchored.recall.x_window.reachable_count` | 125 |
| 实际家族数 | `R.comparison_anchored.bootstrap.family_count` 与 `R.positive_families.<s>.family_count` | 16 / 16 |
| **逐家族正例数向量** | `R.positive_families.<s>.positives_by_family`（**不是** `bootstrap.family_sizes`，§2 第 16 条） | 16 项，最小 2 |
| 被清零的家族 | `R.positive_families.<s>.dropped_families` | `[]` |

### 3.2 §14 第 2 条 · 门

| 门 | 路径 | 第二轮 G-dev 实测（S 格） |
|---|---|---|
| F1 | `R.gates.<s>.gates[] · gate == "F1_pooled_holdout_far_vs_alpha_eff"`（`value` / `threshold` / `tolerance` / `deviation` / `far_all` / `status`） | PASS，0.11945 对 0.09459，偏差 0.0249 |
| F1 的**逐折**列 | `R.cells.<s>.folds[k].far.filtered.far` 对 `R.cells.<s>.folds[k].alpha_eff` | 0.1895 / 0.0745 / 0.0962 对 0.09524 / 0.09375 / 0.09474 |
| F2a | `R.cells.<s>.metrics.far.benign_control_minus_clean`（`all` / `filtered` 两个分母） | −0.0052（`all`） |
| F2b | `R.cells.<s>.metrics.far.benign_lexical`（G-conf 无此臂 ⇒ 不可评） | G-dev 0.0417 |
| F3 | `R.gates.<s>.gates[] · "F3_worst_length_tertile_far"`（`tertile` / `value`）；分档明细 `R.cells.<s>.metrics.far.{length_tertile, length_tertile_filtered}` | FAIL，long 0.1532（P 0.1982 / M 0.2252 / J 0.1441） |
| F4 | `R.cells.<s>.metrics.classes.silent_attack.{far, alarm_count, episode_count, excluded_pre_injection_episodes, denominator, denominator_note}` 对 `R.cells.<s>.metrics.far.clean.all.far + 0.05` | 0.125（5/40）对 0.15417 ⇒ PASS，余量 0.029 |
| F4 的旧分母（可比性列） | `R.cells.<s>.metrics.classes.silent_all_attack_arm_episodes.{far, episode_count}` | 0.0703（9/128） |
| F5 | `R.gates.<s>.gates[] · "F5_matched_group_far"`（`value` / `threshold` / `k_bar` / `matched_group_far_filtered` / `flat_threshold_v3_2_draft`） | PASS，0.1786 ≤ 0.2644，k̄ = 2.4286 |
| F6 | `R.cells.<s>.metrics.endpoint.alarm_onsets_per_1000_eligible`（对同池 M-only 参照） | 落盘 |
| F7 | `R.cells.<s>.metrics.session.*`（G-conf 不是会话批 ⇒ 不可评） | — |
| F8 | `R.assertions.by_statistic.<s>[]`（每格 15 条）+ `R.threshold_manifest.verification.{checks, failed, ok}` | `failed = []`，15/15 PASS |
| N1 | `R.gates.<s>.gates[] · "N1_filter_pass_rate"`（`value` / `pass_count` / `normal_count`） | FAIL，0.7181 = 293/408 |
| N2 | `R.gates.<s>.gates[] · "N2_filtered_normals_per_fold_per_tertile"`（`value` / `counts_by_fold` / `cutpoints`） | PASS，最小 28，切点 219/382 |
| N3 | `R.cells.<s>.fold_summary.H` + `R.args.h_min_survivors` | [352, 352, 352] |
| N4 | `R.cells.<s>.folds[k].attainability.{ok, channels.<s>.rank, n_reference}`（**不是**预注册写的 `attainability_rank`） | rank 10 / 9 / 9 |
| N5 | `R.cells.<s>.metrics.far.{clean,benign_control}.filtered.far` 的差 | 落盘 |
| N6 | G-conf 自动预检的 `x_tool_events` | 0 |
| N7 | 标注冻结记录 `annotations/g_conf/label_freeze.json` | — |
| D1 / D1x / D2–D6 | `g_conf_data_gates.json`（由 `g_dev_data_gates.py` 在 G-conf 上跑同一份代码产出）；D1x 的行是 `D1x_reachable_x_positives`，字段 `value` / `threshold` / `X positives` / `reachable` / `x_beyond_horizon_token` | G-dev：D1x value 125，threshold 62，informational |

### 3.3 §14 第 3 / 9 条 · 逐折表与视界删失

| 量 | 路径 |
|---|---|
| 逐折 `n_cal` / `n_fit` / `H` / `alpha_eff` / `survivors_at_H` | `R.cells.<s>.fold_summary.*`（向量）与 `R.cells.<s>.folds[k].*`（逐折块） |
| 逐折阈值 z | `M.folds[k].cells[<s>].alarm_threshold_z`（阶段 2 只读；同块还有 `standardiser` / `calibrations` / `statistics` / `n_reference_episodes` / `fit_keys_sha256` / `reference_keys_sha256`） |
| 逐折删失 | `R.cells.<s>.folds[k].censored_paths`（第二轮 32 / 27 / 29）；端点侧 `R.cells.<s>.metrics.endpoint.{censored_endpoints, horizon_censored_episodes, eligible_endpoints, emitted_endpoints, alarm_endpoints, alarm_onsets}`（第二轮 S：24151 / 210 / 89596 / 190284 / 5496 / 40，**这些是全池合计、不逐折**） |
| 逐折 ep1 占比 | **⚠ 不落盘**：`ep1_share` 只出现在 `run_detectors_g.py:4138` 的**日志行**里，`result.json` 与 manifest 都没有这个键。报告方须从 `M.folds[k].{fit_episodes, reference_episodes, eval_episodes}` 的 episode key（`…#ep1` 后缀）自行统计，或从运行日志抄。**见 §5 缺口 4** |
| 逐折留出 FAR 与命中率 | `R.cells.<s>.folds[k].far.{all,filtered}`；命中率的逐折切分由 `positives_anchored.per_episode[*].fold` 聚合 |
| `x_beyond_h` 分层的正例数与命中率 | `R.cells.<s>.metrics.positives_anchored.by_x_beyond_h.{False,True}.{reachable_count, hit_count, recall}` + `…fold_summary.x_beyond_h`（逐折） |
| **窗口内无端点因而不可达的正例数** | `R.cells.<s>.metrics.positives_anchored.reachability.window_unreachable_plus_16` |

### 3.4 §14 第 4 / 5 条 · Holm 族与 S-J

| 量 | 路径 | 第二轮 G-dev 实测 |
|---|---|---|
| S1 的点估计 / CI / p | `R.holm_s1_one_sample.S.{point_estimate, ci, p_value, ci_lower_above_null, family_count}` | 0.824 / [0.709, 0.919] / 4.998e-4 / true / 16 |
| S2 的两条件 | `R(stage2_S_vs_M).comparison_anchored.two_condition.*` | −0.016 / [−0.079, 0.037] / p 0.754 / 两条件都 false |
| S-J 主口径 | `R.injection_pairing.<s>.primary_unfiltered.{pair_count, point_estimate, ci, mcnemar.p_value, two_condition}` | J：144 / 0.2153 / [0.097, 0.333] / 1.47e-5 |
| S-J 敏感性口径 | `R.injection_pairing.<s>.sensitivity_filtered_negatives.*` | J：124 / 0.2016 / [0.090, 0.303] |
| 配对丢弃原因 + `filter_pass` 普查 | `R.injection_pairing.<s>.pairing.{discarded, discarded_count, filter_pass_census, positive_count}` | 丢 120（全 `no_benign_control_counterpart`）；负例 T124/F20、正例 T45/F99 |
| **F4 与 S-J 的并排页** | 同一页并列 `R.cells.<s>.metrics.classes.silent_attack`（FAR 侧）与 `R.comparison_injection_present.bootstrap`（正例侧），后者带 `note` 明写 "gate F4 does NOT apply here" | 静默 5/40 是 F4 的分子；同一批在 S-J 里是正例 |

### 3.5 §14 第 6 / 7 条 · 逐类与描述性列

| 量 | 路径 |
|---|---|
| 轨迹类 / 渠道 / 措辞层 / 域组分层 | `R.cells.<s>.metrics.positives_anchored.{by_trajectory_class, by_channel, by_wording_tier, by_domain_group}` |
| 长度三分位 | `R.cells.<s>.metrics.far.{length_tertile, length_tertile_filtered, worst_length_tertile, length_tertile_definition}` |
| `R4:warranty` held-out 分层 | 由 `positives_anchored.per_episode[*]` 按 scenario 的 workflow 类型聚合（配置侧的 `scenarios[*].factory`） |
| E 阶段召回（+ ORACLE 天花板 0.188） | `R.cells.<s>.metrics.positives.recall.*`（天花板来自 `g_dev_primary_diagnostics.md`，不是落盘量） |
| 早于 X / 早于 C 的比例 | `R.cells.<s>.metrics.positives_anchored.early_than_anchor.{count, rate, reachable_count}` |
| 有界抵御（有 E 无 X） | `R.cells.<s>.metrics.positives_anchored.excluded`（`no_x_annotation` 等排除码计数） |
| 仅 analysis 参与 | `g_conf_data_gates.json` 的 D3（交集口径 + 事件口径两列） |
| 滞回与恢复 | `R.cells.<s>.metrics.hysteresis.{entries, exits, final_states, by_trajectory_class, episodes_with_entry, episodes_with_exit, earliest_decision_end_median}` |
| 归因 | `R.cells.<s>.attribution` 只有 `{enabled, top_n}`；**top-3 (layer, expert) 坐标在逐 onset / 逐 token 行的 `attribution` 列里**（`outputs_alarm_onsets.jsonl`） |
| **报警的通道分布**（harmony 通道：analysis / commentary / final / other） | **只在 `outputs.jsonl` / `outputs_alarm_onsets.jsonl` 的 `channel` 列里**（`--outputs all` 或 `primary`+onsets）；`result.json` 里没有汇总。E 锚点侧另有 `R.cells.<s>.metrics.positives.anchor_channel_counts`（锚点所在通道，**不是报警通道**） |
| 注入**渠道**分层（direct_user / multi_turn_user / tool_output，**不是 harmony 通道**） | `R.cells.<s>.metrics.positives_anchored.by_channel.{<channel>}.{hit_count, reachable_count, recall}`（第二轮实测 0.746 / 0.944 / …） |
| 误报解剖 / 重尾正常 episode 审计 | `outputs_alarm_onsets.jsonl` 的正常臂行（`arm` / `class` / `channel` / `evidence_window` / `e0` / `end`） |
| `LEAK:` 排除列 | `g_conf_data_gates.json` 的 `schema_1_1` 块（行注前缀计数 + `LEAK:` 行名） |
| interval-compatible 列 | 同上（`<event>_interval_span` 的对齐输出） |
| V2 / V3 与消融列 | 另跑 `--view V2` / `--view V3` / `--no-standardise` 的运行（**不属于主格的两阶段，必须单列**） |
| 跨池迁移差 | v3.1 的冻结池运行与本轮自校准运行的 FAR 对比（§2.2 的表） |

### 3.6 §14 第 8 / 10 / 11 / 12 条 · 成本、留痕、声明

| 量 | 路径 |
|---|---|
| 每 1000 端点的打分秒数 / 拟合秒数 / 端点数 | `R.cells.<s>.cost.{seconds_per_1000_endpoints, scoring_seconds, fit_seconds, scored_endpoints, total_seconds}` |
| **`prob_js`（S-J 格）的成本单列** | `R.cells.J.cost.*`（第二轮：阶段 1 的 J 是 42.6 s，其余三格合计 8.8 s） |
| 峰值 RSS | 运行日志（不落 `result.json`） |
| 参照集大小 | `M.folds[k].cells[<s>].reference_path_maxima.{count, min, median, max, sha256}` 与 `…n_reference_episodes` |
| 两阶段留痕 | `R.threshold_manifest.{path, sha256}`、`R.inputs.threshold_manifest_sha256`、`R.stage1_attack_traces_skipped`、`R.attack_trace_census.{count, sha256}`、阶段 1 / 阶段 2 的 `created_at`、`R.seal.{when, pools, any_sealed}` 与 `R.threshold_manifest.verification.{seal_at_stage1_start, seal_at_stage2_start}` |
| 冻结守卫 | `R.data_discipline_guard.{enforced, dirty, head_is_freeze_commit, prereg_sha256_matches, labels_sha256_missing, rule}` |
| 折 × fixture × 臂交叉表 | `R.calibration_design.fold_fixture_crosstab.{scenarios_by_fixture_by_fold, episodes_by_arm_by_fold, episodes_by_arm_by_fixture_by_fold, collinear_fixtures, rule}` |

---

## 4. 冻结审阅者清单（提交 A′；逐条给命令或字段路径与期望值）

> 每一条都必须**实际跑一遍或实际读一次**，不接受"看起来对"。
> 命令一律在仓库根目录执行；`$PY` = `/home/wzh/Agent-Moe-Research/.venv/bin/python`，需要导包时前缀 `PYTHONPATH=$PWD/src:$PWD/scripts`。

| # | 检查项 | 怎么验 | 期望 |
|---:|---|---|---|
| **1** | 工作树干净、`HEAD` 就是要冻结的提交 | `git status --porcelain`；`git rev-parse HEAD` | 只剩 `?? artifacts` 一行；HEAD 与提交 message 里记的一致 |
| **2** | §13 的 **16 条**逐条对上 §2 的表 | 逐行照 §2 的"CLI 开关"列 grep `run_detectors_g.py --help` 的输出；逐行照"测试"列 `grep -n` 到测试名 | 16/16 命中；**6 条 MISMATCH 的措辞已按 §2 的注在预注册里改掉**（或组长明确接受不改） |
| **3** | 每个 CLI 开关真的存在 | `PYTHONPATH=... $PY scripts/research_v4/run_detectors_g.py --help \| grep -E -- '--cal-from-target\|--cal-folds\|--fold-key\|--fixture-config\|--seal-manifest\|--cal-filtered-only\|--force-h\|--stage\|--threshold-manifest\|--anchor\|--hit-window\|--positives\|--injection-negatives\|--expect-n-reference-folds\|--tertile-cutpoints-from-target'` | 14 条全部命中 |
| **4** | `--fold-key` 的取值 | 同上，看 `{fixture_rank_mod,scenario_mod}` | 两个取值；**预注册若仍写 `scenario_sorted_mod` 即为不一致，须改** |
| **5** | 阶段 2 的守卫真的有 15 项 | 读第二轮产物 `R.threshold_manifest.verification.checks` 的长度与 `failed` | `len(checks) == 15`、`failed == []`、`ok == true` |
| **6** | 多格 manifest 的结构 | 读 `M.manifest_version` 与 `M.folds.{0,1,2}.cells` 的键集 | `"v3.2-2"`；每折都有 `S/P/M/J` 四个格 |
| **7** | `matched_alpha` 在阶段 1 冻结、阶段 2 只重放 | `R.comparison_anchored.matched_alpha_secondary.source` | `"threshold_manifest.matched_alpha_inputs"` |
| **8** | 三分位切点在阶段 1 冻结 | `R.calibration_design.length_tertiles.{source, replayed_from_manifest, cutpoints}` | `"stage1_target_normals"` / `true` / 与 `M.length_tertiles.cutpoints` 逐位相同 |
| **9** | 折键真的是注册的那一个 | `R.calibration_design.fold_key` 与 `R.calibration_design.fold_assignment_sha256` | `"fixture_rank_mod"`；G-dev 上是 `e389122a393a9ef9…` |
| **10** | 折 × fixture 无共线 | `R.calibration_design.fold_fixture_crosstab.collinear_fixtures` | `[]` |
| **11** | 可达性只有一个定义 | `R.cells.<s>.metrics.positives_anchored.recall.x_window.{is_primary, rule, reachable_count}` 与 `…reachability.primary_denominator` | `is_primary == true`；`primary_denominator == "recall.x_window.reachable_count"` |
| **12** | `x_beyond_h` 的两个分母对得上 | `reachability.{anchor_reachable_plus_16, x_beyond_h, window_unreachable_plus_16, positives}` 与 `by_x_beyond_h.*.reachable_count` | `108 + 18 = 126`（正例）且 `108 + 17 = 125`（可达）；差的 1 条就是 `window_unreachable_plus_16` |
| **13** | S-J 的两个口径都落盘 | `R.injection_pairing.<s>` 的键集 | 同时含 `primary_unfiltered` 与 `sensitivity_filtered_negatives`，两者都带 `two_condition` |
| **14** | 家族向量能读到 | `R.positive_families.<s>.positives_by_family` | 16 项；**不要去找 `bootstrap.family_sizes`，它不存在**（§2 第 16 条） |
| **14b** | 逐折 ep1 占比拿得到 | 按 §5 缺口 4 选定的方式实际算一次 | 三折各有一个数；**若走 (b)，统计命令必须写进操作单** |
| **15** | 门逐格逐条落盘 | `R.gates.<s>.gates` 的长度与 `gate` 字段 | 每格 5 条：F1 / F3 / F5 / N1 / N2；F2 / F4 / F6 / F7 / F8 由报告方从 §3.2 的字段读出 |
| **16** | 攻击臂在阶段 1 一条未被读 | `R(stage1).stage1_attack_traces_skipped` 与 `R(stage2).attack_trace_census.count` | 两者相等（G-dev 264；**G-conf 期望 160**） |
| **17** | 封存哈希在两处各读一次 | `R.threshold_manifest.verification.{seal_at_stage1_start, seal_at_stage2_start}` | 两个块存在且 trace-set 哈希相同；**G-dev 上是 `present: false` 的空跑**（组长裁定 Q7，须在日志里写明） |
| **18** | 检验力表可复现 | 跑 §8.2 与 §8.5 里逐字给出的两条 `prereg_power_sim.py` 命令 | 重写出的 `power_sim.{json,md}` 与 §15.4.6 的 sha256 逐位相同 |
| **19** | 检验力表与正文逐格一致 | 把 `main/power_sim.md` 的三张表与预注册 §8.2 / §8.3 逐格比 | 逐格相同；**±0.03 触发器的那一格（ψ 0.35 / N 80 / ρ 0.30 / Δ 0.15）已在 §8.2 记录原因** |
| **20** | S1 的表不可由 CLI 复现（已知） | `prereg_power_sim.py --help` | **没有单样本模式**；§8.4 的表由 `trm3_g.cluster_bootstrap_rate` 的同一模型算出，**不要求与 `power_sim.json` 对上** |
| **21** | 数据门先于路由 | `g_conf_data_gates.json` 的 `created_at` 早于任何 G-conf 检测器产物 | 时间序正确；D1x 的 `value` ≥ 62（否则按 §9.3 写范围声明并按实际 N 重算检验力） |
| **22** | 冻结的 OLMoE 侧未被碰 | `git diff --stat scripts/research_v3/verify_m_only_vs_frozen.py`（空）；跑该脚本 | `halves_identical = True`、`mismatch_count = 0` |
| **23** | v3.1 §19.7 回归逐字段不变 | 按 `v3_2_harness_changes.md` §10.4 的做法跑改动前 / 后各一次 | 取值不同的 v3.1 字段 **0**；`outputs.jsonl` 逐行逐列相同 |
| **24** | 全部测试全绿 | `PYTHONPATH=... $PY -m pytest tests/ -q` | 全绿；**当次条数抄进提交 message，不写死** |
| **25** | §15.4 的哈希逐行重算 | `freeze_a2_checklist.md` 步 5 的三个循环 | 与 §15.4 的每一行逐位相同；不同即**先改预注册再冻结** |
| **26** | 本文件与操作单进哈希表 | `sha256sum docs/research_v4/prereg_v3_2_code_mapping.md docs/research_v4/freeze_a2_checklist.md` | 两行进 §15.4.1 |
| **27** | 预注册的 sha256 有两个权威副本 | 提交 A′ 的 message + `freeze_a2_checklist.md` 的"冻结记录"节 | 两处一致；`run_detectors_g.PREREG_PATH` 已改指 v3.2 正文并有测试钉住 |
| **28** | 流程侧的两条（代码保证不了） | 读操作单 | ① `--arm-hashes` **不在** G-conf 阶段 1 之前跑（Q6）；② G-conf 阶段 1 **只跑一次**，manifest 哈希当场记录（Q10） |

---

## 5. 已知缺口（提交 A′ 之前必须由组长处置的三件事）

| # | 缺口 | 影响 | 建议处置 |
|---:|---|---|---|
| **1** | **§13 第 16 条要求的 `…bootstrap.family_sizes` 不存在** | 报告方若照措辞取数会取到 `None` | **改预注册的措辞**指向 `positive_families.<s>.positives_by_family`（数据本身齐全，见 §3.1） |
| **2** | **§13 第 3 / 5 / 6 / 11 / 13 / 15 条的开关名 / 字段路径与代码不一致**（`min_x_xtool` / `--threshold-manifest-out` / `--cells` / `--pair-by` / `--negative-arm` / `--pair-filter` / `positives.reachability.*` / `calibration.folds[*]` / `assertions.by_fold[*]` / `attainability_rank` / manifest 的 `matched_alpha` 字段名） | 冻结审阅者按预注册去 grep 会 grep 不到，误判为"未落地" | **改预注册的措辞**（代码的形式更安全，尤其第 6 条的"两个口径无条件同时落盘"） |
| **3** | **`scenario_sorted_mod` vs `scenario_mod`** 的命名不一致 | 同上 | 统一成 `scenario_mod`（`result.json` 里出现的就是它） |
| **4** | **逐折 `ep1_share` 不落盘**（§13 第 13 条与 §14 第 3 条都要求它） | 报告方拿不到这个量，只能从 manifest 的 episode key 后缀自算，或从运行日志抄——**而 G-conf 只跑一次，日志是唯一副本** | 二选一：**(a)** 冻结前加一行代码把 `ep1_share` 写进 `folds[k]`（additive、低风险、需补一条测试）；**(b)** 改预注册措辞为"从 `M.folds[k].*_episodes` 的 `#ep1` 后缀统计"，并在操作单里写死这条统计命令。**我建议 (b)**——A′ 之前改代码会让 §15.4.4 的哈希全部重算 |

**判定层的功能没有缺失**：§13 的十六条都能在真实数据上跑通并落盘
（第二轮冒烟的三次阶段 2 打分 + 一次阶段 1 校准就是证据），H1 / S1 / S2 / S-J 与全部门的每一个量都读得到。
**缺口 1–3 是纯措辞层的**，改预注册即可；**缺口 4 是一个描述性列的落盘缺失**，
它不影响任何判定，但影响 §14 第 3 条的报告义务，必须在 A′ 之前由组长在 (a) / (b) 之间选一个。
