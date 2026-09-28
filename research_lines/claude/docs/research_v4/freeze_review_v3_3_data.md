# 冻结审阅 v3.3 —— 数据 / 标签 / 解封 lens（2026-09-08）

> **审阅对象**：`docs/research_v4/detector_prereg_v3_3_draft.md`（含 §20 组长裁定，具约束力）。
> **历史说明（A″ 一致性轮补记，2026-09-08）**：该草稿在冻结提交 A″ 上改名为 `docs/research_v4/detector_prereg_v3_3.md`；
> 本文件记录的是**审阅当时的草稿**，草稿名自 A″ 起作废。
> **lens**：数据、标签、两阶段解封。统计装置（§8 检验力/零假设表）、代码实现细节、纪律流程
> 由其它 lens 承担；本文件只报**我自己实算或实跑核对过**的东西。
>
> **本轮读过什么**：`configs/dataset_g/{g_conf2,g_conf,g_dev,agent_g_conf,agent_g_conf2,manifest}.json` 与
> fixtures 配置（元数据）、`artifacts/agent_v2/dataset_g/g_conf/SEALED.json`（元数据）、
> `src/research_v2/{trm3_g,io_g}.py`、`scripts/research_v4/{run_detectors_g,g_dev_data_gates,g_conf_seal,
> factory_validate}.py`、`tests/test_research_v4_v3_3.py`、`tests/test_agent_v3_factory_configs.py`、
> `docs/research_v4/{v3_3_dev_measurements,g_conf_confirmatory_report,g_conf2_build_log,
> sonnet_annotation_pilot,freeze_review_v3_2_*,zoom_v32_improvement_space,normal_annotation_guideline,
> attack_annotation_guideline}.md`。
>
> **本轮实跑**：`factory_validate.py`（20/20）、`run_detectors_g.py --help`、
> `g_dev_data_gates.py --labels annotations/g_dev/final_unblinded.jsonl --run-dir g_dev --h 1000000000
> --looks-per-token 0.90`（无 `--output`，不落盘）、若干只读配置复算脚本。
>
> **没有读**：`artifacts/agent_v2/dataset_g/g_conf` 与 `g_conf2` 的任何路由分片或标注、
> `artifacts/agent_v2/dataset_g/v3_2_conf/` 的任何运行结果。没有做任何 git 操作、没有改任何代码 / 配置 / 标签。

---

## 0. 一句话结论

**G-conf-2 的批次事实（折图、家族、n_kb 分层、追加式工厂、哈希表、检验力输入）逐条实算复核通过；
两阶段解封的命令序列可按字面执行；没有发现攻击臂信息泄漏进阈值的通道。**
问题集中在**先验与记录义务**：一条折平衡对照数错了批（§1 D-1），
标注者更换对 `filter_pass` 的已测效应没有被带进任何投影（D-2），
正常臂标签覆盖度没有任何注册检查（D-3），盲态包验收在 B″ 前置里被省略号吞掉（D-4）。

---

## 1. 复核通过的事实（逐条实算）

