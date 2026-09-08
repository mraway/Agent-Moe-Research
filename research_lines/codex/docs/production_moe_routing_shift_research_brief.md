# 生产级 Agent MoE 路由任务偏移检测：研究目标与算法设计 Brief

## 1. 研究目标

本研究希望设计一种可以部署在生产级 Agent 服务中的 MoE 路由异常检测算法。
它持续观察模型生成过程中的 routing trajectory，并输出一个关于 Agent 是否进入
非常规、偏离 routine 工作状态的在线风险信号。

这个信号是生产安全系统的多个输入之一，而不是唯一决策依据。上层系统可以将它与
任务进度、工具调用、权限边界、输出行为及其他安全信号融合，再决定是否记录、提高
监控等级、请求确认、限制工具或中止任务。

本研究的首要目标不是直接预测最终任务是否失败，而是：

> 实时、低误报地发现 Agent 是否进入了偏离 routine 工作流的内部计算状态，并进一步
> 判断这种偏移正在恢复、持续，还是证据不足。

抵御成功的攻击也可能产生真实的思考偏移。因此，“是否发生非常规思考”和“偏移最终
是否导致任务执行”应当作为两个不同的问题处理：

1. **Deviation detection**：是否出现了非常规路由状态？
2. **Outcome/state classification**：该状态是 transient/recovering、sustained，还是
   uncertain？

## 2. 统一的数学视角

设第 `t` 个输出 token 的路由状态为

\[
R_t=(R_{1,t},R_{2,t},\ldots,R_{L,t}),
\]

其中 `R_{l,t}` 表示第 `l` 层的 top-k 专家集合、选择频率或完整 router probability。
Routine Agent 工作定义正常的联合时间分布

\[
P_0(R_t\mid R_{<t}).
\]

当前因果窗口形成观测分布或状态表示 `Q_t`。统一的检测目标可以写成

\[
S_t=D\!\left(Q_t\,\|\,P_0\right),
\]

或者写成正常模型下的 conditional surprisal：

\[
S_t=-\log P_0(R_t\mid R_{<t}).
\]

我们此前讨论的三类现象不是三个互不相关的机制，而是同一个 routing distribution
shift 在不同统计分辨率下的表现。

### 2.1 支持集扩张

当前轨迹选择了正常参考中未出现过的 layer--expert pair：

\[
P_0(E=e\mid L=l)=0,\qquad Q(E=e\mid L=l)>0.
\]

它是最极端的边际分布偏移。在未经平滑的 KL divergence 或 surprisal 下，该事件具有
无穷大代价；有限样本中应使用平滑或显式 support indicator。

### 2.2 专家边际频率重排

所有专家都曾在 routine 中出现，但当前选择频率发生变化：

\[
Q(E\mid L=l)\neq P_0(E\mid L=l).
\]

它可以通过 smoothed surprisal、KL、JS、Hellinger 或其他分布距离处理。

### 2.3 支持集内部的联合分布和动力学变化

各专家的边际频率可能仍然正常，但跨层组合、同层组合或出现顺序不正常：

\[
Q(R_{1,t},\ldots,R_{L,t})
\neq
P_0(R_{1,t},\ldots,R_{L,t}),
\]

或者

\[
Q(R_t\mid R_{<t})\neq P_0(R_t\mid R_{<t}).
\]

这部分包含 onset spike、持续偏移、恢复和二次偏移。

因此，三者在理论上可以统一为“相对于 routine joint temporal distribution 的偏离”。
但完整联合状态空间极大，不可能在当前样本规模下直接精确估计。算法研究的核心问题是：

> 如何用简单、低维、样本高效的表示，近似正常 routing 的联合时间分布，同时保留
> 异常检测所需的信息？

## 3. 生产级算法的设计目标

### 3.1 在线因果性

- 在 token 生成过程中增量更新，只使用当前和历史 routing；
- 不使用未来 token、最终答案或最终行为标签；
- 明确报告首次报警时刻、持续时间和状态变化；
- 允许偏移后恢复，也允许恢复后再次偏移。

### 3.2 可控且可解释的误报率

- 同时控制 trace-level FAR、matched-group FAR 和单位 token/window 报警率；
- 分别报告 clean 与 benign-but-unusual control 的误报；
- 阈值应支持 time-uniform、anytime-valid 或其他对检测次数进行修正的校准；
- 不能因为输出变长就不可控地累积误报；
- 应报告多个 operating point，而不是只报告一个经过挑选的阈值。

