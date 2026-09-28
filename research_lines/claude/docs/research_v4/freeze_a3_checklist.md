# 冻结提交 A″ 操作单（检测器 v3.3 预注册 / 数据集 G-conf-2）

**配套文件**：`docs/research_v4/detector_prereg_v3_3.md`（下称"预注册"；**冻结候选 A″，rev4 定稿**；
它的草稿前身 `detector_prereg_v3_3_draft.md` 在 A″ 之后作废，见步 12）的
§15.1 两步冻结、§15.2 冻结条件（13 条）、§15.3 冻结审阅者清单、§15.4 输入文件与哈希；
以及 `docs/research_v4/prereg_v3_3_code_mapping.md`（下称"代码映射"）的 §4 逐条核验表（38 条）。
**本文件的角色**：把 §15.2 / §15.3 与代码映射 §4 落成"照着敲即可"的操作单，并给出**提交 A″ 的 message 模板**。
**权威性**：口径、阈值、判定规则一律以预注册为准；本文件只规定**执行顺序与命令**，**不新增任何研究口径**。

**写作时间**：2026-09-08（**freeze-A″ 一致性轮**）。
**核对基准 HEAD `42a1ca6`**；开工时工作树 `git status --porcelain` 只有 `?? artifacts` 一行。

**为什么是 A″**：v3.1 的两步冻结用了 **A / B**，v3.2 用了 **A′ / B′**
（`docs/research_v4/freeze_a_checklist.md`、`freeze_a2_checklist.md`、`label_freeze_b.md`、`label_freeze_b2.md`）。
v3.3 的两步记作 **A″**（正文与代码）与 **B″**（G-conf-2 标签）。

---

## 0. 两步冻结 + 两阶段解封里 A″ 是哪一步

| 步 | 提交 / 产物 | 冻结什么 | 之后允许做什么 |
|---:|---|---|---|
| **A″** | **正文与代码冻结提交** | 预注册正文 + §15.4 全表的代码 / 配置 / 文档 / 检验力产物；G-dev 标签 sha256（历史参照）；**G-conf-2 的封存哈希（已存在，§4.1a）** | 正常池冒烟；**G-conf-2 的文本标注**；§9.3 的数据门（`g_dev_data_gates.py`，**不碰路由**）。**G-dev 上不再跑任何决策性运行** |
| **B″** | **标签冻结提交** | `annotations/g_conf2/final_unblinded.jsonl` 等文件的 sha256（写进 `docs/research_v4/label_freeze_b3.md`）+ `g_conf2_data_gates.json` 的判定 + `packets/g_conf2/blindness_scan.json` 的零泄漏判定 | 此后才允许**阶段 1** |
| **M1a / M1b** | **两份阶段 1 的 `threshold_manifest.json`**（`stage1` 与 `stage1_strat`） | **每份都记两个哈希**：(i) 整文件 `sha256sum <path>`（外部凭据）与 (ii) 文件内的 `sha256` 字段（去掉该字段后的 canonical-JSON 自哈希）；另记 `stage1_attack_traces.{count, sha256}` | 此后才允许**阶段 2** |
| **M2** | **三次阶段 2 的 `result.json`** | 每次的 `inputs.threshold_manifest_sha256` 必须等于对应 M1 的 **(ii) 自哈希**，**不是 (i) 整文件哈希**；该项由阶段 2 的 `manifest_sha256` 检查机械强制 | 报告；**不允许任何补跑** |

**运行器的 `--freeze-commit` 指向 B″，不是 A″。**
A″ 与 B″ 之间**不允许**改动算法、阈值、状态规则、门、锚点、命中口径、Holm 族成员、`VAL1` 判据、S-J 分母。
若 A″ 之后必须改代码，**重做 A″** 并在 message 里说明。

> **三条只能由流程保证的纪律（代码保证不了；预注册 §16.3）**：
> 1. **两份阶段 1 各只跑一次**，manifest 的两个哈希**当场记录**（自哈希含 `created_at`，重跑必然换哈希）。
>    `refuse_existing_outputs` 只保证"同名重跑被拒绝"，换 `--run-name` 再跑代码不会拦。
> 2. **阶段 2 共 3 次**（D-3）。**没有任何机制按子集统计次数**——`refuse_existing_outputs` 只看本次 `--run-name`。
> 3. **不补跑 `--arm-hashes`。** G-conf-2 的逐臂哈希**已在封存之前算好并被 `SEALED.json` 的 `extra_files` 覆盖**
>    （`g_conf2_build_log.md` §7.1；这与 v3.2 裁定 Q6 针对 G-conf 的情形不同，见预注册 §3.4）。
>    A″ 之后再补一份没有凭据价值。

---

## 1. 逐步操作单

命令一律在仓库根目录执行。`$PY` = `/home/wzh/Agent-Moe-Research/.venv/bin/python`，
需要导包的命令前加 `PYTHONPATH=$PWD/src:$PWD/scripts`。

### 步 0 — 工作树干净、并发在制品已处理

```bash
git status --porcelain
git rev-parse HEAD
```

**期望**：除 `?? artifacts` 外，只剩本轮 A″ 要提交的文档改动
（见 §4 的"A″ 提交内容"表）。**并发的标注工作流**写在
`artifacts/agent_v2/dataset_g/annotations/g_conf2/` 与 `docs/research_v4/g_conf2_annotation_*.md` 下，
**不属于 A″**；提交时不要把它们卷进来。

### 步 1 — 注册测试集 + 全套测试

```bash
# 注册测试集（预注册 §15.2 第 4 / 12 / 13 条要核的六个类都在第一个文件里）
PYTHONPATH=$PWD/src:$PWD/scripts $PY -m pytest \
  tests/test_research_v4_v3_3.py tests/test_research_v4_v3_2_round2.py \
  tests/test_research_v4_v3_2.py tests/test_research_v4_freeze_fixes.py \
  tests/test_research_v4_prereg_v3_1.py tests/test_research_v4_data_gates.py \
  tests/test_research_v4_g_conf_seal.py -q

# 全套
PYTHONPATH=$PWD/src:$PWD/scripts $PY -m pytest tests/ -q
```

**期望**：全绿。**不写死条数**（预注册 §15.2 第 8 条）——把**当次**计数抄进提交 message。
**A″ 一致性轮实测（HEAD `42a1ca6`）**：注册测试集 **355 passed, 15 subtests**（115.5 s）；
全套 **1432 passed, 143 subtests**（154.9 s）。
逐文件：`v3_3` 83 / `v3_2_round2` 74 / `v3_2` 52 / `freeze_fixes` 42 / `prereg_v3_1` 57 /
`data_gates` 34 / `g_conf_seal` 13。

### 步 2 — 冻结的 OLMoE 侧未被碰（`verify_m_only`）

