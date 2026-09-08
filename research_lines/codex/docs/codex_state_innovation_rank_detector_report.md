# Codex State–Innovation Rank Detector：实验报告

日期：2026-09-05（America/Los_Angeles）

状态：冻结的 SIRD-v1 为 `development_no_go`；失败原因是有限经验秩在完整路径极值下饱和，而不是 raw routing
distance 没有信号；B3 未使用

预注册计划：[SIRD 计划](codex_state_innovation_rank_detector_plan.md)

## 给研究主管的结论

本轮检验了一个简单的双通道算法：用8-token当前 block 到正常 routing manifold 的距离表示 `state novelty`，用相邻
两个完全不重叠的8-token blocks 之间的有符号变化到正常 innovation manifold 的距离表示 `transition novelty`；
两者各自转为 normal-bank 经验秩后取最大值。

冻结版本必须判失败，而且失败得非常彻底：primary SIRD 在 C1、384-token routine 和全部80条 attack 上均为0次报警。
这不是一个“零误报的好 detector”，而是 calibration degeneracy。632个正常 anchors 使单 endpoint 的经验 p 值最小只能
达到 `1/633`，对应 score 上限 `log(633)=6.45047`。60个 calibration groups 中有21组的完整路径最大值已经撞到这个
上限，因此预注册的 `alpha=.05/.10/.20` 三个 SIRD 阈值全部等于上限；严格 `>` 规则使任何样本都不可能越线。

冻结结果之后进行的、明确标为 post-hoc 的 raw-distance 审计表明，底层 routing 表示并没有失败：

| 指标 | Post-hoc raw 双 head（每head α=.05） |
|---|---:|
| C1 calibration group alarm | 3/60 = 5.0% |
| C1 held-out group alarm | 3/60 = 5.0% |
| C1 held-out clean / benign | 2/60 = 3.3% / 1/60 = 1.7% |
| 384-token replay clean / benign | 4/80 = 5.0% / 6/80 = 7.5% |
| 384-token routine group union | 10/80 = 12.5% |
| Silent attack | 2/35 = 5.7% |
| Bounded engagement | 4/5 = 80.0% |
| Cross-domain execution | 37/40 = 92.5% |

这条 raw 规则会数值上通过本轮所有 development gates，但它是在 target 已经被观察后提出的 alternative statistic，
不能用来“修正”SIRD-v1，也不能视为独立验证。它只能支持一个下一阶段判断：**把正常校准放在 raw head 的完整路径
极值上，可能比先做有限 anchor-rank、再校准路径极值更合适。**

机制上，当前 state head 明显占主导。raw state path maximum 对 engaged-vs-routine 的 AUROC 为 `0.980`，raw signed
innovation 为 `0.913`；raw state 单独已经获得与双 head union 相同的终局检出和误报集合。innovation 单独只检出
`13/40` execution 和 `1/5` bounded，但在41条 raw-union engaged alarms 中，有4条由 innovation 先于 state 越线。
因此当前数据支持“innovation 可能改善首报时刻”，不支持“innovation 提高最终检出覆盖”。该时序价值要等独立共识
onset 后才能判断。

## 1. 实验纪律

执行顺序为：

1. 预注册计划先以 commit `8c8abe0` 冻结；
2. 实现、plan-hash execution lock 与8个定向测试以 commit `03e5e1c` 冻结；
3. 254项全量 `unittest` 回归通过；
4. 使用显式 `--execute-routing-analysis` 运行一次 proposal-specific analysis；
5. 冻结结果判为 no-go 后，才读取已保存的 raw scores 做 rank-saturation post-hoc audit。

SIRD scorer 没有读取人工 onset。Replay outcome 只使用不含 onset 的 routing-blind engagement adjudication；全部分数和
首次预测先产生，之后才按 outcome class 汇总。B3 没有生成、读取或用于任何决策。

数据角色按计划保持：

| Role | C1 folds | Groups | Traces | 用途 |
|---|---:|---:|---:|---|
| `N_fit` | 0 | 40 | 80 | state / innovation bank 与 marginal counts |
| `N_cal` | 1--2 | 60 | 120 | matched-group完整路径阈值 |
| `N_eval` | 3--4 | 60 | 120 | 未见 content-family 正常风险 |
| Replay | — | 80 triplets | 240 | 384-token routine stress + outcome utility |

C1 全部320条轨迹均有至少17个最终生成 token，因此不存在16-token算法的机会删失。实际 replay 最终生成长度为
17--384 tokens，中位数92.5；没有按长度截断。

## 2. 冻结算法与 bank

每个 token 每层的top-8专家先在8-token block中形成平方根频率向量。当前 state 与前一不重叠 block 的有符号差分别
进入两个第5近邻 normal banks。所有16层等权；没有选择 layer、expert、domain 或输出格式。