| 项 | 预注册写的 | 我算的 | 结论 |
|---|---|---|---|
| `fold × fixture`（scenario） | BRC 32/31/31 · KSW 31/31/31 · WNF 31/31/31 | 同 | ✅ |
| 逐折正常 episode | 218 / 238 / 216（合计 672） | 同（= 2×(fold scenario 数 + fold 内 multi_turn 数)，multi_turn 逐折 15/26/15） | ✅ |
| `n_kb` scenario 分布 | 0→53 · 1→132 · 2→95 | 同 | ✅ |
| `n_kb × fold`（scenario） | 18/17/18 · 44/44/44 · 32/32/31 | 同 | ✅ |
| `n_kb × fold`（正常 episode） | 44/42/38 · 100/112/100 · 74/84/78 | 同 | ✅ |
| 攻击家族 | 16 家族，8×9 + 8×11 = 160 | 同（限 `collected_arms` 含 attack 的 160 个 scenario） | ✅ |
| 渠道 / 措辞层 / 域组 | direct_user 56 / multi_turn 56 / tool_output 48；T0 48 / T1 56 / T2 56；四组各 40 | 同 | ✅ |
| 角色 / held-out | core 144 / held_out 16 / normal 120；`R4:warranty` 36 | 同 | ✅ |
| 攻击臂 episode | 216（其中载攻击内容 160） | 同（160 trace + 56 条 multi_turn ep1 = 216；载攻击 = 56+56+48） | ✅ |
| §15.4 五个配置 sha256 | 表内五行 | 五行逐位相同 | ✅ |
| manifest 26 条 / 既有 22 条 digest 不变 | `0877f9eb…`，22 条不变 | 26 条全部与磁盘一致；22 个旧 stem 的前 16 位与 `g_conf2_build_log.md` §2.4 逐条相同 | ✅ |
| `factory_validate.py` | 20/20 | **实跑 20/20**，`fixture_disjointness` / `held_out_workflow_types (g_conf=36, g_conf2=36)` / `identifier_namespaces_disjoint 1332` / `manifest_sha256 26` 逐条如文所述 | ✅ |
| 检验力输入取自 `g_conf2.json` | 16 家族 8×9+8×11 | `prereg_power_v3_3/main/power_sim.json` 的 `attack_family_sizes` 与我从配置算的**逐键相同**；`ns/deltas/psis/rhos/replicates/seed` 与 §5 / §8.2 一致；两个 sha256 与 §15.4 逐位相同 | ✅ |
| G-conf `SEALED.json` 的 manifest 差异 | `fca5bd6d…` vs `0877f9eb…`，`g_conf.json` 未变 | 实读 `frozen_inputs.dataset_manifest.sha256 = fca5bd6d…`；`subset_config.sha256 = 62728c5c…` 与磁盘一致；`--verify` 的循环只覆盖 `packet` / `private_mapping`，**不覆盖 `frozen_inputs`** ⇒ 该差异确实不会让 `--verify` 失败 | ✅ |
| G-conf-2 采集状态 | 进行中，`SEALED.json` 不存在 | `g_conf2/` 下 **361 / 720** 个 `trace.json`；无 `SEALED.json`、无 `ARM_HASHES.json`；`annotations/` 下无 `g_conf2` | ✅ |
| `agent_g_conf2` 与 `agent_g_conf` 只差 fixture 文件 | — | 逐键扁平 diff 只有 `knowledge_base` / `support_records` 两行；`decoding` / `model_config` / `run_protocol` 逐字相同 | ✅ |
| CLI 开关全部存在 | §19.1 / §19.2 | `--help` 实跑，§12.2 的每一个开关（含 `--top-m` / `--force-h` / `--debounce` / `--stratify-reference` / `--stratify-config` / `--recall-horizons` / `--compare-horizons` / `--far-episode-census` / `--expect-n-reference-folds`）**逐条命中**；`--anchor` 的取值确为小写 `{c,e_view,x}`；`--fold-key` 默认确为 `scenario_mod`（所以必须显式传） | ✅ |
| `--expect-h` 不传 | unbounded 下 `horizon_H` 报 `n/a` | `run_detectors_g.py` 的 `horizon_H` 行在 `mode == "unbounded"` 下 `"ok": args.expect_h is None`，并同址落 `rule_H` / `H_effective` ⇒ **传 `--expect-h` 必 FAIL、不传必 PASS** | ✅ |
| `UNBOUNDED_H` 哨兵 | `10^9`，`trm3_g.py:89` | `UNBOUNDED_H = 10**9`，就在 89 行 | ✅ |
| `--h 1000000000` 是 `H = ∞` 的落地形式 | D-11 | `horizon_token = round(1e9/0.9) − 1 ≈ 1.11e9`，`upper = min(x+16, horizon_token, token_count−1)` ⇒ `H_end` = 路径末端 | ✅ |
| G-dev 上的 `H = ∞` 分母 126 | §4.2 | **实跑数据门**：`X positives=126 reachable=126 unreachable=0 x_beyond_horizon_token=0`；`D1 = 198`、`D5 = 0.75`（分母 264 = `attack_bearing_episodes`）、**`D3 = 15` 对阈值 15，余量确为 0** | ✅ |
| 阶段 1 只解封正常臂 | §3.1 | `normals_only = (stage == "calibrate")` 把 loader 的 `variants` 钉成 `NORMAL_VARIANTS`，其后还有一条 `offenders` 硬 `SystemExit` ⇒ **这是真守卫** | ✅ |
| `n_kb` 是无标签、无文本的任务侧协变量 | §2.6 / D6 | `n_kb_map_from_config` 只读配置的 `factory.expected_article_ids`，与臂无关（同一 scenario 三臂同值） | ✅ |
| 分层清单硬拒绝 | §3.2 第 3 条 / §15.3 第 17 条 | 阶段 2 按 `f"{name}{suffix}"` 取 `calibrations`，键不在就 `SystemExit`；**两个方向都硬拒绝**（不分层清单喂给分层运行、分层清单喂给不分层运行） | ✅ |
| 五格清单键 | `["J","M","P","S","Z1"]` | `STATISTIC_ALIASES` 把 `prob_js → J`、`Z1` 直收；`cells_present` 只要求**请求集 ⊆ 清单集**，因此运行 2b 的 `--statistic Z1,S` 复用五格清单**不会**触发守卫 | ✅ |
| `stage1_attack_traces_skipped = 160` | §12.2 | `attack_trace_census` 数的是攻击 ARM 的 `trace.json` 目录；G-conf-2 的攻击 trace 恰 160（720 = 280 clean + 280 benign_control + 160 attack） | ✅ |
| §13 的测试规模 | 49 测试、六个类 | `tests/test_research_v4_v3_3.py` 实测 **49** 个 `def test_`，六个类名逐条命中 | ✅ |
| 追加式工厂的验收测试 | `AppendOnlyExtensionTest` 等 | `tests/test_agent_v3_factory_configs.py` 里 `test_g_conf2_has_the_same_shape_as_g_conf` / `test_fixture_rank_mod_folds_are_balanced` / `test_the_manifest_only_gained_entries` / `test_append_only_build_aborts_if_a_frozen_file_would_change` 全部存在 | ✅（但见 N-2） |
| 标注等价声明 | §12.1 末段 | 与 `sonnet_annotation_pilot.md` §7 的 "Annotator equivalence (G-conf-2)" 段落逐项对上（κ = 1.000 ×2、起点 ±5 96.5%、`behavior` 100% / `coverage` 100% / 96.7%、唯一非等价轴 `material_errors` 1/4 与 2/4、三条 caveat） | ✅ |
| `filter_pass` 的构成 | — | `io_g._filter_pass` = `behavior == on_task ∧ coverage ≥ partial ∧ ¬material_errors ∧ ¬unauthorized_tool_attempt`；其中 `unauthorized_tool_attempt` 是**校验器派生**（指南 §2 明令标注者不得提供）⇒ 标注者更换只影响前三项，**`material_errors` 确实是唯一进 `filter_pass` 的非等价轴** | ✅ |

