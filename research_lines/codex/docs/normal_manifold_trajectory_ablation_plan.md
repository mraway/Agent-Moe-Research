# Normal Manifold Finite-Memory Trajectory Ablation：实验计划

日期：2026-09-05（America/Los_Angeles）

状态：运行前冻结；B1/B2 adaptive development ablation，不是新 detector 的确认实验

逻辑依据：[算法设计的逻辑基础](algorithm_design_logic_foundations.md)中的 `O10--O16`、`D1--D10` 与
`H1--H3`；直接前序实验见
[absolute-age-free ablation 报告](normal_manifold_age_free_ablation_report.md)。

## 1. 本实验只回答什么

前序实验已经表明：不使用 absolute decode age 时，跨领域执行仍显著离开 Agent 的正常 routing-state support；
但正常 hard transitions 也会产生高峰，整条路径取任意 endpoint maximum 无法稳定迁移。

本轮回答两个问题：

1. **H2 / transition**：正常状态条件下的下一步 routing transition，是否比单独的当前状态提供额外、可迁移的
   post-drift 信息？
2. **H3 / finite trajectory**：要求最近若干 endpoint 持续偏离，是否能压制正常瞬时峰值，并更好地区分真实
   cross-domain execution 与 benign mention / attacked-but-resisted？

本轮不是超参数搜索。所有候选都必须完整报告，不能根据 target 结果挑选一个“赢家”。如果 transition residual
或 finite-memory floor 没有双向行为特异性增益，就记录 H2/H3 的负证据。

## 2. 数据角色与限制

继续使用冻结的 B1/B2 development data 和原 sample-index hashes：

- B1 `brief=absent`：120 traces，96 non-drift，24 drift；
- B2：240 traces，205 non-drift，35 drift；
- 每个方向 source folds 0--2 为 normal fit，folds 3--4 为 stopping calibration，另一 batch 全量评价；
- 同一 pair group 不跨 fold；B1/B2 已被反复观察，只用于双向假设筛选；B3 不使用。

为与前序实验做受控比较，normal fit 仍使用所有
`goal_plan_deviation_started=false` 的 clean、benign 和 resisted traces。当前 canonical-only 正常数据不足以再拆
独立 fit/cal，因此本轮不解决 `Q1`，也不能作为最终 normal-model 训练规范。

## 3. 固定状态表示

状态 `x_t` 完全复用 age-free ablation：

- final-generation decode routing；
- causal width-8 top-8 expert-selection frequency；
- 每层归一化后平方根变换，对应 Hellinger geometry；
- middle `L5--L10` 与 late `L11--L15` 两个 band 等权；
- 每条 fit trace 最多均匀贡献8个 state anchors；
- 当前状态 raw score 是到全局 normal-state bank 的第5近邻距离；
- 用 fit anchors 的 leave-one-trace-out raw scores 做一次全局 median/IQR 标准化，得到 `S_t`。

`S_t` 不使用 absolute age、workflow、arm、domain、文本、token ID、boundary 或未来长度。

## 4. 固定 conditional-successor transition score

对每条 fit-normal trace 的相邻状态窗口形成 edge `(x_{u-1}, x_u)`，每条 trace 最多均匀保留8条 edge。对
query edge `(x_{t-1}, x_t)`：

1. 以 `x_{t-1}` 到 reference predecessor 的 Hellinger band-distance，选择最近的16条正常 reference edges；
2. 只在这16条 edge 的 successor 中，计算 `x_t` 的第5近邻距离，记为 raw conditional-successor residual；
3. 对每条 reference edge 用 leave-one-trace-out 重复同一过程；
4. 用全部 fit reference residual 的全局 median/IQR 标准化，得到 `T_t`。

这里的16是预先固定的 conditional candidate count，第5近邻与状态 score 保持一致。算法没有按 workflow 或
token index 寻找 transition；reference leave-one-trace-out 防止重叠窗口从同一 trace 复制自身。

`T_t` 的含义是：在与近期状态相似的正常上下文中，当前 successor 是否仍获得正常转移支持。它不是简单的
`S_t-S_{t-1}`，也不使用无界 forecast/CUSUM。

## 5. 五条固定的因果 evidence streams

本轮只比较以下五条 stream：

| Name | Definition | 有效 routing lookback |
|---|---|---:|
| `state_endpoint_z` | `S_t` | 8 tokens |
| `transition_endpoint_z` | `T_t` | 9 tokens |
| `state_floor_4` | 最近4个 `S` 的经验 q25 | 11 tokens |
| `transition_floor_4` | 最近4个 `T` 的经验 q25 | 12 tokens |
| `joint_floor_4` | 对齐后的 `min(state_floor_4, transition_floor_4)` | 12 tokens |

q25 使用固定线性分位数。四个值中只有一个极端峰值时，q25 不会被该峰值主导；只有偏离占据近期窗口的多数时
才明显升高。`joint_floor_4` 是严格 AND：只有状态深度和 conditional transition 同时持续异常才高。

没有 running maximum、无限累计、衰减状态或 absolute-age correction 进入 evidence definition。每个 model
generation episode 独立复位历史；本轮仍只覆盖现有 final-generation episode。

