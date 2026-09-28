# 预注册 v3.3 → 代码映射（冻结提交 A″ 之前的核验，2026-09-08）

作者：Opus 5 研究工程 agent（受组长 Claude Fable 委派，**freeze-A″ 一致性工程师**）。
对象：`docs/research_v4/detector_prereg_v3_3.md`（**冻结 A″ 候选，rev4 定稿**；冻结前的草稿名
`detector_prereg_v3_3_draft.md` 自 A″ 起作废）的 **§12.2 注册命令**、**§13 的七条 harness 改动**、
**§14 的报告义务**、**§19 的开关与产物键**，以及 §15.2 第 4 / 6 / 12 / 13 条的冻结前置条件。

**本文件替代预注册 §19 的 stub**（§15.2 第 6 条要求）。形式沿用
`docs/research_v4/prereg_v3_2_code_mapping.md`（A′ 的同名文件）。

**核验基准**：HEAD **`42a1ca6`**，工作树在开工时 `git status --porcelain` 只有 `?? artifacts` 一行。
**核验方式**：本文件的每一行都是**当轮对着代码读出来的 `file:line`**，不是沿用上一轮的自述。
具体地——

* **CLI 开关**：不用 `--help` 的文本，而是**直接 import `run_detectors_g` 并把 §12.2 的五条注册命令
  逐字喂给 `run_detectors_g._args(argv)`**（占位符位置填合法的假值），既证明开关存在、
  又证明**拼写、取值域与组合**都能通过 argparse。结果见 §7。
* **代码符号**：`grep` 到定义行，并**读了函数体**（不是只看名字）。
* **落盘键**：用**真实产物**核对——
  `artifacts/agent_v2/dataset_g/v3_3_dev/round4_conformal/{stage2_hinf_nostrat_d1_z1,stage2_hinf_nkb_d1_z1}/result.json`
  （rev4 的两次 G-dev 阶段 2）与
  `artifacts/agent_v2/dataset_g/v3_3_dev/round3_pins/stage1_hinf_nostrat_d1/threshold_manifest.json`
  （带三个清单钉子的阶段 1）。
* **测试**：`pytest --collect-only` 的实际条数，逐类计数见 §1。

**范围声明（数据纪律）**：本轮**没有跑任何检测器**，没有改任何配置、任何标签、任何 `src/agent_v3/`。
**没有打开 `artifacts/agent_v2/dataset_g/g_conf2/` 下的任何 `trace.json` 或任何路由分片**——
该目录只读了 `SEALED.json` 与 `ARM_HASHES.json` 两个文件（并对它们做 `sha256sum`）。
`private/` 一个字节都没读。**唯一一次读 `trace.json` 的操作是 §6 的 G-conf 逐臂哈希复核**
（720 个 `trace.json` 的字节摘要，`io_g.trace_digest`，**元数据，无路由分片**），
它只产生哈希，不产生任何判定量。

**本轮改了代码吗**：**没有**。`src/research_v2/trm3_g.py` 与 `scripts/research_v4/run_detectors_g.py`
在本轮**一行未改**；发现的不一致**全部是文档侧**，已在预注册正文里改掉（逐条见 §5）。

---

## 0. 三句话

1. **§12.2 的五条注册命令逐字可解析**（两条阶段 1 + 三次阶段 2），
   **每一个开关都以注册的拼写存在于 argparse 里**，取值域也对得上（§7 给出逐条结果与 `file:line`）。
2. **判据读的每一个产物键都由命令实际走的代码路径产出**：
   `gates.<s>.F1_per_fold_conformal[*].in_band`、`gates.<s>.VAL1_a_exact[*].in_band`、
   `threshold_manifest.cell.{debounce, top_m, horizon_mode}` 与阶段 2 的三项 `cell_*` 检查、
   `folds[k].attainable_rank.{rank, n_reference}`、`folds[k].strata.per_stratum[*]`、
   `calibration_design.pinned`——**全部在真实产物里逐条命中**（§3 / §4）。
3. **发现并已改掉的文档侧不一致共 10 处**（§5），**没有发现任何代码缺陷**；
   最有实质后果的一处是 §3.4 / §13.1 第 13 条 / §16.3 里"工作树上任何位置都不存在 `ARM_HASHES.json`"——
   **G-conf-2 的 `ARM_HASHES.json` 存在、写在封存之前并被 `SEALED.json` 覆盖**，
   于是 `arm_hash_check` 在 G-conf-2 上会从"空跑"变成一次**真实的逐臂比对**（仍不是门）。

---

## 1. 文件与改动面（A″ 上的状态）

| 文件 | v3.3 相对 v3.2 的改动 | 性质 |
|---|---|---|
| `src/research_v2/trm3_g.py` | `RareConcentration`（Z1 统计量，`trm3_g.py:1372`）、`UNBOUNDED_H` / `is_unbounded_h` / `h_horizon_unbounded`（`:89` / `:1872` / `:1890`）、`debounce_p` / `apply_debounce`（`:1940` / `:1992`）、`stratum_label` / `stratify_indices`（`:1847` / `:1863`）、**rev4 的两条精确带** `conformal_far_band`（`:3863`）与 `pooled_stratum_far_band`（`:4043`）及常量 `CONFORMAL_BAND_LEVEL`（`:3860`）/ `POOLED_BAND_REPS`（`:4008`）/ `POOLED_BAND_SEED`（`:4009`） | 全部 additive；v3.2 的签名与默认行为保留。**`binomial_far_band` 已整块删除**（rev4） |
| `scripts/research_v4/run_detectors_g.py` | v3.3 开关组（`:1791`–`:1864`）、`force_h_arg`（`:437`）、`CELL_PIN_KEYS` / `v3_3_switches_used` / `cell_pins`（`:476` / `:479` / `:508`）、`n_kb_map_from_config` / `stratify_provenance` / `stratum_groups` / `_eval_weighted_alpha_eff`（`:2564` / `:2588` / `:2641` / `:2688`）、`CONFORMAL_POOLED_LABEL` / `conformal_fold_specs` / `val1_a_exact_block`（`:3612` / `:3619` / `:3681`）、`gate_block` 里的逐折保形行（`:3892`–`:4051`）、`verify_manifest` 的三项 `cell_*` 检查（`:4633`–`:4680`） | additive 且 flag-gated；不带任何 v3.3 开关时走的就是冻结的 v3.2 代码路径 |
| `src/research_v2/io_g.py` | **v3.3 未改** | — |
| `src/research_v2/trm3.py` | **一行未改**（冻结的序贯核心） | — |
| `scripts/research_v3/verify_m_only_vs_frozen.py` | **一行未改** | — |
| `scripts/research_v4/g_conf_seal.py` | `--arm-hashes` 的**目的地 bug 已修**（相对 `--root` 曾让逐臂哈希写进 `SEALED.json`；两侧都 `resolve()` 后比较），回归用例 `ArmHashDestinationTest`（3 条） | 见 `g_conf2_build_log.md` §7.1 |
| `scripts/research_v4/g_dev_data_gates.py` | v3.3 未改（`--h` / `--looks-per-token` 是 v3.2 就有的） | — |
| `tests/test_research_v4_v3_3.py` | **83 用例 / 10 个测试类**（rev1 是 49 / 6；rev3 R-C1 加 `ManifestPinsTest`，rev4 加 `ConformalBandTest` / `PooledStratumBandTest` / `F1ConformalRowsTest` 并删 `BinomialBandTest` / `F1BinomialRowsTest`） | 新文件 |

**`tests/test_research_v4_v3_3.py` 的逐类计数（`pytest --collect-only` 实测）**：

