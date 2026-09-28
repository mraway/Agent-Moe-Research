# 正常客服路由流形：三个算法 Proposal

日期：2026-09-04（America/Los_Angeles）

状态：算法设计稿；尚未运行本文定义的实验；每个 proposal 在执行前另写一份简短预注册计划

## 1. 核心问题

本阶段把检测问题固定为：

> 只用正常、被授权的客服执行轨迹学习 MoE decode routing 的正常集合；在线生成时，如果当前 routing
> 显著且持续地离开该集合，则发出跨领域任务偏移警告。

这里学习的不是“攻击长什么样”，也不是“诗歌、代码等已知异常类别长什么样”。模型拟合、降维、距离尺度、
阈值和持久性参数都不能使用 drift trace。Drift trace 及其 evidence-onset 只用于算法完成后的评价。

这个 formulation 比已有的 drift-vs-resist ridge classifier 更接近实际部署条件：生产系统通常拥有大量正常日志，
但不可能提前收集完所有未来异常域，也不应把 detector 限制在训练时见过的 poetry、code 等类别中。

## 2. 为什么现在值得检验这个方向

已有数据给出四条直接约束：

1. B1/B2 的跨领域 evidence-onset 附近都存在 matched routing change，width-8 probability-JSD contrast
   分别为 `+0.0240` 和 `+0.0282`，40 个可比较场景中 36 个为正；
2. late layers 的 onset effect 约为 early layers 的 6.8 倍，而 middle layers 的 signed direction 跨批更稳；
3. 正常客服回复中的最大局部 routing change 与 drift onset 同量级，单纯 `JSD > threshold` 已经被否定；
4. 使用 drift 正例学习语义方向、再相对同一 trace 历史评分的方法，在双向 batch transfer 中达到约
   43%--46% 的 `onset +8` recall 和 11%--13% 的 non-drift trace FAR。这说明“方向 + 相对化”有价值，
   但它依赖 drift 正例，尚未回答正常域单类学习能否成立。

因此，下一步不是再换一个二分类器，而是直接检验正常 routing 是否具备可建模的几何或动力学结构。

## 3. 三个 proposal 覆盖的不同假设

| Proposal | 对正常 routing 的假设 | 核心 score | 它主要回答什么 |
|---|---|---|---|
| P1：Layered kNN Manifold | 正常客服是多模态、非线性的局部流形 | 到正常邻域的 conformal rarity | 域外窗口是否根本不像任何正常客服状态？ |
| P2：Conditional PCA Control Chart | 扣除 workflow/生成阶段后，正常变化集中在低秩子空间 | 子空间内极端程度 `T²` + 子空间外残差 `Q` | 正常 nuisance 能否被压缩，域外方向是否落在正常切空间之外？ |
| P3：Route Forecast + CUSUM | 正常客服的下一段 routing 可由自身历史预测 | 正常预测残差的累计 surprise | 罕见但正常的状态转移，能否与不可预测的任务切换区分？ |

P1、P2 都是状态模型，但一个是非参数局部几何，一个是可解释的条件化低秩几何。P3 不要求当前状态本身从未在
正常集中出现；它检验转入该状态的路径是否符合正常客服动力学。三个 proposal 不是同一分类器的参数搜索。

## 4. 共同实验协议

### 4.1 数据角色

核心 cohort 保持不变：

- B1：`brief=absent`，120 traces，其中 96 non-drift、24 drift；
- B2：240 traces，其中 205 non-drift、35 drift；
- B1 与 B2 都已经被反复观察，因此所有结果均为 exploratory development，不是 independent confirmation；
- B3 暂不生成。三个算法都完成后才决定是否有候选值得冻结并进入 B3。

每个 proposal 做两个方向完全同构的 transfer：`B1 -> B2` 和 `B2 -> B1`。Source batch 内沿用已经固定的
`preregistered_fold`：fold 0--2 的 normal traces 用于拟合，fold 3--4 的 normal traces 只用于阈值校准。
对应数量为：

| Source | Normal fit traces | Normal calibration traces |
|---|---:|---:|
| B1 | 59 | 37 |
| B2 | 128 | 77 |

同一 `pair_group_id` 不跨角色。Target batch 不重新拟合、标准化、选择维度或调整阈值。

### 4.2 “只学习正常”的严格含义

