# 冻结审阅（代码线 + 纪律线）逐条处置 —— rev4，冻结提交 A′ 之前

作者：Opus 5 研究工程师（受组长 Claude Fable 委派）。日期：2026-09-07。

**对象**：`docs/research_v4/freeze_review_v3_2_code.md`（代码视角，C1–C5 / S1–S8 / N1–N7）与
`docs/research_v4/freeze_review_v3_2_discipline.md`（纪律视角，D-1 … D-21）共 **41 条** finding，
以及怀疑者（skeptic）对五条 BLOCKING 的复核裁决。

**组长裁定（本轮的执行口径）**：
1. 凡处置为 **`reword_prereg`** 的，**本轮一次性改进正文**，用代码里的**实际名称**；
2. 凡是**文本 / 数值订正**的 SHOULD-FIX 与 NOTE，**本轮一并改**；
3. 凡标为 **`code_change_required`** 的，**本轮一律不改代码**，逐条登记在
   `detector_prereg_v3_2_draft.md` 新增的 **§13.1**，附复审给出的证据，由组长裁定；
4. **不改动任何冻结参数或判定规则**；
5. 结束时重算预注册的 sha256。

**本轮改了什么文件**：只有两个——
`docs/research_v4/detector_prereg_v3_2_draft.md`（新增 §0.3 与 §13.1，另在 15 处改写措辞与字段名）与
`docs/research_v4/prereg_v3_2_code_mapping.md`（4 处订正 + §5 整节结案）。
**没有改动任何代码、配置、标签或 artifacts；没有在 G-conf 上运行任何东西；没有读取 G-conf 路由。**

---

## 0. 三句话

1. **五条 BLOCKING 里四条成立并已在正文改完**（C1 / C2 / C3 / C5，加纪律线的 D-1 … D-6），
   第五条 **C4 被 skeptic 降级为 NOTE**——它描述的 `PREREG_PATH` 切换是 **A′ 操作单里早已注册的步骤**，
   而且**现在不能提前做**（`detector_prereg_v3_2.md` 尚不存在，提前改会让每次非冒烟运行 `SystemExit`）。
2. **全部 41 条里，30 条是纯文本 / 字段名订正，本轮已改完**；
   **11 条必须改代码，本轮一条都没改**，登记在预注册 §13.1。
   其中**只有一条被审阅方判为 BLOCKING**（§13.1 第 1 条：`--normal-only-smoke` 不被封存批拦截）——
   见下面 §3 的评估。
3. **没有任何一条 finding 改变任何数值、任何判定或任何门的结论。**
   改变的全部是"命令能不能敲通""字段名对不对""某条纪律是机械保障还是只靠流程"。

---

## 1. BLOCKING（代码线 C1–C5）

