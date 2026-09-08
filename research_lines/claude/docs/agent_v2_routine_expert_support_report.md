# Routine 专家支持集能否单独检出异常：开发集验证报告

## 结论摘要

“异常样本会调用 routine 工作从未调用过的专家”是一个真实且很有预测力的现象，
但它不是当前检测能力的充分解释，也不足以单独承担在线检测。

- 26 条正常拟合轨迹共提供 260,096 次 layer--expert 选择。在 16 x 64 个
  layer--expert 对中，只有两个从未出现：`layer 8 / expert 14` 和
  `layer 13 / expert 14`；14 层已经覆盖全部 64 个专家。
- 仅检测这两个未见 pair，按整条轨迹判断，可筛出 29/40（72.5%）执行样本，
  而 160 条 replay 正常/良性控制中报警 6 条（3.75%）。这是一个强而廉价的
  trace-level screening signal。
- 将假设放宽为“调用正常流程中的低频专家”，可筛出 37/40（92.5%）执行样本；
  但控制误报升至 14/160（8.75%），且良性控制单独为 13/80（16.25%）。
- 两个简单 detector 都未通过预注册的在线充分性 gate：报警经常早于冻结的行为
  起点，起点后 16 tokens 内的召回都只有 11/40 以下，而且 rare-expert 版本对
  384-token 良性控制的误报略超 15%。
- 对已有 detector 的报警窗口做归因后，LDC 的 22 个起点后执行检出中只有
  6 个（27.3%）包含未见专家；DRR 为 13/23（56.5%）。因此，尤其对 LDC，
  大多数有效报警发生在完全由 routine 已见专家组成的窗口内。

最稳妥的机制结论是：**marginal expert novelty 是异常路由的重要组成部分和很强的
廉价筛查信号；现有检测器的剩余能力来自 routine 支持集内部的调用比例重排、
layer-wise 组合变化，以及这些变化的时间方向和持续性。**

## 实验设计

分析计划在读取本次目标 routing 结果前冻结于 commit `2979c24`，计划 SHA-256
为 `17b4f45dee9a538a7136878c5f9b063919d6fc8d86e064748c3113e2e12f02a7`。
B3 未使用。

数据角色如下：

- 26 条 B1/B2 canonical clean traces：只用于统计每层每个专家的 routine 选择次数；
- C1 folds 0--2：100 个 clean/benign matched groups，用于 rare-expert 阈值校准；
- C1 folds 3--4：60 个 matched groups，用于正常分布 held-out FAR；
- horizon-384 replay：240 条 paired traces，用于开发集异常检出和报警归因；
- frozen LDC/DRR outputs：只用于分析已有报警窗口，不参与简单 detector 的拟合。

两个固定信号均使用 8-token causal rolling window：

1. `unseen8`：窗口内只要出现正常拟合集从未选择过的 layer--expert pair 即报警；
2. `surprisal8`：对正常选择频率做 0.5 pseudocount smoothing，计算 16 x 8 个
   当前选择的平均 `-log p(layer, expert)`，阈值由 C1 calibration group path-max
   的 alpha=0.10 finite-sample upper order statistic给出，固定为
   `4.2752444027`。

二者均不知道文本、任务、workflow、attack channel、domain、token identity、绝对
位置或输出格式，也未选择特殊层。

## 结果一：正常支持集几乎已经覆盖所有专家

每层有 16,256 次 routine 选择。只有以下两个 layer-local pair 的计数为零：

| Layer | Expert | Routine count | 该层已覆盖专家数 |
|---:|---:|---:|---:|
| 8 | 14 | 0 | 63/64 |
| 13 | 14 | 0 | 63/64 |

这里的两个 expert 14 是不同层中的局部专家，不能当成同一个共享模块。该结果也表明，
“进入一大片正常工作完全不用的专家子网络”并不符合当前模型：可利用的字面支持集扩张
实际上集中在两个 pair 上。

## 结果二：作为整轨迹筛查器，简单信号很强

| Detector | C1 held-out group FAR | Replay clean FAR | Replay benign FAR | 执行检出 | 有界抵御检出 | 静默攻击检出 |
|---|---:|---:|---:|---:|---:|---:|
| `unseen8` | 5/60 (8.3%) | 0/80 (0.0%) | 6/80 (7.5%) | 29/40 (72.5%) | 3/5 (60.0%) | 1/35 (2.9%) |
| `surprisal8` | 5/60 (8.3%) | 1/80 (1.25%) | 13/80 (16.25%) | 37/40 (92.5%) | 3/5 (60.0%) | 2/35 (5.7%) |

