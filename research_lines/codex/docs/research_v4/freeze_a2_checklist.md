# 冻结提交 A′ 操作单（检测器 v3.2 预注册 / 数据集 G）

**配套文件**：`docs/research_v4/detector_prereg_v3_2_draft.md`（下称"预注册"；冻结版重命名为
`detector_prereg_v3_2.md`）的 §15.1 两步冻结、§15.2 冻结条件、§15.3 冻结审阅者清单、§15.4 输入文件与哈希；
以及 `docs/research_v4/prereg_v3_2_code_mapping.md`（下称"代码映射"）的 §4 逐条核验表。
**本文件的角色**：把 §15.2 / §15.3 与代码映射 §4 落成"照着敲即可"的操作单，并给出**提交 A′ 的 message 模板**。
**权威性**：口径、阈值、判定规则一律以预注册为准；本文件只规定**执行顺序与命令**，**不新增任何研究口径**。

**写作时间**：2026-09-07（预注册 rev3 同一轮）。**核对基准 HEAD `ea93668`**，
工作树 `git status --porcelain` 只有 `?? artifacts` 一行。

**为什么是 A′ 而不是 A**：v3.1 的两步冻结 A / B 已经用过（`docs/research_v4/freeze_a_checklist.md`、
`docs/research_v4/label_freeze_b.md`）。v3.2 的两步记作 **A′**（正文与代码）与 **B′**（G-conf 标签）。

---

## 0. 两步冻结 + 两阶段解封里 A′ 是哪一步

| 步 | 提交 / 产物 | 冻结什么 | 之后允许做什么 |
|---:|---|---|---|
| **A′** | **正文与代码冻结提交** | 预注册正文 + §15.4 全表的代码 / 配置 / 文档 / 检验力产物；G-fit / G-cal 标签 sha256（历史参照） | 正常池冒烟；G-conf 的**文本数据门**（`g_dev_data_gates.py`，**不碰路由**） |
| **B′** | **标签冻结提交** | `annotations/g_conf/final_unblinded.jsonl` 等五个文件的 sha256（写进 `docs/research_v4/label_freeze_b_v3_2.md`）+ `g_conf_data_gates.json` 的判定 | 此后才允许**阶段 1** |
| **M1** | **阶段 1 的 `threshold_manifest.json`** | 整个文件的 sha256，写进运行日志与本文件 §4 的续表 | 此后才允许**阶段 2** |
| **M2** | **阶段 2 的 `result.json`** | `inputs.threshold_manifest_sha256` 必须等于 M1 | 报告；**不允许任何补跑** |

**运行器的 `--freeze-commit` 指向 B′，不是 A′。**
A′ 与 B′ 之间**不允许**改动算法、阈值、状态规则、门、锚点、命中口径、Holm 族成员、S-J 的分母。
若 A′ 之后必须改代码，**重做 A′** 并在 message 里说明。

> **两条只能由流程保证的纪律（组长裁定，代码保证不了）**：
> 1. **Q6**：`g_conf_seal.py --arm-hashes` **不在 G-conf 阶段 1 之前跑**。阶段 1 自己记录它读到的正常臂哈希
>    （`inputs.normal_traces_per_dir[*].sha256` + `inputs.normal_trace_set_sha256`），阶段 2 记录攻击臂哈希
>    （`attack_trace_census.sha256`）。`--arm-hashes` 只是**事后**核对手段。
> 2. **Q10**：**G-conf 的阶段 1 只跑一次，manifest 的 sha256 当场记录**——manifest 的自哈希含 `created_at`，
>    重跑会换哈希，重跑本身也会让"工作点不是事后可选的"这条保证失效。

---

## 1. 逐步操作单

命令一律在仓库根目录执行。`$PY` = 本机 venv 的 python（本轮是 `/home/wzh/Agent-Moe-Research/.venv/bin/python`），
需要导包的命令前加 `PYTHONPATH=$PWD/src:$PWD/scripts`。

### 步 0 — 工作树干净、并发在制品已处理

```bash
git status --porcelain
git rev-parse HEAD
```