| 测试类 | `file:line` | 用例数 |
|---|---|---:|
| `Z1StatisticTest` | `tests/test_research_v4_v3_3.py:63` | 9 |
| `UnboundedHorizonTest` | `tests/test_research_v4_v3_3.py:177` | 7 |
| `DebounceTest` | `tests/test_research_v4_v3_3.py:338` | 7 |
| `StratifiedReferenceTest` | `tests/test_research_v4_v3_3.py:429` | 7 |
| `_V33Harness`（基类，非 TestCase） | `tests/test_research_v4_v3_3.py:513` | — |
| `RunnerV33Test` | `tests/test_research_v4_v3_3.py:564` | 13 |
| `ReadoutSwitchesTest` | `tests/test_research_v4_v3_3.py:775` | 6 |
| **`ConformalBandTest`**（rev4） | `tests/test_research_v4_v3_3.py:910` | 9 |
| **`PooledStratumBandTest`**（rev4） | `tests/test_research_v4_v3_3.py:1058` | 6 |
| **`ManifestPinsTest`**（rev3 R-C1） | `tests/test_research_v4_v3_3.py:1161` | 10 |
| **`F1ConformalRowsTest`**（rev4） | `tests/test_research_v4_v3_3.py:1332` | 9 |
| **合计** | | **83** |

> **裁定原文写的是 `ManifestPinTest`（单数），工程师落地的类名是 `ManifestPinsTest`（复数）**——
> 预注册 §0.2 / §15.2 第 12 条 / §15.3 第 18 条已按实际类名写，本文件同。

**注册测试集（A″ 上一次跑完，2026-09-08）**：

| 文件 | 用例 | 测试类 |
|---|---:|---:|
| `tests/test_research_v4_v3_3.py` | 83 | 10 |
| `tests/test_research_v4_v3_2_round2.py` | 74 | 18 |
| `tests/test_research_v4_v3_2.py` | 52 | 9 |
| `tests/test_research_v4_freeze_fixes.py` | 42 | 11 |
| `tests/test_research_v4_prereg_v3_1.py` | 57 | 14 |
| `tests/test_research_v4_data_gates.py` | 34 | 10 |
| `tests/test_research_v4_g_conf_seal.py` | 13 | 5 |
| **合计** | **355 passed, 15 subtests passed** | |

全套 `pytest tests/ -q`：**1432 passed, 143 subtests passed**（154.9 s）。

测试文件缩写（下文用）：**`V33`** = `tests/test_research_v4_v3_3.py`、
**`V32`** = `tests/test_research_v4_v3_2.py`、**`R2`** = `tests/test_research_v4_v3_2_round2.py`、
**`FF`** = `tests/test_research_v4_freeze_fixes.py`、**`PV31`** = `tests/test_research_v4_prereg_v3_1.py`、
**`DG`** = `tests/test_research_v4_data_gates.py`、**`SEAL`** = `tests/test_research_v4_g_conf_seal.py`。

---

## 2. §13 的七条 harness 改动（逐条：flag → 代码 → 落盘键 → 测试 → 状态）

状态记号：**IMPL** = 已实现、有测试、且预注册的措辞与代码一致；
**IMPL/DOC-FIXED** = 功能齐备，**预注册原措辞与代码不一致，本轮已改预注册**（§5 逐条）。

| # | 预注册项 | CLI 开关（`file:line`） | 代码（模块 · 函数 · `file:line`） | `result.json` / manifest 键 | 测试 | 状态 |
|---:|---|---|---|---|---|---|
| **1** | **R2：`Z1` 统计量（`top_m = 1`，注册即冻结）** | `--statistic`（`run_detectors_g.py:1443`，帮助文本已列 Z1）· `--top-m`（`:1805`，默认 1）· `--window-z1`（`:1798`） | `trm3_g.RareConcentration`（`trm3_g.py:1372`），`state_dict` / `load_state` 携带 `top_m` ⇒ 可进两阶段清单 | manifest `folds["k"].cells.Z1.statistics.Z1.{kind = "rare_concentration", config.top_m = 1}`；`cells.Z1.folds[k].restored_from_manifest`（`run_detectors_g.py:3009` / `:3182`） | `V33::Z1StatisticTest`（9）· `V33::RunnerV33Test`（13） | **IMPL** |
| **2** | **R1：`H = ∞`（对称去掉）** | `--force-h inf`（`run_detectors_g.py:1700`；解析器 `force_h_arg` 在 `:437` 把 `inf` 变成整数哨兵 `10^9`） | `trm3_g.UNBOUNDED_H`（`trm3_g.py:89`）· `is_unbounded_h`（`:1872`）· `h_horizon_unbounded`（`:1890`） | manifest `cell.force_h = 1000000000`、`cell.horizon_mode = "unbounded"`；逐折 `horizon.{mode = "unbounded", n3_gate = "n/a", rule_H, H_effective, unbounded = true}`、`censored_paths = 0`、`censored_endpoints = 0` | `V33::UnboundedHorizonTest`（7） | **IMPL**（真实产物实测：`rule_H = 113`、`H_effective = 552`、`censored_paths = 0`） |
| **3** | **R3：`--debounce K`（注册值 1 = 恒等）** | `--debounce`（`run_detectors_g.py:1813`，默认 1） | `trm3_g.debounce_p`（`trm3_g.py:1940`）· `apply_debounce`（`:1992`）；调用点 `run_detectors_g.py:968`–`970`（在 `DecisionStream` 建立之前） | `args.debounce`；`calibration_design.v3_3.debounce`（`run_detectors_g.py:5250`）；**manifest `cell.debounce`（rev3 R-C1）** | `V33::DebounceTest`（7）· `V33::RunnerV33Test::test_debounce_1_is_byte_identical_to_the_run_without_the_switch` | **IMPL** |
| **4** | **D6：`--stratify-reference n_kb`** | `--stratify-reference`（`run_detectors_g.py:1822`）· `--stratify-config`（`:1858`） | `trm3_g.stratum_label`（`trm3_g.py:1847`）· `stratify_indices`（`:1863`）；`run_detectors_g.n_kb_map_from_config`（`:2564`）· `stratify_provenance`（`:2588`）· `stratum_groups`（`:2641`）· `_eval_weighted_alpha_eff`（`:2688`） | 清单键 `"<channel>@<stratum>"`（`run_detectors_g.py:2797` 生成 `suffix`，`:2922` 写入 `stratum_states`；阶段 2 的查找在 `:2802`–`:2807`）；逐折 `strata.{key, count, per_stratum["n_kb=j"].{n_cal, n_fit, n_reference_episodes, n_eval, alpha_eff, attainability, attainable_rank, far}}`（`:2901`–`:2914` / `:3014`–`:3019`）；`calibration_design.v3_3.stratify_reference` | `V33::StratifiedReferenceTest`（7） | **IMPL**（阶段 2 换分层被清单键查找**硬拒绝**，双向） |
| **5** | **读数开关**（描述性列） | `--recall-horizons`（`:1843`）· `--compare-horizons`（`:1850`）· `--far-episode-census`（`:1833`） | 代码强制 `--recall-horizons` 含 16；`far_episode_census` 构造在 `run_detectors_g.py:3126` | `comparison_anchored_horizons`（`:5341`，只在传了 `--compare-horizons` 时出现）；**`cells.<s>.far_episode_census`（`:3212`，`metrics` 的**兄弟**键，不在 `metrics` 里）** | `V33::ReadoutSwitchesTest`（6，含 `test_far_episode_census_covers_every_scored_normal_exactly_once` 与"不传就不出现"） | **IMPL** |
| **6** | **runner 侧整合** | 全部 v3.3 开关默认关（`--top-m` 1 / `--debounce` 1 / `--stratify-reference none` / `--force-h None`） | `run_detectors_g.main_v32` / `run_cell_v32` | `calibration_design.v3_3`（`:5249`–`:5258`）四个字段 | `V33::RunnerV33Test`（13） | **IMPL** |
| **7** | **rev3 R-C1 的清单钉子 + rev4 R-F1 / R-VAL1 的两条精确带** | （无新开关；由 `--debounce` / `--top-m` / `--force-h` 的取值驱动） | `CELL_PIN_KEYS = ("debounce", "top_m", "horizon_mode")`（`run_detectors_g.py:476`）· `v3_3_switches_used`（`:479`）· `cell_pins`（`:508`）· `verify_manifest` 的 `cell_{key}` 循环（`:4633`–`:4680`，缺键记 `"not pinned by a pre-v3.3 manifest"` 且**不失败**，`:4668`）；`trm3_g.conformal_far_band`（`trm3_g.py:3863`）· `trm3_g.pooled_stratum_far_band`（`:4043`）；`run_detectors_g.conformal_fold_specs`（`:3619`）· `val1_a_exact_block`（`:3681`）· `gate_block` 的逐折行（`:3892`–`:4051`） | manifest `cell.{debounce, top_m, horizon_mode}`（`cell_pins`，`:519`–`:531`；**`cell` 块 18 → 21 个键**）；`verification.checks` 增 `cell_debounce` / `cell_top_m` / `cell_horizon_mode`（**19 → 22 项**）；`calibration_design.pinned.{debounce, top_m, horizon_mode, force_h, v3_3_switches_used, manifest_pins, rule}`（`:5265`–`:5280`）；`gates.<s>.F1_per_fold_conformal*`（`:4028`–`:4051`）；`gates.<s>.VAL1_a_*`（`:3755`–`:3774`） | `V33::ManifestPinsTest`（10）· `V33::ConformalBandTest`（9）· `V33::PooledStratumBandTest`（6）· `V33::F1ConformalRowsTest`（9） | **IMPL** |