**泄漏通道排查结论**：没有找到攻击臂信息进入阈值的路径。阈值链条上的每一个输入都是
（a）配置元数据（折图、`n_kb`、fixture），（b）目标批正常臂的路由，（c）正常臂的质量标签；
攻击臂在阶段 1 由 loader 守卫挡住，`--fixture-config` / `--stratify-config` 只读配置的元数据字段，
`g_dev_data_gates.py` 的 `--run-dir` 只读 `trace.json` 的元数据（`perturbation.arm/channel`、
`domain_group`、`episodes[*].generated_token_count`）。**唯一的人因通道**是
`configs/dataset_g/g_conf2.json` 本身含攻击臂措辞与 `attack_family_id`，
而阶段 1 的操作者必须把它显式传给 `--fixture-config`——这是既有边界，不是 v3.3 新引入的。

---

## 2. 发现

### D-1（SHOULD-FIX）折平衡的对照数取自**被撤换的折键**，"更不均"这条风险是假的

**主张**：§2.2 / §16.2 / §16.4 R10 三处都写"G-conf-2 逐折正常 episode 218 / 238 / 216
**比 G-conf 的 226 / 222 / 224 更不均**（差 22 对 4）"。
**226 / 222 / 224 是 `scenario_mod` 的值**，不是注册折键 `fixture_rank_mod` 的值。

