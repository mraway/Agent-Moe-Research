# Proposal 2：Directional Relative Recovery（DRR）实验报告

日期：2026-09-05（America/Los_Angeles）

状态：冻结分析已完成；**No-go**；adaptive development evidence；B3未使用

预注册commit：58edc3e

结果artifact SHA-256：ae426df3c1a7564e40703fbf06b4a71b4eaa3930f116600bcd68afddc00439ee

## 主管摘要

DRR验证了一个重要但有限的结果：B1学习到的routing方向配合trace-relative基线，可以在可控的初级误报下发现一部分跨域执行；但把“第一次越线后的routing是否回落”直接作为最终执行判据并不成立。

在384-token B2 replay中，DRR让27/40条真实execution发生过越线，16/40在行为onset后8 token内越线。clean与benign合计17/160发生越线，初级FAR为10.625%。这说明directional-relative分数仍具有早期任务迁移信号。

但是，固定的64-token恢复规则把21条具有完整后续窗口的execution中的17条判成recovered，仅4条判成sustained risk。最终execution召回只有4/40（10%）。相对地，160条clean/benign没有一条被判成sustained risk。第二阶段获得零控制误报的原因，不是准确地区分了抵御与执行，而是它同时压掉了绝大多数真实执行。

在算法自行选择first crossing后，execution与bounded-resisted的delta AUROC为0.690，低于0.75门槛；均值差95% bootstrap CI为[-0.225, 0.755]，包含零。5条bounded中只有2条被算法越线并获得完整窗口，也未满足至少4条的覆盖门。11项冻结检查中6项通过、5项失败，因此结论为No-go。

## 1. 方法

### 1.1 输入和训练

算法在线打分只使用每个decode token的top-8 expert selection，不使用token文本、token ID、任务类型、workflow、attack channel或绝对位置特征。

唯一训练与阈值来源是B1中response-brief-absent的120条trace：

- execution：24条；
- non-execution：96条；
- 8-token route-selection state：16层乘64 experts，共1,024维；
- positive anchors：每条execution的行为onset +7、+15、+23，共72个；
- negative anchors：non-execution全程与execution onset前，共234个；
- ridge penalty固定为1,024，不搜索超参数。

源域每条non-execution使用完整relative路径maximum，每条execution只使用行为onset前的relative路径maximum校准阈值。114条可用negative segment按alpha=0.10的有限样本order statistic给出固定阈值0.928706；其中10/114 source segment严格越线，校准exceedance rate为8.77%。

### 1.2 在线决策

对8-token state的ridge score，减去同一trace内所有非重叠历史state score的中位数：

    r_t = s_t - median({s_j : j <= t - 8})

算法锁定完整路径上第一次满足 r_t > 0.928706 的endpoint e，不允许重置或选择后续更高峰。随后计算：

    early = mean(r[e : e+15])
    late  = mean(r[e+32 : e+63])
    delta = late - early

- 无越线：no detected excursion；
- 越线但无法完整观察到e+63：censored；
- delta < 0：engaged/recovered；
- delta >= 0：sustained execution risk。

这意味着最终判定至少需要first crossing后的64 token。零点分类边界、所有窗口和阈值均在解盲前冻结。

### 1.3 Target和行为分母

Target为完整384-token B2 exact replay，共240条、80个scenario：

- clean：80；
- benign control：80；
- attack execution：40；
- attack bounded-engagement resisted：5；
- attack silent-ignore：35。

主结果使用全部240条。条件delta AUROC只使用“已经越线且first crossing后有完整64-token趋势窗口”的execution与bounded样本；没有越线和删失样本仍作为端到端execution检出的失败计入40条总分母。

## 2. 完整性与盲态审计

- 预注册文档SHA-256为1a267a299e89b49924083178a69b716346c75faa18515970b921b76e1c1a2144，与脚本内fail-closed锁一致；
- B1 sample index SHA-256为f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1；
- target sample index SHA-256为5399b0b32769c8ef98722080da9ef1f159f586fba6d1d907656afcc8e34eac10；
- target prefix audit SHA-256为3405298d0cc5d29a07899f32dc113bb84d08334de51bab6ce1dd442053a41359；
- routing-blind behavior annotation SHA-256为8ad05186bbe7e508f3019a8c91aa71df8e369922bcdd4562b367bd7f298839d7；
- B1原始240/240与target 240/240 routing validation通过；
- target三臂均为80条，attack行为标签40/5/35与冻结报告一致；
- target标签只在routing分数、阈值、first crossing和delta生成后合并；
- B3 used = false。

实际执行命令：

    .venv/bin/python scripts/analyze_proposal_drr.py --execute-routing-analysis

命令正常退出，exit code为0。

## 3. 主结果

### 3.1 控制组风险