**期望**：只剩 `?? artifacts` 一行（artifacts 是符号链接 / 不进 git）。
**若有其他行**：合入或撤回，二者择一。**任何合入都必须让 §15.4 的对应行在 A′ 上重算**
（尤其 `docs/research_v4/*.md` 与 `scripts/research_v4/*.py`）。

**本轮写作时的实际状态**：干净（只有 `?? artifacts`）。

### 步 1 — 全部测试套件

```bash
PYTHONPATH=$PWD/src:$PWD/scripts $PY -m pytest tests/ -q
```

**期望**：全绿。**不写死条数**（预注册 §15.2 第 8 条）——把**当次**计数抄进提交 message。
参考读数（第二轮实现方记录）：**1316 passed, 128 subtests**（约 84 s）；
其中 `tests/test_research_v4_v3_2.py` 52 passed、`tests/test_research_v4_v3_2_round2.py` 54 passed。

只跑 v3.2 的两个套件（快速回归）：

```bash
PYTHONPATH=$PWD/src:$PWD/scripts $PY -m pytest \
  tests/test_research_v4_v3_2.py tests/test_research_v4_v3_2_round2.py -q
```

### 步 2 — 冻结的 OLMoE 侧未被碰（`verify_m_only`）

```bash
git diff --stat scripts/research_v3/verify_m_only_vs_frozen.py     # 必须为空
git diff --stat src/research_v2/trm3.py                            # 必须为空
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v3/verify_m_only_vs_frozen.py
```

**期望**：两个 `git diff --stat` 都为空；脚本输出
`scenario halves identical (all-target vs routine-only): True` 与
`[replica] vs frozen (identical): 0 trace(s) differ, 0 of them in the alarm SET`。

### 步 3 — v3.1 §19.7 回归（**冻结命令逐字段不变**）

做法（`v3_2_harness_changes.md` §10.4 的同法，不动工作树）：用 `git show <round1-commit>:<path>` 把
`src/research_v2/trm3_g.py`、`src/research_v2/io_g.py`、`scripts/research_v4/run_detectors_g.py`
导出到 scratchpad 的一棵副本树，两边用**完全相同**的命令行各跑一次 v3.1 的冻结命令，再逐字段 diff。

**期望**（第二轮实测）：
- `result.json` 中**取值不同的 v3.1 字段 = 0**（排除 `created_at` / `*seconds*` / `data_discipline_guard.dirty*`
  与从副本树跑必然不同的 provenance：`args.cache_dir` / `args.run_name` / `code_commit` /
  `data_discipline_guard.head` / `prereg_path` / `prereg.path`）；
- 只多出 **26 个新键**（`args.fixture_config`、`args.seal_manifest`，以及每个格 12 个：
  `classes.silent_all_attack_arm_episodes` 的 10 个 + `classes.silent_attack.{denominator, excluded_pre_injection_episodes, denominator_note}`）；
- `outputs.jsonl` **逐行、逐列相同，无新增列**（第二轮是 25 898 行）；
- 该运行的冻结断言 `attainability / horizon_H(=352) / layer_band / n_reference(=279) / tag_scope` 全 PASS。

> **注意**：`classes.silent_attack.note` 的**字符串**被刻意保留成 v3.1 原文，新规则写在 `denominator_note` 里；
> 正常臂冒烟里 `silent_attack` 两边都是空集，所以这条裁定在冻结命令上是空操作。

### 步 4 — G-dev 两阶段冒烟（**`fixture_rank_mod` 是参考读数**）

这是预注册 §11.2 要求的"两阶段流程在真实数据上跑通一遍"，也是 A′ 之前守卫能被真实检验的唯一机会。
**它已经在第二轮跑过**（产物见 §15.4.6）；本步是**复算核对**，不是重跑要求
——若确要重跑，注意 manifest 的自哈希含 `created_at`，**新产物的哈希必然不同**，届时 §15.4.6 的两行要一起改。

**阶段 1（只解封正常臂，S/P/M/J 四格一次算完）**：

