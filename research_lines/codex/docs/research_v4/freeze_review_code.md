# 冻结审阅（LENS = 代码）——`docs/research_v4/detector_prereg_v3_1.md`

审阅对象：`docs/research_v4/detector_prereg_v3_1.md`（冻结候选，sha256 `3487cec8…62879a0`，实算一致）。
审阅口径：**代码是否做了冻结正文所说的事**。逐条对着 §16.1 的 47 项映射打开模块 / 函数 / 开关与测试，
并实跑了全部相关测试与 `scripts/research_v3/verify_m_only_vs_frozen.py`。
数据纪律：`artifacts/agent_v2/dataset_g/g_dev` 与 `annotations/g_dev` 全程**未读取**；
下文关于 G-dev 臂划分的结论全部由 `configs/dataset_g/g_dev.json`、`scripts/research_v4/run_agent_v3.py`、
`src/research_v2/io_g.py` 与 `docs/research_v4/g_dev_run_log.md` 推出，没有触碰封存路由。

工作树 HEAD = `d70e713`（`git rev-parse HEAD`）。

---

## 0. 先说通过的部分（实跑证据）

| 核验 | 命令 / 位置 | 结果 |
|---|---|---|
| PV31 / DG / PROB 测试 | `pytest tests/test_research_v4_prereg_v3_1.py tests/test_research_v4_data_gates.py tests/test_research_v4_prob_channels.py` | **107 passed, 15 subtests**（57 / 21 / 29，与 §16.1 的括号数逐一相符） |
| v4 / v3 / v2 全量 | `pytest tests/test_research_v4_*.py tests/test_research_v3_*.py tests/test_research_v2_*.py` | **552 passed, 93 subtests**（v4 246 / v3 201 / v2 105） |
| 冻结 OLMoE 侧未被碰 | `scripts/research_v3/verify_m_only_vs_frozen.py` | `[replica] vs frozen (identical): 0 trace(s) differ, 0 of them in the alarm SET` —— §15.3 第 12 条 **PASS** |
| §19 全部哈希 | 逐个 `sha256sum`（36 个条目） | **全部逐位相同**，含两个解盲标签文件、5 个代码 / 配置文件、全部文档 |
| 12 格 H 表 | `trm3_g.H_FREEZE_TABLE` vs `artifacts/agent_v2/dataset_g/h_rule/h_rule_g.json` | **12 / 12 逐值相同**（352/373、314/328、284/288；314/332、301/312、278/282）；`survivors_at_H = 90/279`、`censored_paths = 89`、`censored_endpoints = 9 297 / 80 572 = 11.54%`、第 352 个 look 的 token 中位 379 / max 393 —— 与 §2.6 冻结句逐位相同 |
| 会话轮数配置 | `run_detectors_g.session_turns_map(configs/dataset_g/g_session.json)` | **100 个会话，`T_max` 3:34 / 4:33 / 5:33** —— 与 §7.5 / §5 相同 |
| §19.7 复现命令的每个 flag | `run_detectors_g.py --help` | **全部存在**，无拼写错误；OR 臂那一行的 `--or-arm / --alpha-extra / --window-prob` 也存在 |
| 信息边界（推理期只读路由） | `trm3_g.py` 全文 grep `variant` / `labels` / `attack_family` / `wording_tier` / `domain_group` | 这些字段**只出现在 `view_anchors` / `evaluate_g` / `session_budget`（第 2065 行之后，评价侧）**；四族统计量的 `fit` / `per_token` / `stream` 只读 `top_k_ids` / `router_logits` / `channel_tags`。**边界成立** |
| 匹配实测 FAR 真的进检验 | `run_detectors_g.compare_cells` + `trm3_g.hits_at_alpha` | `rows.matched` 的 `bootstrap` 与 `mcnemar` 都来自 `hits_at_alpha(secondary, matched_alpha)` 的**重算命中**；`primary_row = "matched"`；`rows.nominal` 并列 —— §7.4 / #27 **属实** |
| 冻结常量 | `trm3_g` 第 84–120 行 | `G_CAL_TERTILE_CUTPOINTS = (219, 379)`、`TOLERANCE_BANDS = (0,4,5,8)`、`TEMPORAL_D = 24`、`TEMPORAL_ENTER/EXIT = 0.10/0.25`、`ALPHA_EXTRA = 0.02`、`E_DENOMINATOR_ARMS = (attack,)`、`ALL_LAYERS = range(24)`、`H_MIN_SURVIVORS = 90`、`MIN_CHANNEL_WINDOWS/TRACES = 30/10` —— 与 §5 逐行相符 |
| 滞回语义 | `trm3_g.hysteresis_track` | 首段 `p ≤ enter`、退出 = D 个连续 `p_inst > 0.25`、**再进入用 `p_inst ≤ enter`** —— 与 §2.7 / 裁定 §20.2 第 1 条一致；`p_inst` 只写进 JSONL 与 `metrics.hysteresis`，`far.*` 的分子全部来自 `summary.alarm` |
| A-raw / 稀疏回退 / 逐池标签 / 归因 / OR 臂预算 | #39 / #10 / #47 / #41 / #36 | 逐条打开代码核对，**与正文一致**（`calibrate_g(pooled_fallback=True)` 是生产默认，`fit_channel_standardiser` 底层默认仍严格；`config_for_g(alphas=)` 给出 α=0.12、w=(0.8333,0.1667)、rank 28/5、`alpha_eff=33/280`） |

