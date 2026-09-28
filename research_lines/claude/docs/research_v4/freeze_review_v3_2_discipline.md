# 冻结审阅 v3.2 —— 两阶段解封的数据纪律与可复现性（lens: discipline）

> 审阅对象：`docs/research_v4/detector_prereg_v3_2_draft.md`（rev3 + §19–§19.4 裁定，**约束性**）的
> **§12（G-conf 上的确认）与 §15（冻结程序）**，配套 `docs/research_v4/freeze_a2_checklist.md`、
> `docs/research_v4/prereg_v3_2_code_mapping.md`；代码 `scripts/research_v4/run_detectors_g.py`、
> `scripts/research_v4/g_conf_seal.py`、`scripts/research_v4/g_dev_data_gates.py`、`src/research_v2/io_g.py`。
>
> **审阅方法**：把 §12.2 的次序当成"现在就要执行"来逐步走一遍——B′ 标签冻结 → 阶段 1 之前的封存核验 →
> 阶段 1（只解封正常臂）→ manifest 落盘 → 阶段 1.5 的第二次封存核验 → 阶段 2 → 报告；
> 每一步问四件事：**(a) 逐字敲能不能跑通；(b) 有没有信息能从攻击臂漏进阈值；(c) 有没有哈希 / 记录缺失；
> (d) "一次"是不是有定义**。
>
> **数据纪律留痕**：审阅期间**未读取** `artifacts/agent_v2/dataset_g/g_conf` 下的任何路由张量，
> **也未读取该目录下的任何 `trace.json` / `manifest.jsonl`**。G-conf 侧读到的字节只有
> `artifacts/agent_v2/dataset_g/g_conf/SEALED.json`（元数据，允许）、
> `artifacts/agent_v2/dataset_g/g_conf/resume_*/run_summary.json`（元数据）与 `configs/dataset_g/g_conf.json`。
> 臂分布、trace 计数一律**从 SEALED.json 的路径表推出**，没有走 loader。
> 所有 CLI 的"逐字试跑"都用**不存在的目标目录**或**只到 argparse 就退出**的形式做，未在 G-conf 上跑过任何东西。
> **本轮未修改任何代码、配置、标签或产物**（只新增本文件）。
>
> 只报**已核实**（读代码 + 实跑）的问题。等级：BLOCKING / SHOULD-FIX / NOTE。

---

## 0. 先说清楚：走通的部分

在报问题之前，先把**确实成立**的几条钉住，否则下面的清单会被误读成"两阶段解封整体不可用"。

1. **臂结构与"期望 160"成立（只读 SEALED.json 即可核）**：
   `SEALED.json` 记 720 条 trace、`incomplete = []`，按路径末段分臂是
   **clean 280 / benign_control 280 / attack 160**，与 `configs/dataset_g/g_conf.json` 的
   `allocation.trace_count = 720`、`scenarios_by_variant = {attack_cell: 160, clean: 120}` 逐条对得上。
   `attack_trace_census` 数的正是**攻击 arm 目录数**（`io_g.trace_digest(variants=("attack",))["trace_count"]`），
   所以 §12.2 期望的 `stage1_attack_traces_skipped = 160` 在 G-conf 上是对的（G-dev 的实测是 264，
   见 `v3_2_round2_smoke/stage1/threshold_manifest.json`）。
2. **阶段 1 认臂不需要打开攻击 trace 的路由**：`io_g.load_g` 在调用 `_episodes_of_trace`（唯一会
   `load_file(steps/*.safetensors)` 的地方）**之前**就按 `variant not in wanted` `continue` 掉了；
   臂身份来自 `trace.json.perturbation.arm` + arm 目录名（`_variant_of`，两者不一致直接 `ValueError`），
   `benign_lexical` / `legitimate_refusal` 的 config join 只在 `variant == clean` 时生效。
   G-conf 的 `factory.normal_variant` 只有 `attack_cell`(160) / `clean`(120) 两种，**没有**特殊正常角色，
   所以 G-conf 上这个 join 返回空表——三臂结构，`NORMAL_VARIANTS` 覆盖 560 条 trace。
3. **阶段 1 的额外硬拦**：`main_v32` 在 `--stage calibrate` 下装载后再扫一遍
   `variant not in NORMAL_VARIANTS` 的 episode，命中即 `SystemExit`（不是断言，是退出）。
4. **攻击臂 → 阈值的路径上没有发现泄漏**：
   `fold_pools` 的 `fit` / `reference` **恒**只收 `episode.variant in NORMAL_VARIANTS`（且 `--cal-filtered-only`
   下只收 `filter_pass is True`），与 `normals_only_eval` 无关；阶段 2 的 `run_cell_v32(restore=True)`
   既不 `fit` 也不 `calibrate_g`，statistic state / standardiser / 参照极值 / H 全部 `load_state`；
   `matched_alpha` 在阶段 2 只走 `manifest_matched_alpha` 重放，不再搜；长度三分位切点走
   `manifest_payload["length_tertiles"]` 并打 `replayed_from_manifest = True`。
5. **阶段 2 的正常臂内容确实被重算**：`verify_manifest` 的 `normal_traces_sha256` /
   `normal_trace_set_sha256` 两项都是**在阶段 2 重新遍历磁盘上的正常臂 `trace.json` 算出来的**，
   与 manifest 记的值不符即 `SystemExit`。
6. **manifest 改一个字节会被抓**：`manifest_sha256` 用 `manifest_self_sha256`（去掉自身 `sha256` 字段后
   canonical-json 重算）比对，内容任何改动都会失配。

