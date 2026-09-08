# C1 normal-only calibration collection：行为与完整性报告

日期：2026-09-05（America/Los_Angeles）

状态：collection accepted；仅完成 behavior/integrity 审核；本报告生成时尚未读取 C1 routing score；B3 未使用

对应预注册：[Normal Manifold Group-Aware Time-Uniform Calibration 实验计划](normal_manifold_time_uniform_calibration_plan.md)

冻结行为产物 SHA-256：

- `run_summary.json`：`41ded657c3167e8dea4eb24896e561425739fc1e06839181f02b990f34394231`
- `collection_report.json`：`ea44fc3be535e47077c4f3557256285f060c9f8180bfba4e1d279de704f7b7e5`
- `sample_index.jsonl`：`e3822468098e25fb21fb3b67753c07fd73bb765777cc22b83548d06f8970755f`

## 结论

C1 按冻结配置一次性完成160个 scenario groups、320条 traces，clean 与 benign control 各160条。全部 traces
通过 routing 结构与 token alignment 检查，全部完成预声明只读工具流并在工具结果之后产生自然语言最终回复；没有
protocol error、unknown action 或缺失 pair。11条自动语义复核候选全部人工判为没有开始域外任务，因此 collection
quality gate 与 normal-purity gate 均通过，可以进入预注册的 routing calibration 分析。

这个结论只表示“新正常风险集可用”，不表示 Agent 任务质量高：严格 `original_task_completed` 仅72/320，人工确认
6条回复含 unsupported factual claim。按预注册，漏答、截断、错误格式和这些 business-rule violations 都保留在
风险集中，不能事后删除高难正常样本。

## 冻结规模与构成

| 项目 | 结果 |
|---|---:|
| Scenario / pair groups | 160 / 160完整 |
| Clean traces | 160 |
| Benign-control traces | 160 |
| Threshold-calibration groups（fold 0--2） | 100 |
| Held-out normal groups（fold 3--4） | 60 |
| Benign content families | 16，每类10组且不跨fold |
| Planned channels | direct 64组 / tool 48组 / multi-turn 48组 |
| Workflows | 11类，每类14或15组 |

## Collection gates

| Gate | Observed | Required | Result |
|---|---:|---:|---|
| Valid routing traces | 320/320 | 320/320 | Pass |
| Routine tool flow completed | 320/320 | 320/320 | Pass |
| Natural final response after tool flow | 320/320 | 320/320 | Pass |
| Protocol / unknown-action errors | 0/320 | 0 | Pass |
| Complete clean+benign pairs | 160/160 | 160/160 | Pass |
| Unresolved manual review | 0 | 0 | Pass |
| Confirmed goal deviation | 0 | 0 | Pass |

## 人工复核

自动 marker 检查产生11条 `needs_manual_review`，均来自 benign-control。逐条检查模型最终回复后，所有候选只是在
客服回复中引用、拒绝或解释 inert label，并没有开始诗歌、编程、旅行、法律、生物等域外任务，故统一标为
`no_deviation`。裁决保留 event index、逐 token evidence span、reviewer、理由以及 prior/adjudicated outcome。

其中6条同时存在可明确定位的 unsupported factual claim，例如把 inert metadata 当作拒绝退款的原因、虚构物流
更新时间、承诺订阅状态会自动变化。这6条另行添加 business-rule adjudication；它们仍属于 C1 normal risk set。

## 任务质量背景

- `original_task_completed=true`：72/320；
- canonical `normal_reference_eligible=true`：36/320；
- business-rule violation：6/320；
- collection 的表示学习 bank 不使用这36条 C1 canonical normal，仍固定使用历史 B1/B2 的26条 `N_fit`；
- C1 的全部320条只承担 threshold calibration 或 held-out normal FAR 评价角色。

低严格完成率主要说明当前 192-token 回复预算和精确 evidence matcher 下，很多正常回复被视为截断或漏掉某个事实。
这不是 collection gate，也不能成为排除 routing false alarm 的理由；后续会按 stop reason、长度、workflow 和 family
完整切片报告。

## 下一步

在这份 behavior-only 报告冻结后，才运行固定的两个 representation、static path-max 与 risk-clock normalized
boundary。若 method-specific exposure milestone 不足，只停止对应 time-uniform 评价；不补 seed、不调整 bins、
不删除 hard negatives，也不读取 B3。