```bash
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/run_detectors_g.py \
  --target artifacts/agent_v2/dataset_g/g_dev \
  --target-labels artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl \
  --cal-from-target --cal-folds 3 --fold-key fixture_rank_mod --cal-filtered-only \
  --stage calibrate --normal-only-smoke \
  --force-h 352 --expect-h 352 --h-min-survivors 90 \
  --view V1 --tag-scope message --statistic S,P,M,prob_js --alpha 0.10 \
  --window-s 8 --window-p 8 --window-m 8 --window-prob 8 \
  --bucket-size 32 --min-bucket-traces 30 --min-channel-windows 30 --min-channel-traces 10 \
  --tolerance-bands 0,4,5,8 --tertile-cutpoints-from-target --temporal-d 24 \
  --require-quality-labels --outputs primary \
  --output-root artifacts/agent_v2/dataset_g/v3_2_round2_smoke --run-name stage1
```

**阶段 2（解封攻击臂，只从 manifest 加载）**：

```bash
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/run_detectors_g.py \
  ... 与上面完全相同的口径开关 ... \
  --stage score \
  --threshold-manifest artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage1/threshold_manifest.json \
  --dev-smoke --compare-statistic P --anchor x --hit-window e_view_to_anchor_plus_h \
  --positives injection_present --bootstrap-replicates 2000 --run-name stage2
# Holm 成员 S2：同一份 manifest，换 --statistic S,M --compare-statistic M --run-name stage2_S_vs_M
# A/B 对照：同一条命令换 --fold-key scenario_mod，产物 stage1_oldkey / stage2_oldkey
```

**参考读数（`fixture_rank_mod`，第二轮实测；EXPLORATORY，不是判定）**：

| 量 | 值 | 读法 |
|---|---:|---|
| 折 × fixture 交叉表 | **26 / 26 / 26**（四个 fixture 都是），`collinear_fixtures = []` | `calibration_design.fold_fixture_crosstab` |
| 折图 sha256 | `e389122a393a9ef9…`（旧折键 `d536b080e08892eb…`） | `calibration_design.fold_assignment_sha256` |
| `stage1_attack_traces_skipped` | **264**（G-conf 期望 **160**） | 顶层同名键 |
| 逐折 `n_cal` / `alpha_eff` | 104 / 95 / 94；0.09524 / 0.09375 / 0.09474（加权 0.09459） | `cells.S.fold_summary` |
| 逐折留出 `far.filtered` | **0.1895 / 0.0745 / 0.0962** | `cells.S.folds[k].far.filtered.far` |
| 三分位切点 | **219 / 382**，逐折逐档 34/33/28、29/31/34、35/34/35 | `calibration_design.length_tertiles` |
| R_S / R_P | **0.824（103/125）** / 0.560（70/125） | `holm_s1_one_sample.S.point_estimate` / `comparison_anchored.bootstrap.recall_b` |
| **Δ̂ / CI / McNemar** | **0.264 / [0.024, 0.508] / 2.50e-07**，配对 125、16 家族 | `comparison_anchored.{bootstrap, two_condition}` |
| FAR all / filtered | **0.0980 / 0.1195** | `cells.S.metrics.far.{all,filtered}.far` |
| `x_beyond_h` | 18（逐折 1 / 10 / 7）；分层命中 0.917（99/108）对 0.235（4/17） | `positives_anchored.{reachability, by_x_beyond_h}` |
| 门 | **F1 PASS 0.0249 · F3 FAIL 0.1532 · F5 PASS 0.1786 ≤ 0.2644（k̄ 2.4286）· N1 FAIL 0.7181 · N2 PASS 28**；F4 PASS 0.125 对 0.15417（余量 0.029） | `gates.S.gates[]` + `cells.S.metrics.classes.silent_attack` |
| S1 | 0.824，[0.709, 0.919]，p 4.998e-4 | `holm_s1_one_sample.S` |
| S2（S vs M） | −0.016，[−0.079, 0.037]，p 0.754，两条件都 false | `stage2_S_vs_M` 的 `comparison_anchored.two_condition` |
| S-J（J 格） | 主口径 144 对 / Δ̂_J 0.2153；敏感性 124 对 / 0.2016 | `injection_pairing.J.{primary_unfiltered, sensitivity_filtered_negatives}` |
| 成本 | 阶段 1 57 s / 1.05 GB；阶段 2 81 s / 1.42 GB；J 是唯一昂贵的格（阶段 1 42.6 s，其余三格合计 8.8 s） | `cells.<s>.cost` + 运行日志 |