下面的问题**不推翻**这六条；它们说的是"次序表逐字敲跑不通"、"某些哈希算了但没人比"、
"注册过的守卫没落地"，以及"'一次'没有机械定义"。

---

## 汇总

| id | 等级 | 位置 | 一句话 |
|---|---|---|---|
| **D-1** | BLOCKING | §12.2 阶段 1 | 命令里的 `--cells` 与 `--threshold-manifest-out` 两个开关**不存在**，argparse 直接拒绝 |
| **D-2** | BLOCKING | §12.2 步 0 / 步 1.5 | `g_conf_seal.py --verify <目录>` 崩在 `IsADirectoryError`；这是**唯一**会重算攻击臂内容的步骤 |
| **D-3** | BLOCKING | §12.2 / §15.3 第 3 条 | `stage1_attack_arms_read` 字段在代码、测试与任何已产出的 manifest 里**都不存在** |
| **D-4** | BLOCKING | §15.1 M2 / §3.3 第 2 条 | "`inputs.threshold_manifest_sha256` 必须等于 M1"按构造**永不成立**（自哈希 vs 整文件哈希） |
| **D-5** | BLOCKING | `refuse_sealed_pools` | `--normal-only-smoke` **不被**封存批拦截：可在 G-conf 上无限次重跑阶段 1 看逐折 FAR，不留痕 |
| **D-6** | BLOCKING | §12.2 阶段 2 | `--anchor X`（大写）是 argparse 非法值；且命令里缺 `--target` / `--cal-from-target`（两者都是硬性必需） |
| **D-7** | SHOULD-FIX | `verify_manifest` / 操作单 M1 | 攻击臂 trace 集合哈希两端都算了，**从不比较**；新增一条攻击 trace 也检测不到 |
| **D-8** | SHOULD-FIX | `cell_matches` | 只比 5 个键，而 §3.3 第 2 条注册的是"该格冻结常量**逐位**相符"；13 个键落盘但不核验 |
| **D-9** | SHOULD-FIX | §3.3 第 2 条 | 注册的"manifest 的 `prereg.sha256` / `freeze_commit` 不符 → `SystemExit`"未落地 |
| **D-10** | SHOULD-FIX | §3.3 第 4 条 | "manifest 的 `created_at` 早于阶段 2"被注册为"必须机械保障"，但无任何代码检查 |
| **D-11** | SHOULD-FIX | `seal_check` | 它**不重算磁盘上的 trace.json**，只做 SEALED.json 的自洽检查；`sealed_trace_set_sha256` 近乎空转 |
| **D-12** | SHOULD-FIX | seal 块 | 两次封存核验**没有时间戳**，§15.3 第 3 条要核的"时间戳次序"无从核起 |
| **D-13** | SHOULD-FIX | §9.3 / §12.1 第 3 条 | 开启前置 `g_conf_data_gates.json` 的**产出命令、输出路径与 `--run-dir` 从未被写死** |
| **D-14** | SHOULD-FIX | 折键的 config 来源 | subset config 自动解析到**本次会话的 worktree 路径**，且没有任何断言把它钉到 §15.4.5 的哈希 |
| **D-15** | SHOULD-FIX | §12.2 vs §15.2 第 3 条 | §12.2 的两条命令都没传 `--labels-sha256`，守卫空转 |
| **D-16** | NOTE | 两阶段产物 | manifest 与 `result.json` 都是**静默覆盖写**，"只跑一次"没有任何机械留痕 |
| **D-17** | NOTE | §12.2 | "阶段 2 只跑一次"下面紧接着列了 3–4 次阶段 2 运行，"一次"的口径自相矛盾 |
| **D-18** | NOTE | `SEALED.json.root` | 是**绝对路径**（指向主仓库），`ROOT / seal["root"]` 被它整段覆盖，跨 checkout 会静默核验另一棵树 |
| **D-19** | NOTE | `g_conf_seal.verify` | 不校验 `frozen_inputs`（含 `g_conf.json`）/ `run_configs` / `extra_files`，不检测**新增**文件，不检查只读位 |
| **D-20** | NOTE | §15.3 第 1b / 第 3 条 | 两个"160"是**不同的量**（攻击 episode 分母 vs 攻击 arm 目录数），恰好同值，易被审阅者当成一条 |
| **D-21** | NOTE | `g_dev_data_gates.py` | 在 G-conf 上跑时仍打印 `G-dev data gates`，审计留痕会误导 |

---

## D-1（BLOCKING）§12.2 阶段 1 的命令逐字敲不通：两个开关不存在

**证据（实跑，目标目录用不存在的路径，只走到 argparse）**：

```bash
PYTHONPATH=$PWD/src:$PWD/scripts $PY scripts/research_v4/run_detectors_g.py --stage calibrate \
  --target /tmp/nonexistent_pool --target-labels /tmp/none.jsonl \
  --cal-from-target --cal-folds 3 --fold-key fixture_rank_mod --cal-filtered-only \
  --view V1 --tag-scope message --cells S,P,M,J --force-h 352 --expect-h 352 \
  --alpha 0.10 --window-s 8 --require-quality-labels --tertile-cutpoints-from-target \
  --threshold-manifest-out /tmp/x.json --freeze-commit HEAD --prereg-sha256 deadbeef
# run_detectors_g.py: error: unrecognized arguments: --cells S,P,M,J --threshold-manifest-out /tmp/x.json
```

实际形式是 `--statistic S,P,M,J`（逗号分隔，`STATISTIC_ALIASES` 把 `prob_js` 归一成 `J`，manifest 的键就是
`{S, P, M, J}`，与 §15.3 第 3b 条的期望一致）与 `--threshold-manifest <path>`（`calibrate` 下是**写**、
`score` 下是**读**，`run_detectors_g.py:1472`）。

