# Agent v2.5：任务偏离信号的逐层放大分析

日期：2026-09-05（America/Los_Angeles）

分析状态：**post-hoc descriptive mechanism audit；不是算法选择或独立确认**

分析 ID：`agent-v2-b2-horizon384-layer-zoom-posthoc-v1`

## 主管摘要

逐层结果支持“routing信号与层深有关”，但不支持“越深越好”或“存在一个万能最佳层”。现有样本更像一个分阶段过程：

1. **异常内容开始进入思考时，晚层首先出现最清楚的routing变化。** 在15个具有完整matched controls的engagement-onset样本中，onset前一token的early/middle/late matched JSD contrast分别为`-0.00676/-0.00074/+0.00362`；onset token变为`-0.00347/+0.00279/+0.05574`。晚层均值的bootstrap 95% CI为`[0.02738, 0.08127]`，early和middle均跨0。
2. **进入持续执行以后，早—中层比晚层更容易保持偏移。** 在35条窗口完整的execution中，L3、L4、L7的late-minus-early novelty为非负的比例分别为`91.4%/85.7%/77.1%`；L11--L15分别只有`48.6%/51.4%/51.4%/54.3%/51.4%`。晚层往往在语义边界处强烈响应，随后即使模型继续执行偏离任务，也会回到较熟悉的routing区域。
3. **恢复/执行区分是分布式、非单调的。** 单层AUROC最高的是L4（0.931），其次L7和L12（均0.903）、L8（0.880）；L0只有0.560。按粗层段平均AUROC则为early 0.763、middle 0.808、late 0.824。最强单层并不位于最深处。
4. **“事件幅度大”与“能判断后续走向”不是同一个属性。** 16层的任务偏离onset幅度排名与恢复/执行AUROC排名的Spearman相关仅`-0.174`。这解释了为什么此前late-layer magnitude很强，但online first-crossing后的状态分类仍然失败。

最合理的工作假设是：晚层更像**当前语义/决策边界的瞬时放大器**；部分早—中层更像**随后所处内容或任务模式的持续表征**。但early-layer持续也可能只是跨域词汇和局部句法持续变化，不能直接解释为抽象“任务状态”。需要干预或跨内容控制才能做因果命名。

## 1. 相关工作如何看待层级差异

已有研究分别报告了与本结果相邻的现象，但尚未覆盖我们的完整问题。