如果把问题限定为“看完整条轨迹后区分 execution 和 routine controls”，两者的
pooled-control false-positive rate 分别为 3.75% 和 8.75%，对应的描述性 balanced
accuracy 分别约为 84.4% 和 91.9%。所以该假设不是弱相关：它足以构成一个非常便宜
的 offline screening baseline，rare-expert 放宽版本尤其强。

但这不是预注册 gate 所要求的在线充分性。整轨迹统计允许异常起点以前或很晚的任意
位置触发，不能说明 detector 在异常真正发生时给出了因果、及时的报警。

## 结果三：作为在线 detector，单一信号不充分

| Detector | 执行起点前 first alarm | 执行 clean post-onset | 执行 onset+16 | 执行 onset+32 | 有界抵御 clean post-onset |
|---|---:|---:|---:|---:|---:|
| `unseen8` | 10/40 (25.0%) | 19/40 (47.5%) | 10/40 (25.0%) | 11/40 (27.5%) | 3/5 |
| `surprisal8` | 11/40 (27.5%) | 26/40 (65.0%) | 11/40 (27.5%) | 18/40 (45.0%) | 2/5 |

在全部 45 条有 engagement onset 的攻击轨迹中，first alarm 提前率分别为
10/45（22.2%）和 12/45（26.7%），均超过预注册上限 10%。

Gate 失败原因：

- `unseen8`：execution clean post-onset recall、onset+16 recall 和 pre-onset rate
  未达标；
- `surprisal8`：replay benign FAR、onset+16 recall、bounded clean-post count 和
  pre-onset rate 未达标。

因此，不能把整轨迹 72.5%/92.5% 的高检出率直接解释为一个合格的在线 detector。
它能识别“这条轨迹整体不寻常”，但不稳定地定位“非常规思考从何时开始”。

## 结果四：已有报警并不主要由未见专家驱动

以 frozen engagement onset 为界，只看已有 detector 的 clean post-onset execution
报警，并直接检查其原始报警窗口：

| Existing detector | Clean post-onset execution alarms | 窗口含 unseen pair | 窗口内存在 `surprisal8` 超阈片段 |
|---|---:|---:|---:|
| LDC | 22 | 6 (27.3%) | 14 (63.6%) |
| DRR | 23 | 13 (56.5%) | 14 (60.9%) |

两个补充观察很关键：

- LDC 的 4 个静默攻击报警和 DRR 的 4 个静默攻击报警，窗口内均没有 unseen pair；
- LDC 有 16/22、DRR 有 10/23 个起点后执行报警，在完全由 routine 已见专家组成
  的窗口内发生。

因此，未见 pair 对 DRR 的贡献较大，对 LDC 也有贡献，但它不能解释任何一个 detector
的全部能力，更不能解释 LDC 的多数有效报警。低频边际比“严格未见”覆盖更多现有报警，
说明 expert marginal frequency 的确是完整分布距离中的重要成分；但 LDC 仍有 8/22、
DRR 仍有 9/23 个起点后执行报警，在同一报警窗口内没有任何完整的 rare-expert
`surprisal8` 超阈片段。

## 对机制和算法的含义

目前证据支持一个三层分解：

1. **支持集扩张**：极少数 routine 未见 layer--expert pair 进入，信号稀疏但很强；
2. **支持集内边际重排**：仍调用已见专家，但把概率质量移向 routine 低频专家；
3. **联合结构和动力学变化**：已见专家的层内分布、跨层组合、变化方向与持续时间发生
   改变。这一部分解释了 simple marginal novelty 没有覆盖的已有报警，也与此前发现的
   late-layer onset spike、early/middle persistence 和 recovery/execution 分叉一致。

下一版通用算法不应丢掉第一层信号：`unseen8`/`surprisal8` 非常适合做计算成本极低的
辅助通道或 first-stage alarm。主 detector 仍应保留 layer-wise distribution distance
和 temporal state，因为仅靠“调用了 routine 不用/少用的专家”无法提供足够稳定的
在线时序，也无法覆盖全部现有有效报警。

## 完整性与限制

- 输出 artifact：`artifacts/agent_v2/routine_expert_support_v1/result.json`；
  SHA-256 `6a90c19bc9c5fba81599d1ae9f8cc69fef8bcdb798fe7285492f70039fc3beec`。
- 本报告只作 development mechanism inference，未使用 B3，不能视为独立确认。
- “routine 从未出现”受 26 条拟合轨迹的有限覆盖影响，不等于模型在总体正常任务中
  概率严格为零。
- 首次执行后发现报警归因窗口错误地引用了 overlapping rolling score；随后按冻结计划
  修正为直接检查 exact raw-token alarm window。两个 detector、阈值、gate 和主检出指标
  均未改变；本报告只使用修正并重跑后的 artifact。