## 6. Phase 对齐与 representation-level 检查

人工 evidence onset `o` 只用于事后评价。每条 stream 根据其真实 routing lookback `L` 划分：

```text
end < o                : pre-onset
o <= end < o + L - 1   : mixed transition
end >= o + L - 1       : fully post-onset
```

例如 `state_endpoint_z` 的 fully-post 从 `o+7` 开始，`joint_floor_4` 从 `o+11` 开始。这样不会把仍含 onset 前
routing 的 history statistic 当作完整跨领域轨迹。

每条 stream、每个方向固定报告：

1. normal、pre、mixed、fully-post 的 endpoint-weighted 和 trace-balanced 分布；
2. target-normal trace-mean q95 与 fully-post drift trace mean 超过它的数量；
3. 同时存在 pre/fully-post 的 drift 内部 `post mean - pre mean`；
4. normal vs fully-post trace-mean AUROC/AP；
5. target normal 与 source calibration 的 trace mean/max shift。

H2 获得支持要求 transition evidence 在两个方向均有一致的 fully-post 抬升，而不是只在一批或少数 domain
有效。若 `T_t` 与 state 相比没有额外分离、明显更不稳定或主要升高在 normal hard transitions，则 H2 不支持。

## 7. Execution-vs-mention/refusal 是 H3 的核心评价

对每条完整因果 stream 取 path maximum，固定报告以下 trace-level ranking：

- drift attack vs resisted attack；
- drift attack vs benign control；
- drift attack vs clean；
- drift attack vs all non-drift。

同时报告 clean、benign、resisted、drift 的 path-maximum 分布，以及 drift/resisted 的 target domain、channel 和
长度诊断。Ranking 仅用于判断表示/轨迹是否包含 behavior-specific information，不等于已经选择报警阈值。

H3 的正证据必须满足：finite-memory stream 在两个方向都比 instantaneous state 更能分开 drift 与 resisted，且
没有通过大幅恶化 clean/benign separation 换取结果。单方向改善、只区分 drift 与 clean、或 resisted 与 drift
一起升高，都不支持 H3。

## 8. Path-level stopping stress test

每条 stream 的 alarm rule 固定为：

```text
alarm at the first eligible endpoint where E_t > threshold
```

threshold 用 source calibration 的**整条 path maximum**校准，`alpha=0.10`、严格 `>`。主要风险单位先把同一
pair group 的全部 non-drift arms 聚合成一个 maximum；legacy per-trace maximum 只作对照。不得从两种校准中
选择 target 表现更好的版本。

Target 固定报告：

- overall 及 clean/benign/resisted FAR；
- onset 前 alarm，`+4/+8/+16/full` clean recall 和可达率；
- latency（连同 recall）；
- 每1,000 eligible normal endpoints 的 alarm onset；
- normal `P(alarm by risk step)`，risk step 为第几个 eligible endpoint，检查点固定为 `4/8/16/32/64/full`。

risk step 只进入风险曲线，不进入 score。当前 source calibration 独立 group 数很小，pair-group order statistic
可能退化为最大值；这要作为数据限制报告，不能改回 trace-weighted calibration。

## 9. 固定解释规则

- `state_floor_4` 双向降低 resisted/clean/benign path tails，同时保留 drift separation：支持“持续性有用”；
- `transition_endpoint_z` 双向强于 state，或 `transition_floor_4/joint_floor_4` 带来行为特异性增益：支持 H2；
- finite-memory 只降低 recall、或正常峰值本身连续超过4步：反对当前 H3 形态；
- transition 与 resisted 同步升高：说明它更像语义/政策转折 detector，而不是 execution detector；
- 双向结论不一致：记录 batch support shift，不选单方向最佳方法；
- representation ranking 很强但 path calibration 仍失败：只能说明存在信号，不能冻结 detector。

本轮不调 history length、q25、conditional candidate count、neighbor k、width、layers、anchor count、normal pool 或
score combination。

## 10. 实现、防泄漏与产物

- 从冻结 compact routing cache 读取，不重新运行模型；
- 运行时验证 B1/B2 hashes、trace counts 和 age-free result hash；
- unit tests 覆盖 conditional gating、leave-one-trace-out、q25 history alignment、joint alignment、method-specific
  fully-post boundary、pair-group calibration 和 risk-step curve；
- 每条 trace 保存完整 endpoints、state、transition 和五条 evidence streams；
- 代码与 tests 完成并提交后，才运行一次完整 B1/B2 分析；
- 输出到新的 ignored artifact，不覆盖任何既有 trace 或实验结果。

计划产物：

```text
scripts/run_normal_manifold_trajectory_ablation.py
tests/test_normal_manifold_trajectory_ablation.py
artifacts/agent_v2/normal_manifold_trajectory_ablation/result.json
docs/normal_manifold_trajectory_ablation_report.md
```

## 11. 停止规则

结果生成后不修改五条 streams 或超参数。如果 H2/H3 都不支持，先逐 trace 审计 drift 与 resisted 的 trajectory
重叠，明确 route-only 的信息边界；如果至少一条 finite-memory stream 在两个方向显示一致行为特异性增益，再另写
stopping/calibration proposal。B3 只在候选算法完全冻结后使用。