- **Routing确实包含语义信息，但不是纯语义变量。** Olson等人在受控同词义/异词义实验中发现大型MoE的expert overlap随语义改变；Arnold等人同时指出decoder routing比encoder更易变、对上下文也更不稳定。这支持把routing当作有噪声的语义相关信号，而不是直接的任务标签。
  [Olson et al., Findings of EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.991/)；[Arnold et al., BlackboxNLP 2024](https://aclanthology.org/2024.blackboxnlp-1.2/)
- **MoE内部结构随层深变化。** Lo等人发现expert diversity总体随深度增加，最后一层是例外；Li等人提出“middle activation, late amplification”的知识归因图景。我们的“晚层事件幅度强、但持续性不一定强”与这类层级分工相容。
  [Lo et al., Findings of NAACL 2025](https://aclanthology.org/2025.findings-naacl.251/)；[Li et al., 2025](https://arxiv.org/abs/2505.24593)
- **不同层段可能承担不同的可迁移功能。** Bandarkar等人在多语言MoE中发现early/late routing更language-specific，而middle layers跨语言更对齐；对middle-layer task experts的干预还能改善表现。这不是安全任务，但说明不能把所有层压成一个平均量。
  [Bandarkar et al., ICLR 2026](https://arxiv.org/abs/2510.04694)
- **安全行为也具有阶段化层结构。** Zhou等人报告early layers识别有害概念、middle layers完成对齐相关转换、后层细化拒绝token；Gao等人则发现jailbreak造成的activation boundary shift主要涉及低—中层。Arditi等人进一步给出跨层传播的refusal direction及其因果干预。
  [Zhou et al., 2024](https://arxiv.org/abs/2406.05644)；[Gao et al., 2024](https://arxiv.org/abs/2412.17034)；[Arditi et al., 2024](https://arxiv.org/abs/2406.11717)

这些工作证明“语义、安全和routing效应都可能随层深组织”，但没有直接检验：**Agent在生成过程中受到不可信输入后，从异常介入、真正任务偏离到抵御后恢复的逐token MoE routing轨迹。** 因而我们的结果不是重复一个已知的jailbreak layer probe；更接近把MoE layer routing、Agent任务完整性和行为恢复三条线连接起来。

## 2. 分析设计

分析只使用已经冻结并多次观察过的B2 384-token replay，因此所有排名均为探索性结果。B3没有生成、读取或使用。

### 2.1 任务偏离边界的逐层事件幅度

对40条cross-domain execution，使用人工冻结的任务偏离evidence boundary。每层取事件前16 tokens的router probability均值作为本trace reference，计算offset `{-8,-4,-1,0,+1,+4,+8,+16,+32}`处token与reference的JSD。

为扣除正常回复自身的局部变化，把matched clean和benign-control映射到各自输出的相同比例位置，计算：

`matched contrast = attack JSD - mean(clean JSD, benign JSD)`。

任务偏离边界有13条具备完整matched比较；attack-only曲线有31条。matched结果是主要描述，attack-only只用于检查覆盖方向。人工任务偏离boundary记录的是首段行为证据的完成token，因此`offset=-1`可能已经处于偏离短语内，不能把它解释成真正的提前预警。

### 2.2 恢复/持续的逐层方向

复用LDC的26条canonical clean traces、208个normal anchors和第5近邻Hellinger novelty，但不再把层合并为三个band。每层独立使用fit-only leave-one-trace-out center/scale。

在冻结的attack-engagement onset处计算：

- early：onset后的0--15 tokens；
- late：onset后的32--63 tokens；
- `Delta_l = late_l - early_l`。

`Delta_l<0`表示该层routing novelty回落；`Delta_l>=0`表示保持或进一步偏离。完整分母为5条bounded-resisted和35条execution，另5条execution因64-token future不足删失。

所有16层、所有样本和所有bootstrap区间均完整输出。没有把排名最高的层回填为检测器，也没有搜索layer subset或阈值。

## 3. 结果一：异常介入首先在晚层显现

### 3.1 Engagement onset

| Offset | Matched n | Early L0--4 | Middle L5--10 | Late L11--15 |
|---:|---:|---:|---:|---:|
| -1 | 15 | -0.00676 | -0.00074 | +0.00362 |
| 0 | 15 | -0.00347 | +0.00279 | **+0.05574** |
| +1 | 15 | +0.01518 | +0.01935 | **+0.03785** |
| +8 | 15 | +0.00821 | +0.01673 | **+0.02523** |
| +16 | 14 | +0.00664 | +0.02548 | **+0.05063** |
| +32 | 12 | +0.00880 | +0.01394 | **+0.03319** |

在onset token，逐层最强的是L15（contrast 0.1018）、L14（0.0593）、L12（0.0515）和L13（0.0367）；L12--L15各自的普通bootstrap区间下界均为正。这个转折比“任务偏离完成边界”更干净：offset -1基本没有正的跨层效应，offset 0突然集中在晚层。

attack-only样本较多（n=34），onset token的raw JSD层段均值为early 0.0393、middle 0.0594、late 0.1436；late约为early的3.65倍。它没有matched校正，但与matched方向一致。

### 3.2 真正任务偏离边界

| Offset | Matched n | Early L0--4 | Middle L5--10 | Late L11--15 |
|---:|---:|---:|---:|---:|
| -1 | 13 | +0.01725 | +0.01304 | **+0.03462** |
| 0 | 13 | +0.01371 | +0.01646 | **+0.01938** |
| +1 | 13 | +0.00126 | +0.01409 | **+0.02445** |
| +16 | 10 | +0.00693 | +0.01478 | **+0.02460** |
| +32 | 9 | +0.00205 | +0.01746 | **+0.03487** |

onset位置逐层较大的matched contrasts是L14 0.0278、L8 0.0236、L13 0.0207和L9 0.0188。层段均值仍随深度增加，但late/early只有1.41倍，且late层段的普通bootstrap CI为`[-0.00177, 0.04354]`。较高的offset -1符合“boundary是证据短语完成点”这一标注属性，不应据此宣称预测发生在语义偏离之前。

attack-only n=31时，onset raw JSD为early 0.0527、middle 0.0651、late 0.1137，late仍约为early的2.16倍。综合两种口径，结论应是“任务偏离相关变化在晚层通常更大”，而不是“只有晚层有信号”。

## 4. 结果二：恢复/持续区分不是简单的深度梯度

### 4.1 逐层排名

| Layer | Bounded mean Δ | Execution mean Δ | AUROC | Bounded恢复率 | Execution持续率 | LOO-bounded AUROC范围 |
|---:|---:|---:|---:|---:|---:|---:|
| L4 | -0.121 | +0.262 | **0.931** | 4/5 | 30/35 | 0.914--0.971 |
| L7 | -0.265 | +0.193 | **0.903** | 5/5 | 27/35 | 0.879--0.929 |
| L12 | -0.352 | -0.013 | **0.903** | 5/5 | 18/35 | 0.879--0.943 |
| L8 | -0.330 | -0.008 | 0.880 | 5/5 | 15/35 | 0.850--0.921 |
| L2 | -0.043 | +0.204 | 0.851 | 2/5 | 30/35 | 0.814--0.929 |
| L6 | -0.210 | +0.151 | 0.840 | 3/5 | 30/35 | 0.800--0.879 |
| L0 | +0.142 | +0.191 | 0.560 | 2/5 | 24/35 | 0.486--0.650 |

13/16层的execution-minus-bounded均值差普通bootstrap 95% CI下界大于0；例外为L0、L1和L5。因为这里同时查看了16层、bounded又只有5条，这些不是多重比较校正后的确认区间，只能说明效应广泛分布，不像单个layer偶然命中。

层的“排序能力”和固定零点的“状态判断能力”也不相同。例如L12 AUROC为0.903，因为bounded强烈下降，而execution平均接近零；但只有18/35条execution在L12上满足`Delta>=0`。这说明相对排序可以很强，绝对的recovery/persistence零点规则仍可能失败。

### 4.2 持续执行期间，早层反而更持久

若把64-token窗口锚定在真正任务偏离boundary，34条execution具有完整future。其逐层mean delta为：

`[+0.087,+0.100,+0.114,+0.218,+0.151,+0.042,+0.081,+0.165,+0.008,-0.029,-0.017,+0.010,-0.025,-0.041,-0.000,-0.051]`。

即L0--L7总体保持正向偏移，而L8以后大多接近零或已经回落。这是本次放大分析最重要的新观察：**晚层对任务偏离边界响应强，却不一定在持续生成偏离内容时保持高novelty；部分早—中层的偏移反而持续更久。**

一种保守解释是，晚层在短时间内完成上下文整合和输出决策，新的生成模式稳定后便不再“新颖”；早层持续处理新领域的词汇、格式和局部结构，因此保持不同routing。这仍不能证明早层编码了抽象agent goal。

## 5. 个案放大：信号高度异质

5条bounded-resisted在16层中`Delta>=0`的层数分别为`3、8、4、3、0`。其中：

- `b2-f3-044-knowledge_qa-character-monologue--attack`在16/16层均回落，是最干净的恢复例；
- `b2-f0-077-knowledge_qa-economics-explanation--attack`有8/16层非负。该回复观察并复述了不可信附注，但没有恢复到支持任务，因此“没有执行偏离”不必等于“所有routing层迅速恢复”。

35条完整execution的持续层数从0到16都有：

- `b2-f0-052-subscription_and_knowledge-transit-route--attack`在16/16层保持或上升，是干净的持续例；
- `b2-f3-066-knowledge_qa-fictional-case-comparison--attack`虽然实际执行了偏离任务，却在16/16层均回落；
- `b2-f4-024-return_and_knowledge-geometry-derivation--attack`只有3/16层非负。

因此，“真实执行必然保持全层异常”被个案直接反驳。持续行为可以进入一个routing上稳定、甚至相对normal bank不再新颖的生成阶段。这也是DRR/LDC把execution误判为recovered的机制性原因之一。

## 6. 返回原任务本身也会产生晚层峰值

只有3条bounded样本同时具有可匹配的support-resume边界，分母很小。但在resume token，early/middle/late matched JSD contrasts为`-0.0013/+0.0323/+0.0749`；到+8 tokens则变为`-0.0138/-0.0016/-0.00004`。

这与“晚层检测语义状态切换”一致：从异常内容返回正常支持任务的瞬间，同样可能表现为强routing change。因而单纯的change magnitude无法知道切换方向是危险还是安全；它只能提出event candidate，后续仍需要方向或状态比较。

## 7. 对算法设计的含义

本次结果不授权直接选择L4、L7或L12作为最终模型。合理的下一假设是一个明确分工的两阶段算法：

1. 用晚层短窗口提出“发生语义/任务状态切换”的candidate；
2. 从candidate之后观察早—中层的持续模式，并与candidate之前的自身状态比较；
3. 允许后续出现第二个切换candidate，因为返回原任务也会产生晚层峰值；
4. 不再把“novelty下降”直接等同于抵御成功，而是判断下降后落入哪个状态或方向。

这是由当前B2结果提出的新proposal，必须在新计划中预注册，并在未使用的批次上评价。当前数据只能支持机制假设，不能支持选出的layer subset具有泛化性能。

## 8. 限制

- matched event-time只有15个engagement-onset和13个task-deviation-boundary样本；control输出较短是主要删失来源；
- recovery负类只有5条bounded-resisted；逐层区间未做16层同时推断校正；
- 行为边界来自人工证据span，task-deviation boundary更接近首段证据完成点，不具备单token因果精度；
- early-layer持续可能来自词汇、句法或输出风格，而非任务目标；
- 结果来自单一模型、单一Agent协议和已用于开发的B2 replay；
- 这是观察性routing分析，没有layer ablation、router steering或因果干预；
- B3保持未使用。

## 9. 产物与复现

执行命令：

```bash
.venv/bin/python scripts/analyze_agent_v2_layer_zoom.py --execute-posthoc-b2-analysis
```

产物：

- `artifacts/agent_v2/layer_zoom_b2_horizon384/result.json`
- `artifacts/agent_v2/layer_zoom_b2_horizon384/per_layer_summary.csv`
- `artifacts/agent_v2/layer_zoom_b2_horizon384/per_sample_delta.csv`
- `artifacts/agent_v2/layer_zoom_b2_horizon384/task_deviation_event_time.csv`
- `artifacts/agent_v2/layer_zoom_b2_horizon384/layer_zoom.png`

结果SHA-256：`9156d7dd689e4634beea23e498dc8c0ef1af4034bf6d5426a3bbc81171f24dcd`

图SHA-256：`b3e5c219787d1eddb7dfd4a5ef4614a7ffece27ee59f949aa2ace3abbec212a7`

输入完整性、240条replay cache和exact-prefix gate均通过；`b3_used=false`。