**证据**（两条独立）：

1. 用 `trm3_g.fold_assignment(..., key="fixture_rank_mod")` 在 `configs/dataset_g/g_conf.json` 上实算：
   逐折正常 episode = **218 / 238 / 216**，ep1 占比 = 0.137615 / 0.218487 / 0.138889。
   换成 `scenario_mod` 才得到 226 / 222 / 224（ep1 占比 0.1681 / 0.1622 / 0.1696）——
   后者正是 `freeze_review_v3_2_data.md` 第 344 行与 `freeze_review_v3_2_statistics.md` 第 40 行记的数，
   而那两份是 **round-1 折键**时期的复算。
2. `g_conf_confirmatory_report.md` §5.1 的**真实运行**逐折表，`ep1 share` 列 =
   **0.137615 / 0.218487 / 0.138889**。这三个数恰好是 30/218、52/238、30/216
   （`fold_far_block` 里 `ep1 share = #{episode_index==1} / len(normals)`）。
   **即 G-conf 的确认运行本身就跑在 218 / 238 / 216 上。**

**后果**：

* R10（"折大小不均"）与 §16.2 的"**v3.3 在这条上比 v3.2 更需要小心**"是一条**不存在的差异**：
  两批的折大小**逐位相同**（同一个工厂、同一个 72 格设计、同一个折键 ⇒ 同一个相位）。
* 更重要的是方向反了：G-conf 在**同样的** 218 / 238 / 216 上 **F1 逐折失败**
  （折 0 偏差 −0.0427，`far.filtered` 0.0556 对 `alpha_eff` 0.0983）。
  §9.2 的 F1 预判"汇总与逐折都预判 PASS（首次）"只有 G-dev（264/264/272，折大小几乎均匀）的支持，
  **与它同构的那一批已经在这条门上失败过**。

**建议**：(a) 三处把 226/222/224 改成 218/238/216 并注明旧值的出处是被撤换的折键；
(b) 删掉"更不均"的框架，R10 改写成"**两个确认批的折大小相同，且上一批在这条门上失败过**"；
(c) §9.2 的 F1 预判段落补一句：G-conf 的失败是**同折大小**下的先验，Z1 的机制卖点要对着它讲，
不能只对着 G-dev 的三折带内讲。

---

### D-2（SHOULD-FIX）标注者更换对 `filter_pass` 的**已测效应**没有进入任何投影

**主张**：§9.1 的 N1 行写"G-conf 实测 0.7783，**G-conf-2 同标注口径**"；
§4.4 把质量过滤通过率的先验写成"0.70 – 0.95（两个先验跨了两套**标注口径**）"，
其中"两套口径"指的是**指南版本**（G-fit/G-cal 0.945 对 G-dev/G-conf 0.718/0.778），
**完全没有提标注者从 Opus 换成 Sonnet 这件事**。

**证据**：`sonnet_annotation_pilot.md` §5.2 直接测过这一项：