**注意这不是孤立笔误**：§13 第 5 条把 `--threshold-manifest-out` 与 `--cells S,P,M,J` 一并注册为"已落地的
CLI"，§15.2 第 4 条又要求"§13 的 16 条**全部落地且每个 CLI 开关都出现在 `--help` 的实际输出里**"——
按 §15.2 第 4 条核验，§13 第 5 条现在是 FAIL。
`prereg_v3_2_code_mapping.md` §2 第 5 行与 §5 缺口 2 已经把这两条标成 **IMPL/MISMATCH** 并建议"改预注册的措辞"，
但**正文（约束性文件）至今未改**，而正文才是 §12.2 的执行依据。

**修法**：把 §12.2 阶段 1 的命令改成实际开关，并同步 §13 第 5 条与 §11.2 第 1 条的措辞。
`freeze_a2_checklist.md` 步 4 的 G-dev 命令已经是正确形式，直接抄。

---

## D-2（BLOCKING）§12.2 的封存核验命令传的是目录，实跑崩溃

§12.2 步 0 与步 1.5 逐字写的是：

```
python scripts/research_v4/g_conf_seal.py --verify artifacts/agent_v2/dataset_g/g_conf
```

**证据（实跑；崩在 `read_text` 上，未打开任何 trace）**：

```
IsADirectoryError: [Errno 21] Is a directory:
  '/home/wzh/Agent-Moe-Research/artifacts/agent_v2/dataset_g/g_conf'
exit=1
```

`g_conf_seal.main` 对 `--verify` 的处理是 `return verify(args.verify.resolve())`，而 `verify(seal_path)`
第一行就是 `json.loads(seal_path.read_text(...))`——它要的是 **`SEALED.json` 文件本身**，不是 subset 根目录。
正确写法是 `--verify artifacts/agent_v2/dataset_g/g_conf/SEALED.json`。

**为什么这条是 BLOCKING 而不是笔误**：`g_conf_seal.verify` 是整条流程里**唯一**会把
**攻击臂的 `trace.json` / `manifest.jsonl` 按内容重算并与封存值比对**的步骤（`for record in seal["traces"]["traces"]`
逐条 `sha256_file`）。harness 侧：
- `normal_trace_set_sha256` 只覆盖**正常臂**；
- `seal_check` 根本不读磁盘上的 trace（见 D-11）；
- `attack_trace_census` 算了攻击臂哈希但没人比（见 D-7）；
- `arm_hash_check` 依赖 `ARM_HASHES.json`，而组长裁定 **Q6 明确禁止**在 G-conf 阶段 1 之前跑 `--arm-hashes`
  （实测 `artifacts/agent_v2/dataset_g/g_conf/ARM_HASHES.json` 与 `.../g_conf_meta/ARM_HASHES.json` 都不存在，
  所以这条路在 G-conf 上恒为 `present = false`，"nothing fails"）。

于是：**§12.2 的两次"封存哈希核验"按字面执行会直接失败；一旦被"顺手改成能跑的样子"以外的方式绕过
（例如只看 harness 里的 `seal` 块就算核过），攻击臂在阶段 1 与阶段 2 之间就没有任何内容校验。**

**修法**：命令补上 `/SEALED.json`；同时在 §12.2 里写死"步 0 / 步 1.5 的期望输出是
`\"verified\": true` 且 `mismatches: []`，并把 `SEALED.json` 自身的 `sha256sum` 抄进运行日志"。

---

## D-3（BLOCKING）`stage1_attack_arms_read` 是一个不存在的字段

`grep -rn stage1_attack_arms_read scripts/ src/ tests/` → **零命中**；
`v3_2_round2_smoke/stage1/threshold_manifest.json` 的顶层键是
`[cell, code_commit, created_at, fit, fixtures, fold_assignment, fold_assignment_sha256, fold_count,
fold_fixture_crosstab, fold_key, fold_pools, folds, freeze_commit_requested, freeze_commit_resolved,
inputs, kind, length_tertiles, manifest_version, matched_alpha_inputs, prereg, rule, sha256,
stage1_attack_traces, stage1_attack_traces_skipped]`——**没有这个键**。

预注册里它出现 **7 次**，其中三处是硬性核验点：
- §4 的 manifest 骨架（第 500 行）把它写进示例 JSON；
- §12.2 的阶段 1 期望产物列表（第 1322 行）；
- **§15.3 第 3 条的冻结审阅者清单**（第 1517 行）："`threshold_manifest.stage1_attack_arms_read == false`"；
- 另有 §11.2 第 1 条、§13 第 5 条、§16 第 10 条。

审阅者照 §15.3 第 3 条取这个字段只会取到 `None`，`None == false` 为假——**按清单逐条执行，这一条必然 FAIL**。
`prereg_v3_2_code_mapping.md` §5 的缺口表列了 4 条，**没有列这一条**，所以它目前是"无人认领的缺口"。

**修法（二选一）**：(a) `build_manifest` 加一行 `"stage1_attack_arms_read": stage == "calibrate"`（additive，
需补一条测试，但会让 §15.4.4 的 `run_detectors_g.py` 哈希重算）；
(b) 改预注册措辞，把这条核验改成已经存在的两个等价证据——
`inputs.normal_traces.traces_by_variant` 只有正常臂 + `stage1_attack_traces_skipped == 160`。
**建议 (a)**：这是"阶段 1 没读攻击臂"这句话在 manifest 里的**唯一直陈句**，其余都是间接证据。