**注 a（第 7 条 · 逐折保形带的 `rank` 到底从哪里来）**：预注册 §0.2 R-F1 与 §9.2 F1 写的是
"`r_k` / `n_cal_k` 取自阶段 1 清单 `folds[k].cells.<s>.attainability.{channels.<ch>.rank, n_reference}`"。
**代码逐字如此**（`trm3_g.conformal_far_band` 的 docstring `trm3_g.py:3907`–`:3914` 就写着这两条路径），
但**读取点在结果侧**：`gate_block` 读的是 `cells.<s>.folds[k].attainable_rank.{rank, n_reference}`
（`run_detectors_g.py:3945`–`:3948`），它由 `trm3_g.attainable_rank(calibration.n_reference, alpha)`
（`trm3_g.py:3838`）在**从清单恢复的标定集**上算出（`run_detectors_g.py:2989`–`:2995`），
再由**两处硬核对**把它钉回清单：
`attainability.n_reference == attainable_rank.n_reference`（`:3950`–`:3960`，不等即 `SystemExit`）与
`alpha_eff_k == rank / (n_cal + 1)`（1e-12，`:3963`–`:3972`，不等即 `SystemExit`）。
**两条路径在数值上恒等**（`attainability.channels.<primary>.rank` 与 `attainable_rank.rank` 都是
`floor((n_cal + 1)·α)`，主通道权重 1.0），因此预注册的措辞成立；**审阅者要 grep 的键名是 `attainable_rank`**。

**注 b（第 7 条 · 分层格上没有单一 `rank`）**：`--stratify-reference` 之后该折的计数是**若干个参数不同的
beta-binomial 之和**、无闭式，`gate_block` 因此走 `band_source = "mc"`（`run_detectors_g.py:3901`–`:3942`），
行里 `rank = null`、`rank_note` 说明原因、建带用的逐层 `(n_cal, rank, n_eval)` 记在 `band.per_stratum`。
真实产物实测：`stage2_hinf_nkb_d1_z1` 的折 0 是 `rank = null` / `band_source = "mc"` / `in_band = true`。

**注 c（第 7 条 · 可达性下限不满足时无带）**：`conformal_far_band` 拒绝 `rank < 1`
（`trm3_g.py:3926`–`:3929`），`gate_block` 因此记 `band_source = "unattainable"` / `band = null` /
`in_band = null`（`run_detectors_g.py:3978`，行里的 `band_source` 字段在 `:4004`），汇总标志为 `null`（不可评）。
**只影响 smoke 规模的 fixture**；G-dev / G-conf / G-conf-2 的投影秩都 ≥ 1（预注册 §4.4）。

---

## 3. §14 的报告义务：v3.3 的每一个量 → 从哪里读

> 路径前缀省略：`R` = 阶段 2 的 `result.json`，`M` = 阶段 1 的 `threshold_manifest.json`，
> `<s>` ∈ {`Z1`,`S`,`P`,`M`,`J`}（`J` = `prob_js`）。
> **v3.2 §14 的十二条报告义务逐字沿用**，读法见 `prereg_v3_2_code_mapping.md` §3；
> 本节只列 **v3.3 新增或改变**的量。

### 3.1 主判定与 Holm 族

| 量 | 路径 | 产出代码 |
|---|---|---|
| Δ̂ / CI / McNemar / 两条件 | `R.comparison_anchored.{bootstrap, two_condition, matched_alpha_secondary, normal_denominator, primary_row}` | `run_detectors_g.compare_cells_anchored`（`:3250`），写盘 `:5337` |
| 描述性的其它命中视界 | `R.comparison_anchored_horizons` | `:5341`（只在传 `--compare-horizons` 时出现） |
| Holm 成员 S1 | `R.holm_s1_one_sample.Z1` | `run_detectors_g.one_sample_rate_block`（`:3391`），写盘 `:5344` |
| Holm 成员 S2 | 运行 2b 自己的 `R.comparison_anchored.two_condition` | 同上 |
| S-J | `R.injection_pairing.J.{primary_unfiltered, sensitivity_filtered_negatives}` | 写盘 `:5345` |
| 可达性（`H = ∞` 下期望 `x_beyond_h == 0`） | `R.cells.<s>.metrics.positives_anchored.{recall.x_window.reachable_count, reachability.x_beyond_h, by_x_beyond_h}` | `trm3_g.anchored_positives`（`trm3_g.py:4457`），挂载 `run_detectors_g.py:3106` |

### 3.2 门（v3.3 改变的两条）

| 量 | 路径 | 产出代码 | 注册判定 |
|---|---|---|---|
| **F1 汇总臂** | `R.gates.<s>.gates[] · gate == "F1_pooled_holdout_far_vs_alpha_eff"` 的 `value` / `threshold` / `status`；`R.gates.<s>.alpha_eff_weighted` | `gate_block`（`:4052` 起的 `gates` 列表），常量 `GATE_F1_TOLERANCE = 0.03`（`:3603`） | `\|far.filtered − alpha_eff_w\| ≤ 0.03`（**不变**） |
| **F1 逐折臂（rev4 R-F1）** | `R.gates.<s>.F1_per_fold_conformal[*]`（18 个字段：`fold` / `n_eval` / `n_cal` / `rank` / `rank_note` / `denominator` / `alarm_count` / `far` / `alpha_eff` / `band` / `band_source` / `interval` / `interval_counts` / **`in_band`** / `record_only` / `deviation` / `tolerance` / `within_tolerance`）+ `F1_per_fold_conformal_all_in_band` + `F1_per_fold_conformal_joint_null_pass` + `F1_per_fold_conformal_rule` | `run_detectors_g.py:3892`–`:4051`；带由 `trm3_g.conformal_far_band`（`trm3_g.py:3863`）或 `pooled_stratum_far_band`（`:4043`）算 | **读 `in_band`**；`deviation` / `tolerance` / `within_tolerance` 是 ±0.03 的**记录列**（`record_only: true`，`:4014`） |
| **`VAL1` (a)（rev4 R-VAL1）** | `R.gates.<s>.VAL1_a_exact["n_kb=j"].{stratum, alarm_count, n, denominator, far, band, interval, interval_counts, in_band, per_fold}` + `VAL1_a_all_in_band` / `VAL1_a_joint_null_pass` / `VAL1_a_seed` / `VAL1_a_reps` / `VAL1_a_numpy_version` / `VAL1_a_error` / `VAL1_a_rule` | `run_detectors_g.val1_a_exact_block`（`:3681`），并入 `gate_block` 于 `:4051` | **读 `in_band`**；**只在分层运行上出现**（无分层时 `val1_a_exact_block` 返回 `{}`，`:3719`–`:3720`）。**`VAL1_a_seed == 0` 且 `VAL1_a_reps == 200000`**（`trm3_g.POOLED_BAND_SEED` / `POOLED_BAND_REPS`，`trm3_g.py:4009` / `:4008`） |
| **`VAL1` (b)** | 逐层 `R.cells.<s>.folds[k].strata.per_stratum["n_kb=j"].far.all` **报告侧合并**；无分层侧要把 `R.cells.<s>.far_episode_census` 与 `--stratify-config` 的 `n_kb` 映射连接 | 合并值**不落盘**（§13.1 第 11 条，登记的缺口） | max/min ≤ 2.5（**不变**） |
| F3 / F5 / N1 / N2 | `R.gates.<s>.gates[]` 的另四行 | `gate_block`（`:3778`） | 不变 |
| N4（逐层判） | `R.cells.<s>.folds[k].attainability.{ok, stratified, per_stratum.<label>.{ok, channels.<ch>.rank, n_reference}}` | `run_detectors_g.py:2927`–`:2946`（分层时 `ok` = **每一层都 ok**） | `floor((n_cal+1)·α) ≥ 1` **逐层** |

