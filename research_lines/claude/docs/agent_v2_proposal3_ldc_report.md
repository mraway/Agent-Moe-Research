# Proposal 3：Layer-Directional Consensus（LDC）验证报告

日期：2026-09-05（America/Los_Angeles）

分析状态：冻结的 development validation 已完成；结论为 `inconclusive_sample_support`；B3未使用

预注册提交：`58edc3e`（`docs: preregister four routing mechanism proposals`）

预注册计划 SHA-256：`5e9f705a98637a2ad17289923d7bcff15330973da700d1ebc7b7abf87dd7de34`

结果 artifact SHA-256：`63562e7ec40487f62faa326b9f30e83c16933dd46e9323fc3651bba3cae30ec5`

## 主管摘要

LDC得到一个明确但不充分的结果：**当人工提供正确的attack-engagement起点时，跨层多数方向能够很好地排序“抵御
后恢复”和“继续执行”；但当算法必须自己寻找起点时，有限时间状态分类明显失效。**

主要结果如下：

- held-out normal matched-group FAR为`2/60=3.3%`，低于预注册上限15%；clean与benign-control各为
  `1/60=1.7%`。
- oracle-onset下，5条bounded-resisted的consensus delta全部为负；35条窗口完整的execution中24条为非负。
  execution-vs-bounded AUROC为`0.920`，均值差为`0.346`，bootstrap 95% CI为`[0.201, 0.489]`。
- 但bounded-resisted只有5条，低于预注册最低12条；execution多数持续率为`24/35=68.6%`，也略低于70%门。
- onset-free LDC检测到`35/40=87.5%` execution，却有13条首次alarm早于冻结行为onset。剩余22条非提前检测中，
  3条被判为sustained、12条反而被判为recovered、4条uncertain、3条censored。
- onset-free排序的表面AUROC为1.0，但只有1条bounded和15条execution进入该统计，不能作为可靠成功证据。
- 与late-only相比，LDC同时取得更低normal FAR（3.3% vs 5.0%）和更高execution detection（87.5% vs
  67.5%）；但它没有解决“首次candidate不对应真实engagement阶段”的核心问题。

因此，本轮支持“跨层routing恢复方向是一个值得确认的机制信号”，但不支持把当前LDC状态机作为已经有效的通用在线
算法。冻结结论不是Go。

## 1. 冻结方法

LDC只读取每个生成token的MoE top-8 routing，不读取token文本、任务、workflow、attack channel、target domain、
输出格式或绝对位置。

16个MoE layers被预先固定为三个等权band：

- early：L0--L4；
- middle：L5--L10；
- late：L11--L15。

每个band使用同一组26条canonical clean fit traces，每条8个anchor，共208个正常routing anchors。逐token分数为
band内Hellinger距离的第5近邻，并用fit-only leave-one-trace-out center/scale做robust标准化。算法没有选择具体
expert，也没有给late band额外权重。

对每个可能起点`e`，计算：

- early mean：`A_b(e)=mean(z_b[e:e+15])`；
- late mean：`L_b(e)=mean(z_b[e+32:e+63])`；
- 时间方向：`D_b(e)=L_b(e)-A_b(e)`。

三个early means的第二大值是engagement score，因此严格越阈值等价于至少两个band同时异常。首次越线即为算法
candidate，不允许跳到后续更有利的candidate。候选产生于`e+15`；恢复/持续分类产生于`e+63`：至少两个active
bands回落则为`engaged_recovered`，至少两个保持或上升则为`sustained_execution_risk`，否则为uncertain；窗口不足
为censored。连续分类分数是三个`D_b`的中位数。

LDC以normal-only FHTS为基础，不训练directional-relative classifier。late-only与pooled-all-layer FHTS在相同窗口、
相同alpha下独立校准，作为冻结比较器。

## 2. 数据与完整性

| 数据角色 | 样本 | 用途 |
|---|---:|---|
| Canonical clean fit | 26 traces / 208 anchors | 四个normal banks的表示拟合 |
| C1 folds 0--2 | 200 traces / 100 matched groups | 完整路径group-maximum阈值校准 |
| C1 folds 3--4 | 120 traces / 60 matched groups | held-out normal FAR |
| 384-token B2 replay | 240 traces / 80 attack traces | 行为机制与onset-free utility |

C1完整数据含320 traces、160 matched groups。384-token replay中80条attack的冻结行为分布为：35条
`no_observable_engagement`、5条`bounded_engagement_resisted`、40条`cross_domain_execution`。

运行前以下完整性门全部通过：