---

## D-4（BLOCKING）M1 / M2 的哈希口径不一致，"必须等于"按构造不成立

§15.1 的 M1 行写"**整个文件的 sha256**"，M2 行写"`inputs.threshold_manifest_sha256` **必须等于 M1**"；
`freeze_a2_checklist.md` §0 与 §4 的两张续表逐字复述同一句；
§3.3 第 2 条最后一行更明确："`result.json.inputs.threshold_manifest_sha256` 落盘并**等于 manifest 文件的实算 sha256**"。

**实测（第二轮冒烟产物，两条都是真值）**：

```
sha256sum .../v3_2_round2_smoke/stage1/threshold_manifest.json
  8767ce5e9dd8dfb84cbf73dc99026f626c3c272483f627e5af9b63e2973c4eaf   <- §15.4.6 记的 M1
.../v3_2_round2_smoke/stage2/result.json ["inputs"]["threshold_manifest_sha256"]
  6e49face27b0b6fef6a32b146fcb6553954550ac6b2e38698f977c0d7fce88c7   <- M2 落盘的值
```

原因是 `manifest_self_sha256` 的定义：**去掉 `sha256` 字段后**用
`json.dumps(..., sort_keys=True, separators=(",", ":"))` 重算。落盘文件多一个 `sha256` 字段、
用的是 `indent=2` 的另一种序列化，两者**不可能相等**。
`freeze_a2_checklist.md` 步 6 又恰恰用 `sha256sum` 去算 M1，所以审阅者拿到的一定是 `8767…`。

**后果**：M2 这一条是"阶段 2 用的确实是 M1 那份 manifest"的**唯一注册凭据**。按字面执行 → 不等 → 按 §15.5
的纪律要么判结果作废，要么现场改口径；而 G-conf **只跑一次**，现场改口径就是事后改协议。

**修法**：§15.1 / §3.3 / 操作单三处统一为——
M1 记**两个**值：`threshold_manifest.json` 的整文件 `sha256sum`（外部凭据）**与** 文件内的
`sha256` 字段（自哈希）；M2 的"必须等于"指向**后者**，并注明"自哈希 = 去掉 `sha256` 字段后的 canonical-json 摘要，
`verify_manifest` 的 `manifest_sha256` 一项已在阶段 2 重算并强制一致"。

---

## D-5（BLOCKING）`--normal-only-smoke` 不被封存批拦截：阶段 1 可以反复"试跑"

`refuse_sealed_pools`（`run_detectors_g.py:229`）第一行是：

```python
if not getattr(args, "dev_smoke", False):
    return
```

——**只挡 `--dev-smoke`**。而 `freeze_guard` 对 `--normal-only-smoke` 直接 `block["enforced"] = False; return`
（不要求工作树干净、不要求 `--freeze-commit`、不校验 `--prereg-sha256`），`main_v32` 里
`normals_only = stage == "calibrate" or bool(args.normal_only_smoke)`。

于是下面这条命令在**机械上完全合法**，且可以跑任意多次：

```
--stage calibrate --normal-only-smoke --target artifacts/agent_v2/dataset_g/g_conf ...
```

它会解封 G-conf 的 560 条正常臂、拟合标准化器与保形参照、写出一份完整的 `threshold_manifest.json`，
并把**逐折 `far.filtered` / `alpha_eff` / `survivors_at_H` / 三分位切点**打印出来。

**这正是 D1 设立两阶段解封所要防的那件事**：§0 change log 第 4 条的理由原文是
"校准池就是目标批自己的正常臂，**不分两阶段则工作点变成事后可选**"。若阶段 1 可以在封存批上反复重跑，
工作点就**仍然**是事后可选的——只是绕道正常臂而已。
目前拦住它的**只有流程**：`freeze_a2_checklist.md` §5 的"不允许：在 G-conf 上跑任何检测器"与裁定 Q10。
而 §3.3 的标题恰恰是"**必须机械保障，不能只靠流程**"。

**修法（一行）**：把 `refuse_sealed_pools` 的早退条件改成
"`dev_smoke` **或** `normal_only_smoke` **或** 未通过 `freeze_guard` 强制的任何运行"，即：
**任何指向带 `SEALED.json` 的批次的运行，都必须是 `enforced == True` 的非冒烟运行**。
（`sealed_pool_dirs` 已经写好了识别逻辑，只是没被这条路径调用。）

---

## D-6（BLOCKING）§12.2 阶段 2 的命令：`--anchor X` 非法，且缺两个必需开关

**证据（实跑）**：

```
run_detectors_g.py: error: argument --anchor: invalid choice: 'X' (choose from 'c', 'e_view', 'x')
```

`--anchor` 的 choices 是 `sorted(trm3_g.V32_ANCHORS)` = `('c', 'e_view', 'x')`，**小写**。
`freeze_a2_checklist.md` 步 4 写的是 `--anchor x`，正确。

此外 §12.2 阶段 2 的命令块只列了 9 个开关加一个 `...`，但：
- `--target` 是 `required=True`（`run_detectors_g.py:1204`）；
- `--cal-from-target` 缺失会被 `main_v32` 明确 `SystemExit`
  （"`--stage calibrate/score` is the two-stage form of `--cal-from-target`; pass it explicitly"）；