```bash
git diff --stat scripts/research_v3/verify_m_only_vs_frozen.py     # 必须为空
git diff --stat src/research_v2/trm3.py                            # 必须为空
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v3/verify_m_only_vs_frozen.py
```

**期望**：两个 `git diff --stat` 都为空；脚本输出
`scenario halves identical (all-target vs routine-only): True` 与
`[replica] vs frozen (identical): 0 trace(s) differ, 0 of them in the alarm SET`。

### 步 3 — v3.2 注册命令与 v3.1 §19.7 的回归（**逐字段不变**）

预注册 §13 的"A″ 回归条件"。做法与 `v3_2_harness_changes.md` §10.4 相同（不动工作树）。
**rev4 落地时已跑过一次**（`freeze_review_v3_3_resolution.md` §6.7 的两条回归）：

- **冻结的 v3.2 注册命令（`v3_2_a2_verify` 两阶段）从 `args` 逐字重放**：
  `threshold_manifest.json` **取值不同 0、丢失 0、新增 0**（`cell` 仍是那 18 个冻结键）；
  两个阶段的 `result.json` **取值不同 0、丢失 0**，新增 442（纯 additive）；
  **四个格的 `gates[]` 与 `comparison_anchored` 逐字节相同**，`verification.checks`
  **仍然正好 19 项、名字与顺序逐条相同、`ok == true`**。
- **round 3 → round 4 重跑**（只换 `--output-root`）：取值不同 5 个，**全是溯源项**；
  丢失 385（`F1_per_fold_binomial*`）、新增 510（`F1_per_fold_conformal*`）；
  **五个格的 `gates[]` 与 `comparison_anchored` 逐字节相同**。

**若在 A″ 上重跑**：读数必须与上面逐位一致；不同就停下来查原因，**不要**为了让数字对上去改任何参数。

### 步 4 — G-dev 两阶段读数复核（**不是重跑要求**）

rev4 的两次 G-dev 阶段 2 产物：

```bash
sha256sum artifacts/agent_v2/dataset_g/v3_3_dev/round4_conformal/stage2_hinf_nostrat_d1_z1/result.json \
          artifacts/agent_v2/dataset_g/v3_3_dev/round4_conformal/stage2_hinf_nkb_d1_z1/result.json \
          artifacts/agent_v2/dataset_g/v3_3_dev/round3_pins/stage1_hinf_nostrat_d1/threshold_manifest.json
```

**A″ 一致性轮实测（EXPLORATORY，不是判定）**：

| 量 | 值 | 读法 |
|---|---|---|
| 清单守卫 | **22 / 22 PASS**，`failed == []`，`manifest_version = "v3.2-2"` | `threshold_manifest.verification` |
| 清单钉子 | `cell_debounce` / `cell_top_m` / `cell_horizon_mode` 都在，`calibration_design.pinned.manifest_pins = ["debounce","horizon_mode","top_m"]` | 同上 + `calibration_design.pinned` |
| 五格 | `gates` 的键集 `["J","M","P","S","Z1"]` | `gates` |
| **F1 逐折（`Z1 · ∞`）** | 折 0 **8/95 ∈ [3, 18]**（`n_cal` 104、`rank` 10、`band_source = "exact"`）；`all_in_band = true`；`joint_null_pass = 0.8922747384922635` | `gates.Z1.F1_per_fold_conformal*` |
| **F1 逐折（分层格）** | 折 0 **9/95**、`rank = null`、`band_source = "mc"`、`in_band = true`；`joint_null_pass = 0.9038655439632` | 同上（`stage2_hinf_nkb_d1_z1`） |
| **`VAL1_a_exact`** | 三层 `in_band` 全 `true`；`VAL1_a_all_in_band = true`、`VAL1_a_joint_null_pass = 0.91675`、`VAL1_a_seed = 0`、`VAL1_a_reps = 200000`、`VAL1_a_numpy_version = "2.5.2"`、`VAL1_a_error = null` | `gates.Z1.VAL1_a_*` |
| `F1_per_fold_binomial*` | **一个都不存在**（rev4 已整块删除） | 反向核对 |
| 无界视界 | `horizon.mode = "unbounded"`、`n3_gate = "n/a"`、`rule_H = 113`、`H_effective = 552`、`censored_paths = 0` | `cells.Z1.folds[k].horizon` |

**产物 sha256（A″ 上实算）**：

```
5c399016790ac341e6de4dd33fa3bcd040f8436c175d6ed2b753d5ca9f3b1969  v3_3_dev/round4_conformal/stage2_hinf_nostrat_d1_z1/result.json
500044a11d3a099ca662676f424f3e052a3fdd840ffaa57ac197468dbf0a4ad3  v3_3_dev/round4_conformal/stage2_hinf_nkb_d1_z1/result.json
99cf40c211df49f05c3a593fb8360d2c5264040610a28f0f2914c2af48cb1646  v3_3_dev/round3_pins/stage1_hinf_nostrat_d1/threshold_manifest.json
```

### 步 5 — 检验力表复核（不读任何数据）

预注册 §8.2 的注册命令（`--config configs/dataset_g/g_conf2.json`，4000 重复 × 2000 bootstrap，seed 20260907）。

```bash
sha256sum artifacts/agent_v2/dataset_g/prereg_power_v3_3/main/power_sim.json \
          artifacts/agent_v2/dataset_g/prereg_power_v3_3/main/power_sim.md
```

**A″ 实测**：`a4f32700956407f33b402cf790b72143b4ed4d775f3eb09ec8f747ad96ed67b2` /
`abcd8927c5f5c2c631b604eacf9c81a389f631534aeee69f5fb7a5982d3d7713`（与预注册 §15.4 逐位相同）。

> **⚠ 与 A′ 同一条限定**：`power_sim.json` 含 `created_at` / `elapsed_seconds`、`power_sim.md` 第 3 行含
> "Generated by … on \<时间戳\>"，**重跑的文件哈希按构造必然不同**。
> §15.4 的两行 sha256 是**冻结产物的凭据**，不是**可复现性判据**。
> **重跑一律写到 scratchpad 的临时目录**，不要覆盖 `artifacts/.../prereg_power_v3_3/`。
> **判据是逐格数值一致**，不是文件哈希一致。
>
> **三个默认值陷阱（必须显式传）**：`--config` 默认 `g_dev.json`；`--n` 默认 107/150/177/264；`--delta` 默认 0–0.20。
> **§8.4（Holm 成员 S1）的表不能由这个 CLI 复现**（它没有单样本"下界 > null"模式），
> 该表由 `trm3_g.cluster_bootstrap_rate` 的同一模型算出。

### 步 6 — 哈希逐行重算