**结论：§16.1 的 47 项里，除下面点名的以外，代码确实做了正文所说的事。**

---

## 1. BLOCKING

### B1 —— 冻结守卫校验的是**草案**，不是本文件；§19.7 的复现命令必然被守卫拒绝

`scripts/research_v4/run_detectors_g.py:54`

```python
PREREG_PATH = ROOT / "docs" / "research_v4" / "detector_prereg_v3_1_draft.md"
```

`freeze_guard`（同文件 100 / 108–113 行）用它算 `prereg_sha256`，并与 `--prereg-sha256` 比较；
`result.json` 的 `prereg.path` / `prereg.sha256`（1216–1218 行）记录的也是这个路径。

实算：

```
af590f61…772c  docs/research_v4/detector_prereg_v3_1_draft.md
3487cec8…879a0 docs/research_v4/detector_prereg_v3_1.md
```

后果，逐条对照正文：

* **§15.2 第 1 条**（"本文件的 sha256 … 写入每个 `result.json` 的 `prereg_sha256`（`--prereg-sha256`）"）与 **§19.6**
  （"本文件 … 写入提交 message 与每个 `result.json` 的 `prereg_sha256`"）**不可实现**：
  按正文把 `3487cec8…` 传给 `--prereg-sha256`，守卫在
  `if args.prereg_sha256 and prereg_sha != args.prereg_sha256: raise SystemExit(...)` 处**拒绝运行**，
  也就是说 **§19.7 的那条冻结后主格命令按字面照抄一定跑不起来**。
* 若审阅者改传草案的哈希以绕过，则 **§15.3 第 2 条的 `prereg_sha256_matches == true` 校验的是被本文件取代的草案**，
  冻结凭据指向错误的文档。
* `PV31::Item43FreezeGuardTest::test_a_matching_commit_and_hashes_pass` 用
  `RUNNER.sha256_file(RUNNER.PREREG_PATH)` 自证，**不钉住路径**，所以测试对这个错误完全不敏感。

**修法（二选一，必须在冻结前做）**：把 `PREREG_PATH` 改指 `detector_prereg_v3_1.md`（并给它一个钉住文件名的测试）；
或在正文里明写守卫校验的是草案——但那与 §15.2 第 1 条 / §19.6 直接冲突，不建议。

---

### B2 —— G-dev 的 `benign_lexical` 与 `legitimate_refusal` 两臂在装载时会变成 `clean`；§4 的逐臂 FAR、F2 第二条、#22、§15.3 第 7 条都取不到

链条（全部是代码 / 配置，未读 g_dev 产物）：

1. `configs/dataset_g/g_dev.json` 的 `collection_plan`：

   | group | arms | trace_count |
   |---|---|---:|
   | core_72_cells | clean, benign_control, attack | 432 |
   | attack_supplement | attack | 120 |
   | **benign_lexical** | **clean** | 24 |
   | **legitimate_refusal** | **clean** | 24 |

   两个"困难正常变体"是**以 `clean` 臂采集的**（`scenarios[*].factory.normal_variant` 才记录它们的真实身份）。