### 3.3 两阶段留痕与守卫

| 量 | 路径 | 产出代码 | 注册期望 |
|---|---|---|---|
| 清单守卫 | `R.threshold_manifest.verification.{checks, failed, ok, manifest_version, stage2_start, seal_at_stage1_start, seal_at_stage2_start}` | `verify_manifest`（`:4471`） | **`len(checks) ≥ 22`**、`failed == []`、`ok == true` |
| 三项清单钉子 | `…checks[check ∈ {cell_debounce, cell_top_m, cell_horizon_mode}]` | `:4633`–`:4680` | `cell_debounce == 1`、`cell_top_m == {"Z1": 1}`（`:4653`–`:4655` 按格构造期望值）、`cell_horizon_mode == "unbounded"` |
| 本次运行钉住了什么 | `R.calibration_design.pinned.{debounce, top_m, horizon_mode, force_h, v3_3_switches_used, manifest_pins, rule}` | `:5265`–`:5280` | G-conf-2 的阶段 2 上 `manifest_pins == ["debounce", "horizon_mode", "top_m"]`；**空列表 = 清单是 pre-v3.3 的，三项检查按 `"not pinned"` 通过** |
| run-once 守卫 | `R.run_once_guard.{stage, paths_checked, existing, allow_overwrite, enforced, rule}` | `refuse_existing_outputs`（`:4856`），写盘 `:5360` | `existing == []`、`allow_overwrite == false`、`enforced == true` |
| 封存两读 | `R.seal.{when, checked_at, any_sealed, pools[*]}` 与 `R.threshold_manifest.verification.seal_at_stage{1,2}_start` | `seal_check`（`:2345`），写盘 `:5359` | 两次的 `trace_json_set_sha256` 相同、`self_consistent == true` |
| **逐臂哈希（G-conf-2 上不再是空跑）** | `R.seal.pools[*].arm_hashes.{present, path, per_arm, normal_union_sha256_recorded, normal_union_sha256_observed, normal_union_matches, attack_sha256_recorded, attack_sha256_observed, attack_matches}` | `arm_hash_check`（`:2301`；候选路径 `<target>/ARM_HASHES.json` 与 `<subset>_meta/ARM_HASHES.json`，`:2311`–`:2318`） | **`present == true`**、两个 `*_matches` 都 `true`、`normal_union_sha256_recorded == b56705ea623eac6d…`。**不是门**（不 `SystemExit`），必须逐字报告 |
| 攻击臂在阶段 1 一条未读 | `M.stage1_attack_traces{count, sha256, per_dir}` / `M.stage1_attack_traces_skipped`（`:4329`–`:4330`）与 `R.attack_trace_census.{count, sha256}`（`:5362`） | `attack_trace_census`（`:2275`） | 两者相等，**G-conf-2 期望 160** |
| 三分位切点 | `R.calibration_design.length_tertiles.{source, cutpoints, replayed_from_manifest}`（`:5240`）与 `M.length_tertiles`（`:4314`） | `build_manifest`（`:4220`） | `source == "stage1_target_normals"`；**不要读 `metrics.far.length_tertile_definition`**（出处串与事实相反，§13.1 第 6 条） |
| 冻结守卫 | `R.data_discipline_guard.*`（`:5219`）、`R.prereg.{path, sha256}`（`:5211`） | `prereg_path`（`:94`，默认常量 `PREREG_PATH` 在 `:83`，**仍是冻结的 v3.1 文件**） | `enforced` / `dirty == false` / `head_is_freeze_commit` / `prereg_sha256_matches` / `labels_sha256_missing == []` |

---

## 4. 冻结审阅者清单（提交 A″；逐条给命令或字段路径与期望值）

> 每一条都必须**实际跑一遍或实际读一次**，不接受"看起来对"。
> 命令一律在仓库根目录执行；`$PY` = `/home/wzh/Agent-Moe-Research/.venv/bin/python`，
> 需要导包时前缀 `PYTHONPATH=$PWD/src:$PWD/scripts`。
> **前 12 条逐字沿用 v3.2 §15.3 / `prereg_v3_2_code_mapping.md` §4**；**13–18 是预注册 §15.3 的 v3.3 专属六条**；
> **19 起是本文件为 A″ 补的**。