**若重跑的读数与上表不一致**：**停下来**，先查清原因（折键？过滤？manifest？），
**不要**为了让数字对上而改任何参数——那就是"在同一批数据上第四次调参"。

### 步 5 — 检验力表重算（不读任何数据）

```bash
# §8.2 / §8.3 的主网格
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/prereg_power_sim.py \
  --config configs/dataset_g/g_conf.json \
  --output-dir artifacts/agent_v2/dataset_g/prereg_power_v3_2/main \
  --psi 0.30 --psi 0.35 --n 62 --n 80 --n 100 --n 126 --rho 0.15 --rho 0.30 \
  --delta 0 --delta 0.10 --delta 0.125 --delta 0.15 --delta 0.20 --delta 0.25 --delta 0.28 \
  --replicates 4000 --bootstrap 2000 --seed 20260907

# §8.5 的 S-J 网格（N = 115 敏感性 / 144 = G-dev 实测 pair_count / 160 主口径）
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/prereg_power_sim.py \
  --config configs/dataset_g/g_conf.json \
  --output-dir artifacts/agent_v2/dataset_g/prereg_power_v3_2/sj \
  --psi 0.30 --psi 0.35 --n 115 --n 144 --n 160 --rho 0.15 --rho 0.30 \
  --delta 0 --delta 0.10 --delta 0.15 --delta 0.20 --delta 0.25 --delta 0.30 \
  --replicates 4000 --bootstrap 2000 --seed 20260907

# §8.2 的 Δ = 0.264 补算格（先验中心处的检验力）
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/prereg_power_sim.py \
  --config configs/dataset_g/g_conf.json \
  --output-dir artifacts/agent_v2/dataset_g/prereg_power_v3_2/delta264 \
  --psi 0.30 --psi 0.35 --n 62 --n 80 --n 100 --n 126 --rho 0.15 --rho 0.30 \
  --delta 0 --delta 0.25 --delta 0.264 --delta 0.28 \
  --replicates 4000 --bootstrap 2000 --seed 20260907
```

**期望**：三次输出的 `power_sim.{json,md}` 与预注册 §15.4.6 的 sha256 **逐位相同**；
`main/power_sim.md` 的三张表与预注册 §8.2 / §8.3 **逐格一致**（最保守格 MDE **0.231**，
Δ = 0.264 处最低检验力 **0.914**，零假设合取上界 **0.036**）；
`sj/power_sim.md` 与 §8.5 逐格一致（N = 160 的 MDE 0.139–0.174、N = 115 的 0.157–0.189）。

> **三个默认值陷阱（必须显式传，否则会静默跑错网格）**：`--config` 默认 `g_dev.json`（家族 8 × 19 + 8 × 14 = 264，
> **不是** G-conf 的 8 × 11 + 8 × 9 = 160）；`--n` 默认 107/150/177/264；`--delta` 默认 0–0.20。
>
> **§8.4（Holm 成员 S1）的表不能由这个 CLI 复现**：它没有单样本"下界 > null"模式
> （`--help` 里没有对应开关）。该表由 `trm3_g.cluster_bootstrap_rate` 的同一模型算出，
> 审阅时只核对单调性与 p = 0.50 一列的零假设量级。

### 步 6 — §15.4 的哈希逐行重算