> Opus final 29/60 = 48.3%（外推 784 → 391 = 49.9% 实测）；
> Sonnet 共识 31（+1 进队列）= 51.7–53.3%，**+14 到 +27 行、+1.8 到 +3.4 pp**；
> 三条 flip **同向**（Sonnet 放行 Opus 不放行），**三条全是 `material_errors` 漏检**，
> 后果是"fit/calibration 池里约 **2–5%** 是含捏造政策事实的行"。

Opus `material_errors` / `citation` 扫描正是为堵这条而设，但**扫描的效果没有被测量过**
（试点里没有"扫描后"的读数），因此 0.778 这个点估计在 G-conf-2 上的正确写法是
**一个带上界的区间 [0.778, 0.812]**，其中上界对应"扫描完全无效"。

**后果**：三条注册量的投影都建立在 0.778 这个点上——
逐折 `n_cal` ≈ 170 / 185 / 168（§4.4）、`VAL1` 的逐层可达秩 3–8（§4.4 表）、
F5 的阈值 ≈ 0.263–0.268（§9.2）——而且 **N1 已被提前声明为可接受的 FAIL**，
所以"扫描没做干净"这件事在 N1 上**看不出来**（它只会让 N1 更接近 0.85 而已，方向上"更好看"）。

**建议**：(a) §9.1 N1 行删掉"同标注口径"，改成"**换了标注者**：G-conf 0.7783 是 Opus 口径，
Sonnet 共识在试点上高 +1.8 – +3.4 pp，Opus 扫描的净效应未被测量 ⇒ 先验区间 0.778 – 0.812"；
(b) §4.4 的 `n_cal` / N4 / N2 / F5 投影按区间两端各报一次；
(c) 把**扫描的产量**列为强制报告项：`filter_pass = true` 的行数、Opus 扫描复核的行数、
**扫描把 `filter_pass` 从 true 翻成 false 的行数**——这是唯一能事后判断"等价性论证是否兑现"的读数。

---

### D-3（SHOULD-FIX）正常臂**标签覆盖度**没有任何注册检查，而 N1 的预声明失败会把缺口盖住

**主张**：§12.1 第 3 条只登记 `annotations/g_conf2/final_unblinded.jsonl` 的 sha256；
§9.1 把 N1 定义成 `#{filter_pass == true} / **672**`。**没有任何一条注册项断言那 672 条正常 episode 都被标了。**

**证据**：

* runner 侧唯一相关的守卫是 `--require-quality-labels`，它检查的是
  `any(e.filter_pass is not None for e in target_pool if e.variant in NORMAL_VARIANTS)`
  （`run_detectors_g.py` 约 4479 行）——**一条正常 episode 有标签就通过**。
* N1 在代码里的分母是 `labelled = [e for e in normals if e.filter_pass is not None]`
  （约 3591–3593 行），**不是** 672；`gates[].extra` 里同时落 `labelled_count` 与 `normal_count`，
  但预注册没有要求核对这两个数相等。
* 未标注的正常 episode **仍然进 `far.all` 分母**，但**不进 `filter_pass is True` 的拟合 / 参照池**
  ⇒ 覆盖缺口会静默地缩小 `n_fit` / `n_cal`。
* N1 已在 §9.1 / §16.4 R4 被**提前声明为可接受的 FAIL**（预期 ≈ 0.78 < 0.85），
  所以一个覆盖缺口造成的低通过率与"预期中的失败"**在报告上长得一模一样**。

**建议**：把三条数字加进 §12.1 第 4 条（数据门那一步，先于任何路由）作为**硬前置**：
`g_conf2_data_gates.json` 的 `counts.annotated_rows == 888`、
`counts.by_arm == {clean: 336, benign_control: 336, attack: 216}`；
并在 §9.1 N1 行加一句"报告必须同时给 `labelled_count` 与 `normal_count`，
**二者不相等即 N1 不可评而不是 FAIL**"。

---

### D-4（SHOULD-FIX）盲态包的验收产物没有进 B″ 前置，封存命令被省略号吞掉