| 指标 | 计数 | 比率 | Wilson 95% CI |
|---|---:|---:|---:|
| clean+benign任意越线 | 17/160 | 10.625% | [6.74%, 16.36%] |
| clean+benign最终sustained risk | 0/160 | 0% | [0%, 2.34%] |
| clean任意越线 | 7/80 | 8.75% | [4.30%, 16.98%] |
| benign任意越线 | 10/80 | 12.50% | [6.93%, 21.50%] |

控制组每1,000个eligible relative endpoints出现1.390次crossing onset。17条越线控制中，7条具有完整窗口且全部被判为recovered，另10条因输出结束而删失；没有控制被判成最终sustained risk。

### 3.2 Execution检出与时延

| 指标 | 计数 | 比率 | Wilson 95% CI |
|---|---:|---:|---:|
| 任意越线recall | 27/40 | 67.50% | [52.02%, 79.92%] |
| 最终sustained-risk recall | 4/40 | 10.00% | [3.96%, 23.05%] |
| 完整64-token趋势窗口 | 21/40 | 52.50% | [37.50%, 67.06%] |
| 行为onset前首次越线 | 5/40 | 12.50% | [5.46%, 26.11%] |
| onset后+8 clean recall | 16/40 | 40.00% | [26.35%, 55.40%] |
| onset后+16 clean recall | 18/40 | 45.00% | [30.71%, 60.17%] |
| onset后+32 clean recall | 21/40 | 52.50% | [37.50%, 67.06%] |
| onset后完整输出clean recall | 22/40 | 55.00% | [39.83%, 69.29%] |

22条onset后首次越线的命中样本中，latency中位数为4 token、均值20.77、范围1到320。320-token极端值说明只报告命中样本的中位延迟会掩盖严重的晚检。

40条execution的最终状态为：

| 状态 | 数量 |
|---|---:|
| 无越线 | 13 |
| 越线后删失 | 6 |
| 被判recovered | 17 |
| 被判sustained risk | 4 |

从27条初级命中到4条最终命中，是本方案失败的主要来源。

### 3.3 Resisted细分和删失

| 行为类 | 总数 | 任意越线 | 完整趋势窗口 | recovered | sustained | censored | 无越线 |
|---|---:|---:|---:|---:|---:|---:|---:|
| bounded resisted | 5 | 2 | 2 | 2 | 0 | 0 | 3 |
| silent ignore | 35 | 4 | 3 | 2 | 1 | 1 | 31 |
| clean | 80 | 7 | 4 | 4 | 0 | 3 | 73 |
| benign | 80 | 10 | 3 | 3 | 0 | 7 | 70 |
| execution | 40 | 27 | 21 | 17 | 4 | 6 | 13 |

全体240条的状态为190条无越线、28条recovered、17条censored、5条sustained risk。17条删失由6条execution、3条clean、7条benign和1条silent-ignore构成。

输出长度差异明显：execution的token数中位数268、范围39--384；bounded中位数240、范围144--266；silent-ignore中位数100、范围20--384；routine control中位数74.5、范围17--384。固定64-token决策等待因此对短输出有结构性覆盖限制。

### 3.4 Recovery / execution分叉

条件比较只有21条execution和2条bounded：

| 指标 | 结果 |
|---|---:|
| execution delta均值 | -0.440 |
| bounded delta均值 | -0.704 |
| execution减bounded均值差 | 0.264 |
| delta AUROC | 0.690 |
| 5,000次scenario bootstrap 95% CI | [-0.225, 0.755] |
| delta >= 0的execution sensitivity | 4/21 = 19.05% |
| delta < 0的bounded specificity | 2/2 = 100% |
| 零点分类balanced accuracy | 59.52% |

方向上execution的delta比bounded略高，但两类绝大多数都低于零。oracle onset下曾观察到的恢复分叉，在算法自行锁定first crossing后明显减弱，且不确定性区间跨零。

## 4. Leave-one-family-out审计

每个fold从B1训练与阈值校准中移除对应family的全部scenario，再评价target同family；八折合并后每条target只被out-of-family模型评分一次。

### 4.1 合并结果

| 指标 | LOFO结果 |
|---|---:|
| control任意越线FAR | 17/160 = 10.625% |
| control sustained-risk FAR | 1/160 = 0.625% |
| execution任意越线recall | 23/40 = 57.50% |
| execution sustained-risk recall | 6/40 = 15.00% |
| execution完整趋势窗口 | 19/40 = 47.50% |
| bounded任意越线并有完整窗口 | 2/5 = 40.00% |
| delta AUROC（19 execution / 2 bounded） | 0.658 |
| execution-minus-bounded delta均值差 | 0.216 |
| delta bootstrap 95% CI | [-0.338, 0.779] |
| 零点balanced accuracy | 65.79% |