### 3.3 及时检出

主要时序指标包括：

- onset 后 8、16、32、64 tokens 内的 clean detection recall；
- first-alarm latency 分布；
- onset 前 first-alarm rate；
- recovery/sustained state 的最早可靠判定时间。

整条轨迹结束后的 AUROC 或 any-alarm recall 只能作为 screening 指标，不能替代在线
检测指标。

### 3.4 跨任务通用性

- 不依赖某一种输出格式、任务模板、工具类型、语言或 domain 的专用规则；
- 不使用 JSON、代码、SQL 等输出形式的 specialized feature；
- 不将 workflow、attack channel 或 domain identity 作为必要输入；
- 应在未参与拟合的正常工作流、良性非常规输入和攻击机制上分别评估；
- 模型或 router 版本改变后允许重新拟合正常参考，但算法形式不应随任务改变。

### 3.5 低开销和可扩展性

- 首选仅使用推理过程中已经产生的 top-k expert IDs 或 router probabilities；
- 单 token 更新应为固定或近似固定成本，避免随历史长度线性增长；
- 正常参考存储和 nearest-neighbor bank 应有明确上界；
- 给出时间复杂度、内存复杂度和生产延迟估计；
- 支持 batching、流式状态保存、版本化和回滚。

### 3.6 可校准、可融合，而不是自行裁决

检测器应输出连续风险及证据状态，而不是只给一个最终二元结论。至少应能够表达：

- 当前 deviation score；
- routine / transient deviation / recovering / sustained / uncertain 状态；
- first deviation time 和当前持续时间；
- confidence 或 calibration provenance；
- 主要证据来自 marginal novelty、joint shift 还是 temporal persistence。

这些输出用于上层 risk fusion。算法本身不应直接把所有报警解释为攻击成功，也不应独立
决定是否阻断 Agent。

### 3.7 简单性、可复现性和抗过拟合

- 优先少量具有明确含义的超参数；
- 不通过 target labels 搜索 layer、expert、window 或阈值；
- 任何监督信息、normal-only 信息和 outcome labels 的数据角色必须分离；
- 所有 target routing 分析前先冻结计划、代码路径、阈值规则和 success gates；
- post-hoc 分析必须明确标注，不得混入预注册结论。

## 4. 当前机制证据

### 4.1 边际 novelty 是强信号，但不是充分解释

在 26 条 canonical normal-fit traces 的 260,096 次选择中，16 x 64 个
layer--expert pairs 仅有两个未出现：`layer 8 / expert 14` 和
`layer 13 / expert 14`。

- 仅检测这两个 unseen pairs，整轨迹筛出 29/40 execution，replay routine controls
  报警 6/160；
- smoothed routine-frequency surprisal 筛出 37/40 execution，controls 报警
  14/160；
- 但二者均存在明显的 pre-onset first alarms，且 onset+16 clean recall 只有
  10/40 和 11/40；
- unseen detector 和 surprisal detector 都没有通过冻结的在线充分性 gate。

因此，marginal expert novelty 适合作为廉价辅助通道或 first-stage screening signal，
但不能单独作为生产在线 detector。

### 4.2 当前 detector 多数报警可在 routine 支持集内部发生

在 frozen onset 之后的 execution 报警窗口中：

- LDC：22 个 clean post-onset alarms 中只有 6 个包含 unseen pair；
- DRR：23 个 clean post-onset alarms 中有 13 个包含 unseen pair；
- rare-expert `surprisal8` 可分别解释对应报警窗口的一部分，但仍不能覆盖全部
  LDC/DRR 报警。

这说明已知专家之间的频率重排、跨层联合结构和时间动力学确实提供了额外信息。

### 4.3 Layer 和时间阶段具有不同作用

已有 layer zoom 观察显示：

- engagement onset 附近，late layers 更容易出现快速 spike；
- 后续 task-deviation persistence 更多出现在 early/middle layers；
- recovery 和 execution 的区别不是单纯由初始偏移幅度决定；
- 个体样本差异较大，不能依赖单个固定层；
- transient spike 只能证明状态发生变化，不能单独证明变化方向。

因此，候选算法需要同时保留短时间 onset 证据和较长时间 persistence/recovery 证据。

## 5. 建议的统一算法形态

候选算法可以从一个统一 routing anomaly score 出发：

\[
S_t =
S_{\mathrm{marginal},t}
+ \lambda_J S_{\mathrm{joint},t}
+ \lambda_T S_{\mathrm{temporal},t}.
\]