| id | 处置 | 落在哪里 | 复核要点 |
|---|---|---|---|
| **C1** 三个不存在的开关（`--cells` / `--threshold-manifest-out` / `--anchor X`） | **改正文**（reword_prereg） | §12.2 整节重写为四个逐字可执行的命令块；§13 第 3 / 5 / 6 条；§0.3 r1 | 我自跑 `--help` 复核：实际形式是 `--statistic`（逗号分隔，`prob_js` → 键 `J`）、**一个读写共用的** `--threshold-manifest`、`--anchor {c,e_view,x}`。skeptic 的补充也已采纳：§12.2 同时缺 `--labels-sha256`、`--fixture-config`、`--expect-n-reference-folds`，`<B>` 应为 `<B′>` |
| **C2** `g_conf_seal.py --verify <目录>` 崩溃 | **改正文** | §12.2 步 0 / 步 1.5 改为 `.../g_conf/SEALED.json` 并写死期望输出 `{"verified": true, "mismatches": []}` | 我复跑确认 `verify(seal_path)` 第一行就是 `json.loads(seal_path.read_text())`；**代码没有错**，argparse help 与单元测试都说是文件。采纳 skeptic 的加固：明写"崩溃 ≠ 哈希不符"，避免在步 1.5 误判而作废一次不可重来的阶段 1 |
| **C3** §7.1 指向 E 锚点的遗留块 | **改正文** | §7.1 整表重指 `comparison_anchored.*` / `positives_anchored.*`；§9.2 F8；§15.3 第 5 / 6 条；§0.3 r3 / r4 | 我在真实产物上复核：`comparison.rows.matched.bootstrap` = 0.0508 / 0.0051 / 0.0457、配对 197（E 锚点，**且合取条件同样成立** ⇒ 照旧文取数会得到"看起来成立"的错结论）；`comparison_anchored.bootstrap` = 0.824 / 0.560 / 0.264、配对 125。**`comparison.*` 的名字不改**（§7.3 注册它为存档描述性列，改名会让 §15.4 的产物哈希全部失效） |
| **C4** `PREREG_PATH` 仍指 v3.1 | **skeptic 降级为 NOTE；不改代码** | §15.2 第 1 条补执行提示；§13.1 末段；§15.4 前言补"用工作树内容重算"的提示 | 事实全部属实，但这是 **A′ 操作单步 9 的既有步骤**，且**现在改就是错的**：`detector_prereg_v3_2.md` 还不存在，`sha256_file` 对不存在的路径返回 `None` ⇒ 每次非冒烟运行在 `prereg_sha256_matches` 上 `SystemExit`。**重命名 + 改常量 + 改测试必须在同一个提交里。** 另采纳 skeptic 发现的两条真缺陷：操作单步 6 的 `git show HEAD:<path>` 对 A′ 自己要改的文件会填进陈旧哈希；§15.4 前言把哈希循环写成了"步 5"（实为步 6） |
| **C5** §3.2 约十二个字段名不存在或改名 | **改正文** | §3.2 整块换成实测结构；§3.3 第 2 条；§9.2 F8；§14 第 10 条；§15.3 第 3 / 3b / 6 条；§0.3 r5 / r6 / r11 | 我在真实 manifest 上逐键复核，确认 skeptic 的两点收窄：**每一个实质内容都在**，只是改名或换了嵌套层（`n_fit`/`n_cal`/`survivors_at_H`/`censored_paths` 在 `folds[k].cells[<s>]`，`fixture_counts` → 顶层 `fold_fixture_crosstab`，`Omega_rare` 内联，`whitening_ref` → `fit.whitening.M[k]`…）；`matched_alpha` 我按 manifest 的 `grid` 复算得 0.10416666666666667，与落盘值逐位相同 ⇒ **S6 成立**。唯二真消失的是 `stage1_attack_arms_read`（改指 `stage1_attack_traces_skipped` + `attack_trace_census.count`）与 `selection`（合并选取是结构性保证的：每格只有一张 grid） |

## 2. BLOCKING（纪律线 D-1 … D-6）

| id | 处置 | 落在哪里 |
|---|---|---|
| **D-1** 阶段 1 命令的两个不存在开关 | **改正文**（与 C1 合并处置） | §12.2 / §13 第 5 条 / §15.2 第 4 条（该条此前是 FAIL，现重新可判 PASS） |
| **D-2** 封存核验传目录 | **改正文**（与 C2 合并） | §12.2 步 0 / 步 1.5；另在 §3.4 加了"两种核验强度不同"的对照表 |
| **D-3** `stage1_attack_arms_read` 不存在 | **改正文**，取方案 (b) | §3.2 末段：改指 `stage1_attack_traces_skipped`（G-conf 期望 160）与阶段 2 的 `attack_trace_census.count`，两者必须相等；§11.2 / §12.2 / §13 第 5 条 / §14 第 10 条 / §15.3 第 3 条同步 |
| **D-4** `inputs.threshold_manifest_sha256 == M1 整文件哈希`按构造永不成立 | **改正文** | §15.1 的 M1 / M2 行：M1 记**两个**哈希（整文件 `sha256sum` = `8767ce5e…` 与文件内自哈希 = `6e49face…`），"必须等于"指向**自哈希**；§3.3 第 2 条同步。我自算复核了这两个值 |
| **D-5** `--normal-only-smoke` 不被封存批拦截 | **`code_change_required` → §13.1 第 1 条（不改代码）** | 正文在 §3.3 前言 / §16.2b 第 19 条如实声明"目前只靠流程拦着" |
| **D-6** 阶段 2 命令 `--anchor X` 非法且缺必需开关 | **改正文**（与 C1 合并） | §12.2 的两条阶段 2 命令逐字写全 |

## 3. `code_change_required`（11 条，本轮**一条未改**，登记在预注册 §13.1）

