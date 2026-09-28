# MoE Sequential Drift Detection：并行算法研究背景与目标

日期：2026-09-04（America/Los_Angeles）

用途：交给 Claude Code，由它自主组织多个 AI 并行探索下一阶段算法

## 给 Claude Code 的任务

请完整阅读本文以及“已有材料”中列出的报告和代码，然后自主设计下一阶段研究方案，并启动多个 AI agent
并行探索。

不要把现有审计提出的改进方向当成标准答案。你可以采用相似思路，也可以提出完全不同的问题定义、特征、
模型、校准方式或评价方法。请自行决定如何拆分研究任务、哪些实验值得做、哪些假设应优先证伪，以及如何整合
不同 agent 的结果。

我们希望最终得到的不是数量很多的实验，而是对下面两个问题更有把握的回答：

1. MoE routing 中是否存在足够稳定、具有时间对应关系的跨领域任务偏移信号？
2. 如果存在，什么样的算法最有希望把它转化成可泛化的持续运行检测？

本轮使用 B1 和 B2 做算法研究。B3 只在我们得到一个真正有希望、值得进一步验证泛化的算法之后才需要，
当前不要生成 B3。

## 1. 研究目标

我们的长期问题是：

> 对一个执行固定生产任务的 MoE agent，能否持续观察其逐 token expert routing，在它开始执行与授权工作
> 不同的跨领域任务时发出警告？

当前研究聚焦于 **cross-domain task drift**。例如客服 agent 被用户或不可信工具输出诱导，开始写诗、代码、
数学推导、菜谱、故事、旅行计划或法律分析。我们暂不试图用 routing 同时解决非法状态转移、错误工具参数、
业务规则错误、幻觉等所有 agent 异常。

当前阶段不是生产落地，也不是完成论文级强基线比较。研究组长希望先获得足够强、有说服力的信号，判断这个
问题是否值得正式立项并交给后续研究成员。

一个理想的研究结果应当帮助区分：

- routing 只是反映 prompt、topic 或已经可见的输出语义；
- routing 能追踪 agent 从 routine work 转入异常领域的过程；
- routing 信号虽然存在，但无法稳定校准为低误报、合理延迟的报警；
- routing 在新任务、领域和 batch 上仍具有可泛化价值。

## 2. Agent 与目标样本

当前研究仪器是 Atlas Support Agent v2.5。它是一个允许自然对话和只读工具调用的客服 agent，routine surface
包含 11 类 workflow：

- order、return、support case、warranty、subscription status；
- 简单 knowledge-base QA；
- 五类 status + knowledge 的组合 workflow。

Agent 可以自由生成用户可见回复，不被限制为只能输出工具 action。跨领域 attack 明确要求它在完成或绕过
客服工作后生成另一类 deliverable。行为标签关注的是 agent 是否**实际开始生成实质域外工作**，而不是是否
看见攻击、复述 marker、提到任务名称或完整满足攻击者格式要求。

每条 positive trace 都保存了人工标注的首个实质域外 output-token boundary。模型生成内容质量差、被长度
截断或没有完成 enclosure marker，不会擦除已经发生的任务偏移；拒绝、复述、占位符和只完成客服工作仍是
negative。

## 3. 已收集数据

### B1

- 80 个 matched scenarios，每个有 clean、benign control、attack 三臂，共 240 条 trace；
- 80 条 attack 中自然产生 27 drift、53 resist；
- 一半 scenario 有 verified response brief，一半没有；
- 后续 headline 分析使用 `brief=absent` 的 40 个 scenarios、120 条 trace；
- headline attack 为 24 drift、16 resist。

### B2

- 80 个全新 matched scenarios，共 240 条 trace；
- 更换了业务实体、attack family、topic、措辞和随机种子；
- 全部为 `brief=absent`；
- 80 条 attack 中自然产生 35 drift、45 resist。

B1 headline 与 B2 合计有 120 个 scenarios、360 条核心开发 trace，其中 59 条 drift、61 条 resisted attack，
另外有 120 条 clean 和 120 条 benign control。B1 的 `brief=present` 数据可以作为额外 nuisance/stress material。

