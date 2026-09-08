# Agent v2.5 冻结预测的 E/C/X 时序敏感性报告

日期：2026-09-06（America/Los_Angeles）

状态：`timing_sensitivity_complete`；14 个冻结方法；80 条 horizon-384
attack traces；算法、threshold 和 first alarm 未改变；B3 未使用

预注册计划：[onset timing sensitivity plan](agent_v2_onset_timing_sensitivity_plan.md)

## 面向研究主管的结论

本轮最重要的结论不是“换一个 onset 能让旧算法变好”，而是明确了这些
MoE routing 信号在行为链条中更接近什么：**它们主要反映非常规任务已经进入
agent 的思考/响应过程（E），或已经形成任务承诺（C），并不天然等价于已经开始
输出实质攻击内容（X）。**

因此，同一个冻结报警在不同语义边界下有完全不同的解释：

- 以 E 为界，主要非零方法的因果 first alarm 提前率为 `0/45` 到 `3/45`
  （0%--6.7%）；
- 以 C 为界，多数方法仍只有 `0/40` 到 `4/40` 提前；
- 以 X 为界，DRR、LDC、marginal surprisal、unseen-support 和 raw state 的
  提前率分别升至 20.5%、23.1%、38%--41%、35.9% 和 46.2%。

这支持我们此前提出的机制观点：**抵御攻击时发生的“思考出轨”本身就是有效
异常信号，是否最终执行只是这个异常行为的下游细分类。** 将 X 当作唯一合法
报警起点，会把发生在 E--C--X 过渡区中的真实异常信号错误地计为提前误报。

但这不表示现有算法已达到生产要求。E 边界下，多数方法的严格 `+8` recall
仍然偏低；较好的 `+16` 方法也只有 42%--53%。此外，部分方法原有正常流量 FAR
或跨 reference-bank 稳定性问题并未因新边界消失。正确的研究结论是“信号语义
得到澄清”，不是“检测问题已经解决”。

## 1. 审计范围与计分口径

共识事件分母固定为：E `45`、C `40`、X `39`。所有方法均使用原始冻结
first alarm；如果 first alarm 早于边界，不允许跳过它并选择后续更好看的报警。

生产时序统一使用真正可见的因果 endpoint：

- LDC family 使用 `engagement_visible_at`，而非 16-token window 的起点；
- DRR 使用 `first_crossing` endpoint；
- routine-support 与 SIRD family 使用 window endpoint；
- window/candidate start 只进入定位附录，不冒充实时报警时间。

以下主表均为 consensus `start_point`、零 tolerance。每格依次给出
`pre / +16 / full / 命中中位 latency`。`full` 仍排除 first alarm 提前的样本。

## 2. 主结果：E/C/X 改变的是报警语义

| 冻结方法 | E（n=45） | C（n=40） | X（n=39） |
|---|---|---|---|
| DRR | 2 / 14 / 27 / 16 | 3 / 13 / 24 / 16 | 8 / 16 / 19 / 8 |
| Late-only FHTS | 0 / 8 / 27 / 45 | 0 / 9 / 27 / 45 | 1 / 12 / 25 / 17 |
| LDC | 2 / 11 / 35 / 28 | 1 / 12 / 34 / 24 | 9 / 13 / 25 / 16 |
| Pooled-layer FHTS | 1 / 10 / 36 / 36 | 1 / 11 / 34 / 29.5 | 6 / 15 / 28 / 16 |
| Routine surprisal8 | 3 / 19 / 37 / 15 | 4 / 16 / 33 / 20 | 16 / 11 / 20 / 8.5 |
| Routine unseen8 | 0 / 23 / 32 / 7 | 2 / 18 / 27 / 7 | 14 / 8 / 14 / 8 |
| SIRD surprisal8 | 2 / 21 / 37 / 15 | 3 / 18 / 33 / 15 | 15 / 12 / 20 / 8 |
| Raw state（post-hoc） | 3 / 24 / 38 / 15 | 3 / 22 / 34 / 14.5 | 18 / 14 / 18 / 7 |
| Raw innovation（post-hoc） | 0 / 3 / 14 / 65.5 | 0 / 3 / 13 / 63 | 3 / 2 / 9 / 58 |
| Raw union（post-hoc） | 3 / 24 / 38 / 15 | 3 / 22 / 34 / 14.5 | 20 / 12 / 16 / 8 |