| §13.1 # | 来源 | 一句话评估 |
|---:|---|---|
| **1** | D-5 | **唯一一条被判 BLOCKING 的**。`refuse_sealed_pools` 只挡 `--dev-smoke`，`--normal-only-smoke` 既不被它挡、也绕开 `freeze_guard`，可以在 G-conf 上无限次重跑阶段 1 并看到逐折 FAR / 阈值 / 三分位切点 —— 这正是两阶段解封要防的"工作点事后可选"，而 §3.3 的标题是"必须机械保障"。**我的评估：它确实把一条注册为机械的纪律降级成了流程纪律，但它不会污染已产出的任何数字，也不会自己触发；正文已如实声明现状。修它是两行 additive 改动（`sealed_pool_dirs` 已存在），代价是 §15.4.4 的哈希重算 + 补一条测试。建议组长在 A′ 之前修。** |
| **2** | D-7 | 攻击臂 trace 集合哈希两阶段都算了但从不比较。加上第 8 条，两阶段之间**新增**一个攻击 trace 目录目前无人能发现。additive 一项检查即可。 |
| **3** | S5 / D-8 | `cell_matches` 只比 5 个键。**实测危害低于审阅方预期**：阶段 2 从 manifest 回放全部常量，CLI 错值被忽略（三次实跑读数逐位不变），所以这是"承诺没兑现"而不是"结果会错"。正文已改成真实性质。 |
| **4** | N1 / D-9 | manifest 的 `prereg.sha256` / `freeze_commit_resolved` 不被直接比对；覆盖是间接的（自哈希 + `manifest_code_commit` + `freeze_guard`）。additive 两项。 |
| **5** | D-10 | 两个 `created_at` 的顺序没有代码检查，只能人眼核对；而它写在标题为"必须机械保障"的 §3.3 里。additive 一项。 |
| **6** | D-12 | 两次封存核验都没有机读时间戳，§15.3 要核的"两次核验的时刻"只能靠手写日志。两处 additive 字段。 |
| **7** | D-11 | `seal_check` 是自洽检查不是内容检查。**文本侧已彻底处理**（§3.4 的对照表 + §12.2 明写内容检查只由 `--verify` 承担），代码加固可选。 |
| **8** | D-19 | `g_conf_seal.py --verify` 不覆盖 `frozen_inputs` / `run_configs` / `extra_files`，也不枚举新增文件、不查只读位。 |
| **9** | D-16 | manifest / `result.json` 无条件覆盖写，"只跑一次"没有机械留痕。两行 additive。 |
| **10** | D-21 | `g_dev_data_gates.py` 在 G-conf 上仍打印 `G-dev data gates`。纯表面，运行日志自己写清即可。 |
| **11** | D-18 | `SEALED.json` 的 `root` 是绝对路径，跨 checkout 时 `verify` 会静默核验另一棵树。 |

**另有一条不属于本表**：`PREREG_PATH` 切换（C4）——它**早已注册**在 §15.2 第 1 条与操作单步 9，
是 A′ 提交自己的一步，不是新提出的改动。

## 4. SHOULD-FIX 与 NOTE（全部为文本 / 数值订正，本轮已改完）