| # | 检查项 | 怎么验 | 期望 |
|---:|---|---|---|
| **1** | 工作树干净、`HEAD` 就是要冻结的提交 | `git status --porcelain`；`git rev-parse HEAD` | 只剩 `?? artifacts` 一行；HEAD 与提交 message 里记的一致 |
| **2** | 折键与 fixture 均衡 | `R.calibration_design.{fold_key, fold_assignment_sha256, fold_fixture_crosstab.collinear_fixtures}` | `"fixture_rank_mod"`；`collinear_fixtures == []`；G-conf-2 的 `fold × fixture` = BRC 32/31/31 · KSW 31/31/31 · WNF 31/31/31 |
| **3** | 数据门先于路由 | `g_conf2_data_gates.json` 的 `created_at` 早于任何 G-conf-2 检测器产物 | 时间序正确；**并核 `counts.annotated_rows == 888`、`counts.by_arm == {clean: 336, benign_control: 336, attack: 216}`**（§12.1 第 4 条的硬前置） |
| **4** | 守卫有牙（冻结提交 / 预注册哈希 / 标签哈希） | 用一个错的 `--freeze-commit` 跑一次非冒烟命令 | **非零退出**；正确命令下 `R.data_discipline_guard` 五项全真 |
| **5** | 两阶段守卫有牙 | 把 manifest 改一个字节 → `--stage score`；请求一个不在 `M.cell.statistics` 里的统计量 | 两次都 `SystemExit` |
| **6** | 多格清单真的多格 | `M.manifest_version` 与 `M.folds.{0,1,2}.cells` 的键集 | `"v3.2-2"`；每折都是 `["J","M","P","S","Z1"]` |
| **7** | 逐折断言被强制 | `R.assertions.by_statistic.<s>[]` 与 `--expect-n-reference-folds` | 主格（无分层）上三行 `n_reference` 的 `expected` 非空；**运行 2c 上 `expected` 恒为 `None`（§13.1 第 9 条），必须人工核对 `<m0,m1,m2>`** |
| **8** | 命中口径 | `R.anchoring.{anchor, hit_window, positives}` | `x` / `e_view_to_anchor_plus_h` / `injection_present` |
| **9** | 匹配分母 | `R.comparison_anchored.{normal_denominator.denominator, primary_row, matched_alpha_secondary.source}` | `"filtered_normal_union_no_legitimate_refusal"` / `"matched"` / `"threshold_manifest.matched_alpha_inputs"` |
| **10** | S-J 的两个口径都落盘 | `R.injection_pairing.J` 的键集 | 同时含 `primary_unfiltered` 与 `sensitivity_filtered_negatives`，两者都带 `two_condition` |
| **11** | 三分位切点来自阶段 1 | `R.calibration_design.length_tertiles.{source, replayed_from_manifest, cutpoints}` | `"stage1_target_normals"` / `true` / 与 `M.length_tertiles.cutpoints` 逐位相同 |
| **12** | 家族向量能读到 | `R.positive_families.Z1.positives_by_family` | 16 项；**不要去找 `bootstrap.family_sizes`，它不存在** |
| **13** | **`H = ∞` 真的对称** | `M.cell.force_h`；`R.cells.<s>.folds[k].horizon.{mode, n3_gate, rule_H, H_effective}`；`…censored_paths`；`…positives_anchored.reachability.x_beyond_h` | `10^9` / `"unbounded"` / `"n/a"` / 三折都有 `rule_H` 与 `H_effective`；`censored_paths == 0`；`x_beyond_h == 0`。**反向测试：传 `--expect-h 352` 必须判 FAIL** |
| **14** | **`Z1` 真的是 top-1** | `M.folds["k"].cells.Z1.statistics.Z1.{kind, config.top_m}`；`R.cells.Z1.folds[k].restored_from_manifest` | `"rare_concentration"` / `1` / `true`。**反向测试：`--top-m` 拉满（`len(layers) × num_experts`）时 Z1 的 FAR 等于 S 格**（`V33::Z1StatisticTest`） |
| **15** | **去抖是恒等的** | `R.args.debounce` 与 `R.calibration_design.v3_3.debounce`（**两处**） | 都是 `1`。"与不带该开关的运行逐字节相同"这一半由 `V33::RunnerV33Test::test_debounce_1_is_byte_identical_to_the_run_without_the_switch` 承担（G-conf-2 上不可执行，§12.2 禁止第 4 次 `--stage score`） |
| **16** | **五格清单** | `M.cell.statistics`、`M.folds["k"].cells`、**`M.matched_alpha_inputs.cells`** 三处的键集 | 都是 `["J","M","P","S","Z1"]`；**`Z1` 与 `S` 的 `M.fit.q_table_sha256` 逐位相同**（共用同一个 q / `Omega_rare`） |
| **17** | **`VAL1` 用的是另一份清单** | `stage1_strat` 的清单键形如 `"<channel>@<stratum>"` | 是；**反向测试：把 `stage1` 的清单喂给 `--stratify-reference n_kb` 的阶段 2 必须被硬拒绝**（`V33::StratifiedReferenceTest`） |
| **18** | **清单钉子真的有牙** | `M.cell.{debounce, top_m, horizon_mode}`；`R.threshold_manifest.verification.checks` 里的三项 `cell_*`；`len(checks)` | `1` / `{"Z1": 1}` / `"unbounded"`；三项都 `ok == true`；`len(checks) ≥ 22`、`failed == []`。**反向测试：同一份清单跑 `--debounce 2` 必须被硬拒绝**；**兼容测试：pre-v3.3 清单三项各记 `"not pinned"` 且 `ok == true`**（`V33::ManifestPinsTest`，10 条） |
| **19** | **两条精确带已落地** | `PYTHONPATH=... $PY -c "from research_v2 import trm3_g; print(hasattr(trm3_g,'conformal_far_band'), hasattr(trm3_g,'pooled_stratum_far_band'), hasattr(trm3_g,'binomial_far_band'), 'conformal_far_band' in trm3_g.__all__, 'pooled_stratum_far_band' in trm3_g.__all__, 'binomial_far_band' in trm3_g.__all__)"` | `True True False True True False`（预注册 §15.2 第 13 条） |
| **20** | **F1 逐折行的注册判定字段在场** | `R.gates.<s>.{F1_per_fold_conformal, F1_per_fold_conformal_all_in_band, F1_per_fold_conformal_joint_null_pass}`；每行 18 个字段 | 每格三行；`in_band` / `band_source` / `record_only == true` 都在；**`F1_per_fold_binomial*` 一个都不存在** |
| **21** | **`VAL1_a_exact` 的种子与次数是注册值** | 运行 2c 的 `R.gates.<s>.{VAL1_a_seed, VAL1_a_reps, VAL1_a_numpy_version, VAL1_a_all_in_band}` | `0` / `200000` / 记录当次 numpy 版本 / 三层布尔；**换种子或换次数 = 换一道门**（§15.2 第 13 条） |
| **22** | **`band_source` 的三种取值各自出现在该出现的地方** | 主格（无分层）`exact`；运行 2c（分层）`mc` 且 `rank == null`、`band.per_stratum` 在场；`unattainable` 只在 `rank < 1` 时出现 | 与 §2 注 b / 注 c 一致 |
| **23** | **`--fold-key` 的取值** | `run_detectors_g.py:1654` 的 `choices=sorted(trm3_g.FOLD_KEYS)`；`trm3_g.py:3676` | `{fixture_rank_mod, scenario_mod}`；**默认值是 `scenario_mod`（`trm3_g.DEFAULT_FOLD_KEY`，`trm3_g.py:3677`），因此注册命令必须逐字带 `--fold-key fixture_rank_mod`** |
| **24** | **§12.2 的五条注册命令逐字可解析** | §7 的干跑脚本（import `run_detectors_g` 并对每条注册 argv 调 `_args`） | 五条全过、`force_h == 1000000000`、`prereg_path == docs/research_v4/detector_prereg_v3_3.md`、`allow_overwrite == False`、`dev_smoke == False` |
| **25** | **注册命令指名的预注册文件真的存在** | `ls docs/research_v4/detector_prereg_v3_3.md`；`grep -n -- '--prereg-path docs/research_v4/detector_prereg_v3_3.md' docs/research_v4/detector_prereg_v3_3.md` | 文件存在；`grep` 命中 **2 行**（阶段 1a 与运行 2a 的命令块；1b / 2b / 2c 写的是"与上面逐字相同，只改这几个"） |
| **26** | **`--prereg-path` 的默认值仍是冻结的 v3.1 正文** | `grep -n 'PREREG_PATH' scripts/research_v4/run_detectors_g.py`（`:83`）；`FF::PreregPathTest`；`R2::PreregPathFlagTest` | 默认仍是 `docs/research_v4/detector_prereg_v3_1.md`，**A″ 不改这个常量**（正是它让 v3.1 §19.7 的回归逐字段不变） |
| **27** | 冒烟不能碰封存批 | `$PY scripts/research_v4/run_detectors_g.py --stage calibrate --normal-only-smoke --target artifacts/agent_v2/dataset_g/g_conf2 --cal-from-target --outputs none` | **非零退出**，信息里含 `--normal-only-smoke` / `SEALED` 与该目录；**不产生任何文件** |
| **28** | "只跑一次"有机械留痕 | `R.run_once_guard`；把同一条命令原样再敲一次 | `existing == []` / `allow_overwrite == false` / `enforced == true`；重敲**非零退出**（**不得用 `--allow-overwrite` 绕过**） |
| **29** | **G-conf-2 的封存哈希逐行重算** | `sha256sum` 预注册 §4.1a / §15.4 的每一行；`$PY scripts/research_v4/g_conf_seal.py --verify artifacts/agent_v2/dataset_g/g_conf2/SEALED.json` | 与 §4.1a 逐位相同（A″ 一致性轮已全部重算过一次）；`--verify` 输出 `verified: true, mismatches: [], trace_count: 720` |
| **30** | **逐臂哈希在 G-conf-2 上是"真比对"而不是空跑** | 阶段 1 / 阶段 2 的 `R.seal.pools[*].arm_hashes` | `present == true`、`normal_union_matches == true`、`attack_matches == true`；**这不是门**，报告必须逐字抄这三个布尔值（§3.3 / 预注册 §3.4） |
| **31** | **折 × episode 的逐折大小** | `PYTHONPATH=$PWD/src $PY -c "…trm3_g.fold_assignment(ids, folds=3, key='fixture_rank_mod', fixtures=…)"` 在 `configs/dataset_g/g_conf2.json` 上重算 | 逐折正常 episode **218 / 238 / 216**（合计 672）、逐折 ep1 占比 0.137615 / 0.218487 / 0.138889、`n_kb × fold` 正常 episode 0：44/42/38 · 1：100/112/100 · 2：74/84/78（预注册 §2.2 / §4.4 / §4.1a） |
| **32** | **载攻击 episode 数** | 同一份配置：`sum(1 for s in scenarios if 'attack' in s.factory.collected_arms)` 与它们的 `arms.attack.channel` 分布 | **160**；`direct_user` 56 / `multi_turn_user` 56 / `tool_output` 48（预注册 §4.1a / §4.3） |
| **33** | 检验力表可复现且与正文逐格一致 | 跑 §8.2 的注册命令到 scratchpad，逐格比 `main/power_sim.md` 与预注册 §8.2 / §8.3 | 逐格相同。**sha256 是凭据不是复现判据**（`power_sim.json` 含 `created_at` / `elapsed_seconds`，md 含 "Generated on"，见 `freeze_a2_checklist.md` 步 5 的 ⚠） |
| **34** | 冻结的 OLMoE 侧未被碰 | `git diff --stat scripts/research_v3/verify_m_only_vs_frozen.py`（空）；`git diff --stat src/research_v2/trm3.py`（空）；跑该脚本 | `halves_identical = True`、`0 trace(s) differ` |
| **35** | 全部测试全绿 | `PYTHONPATH=... $PY -m pytest tests/ -q` | 全绿；**当次条数抄进提交 message，不写死**（A″ 一致性轮实测 1432 passed / 143 subtests） |
| **36** | §15.4 的哈希逐行重算 | `freeze_a3_checklist.md` 步 6 的循环 | 与 §15.4 的每一行逐位相同；不同即**先改预注册再冻结** |
| **37** | 本文件与操作单进哈希表 | `sha256sum docs/research_v4/prereg_v3_3_code_mapping.md docs/research_v4/freeze_a3_checklist.md` | 两行进 `freeze_a3_checklist.md` §4 的冻结记录 |
| **38** | **流程侧的三条（代码保证不了）** | 读操作单 | ① 两份阶段 1 **各只跑一次**、manifest 的两个哈希当场记录（Q10 的 v3.3 形式）；② 阶段 2 **只跑 3 次**（D-3；`refuse_existing_outputs` 只按 `--run-name` 拒绝，**不按子集计数**）；③ `--arm-hashes` **不在 G-conf-2 阶段 2 之后补跑**（本预注册不注册该运行；G-conf-2 的逐臂哈希已在封存之前算好并被 `SEALED.json` 覆盖） |