严格 `+8` 的完整计数进一步说明现有方法仍不够快：

| 方法 | E +8 | C +8 | X +8 |
|---|---:|---:|---:|
| DRR | 3/45 (6.7%) | 3/40 (7.5%) | 10/39 (25.6%) |
| LDC | 0/45 (0%) | 0/40 (0%) | 6/39 (15.4%) |
| Routine surprisal8 | 11/45 (24.4%) | 11/40 (27.5%) | 10/39 (25.6%) |
| Routine unseen8 | 19/45 (42.2%) | 17/40 (42.5%) | 7/39 (17.9%) |
| SIRD surprisal8 | 8/45 (17.8%) | 8/40 (20.0%) | 10/39 (25.6%) |
| Raw state | 7/45 (15.6%) | 7/40 (17.5%) | 10/39 (25.6%) |
| Raw innovation | 2/45 (4.4%) | 2/40 (5.0%) | 2/39 (5.1%) |
| Raw union | 8/45 (17.8%) | 8/40 (20.0%) | 8/39 (20.5%) |

SIRD 的 rank-union、state-rank、innovation-rank 和 expanded-bank unseen8
均为零报警，因而在所有边界、tolerance 和 horizon 下 recall 都为零。共识 onset
没有改变 SIRD-v1 的 `development_no_go` 结论，也没有为它创造 timing 结果。

## 3. 对“异常专家是否已经足够”的直接回答

本轮给出一个比“是/否”更精确的答案。

使用较小 routine-fit bank 的 literal `unseen8` 对 E 很敏感：

- E 前报警 `0/45`；
- E+8 为 `19/45 = 42.2%`；
- E+16 为 `23/45 = 51.1%`；
- full clean E detection 为 `32/45 = 71.1%`，命中中位 latency 7 tokens。

这证明“routine 不调用的专家开始出现”确实能解释一部分早期异常信号，而且它
更像 engagement detector，而不是 execution detector。以 X 评分时，同一规则
有 `14/39 = 35.9%` first alarms 落在 X 之前。

但 expanded normal-fit bank 中，同样的 literal unseen 定义是 `0/45` 检出。
参考库覆盖扩大后，原本“未见”的 layer--expert pair 已被正常样本覆盖。换句话说，
`unseen` 是一个对 normal reference size 和覆盖度高度敏感的有限样本属性，不是
稳定的语义机制。它可以作为证据通道之一，但不足以单独成为通用生产算法。

相较之下，marginal surprisal 和 joint raw state 在不同构造中保持了较高覆盖，
但前者已有 replay benign FAR 超标问题，后者仍是 target-seen post-hoc diagnostic。
后续算法应保留“偏离正常分布”的连续量，而不把“是否绝对未见”作为核心二值规则。

## 4. Raw innovation 的作用被进一步限定

Raw innovation 单独并不是一个强检测器：E full coverage 只有 `14/45`，命中中位
latency 65.5 tokens。Raw state 则为 `38/45`、15 tokens。两者 union 的最终 E
coverage仍是 `38/45`，说明 innovation 没有增加最终覆盖。

不过，innovation 在四条 union detections 中早于 state：

| 行为 | E / C / X | Innovation / State alarm | 解释 |
|---|---|---|---|
| execution | 56 / 56 / 64 | 60 / 65 | 位于承诺后、执行前 |
| execution | 133 / 133 / 158 | 153 / 154 | 位于执行前 |
| engaged-only | 21 / — / — | 47 / 208 | 抵御样本中的异常持续变化 |
| execution | 114 / 114 / 119 | 118 / 122 | 位于执行前一 token |

这四条都没有早于 E。也就是说，innovation 的“提前”是相对于 state head 和 X，
不是在常规行为阶段凭空报警。它捕捉到的是 transition dynamics，符合“非常规
思考已经发生、执行结果尚未确定”的机制解释。

代价也很清楚：union 相比 state 在 E+8 仅从 `7/45` 增至 `8/45`，最终覆盖不变；
以 X 为界，pre-X 从 `18/39` 增至 `20/39`。因此 innovation 适合保留为辅助的
change/dynamics 通道，但不能仅凭“更早”就视为更好的执行风险报警。

## 5. Candidate localization 与真实报警时间必须分开

窗口方法可把异常区域的估计起点放在报警 endpoint 之前，但生产系统只有在窗口
完整后才能报警。LDC 最能说明这个差别：