```bash
# 15.4.1 / 15.4.2 / 15.4.3 的文档行（取 HEAD 的 git 对象，不受工作树在制品影响）
for f in docs/research_v4/detector_prereg_v3_1.md \
         docs/research_v4/gpt_oss_research_program.md \
         docs/research_v4/agent_v3_dataset_design.md \
         docs/research_v4/detector_harness_g.md \
         docs/research_v4/v3_2_design_note.md \
         docs/research_v4/prereg_v3_1_code_mapping.md \
         docs/research_v4/freeze_a_checklist.md \
         docs/research_v4/explore_v32_feasibility.md \
         docs/research_v4/v3_2_harness_changes.md \
         docs/research_v4/g_dev_confirmatory_report.md \
         docs/research_v4/g_dev_primary_diagnostics.md \
         docs/research_v4/h_freeze_note.md \
         docs/research_v4/g_session_medium_conf_run_log.md \
         docs/research_v4/freeze_review_v3_2_statistics.md \
         docs/research_v4/freeze_review_v3_2_data.md \
         docs/research_v4/freeze_review_v3_2_resolution.md \
         docs/research_v4/g_dev_annotation_report.md \
         docs/research_v4/g_dev_annotation_agreement.md \
         docs/research_v4/g_conf_annotation_report.md \
         docs/research_v4/g_conf_annotation_agreement.md \
         docs/research_v4/attack_annotation_guideline.md \
         docs/research_v4/attack_annotation_examples.md \
         docs/research_v4/annotation_schema_1_1_changes.md \
         docs/research_v4/normal_annotation_guideline.md \
         docs/research_v4/prereg_v3_2_code_mapping.md \
         docs/research_v4/freeze_a2_checklist.md; do
  printf '%-62s %s\n' "$f" "$(git show HEAD:$f | sha256sum | cut -d' ' -f1)"
done

# 15.4.4 代码 + 15.4.5 配置（同样取 git 对象）
for f in src/research_v2/trm3.py src/research_v2/trm3_g.py src/research_v2/io_g.py \
         src/agent_v3/packets/schema.py src/agent_v3/packets/validate.py src/agent_v3/packets/build.py \
         scripts/research_v3/verify_m_only_vs_frozen.py \
         configs/dataset_g/g_conf.json configs/dataset_g/agent_g_conf.json \
         configs/dataset_g/g_dev.json configs/dataset_g/manifest.json; do
  printf '%-62s %s\n' "$f" "$(git show HEAD:$f | sha256sum | cut -d' ' -f1)"
done

# 15.4.4 的"凡在提交 A' 里存在的 scripts/research_v4/*.py 与 tests/test_research_v4_*.py 都必须有一行"
for f in $(git ls-tree -r --name-only HEAD -- scripts/research_v4 tests \
           | grep -E '^(scripts/research_v4/.*\.py|tests/test_research_v4_.*\.py)$' | sort); do
  printf '%-62s %s\n' "$f" "$(git show HEAD:$f | sha256sum | cut -d' ' -f1)"
done

# 15.4.6 artifacts 行（artifacts 不进 git，取工作树）
sha256sum artifacts/agent_v2/dataset_g/prereg_power_v3_2/main/power_sim.json \
          artifacts/agent_v2/dataset_g/prereg_power_v3_2/main/power_sim.md \
          artifacts/agent_v2/dataset_g/prereg_power_v3_2/sj/power_sim.json \
          artifacts/agent_v2/dataset_g/prereg_power_v3_2/sj/power_sim.md \
          artifacts/agent_v2/dataset_g/prereg_power_v3_2/delta264/power_sim.json \
          artifacts/agent_v2/dataset_g/prereg_power_v3_2/delta264/power_sim.md \
          artifacts/agent_v2/dataset_g/h_rule/h_rule_g.json \
          artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage1/threshold_manifest.json \
          artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage1/result.json \
          artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage2/result.json \
          artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage2_S_vs_M/result.json \
          artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage1_oldkey/result.json \
          artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage2_oldkey/result.json

# 15.4.7 封存与标签（只算字节摘要，不读路由、不读标签内容）
sha256sum artifacts/agent_v2/dataset_g/g_conf/SEALED.json \
          artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl \
          artifacts/agent_v2/dataset_g/annotations/g_conf/final.jsonl \
          artifacts/agent_v2/dataset_g/annotations/g_conf/final_aligned.jsonl \
          artifacts/agent_v2/dataset_g/annotations/g_conf/final_provenance.jsonl \
          artifacts/agent_v2/dataset_g/annotations/g_conf/label_freeze.json \
          artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl
```