| id | 处置 | 落在哪里 |
|---|---|---|
| **S1** §15.3 八条检查的字段名不成立 | 改正文 | §15.3 第 3 / 3b / 4 / 5 / 6 / 7 / 8 / 10 条逐条按实测路径重写 |
| **S2** `length_tertile_definition` 出处串与事实相反 | 改正文 + 记边界（**不改代码**） | §15.3 第 8 条改指 `calibration_design.length_tertiles`；新增 §16.2b 第 18 条边界；§0.3 r14。**切点逐位相同 ⇒ F3 / N2 的数值与判定不受影响** |
| **S3** 逐折 `ep1_share` 其实落盘 / 代码映射缺口 4 的替代读法不可执行 | 改正文 + **撤销缺口 4** | §7.3 / §13 第 13 条 / §14 第 3 条改指 `cells.<s>.folds[k].far.episode_index_1_share`；代码映射 §0 / §2 第 13 条 / §3.3 / §4 第 14b 条 / §5 缺口 4；§19.4 加执行更正注 |
| **S4** 逐折命中率不可由 `per_episode[*].fold` 得到 | 改正文（登记连接口径） | §14 第 3 条写死 `per_episode` key → scenario → `calibration_design.fold_assignment` 的连接；§7.3；代码映射 §3.3 |
| **S5** §3.3 第 2 条的"逐位相符即 SystemExit"未落地 | 改正文为真实（更安全的）性质 | §3.3 第 2 条；§13 第 5 条测试 (g) **撤销**并说明原因（附三次实跑证据） |
| **S6 / D-17** 注册的阶段 2 命令与排练的不是同一个 | 改正文，定死 **N = 2** | §12.2 逐字写出运行 2a（`--statistic S,P,M,prob_js --compare-statistic P`，`cells` = {S,P,M,J}）与运行 2b（S vs M）；§13 第 9 条重写为"没有 OR 臂"的断言 |
| **S7** `--expect-n-reference-folds` 从未被注册命令传入 | 改正文 | §12.2 两条阶段 2 命令都加该开关，值抄自 M1 的 `folds[k].cells.S.n_reference_episodes`；§9.2 F8 / §15.3 第 4 条加"`expected` 为 `None` 即断言空转"的核对 |
| **S8** `cluster_bootstrap_rate` 签名写错 | 改代码映射 | 代码映射 §2 第 12 条；预注册 §13 第 12 条 |
| **N1** manifest 的 `prereg.sha256` 从不比对 | 改正文（点明间接链）+ 登记 §13.1 第 4 条 | §3.3 第 2 条 |
| **N2** `seal_check` 只是自洽检查 | 改正文 | §3.4 的强度对照表；§16.3；§15.3 第 3 条 |
| **N3** S1 两条件"等价"差一个次序统计量 | 改正文（软化，并指出方向保守） | §10.2；§13 第 12 条测试；代码映射 §2 第 12 条 |
| **N4** `channel` 列的测试引用错 | 改代码映射 | 代码映射 §2 第 10 条（实际断言在 `V32::RunnerV32Test`，`tests/test_research_v4_v3_2.py:1053`） |
| **N5** 操作单步 6 多哈希三个 packets 文件 | 改正文（交叉引用，**不新增行**） | §15.4.4 前言：那三个文件**已经在 §15.4.3 里有行**，HEAD 上逐位相同；另加一条"A′ 上新出现的 `scripts/research_v4/*.py` 必须补行"的提示——复核期间另一条研究线的提交 `12c83b4` 新增了 `scripts/research_v4/codex_smoke_g.py`，**本轮已补行**（它不在 v3.2 的判定路径上）；§15.4.4 的既有行与 §15.4.5 的配置行在 `ea93668` / `6f39053` / `12c83b4` 三个 HEAD 上逐位相同，已复算确认 |
| **N6** §12.1 仍写"提交 A / B"与 `freeze_a_checklist` | 改正文 | §12.1 第 1 / 2 条改为 A′ / B′ 与 `freeze_a2_checklist.md` |
| **N7** 裸 `§7.4` | 改正文 | §1.1 / §6 B-P 行 / §10.2 S2 行统一写 **v3.1 §7.4** |
| **D-13** G-conf 数据门的命令从未写死 | 改正文 | §9.3 写死完整命令（含 `--output` 与 `--run-dir`，并说明 `--run-dir` 只读 trace 元数据、不读路由）；§12.1 第 3 条 |
| **D-14** 折图的 subset config 来源不可钉 | 改正文 | §12.2 两条命令都显式传 `--fixture-config configs/dataset_g/g_conf.json`；§15.3 新增第 12 条（`fixtures.sources[*].sha256 == 62728c5c…`） |
| **D-15** `--labels-sha256` 未进注册命令 | 改正文 | §12.2 两条命令都加；§3.3 第 2 条 / §15.3 第 2 条说明"不传即恒真" |
| **D-20** 两个 160 量纲不同 | 改正文 | §15.3 第 1b 条与第 3 条各加括注（载攻击 episode 数 vs 攻击 arm 目录数；G-conf 攻击 episode 总数是 216） |

---

## 5. 明确没有做的事

- **没有改任何代码、配置、标签或 artifacts**（`git status` 只多出本文件与两份复审报告）；
- **没有改任何冻结参数**：α = 0.10、H = 352、band = 0、w = 8、24 层、`rare_threshold = 0.02`、
  K = 3、`fixture_rank_mod`、Holm 族成员、S-J 的分母与配对口径、全部门的阈值——一个字都没动；
- **没有改任何判定规则**：H1 的两条件合取、S1 / S2 的两条件、S-J 的单检验，全部原样；
- **没有改任何数值读数**：§0.2 的 t1–t26 与 §8 的全部检验力表原样；
- **没有在 G-conf 上运行任何东西，没有读取 G-conf 的路由**（本轮读到的 G-conf 侧字节只有
  `configs/dataset_g/g_conf.json` 与 `SEALED.json` 的元数据）。

## 6. 交给组长的两个决定

1. **§13.1 第 1 条（`--normal-only-smoke` 不被封存批拦截）要不要在 A′ 之前修？**
   它是 41 条里唯一一条把"注册为机械保障"的纪律实际降级成流程纪律的代码缺口。
   修它是两行 additive（`sealed_pool_dirs` 已存在），代价是补一条测试 + §15.4.4 的两行哈希重算。
   **我的建议：修。** 其余十条建议留到 A′ 之后（它们都是"加检查"，不修也不会让任何结果出错）。
2. **§13.1 第 2 / 4 / 5 条（攻击臂集合哈希比对、`prereg.sha256` 比对、`created_at` 顺序检查）
   要不要打包一起修？** 三条都是在 `verify_manifest` 里各加一项、纯 additive，
   一次改完只需要一轮测试与一次哈希重算。**我的建议：与第 1 条打包，或整包留到 A′ 之后。**