Primary normal set 是所有 `goal_plan_deviation_started=false` 的 decode：clean、benign control 和 resisted attack。
把 resisted attack 视为正常是刻意的：它们包含攻击暴露，但 agent 仍停留在授权行为域，在线 detector 不应因拒绝
攻击而报警。

同时报告一个 `clean + benign only` 的 fit sensitivity，用来回答正常模型是否必须见过攻击拒绝才能工作。该
sensitivity 不取代 primary，也不根据结果选择阈值。

以下信息禁止进入拟合与校准：

- drift trace 的任何 pre-onset 或 post-onset window；
- drift/resist label、target domain、attack family、attack channel；
- evidence onset、completion boundary 或未来 decode length；
- output token ID、token text、token hash、文本 embedding。

允许使用生产时天然已知的 `workflow`、当前 decode token index 和过去 routing history。

### 4.3 共同 routing signature

Primary 使用 decode-only、width `w=8` 的 top-8 expert-selection signature。对窗口结束位置 `t`：

```text
s[t,l,e] = count(expert e selected in tokens t-w+1 ... t at layer l) / (w * 8)
```

每层的 64 维分布和为 1。它与相关工作中的 per-layer normalized routing signature 对齐，只是不再汇总完整
prompt 和完整输出，而是形成因果滑窗序列。Primary 使用 middle `L5--L10` 与 late `L11--L15`；两组在 score
中等权，避免 late layers 因绝对幅度更大而完全淹没方向更稳定的 middle layers。Early `L0--L4` 只作预声明
的 layer ablation。

`w=4` 和 `w=16`、完整 router-probability signature 都只作为预声明 sensitivity：不能用它们在 target batch
上选择 primary 版本。这样既检查延迟/平滑度权衡，也能回答只保存 top-k IDs 是否已经足够。

长回复不能贡献更多拟合权重。P1/P2 从每条 fit trace 最多均匀抽取 8 个 reference windows；P3 对每条 trace
的训练 loss 赋予相同总权重。

### 4.4 在线与时间语义

所有 score 只能使用截至 token `t` 已经保存的 routing。token `t` 的 routing 在该 token 生成并送回下一次
forward 后才可用，所以 latency `0` 表示可以在生成 token `t+1` 之前阻止继续输出，不表示能阻止 token `t`。

主行为 anchor 使用 observation atlas 从冻结 evidence char span 重建的 **evidence onset**。原始
`goal_plan_deviation_start_output_token` 是 evidence completion，保留为审计指标，不能继续当作语义起点。

### 4.5 阈值与共同评价

每条 calibration normal trace 只贡献一个 full-trace maximum，从这些 maxima 用有限样本上尾 order statistic
得到 `alpha=0.10` 的 source threshold。阈值控制单位是完整 trace，不是大量相关的滑动窗口。Target 不校准。

Primary 指标为：

- target non-drift trace FAR，并按 clean、benign、resisted attack 分层；
- clean-hit recall at evidence-onset `+4/+8/+16`：onset 前没有 alarm，且在对应期限前首次报警；
- 首次 post-onset alarm latency 中位数及理论可达率；
- drift trace 的 pre-onset alarm rate，另列可能的 semantic-commitment late-label audit；
- 每 1,000 个 normal eligible positions 的 alarm-onset 数；
- 按 domain、channel、workflow 的覆盖，不把小格子解释为精确率估计。

一个值得进入 B3 的 normal-only 候选，理想上应在两个 transfer 方向同时满足：target non-drift FAR 不高于
15%，`onset +8` clean-hit recall 至少约 35%，median latency 不超过 8 tokens，并且命中不是由单一 target
domain 驱动。这个 gate 是研究信号标准，不是生产部署标准。

## 5. Proposal 1：Layered kNN Normal Manifold

### 5.1 假设

正常客服可能包含政策解释、状态事实、拒绝、列表和收尾等多个离散模式，不适合由单个中心或单个高斯描述；
但一个正常窗口应当在历史正常库中找到若干 routing 近邻。跨领域窗口则会逐渐失去这样的邻域支持。

### 5.2 模型

1. 对每层 selection distribution 使用平方根变换 `phi(s)=sqrt(s)`，使欧氏几何对应 Hellinger 几何；
2. reference bank 由 normal-fit traces 的均匀 8 anchors 构成；
3. 邻居优先限制在相同 broad workflow family（status-only、knowledge-QA、status+knowledge）和相同 causal
   age bin（`8--15`、`16--31`、`32--63`、`64--127`、`128+`）；