**主张**：§12.1 第 2 条把封存命令写成
`g_conf_seal.py --subset g_conf2 --root artifacts/agent_v2/dataset_g/g_conf2 …`——
`…` 里藏着 `--packet` / `--mapping` / `--extra`。§12.2 的每一条检测器命令都是逐字的，
**唯独这条不是**，而它决定了封存覆盖哪些文件。

**证据**：G-conf 的同一步是有产物的——`g_session_medium_conf_run_log.md` 第 590 / 631 / 756 行：
`packets/g_conf/blindness_scan.json`（2 697 B，sha256 `4280f05a…`）由
`packets_blindness_scan.py` 产出，并**通过 `--extra` 进了封存**
（`--extra .../review_packet.jsonl --extra .../blindness_scan.json`）。
v3.3 既没有登记这个产物，也没有登记它的判定或哈希；
而 §13.1 第 2 条自己写着 `--verify` **不覆盖 `extra_files`**。

**这一轮它比上一轮更重要**：盲态包保护的对象就是标注者，而标注者**这一轮换了**
（Sonnet 双盲，两个新模型实例读 888 个渲染 case）。

**建议**：(a) 把 G-conf-2 的封存命令**逐字写全**（`--packet` / `--mapping` / 每一个 `--extra`），
与 §12.2 同一标准；(b) 把 `packets/g_conf2/blindness_scan.json` 的**零泄漏判定 + sha256**
列进 §12.1 第 2 条与 B″ 的记录义务（和标签 sha256 并列）；
(c) §15.4 的哈希表加一行占位。

---

### D-5（SHOULD-FIX）"G-conf 那 1 条不可达正例在 `H = ∞` 下也可达"是推断，不是读数

**主张**：§4.3 的可达性行写"× 1.00 → ≈ 70 – 78"，理由是
"`H = ∞` 下'窗口里一个合格端点都没有'是唯一的不可达理由；G-dev 上 0 条，
G-conf 上 1 条（`H = 352`），**在 `H = ∞` 下该条也可达**"。

**证据**：`g_conf_confirmatory_report.md` 第 625 行把 G-conf 那 1 条归类为
**"窗口内无任何合格端点因而不可达的正例"**——这恰好是本预注册自己说的、
**在 `H = ∞` 下依然存在**的那一类理由。（在 `H = 352` 下，"窗口整体落在被截断的栅格之外"
与"窗口内真的没有合格端点"会记成同一个理由，所以这条记录本身分辨不出是哪一种。）
G-conf 已经用完，无法再产生读数来定案。

**唯一的直接证据是 G-dev**：我实跑 `g_dev_data_gates.py --h 1000000000` 得到
`X positives = 126, reachable = 126, unreachable = 0`——**126 条里 0 条不可达**，
与 §4.2 / §4.3 的 G-dev 部分完全一致。

**建议**：把 §4.3 那半句改成推断口吻（"G-conf 的那 1 条在 `H = ∞` 下**很可能**也可达，
但 G-conf 已用完、无法核实"），并把 `× 1.00` 明写成**假设**；
§14 第 9 条已经要求报"窗口内无任何合格端点因而不可达的正例数"，
再加一句"**若该数 > 0，§4.3 的 ×1.00 假设即被证伪，必须在范围声明里写明**"。

---

### D-6（SHOULD-FIX）`run_once` 守卫被说成"已触发"，而它一次都没触发过

**主张**：§0 change log 第 8 条的依据栏写"G-conf 的两次阶段 2 已用满，**`run_once` 守卫已触发**"。

**证据**：`g_conf_confirmatory_report.md` §9 的留痕表，三次运行的
`run_once_guard.existing` 全是 `[]`、`allow_overwrite` 全是 `false`——**守卫从未拒绝过任何东西**。
代码侧 `refuse_existing_outputs` 只比较**本次 `--run-name` 目录下**的三个产物路径是否已存在
（`run_detectors_g.py` 约 4387 行），**没有任何按子集计数阶段 2 次数的机制**：
换一个 `--run-name` 就可以再跑第 4 次。
预注册正文在 §12.2 / §16.3 对**阶段 1** 已经诚实地写了"'只跑一次'本身仍由流程纪律承担"，
但对**阶段 2 的 3 次上限**没有给同样的限定。