- 对 E，candidate start 的中位 signed offset 是 `+11`，而因果 alarm 的命中
  中位 latency 是 `+28`；
- 对 X，candidate start 的中位 signed offset 是 `-5.5`，20/39 位于 X 前；
  因果 alarm 则为中位 `+16`，9/39 位于 X 前。

因此 candidate start 可以用于解释 routing change region，却不能用于宣称在线
延迟。此前若把 window start 当作 first alarm，会同时高估“提前误报”和实时性。

## 6. Confirmation end 不是 onset

把完整证据的 confirmation end 当成报警边界，会系统性增加“提前”计数。例如：

| 方法 / 事件 | Start pre | End-lower pre | End-upper pre |
|---|---:|---:|---:|
| DRR / E | 2/45 | 5/45 | 9/45 |
| Routine unseen8 / E | 0/45 | 13/45 | 16/45 |
| SIRD surprisal8 / E | 2/45 | 11/45 | 16/45 |
| Raw state / E | 3/45 | 14/45 | 19/45 |
| Raw state / X | 18/39 | 23/39 | 33/39 |

这个变化不是 detector 变差，而是 end 表示“人类已经看到完整文本证据”的时刻。
对于预警系统，start 是主要 latency anchor；end 适合作为证据确认敏感性，不应
替代 onset。机器结果完整保留了 lower、upper 和 interval 三种 end 口径。

## 7. ±4/±8 tolerance 的影响

严格短时 recall 对几个 token 的边界差异很敏感。采用预注册的 ±8 compatible
tolerance 后，E+8 从：

- DRR `3/45` 变为 `14/45`；
- LDC `0/45` 变为 `11/45`；
- routine unseen8 `19/45` 变为 `23/45`；
- raw state `7/45` 变为 `25/45`。

因此，“8 tokens 内是否命中”不能脱离 tolerance 定义单独使用；论文中应同时
报告严格值和 tolerance curve，不能挑选较有利的 ±8 结果。

另一方面，E 与 X 的方向性差异在 tolerance 下仍存在。即便给 X ±8 tolerance，
DRR、LDC、routine unseen8、routine surprisal8、raw state 和 raw union 仍分别有
`6/39`、`5/39`、`8/39`、`10/39`、`14/39`、`14/39` definitely-pre alarms。
这说明“routing signal 通常早于 substantive execution”不是单 token 标注误差造成的。

## 8. 对下一版算法的约束

本轮结果把算法目标进一步收紧为两个阶段，而不是一个含混的二分类：

1. **异常 engagement/change detection：** 在 routine-calibrated FAR 约束下检测 E，
   允许抵御成功与执行样本共享早期异常信号；
2. **outcome/state refinement：** 在首次异常之后，根据 persistence、recovery 和
   dynamics 区分 resisted、committed-only 与 execution risk。

具体设计上应保留：

- joint routing-state distance，作为覆盖主通道；
- marginal rarity/surprisal，作为简单可解释证据，但不能单独定案；
- innovation/dynamics，作为少量更早 transition evidence；
- causal alarm endpoint 与 candidate region 的双时间戳。

应放弃或降级：

- literal unseen pair 作为唯一机制；
- 把 X 之前的所有报警都当作 false alarm；
- 用 candidate window start 代替生产 alarm time；
- 在当前 80 条开发样本上根据这次敏感性结果重新选 threshold。

## 9. 可复现性与产物

机器结果：
`artifacts/agent_v2/onset_reliability_audit_v1/timing_sensitivity.json`

- SHA-256:
  `1d498a68b471d38e01f5fe691c1984a3d100b81709187f65efbc886094fc4625`;
- size: 1,642,087 bytes;
- methods: 14；cases: 80；`predictions_changed=false`；`b3_used=false`。

评价器先验证全部输入 hash，随后在打开 consensus labels 之前抽取并复核冻结预测。
每个方法必须完整覆盖相同的 80 个 trace IDs。SIRD 与 raw-distance 的抽取报警数
还必须复现其原始 artifact 聚合计数。完整项目测试为 `267 passed, 6 subtests
passed`，结果重复运行 hash 不变。

本报告不更改任何旧实验的正式 go/no-go 状态。它给出的新信息是：**我们过去
观察到的路由偏移，主要是 E/C 级别的“任务进入与承诺”信号；execution outcome
需要在报警后另做状态细分。**