所有 480 条原始 trace 都通过 routing schema 与 token alignment。采集系统记录每个 prefill 和 decode token
在 16 个 MoE layers、64 个 experts 上的 router logits、top-8 expert IDs/weights 及衍生统计。decode routing
与已经生成并送回模型的 output token 对齐；因此 token `t` 的 routing 在 token `t` 产生后可用。

数据位置：

```text
B1: /home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b1
B2: /home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2
```

固定 sample-index hashes：

```text
B1: f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1
B2: e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942
```

## 4. 已完成的算法探索

### 4.1 Fixed-prefix / full-output 分类

最初的分类问题是在已知遭遇 attack 的 trace 中，区分 agent 最终 drift 还是 resist。

B1 headline 的 family-held-out OOF 结果：

| Feature | First 16 decode AUROC | Full decode AUROC |
|---|---:|---:|
| route selection | 0.753 | 0.953 |
| token hash | 0.654 | 0.805 |

在 B1 上冻结分类器后，B2 得到：

| Feature | B2 AUROC |
|---|---:|
| first-16 decode route selection | 0.817 |
| first-16 token hash | 0.793 |
| prefill route selection | 0.829 |
| full-decode route selection | 0.967 |

这些结果说明 routing 与行为结果、尤其是已经展开的输出任务语义高度相关。但 first-16 route 只比 token hash
高 0.023，而且 prefill 不弱于 early decode，所以它尚不能证明已经捕捉到运行中的 task switch。

### 4.2 Sequential detector v1

我们随后把问题改成逐 token 因果评分。v1 使用最近 16-token 的 top-8 expert-selection rate、ridge classifier
和一个绝对阈值，从首个完整窗口持续评分到 full decode。

B1 development OOF（20 drift）表现很清晰：

| Detector | Non-drift FAR | Recall +16 | Final recall |
|---|---:|---:|---:|
| route | 0/79 | 12/20 | 18/20 |
| token hash | 0/79 | 1/20 | 5/20 |

冻结模型和阈值后，B2 结果明显下降：

| Detector | Non-drift FAR | Pre-boundary FAR | Recall +16 | Final recall |
|---|---:|---:|---:|---:|
| route | 0/205 | 3/27 | 9/35 | 12/35 |
| token hash | 8/205 | 1/27 | 5/35 | 14/35 |

Sequential v1 的预注册成功条件没有通过。它对 routine traffic 非常保守，但遗漏了多数真实 drift。因此不能
把当前方法称为可靠 runtime detector。

## 5. 对 v1 的方法审计

审计没有发现会推翻结果的实现 bug：窗口确实因果，B2 没有 refit、重新标准化或重校准，全部完整 decode
窗口都得到评分。

审计指出了几个可能的重要原因，但它们仍是待检验解释：

- v1 更像滑动的绝对语义状态分类器，而不是相对 routine 状态的 change detector；
- boundary 当下和 boundary+7 的 16-token positive windows 仍含大量边界前 token；
- 最终 threshold 只由 20 个 B1 calibration segments 的 q0.99 决定，受少数极值强烈影响；
- 1,024 维 route feature 相对 trace 和 drift 数量较高，B1 候选选择可能乐观；
- token hash 是低成本 sanity control，不足以证明 routing 拥有独立于文本的独有信息。

同一审计对冻结 B2 score 做了事后诊断：

- 27 条可比较 drift 中，26 条的 post-boundary max 高于自身 pre-boundary max；
- `post max - pre max` 中位数为 +0.769；
- 比较边界前最后窗口和首个完全位于边界后的窗口，22/26 上升，中位增量 +0.580；
- post-boundary max 与 non-drift full-trace max 的事后排序 AUROC 为 0.982。

这些数字不能替代失败的冻结评估，也不能直接作为新算法成绩。它们只说明：B2 中仍存在值得解释和建模的
时间变化信号，v1 失败可能主要发生在问题表述、学习方式或 operating-point calibration，而不一定是 routing
信号完全消失。