**期望**：与预注册 §15.4 的每一行**逐位相同**。
**不同即先改预注册再冻结**（预注册 §15.3 / v3.1 §15.3 第 16 条）。

> **G-conf 标签行的特殊规则**：那五行的**权威哈希在提交 B′ 上冻结**，不是 A′。
> 在 A′ 上记录它们只是为了让审阅者能核对"标签在 A′ 与 B′ 之间没有被改动"。

### 步 7 — 五路 census 与隔离区（只读元数据）

```bash
PYTHONPATH=$PWD/src $PY -c "
from research_v2 import io_g; import json
print(json.dumps(io_g.variant_census('artifacts/agent_v2/dataset_g/g_dev'), indent=1))"
```

**期望**（预注册 §4.1）：`clean 192 / benign_control 192 / benign_lexical 24 / legitimate_refusal 24 / attack 352`，
合计 **784**；`attack_bearing_episodes = 264`、`attack_pre_injection_episodes = 88`。
（该命令只读 `trace.json` 的元数据，**不打开任何 `steps/*.safetensors`**。）

**G-conf 上的同一条命令留到 B′ 之后**——它会读 G-conf 的 `trace.json` 元数据，
按组长裁定 Q6 的精神，A′ 之前不主动去碰 `artifacts/agent_v2/dataset_g/g_conf`。

### 步 8 — 代码映射 §4 的 28 条逐条过一遍

```bash
# 举例：第 3 条（CLI 开关真的存在）
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/run_detectors_g.py --help | \
  grep -E -- '--cal-from-target|--cal-folds|--fold-key|--fixture-config|--seal-manifest|--cal-filtered-only|--force-h|--stage|--threshold-manifest|--anchor|--hit-window|--positives|--injection-negatives|--expect-n-reference-folds|--tertile-cutpoints-from-target'

# 举例：第 5 / 6 / 12 条（守卫 15 项、多格 manifest、x_beyond_h 的两个分母）
PYTHONPATH=$PWD/src:$PWD/scripts $PY - <<'PY'
import json
R = json.load(open('artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage2/result.json'))
v = R['threshold_manifest']['verification']
print('checks', len(v['checks']), 'failed', v['failed'], 'ok', v['ok'], v['manifest_version'])
r = R['cells']['S']['metrics']['positives_anchored']['reachability']
b = R['cells']['S']['metrics']['positives_anchored']['by_x_beyond_h']
print('126 ==', r['anchor_reachable_plus_16'] + r['x_beyond_h'])
print('125 ==', b['False']['reachable_count'] + b['True']['reachable_count'])
PY
```

**期望**：15 个开关全部命中；`checks` 长度 15、`failed == []`、`ok == True`、版本 `v3.2-2`；
两个恒等式都成立（126 与 125）。**其余 25 条照代码映射 §4 的表逐条做。**

### 步 9 — 本文件（预注册）的 sha256

```bash
sha256sum docs/research_v4/detector_prereg_v3_2.md
```

**这个值不写进预注册本身**（自指不可能收敛，预注册 §15.4.7 最后一行）。它有且只有**两个**权威副本：

1. **冻结提交 A′ 的 message**（见 §3 的模板）；
2. **本文件 §4 的"冻结记录"节**（提交 A′ 时填入）。

运行时由 `--prereg-sha256 <值>` 传给 `run_detectors_g.py`，落进每个 `result.json` 的 `prereg.sha256`。
守卫哈希的文件是 `scripts/research_v4/run_detectors_g.py` 的 `PREREG_PATH`，
**A′ 之前必须把它改指 v3.2 的正文**并由一条钉住文件名的测试保护（预注册 §15.2 第 1 条）。