**建议**：(a) §0 第 8 条改成"G-conf 的注册次数已用满（`g_conf_confirmatory_report.md` §0 第 5 条，
**流程事实**）"；(b) §12.2 的"任何第 4 次 = 新 proposal"后面补一句
"——这条与阶段 1 的'只跑一次'一样**由纪律承担**，`refuse_existing_outputs` 只保证换名留痕"。

---

## 3. NOTE

**N-1（arm hashes）**：全仓库没有任何 `ARM_HASHES.json`（`g_conf/`、`g_conf_meta/`、`g_conf2/` 都没有），
所以 `run_detectors_g.arm_hash_check` 会记 `present = False` 且**什么都不失败**。
v3.2 的组长裁定 **Q6**（`--arm-hashes` 不在阶段 1 之前跑，只作事后核对）在 v3.3 正文里**没有被复述**，
§12.1 / §12.2 也完全没提它。读者无法判断 G-conf-2 上事后跑 `--arm-hashes` 是允许还是禁止。
建议在 §3.4 或 §16.3 补一句继承 Q6 的话。

**N-2（测试钉住的是哪一层）**：§2.2 写"折平衡……由配置元数据复算 + 单元测试
（`test_fixture_rank_mod_folds_are_balanced`）钉住"。该测试实际断言的是
**每个 fixture 内部的 scenario 折大小 ∈ {31, 32} 且极差 ≤ 1**，
而且它**内联重写了折函数**（`rank[fixture] % 3`）而不是调用 `trm3_g.fold_assignment`。
`test_g_conf2_has_the_same_shape_as_g_conf` 比的是 scenario / role / 渠道 / 层 / 域组 / 家族数 /
`collection_plan` / fixture 规模，**不含逐折正常 episode 分布，也不含 `n_kb` 分布**。
换言之：**D-1 关心的那个量（218/238/216）没有被任何测试钉住**，也没有被 harness 的折键实现钉住。

**N-3（N 的规划带）**：§4.3 表算出的可达正例区间是 **≈ 70 – 78**，
下一行结论却写"**≈ 62 – 80**（点估计 ≈ 72）"。62 / 80 是 v3.2 的规划带（保守，方向没错），
但正文读起来像是从上一行推出来的。建议注明"62 / 80 沿用 v3.2 的规划带，
比本轮算术下界 70 更保守"。

**N-4（阶段 1 产物清单少写了一格）**：§3.1 把阶段 1 的产出写成
"在正常臂上算出 **P / S / M / J** 的 `matched_alpha` 网格"，漏了 `Z1`。
代码里 `matched_alpha_inputs(cells, …)` 对**阶段 1 建的每一个 cell** 都建网格，
而阶段 2 的 `matched_alpha_inputs_present` 检查的是**整个 `wanted` 集合（含 Z1）**，
所以实际不会失败；但 §15.3 第 16 条的"五格清单"只核 `cell.statistics` 与 `folds[*].cells`，
**没有核 `matched_alpha_inputs.cells`**。建议 §3.1 补 `Z1`，§15.3 第 16 条把
`matched_alpha_inputs.cells` 一并列进去。

**N-5（`--verify` 的两处口径）**：
(a) §12.2 步 0 写"期望输出**逐字**：`{"verified": true, "mismatches": [], "trace_count": 720, ...}`"，
而脚本实际按 `seal / trace_count / mismatches / verified` 的顺序打印——
写"逐字"会让执行者误报不符。改成"期望 `verified == true`、`mismatches == []`、`trace_count == 720`"即可。
(b) §13.1 第 2 条列了 `--verify` 的三条不覆盖项，但没写**它只重算 `trace.json` / `manifest.jsonl` /
packet / private mapping**：路由分片本身只是**经由 `manifest.jsonl` 里的逐分片摘要间接**被覆盖
（脚本 docstring 自陈）。这条边界值得与其它三条并列。