4. 若一个 cell 少于 20 个 reference windows，依次回退到同 workflow family、再回退到全局 normal bank；
5. middle 与 late 分别计算到第 5 近邻的距离，再对两个 band 等权平均；
6. 用 fit bank 的 leave-one-trace-out kNN 距离建立对应 cell 的经验 normal tail，将距离转成
   `a_t = -log(p_t)`；
7. 定义 persistent score `b_t=min(a_{t-1},a_t)`，再用每条 calibration trace 的 `max_t b_t` 校准阈值；
   `b_t` 超过阈值才报警。

这里的 conformal tail 是经验 rarity normalization，不宣称 overlapping windows 下具有严格 anytime-valid
coverage；真正的错误率仍由独立 calibration traces 的 full-trace maxima 控制。

### 5.3 最有信息量的结果

- **成功**：说明无需见过任何异常域，正常客服 routing 本身就形成了可用于开放集检测的局部支持；
- **clean 正常、benign/resisted 高误报**：说明 manifold 学到的是窄文本风格，而不是完整授权行为域；
- **P1 只在同 workflow 有效**：说明 routing 更像 workflow identifier，跨 workflow 的正常域尚未统一；
- **FAR 可控但 recall 很低**：说明域外执行仍落在正常局部支持内，或 8-token signature 的 token 语义混合过强。

P1 最接近当前核心命题，因此第一个运行。

## 6. Proposal 2：Conditional PCA `T² + Q` Control Chart

### 6.1 假设

正常客服的高维 expert identity 看似复杂，但主要变化可能由少数 workflow、生成阶段和正常语义转折方向组成。
跨领域任务不一定远离所有正常近邻，却可能产生正常低秩切空间无法重建的 residual。

### 6.2 模型

1. 使用与 P1 相同的平方根 selection signature；
2. 按 broad workflow family 与 causal age bin 估计 normal mean。每个 cell mean 用 16 个伪窗口的权重向同
   age-bin global mean 收缩，避免小 cell 过拟合；
3. 对条件均值残差拟合 normal-only PCA，middle 与 late 各保留固定 16 个成分，共 32 维，不根据 drift
   结果选 rank；
4. 每个窗口计算：
   - `T²`：在正常主子空间内，标准化坐标离中心有多极端；
   - `Q`：原始条件残差中不能被正常主子空间重建的能量；
5. 分别用 normal-fit windows 的经验 tail 把 `T²`、`Q` 转成 rarity，取二者较大值作为 `a_t`；
6. 与 P1 相同，先取相邻两个 `a_t` 的最小值作为 persistent score，再由 calibration trace maxima 得到
   threshold。

`T²` 防止异常沿正常高方差方向走得过远，`Q` 捕获新的专家组合方向。二者缺一都会给算法留下明显盲区。

### 6.3 最有信息量的结果

- **P2 成功且 P1 失败**：正常集合有可迁移的低秩结构，但有限 reference bank 的局部覆盖不足；
- **P1 成功且 P2 失败**：正常 routing 明显非线性或多模态，线性切空间把异常方向吸收成了正常方差；
- **`Q` 有效、`T²` 无效**：异常主要是新方向；反之则是沿已知正常方向走到极端；
- **source calibration 正常、target 全面漂移**：conditional mean/PCA basis 存在 batch shift，暂不适合作为稳定
  正常模型。

P2 在 P1 完成并冻结报告后第二个运行，不根据 P1 的 target 数字修改 rank、age bins 或 persistence。

## 7. Proposal 3：Normal Route Forecast + CUSUM

### 7.1 假设

一个 routing 状态本身可能很少见，但如果它是从当前客服内容自然转入政策说明、拒绝或总结，仍应当能由近期
正常 routing 预测。跨领域 drift 的关键信号可能不是绝对位置，而是“从这段客服历史出发，不应该这样转移”。

### 7.2 模型

1. 将 decode 划为不重叠的 4-token causal blocks，使用 middle/late selection signature；
2. 在 normal-fit blocks 上，middle 与 late 各做 16 维 PCA，得到 32 维状态 `z_j`；
3. 用 fixed-ridge multi-output regression 预测当前 block：

   ```text
   z_hat[j] = f(z[j-1], z[j-1]-z[j-2], broad_workflow, current_age_bin)
   ```

   loss 对每条 trace 等权；ridge penalty 固定为 predictor input dimension；