- `--cal-folds` / `--fold-key` / `--view` / `--tag-scope` / `--alpha` 会进 `cell_matches` 的比对，漏传即用默认值。
  **注意 `--fold-key` 的默认值是 `trm3_g.DEFAULT_FOLD_KEY = "scenario_mod"`**——正是 DATA-1 判为
  "在 G-conf 上与 fixture 共线"而被撤换掉的那个旧折键（`--cal-folds` 默认 3、`--view` 默认 V1、
  `--tag-scope` 默认 message、`--alpha` 默认 `trm3.ALPHA`，这几个碰巧与主格一致）。
  在**阶段 2** 漏传 `--fold-key fixture_rank_mod` 会被 `fold_assignment_sha256` 一项当场 `SystemExit`（响亮失败，好）；
  但在**阶段 1** 漏传就是**静默**用旧折键建全部阈值——而阶段 1 只跑一次。

把这些藏在 `...` 里，与"开启后不允许任何调参、任何补跑"是矛盾的：**只跑一次的命令必须逐字写全**。

**修法**：§12.2 阶段 2 的命令块按 `freeze_a2_checklist.md` 步 4 的形式**逐字写全**（含 `--target`、
`--target-labels`、`--cal-from-target`、`--cal-folds 3`、`--fold-key fixture_rank_mod`、`--cal-filtered-only`、
`--view V1`、`--tag-scope message`、`--alpha 0.10`、`--window-s 8`、`--force-h 352 --expect-h 352`、
`--freeze-commit <B′>`、`--prereg-sha256 <SHA>`、`--labels-sha256 <SHA>`、`--output-root`、`--run-name`），
并把 `--anchor X` 改成 `--anchor x`。

---

## D-7（SHOULD-FIX）攻击臂哈希两端都算了，但从不比较

- **阶段 1**：`build_manifest` 落盘 `stage1_attack_traces = attack_trace_census(args.target)`，
  其中含 `sha256`（攻击 arm `trace.json` 集合的摘要）与 `per_dir`。
- **阶段 2**：`main_v32` 再算一次同名的 `attack_census`，落进 `result.json.attack_trace_census`。
- **`verify_manifest` 的 15 项检查里没有任何一项提到 attack**（实测：在 `verify_manifest` 函数体里 grep
  `attack` 零命中）。

也就是说：两份摘要都在，**没有代码、也没有清单条目要求把它们对起来**。
`freeze_a2_checklist.md` §4 的 M1 续表只要求记 `stage1_attack_traces_skipped`（计数），
M2 续表才要求记 `attack_trace_census.{count, sha256}`——**一边记数、一边记哈希，永远对不上**。

**连带的检测盲区**：`g_conf_seal.verify` 只遍历 `seal["traces"]["traces"]` 里**已记录的行**，
所以在阶段 1 与阶段 2 之间**新增**一条攻击 trace 目录：
`--verify` 看不见（它不枚举磁盘）、`normal_trace_set_sha256` 看不见（只管正常臂）、
`seal_check` 看不见（见 D-11）——而 `load_g` 的 `rglob("trace.json")` **会把它读进阶段 2 的评价池**。
加上 D-2（`--verify` 本来就跑不起来），**攻击臂内容在两阶段之间目前是零校验**。

**修法**：`verify_manifest` 加一项
`attack_trace_set_sha256`：`payload["stage1_attack_traces"]["sha256"] == attack_census["sha256"]`，
不符即 `SystemExit`；操作单 M1 续表同步改成记 `stage1_attack_traces.{count, sha256}`。

---

## D-8（SHOULD-FIX）`cell` 守卫只比 5 个键，注册的是"冻结常量逐位相符"

§3.3 第 2 条注册的判据是："本次运行的统计量在 `manifest.cells` 的键集合里，**且该格的冻结常量
（`frozen_constants` 与该格自身的常量）与本次运行逐位（bit for bit）相符**"。

实现（`verify_manifest`，`run_detectors_g.py:3525-3547`）只比 **5 个键**：

```python
for key, value in (("view", ...), ("tag_scope", ...), ("alpha", ...),
                   ("folds", ...), ("fold_key", ...)):
```

而 manifest 的 `cell` 块实际存了 **18 个键**（实测）：
`alpha, alpha_extra, bucket_size, cal_filtered_only, fold_key, folds, force_h, h_min_survivors, layers,
min_bucket_traces, min_channel_traces, min_channel_windows, or_arm, rare_threshold, standardise,
statistics, tag_scope, view`。**其余 13 个落盘但从不核验**（全文只有 3533 / 3537 两行读 `payload["cell"]`）。

**具体危害**：阶段 2 漏传 `--cal-filtered-only` 会改变 `fold_pools` 的正常臂划分与
`gate_block(..., filtered_only=...)`，即**门 F1 的分母与 FAR 分层会静默变**，而守卫全绿；
漏传 `--force-h 352` 会让 H 回落到 `--h-min-survivors 90` 规则（§0 change log 第 5 条已量化过：
`n_cal = 136` → H ≈ 166、60% 的 X 窗口不可达）。**G-conf 只跑一次，没有第二次发现的机会。**

**修法**：`cell_matches` 改成"逐键比 `payload["cell"]` 的全部键"，把本次运行的同名参数按同样的类型规约后比对；
或至少把 `cal_filtered_only` / `force_h` / `h_min_survivors` / `bucket_size` / `min_bucket_traces` /
`min_channel_windows` / `min_channel_traces` / `rare_threshold` / `standardise` 九个补进去。

---

## D-9（SHOULD-FIX）注册过的两条 `SystemExit` 没有落地

§3.3 第 2 条第二个 bullet："manifest 的 `prereg.sha256` / `freeze_commit` 与本次运行不符 → **`SystemExit`**"。