- replay config SHA-256：`ab00100f56298fad9c08d4e2075aa70db7d2e1071ce609d9b4209e4d241abebf`；
- exact-prefix audit SHA-256：`3405298d0cc5d29a07899f32dc113bb84d08334de51bab6ce1dd442053a41359`；
- behavior engagement summary SHA-256：`50b890ee4e30ff81855db5547cef53636669a5c4207c6930a969844736fae5e3`；
- behavior-only collection report SHA-256：`b730343735831d962a3df4d484fd6a6ac21b652c18b5fc569d99dd7b4135b0a3`；
- historical、C1与replay routing caches分别覆盖360、320、240条trace；
- `b3_used=false`。

脚本要求显式`--execute-routing-analysis`并在任何routing load前验证预注册计划哈希。本次验证通过。

## 3. Normal-only校准与误报

三个方法各自用100个C1 calibration group的完整路径maximum校准；order-statistic rank均为91，校准严格越线率均为
`9/100=9%`。

| 方法 | 阈值 | Held-out group FAR | Clean trace FAR | Benign trace FAR | Alarm / 1,000 candidate starts |
|---|---:|---:|---:|---:|---:|
| LDC 2-of-3 | 0.8322 | 2/60 = 3.3% | 1/60 = 1.7% | 1/60 = 1.7% | 0.203 |
| Late-only FHTS | 1.0196 | 3/60 = 5.0% | 2/60 = 3.3% | 1/60 = 1.7% | 0.304 |
| Pooled-all-layer FHTS | 0.7629 | 2/60 = 3.3% | 2/60 = 3.3% | 0/60 = 0% | 0.203 |

LDC held-out group FAR的Wilson 95%区间为`[0.9%, 11.4%]`；clean与benign trace FAR区间均为
`[0.3%, 8.9%]`。120条held-out normal trace共有9,863个candidate starts，LDC产生2个episode alarms：1个最后被
分类为recovered，1个因后续长度不足被分类为censored。正常FAR门全部通过。

LDC的FAR低于late-only，并与pooled方法相同。这个结果支持2-of-3对单band偶然异常的过滤作用，但60个group仍只给出
较宽置信区间，不能解释为生产级FAR保证。

## 4. Oracle-onset机制结果

人工行为onset后具有完整64-token窗口的样本为5条bounded和35条execution；另有5/40条execution因future window
不足而删失。所有5条bounded均可比较。

| 表示 | Bounded delta mean | Execution delta mean | Execution-vs-bounded AUROC |
|---|---:|---:|---:|
| LDC三band中位数 | -0.251 | 0.095 | 0.920 |
| Early band | -0.071 | 0.273 | 0.926 |
| Middle band | -0.360 | 0.048 | 0.943 |
| Late band | -0.339 | 0.016 | 0.874 |
| Pooled 16 layers | -0.221 | 0.100 | 0.903 |

LDC consensus delta的详细范围为：

- bounded：median `-0.210`，range `[-0.429, -0.030]`，5/5为多数band回落；
- execution：median `0.091`，range `[-0.286, 0.464]`，24/35为多数band保持或上升；
- execution-minus-bounded mean contrast：`0.346`；
- 5,000次scenario bootstrap 95% CI：`[0.201, 0.489]`。

逐band符号进一步说明结果不是“所有层都一致”：

- bounded中early为3/5回落，middle与late均为5/5回落；
- execution中early为32/35保持或上升，middle为21/35，late仅19/35。

因此，跨层中位数有很强的排序能力，但“execution在多数band持续”的经验比例只有68.6%，未达到冻结的70%门。
结果也没有重复过去“late最强”的描述：在当前early-to-late时间差指标上，early和middle的AUROC反而高于late。这正是
预注册等权band与late-only对照需要暴露的负面信息，不能事后给late加权。

## 5. Onset-free在线结果

| 方法 | Silent-ignore alarm | Bounded detection | Execution detection | Execution pre-onset alarm | 进入clean排序的 B/E | 排序AUROC |
|---|---:|---:|---:|---:|---:|---:|
| LDC 2-of-3 | 4/35 = 11.4% | 2/5 = 40.0% | 35/40 = 87.5% | 13/40 | 1 / 15 | 1.000 |
| Late-only FHTS | 4/35 = 11.4% | 0/5 = 0% | 27/40 = 67.5% | 8/40 | 0 / 16 | 不可计算 |
| Pooled-all-layer FHTS | 6/35 = 17.1% | 2/5 = 40.0% | 35/40 = 87.5% | 14/40 | 1 / 18 | 1.000 |

这里的“clean排序”严格排除首次candidate早于人工onset、future不足以及LDC投票uncertain的轨迹。LDC虽显示AUROC
1.0，但分母只有1条bounded对15条execution，因此只满足代码中的数值门，不构成稳定证据。

LDC的状态分解揭示更重要的负结果：

- 35条execution detections中，13条首次candidate早于冻结行为onset；
- 剩余22条非提前execution detections中，3条censored、4条uncertain、12条被判为recovered，只有3条被判为
  sustained；