这里的分解主要用于样本效率和解释，不意味着必须训练三个独立 detector：

- `S_marginal` 自然统一 unseen support 和 rare-expert reweighting；
- `S_joint` 衡量 layer-wise distribution 或 normal-manifold deviation；
- `S_temporal` 衡量 change、persistence、recovery 和 repeated transition。

一个最小生产架构可以包含：

1. 每 token 计算轻量 routing representation；
2. 在一个或两个固定时间尺度上与 normal reference 比较；
3. 将证据输入简单的 sequential accumulator 或有限状态机；
4. 使用正常数据做 time-uniform calibration；
5. 输出校准风险、onset 和状态，而不是直接输出安全裁决。

这只是共同设计空间，不是要求两个研究者实现相同方法。独立方案可以采用 density
estimation、normal manifold、predictive coding、likelihood ratio、change-point、
conformal/e-value 或其他方法，只要满足本文的数据边界和生产目标。

## 6. 明确的非目标

本阶段不追求：

- 为某种输出格式编写专门规则；
- 从 routing 单独判断所有攻击、事实正确性或最终任务成功；
- 把 unseen expert 当成唯一异常机制；
- 在开发结果上继续搜索最优单层、expert ID 或 arbitrary window；
- 用高复杂度模型换取无法解释、无法校准的小幅指标提升；
- 在算法和阈值尚未冻结前使用 B3。

## 7. Codex 与 Claude 的并行研究协议

两位研究者应独立完成算法设计，避免在方案冻结前互相吸收对方的具体设计。双方共享
本 brief、既有实验事实和数据接口，但分别产出自己的 preregistration。

每个 proposal 至少需要给出：

1. **一句话核心假设**：算法认为异常 routing 的可检出结构是什么；
2. **数学定义**：输入表示、normal model、score、sequential update 和 state rule；
3. **生产属性**：因果性、单 token 复杂度、存储上界和所需 routing tensors；
4. **数据角色**：哪些数据用于 normal fit、calibration、development evaluation；
5. **冻结参数**：layer aggregation、window、threshold/calibration 和所有超参数；
6. **baselines/ablations**：至少包含 unseen-only、marginal surprisal，以及一个不含时间
   动力学的版本；
7. **success/failure gates**：在读取 proposal-specific target results 前确定；
8. **实验报告**：主结果、失败模式、逐类结果、运行成本和机制解释；
9. **可复现资产**：代码、测试、配置、artifact hashes 和 commit provenance。

独立方案完成后再做统一比较。比较时不能只按一个 AUROC 排名，应至少覆盖以下维度：

- C1 held-out clean/benign trace FAR 和 matched-group FAR；
- replay clean/benign 长序列 FAR；
- silent / bounded / execution 的 any-alarm detection；
- engaged attacks 的 pre-onset rate；
- onset+8/16/32/64 recall 和 latency；
- recovery 与 sustained execution 的分类覆盖率、准确性和 abstention；
- 不同 workflow/channel/domain/长度上的最差组表现；
- 每 token 延迟、峰值内存和 reference-bank 大小；
- 相对 unseen-only、surprisal-only、LDC 和 DRR 的增益与重叠。

## 8. 数据纪律与 B3 边界

- 当前 B1/B2、C1 和 horizon-384 replay 均属于 development evidence；
- 已经观察过的数据可以用于机制分析，但不能重新包装为 independent confirmation；
- 每个新 proposal 必须在运行 proposal-specific target analysis 前冻结；
- B3 在最终算法、阈值、状态规则和 primary metrics 全部锁定之前保持封存；
- 如果 Codex 与 Claude 的方案最终需要合并，应先在 development data 上冻结合并规则，
  再进行一次且仅一次的 B3 confirmation。

## 9. 最终研究判断标准

一个值得进入 B3 的算法不必在所有指标上完美，但必须同时满足四点：

1. 比 marginal novelty baseline 提供明确且可解释的增量；
2. 在长输出和 benign-but-unusual controls 上维持可控误报；
3. 对异常 onset 和后续 recovery/persistence 提供真实的在线信息；
4. 保持足够简单，使其能够被复现、校准、审计并部署为上层风险系统的一路信号。

论文层面的核心贡献也应围绕这一点组织：不是提出一组任务特例，而是提出一个简单、
通用、在线可校准的估计方法，用有限正常样本逼近 MoE Agent 的 routine joint temporal
routing distribution，并检测其状态变化。