`N_fit` 产生632个对齐 anchors，覆盖80条 source traces。bank资源为：

| 项目 | 数值 |
|---|---:|
| State + innovation elements | 1,294,336 |
| float32 理论存储 | 5,177,344 bytes（4.94 MiB） |
| float16 理论存储 | 2,588,672 bytes（2.47 MiB） |
| 每 endpoint 的 layer-level distance cells | 20,224 |
| 等价64维距离坐标处理量 | 约1.29M values / endpoint |

整次运行在当前实现记录为CPU路径，完成48,395个 endpoints，wall time `2.05s`；约23.6k endpoints/s。该数字来自
整条轨迹矩阵化批处理，不能直接当作生产 singleton token latency，后续仍需独立流式 benchmark。

## 3. Primary 结果：经验秩饱和导致 detector 退化

两个 head 对632个 leave-one-trace-out reference raw scores 计算 tie-conservative upper-tail rank：

```text
p(a) = (1 + #{reference raw >= a}) / 633
Z(a) = -log p(a)
```

所以任意 head 都满足 `Z <= 6.4504704475`。完整路径只要出现一个比全部 reference raw scores 更大的 endpoint，就会
精确落在同一个 cap，而不会保留“超过参考最大值多少”的信息。

在60个 calibration group maxima 中：

| Head | 撞到 rank cap 的 groups | α=.10 阈值 | 严格 calibration exceedances |
|---|---:|---:|---:|
| State | 10/60 | 6.45047 | 0 |
| Innovation | 14/60 | 6.45047 | 0 |
| SIRD max-union | 21/60 | 6.45047 | 0 |

对 SIRD 而言，21个 ties 已覆盖 `.20` 阈值所需的上尾，因此 `.05/.10/.20` 三个 operating points 全部退化为相同的
最大值。报警又按预注册使用严格 `>`，故 C1留出、replay routine、silent、bounded和execution均为0。

把规则临时改成 `>=` 也不是合法或有效修补：它至少会令 calibration 中21/60（35%）的 groups 报警，远超10%的目标。
真正的问题是有限 rank 在进入长路径 maximum 之前已经丢失了上尾分辨率。

Replay 进一步显示这不是少数点现象。SIRD path maximum 撞 cap 的比例为：

| Cohort | Saturated paths |
|---|---:|
| Routine clean + benign | 38/160 = 23.8% |
| Silent attack | 10/35 = 28.6% |
| Bounded engagement | 4/5 = 80.0% |
| Execution | 39/40 = 97.5% |

饱和比例本身仍有排序信息，但大量 ties 将 engaged-vs-routine AUROC压到`0.873`，且无法形成预定 operating point。

## 4. Baselines

Primary `alpha=.10` 下：

| Method | C1 held-out group FAR | Replay clean FAR | Replay benign FAR | Routine group FAR | Silent | Bounded | Execution |
|---|---:|---:|---:|---:|---:|---:|---:|
| SIRD | 0/60 | 0/80 | 0/80 | 0/80 | 0/35 | 0/5 | 0/40 |
| State-only rank | 0/60 | 0/80 | 0/80 | 0/80 | 0/35 | 0/5 | 0/40 |
| Innovation-only rank | 0/60 | 0/80 | 0/80 | 0/80 | 0/35 | 0/5 | 0/40 |
| Marginal surprisal8 | 6/60 | 5/80 | 14/80 | 18/80 | 4/35 | 3/5 | 36/40 |
| Literal unseen8 | 0/60 | 0/80 | 0/80 | 0/80 | 0/35 | 0/5 | 0/40 |

Marginal surprisal 再次表现为强而不充分：execution `90%`、bounded `60%`，但 replay benign FAR `17.5%` 和 routine
group FAR `22.5%` 均越过预注册门。扩大 C1 normal fit 后 literal unseen 没有检出任何 cohort，说明“正常专家库中
从未出现的专家”在这个更有覆盖度的参考下不再是可用主信号。

## 5. Post-hoc raw-distance审计

本节只回答失败诊断，不改变 primary status。审计没有重新读取 routing，只读取冻结 artifact 中已经保存的 raw state
和 innovation distances。

探索性规则是对每个 raw head 的 `N_cal` matched-group path maxima 分别用 `alpha_head=.05` 校准，再取两个严格越线
的 union；Bonferroni nominal episodic alpha 为0.10。阈值为：

- state raw：`0.5665370`；
- signed innovation raw：`0.5513903`。

它在 calibration union 上为3/60，C1留出也是3/60；384-token replay的完整结果见摘要表。Raw-vs-rank 的
engaged-vs-routine path-max AUROC为：

| Evidence | Raw distance | 经验秩后 |
|---|---:|---:|
| State | 0.980 | 0.928 |
| Signed innovation | 0.913 | 0.869 |
| Max-union | — | 0.873 |
| Marginal surprisal | 0.950 | — |