LOFO的初级越线recall仍为57.5%，但最终sustained-risk recall只有15%，继续表明问题在恢复分类而不只在初级方向迁移。

### 4.2 Family诊断

| held-out family | execution：越线/总数 | execution：sustained/总数 | control：越线/总数 | control：sustained/总数 |
|---|---:|---:|---:|---:|
| cooking | 4/6 | 0/6 | 2/20 | 0/20 |
| fiction | 4/5 | 3/5 | 5/20 | 0/20 |
| general knowledge | 1/1 | 0/1 | 0/20 | 0/20 |
| legal analysis | 0/4 | 0/4 | 0/20 | 0/20 |
| mathematics | 6/8 | 3/8 | 4/20 | 1/20 |
| poetry | 5/5 | 0/5 | 3/20 | 0/20 |
| programming | 0/6 | 0/6 | 1/20 | 0/20 |
| travel planning | 3/5 | 0/5 | 2/20 | 0/20 |

最终6条LOFO true positive全部来自fiction和mathematics。legal与programming没有一条execution越线；cooking、general knowledge、poetry和travel虽有初级命中，却没有最终sustained判断。因而该方案不能解释为family-independent执行检测器。

## 5. 预注册Go / No-go门

| 冻结检查 | 门槛 | 结果 | 通过 |
|---|---:|---:|---|
| control any-crossing FAR | <=15% | 10.625% | 是 |
| control sustained-risk FAR | <=10% | 0% | 是 |
| execution any-crossing recall | >=40% | 67.50% | 是 |
| execution端到端sustained recall | >=30% | 10.00% | **否** |
| bounded完整趋势窗口 | >=4/5 | 2/5 | **否** |
| 条件delta AUROC | >=0.75 | 0.690 | **否** |
| delta contrast bootstrap CI下界 | >0 | -0.225 | **否** |
| 零点balanced accuracy | >=0.70 | 0.595 | **否** |
| LOFO control sustained FAR | <=15% | 0.625% | 是 |
| LOFO execution sustained recall | >=25% | 15.00% | **否** |
| LOFO delta AUROC（适用时） | >=0.65 | 0.658 | 是 |

实现输出的逐项gate字典把第7项LOFO复合门拆为三项，因此共有11个boolean：6项通过、5项失败。按预注册规则，任一主门失败即No-go；本轮最终decision为**no_go**。

## 6. 负结果的解释

本结果不支持以下强命题：

- “first crossing后回落”可以直接等同于成功抵御；
- execution在first crossing后的32--63 token中通常维持正向相对位移；
- oracle行为onset下的delta分叉可以无损迁移到算法检测onset；
- B1学到的单一execution方向已经跨family稳定泛化；
- 64-token等待只降低时效、不会降低覆盖。

更窄、仍被支持的观察是：directional-relative初级分数确实能以约10.6%的control path FAR覆盖67.5%的execution，并且40%的execution可在onset后8 token内首次越线。但这个初级信号本身不能可靠判断“抵御还是继续执行”。

一种与数据相符但尚未被本实验单独证明的解释是：第一次越线经常不是需要分类的实质execution起点。5/40条execution甚至在人工execution onset前已经锁定first crossing；锁定后不允许重置，后续64-token窗口可能记录早期波动的消退，而不是跨域执行的持续性。该解释不能用于回改本轮规则，只能作为未来不同proposal的假设。

## 7. 局限

1. B1与B2均已被多轮观察，本轮只能作为adaptive development evidence；
2. bounded-resisted只有5条，且DRR只覆盖其中2条，条件AUROC的负类分母极小；
3. 384 token仍有15条length stop；64-token等待造成17条越线轨迹删失；
4. clean/benign比execution短，趋势窗口覆盖受自然输出长度影响；
5. delta比较以“已经越线且完整观察”为条件，不能替代40条execution的端到端性能；
6. source和target共享八个高层family，主模型并非未知family验证；LOFO虽更严格，但各family样本量仍小且表现高度不均；
7. 本实验只检验固定的8-token ridge、历史中位数、first crossing锁定和零点delta实例。No-go不一般性否定directional routing或其他轨迹模型；
8. 没有文本基线，因此不能声称该初级信号为routing独有；
9. B3仍保留，不能因本轮失败而用B3调参。

## 8. 结论与处置

Proposal 2不进入B3确认候选。应保留的机制线索是“方向性、trace-relative routing有早期任务迁移信号”；应淘汰的是“锁定第一次越线后，用固定delta符号把恢复与执行二分”的具体组合。

按冻结协议，本报告不提出或测试同target上的窗口、阈值、重置或分类边界修补。任何后续变体必须作为新的proposal重新预注册，并与本No-go结果并列保存。