```bash
# 文档（A″ 上改过的取工作树，其余取 HEAD 的 git 对象）
sha256sum docs/research_v4/detector_prereg_v3_3.md \
          docs/research_v4/prereg_v3_3_code_mapping.md \
          docs/research_v4/freeze_a3_checklist.md \
          docs/research_v4/g_conf2_build_log.md \
          docs/research_v4/v3_3_dev_measurements.md \
          docs/research_v4/freeze_review_v3_3_resolution.md \
          docs/research_v4/freeze_review_v3_3_code.md \
          docs/research_v4/freeze_review_v3_3_data.md \
          docs/research_v4/freeze_review_v3_3_statistics.md \
          docs/research_v4/g_conf_confirmatory_report.md \
          docs/research_v4/technical_report_gpt_oss.md

for f in docs/research_v4/detector_prereg_v3_2.md docs/research_v4/detector_prereg_v3_1.md \
         docs/research_v4/prereg_v3_2_code_mapping.md docs/research_v4/freeze_a2_checklist.md \
         docs/research_v4/gpt_oss_research_program.md docs/research_v4/agent_v3_dataset_design.md \
         docs/research_v4/detector_harness_g.md docs/research_v4/v3_2_design_note.md \
         docs/research_v4/v3_2_harness_changes.md docs/research_v4/zoom_v32_improvement_space.md \
         docs/research_v4/sonnet_annotation_pilot.md docs/research_v4/g_dev_v3_2_development_report.md \
         docs/research_v4/attack_annotation_guideline.md docs/research_v4/attack_annotation_examples.md \
         docs/research_v4/annotation_schema_1_1_changes.md docs/research_v4/normal_annotation_guideline.md \
         docs/research_v4/label_freeze_b2.md docs/research_v4/g_conf_unsealing_run_log.md; do
  printf '%-58s %s\n' "$f" "$(git show HEAD:$f | sha256sum | cut -d' ' -f1)"
done

# 代码 + 配置（取 HEAD 的 git 对象）
for f in src/research_v2/trm3.py src/research_v2/trm3_g.py src/research_v2/io_g.py \
         scripts/research_v4/run_detectors_g.py scripts/research_v4/g_conf_seal.py \
         scripts/research_v4/g_dev_data_gates.py scripts/research_v4/prereg_power_sim.py \
         scripts/research_v3/verify_m_only_vs_frozen.py \
         tests/test_research_v4_v3_3.py tests/test_research_v4_v3_2_round2.py \
         tests/test_research_v4_v3_2.py tests/test_research_v4_freeze_fixes.py \
         tests/test_research_v4_prereg_v3_1.py tests/test_research_v4_data_gates.py \
         tests/test_research_v4_g_conf_seal.py \
         configs/dataset_g/g_conf2.json configs/dataset_g/agent_g_conf2.json \
         configs/dataset_g/fixtures/kb_g_conf2.json configs/dataset_g/fixtures/records_g_conf2.json \
         configs/dataset_g/manifest.json configs/dataset_g/g_conf.json configs/dataset_g/g_dev.json; do
  printf '%-58s %s\n' "$f" "$(git show HEAD:$f | sha256sum | cut -d' ' -f1)"
done

# 15.4.4 的"凡在 A″ 上存在的 scripts/research_v4/*.py 与 tests/test_research_v4_*.py 都必须有一行"
for f in $(git ls-tree -r --name-only HEAD -- scripts/research_v4 tests \
           | grep -E '^(scripts/research_v4/.*\.py|tests/test_research_v4_.*\.py)$' | sort); do
  printf '%-58s %s\n' "$f" "$(git show HEAD:$f | sha256sum | cut -d' ' -f1)"
done

# artifacts（不进 git，取工作树）
sha256sum artifacts/agent_v2/dataset_g/prereg_power_v3_3/main/power_sim.json \
          artifacts/agent_v2/dataset_g/prereg_power_v3_3/main/power_sim.md \
          artifacts/agent_v2/dataset_g/h_rule/h_rule_g.json \
          artifacts/agent_v2/dataset_g/g_conf2/SEALED.json \
          artifacts/agent_v2/dataset_g/g_conf2/ARM_HASHES.json \
          artifacts/agent_v2/dataset_g/packets/g_conf2/packet.jsonl \
          artifacts/agent_v2/dataset_g/packets/g_conf2/review_packet.jsonl \
          artifacts/agent_v2/dataset_g/packets/g_conf2/blindness_scan.json \
          artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl \
          artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl \
          artifacts/agent_v2/dataset_g/g_conf/SEALED.json
```

**期望**：与 §4 的 A″ 表逐行相同。**不同即先改预注册再冻结**（预注册 §15.3 / v3.1 §15.3 第 16 条）。

### 步 7 — G-conf-2 的封存复验（**只读**）

```bash
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/g_conf_seal.py --verify \
    artifacts/agent_v2/dataset_g/g_conf2/SEALED.json
```

**期望**（三条断言，不是逐字 JSON）：`verified == true`、`mismatches == []`、`trace_count == 720`。
**把执行时刻手写进运行日志**（`--verify` 无机读时间戳）。
**注意**：参数是 `SEALED.json` 这个**文件**，不是它所在的目录（传目录会 `IsADirectoryError`）。

> **A″ 之前不要对 G-conf-2 跑 `variant_census` / `g_dev_yields.py` 之外的任何东西**，
> 更不要打开任何 `steps/*.safetensors`。本操作单只读 `SEALED.json` 与 `ARM_HASHES.json`。

### 步 8 — 代码映射 §4 的 38 条逐条过一遍

```bash
# 举例：第 19 条（两条精确带已落地、二项带已删）
PYTHONPATH=$PWD/src $PY -c "
from research_v2 import trm3_g
print(hasattr(trm3_g,'conformal_far_band'), hasattr(trm3_g,'pooled_stratum_far_band'),
      hasattr(trm3_g,'binomial_far_band'),
      'conformal_far_band' in trm3_g.__all__, 'pooled_stratum_far_band' in trm3_g.__all__,
      'binomial_far_band' in trm3_g.__all__)"
# -> True True False True True False

# 举例：第 18 / 20 / 21 条（清单钉子、F1 逐折行、VAL1 行）
PYTHONPATH=$PWD/src:$PWD/scripts $PY - <<'PY'
import json
R = json.load(open('artifacts/agent_v2/dataset_g/v3_3_dev/round4_conformal/stage2_hinf_nkb_d1_z1/result.json'))
v = R['threshold_manifest']['verification']
print('checks', len(v['checks']), 'failed', v['failed'], 'ok', v['ok'])
print([c['check'] for c in v['checks'] if c['check'].startswith('cell_')])
b = R['gates']['Z1']
print('F1 all_in_band', b['F1_per_fold_conformal_all_in_band'],
      'joint', b['F1_per_fold_conformal_joint_null_pass'])
print('VAL1', b['VAL1_a_all_in_band'], b['VAL1_a_joint_null_pass'], b['VAL1_a_seed'], b['VAL1_a_reps'])
print('no binomial keys:', [k for k in b if 'binomial' in k] == [])
PY

# 举例：第 24 条（§12.2 的五条注册命令逐字可解析；不跑任何检测器）
#   见代码映射 §7：脚本把命令块从预注册里正则抽出来再喂给 run_detectors_g._args
```