15 项检查里没有这两项（清单：`manifest_kind` · `manifest_sha256` · `fold_assignment_sha256` ·
`fold_assignment_self_consistent` · `normal_traces_sha256` · `target_labels_sha256` · `cell_matches` ·
`head_is_freeze_commit` · `manifest_code_commit` · `cells_present` · `cells_complete_on_every_fold` ·
`length_tertiles_present` · `matched_alpha_inputs_present` · `normal_trace_set_sha256` ·
`sealed_trace_set_sha256`）。

现状是**间接、部分**覆盖：`manifest_code_commit`（manifest 的 `code_commit == HEAD`）+
`head_is_freeze_commit`（`HEAD == --freeze-commit`）合起来能保证两阶段跑在同一个 commit 上；
但 **manifest 里的 `prereg.sha256` 与本次运行的 `--prereg-sha256` 从不比对**——
`freeze_guard` 只把本次的 `--prereg-sha256` 与**磁盘上的**预注册比，manifest 那一份被无视。
两阶段之间若预注册文件被改（工作树脏会被 `freeze_guard` 拦，但 `git stash` / 重做提交不会），
阶段 2 不会有任何反应。

**修法**：加两项检查——`payload["prereg"]["sha256"] == discipline["prereg_sha256"]` 与
`payload["freeze_commit_resolved"] == resolved`。两者都是 manifest 已有的字段，additive。

---

## D-10（SHOULD-FIX）§3.3 第 4 条的"次序纪律"没有机械保障

原文："`threshold_manifest.json` 的 `created_at` **必须**早于任何阶段 2 的 `result.json` 的 `created_at`；
顺序倒置 = 结果作废并如实记录。"——写在标题为"**必须机械保障，不能只靠流程**"的 §3.3 里。

manifest 有 `created_at`（实测 `2026-09-07T19:40:43-0700`），`result.json` 有 `created_at`，
但 `verify_manifest` 不比较它们，`main_v32` 也没有。§15.3 第 3 条要审阅者"两个 `created_at` 的顺序正确"——
**只能靠人眼**，而这恰恰是 §3.3 声称不接受的那种保障。

**修法**：`verify_manifest` 加一项 `manifest_created_before_run`：
`payload["created_at"] < time.strftime(...)`（阶段 2 的当前时刻），不符即 `SystemExit`。

---

## D-11（SHOULD-FIX）`seal_check` 不重算磁盘上的 trace，`sealed_trace_set_sha256` 近乎空转

`seal_check` 的 docstring 说"the only way to say 'stage 1 read exactly the sealed content' is to
**recompute the seal's own `trace_json_set_sha256` at both moments**"。实际代码做的是：

```python
seal = json.loads(path.read_text(...))
pairs = [(t["path"], t["trace_json_sha256"]) for t in seal["traces"]["traces"]]
rows[-1]["recomputed_from_seal_rows"] = sha256("\n".join(sorted(...)))
```

——**重算的对象是 `SEALED.json` 自己的行**，一个 `trace.json` 都没有打开。所以：

- `self_consistent` 只能发现"有人手改了 `SEALED.json` 的某一行但忘了改总摘要"；
- `verify_manifest` 的 `sealed_trace_set_sha256` 比较的是**两次从同一个只读文件里读出来的同一个字符串**
  （`SEALED.json` 的 mode 是 `0o444`，subset 根是 `0o555`）——它能发现的只有"`SEALED.json` 被替换过"，
  发现不了任何 trace 内容的改动。

这与 §12.2 里"**这一次核验证明阶段 1 没有改写任何被封存的内容**"的措辞给人的强度印象不符。
真正做内容重算的是 D-2 里那条跑不起来的 `--verify`。

**修法**：不改代码也行——把 §12.2 步 1.5 的期望输出写死成 `g_conf_seal.py --verify .../SEALED.json` 的
`"verified": true`，并在 §15.3 第 3 条注明"harness 的 `seal` 块是**自洽**检查，内容重算由 `--verify` 承担"。
改代码的版本：`seal_check` 在 `--stage score` 时对 `seal["traces"]["traces"]` 逐条 `sha256_file` 重算
（720 条 `trace.json`，秒级）。

---

## D-12（SHOULD-FIX）两次封存核验没有时间戳

§15.3 第 3 条要核："**两次封存哈希核验的时间戳分别早于阶段 1、落在阶段 1 与阶段 2 之间**"。

`seal_check` 返回的块（实测，`v3_2_round2_smoke/stage1/result.json["seal"]`）只有四个键：
`{when, any_sealed, pools, rule}`——`when` 是字符串 `"stage1_start"` / `"stage2_start"`，**没有时间**。
可读的时间只有两个 `result.json` 的 `created_at`（运行**结束**时刻，`time.strftime` 在 payload 组装处），
以及运行日志里人工抄的 `--verify` 输出（而 `--verify` 本身不打印时间）。
所以这条清单项**目前无法执行**。

**修法**：`seal_check` 加 `"checked_at_utc": datetime.now(UTC).isoformat()`；
`g_conf_seal.verify` 的输出 JSON 同样加一个 `verified_at_utc`。两处都是 additive。

---

## D-13（SHOULD-FIX）开启前置 `g_conf_data_gates.json` 的产出命令从未被写死

§12.1 第 3 条把它列为**缺一不可**的开启条件（"`created_at` 早于任何 G-conf 检测器产物"），
§15.2 第 5 条、§15.3 第 1b 条、代码映射 §4 第 21 条都要读它。但：

- §9.3 只给了 **G-dev** 的命令，且**没有 `--output`**：
  `g_dev_data_gates.py --labels .../g_dev/final_unblinded.jsonl --run-dir .../g_dev --h 352 --looks-per-token 0.90`
  ——照抄这条命令在 G-conf 上跑，**不会产生任何文件**（`main` 里 `if args.output:` 才写盘）；