2. `scripts/research_v4/run_agent_v3.py:525` `trace_dir = run_root / scenario["pair_group_id"] / arm_name`，
   第 228 行 `"perturbation": {"arm": arm_name, …}`，第 150 行 `trace_id = f"{base_task_id}--{arm_name}"`
   —— 三处写进磁盘的都是字符串 `"clean"`。`metadata` 里**没有** `scenario_role` / `normal_variant` 字段。
3. `src/research_v2/io_g.py:1061 _variant_of()` 只看 `trace["perturbation"]["arm"]` 和
   `Path(trace_dir).name`，二者都是 `clean`；`GEpisode`（第 130–163 行）也**没有** `scenario_role` /
   `normal_variant` 字段，`load_g` 用 `rglob("trace.json")` 装载，group 目录名被丢弃。

独立佐证：`g_dev_run_log.md` §7.1 的逐臂表只有 **clean / benign_control / attack** 三列，
clean 列是 **192 trace / 240 episode**（= 144 core clean + 24 benign_lexical + 24 legitimate_refusal，
与 §9.3 的 `scenario 角色` 表 24 / 24 相加吻合）。

对正文的后果：

* **§4**（"逐臂分列：`clean` / `benign_control` / `benign_lexical`，各自两个分母"）——
  `trm3_g.evaluate_g` 第 2354 行按 `io_g.NORMAL_VARIANTS` 建块，`far["benign_lexical"]` 的两个分母都会是 **0 条**（空块）。
* **§4**（"`legitimate_refusal` … **不在任何 FAR 分母里**，各自单列"）——**被违反**：
  这 24 条会以 `variant == "clean"` 落进 `far["clean"]` 与 `far["all"]/["filtered"]`，
  即 clean 的 FAR 分母被正文明令排除的对照臂污染约 **24/240 = 10%**。
* **§9.2 F2** 的第二条 `benign_lexical − clean <= 0.10` —— `far["benign_lexical_minus_clean"]` 恒为 `None`，**门不可评**；
  §9.2 里"`benign_lexical` n = 24，**分辨率 0.042**"这句话所设的口径在数据上不存在。
* **§16.1 #22**（`evaluate_g.classes.legitimate_refusal`，代码第 2516 行 `e.variant == io_g.LEGITIMATE_REFUSAL`）
  与 **§15.3 第 7 条**（"`excluded_by_arm` 里 `legitimate_refusal` … 都在 `arm_not_in_e_denominator` 下"）
  —— 两处都会是**空的 / 键不存在**，审阅清单第 7 条无法机械核对。
* **§14 第 3 条**要求按轨迹类报 `legitimate_refusal`、**§12.2 D1** 的
  `excluded_legitimate_refusal_arm` 计数（`g_dev_data_gates.py:305`，`variant_of` 的三级回退也只会读到 `--clean`）
  —— 同样恒为 0。（D1 / D5 的**数值**不受影响：这些 episode 因 `arm != attack` 被跳过，本来就不进分子分母。）

**这不是"实现待补"，是冻结正文的四处约束与已生成的数据不兼容。** 修法二选一：
(a) 让 `io_g` 从 group 目录名或 `configs/dataset_g/g_dev.json` 的 `factory.normal_variant` 还原真实臂
（**需要改代码 + 一次重新装载**，并把 §15.3 第 7 条的核对补上）；
(b) 在正文里把 `benign_lexical` / `legitimate_refusal` 降级为"本批不可分离，合并在 clean 臂内"，
同时**删掉 F2 的第二条**、改写 §4 的分母纪律、改写 #22 与 §15.3 第 7 条。
无论哪条，**冻结前必须动**——否则 F2 会以"不可评"而不是判定进入报告，而 clean 的 FAR 分母是错的。

---

## 2. SHOULD-FIX

### S1 —— §4 的 G-dev "按 episode 计"一行自相矛盾

§4 写"按 trace 计：clean 144、benign_control 144、benign_lexical 24、legitimate_refusal 24、attack 264"，
紧接着写"按 episode 计：clean 240、benign_control 192、attack 352"。后者合计 784（= 全池），
却**只列了三个臂**：24 条 benign_lexical 与 24 条 legitimate_refusal 的 episode 无处安放。
对照 `g_dev_run_log.md` §7.1，240 是 **192 clean + 24 benign_lexical + 24 legitimate_refusal**，
真正的 clean 臂只有 **192 条 episode**。（这正是 B2 的表面症状；即使 B2 按 (a) 修好，这一行仍要改成
clean 192 / benign_control 192 / benign_lexical 24 / legitimate_refusal 24 / attack 352。）

