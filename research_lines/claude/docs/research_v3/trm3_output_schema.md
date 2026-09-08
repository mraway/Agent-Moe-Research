# TRM-3 输出契约（`outputs.jsonl` / `result.json`）

实现：`src/research_v2/trm3.py`，运行器：`scripts/research_v3/run_trm3.py`。
本文件描述**产物格式**，不改变 `docs/research_v3/trm3_prereg.md` 冻结的任何算法参数；prereg §9 列出的
16 个字段全部保留且语义不变，其余为附加的诊断字段（两线统一比较时只需读 §9 的 16 个字段）。

产物目录：`artifacts/agent_v2/research_v3/trm3/<run_name>/`，`run_name` 默认 `<target>_<calibration>`
（routine-only 冒烟加后缀 `_smoke`；`--calibration both` 时为 `<target>_both`）。

本文件已按 prereg §11 的 v1.1 修订更新（融合改 `min`、固定参照集、h384 双锚点、
`spontaneous_drift` 描述性组、B-U 固定规则与退化标记、G8 参照的分母说明与运行内 G5、
`(batch, trace_id)` 键、新增 `no_temporal2`）。

| 文件 | 内容 |
|---|---|
| `result.json` | code_commit、prereg sha256、config、输入 sha256、逐 variant 指标、G1–G8、P1 的 McNemar；`--calibration both` 时逐列放在 `columns.{D,C1}` 下，另有顶层 `g5` |
| `outputs.jsonl` | 逐端点输出（默认只写第一个 variant，`--outputs all` 写全部，`--outputs none` 不写） |
| `tables.md` | FAR / 端点率表、主事件召回表、McNemar 表、门表 |

## 1. `outputs.jsonl` 逐行字段

一行 = 一条 trace 的一个**端点**（endpoint）。端点网格 = 各通道因果窗端点的交集，
即 `end >= max_c w_c - 1`（三通道时 = 7，与冻结 harness 一致：前 w−1 个 token 不产生端点）。

### 1.1 prereg §9 冻结字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `trace_id` | str | trace 标识 |
| `batch` | str | `b1` / `b2` / h384 / c1 批次标识 |
| `key` | str | `<batch>\|<trace_id>`：**逐 trace 主键**（v1.1 修订 7；h384 复用 B2 的 trace_id，单靠 trace_id 会撞键）。`result.json` 内所有逐 trace 字典（`primary_event_hits_plus_8`、`spontaneous_drift.keys`、`summaries[].key`）都用它 |
| `arm` | str | `clean` / `benign_control` / `attack` |
| `class` | str | `clean` / `benign` / `resist` / `drift`（`trm3.trace_arm_class`，与 `io.arm_class` 同规则） |
| `calibration` | str | `<D|C1>\|<评价集>\|half<0/1>`，即校准列、评价集合与实际使用的校准半份 |
| `k` | int | 该 episode 内融合网格上的端点序号（0 起；**不是** token 下标） |
| `end` | int | 该端点对应的 decode token 下标（窗口 = `[end-w+1, end]`） |
| `p_S`, `p_M`, `p_J` | float / null | 三通道的 p 值；该 variant 未启用的通道为 `null`。序贯规则下 `p_c(k) = (1+#{g: Z_c^g ≥ R_c(k)})/(n_cal+1)`，参照集 = 校准半份中每条路径的**全路径最大值**（v1.1 修订 2，固定、与 k 无关，等价于冻结 mode-D 的次序统计量阈值）；`no_temporal` 为单窗口分位 p × K_max=384，`no_temporal2` 为不校正的单窗口分位 p，`unseen_only` 为固定规则（窗口原始分数 > 0 → p=0，否则 p=1） |
| `p_fused` | float | `min(1, α·min_c p_c/α_c)`（v1.1 修订 1 的 Bonferroni 正确形式），α = 0.10，α_S/α_M/α_J = 0.02/0.04/0.04：任一通道 `p_c ≤ α_c` 即 `p_fused ≤ α` |
| `state` | str | `CONFIRMED`（`p_fused ≤ 0.10`）/ `PROVISIONAL`（≤ 0.25）/ `SILENT`；无首次越线锁定 |
| `temporal_state` | str | `NONE` / `UNCERTAIN` / `SUSTAINED` / `RECOVERING`（基线判定规则 `no_temporal` / `no_temporal2` / `unseen_only` 一律为 `DISABLED`） |
| `e0` | int / null | 当前 episode 首次进入 PROVISIONAL 的端点的 `end`；RECOVERING 后再次进入会重置 |
| `attribution` | str | `argmin_c p_c/α_c`，并列时按冻结通道顺序 S→M→J |
| `regime_flag` | bool / null | 当前 w=8 选择率窗口在 routine PCA 第一主成分上的投影是否落入“结构化瓣”（routine q05–q95）。**纯诊断**，不进分数、不进状态；未拟合到结构化 routine 时为 `null` |