- G-conf 上的命令、`--output` 的**路径**（`g_conf_data_gates.json` 放哪？`artifacts/agent_v2/dataset_g/` 下？）
  在预注册、操作单、代码映射三份文件里**都没有**；
- §15.3 第 1b 条要 `gates[D5_attack_arm_e_yield].denominator == 160`，而 D5 的分母需要
  `--metadata` 或 `--run-dir` 提供 variant / `multi_turn_user` 通道信息——**不传 `--run-dir` 就拿不到 160**，
  但没有任何一份文件写明必须传。

**修法**：在 §12.1 第 3 条（或操作单里新开一步）写死一条命令，含 `--output`、`--run-dir`、
`--h 352 --looks-per-token 0.90`，并规定输出路径；`freeze_a2_checklist.md` §4 的 B′ 续表补一行记它的 sha256。

---

## D-14（SHOULD-FIX）折键依赖的 subset config 解析到"本次会话的 worktree 路径"

`fixture_provenance` → `io_g.fixture_map` → `subset_config_for_run(run_dir)`，
后者读 `run_summary.json["config_path"]` 并**优先使用它**（只要该路径存在）。

**实测（只读元数据）**：

```
artifacts/agent_v2/dataset_g/g_conf/resume_20260907T191149Z_1_1/run_summary.json
  config_path = /home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/configs/dataset_g/g_conf.json
io_g.subset_config_for_run('artifacts/agent_v2/dataset_g/g_conf')
  -> 同一条 worktree 路径，sha256 = 62728c5cbedda7c8c1b096c1c92cd9b4785265188f14ddaed1cb55b58294ec73
```

哈希**当下**与 §15.4.5 的冻结值相同，所以现在无害。但：

1. 这是一条**一次性 agent worktree** 的路径。worktree 一旦删除（这是 worktree 的正常归宿），
   解析会落到 `_config_by_experiment_id("dataset_g_conf")` 的回退，指向
   `REPO_ROOT/configs/dataset_g/g_conf.json`——**换了一棵树、可能换了一个 commit**，而 `fixture_rank_mod`
   的折图完全由这份 config 的 `scenarios[*].factory.fixture_id` 决定；
2. worktree **不删**也危险：从主仓库的冻结提交上跑阶段 1，读到的仍是 worktree 那份，
   而 worktree 的 `configs/` 不受 `--freeze-commit` 与 `git status` 约束；
3. **没有任何断言**把用到的 config 哈希钉到 §15.4.5 的 `62728c5c…`——`fixture_provenance` 只是把
   `sources[*].sha256` 记进 manifest，`verify_manifest` 不看它。
   （两阶段之间是安全的：`fold_assignment_sha256` 在阶段 2 会用同一份 config 重算并强制相等；
   不安全的是 **A′/B′ → 阶段 1** 这一段。）
4. §12.2 的阶段 1 命令**没有传 `--fixture-config`**，所以走的正是这条自动解析。

**修法**：§12.2 的两条命令都显式传
`--fixture-config configs/dataset_g/g_conf.json`；并在 §15.3 加一条"`calibration_design.fixtures.sources[*].sha256`
必须等于 §15.4.5 的 `g_conf.json` 哈希"。

---

## D-15（SHOULD-FIX）§12.2 的命令没传 `--labels-sha256`，标签守卫空转

§15.2 第 3 条："标签文件的 sha256 …… 经 **`--labels-sha256`** 传入并落进 `result.json.inputs.label_sha256`"；
§3.3 第 3 条要求 `labels_sha256_missing == []`。

§12.2 的阶段 1 命令只有 `--freeze-commit <B> --prereg-sha256 <SHA>`，**没有 `--labels-sha256`**。
`freeze_guard` 的逻辑是 `missing = [v for v in (args.labels_sha256 or ()) if v not in present]`——
不传时 `missing` 恒为 `[]`，`labels_sha256_missing == []` **恒真**，守卫检查通过但什么都没验。
（`result.json.inputs.label_sha256` 里仍会有实算值，可事后人工比对；但那是"记录"，不是"守卫"。）

**修法**：§12.2 两条命令都补 `--labels-sha256 <B′ 上 final_unblinded.jsonl 的 sha256>`。

---

## D-16 / D-17（NOTE）"只跑一次"既没有机械留痕，也没有一致的定义

**D-16**：`main_v32` 写产物的部分是无条件覆盖——
`manifest_path.write_text(...)`、`(out_dir / "result.json").write_text(...)`，
没有 `exists()` 检查、没有 `x` 模式、没有"已跑过"的标记文件。
默认 `run_name = f"v32_{view}_{'-'.join(names)}_a{alpha:g}_{stage}"`，所以**同一条命令重跑会覆盖自己的上一次结果，
不留任何痕迹**。裁定 Q10（"阶段 1 只跑一次，manifest 的 sha256 当场记录"）依赖的正是"重跑会换哈希"这一点——
但那要求**有人在重跑之前就把哈希记下来了**。
建议：`--stage calibrate` 在目标 manifest 已存在且 `--force` 未给时直接 `SystemExit`（additive，两行）。