### S2 —— §9.2 F8 / §15.2 第 4 条列的"同时断言"，有一半代码不做

`run_detectors_g.frozen_assertions`（460–536 行）**只产出五行**：
`horizon_H` / `attainability` / `layer_band` / `n_reference` / `tag_scope`
（`PV31::ResultJsonContractTest::test_the_guard_and_assertions_blocks_are_present` 明确断言就是这五个）。

* `n_reference` 行是 `"expected": int(args.expect_n_reference) if args.expect_n_reference else None`，
  `ok` 为 `(not args.expect_n_reference) or …` —— **不传 `--expect-n-reference` 就是恒真的空转**
  （`_args` 第 1043 行 `default=None`）。而 **§19.7 的复现命令没有 `--expect-n-reference 279`**，
  所以 §15.2 第 4 条"断言 `calibration.n_reference == 279`"在冻结命令下**不会被强制**。
* **`alpha_eff == 28/280` 根本没有断言**：`attainability` 行只校验 `rank >= floor`（floor 默认 1），
  `alpha_eff` 只是被记录。
* **`view == "V1"` 与 `statistic in {"S","P"}` 也没有断言**。`horizon_H` 的期望值来自
  `frozen_h(tag_scope, view, w)`，所以 `--view V3` 会去断言 352→284 并**通过**，格错了守卫不响。

修法：§19.7 加 `--expect-n-reference 279`；`frozen_assertions` 增加 `alpha_eff` / `view` / `statistic` 三行
（或把 §9.2 F8 / §15.2 第 4 条改写成"记录并由审阅者核对"）。

### S3 —— §15.3 第 13 条的测试计数与实跑不符

正文："冻结前末次：**532 passed, 93 subtests**；其中 v4 226、v3 201、v2 105。"
本轮实跑（同一工作树）：**552 passed, 93 subtests**，v4 **246** / v3 201 / v2 105。
v4 逐文件：prereg_v3_1 57、data_gates 21、prob_channels 29、statistics 14、channel_fallback 12、
io_g 15、gbridge 23、detectors_g 26、g_dev_missing 49 = 246。
全绿，但审阅者按第 13 条机械比对会对不上，差 20。

### S4 —— `horizon_H` 断言对"表外格"是空转

`frozen_assertions` 第 488 行 `"ok": expected is None or measured == expected`。
`frozen_h` 只覆盖 w ∈ {4, 8}，任何别的窗宽（例如 `--window-s 6`）都会得到 `expected = None` → `ok = True`。
正文 §2.6 说"12 个值 … 由 `frozen_assertions` 的 `horizon_H` 行**逐格断言**"，
但对不在表里的格它不是断言而是放行。行内的 `note` 说明了这一点，正文没有。
（建议：非冒烟运行遇到表外格直接 `SystemExit`，或在 §2.6 加一句限定。）

---

## 3. NOTE

### N1 —— §19.7 用 `--outputs primary`，拿不到 §2.8 / §14 第 4 条要的逐 token 行
`run_cell` 只在 `args.outputs == "all"` 时构造 JSONL 行（640 行），`main` 只在 `rows` 非空时写 `outputs.jsonl`。
`--outputs primary` 下 `p_inst / hysteresis_state / hysteresis_e0 / hysteresis_segment / view / statistic /
episode_index / session_id` 这些列**不落盘**，§14 第 4 条的"`p_inst` 与 `p` 的对比曲线"没有输入。
`metrics.hysteresis` 的描述性汇总仍然有。建议 §19.7 改 `--outputs all`，或在 §14 第 4 条注明它需要另一次 `--outputs all` 运行。

### N2 —— §19.7 把 `--session-turns-config g_session.json` 用在 G-dev target 上
`session_budget`（`trm3_g.py:2630`）按 `episode.pair_group_id` 查表，G-dev 的 scenario id 不在 g_session 配置里，
于是每个会话都回落到全局 `--session-turns`（默认 **4**，`alpha_ep = 0.025`），`budget_source = "global"`、
`configured_turn_sessions = 0`。§7.5 的"按该会话配置的轮数 Bonferroni"与 §9.2 的门 F7 都需要**一次以 G-session 为 target 的运行**，
§19.7 没有给出那条命令。建议在 §19.7 补一条 G-session 命令，或注明该 flag 在 G-dev 运行里是空载。