这支持两个机制判断：

1. **联合 state geometry 有明显的 marginal 之外信息。** Raw state 在更低 normal risk 下，排序和检出均高于
   marginal surprisal；它衡量的是同一个正常 anchor 是否能同时解释全部层的 routing configuration。
2. **signed innovation 是高特异但覆盖较窄的信号。** 单独使用时，replay clean为0/80、benign为1/80、silent为
   0/35，但只覆盖1/5 bounded和13/40 execution。

Raw state 单独已经覆盖4/5 bounded和37/40 execution，并产生与union相同的终局报警集合。Union的41条 engaged
first alarms中，36条先由state越线、4条先由innovation越线、1条同时越线。因此 temporal head 的当前价值是潜在
提前报警，而不是增加最终覆盖；没有共识 onset 时不能把这个绝对 endpoint提前解释为有效 latency gain。

## 6. State classification 与 onset

冻结 SIRD 没有任何首次报警，所以预注册的 `+8` independent-block recovering/sustained 分类全部落在
`no_deviation_alarm`，没有可解释价值。

本轮严格没有运行 onset metrics。Reviewer-B/共识 E-C-X 标定完成后，冻结 SIRD-v1 因0报警也不会产生有用 timing
结果。若要判断 raw innovation 提前的4条样本是否真的靠近异常开始，应作为显式 post-hoc机制分析或未来 SIRD-v2 的
独立确认指标，不能把旧 goal boundary 或 Reviewer-A 单标替代共识。

## 7. 对算法研究的更新

### 7.1 被否定的元素

否定的是：**先用有限 normal anchors 将每个 endpoint 压成有硬上限的经验秩，再对长路径 maximum 定阈值。** Bank
有数百个 anchors 仍然不够，因为问题不只是 `1/(m+1)` 分辨率；所有超出 reference maximum 的 raw distances 都被
映射到同一个值，路径越长越容易饱和。

### 7.2 保留的元素

- 8-token joint state第5近邻 raw distance：当前最强、最简单的主候选；
- 相邻不重叠 block 的 signed innovation raw distance：保留为可能改善 onset timing 的辅助head；
- normal-only、matched-group、完整路径 calibration：保留；
- marginal surprisal与unseen支持：继续作为必要 baseline，不作为主算法；
- 输出连续风险并由上层融合：保持不变。

### 7.3 下一 proposal 的最小形式

如果继续 Codex 路线，最小 SIRD-v2 应直接在每个 raw head 的正常完整路径 maximum 上校准阈值，并用预先分配的
`alpha/2` 做两head union；不再做 endpoint anchor-rank fusion，也不增加layer、expert、文本或任务特征。当前
post-hoc结果说明这条形式值得冻结验证，但不能作为其验证证据。

在进入B3之前仍需：

1. 完成独立 Reviewer-B/共识 onset，判断innovation是否真正改善首报时序；
2. 与 Claude 独立方案做统一比较，确认是否值得保留第二个head；
3. 为384-token或更长episode准备独立长输出normal calibration，而不是只依靠192-token C1外推；
4. 冻结最终算法后才允许一次B3 confirmation。

## 8. 局限

- 所有数据都是已多次观察过的 development data；
- post-hoc raw规则直接受到本轮失败和target结果启发，证据地位低于预注册实验；
- C1正常episode最多192 tokens，384-token replay虽提供压力测试，但没有独立长输出normal留出；
- 只有一个固定Agent、一个MoE checkpoint和final-generation episode；
- bounded仅5条，不能确认recovery机制；
- 未与文本、hidden-state或强外部Agent-state baseline比较；
- vectorized离线吞吐不能替代生产逐token延迟测量。

## 9. 产物与复现

冻结分析：

```bash
.venv/bin/python scripts/run_codex_sird.py --execute-routing-analysis
```

Post-hoc saturation audit：

```bash
.venv/bin/python scripts/audit_codex_sird_rank_saturation.py
```

产物：

- primary result：`artifacts/agent_v2/codex_sird/result.json`，13,712,330 bytes，SHA-256
  `70c9c455950d93f7cbfa4aa36ca6c1b3a516260b863be809913b4c16b9ac854d`；
- post-hoc audit：`artifacts/agent_v2/codex_sird/posthoc_rank_saturation_audit.json`，8,548 bytes，SHA-256
  `d74e8abda66f907c95aa18b7e0fd4cb1e0367c3e5d5504267a0e2ca3e8255245`；
- plan SHA-256：`3711f3bf39efcc2db728cc7779e474331fdefe9ca1e2cc16f18cee99eb17a35d`；
- 运行前全量测试：254/254通过；
- B3 used：false；onset fields read：false。