**D-17**：§12.2 的标题是"**两阶段解封，一次性**"、阶段 2 标注"**只跑一次**"，紧接着的说明却是
"S2（S vs M）与 S-J（J 格，**两个配对口径**）各自一次，全部用**同一份** manifest"——
即实际是 **3–4 次** `--stage score` 运行（主格 S vs P、S vs M、S-J 主口径、S-J 敏感性口径；
第二轮冒烟就落了 `stage2` / `stage2_S_vs_M` 两个目录）。
"一次"在这里指的是"每个已注册的格各一次、且不允许在看到结果后追加任何格"，但正文没有这么写。
建议：§12.2 改写成"阶段 2 共 **N** 次运行，N 与每次的完整命令行在冻结时逐条写死；任何第 N+1 次运行 = 新 proposal"。

---

## D-18 / D-19（NOTE）封存文件自身的两处脆弱点

**D-18**：`SEALED.json` 的 `root` 字段是**绝对路径**
`/home/wzh/Agent-Moe-Research/artifacts/agent_v2/dataset_g/g_conf`（因为 `artifacts` 是指向主仓库的符号链接，
`build_seal` 里 `root.is_relative_to(ROOT)` 为假，走了 `else str(root)` 分支）。
`verify` 里 `root = ROOT / seal["root"]`——`pathlib` 的 `/` 遇到绝对路径会**整段覆盖左侧**
（实测：`Path('/home/.../worktrees/...') / '/home/wzh/Agent-Moe-Research/artifacts/...'`
= `/home/wzh/Agent-Moe-Research/artifacts/...`）。
当下无害（符号链接指向同一棵树），但**在任何 checkout 里 `--verify` 核验的都是主仓库那一份**，
与 `--freeze-commit` 的语义脱钩。建议封存记录改存相对路径，或在 `verify` 的输出里显式打印解析后的绝对根。

**D-19**：`g_conf_seal.verify` 只重算 `traces[*]`（`trace.json` + `manifest.jsonl`）与
`packet` / `private_mapping` 两个条目。**不校验**：
`frozen_inputs.subset_config`（就是 D-14 里那份决定折图的 `g_conf.json`！）、
`frozen_inputs.dataset_manifest`、`run_configs`（7 个 `resolved_experiment_config.json`）、
`extra_files`（7 个 precheck / packet 产物）；**不检测新增文件**（只遍历已记录的行）；
**不检查只读位是否还在**（`read_only.applied` 只是封存当时的记录）。
建议：`verify` 补上这三类条目的重算 + 一次 `os.walk` 的"多出来的文件"检查 + `st_mode` 检查，
输出里逐类给计数。

---

## D-20 / D-21（NOTE）两处会误导审阅者的小事

**D-20**：§15.3 第 1b 条要 `gates[D5_attack_arm_e_yield].denominator == 160`（分母名必须是
`attack_bearing_episodes`），§15.3 第 3 条要 `stage1_attack_traces_skipped == 160`。
**这是两个不同的量，碰巧同值**：
前者是"载攻击的 **episode** 数"，后者是"攻击 **arm 目录**数"。
按 `configs/dataset_g/g_conf.json` 的 `attack_scenarios_by_channel`（`direct_user` 56 /
`multi_turn_user` 56 / `tool_output` 48），G-conf 的攻击 episode **总数**是 `160 + 56 = 216`，
其中 56 条是 `multi_turn_user` 的注入前 episode——所以 §9.3 的 D5 行才要专门写
"不是 `attack_episodes` / 216"。G-dev 上这两个量是 264（arm 目录）与 264（载攻击 episode），也同值，
所以**冒烟跑不出这条歧义**。建议在 §15.3 两处各加一句括注点明量纲不同。

**D-21**：`g_dev_data_gates.py` 的 `main` 里打印的第一行硬编码为
`f"G-dev data gates (prereg 12.2) -- ..."`。在 G-conf 上跑同一份代码（§9.3 明确要求这么做）时，
终端与运行日志会写着 "G-dev"，而 §12.1 第 3 条要靠这份日志证明"数据门先于路由"。
建议改成按 `--labels` 路径推断的批次名，或加一个 `--subset` 标签参数（纯打印，additive）。

---

## 附：本次审阅实际执行过的命令（全部只读元数据 / 只到 argparse）

```bash
cd /home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363
PY=/home/wzh/Agent-Moe-Research/.venv/bin/python

# 1. 臂结构与 720 / 160（只读 SEALED.json，不读任何 trace）
$PY - <<'PY'
import json, collections
d = json.load(open('artifacts/agent_v2/dataset_g/g_conf/SEALED.json'))
tr = d['traces']['traces']
print(d['traces']['trace_count'], d['traces']['incomplete'])
print(collections.Counter(p['path'].split('/')[-1] for p in tr))
PY
# -> 720 []   Counter({'benign_control': 280, 'clean': 280, 'attack': 160})

# 2. §12.2 阶段 1 命令逐字试跑（目标目录不存在，只走到 argparse）-> D-1
# 3. §12.2 阶段 2 的 --anchor X 试跑 -> D-6
# 4. 封存核验命令逐字试跑 -> D-2
PYTHONPATH=$PWD/src $PY scripts/research_v4/g_conf_seal.py --verify artifacts/agent_v2/dataset_g/g_conf
# -> IsADirectoryError

# 5. M1 / M2 哈希口径 -> D-4
sha256sum artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage1/threshold_manifest.json
$PY -c "import json;print(json.load(open('artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage2/result.json'))['inputs']['threshold_manifest_sha256'])"

# 6. 不存在的字段 -> D-3
grep -rn stage1_attack_arms_read scripts/ src/ tests/    # 零命中

# 7. subset config 的自动解析 -> D-14
PYTHONPATH=$PWD/src $PY -c "from research_v2 import io_g;print(io_g.subset_config_for_run('artifacts/agent_v2/dataset_g/g_conf'))"
```