### N3 —— §16.1 #33 的括号内容超出测试实际断言的范围
`PV31::Item33SessionTurnsTest::test_the_real_g_session_config_parses_when_present` 只断言解析结果非空且
`set(turns.values()) <= {3,4,5}`，**没有**断言 100 个会话或 34/33/33 的分布。
我另行核对过真实配置确实是 100 / 3:34 / 4:33 / 5:33，所以正文的**事实是对的**，只是"含真实 config 解析：100 个会话，
`T_max` 3:34 / 4:33 / 5:33"这句把未被测试的数值写成了测试内容。

### N4 —— §16.2 的 D3 / D4 机械定义比代码宽
`g_dev_data_gates.compute_gates`（296–331 行）把 D3 与 D4 都放在**攻击臂 ∧ 已排除过度拒绝无内容 ∧ 有 E** 的分支里：
D3 实际是"攻击臂 ∧ 交集口径"，D4 实际是"攻击臂 ∧ E 存在 ∧ `x` 非空 ∧ `domain_group == code`"。
§16.2 表里 D3 只写了交集式、D4 只写了"攻击臂 ∧ code ∧ 有文本 X"。数值上大概率一致（`e_analysis ≠ null` 蕴含有 E），
但写死在正文里的"机械定义"应当和代码逐字一致。

### N5 —— §15.3 第 1 条把审计信号写成了通过条件
"`schema_1_1.validator_field_disagreements` 为空（否则该包是 1.0.0 校验器产的）"。
按 `g_dev_data_gates.schema_1_1_block` 与 §16.2 的设计，**D3 永远用脚本自己算的交集**，
行字段不一致只触发 WARNING 并逐条点名，**不影响任何门**。把"为空"当成冻结清单的通过条件，
会让一个 1.0.0 校验器产出的合法标注包卡住冻结，尽管 D3 的数值是对的。建议改成"记录并逐条点名"。

### N6 —— OR 臂下滞回的 entry 语义与 §2.7 的字面不同（描述性，无影响）
`episode_hysteresis` 的默认 `enter=config.alpha`（OR 臂下是 0.12），runner 显式传 `enter=args.alpha`（0.10）。
于是在 `--or-arm` 运行里，报警条件是 `p_fused ≤ 0.12`，而滞回的"首段进入"是 `p_fused ≤ 0.10`，
两者不再是 §2.7 说的"与首次报警同一条件"。滞回全程描述性、不进任何率，
但 §16.4 只记了"共用 H"和"用主通道标准化器"两条边界，可以补第三条。

### N7 —— §19.5 的 HEAD 注记落后一个提交（不影响校验）
表头写 "冻结候选工作树，HEAD `666fbca…`"，而冻结候选正文本身在 `d70e713`。
我在 `d70e713` 上重算了 §19 的全部 36 个哈希，**逐个相同**（`666fbca → d70e713` 只改了文档），
所以只是 provenance 行陈旧。另：当前工作树除 `artifacts` 外还有并发 agent 的
`.build_batch22_opusA.py` / `.chan_batch22_opusA.json` 两个未跟踪文件，
`working_tree_status()` 只白名单 `artifacts`，冻结提交 A 前需要清掉（§15.3 第 14 条）。

---

## 4. 一句话结论

**代码整体确实实现了正文所述的机制**——序贯保形核心未改、12 格 H 与全部冻结常量逐位对上、
匹配实测 FAR 真的驱动 bootstrap 与 McNemar、E 分母的理由码齐全、`p_inst` 严格描述性、
推理期只读路由。但有**两处必须在冻结前修**：守卫校验的是草案而不是本文件（B1，会让 §19.7 的命令被自己拒绝），
以及 G-dev 的两个困难正常臂在装载时坍缩成 `clean`（B2，使 §4 的分母纪律被违反、F2 第二条不可评）。
另有四处 SHOULD-FIX（F8 的一半断言未实现、§4 的 episode 计数自相矛盾、§15.3 的测试计数过期、表外格的 H 空转）。