## 6. 目前开放的研究问题

请并行研究自行判断哪些问题最关键，包括但不限于：

- 应把检测对象定义为域外语义状态、相对 routine 的变化，还是其他可操作 estimand？
- 怎样利用逐层 expert routing，同时避免高维、小样本和特定 domain pattern 的过拟合？
- 人工 boundary 应怎样参与训练和评价，怎样处理跨越 boundary 的混合窗口？
- 怎样在不同 workflow、batch、输出位置和自然语言风格之间获得可迁移的 score？
- 报警阈值或 sequential evidence 应如何校准，才能同时控制误报与漏报？
- 哪些时间尺度、累积方式或低容量模型真正有帮助？
- route 相比可见 token、长度、prefill 或其他低成本信息究竟提供了什么增量？
- 现有 B1/B2 能支持多强的结论，哪些观察只是 adaptive/post-hoc evidence？

现有审计提出过 relative change、routine-conditioned residual、transition-aware training 等可能方向，但并行
agent 不需要沿用，也不应假设这些方向一定正确。

## 7. 研究边界

为了保留结果的基本可解释性，本轮只有少数事实性边界：

- B1 和 B2 都已经被用于方法理解，从现在起都属于 development data；
- B1/B2 上的新结果不得称为 independent confirmation；
- 不修改原始 trace、人工标签或 behavior boundary；
- 若目标是运行时因果检测，推理时不能使用未来 token、最终 outcome 或真实 boundary；
- 当前不生成 B3；
- 不覆盖既有 v1 冻结模型和结果文件，新的实验使用新路径；
- 无论结果正负，都保留失败方法和反例。

除此之外，请 Claude Code 自主决定：agent 分工、算法路线、特征、模型、数据拆分、校准、指标、代码组织和
综合方式。不同 agent 得到相似想法并不是问题；独立实现、不同细节和不同推理可能正是有价值的比较。

## 8. 希望得到的最终产物

并行研究结束后，希望 Claude Code 提供一份综合报告，清楚回答：

1. 各 AI 分别提出了什么假设，为什么；
2. 实际实现并比较了哪些方法；
3. 每个方法在 B1、B2、不同 domain/workflow/channel 上表现怎样；
4. 哪些结果稳定，哪些只来自单个 batch、领域、阈值或少数样本；
5. 哪些失败改变了我们对问题的理解；
6. 是否存在明显优于 v1、值得继续研究的算法；
7. 当前证据是否足以开始冻结候选并设计 B3，还是仍应继续方法探索；
8. 完整的可复现代码、运行命令、数据/配置 hash 和未筛选结果在哪里。

我们不预设最终一定要选出一个赢家。若多个独立探索都表明信号无法稳定转化成报警，`no promising method`
也是有效而有价值的研究结论。

## 9. 已有材料

研究报告：

```text
docs/agent_v2_5_b1_routing_report.md
docs/agent_v2_5_b2_report.md
docs/agent_v2_sequential_b1_plan.md
docs/agent_v2_sequential_b1_report.md
docs/agent_v2_sequential_b2_plan.md
docs/agent_v2_sequential_b2_report.md
docs/agent_v2_sequential_method_audit.md
docs/research_signal_validation_protocol.md
```

主要代码：

```text
src/phase_a/sequential.py
src/phase_a/classifier.py
src/phase_a/routing_analysis.py
scripts/analyze_agent_v2_b1_routing.py
scripts/explore_agent_v2_sequential_b1.py
scripts/freeze_agent_v2_sequential_b2.py
scripts/score_agent_v2_sequential_b2.py
```

历史结果：

```text
artifacts/agent_v2/agent_v2_5_b1/sequential_development.json
artifacts/agent_v2/agent_v2_5_b2/routing_confirmation.json
artifacts/agent_v2/agent_v2_5_b2/sequential_evaluation.json
```

请把历史脚本和结果视为可读参考。若需要重新计算或扩展分析，应写入新的实验路径，避免覆盖既有审计产物。