单通道 variant（`m_only` 等）α_c = 0.10，`p_fused == p_c`；两通道 variant α_c = 0.05 各半
（prereg 只冻结了三通道的 0.02/0.04/0.04，两通道的均分见 `trm3.py` 模块文档“协议决定 4”）。

### 1.2 附加诊断字段（不属于 §9，可忽略）

| 字段 | 类型 | 含义 |
|---|---|---|
| `duration` | int | 自 `e0` 起（含）已经过的端点数；无 episode 时 0 |
| `censored` | bool | `e0` 已出现但 `[e0, e0+D)` 窗口尚未走满（D = 32），即时间状态判定被删失 |
| `earliest_decision_end` | int / null | `e0 + D`：最早可靠时间状态判定时刻 |
| `remaining_budget` | float | 每 episode 误报预算的剩余部分：首个 CONFIRMED 之前为 α = 0.10，之后为 0（running-max p 为 anytime 形式，一次报警即花掉整份预算） |
| `calibration_version` | str | `trm3-v1:<pool>:<config+校准 trace 列表的 sha256 前 16 位>` |
| `evidence_window` | [int, int] | 证据窗口的首尾 token 下标（最近 8 个端点） |
| `top_coordinates` | list | 归因通道的前 3 个贡献坐标；仅在 `--emit-evidence` 且该通道提供 `top_coordinates(state, trace, end, n)` 钩子时出现，否则缺省（回退为空）。S / B-S / B-U 返回 `{layer, expert, contribution, window_selections}`，J 返回 `{pair, experts, contribution, window_selections}`；S / B-S / J 的全部贡献之和精确等于该窗口分数（B-U 的窗口分数是最大值，因此其计数不求和）。冻结的 CAND-A 通道 M 不提供钩子（`wgm.py` 不得改动），故 M 归因的端点回退为空 |
| `p_U`, `p_P` | float | baseline variant（`unseen_only` / `surprisal_marginal`）的单通道 p |

## 2. `result.json` 结构

```
{
  schema_version, code_commit,
  prereg: {path, sha256},
  target, calibration, routine_only_smoke, variants_requested,
  pools: {routine_fit_count, calibration_pool_count, evaluation_sets, source_batch},
  inputs_sha256: {core, h384_sample_index, c1_sample_index,
                  h384_engagement_adjudications, product_onset_labels, topic_entry_labels},
  channel_fit_seconds: {<scorer|config>: seconds},
  anchor_policy: {primary, secondary},
  spontaneous_drift_group: {keys, trace_ids, rule},
  columns: { <D|C1>: {pools, channel_fit_seconds, g8_reference, variants, gates, mcnemar} },
  g5: { <variant>: <G5 门块> },                       # 仅 --calibration both
  # 单列调用时下列字段同时平铺在顶层（向后兼容）：
  variants: { <variant>: { config, calibration, sets: {<set>: <metrics>}, cost,
                           degenerate?, degenerate_reason? } },
  gates:    { <variant>: { G1..G8, summary } },
  mcnemar:  { <variant>: {only_a, only_b, net_gain_a_over_b, p_value, ...} }
}
```

`sets` 的键：`target`（本次评价目标）与（C1 校准列）`c1_heldout`（folds 3–4 的 held-out 正常 FAR）。

### 2.1 每个 set 的指标块（`trm3.evaluate`）

* `far`：`clean` / `benign` / `pooled` / `benign_minus_clean` / `matched_group`（C1 与 B1/B2 的
  pair group：组内任一成员报警即该组误报）及各自计数；
* `far_by_calibration_half` + `far_half_gap`：两校准半份的 FAR 与差（门 G3）；
* `endpoint`：`eligible_endpoints` / `alarm_endpoints` / `alarm_endpoint_rate` /
  `alarm_onsets` / `alarm_onsets_per_1000_eligible`（门 G8）；