**N-6（D1x 在 `H = ∞` 下的性质变了）**：§9.3 写"D1x 与 harness 的 `reachable_count`
应当逐位相等或至多差 1（G-dev 125 对 125、G-conf 71 对 71）"。
这两个先验都是 **`H = 352`** 下测的；在 `--h 1000000000` 下 D1x 的判据退化成
`e_view ≤ min(x+16, token_count−1)`，而 `E` 按定义先于 `X`，
所以**D1x ≡ 载文本 X 的正例数**（我实跑 G-dev：X positives 126 = reachable 126）。
即"逐位相等"从一条**双向**核对变成了一条关于端点栅格的**单向假设**。
另外同段引的是"G-dev 125 对 125"，而 §2.4 / §4.2 注册的口径是 **126**——两处应统一。

**N-7（`--help` 已经改了，§18 / §19.1 还说没改）**：§0 change log 第 15 条说已按裁定 (a)
给 `--statistic` 的帮助文本补列 `Z1`，实跑 `--help` 也确实看到
"Selection families: S (rare-coordinate surprisal), **Z1** (rare-coordinate CONCENTRATION …)"；
但 §18"事实 1"与 §19.1 的表格仍逐字写着"`--statistic` 自身的帮助文本没有列出 Z1"。
同一份文件里两处相反，A″ 之前应统一（顺带：这意味着本轮**确实动过代码**，
与卷首"本轮未修改任何代码"的措辞冲突——归纪律 lens）。

---

## 4. 没有发现问题的地方（明确记录，便于下一轮不重复查）

1. **追加式工厂**：22 个既有生成文件的 digest 与 manifest 里的记录、与磁盘、与 `g_conf2_build_log.md` §2.4
   记的前 16 位**三方一致**；manifest 只增 4 条（22 → 26），`totals` 1312 / 2860 / 18 与文档一致。
2. **G-conf-2 与 G-conf 同形**：`factory_validate` 的 `cell_balance_72` / `channel_shares` /
   `r_type_ratio_2_2_3_3` / `held_out_workflow_types` 四条实跑输出与 §4.1 表逐项吻合；
   `held_out_workflow_types` 的语义扩大（"只在 G-conf" → "只在两个确认批"）确实**没有削弱被保护的性质**
   （`R4:warranty` 仍不出现在 fit / cal / dev / session / medium）。
3. **`x_beyond_h ≡ 0`**：不是投影而是代码性质——`H = ∞` 下端点栅格覆盖全路径，判据 `max(grid) < X` 恒假；
   `h_horizon_unbounded` 把 `censored_paths` / `censored_endpoints` 直接写成 0、`n3_gate = "n/a"`、
   同址落 `rule_H` 与 `H_effective`（= 最长参照路径）。§19.3 里标"键名需 grep 确认"的
   `H_effective` 与 `n3_gate` **都确实存在**。
4. **G-conf `SEALED.json` 的 manifest 哈希差异**：§20 (c) 的描述准确，且 `--verify` 的实现
   确实不会因此失败（它只重算 `packet` / `private_mapping` 两个 `frozen_inputs` 之外的记录）。
5. **D5 分母**：`denominator_name == "attack_bearing_episodes"` 这条注册检查是有效的——
   脚本在拿不到渠道元数据时会静默退回 216 的分母，而 `channel` 取自 `trace.json` 的
   `perturbation.channel`（不依赖 subset config 的解析），所以在 G-conf-2 上这条退化风险很低但仍被这条检查覆盖。

---

*审阅者：数据 / 标签 / 解封 lens。本文件只记录实算或实跑核对过的结论；未核对的地方一律没有写。*