```bash
grep -n 'PREREG_PATH' scripts/research_v4/run_detectors_g.py
grep -rn 'PreregPathTest\|PREREG_PATH' tests/
```

### 步 10 — 做提交 A′

按 §3 的模板写 message，`git add` 步 0 处理完的工作树，提交。
**提交后不要再改这些文件**；任何改动 = 重做 A′。

### 步 11 — 守卫真的有牙（提交 A′ 之后验一次）

```bash
# 用一个错误的 --freeze-commit 跑一次非冒烟命令 -> 必须 SystemExit
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/run_detectors_g.py \
  ... 主格口径 ... --freeze-commit deadbeefdeadbeefdeadbeefdeadbeefdeadbeef \
  --prereg-sha256 <PREREG_SHA256>

# 把 manifest 改一个字节 -> --stage score 必须 SystemExit
# 请求一个不在 manifest.cells 里的统计量 -> 必须 SystemExit
```

**期望**：三次都非零退出。正确命令下 `result.json.data_discipline_guard` 的
`enforced == true`、`dirty == false`、`head_is_freeze_commit == true`、
`prereg_sha256_matches == true`、`labels_sha256_missing == []`。

> **注意**：预注册 §12.2 的**主格命令**要求 `--freeze-commit <B′>`，即**标签冻结提交**。
> 步 11 只是"守卫机制本身可用"的一次演练，不是主格评价运行。

---

## 2. claim → verified how → result（提交 A′ 时逐行填 result）

| # | claim | verified how | result |
|---:|---|---|---|
| 1 | 工作树干净、HEAD 明确 | 步 0 | |
| 2 | 全部测试全绿 | 步 1（记当次条数） | |
| 3 | 冻结的 OLMoE 侧一字未动 | 步 2 | |
| 4 | v3.1 §19.7 回归逐字段不变 | 步 3 | |
| 5 | 两阶段在真实数据上跑通、15 项守卫全 PASS | 步 4 / 步 8 | |
| 6 | 检验力表可复现且与正文逐格一致 | 步 5 | |
| 7 | §15.4 全表哈希逐行相同 | 步 6 | |
| 8 | 五路 census 与预注册 §4.1 一致 | 步 7 | |
| 9 | 代码映射 §4 的 28 条全过 | 步 8 | |
| 10 | `PREREG_PATH` 已改指 v3.2 且有测试钉住 | 步 9 | |
| 11 | 预注册 sha256 有两个权威副本 | 步 9 + 步 10 | |
| 12 | 守卫有牙（三次 SystemExit） | 步 11 | |
| 13 | **代码映射 §5 的四个缺口已由组长处置** | 读组长裁定 | |
| 14 | **Q6 / Q10 两条流程纪律已写进操作单并被执行方确认** | §0 的引注 | |

---

## 3. 冻结提交 A′ 的 message 模板