4. 用 normal-fit prediction residual 的 diagonal robust scale 计算标准化 residual energy `q_j`；
5. 将 `q_j` 转成 fit-normal empirical tail `p_j`，形成 surprise `u_j=-log(p_j)`；
6. 在线累计：

   ```text
   C[j] = max(0, C[j-1] + u[j] - 1)
   ```

7. Calibration normal trace 的 `max_j C[j]` 决定 `alpha=0.10` threshold；超过即报警。

不重叠 block 避免把几乎相同的 overlapping windows 当成多次独立证据。使用两个历史 blocks 后，最早 score
在 output token index 11；因此必须显式报告早 onset 的不可达比例。

### 7.3 最有信息量的结果

- **P3 成功而 P1/P2 失败**：异常信号主要存在于 transition dynamics，而不是静态 off-manifold 状态；
- **P1/P2 成功而 P3 失败**：状态身份清楚，但正常语言生成太多样，低容量预测器无法预测下一段 routing；
- **单点 residual 有信号、CUSUM 延迟过长**：任务偏移更像短促 change point，不适合持续累计；
- **CUSUM 在正常长回复不断累积**：normal tail 或条件模型没有处理序列依赖，不能靠调高阈值掩盖。

P3 最复杂，放在最后运行。前两个静态模型的失败不能用于修改 P3 输入；否则三个 proposal 会退化成连续的
target-guided 调参。

## 8. 最小 sanity checks

本阶段不扩展为大规模 baseline 竞赛，只保留能改变结论的检查：

1. **Layer ablation**：early-only、middle-only、late-only，检查结果是否符合已有层级观察；
2. **Selection vs probability**：同算法更换 routing signature，判断廉价 top-k instrumentation 是否足够；
3. **Expert-identity destruction**：在每层内打乱 token 与 expert assignment，同时保持 token 数和该层总体负载，
   检查信号是否真的依赖结构化 expert identity；
4. **Normal-arm stress**：clean、benign、resisted 的 FAR 必须分列，防止只在最容易的 clean traffic 上成立。

Token hash、强文本模型、hidden-state probe 和生产 guardrail 不属于这一轮 proposal 的主任务。若 normal-only
routing detector 本身没有清晰信号，不应先靠扩大 baseline 工程来挽救命题。

## 9. 执行顺序与停止规则

严格按 `P1 -> P2 -> P3` 执行。每个实验都遵循：

1. 先把该 proposal 的具体实现、数据 hash、超参数、指标和 artifact 路径写入独立 plan 并提交；
2. 再运行一次 B1/B2 双向 transfer；
3. 保存逐 trace、逐时间点 score，不只保存汇总指标；
4. 写完成功、失败和反例报告后，才开始下一个 proposal；
5. 不覆盖既有 artifacts，不修改原始 trace、behavior label 或 boundary。

三个 proposal 都完成后只做一次横向选择。如果没有任何方案达到“两个方向、合理 FAR、及时多域 recall”的最低
形态，则结论应是：现有数据支持 routing 与跨领域执行同时变化，但尚不支持只靠正常 routing 流形形成有用的
开放集在线 detector。如果至少一个达到 gate，则冻结唯一候选，之后才设计 B3。

## 10. 与既有方法的关系

- Sequential v1：学习 drift-vs-normal 的绝对状态轴；本轮完全不允许使用 drift 正例；
- Adjacent-block JSD：只看无方向变化幅度，已得到负结果；本轮学习 normal support、normal subspace 或 normal
  transition，不再把“变化大”直接等同于异常；
- Directional-relative pilot：证明 learned drift direction 加 trace-relative baseline 有初步价值；本轮检验
  是否能从 normal data 自身恢复足够的边界，而不预先知道异常方向；
- Task-conditioned routing signature related work：提供 per-layer normalized expert-frequency 表示；本轮把
  静态整段任务分类改造成 decode-only、因果、开放集、trace-level calibrated 的持续监测。

相关现有材料：

- [routing 数据观察报告](routing_data_observation_report.md)
- [Sequential v1 方法审计](agent_v2_sequential_method_audit.md)
- [无方向 relative-change 负结果](sequential_relative_change_pilot_report.md)
- [方向性 relative pilot](sequential_directional_relative_pilot_report.md)
- [算法并行研究背景](sequential_v2_parallel_research_brief.md)