**期望**：见代码映射 §4 的"期望"列，逐条对。

### 步 9 — 预注册本身的 sha256

```bash
sha256sum docs/research_v4/detector_prereg_v3_3.md
```

**这个值不写进预注册本身**（自指不可能收敛，预注册 §15.2 第 1 条）。它有且只有**三个**权威副本：

1. **冻结提交 A″ 的 message**（见 §3 的模板）；
2. **本文件 §4 的"冻结记录"节**；
3. **`docs/research_v4/freeze_review_v3_3_resolution.md` §6.9**（取代 §6.8 的草稿哈希）。

运行时由 `--prereg-sha256 <值>` 传给 `run_detectors_g.py`，落进每个 `result.json` 的 `prereg.sha256`。

**`--prereg-path` 的口径（与 A′ 相同，不变）**：

```bash
grep -n 'PREREG_PATH' scripts/research_v4/run_detectors_g.py     # 仍是 detector_prereg_v3_1.md（:83）
grep -rn 'PreregPathTest' tests/                                  # 仍钉 v3.1，A" 不改
PYTHONPATH=$PWD/src:$PWD/scripts $PY -m pytest \
  tests/test_research_v4_v3_2_round2.py -k PreregPathFlagTest -q  # 4 passed
grep -n -- '^  --prereg-path docs/research_v4/detector_prereg_v3_3.md' \
  docs/research_v4/detector_prereg_v3_3.md                        # 恰好 2 行（阶段 1a 与运行 2a）
```

**A″ 一致性轮实测**：`PREREG_PATH` 仍是 v3.1 文件；最后一条 `grep` 命中 **2 行**（第 1497、1563 行）。

### 步 10 — 做提交 A″

按 §3 的模板写 message，`git add` 步 0 处理完的工作树，提交。
**提交后不要再改这些文件**；任何改动 = 重做 A″。

### 步 11 — 守卫真的有牙（提交 A″ 之后验一次）

```bash
# 冒烟不能碰封存批
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/run_detectors_g.py \
  --stage calibrate --normal-only-smoke --cal-from-target --outputs none \
  --target artifacts/agent_v2/dataset_g/g_conf2
# -> 非零退出，信息里含 --normal-only-smoke / SEALED 与该目录，不产生任何文件

# 错的 --freeze-commit -> SystemExit
# 改一个字节的 manifest -> --stage score 必须 SystemExit
# 同一份清单跑 --debounce 2 -> 必须被 cell_debounce 硬拒绝
```

**期望**：四次都非零退出。

### 步 12 — 草稿文件作废（**A″ 的最后一步**）

冻结候选的正文文件名是 **`docs/research_v4/detector_prereg_v3_3.md`**；
它的草稿前身 **`docs/research_v4/detector_prereg_v3_3_draft.md`** 在提交 A″ 之后**作废**。

**做什么**：
1. 提交 A″ 的 message 里写明"草稿作废"（模板已含该段）；
2. **草稿留在 git 历史里**（rev1–rev4 的演进是审阅链的一部分，**不删除历史**）；
   本轮用 `git mv` 改名，因此工作树上不再有草稿文件；
3. 此后**任何**引用一律指 `detector_prereg_v3_3.md`：

```bash
grep -rn 'detector_prereg_v3_3_draft' docs/ scripts/ src/ tests/ \
  | grep -v '作废\|SUPERSEDED\|历史\|改名\|审阅当时\|retired\|supersede'
```

**期望**：无输出（或只剩明确标注为历史的行）。
**A″ 一致性轮实测**：剩下的全部命中都在**审阅报告的"审阅对象"行**与
`freeze_review_v3_3_resolution.md` 的 rev2 / rev3 / rev4 **哈希记录**里，
四份审阅报告都已加"**A″ 之后该草稿改名为 `detector_prereg_v3_3.md`**"的历史说明行。
**这一步之后，`detector_prereg_v3_3.md` 是唯一权威正文**；对它的任何改动 = 新 proposal（预注册 §15.5）。

---

## 2. claim → verified how → result（提交 A″ 时逐行填 result）

| # | claim | verified how | result（A″ 一致性轮 2026-09-08，HEAD `42a1ca6`） |
|---:|---|---|---|
| 1 | 工作树干净、HEAD 明确 | 步 0 | 开工时只有 `?? artifacts`；提交前只余本轮文档改动 |
| 2 | 注册测试集 + 全套测试全绿 | 步 1 | **355 passed / 15 subtests**；全套 **1432 passed / 143 subtests** |
| 3 | 冻结的 OLMoE 侧一字未动 | 步 2 | 待组长在 A″ 上执行 |
| 4 | v3.2 注册命令与 v3.1 §19.7 回归逐字段不变 | 步 3 | rev4 落地时已验（`freeze_review_v3_3_resolution.md` §6.7）：`gates[]` 与判定数逐字节相同 |
| 5 | **22 项**清单守卫全 PASS、三项钉子在场 | 步 4 / 步 8 | `len(checks) == 22`、`failed == []`、`ok == true`（两份 round-4 产物） |
| 6 | 检验力表哈希与正文一致 | 步 5 | 两行 sha256 与 §15.4 逐位相同 |
| 7 | §15.4 / §4.1a 全表哈希逐行相同 | 步 6 | **全部重算过，逐位相同**（见 §4 的表） |
| 8 | G-conf-2 的封存可复验 | 步 7 | `SEALED.json` sha256 `e788bc09…`；`--verify` 待组长在 A″ 上执行一次并记录时刻 |
| 9 | 代码映射 §4 的 **38** 条 | 步 8 | **本轮已核完可离线核的全部**（开关存在性与拼写、产物键、测试类、哈希、折大小、载攻击数、注册命令解析、两条精确带的落地）；**需要在 A″ 上执行的留给组长**：全部**反向测试**（`--expect-h 352` 判 FAIL、`--top-m` 拉满、错清单、`--debounce 2`）、`g_conf_seal.py --verify`、v3.1/v3.2 回归重跑、`verify_m_only`、检验力重跑，以及一切只能从**真实 G-conf-2 运行产物**上读的字段 |
| 10 | **`--prereg-path` 存在、默认值仍是 v3.1 冻结正文、§12.2 的命令都带它** | 步 9 | `PREREG_PATH` 仍是 v3.1；`grep` 命中 2 行 |
| 11 | 预注册 sha256 有三个权威副本 | 步 9 + 步 10 | 已写入本文件 §4 与 `freeze_review_v3_3_resolution.md` §6.9；第三份在 A″ 的 message |
| 12 | 守卫有牙 | 步 11 | 待组长在 A″ 之后验 |
| 13 | **草稿文件已作废、正文以 `detector_prereg_v3_3.md` 为准** | 步 12 | `git mv` 已完成；全仓库引用已改 |
| 14 | **§13.1 的 15 条缺口都有明确状态** | 代码映射 §5 | 2 条已闭合（第 8 / 15）、1 条已改写（第 13）、其余 12 条维持"不改代码 + 正文如实声明" |
| 15 | **组长裁定 R-A / R-B 已逐字记录** | 预注册 §4.1a + `g_conf2_build_log.md` §11 | 两处逐字，落点写进 §16.1 第 27 / 28 条 |
| 16 | **G-conf 的逐臂哈希已复核** | 代码映射 §6 | **零 mismatch**；并更正了构建日志 §7.1 / §8 第 2 条的前提（G-conf 从来没有 `ARM_HASHES.json`） |