---

## 5. 本轮改掉的文档侧不一致（**10 处，全部只改预注册正文，没有改代码**）

| # | 位置 | 原措辞 | 实际情况（`file:line` / 实测） | 处置 |
|---:|---|---|---|---|
| **1** | §3.3 (c) | "阶段 2 的 **19 项** `verify_manifest` 守卫" | `verify_manifest` 实际产出 **22 项**（19 + `cell_debounce` / `cell_top_m` / `cell_horizon_mode`，`run_detectors_g.py:4633`–`:4680`）；真实产物 `round4_conformal/*/result.json` 实测 `len(checks) == 22`、`failed == []` | 改成 22 项，并把 19 项标为"v3.2 的" |
| **2** | §12.2 | "`cell` 块实测 **18** 个键" | rev3 R-C1 之后是 **21** 个（`run_detectors_g.py:519`–`:531`（`cell_pins`）写入三个钉子）；v3.2 的 `v3_2_round2b_smoke/stage1/threshold_manifest.json` 实测 18，v3.3 的 `round3_pins/stage1_hinf_nostrat_d1/threshold_manifest.json` 实测 21 | 改成 21，并逐字列出 v3.2 的那 18 个 |
| **3** | §3.4（rev2 的 N-1 段） | "工作树上**任何位置都不存在 `ARM_HASHES.json`**，因此 `arm_hash_check` 在 G-conf-2 的每一次运行上都会记 `present = False` 并不产生任何失败" | **G-conf-2 的 `ARM_HASHES.json` 存在**（`artifacts/agent_v2/dataset_g/g_conf2/ARM_HASHES.json`，sha256 `418000a6…`），写在封存**之前**并作为 `extra_files` 被 `SEALED.json` 覆盖（`g_conf2_build_log.md` §7.1）；`arm_hash_check`（`run_detectors_g.py:2301`）因此会记 `present = True` 并**当场重算**正常并集与攻击臂摘要 | 整段重写：注册期望改为 `present == true` + 两个 `*_matches == true`；**保留"它不是门"的限定**；并写明与 Q6 的关系（Q6 的理由——文件落在 `<subset>_meta/` 不被封存覆盖——在本批上不适用） |
| **4** | §3.4 末段 | "`g_conf2` 的封存尚未执行（`g_conf2_build_log.md` §4–§7 目前是占位符，采集仍在进行）" | 已于 `2026-09-08T14:18:22.129190Z` 封存；`SEALED.json` sha256 `e788bc09…`、`trace_count = 720`、`--verify` 实测 `verified: true` | 改成"已封存"，并把 §12.1 第 2 条标为已满足、第 3 条（标签）仍待办 |
| **5** | §12.1 第 2 条 | "**当前状态：采集进行中，`packets/g_conf2/` 与 `SEALED.json` 都尚不存在**" | 同上；`packets/g_conf2/{packet,review_packet}.jsonl` 与 `blindness_scan.json` 都在，哈希已实算 | 改成已满足，并把两个手工核对的哈希直接写进正文 |
| **6** | §10.3 两处 | "注册的合并读法…零假设通过率 **0.964**"、"上面的零假设通过率（(a) **0.964**、(b) ≈ 0.95）" | 0.964 是 **rev2 的 ±0.05 固定带**的值；**rev4 注册的是蒙特卡洛精确带**，`VAL1_a_joint_null_pass` 实测 **0.91675**（G-dev），投影 0.9195 | 两处都改成 0.9167 / 0.9195，0.964 降为"为什么必须换带"的中间读数 |
| **7** | §13 卷首 | "`tests/test_research_v4_v3_3.py` **49 个测试、六个类**；全套 **1395 passed, 128 subtests**" | 实测 **83 / 10 类**；全套 **1432 passed, 143 subtests**（A″，HEAD `42a1ca6`） | 改成实测值，并给 §13 的表补第 7 行（R-C1 + R-F1 / R-VAL1） |
| **8** | §13.1 第 13 条 / §16.3 第三条 | "工作树上任何位置都没有 `ARM_HASHES.json`" | 同 #3 | 改写；**G-conf 侧的"两个位置都不存在"是对的**（§6 复核确认），予以保留并写明它的替代凭据 |
| **9** | §15.2 第 8 条 | "本轮参考读数 **1395 passed, 128 subtests**" | 同 #7 | 补 A″ 的实测计数，仍保留"不写死条数"的规定 |
| **10** | §0.2 R-C1、§3.3、§15.3 第 18 条 | "`cell_top_m == 1`"、"`cell` 块含 `top_m == 1`" | **`cell.top_m` 是一个 `{statistic: top_m}` 映射，不是标量**：`cell_pins` 只为 `TOP_M_STATISTICS = ("Z1",)` 的格写这个键（`run_detectors_g.py:473` / `:521`–`:525`），实测 `{"Z1": 1}`；`verify_manifest` 的 `cell_top_m` 比的是**清单键集 ∩ 本次 `--statistic` 请求集**上的值（`:4653`–`:4656`） | 三处改成 `{"Z1": 1}` 并写明比较口径；**数值不变**（`top_m` 仍是 1，运行 2b 的期望仍是 `{"Z1": 1}`） |