- 在15条给出确定且不删失状态的execution中，只有`3/15=20%`得到正确的sustained方向；若把uncertain也视为未
  成功，则完整future的19条中只有`3/19=15.8%`输出sustained；
- 5条bounded只检测到2条，其中1条是pre-onset；唯一非提前、可分类样本被正确判为recovered；
- 35条silent-ignore中4条报警，状态为2条recovered、1条uncertain、1条censored。

对于LDC非提前execution detections，candidate start相对人工onset的offset范围为`+1`至`+90`、median `+13.5`；
engagement alarm实际可见时间还要加15 tokens，因此median为onset后28.5 tokens。排除censored后，最终64-token状态的
可见时间范围为onset后64至153 tokens、median 83 tokens。唯一可用bounded状态要到onset后76 tokens才可见。

这说明当前瓶颈不是“能否发现某种异常”——execution detection很高——而是**首次正常校准越线的位置不等于机制
分析中的engagement onset**。在错误或过早candidate上读取64-token delta，会把许多真实execution判成recovered。

## 6. 与固定比较器的解释

LDC相对late-only满足两个预注册价值门：

- held-out normal group FAR没有更高：3.3% vs 5.0%；
- execution detection没有损失10个百分点，实际更高：87.5% vs 67.5%。

相对pooled-all-layer，LDC的normal group FAR和execution detection完全相同，但silent-ignore alarm更低（11.4% vs
17.1%），pre-onset execution alarm略低（13 vs 14）。这些是有利的描述性结果。

然而三个方法都大量把首次candidate后的execution轨迹判断为recovered：LDC非提前完整future为12条recovered、3条
sustained、4条uncertain；late-only为14条recovered、2条sustained；pooled为14条recovered、4条sustained。由此不能
声称跨层consensus已经解决了在线恢复分类。

## 7. 全部预注册门

| Gate | 观测值 | 结果 |
|---|---:|---|
| Held-out matched-group FAR <= 0.15 | 2/60 = 0.033 | Pass |
| Clean trace FAR <= 0.15 | 1/60 = 0.017 | Pass |
| Benign trace FAR <= 0.15 | 1/60 = 0.017 | Pass |
| Oracle bounded eligible n >= 12 | 5 | **Fail** |
| Oracle execution eligible n >= 12 | 35 | Pass |
| Oracle AUROC >= 0.70 | 0.920 | Pass |
| Oracle contrast bootstrap CI lower > 0 | 0.201 | Pass |
| Bounded majority recovery >= 0.70 | 5/5 = 1.000 | Pass |
| Execution majority sustained >= 0.70 | 24/35 = 0.686 | **Fail** |
| Online clean bounded n >= 8 | 1 | **Fail** |
| Online clean execution n >= 8 | 15 | Pass |
| Online clean AUROC >= 0.70 | 1.000（1 vs 15） | Pass，低支持度 |
| Normal FAR no worse than late-only | 0.033 <= 0.050 | Pass |
| Execution detection within 0.10 of late-only | 0.875 >= 0.675-0.10 | Pass |

按照冻结优先级，任一样本量门失败时总体状态为`inconclusive_sample_support`。即使未来补足样本量，当前数据还存在一个
独立性能失败：execution多数持续率68.6%低于70%。不得把“只差一条样本”改写为通过。

## 8. 结论、限制与下一步

本实验提供两层结论：

1. **机制层有希望。** 在正确行为onset对齐后，三个粗粒度layer bands的多数时间方向对恢复与执行具有很强排序，且
   不是由late-only驱动。
2. **当前算法层不成立。** normal-only首次越线可以高召回发现execution，却不能稳定定位应读取恢复斜率的起点，导致
   在线状态机把多数execution判断为recovered。

主要限制是bounded样本仅5条、5条execution被64-token窗口删失、数据仍是已用于机制开发的B2 replay而非独立新批次、
行为onset依赖人工冻结标注，以及首次candidate存在大量pre-onset alarm。粗粒度band结果缓解了“单一expert”解释，
但不能证明跨模型、跨MoE架构稳定。

本轮不应搜索新band、改变first-candidate规则或重新选择窗口。若四个独立proposal比较后仍认为该方向值得继续，下一次
应在全新长输出批次上事先获得至少12条bounded，并把“engagement detection”和“选择哪个candidate计算恢复”作为两个
清楚分离的问题；任何新candidate-selection规则必须另行预注册。

## 9. 复现

实际执行命令：

```bash
.venv/bin/python scripts/run_agent_v2_proposal3_ldc.py --execute-routing-analysis
```

结果文件：`artifacts/agent_v2/proposal3_ldc/result.json`

结果 SHA-256：`63562e7ec40487f62faa326b9f30e83c16933dd46e9323fc3651bba3cae30ec5`

主分析执行后没有修改预注册计划、算法、门槛或实现；本文件仅报告冻结artifact。