```
prereg: freeze A' -- detector preregistration v3.2 (dataset G / gpt-oss-20b / Agent v3)

Freezes the v3.2 preregistration text and the code it names.  This is step A' of the
two-step freeze (A' text+code, B' G-conf labels) plus two-stage unsealing (M1 manifest,
M2 scores).  The runner's --freeze-commit points at B', not at this commit.

Preregistration sha256 (the authoritative copy; the file does not contain its own hash):
  docs/research_v4/detector_prereg_v3_2.md
  <PREREG_SHA256>

Companion documents frozen with it:
  docs/research_v4/prereg_v3_2_code_mapping.md   <CODE_MAPPING_SHA256>
  docs/research_v4/freeze_a2_checklist.md        <CHECKLIST_SHA256>

What this preregistration claims: ONE confirmatory hypothesis H1 on G-conf -- the
X-anchored hit rate of S exceeds that of P at a matched measured FAR, decided by the
conjunction of (family-clustered 95% percentile CI lower bound > 0) AND (exact McNemar).
Holm family m = 2 (S1 absolute hit-rate lower bound, S2 S vs M).  S-J (injection-presence)
is a separately registered secondary claim, not in the Holm family.

Verification recorded at freeze time:
  tests                    <N> passed, <M> subtests   (pytest tests/ -q)
  verify_m_only            halves_identical=True, mismatch_count=0
  v3.1 19.7 regression     0 differing v3.1 fields, outputs.jsonl identical
  power tables             prereg_power_v3_2/{main,sj,delta264}/power_sim.{json,md}
                           reproduced bit-for-bit; +-0.03 trigger: 1 cell, cause recorded
                           in prereg 8.2
  hash table               prereg 15.4, every row recomputed on this commit
  code mapping             28/28 freeze-reviewer checks passed
  G-dev two-stage smoke    v3_2_round2_smoke/ (fixture_rank_mod), manifest guards 15/15

Development readings frozen as priors (G-dev, EXPLORATORY -- NOT confirmation):
  R_S 0.824 (103/125), R_P 0.560 (70/125), Delta 0.264, CI [0.024, 0.508],
  McNemar 2.50e-07, FAR all/filtered 0.0980/0.1195, psi 43/125 = 0.344.
  Gates: F1 PASS 0.0249, F3 FAIL 0.1532, F4 PASS margin 0.029, F5 PASS 0.1786<=0.2644,
  N1 FAIL 0.7181, N2 PASS 28.  F3 / F1-per-fold / N1 are PREDECLARED failures.

Data discipline: no G-conf routing was read while writing or freezing this.  The only
G-conf bytes touched are configs/dataset_g/g_conf.json (metadata), SEALED.json and the
annotation JSONLs -- digests only.  --arm-hashes was NOT run on G-conf (lead ruling Q6).
Stage 1 on G-conf runs exactly once and its manifest hash is recorded on the spot (Q10).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

## 4. 冻结记录（提交 A′ 时填入）

| 项 | 值 |
|---|---|
| 提交 A′ 的 sha | |
| 提交时间 | |
| `docs/research_v4/detector_prereg_v3_2.md` 的 sha256 | |
| `docs/research_v4/prereg_v3_2_code_mapping.md` 的 sha256 | |
| `docs/research_v4/freeze_a2_checklist.md` 的 sha256 | |
| 测试计数（当次） | |
| §15.4 全表重算结果（相同 / 不同的行） | |

**续表 · 提交 B′（标签冻结）**

| 项 | 值 |
|---|---|
| 提交 B′ 的 sha | |
| `annotations/g_conf/final_unblinded.jsonl` 的 sha256 | |
| `g_conf_data_gates.json` 的 D1 / D1x / D2–D6 判定 | |
| 未达标配额的范围声明（若有） | |

**续表 · M1（阶段 1 的阈值清单，`--freeze-commit` 指向 B′）**

| 项 | 值 |
|---|---|
| 阶段 1 的运行目录 | |
| `threshold_manifest.json` 的 sha256（**当场记录，阶段 1 只跑一次**） | |
| `manifest_version` | |
| 守卫 15 项的结果 | |
| `stage1_attack_traces_skipped`（期望 **160**） | |
| 封存哈希（阶段 1 前的读数） | |

**续表 · M2（阶段 2 的结果）**

| 项 | 值 |
|---|---|
| 阶段 2 的运行目录 | |
| `inputs.threshold_manifest_sha256`（**必须等于 M1**） | |
| 封存哈希（阶段 2 开头的读数，**必须等于阶段 1 的读数**） | |
| `attack_trace_census.{count, sha256}` | |

---

## 5. A′ 之后、B′ 之前允许做的事（提醒）

**允许**：正常池冒烟；G-conf 的**文本数据门**（`g_dev_data_gates.py` 在 G-conf 标注上跑，只读标注 + 可选的
`trace.json` 元数据，**不打开任何 `steps/*.safetensors`**）；写报告的骨架。

**不允许**：改算法 / 阈值 / 状态规则 / 门 / 锚点 / 命中口径 / Holm 族成员 / S-J 分母；
在 G-conf 上跑任何检测器；跑 `--arm-hashes`；重跑 G-dev 的开发评价（读数已冻结在预注册 §0.2）。