**另外新增（不是"改错"，是 A″ 上第一次可算）**：§4.1a（G-conf-2 的封存事实与哈希 + 组长裁定 **R-A** / **R-B** 逐字）、
§15.4 的五行封存哈希、§19.3 的逐臂哈希行、§16.4 R8 的状态更新。

**没有发现代码缺陷。** 预注册 §13.1 已登记的 15 条缺口，本轮逐条复核后状态如下：

| §13.1 # | 状态（A″ 复核） |
|---:|---|
| 1 / 2 / 3 / 4 | 不变（`--verify` 无时间戳、不覆盖 `extra_files`、runner 不自动跑 `--verify`、`cell_matches` 只比 5 个键）。**第 4 条的实质影响已被 R-C1 的三项 `cell_*` 检查缩小**——`cell_matches` 仍只比 5 个键，但另有三项独立检查 |
| 5 | 不变（`g_dev_data_gates.py:` 的 `THRESHOLDS` 硬编码 G-dev 值），判定以预注册 §9.3 的表为准 |
| 6 | 不变（`metrics.far.length_tertile_definition.source` 硬编码 `"frozen_g_cal_cutpoints"`，与事实相反；两处 `cutpoints` 逐位相同，数值不受影响） |
| 7 | 不变（`positives_anchored.per_episode[*]` 无 `fold` 字段，必须按 key → scenario → `calibration_design.fold_assignment` 连接） |
| 8 | **已闭合**（rev3 R-C1 落地，见 §2 第 7 行）；**剩余**：`anchor` / `hit_window` / `tolerance_bands` / `temporal_d` / `recall_horizons` 按构造仍进不了清单 |
| 9 | 不变（`--expect-n-reference-folds` 在分层运行上空转，`run_detectors_g.py` 的 `fold_args.expect_n_reference = expected[fold] if label is None else None`）⇒ 运行 2c 上是记录项 + 人工核对 |
| 10 | 不变（`_weighted_alpha_eff` 按 `n_cal` 加权，与 `gates[]` 的 F1 行文字一致） |
| 11 | 不变（逐层合并的条件 FAR 不落盘，报告侧重算；**(a) 侧已由 `VAL1_a_exact` 落盘合并值，只有 (b) 侧仍需重算**） |
| 12 | 不变（`gates.<s>.gates[*]` 由 runner 计算，五行） |
| 13 | **已改写**（见 §5 #3 / #8） |
| 14 | 不变（`--statistic` 帮助文本已列 Z1，§20 (a)） |
| 15 | **已闭合**（rev4 把 `binomial_far_band` 与 `F1_per_fold_binomial*` 整块删除；`V33::ConformalBandTest` 直接断言 `not hasattr(trm3_g, "binomial_far_band")` 与 `"binomial_far_band" not in trm3_g.__all__`，`tests/test_research_v4_v3_3.py:1032`–`:1033`） |

---

## 6. G-conf 的逐臂哈希复核（`g_conf2_build_log.md` §8 第 2 条的遗留问题）

**问题**：构建日志 §7.1 写"G-conf 的 `ARM_HASHES.json` 是封存之后才算的，那时子集根已只读，
文件只能落到 `artifacts/agent_v2/dataset_g/g_conf_meta/`"，§8 第 2 条据此建议"复核 G-conf 的
`g_conf_meta/ARM_HASHES.json` 是否仍是正确内容"。

**事实（A″ 一致性轮实测）**：**G-conf 从来没有 `ARM_HASHES.json`。**
`find artifacts -name 'ARM_HASHES*'` 在整个 artifacts 树下只命中**一个**文件——
`artifacts/agent_v2/dataset_g/g_conf2/ARM_HASHES.json`；
`artifacts/agent_v2/dataset_g/g_conf_meta/` 这个目录**根本不存在**。
这与组长裁定 **Q6**（`--arm-hashes` 不得在 G-conf 阶段 1 之前跑）以及
`freeze_review_v3_2_discipline.md` §144–145、`freeze_review_v3_3_data.md` N-1 的记录**完全一致**：
那一轮**从未生成过**该文件。**⇒ 构建日志 §7.1 / §8 第 2 条的这个前提要更正**（本轮已在该文件末尾追记）。

**G-conf 的逐臂哈希实际记录在哪里**：正是 Q6 设计的替代物——**两阶段产物自己记的**：

| 量 | 路径 | 记录值 |
|---|---|---|
| 正常臂（clean + benign_control + benign_lexical）逐目录摘要 | `v3_2_conf/stage1/threshold_manifest.json` → `inputs.normal_traces_per_dir[0].sha256`（560 条） | `aa27366f361dae5884feac9fa7b389d80b59f3f1286320f774c9ae44ece7a457` |
| 正常臂集合摘要（**另绑运行目录字符串**） | 同上 `inputs.normal_trace_set_sha256` | `48b12b30b9d66fcd352d22fff0632f7bfb60e6e537c18104dd0dd39faf7381f8` |
| 攻击臂 | `stage1` 的 `stage1_attack_traces.sha256` = 三次阶段 2 的 `attack_trace_census.sha256`（160 条） | `02d04db0d7c95dc97d8319a7adc7fbbb9dae3b5af6e8b88a102169cf381de3b7` |

**复算（只读，2026-09-08）**：用 `ARM_HASHES.json` 自己写明的那条规则
（"`normal_union_sha256` 就是 `run_detectors_g.normal_trace_manifest` 记进
`inputs.normal_traces_per_dir[*].sha256` 的逐目录摘要——它哈希的是**相对路径 + 文件内容**"），
即 `io_g.trace_digest(run_dir, variants=…)`（`src/research_v2/io_g.py:1568`，**只读 `trace.json`，不打开任何路由分片**）
在 `artifacts/agent_v2/dataset_g/g_conf` 上重算：

| 臂 | trace | 重算 sha256 | 与记录值 |
|---|---:|---|---|
| `clean` | 280 | `7d61c280c342fc2ef3d8db6adcc04bf3d2dd94224b8aa45ca5faa05e415aeb26` | （G-conf 未逐臂记录，本行是新读数） |
| `benign_control` | 280 | `e84607f13c6bf914b58f2f56a9ae143f79956f605389f8fdc3007aef1047a928` | 同上 |
| `benign_lexical` / `legitimate_refusal` | 0 / 0 | `e3b0c442…b855`（空集摘要） | 同上 |
| **正常并集**（`io_g.NORMAL_VARIANTS`） | **560** | **`aa27366f361dae5884feac9fa7b389d80b59f3f1286320f774c9ae44ece7a457`** | **✅ 与阶段 1 清单逐位相同** |
| **`attack`** | **160** | **`bdf4ac51f7c513d21ddd8bfc07a4df9ebc6e0708c6761460428881360d7a1ecf`** | 见下一行 |
| 全 720 | 720 | `7628fd3afcd9446b23f8ee91f7693a4f49ae5d1788219051362a2a9a77ee9462` | （新读数） |

**两个"绑目录字符串"的合成摘要也逐位重现**（`normal_trace_manifest` `run_detectors_g.py:2190`–`:2196`
与 `attack_trace_census` `:2275`–`:2280` 的规则都是 `sha256("<run_dir>:<per-dir digest>")`）：