---

## 3. 冻结提交 A″ 的 message 模板

```
prereg: freeze A" -- detector preregistration v3.3 (dataset G-conf-2 / gpt-oss-20b / Agent v3)

Freezes the v3.3 preregistration text and the code it names.  This is step A" of the
two-step freeze (A" text+code, B" G-conf-2 labels) plus two-stage unsealing (M1a/M1b
manifests, M2 scores).  The runner's --freeze-commit points at B", not at this commit.

Preregistration sha256 (the authoritative copy; the file does not contain its own hash):
  docs/research_v4/detector_prereg_v3_3.md
  3f9063c1107f5df3f0294ca811ce764965ae2b5bc448d08a38aa03f967d21467

The draft it supersedes, docs/research_v4/detector_prereg_v3_3_draft.md, is retired by this
commit (git mv): it stays in git history, and from A" on the frozen file above is the only
authority.

Companion documents frozen with it:
  docs/research_v4/prereg_v3_3_code_mapping.md   c5983f62199e1d7624f51320ac9fa511c9aed8ca623612a86167f8e317707023
  docs/research_v4/freeze_a3_checklist.md        <CHECKLIST_SHA256 -- recompute after filling section 4>

Code frozen with it (prereg 15.4; recomputed on this commit, all unchanged since 42a1ca6):
  src/research_v2/trm3.py                        eaa188646369f2c1b77e24093f1e2cf68ae9ba3e3004595350e6fcb7d74d34bf
  src/research_v2/trm3_g.py                      0af16547c286eed75158a97d3734081d62b7e0aa6383a3fa9aa3736650b2c75d
  src/research_v2/io_g.py                        ccaff8fd1c0b83ab582807603135f04b34a98b1e38c6d012e59f8b36e27f1207
  scripts/research_v4/run_detectors_g.py         9577ae61bb918a876ed5abd604aa12ae746e24619fd45f04d0b571f8d230b98c
  scripts/research_v4/g_conf_seal.py             a7926dd9891846b610a5f395041a5b6aa28b0b3c4dbc06be4c60b043f38e705e
  scripts/research_v4/g_dev_data_gates.py        cee07525ee8c9bb9bcedfd0c5f1d66f1a64da706c0a03a45e953ddf566cf25a3
  scripts/research_v4/prereg_power_sim.py        83d3f0b5b0b28306f84ec8574fa06349007dfa70cfe4dbbd22a37f06c3455fb0
  scripts/research_v3/verify_m_only_vs_frozen.py 30dc93dce959ac7ff12dd69eb9c2579173d5a37ceb4244e75c9ea908f5d66623
  tests/test_research_v4_v3_3.py                 5d0a3f6f2a5c8f43f59c24406da553f1538b6d4d5f12b585ffd1bd32b2639878
  configs/dataset_g/g_conf2.json                 0614908c925087e3e1724b2109712ecad3519339731c3e23bb20f6e13bcca3ce
  configs/dataset_g/manifest.json                0877f9eb275739fafb7d24febd80d852cda2d016cab6c4c206918c5369a8f2f7
  (the remaining scripts/research_v4/*.py and tests/test_research_v4_*.py rows are in the
   checklist section 4 and were recomputed unchanged on this commit)

What this preregistration claims: ONE confirmatory hypothesis H1 on G-conf-2 -- the
X-anchored hit rate of Z1 (top-1 rare-coordinate concentration, H = inf) exceeds that of P
at a matched measured FAR, decided by the conjunction of (family-clustered 95% percentile CI
lower bound > 0) AND (exact McNemar).  Holm family m = 2 (S1 absolute hit-rate lower bound,
S2 Z1 vs S).  VAL1 (n_kb-stratified reference) and S-J (injection presence) are separately
registered claims, not in the Holm family.

Verification recorded at freeze time:
  tests                    355 passed / 15 subtests (registered set of 7 files)
                           1432 passed / 143 subtests (pytest tests/ -q)
  manifest guards          22/22 PASS, failed == [], including the three R-C1 cell pins
  exact bands (rev4)       trm3_g.conformal_far_band + pooled_stratum_far_band present and
                           in __all__; binomial_far_band absent; F1_per_fold_conformal* and
                           VAL1_a_* on disk; VAL1_a_seed 0 / VAL1_a_reps 200000
  registered commands      all five section 12.2 blocks extracted verbatim from the prereg
                           and parsed through run_detectors_g._args -- no detector was run
  hash table               prereg 4.1a and 15.4, every row recomputed on this commit
  G-conf-2 seal            SEALED.json e788bc09..., sealed 2026-09-08T14:18:22.129190Z,
                           720 traces / 888 episodes (clean 280/336, benign_control 280/336,
                           attack 160/216), 160 attack-bearing episodes, fold sizes under
                           fixture_rank_mod recomputed as 218 / 238 / 216
  G-conf arm hashes        recomputed read-only over G-conf's 720 trace.json files: normal
                           union aa27366f... and attack bdf4ac51... reproduce the stage-1
                           manifest and the attack census bit for bit, zero mismatch.  G-conf
                           never had an ARM_HASHES.json (lead ruling Q6) -- the build log's
                           g_conf_meta/ premise is corrected in g_conf2_build_log.md 12

Lead rulings recorded verbatim in prereg 4.1a and g_conf2_build_log.md 11:
  R-A  the manifest self-hash change is an append-only extension (22 -> 26), not a broken seal
  R-B  two benign_control tool-result injections were never delivered; both episodes stay in
       the pool as negatives; "benign_control saw an external note" statements use 166

Development readings frozen as priors (G-dev, EXPLORATORY -- NOT confirmation):
  Z1 . H = inf: hits 114/126, far.filtered 24/293, far.all 31/408, Delta-hat 0.3254.
  F1 per-fold conformal: all three folds in band, joint null pass 0.8923.
  VAL1 (a): all three strata in band, joint null pass 0.9167.
  Gates F3 / N1 remain PREDECLARED failures; F4's prior failure probability is 16-47%.

Data discipline: no G-conf-2 routing was read while writing or freezing this.  The only
G-conf-2 bytes touched are configs/dataset_g/g_conf2.json (metadata), SEALED.json,
ARM_HASHES.json and the packet files -- digests only.  The one trace.json read of the round
was the read-only G-conf arm-hash recomputation (metadata, no routing shard).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

## 4. 冻结记录（提交 A″ 时填入）

### 4.1 A″ —— 正文与配套文档

| 项 | 值 |
|---|---|
| 提交 A″ 的 sha | **62f2f9fb964781d98ddd32f125be4ac379f229ea** |
| 提交时间 | **2026-09-08T08:11:14-07:00** |
| `docs/research_v4/detector_prereg_v3_3.md` 的 sha256 | **`3f9063c1107f5df3f0294ca811ce764965ae2b5bc448d08a38aa03f967d21467`** |
| `docs/research_v4/prereg_v3_3_code_mapping.md` 的 sha256 | `c5983f62199e1d7624f51320ac9fa511c9aed8ca623612a86167f8e317707023` |
| `docs/research_v4/freeze_a3_checklist.md` 的 sha256 | **本记录填入后文件哈希改变，以 A″ 提交对象为准**（`git show <A″>:docs/research_v4/freeze_a3_checklist.md \| sha256sum`） |
| 测试计数（当次） | 注册集 **355 passed, 15 subtests**；全套 **1432 passed, 143 subtests** |
| 核对基准 HEAD | `42a1ca6`（A″ 提交只改文档，代码行不变） |

**A″ 提交内容（本轮改动的文件）**

| 文件 | 改动 | 工作树 sha256 |
|---|---|---|
| `docs/research_v4/detector_prereg_v3_3.md` | **`git mv` 自 `..._draft.md`** + 10 处文档侧订正 + §4.1a / §16.1 第 27–28 条新增 | `3f9063c1107f5df3f0294ca811ce764965ae2b5bc448d08a38aa03f967d21467` |
| `docs/research_v4/prereg_v3_3_code_mapping.md` | **新文件**（代码映射，§15.2 第 6 条） | `c5983f62199e1d7624f51320ac9fa511c9aed8ca623612a86167f8e317707023` |
| `docs/research_v4/freeze_a3_checklist.md` | **新文件**（本操作单） | 填入后重算 |
| `docs/research_v4/g_conf2_build_log.md` | 追加 §11（R-A / R-B 逐字）与 §12（G-conf `ARM_HASHES` 前提更正 + 复核结果） | `96a674a540b6e6503333e6dca9a098e70302693bac1099f07937f7ff1f7e2931` |
| `docs/research_v4/freeze_review_v3_3_resolution.md` | 追加 §6.9（A″ 的最终 sha256 + 改名记录） | 填入后重算 |
| `docs/research_v4/v3_3_dev_measurements.md` | 一处引用改指冻结文件名 | `3c3d2c1eeb7d1346bedfbb6b711f0d9eb4e2a101a5114d23a2e736e37a03b911` |
| `docs/research_v4/freeze_review_v3_3_code.md` | 加一行"草稿已改名"的历史说明 | `80bfebc3e545d05dbf149cde34811f744ff03d5491089313e6cda064dbba1ee2` |
| `docs/research_v4/freeze_review_v3_3_data.md` | 同上 | `0f00ebb35fb83c6c58ae2bea22b189101b21440ccdd2909928babd5fbc7946cc` |
| `docs/research_v4/freeze_review_v3_3_statistics.md` | 同上 | `5772cf79492bdd3ee27234155928384650ae9191b43a68ec50441e7cb1467543` |
| `docs/research_v4/g_conf_confirmatory_report.md` | 勘误里的一处文件名引用 | `25cdde4471ce7677d1a4008d1a47d37578e7d793d756db0acda11ac117c0fafc` |
| `docs/research_v4/technical_report_gpt_oss.md` | 同上 | `13885c4b88340707e853d90778f3158573d523d6370a7c718ac6fa443f04343f` |

**代码 / 配置（A″ 上 `git show HEAD:` 重算，全部与 `42a1ca6` 相同）**

| 文件 | sha256 |
|---|---|
| `src/research_v2/trm3.py` | `eaa188646369f2c1b77e24093f1e2cf68ae9ba3e3004595350e6fcb7d74d34bf` |
| `src/research_v2/trm3_g.py` | `0af16547c286eed75158a97d3734081d62b7e0aa6383a3fa9aa3736650b2c75d` |
| `src/research_v2/io_g.py` | `ccaff8fd1c0b83ab582807603135f04b34a98b1e38c6d012e59f8b36e27f1207` |
| `scripts/research_v4/run_detectors_g.py` | `9577ae61bb918a876ed5abd604aa12ae746e24619fd45f04d0b571f8d230b98c` |
| `scripts/research_v4/g_conf_seal.py` | `a7926dd9891846b610a5f395041a5b6aa28b0b3c4dbc06be4c60b043f38e705e` |
| `scripts/research_v4/g_dev_data_gates.py` | `cee07525ee8c9bb9bcedfd0c5f1d66f1a64da706c0a03a45e953ddf566cf25a3` |
| `scripts/research_v4/prereg_power_sim.py` | `83d3f0b5b0b28306f84ec8574fa06349007dfa70cfe4dbbd22a37f06c3455fb0` |
| `scripts/research_v3/verify_m_only_vs_frozen.py` | `30dc93dce959ac7ff12dd69eb9c2579173d5a37ceb4244e75c9ea908f5d66623` |
| `tests/test_research_v4_v3_3.py` | `5d0a3f6f2a5c8f43f59c24406da553f1538b6d4d5f12b585ffd1bd32b2639878` |
| `tests/test_research_v4_v3_2_round2.py` | `722fc0f923afa4ea9b005a7303ae0110ec532479dd06ff6bf400154f47f0a01f` |
| `tests/test_research_v4_v3_2.py` | `4bffcf17fda8bf795d8f296341a02b9079d3d310f70034bd4b28661fa3b2682e` |
| `tests/test_research_v4_freeze_fixes.py` | `3ac847a951e931353acd6f1a140eb75e6ac985aab53b1b1e5e75fb6f6a73575e` |
| `tests/test_research_v4_prereg_v3_1.py` | `b3c2345cd5cdfde8b7f3f1ec47f905dc3898ef69dda83affbc52a30383718430` |
| `tests/test_research_v4_data_gates.py` | `aac33699e8b465e59451464e6a93946f142ff8c65efe732142a4caefe1610109` |
| `tests/test_research_v4_g_conf_seal.py` | `a59349155c9f3d651195ec027f787cd2c0362376acbd9b737ef534a5be9dbbd0` |
| `configs/dataset_g/g_conf2.json` | `0614908c925087e3e1724b2109712ecad3519339731c3e23bb20f6e13bcca3ce` |
| `configs/dataset_g/agent_g_conf2.json` | `6ef85f35f4eabdcbd1b2c89ce00d53cbd67186f578de97f7c1e05f96c34b4d77` |
| `configs/dataset_g/fixtures/kb_g_conf2.json` | `0d45badfb5ce55ce5e4290040266d1a01996418a66c3f47e1b25f09260e319dc` |
| `configs/dataset_g/fixtures/records_g_conf2.json` | `006c199f8ae5ff9c0e7403c94dacc1e7fab08af2691664e938d9fb7b136a3d93` |
| `configs/dataset_g/manifest.json` | `0877f9eb275739fafb7d24febd80d852cda2d016cab6c4c206918c5369a8f2f7` |
| `configs/dataset_g/g_conf.json` | `62728c5cbedda7c8c1b096c1c92cd9b4785265188f14ddaed1cb55b58294ec73` |
| `configs/dataset_g/g_dev.json` | `11b36e911431902ef4e82f1d9dd66993c3e25de17b0c4d14bdfb970dcc194b7b` |

> **`g_conf2_build_log.md` §8 第 7 条已解决**：四个新配置与新 manifest **都已在 `42a1ca6` 进 git**
> （上表的 `git show HEAD:` 值与磁盘值逐位相同），G-conf-2 的冻结输入在版本库里。

**继承的冻结文档（`git show HEAD:` 重算，A″ 上未改动）**

| 文件 | sha256 |
|---|---|
| `docs/research_v4/detector_prereg_v3_2.md` | `b717950012c2d849ba9e9b15f4296fd17e8821b67c34eabe8f5effbc457d88eb` |
| `docs/research_v4/detector_prereg_v3_1.md` | `f9351639d93877539ef5e481293eac0951f55ce5c14a4fc4e91c47649168e2c7` |
| `docs/research_v4/prereg_v3_2_code_mapping.md` | `b0e1a151a995039be32835db28b8f4d03a7db85081c28880f731c6db1a5d4092` |
| `docs/research_v4/freeze_a2_checklist.md` | `689c4ff8a3405de2037cc8fb6a6192f45d194cf76a971c42f1ec048d34b76c6c` |
| `docs/research_v4/gpt_oss_research_program.md` | `debbf1c6defdcbf5342ec3e24584e139ef982cfefd62dfa9379172493206706b` |
| `docs/research_v4/agent_v3_dataset_design.md` | `1dcbbb7bcd7de2eab4ef9081fe37dd3dbfe77108594aa88d807d99814305821a` |
| `docs/research_v4/detector_harness_g.md` | `4b5cdf991da36119c6a2616e82282c121299e1409e21f77c05208d08853ea24f` |
| `docs/research_v4/v3_2_design_note.md` | `5346cd0ced83335beb1160dae78e5f9ca1ebf2de95309743ed82cffb26baeb2f` |
| `docs/research_v4/v3_2_harness_changes.md` | `ac3b1099075a1b9397d46a1f37ea2ef0ef8a85061d994308b1cd21cda8bb5d1a` |
| `docs/research_v4/zoom_v32_improvement_space.md` | `4e8a4de8a62e7a5fbd2537ad4188c75179cb9cf2040d3e0dd8d8ddc2b88ad53f` |
| `docs/research_v4/sonnet_annotation_pilot.md` | `ae82b690b3575d2cb0587352d9a7557fd2aba2c2c54aa79fe8c802abffa3a8c2` |
| `docs/research_v4/g_dev_v3_2_development_report.md` | `a35aebe8f5ca2baec395a0332f1b01e516ceb9d3e61a3c613d7ef8d9ac29fe7f` |
| `docs/research_v4/attack_annotation_guideline.md` | `958291b7b3ce3be64191e8fe3f3b1d0d245576b465fd03da497b9512dd0db32e` |
| `docs/research_v4/attack_annotation_examples.md` | `0e152c606c92eb8ed1130a44c0237f9c2af20a5c75e9c5547de73cb606d3c1d7` |
| `docs/research_v4/annotation_schema_1_1_changes.md` | `2173a666a496797868c6e5cc6fc4cd91e9e8cdd9162febe70801ebb19ac6df71` |
| `docs/research_v4/normal_annotation_guideline.md` | `0bb55523bfb50e47e30779069e7201015f101ff6d87b21c4281a56a7a50bbcf8` |
| `docs/research_v4/label_freeze_b2.md` | `201076adffaaf2988f3a0f6cb8c7b614e002a82cb68ebf75d7a07a7c65258350` |
| `docs/research_v4/g_conf_unsealing_run_log.md` | `f1e095affc81c35a73a0a08c29cba9502d2ff13ba03fefaeb9f5dec88878a66d` |

**artifacts（不进 git，A″ 上取工作树）**

| 文件 | sha256 |
|---|---|
| `prereg_power_v3_3/main/power_sim.json` | `a4f32700956407f33b402cf790b72143b4ed4d775f3eb09ec8f747ad96ed67b2` |
| `prereg_power_v3_3/main/power_sim.md` | `abcd8927c5f5c2c631b604eacf9c81a389f631534aeee69f5fb7a5982d3d7713` |
| `h_rule/h_rule_g.json` | `1ec856837043aebec844b4003dcdc935c1b418320346f74499b124b6d1f8ef26` |
| **`g_conf2/SEALED.json`** | **`e788bc0908bb64f610ae9b24777bc42558f29e2434e939ef4be2418a44ddf5fc`** |
| **`g_conf2/ARM_HASHES.json`** | **`418000a6d742c6d6876f22cf83d7d5614b38d3e8d8c205cdf552831e72ec1e97`** |
| `packets/g_conf2/packet.jsonl` | `2aa197d37a7758baaed12ed4d22fabfcc4f25722d1194b16f8465b2879251866` |
| `packets/g_conf2/review_packet.jsonl` | `3f2b4fd5b71964af7b57fca7ad5ae34cb7fc38707a8dc02956b90f5a8ca3117a` |
| `packets/g_conf2/blindness_scan.json` | `4b5532b89890625e4ab3c52d5df6cd7b3d111266d756ef0c1a3f54c352d90d7c` |
| `annotations/g_dev/final_unblinded.jsonl`（历史参照） | `14ebd9d007157e73ef723ad54e8c1614b997b0eaff347833d735951f2bce61ea` |
| `annotations/g_conf/final_unblinded.jsonl`（B′ 冻结值，历史参照） | `09caa466022179e7288ed2e18d18aae3a53b0d4414d35eb3d1a6b3a04cd0f8a0` |
| `g_conf/SEALED.json`（历史参照） | `1d6a30e03724536eabf73493bb4db1baaf79a802ecd3298c758e7e9ae20899e7` |
| `v3_3_dev/round4_conformal/stage2_hinf_nostrat_d1_z1/result.json` | `5c399016790ac341e6de4dd33fa3bcd040f8436c175d6ed2b753d5ca9f3b1969` |
| `v3_3_dev/round4_conformal/stage2_hinf_nkb_d1_z1/result.json` | `500044a11d3a099ca662676f424f3e052a3fdd840ffaa57ac197468dbf0a4ad3` |
| `v3_3_dev/round3_pins/stage1_hinf_nostrat_d1/threshold_manifest.json` | `99cf40c211df49f05c3a593fb8360d2c5264040610a28f0f2914c2af48cb1646` |

### 4.2 续表 · 提交 B″（标签冻结）—— **待填**

**填写规则**：B″ 在 **G-conf-2 的文本标注完成并裁决定稿之后**做，**先于任何阶段 1**。

| 项 | 值 | 怎么算 |
|---|---|---|
| 提交 B″ 的 sha | **待填** | `git rev-parse HEAD`（B″ 提交之后） |
| `annotations/g_conf2/final_unblinded.jsonl` 的 sha256 | **待填** | `sha256sum <path>`；这就是每条注册命令的 `--labels-sha256 <LSHA>` |
| 其余标注产物（`final.jsonl` / `final_aligned.jsonl` / `final_provenance.jsonl` / `label_freeze.json`）的 sha256 | **待填** | 同上，写进 `docs/research_v4/label_freeze_b3.md` |
| `g_conf2_data_gates.json` 的 sha256 与 D1 / D1x / D2–D6 判定 | **待填** | 预注册 §9.3 的注册命令（含 `--h 1000000000`）；`created_at` 必须早于任何 G-conf-2 检测器产物 |
| `counts.annotated_rows` / `counts.by_arm` | **待填** | **硬前置**：必须是 `888` 与 `{clean: 336, benign_control: 336, attack: 216}`（预注册 §12.1 第 4 条） |
| `packets/g_conf2/blindness_scan.json` 的零泄漏判定 | **待填**（sha256 已在 A″ 上记为 `4b5532b8…`） | 手工核对；`--verify` 不覆盖 `extra_files`（§13.1 第 2 条） |
| 未达标配额的范围声明（若有） | **待填** | D1 / D1x / D3 / D5 是配额门，不达标不阻断，但必须按实际 N 用 `prereg_power_sim.py --n <实际 N>` 重算检验力 |

### 4.3 续表 · M1a / M1b（两份阶段 1 的阈值清单）—— **待填**

**填写规则**：**每份阶段 1 只跑一次**，跑完**当场**记录两个哈希（自哈希含 `created_at`，重跑必然换值）。
`--freeze-commit` 指向 **B″**。

| 项 | `stage1`（主格 / Holm / S-J） | `stage1_strat`（`VAL1`） |
|---|---|---|
| 运行目录 | **待填** | **待填** |
| `threshold_manifest.json` 整文件 sha256（**M1 i**，外部凭据） | **待填** | **待填** |
| manifest 内 `sha256` 字段（**M1 ii**，自哈希） | **待填** | **待填** |
| `manifest_version` | 期望 `"v3.2-2"` | 期望 `"v3.2-2"` |
| `cell` 的键数与三个钉子 | 期望 **21** 键；`debounce = 1`、`top_m = {"Z1": 1}`、`horizon_mode = "unbounded"`、`force_h = 10^9` | 同左 |
| `cell.statistics` / 每折 `cells` / `matched_alpha_inputs.cells` 的键集 | 期望 `["J","M","P","S","Z1"]` | 同左（清单键形如 `"<channel>@<stratum>"`） |
| `stage1_attack_traces_skipped` | 期望 **160** | 期望 **160** |
| 逐折 `n_reference_episodes`（抄进阶段 2 的 `<n0,n1,n2>` / `<m0,m1,m2>`） | **待填**（投影 170 / 185 / 168） | **待填**（逐层投影见预注册 §4.4） |
| 逐折 `attainability.n_reference` 与 `channels.<ch>.rank` | **待填** | **待填**（逐层） |
| 封存哈希（阶段 1 前的 `--verify` 读数与时刻） | **待填** | **待填** |
| `seal.pools[*].arm_hashes.{present, normal_union_matches, attack_matches}` | 期望 `true / true / true` | 同左 |

### 4.4 续表 · M2（三次阶段 2 的结果）—— **待填**

| 项 | 2a `stage2_Z1_vs_P` | 2b `stage2_Z1_vs_S` | 2c `stage2_val1_nkb` |
|---|---|---|---|
| 运行目录 | **待填** | **待填** | **待填** |
| `inputs.threshold_manifest_sha256`（**必须等于对应 M1 的 (ii)**） | **待填** | **待填** | **待填**（`stage1_strat`） |
| `result.json` 的 sha256 | **待填** | **待填** | **待填** |
| `verification.{len(checks), failed, ok}` | 期望 `≥ 22 / [] / true` | 同左 | 同左 |
| 三项钉子 `cell_debounce` / `cell_top_m` / `cell_horizon_mode` | 期望 `1` / `{"Z1": 1}` / `"unbounded"` | 同左 | 同左 |
| `attack_trace_census.{count, sha256}` | 期望 count **160** | 同左 | 同左 |
| 封存哈希（阶段 2 开头的读数，**必须等于阶段 1 的读数**） | **待填** | **待填** | **待填** |
| 主判定 / Holm / S-J / `VAL1` 的读数 | **待填** | **待填** | **待填** |

---

## 5. A″ 之后、B″ 之前允许做的事（提醒）

**允许**：正常池冒烟；**G-conf-2 的文本标注**（Sonnet 双盲 → Opus 裁决 → Opus `material_errors`/`citation` 扫描，
预注册 §12.1 第 3 条）；G-conf-2 的**文本数据门**（`g_dev_data_gates.py`，只读标注 + 可选的 `trace.json` 元数据，
**不打开任何 `steps/*.safetensors`**）；写报告骨架。

**不允许**：改算法 / 阈值 / 状态规则 / 门 / 锚点 / 命中口径 / Holm 族成员 / `VAL1` 判据 / S-J 分母；
在 G-conf-2 上跑任何检测器；补跑 `--arm-hashes`；在 G-dev 上跑任何决策性运行
（读数已冻结在预注册 §0.2 / §11.2 / §17.3a）；再从 G-conf 产生任何新读数。