* `recall_strict` / `recall_tolerant`：`recall_plus_{8,16,32,64}`、`recall_final`、
  `pre_onset_rate`、latency（median / mean / p90 / 全表）。**strict**：锚点前的任何报警都记为
  pre-onset 且该 trace 不计为命中；**tolerant**：锚点前 5 个 token 内的报警计为命中（latency 截为 0）；
* `anchor_policy` / `recall_strict_secondary` / `recall_tolerant_secondary`：h384 的双锚点列
  （v1.1 修订 3：主锚点 = `engagement_onset`（证据段首 token，执行与有界抵御同用），
  副锚点 = `execution_onset`（证据段末 token））；无副锚点时为 `null`；
* `primary_event_hits_plus_8`（及 `..._secondary_anchor`）：`{key: bool}`，P1 配对 McNemar 的输入；
* `spontaneous_drift`：v1.1 修订 4 的描述性组（h384 的 4 条 `goal_drift` benign_control）：
  `trace_count` / `any_alarm_count` / `any_alarm_rate` / `keys`；这些 trace **不进** routine 拟合、
  校准池与任何 FAR 分母；
* `silent_alarm_rate` / `silent_count`：静默抵御的报警率（门 G4）；
* `temporal.confusion`：behaviour class（execution / bounded / silent / clean / benign /
  spontaneous_drift）×
  时间状态计数 + `any_alarm_rate` / `abstention_rate`（UNCERTAIN 或 NONE）/ `censored_rate` /
  `earliest_decision_median`；
* `worst_group`：workflow / channel / domain / 长度三分位，每组 FAR 与 +8 召回，并给出
  `worst_far` 与 `worst_recall_plus_8`；
* `attribution`：`endpoints`（非 SILENT 端点的通道分布）与 `first_confirmed`（首次报警的通道分布）；
* `summaries`：逐 trace 摘要（`TraceSummary`：报警、首报警端点、报警 onset 数、e0、时间状态、
  使用的校准半份、eligible 端点数等）。

routine-only 冒烟（`--routine-only-smoke`）只写 `far` / `far_by_calibration_half` / `endpoint` /
`temporal.confusion` / `attribution` / `worst_group`；`recall_*` 与 `primary_event_hits_plus_8`
在冒烟产物中**不存在**（`trm3.routine_only_metrics` 会在看到任何 attack/drift trace 时直接抛错）。

### 2.2 门（`trm3.check_gates`）

每个门给出 `{gate, definition, status, pass, value, ...}`；缺少输入时 `status = "not_evaluated"`
（例如冒烟时没有静默抵御 → G4）。G5：`--calibration both` 会在同一次调用里跑两列并在运行内评价
（结果同时写入两列的门块与顶层 `g5`）；单列调用仍可用 `--gate-reference` 指向另一列的 `result.json`。
G8 的参照是**冻结 CAND-A** 产物（`artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late`，mode D，
alpha=0.10，reading=max）的每 1000 端点报警 onset 数（v1.1 修订 6）；其分母是冻结的非 drift 端点池，
与本运行的 clean+benign 端点池不同，该分母说明写在 `g8_reference.denominator_caveat` 与
每个 G8 门块的 `reference_note` 里。

## 3. 锚点约定

| 目标 | 主事件正例 | 锚点 |
|---|---|---|
| b1 / b2 | drift ∪ 有锚点抵御 | `product_onset_v1_adjudicated.jsonl` 的 `product_onset`；抵御用 `topic_entry_v1_adjudicated.jsonl` 的 `topic_entry_onset`（为 null 即无锚点，不进主事件） |
| h384 | 执行 ∪ 有界抵御 | **主** = `labels.engagement_onset`（证据段首 token，执行与有界抵御同用）；**副** = `labels.execution_onset`（证据段末 token，中位晚 6）。两列都报（v1.1 修订 3），来自冻结的 routing-blind engagement 裁定 |
| c1_heldout | 无（纯正常） | — |

## 4. 复现

```
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v3/run_trm3.py \
    --variant trm3,m_only,s_only,j_only,sm,mj,sj,unseen_only,surprisal_marginal,no_temporal,no_temporal2 \
    --calibration both --target b2 --outputs primary
```

冻结提交之前只允许：

```
... --variant m_only --calibration D --target b2 --routine-only-smoke
```