```
sha256("artifacts/agent_v2/dataset_g/g_conf:aa27366f…") = 48b12b30b9d66fcd352d22fff0632f7bfb60e6e537c18104dd0dd39faf7381f8  ✅
sha256("artifacts/agent_v2/dataset_g/g_conf:bdf4ac51…") = 02d04db0d7c95dc97d8319a7adc7fbbb9dae3b5af6e8b88a102169cf381de3b7  ✅
```

**结论：全部匹配，零 mismatch。** G-conf 的正常臂与攻击臂 `trace.json` 集合**自 v3.2 的两阶段运行以来一字未改**。
`auto_subset_config` 与显式 `--config configs/dataset_g/g_conf.json` 两种变体覆盖解析下**读数完全相同**
（G-conf 没有 `normal_variant` 覆盖生效的 scenario）。
**本轮没有向 `g_conf/` 写入任何东西**，也没有生成 `ARM_HASHES.json`（Q6 之后再补一份没有凭据价值：
它无法证明"当时"的内容，只能证明"现在"的内容——而"现在"已由上表证明）。

**G-conf-2 侧不做同样的复算**：它是封存批，A″ 一致性轮**不打开它的任何 `trace.json`**。
`ARM_HASHES.json` 记录的 `normal_union_sha256 = b56705ea623eac6d…`（560）/ attack `7f709aac…`（160）
将在**阶段 1 / 阶段 2 由 `arm_hash_check` 当场重算并比对**（§3.3 / §4 第 30 条）。

---

## 7. §12.2 的五条注册命令 —— argparse 干跑结果

**做法**（**没有跑任何检测器**）：用一段脚本把 **§12.2 的五个命令块从预注册文件里正则抽出来**
（不手抄），把 `<B″>` / `<A″SHA>` / `<LSHA>` / `<n0,n1,n2>` / `<m0,m1,m2>` 填成合法的假值，
把三条"与上面逐字相同、只改这几个"的块按它自己写的改动量拼到它的基命令上，
然后调 `run_detectors_g._args(argv)`（`run_detectors_g.py:1430`），再逐字段断言注册值。
**五个块全部解析成功**，`run_name` 依次是 `stage1` / `stage1_strat` / `stage2_Z1_vs_P` /
`stage2_Z1_vs_S` / `stage2_val1_nkb`。

| 命令 | argv 词数 | 解析 | 抽查断言 |
|---|---:|---|---|
| **阶段 1a**（`stage1`） | 74 | ✅ | `stage == "calibrate"`、`statistic == "Z1,S,P,M,prob_js"`、`top_m == 1`、`debounce == 1`、`force_h == 1000000000`、`stratify_reference == "none"`、`fold_key == "fixture_rank_mod"`、`cal_filtered_only == True`、`expect_h is None`、`outputs == "primary"`、`allow_overwrite == False`、`dev_smoke == False`、`prereg_path == docs/research_v4/detector_prereg_v3_3.md` |
| **阶段 1b**（`stage1_strat`） | 76 | ✅ | 同上，且 `stratify_reference == "n_kb"`、`run_name == "stage1_strat"` |
| **运行 2a**（`stage2_Z1_vs_P`） | 93 | ✅ | `stage == "score"`、`anchor == "x"`、`hit_window == "e_view_to_anchor_plus_h"`、`positives == "injection_present"`、`compare_statistic == "P"`、`outputs == "all"`、`far_episode_census == True`、`recall_horizons == "8,16,32,64,full"`、`compare_horizons == "32,64"` |
| **运行 2b**（`stage2_Z1_vs_S`） | 93 | ✅ | `statistic == "Z1,S"`、`compare_statistic == "S"`、`run_name == "stage2_Z1_vs_S"` |
| **运行 2c**（`stage2_val1_nkb`） | 95 | ✅ | `stratify_reference == "n_kb"`、`expect_n_reference_folds == "<m0,m1,m2>"`、`run_name == "stage2_val1_nkb"` |

**逐个开关的 `file:line`**（注册命令里出现的**全部** 43 个开关，按命令里的出现顺序；
每一个都以**注册的拼写**存在）：

| 开关 | `run_detectors_g.py:` | 开关 | `run_detectors_g.py:` |
|---|---:|---|---:|
| `--stage` | 1711 | `--tolerance-bands` | 1552 |
| `--target` | 1437 | `--temporal-d` | 1530 |
| `--target-labels` | 1466 | `--tertile-cutpoints-from-target` | 1543 |
| `--cal-from-target` | 1641 | `--require-quality-labels` | 1624 |
| `--cal-folds` | 1648 | `--outputs` | 1625 |
| `--fold-key` | 1654 | `--threshold-manifest` | 1721 |
| `--cal-filtered-only` | 1683 | `--output-root` | 1626 |
| `--fixture-config` | 1666 | `--run-name` | 1627 |
| `--seal-manifest` | 1675 | `--prereg-path` | 1578 |
| `--view` | 1441 | `--prereg-sha256` | 1576 |
| `--tag-scope` | 1614 | `--labels-sha256` | 1588 |
| `--statistic` | 1443 | `--freeze-commit` | 1575 |
| `--alpha` | 1455 | `--compare-statistic` | 1454 |
| `--top-m` | 1805 | `--anchor` | 1735 |
| `--force-h` | 1700 | `--hit-window` | 1742 |
| `--debounce` | 1813 | `--positives` | 1750 |
| `--stratify-reference` | 1822 | `--injection-negatives` | 1758 |
| `--stratify-config` | 1858 | `--recall-horizons` | 1843 |
| `--h-min-survivors` | 1469 | `--compare-horizons` | 1850 |
| `--window-s` | 1492 | `--far-episode-census` | 1833 |
| `--window-z1` | 1798 | `--bootstrap-replicates` | 1628 |
| `--window-p` | 1494 | `--expect-n-reference-folds` | 1764 |
| `--window-m` | 1493 | | |
| `--window-prob` | 1497 | | |
| `--bucket-size` | 1470 | | |
| `--min-bucket-traces` | 1471 | | |
| `--min-channel-windows` | 1473 | | |
| `--min-channel-traces` | 1480 | | |

**注册命令里不出现、但必须确认"没被误传"的两个**：`--allow-overwrite`（`:1770`）与 `--dev-smoke`（`:1778`）——
五条命令的 `Namespace` 上都是 `False`。**`--expect-h`（`:1595`）也确实没有出现在任何一条命令里**
（`H = ∞` 下传它必然判 FAIL，预注册 §12.2 第一条注）。

**数据门与检验力脚本的开关**（预注册 §19.4 / §19.5）：
`g_dev_data_gates.py` 的 `--labels`（`:753`，**required、可重复**）· `--metadata`（`:757`）· `--run-dir`（`:761`）·
`--h`（`:766`）· `--looks-per-token`（`:770`）· `--output`（`:776`）；
`prereg_power_sim.py` 的 `--config`（`:453`）· `--output-dir`（`:454`）· `--replicates`（`:457`）·
`--bootstrap`（`:458`）· `--seed`（`:459`）· `--n`（`:460`）· `--rho`（`:463`）· `--delta`（`:464`）· `--psi`（`:465`）；
`g_conf_seal.py` 的 `--subset`（`:321`）· `--root`（`:322`）· `--config`（`:323`）· `--manifest`（`:324`）·
`--packet`（`:325`）· `--mapping`（`:326`）· `--extra`（`:327`）· `--out`（`:328`）· `--no-chmod`（`:329`）·
`--verify`（`:330`）· `--arm-hashes`（`:331`）。**全部以注册的拼写存在。**

---

*本文件由研究工程 agent（Opus 5，freeze-A″ 一致性工程师）撰写。*
*核验基准 HEAD `42a1ca6`；预注册正文 `docs/research_v4/detector_prereg_v3_3.md`；*
*操作单 `docs/research_v4/freeze_a3_checklist.md`；本轮没有改动任何代码、配置或标签。*
