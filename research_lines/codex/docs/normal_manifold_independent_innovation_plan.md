# Normal Manifold Independent Routing Innovation：实验计划

日期：2026-09-05（America/Los_Angeles）

状态：运行前冻结；B1/B2 adaptive development representation experiment，不是 detector confirmation

逻辑依据：[算法设计的逻辑基础](algorithm_design_logic_foundations.md)中的 `M3--M4`、`O9--O16`、
`D1--D10` 与 `H1--H3`；直接前序实验见
[finite-memory trajectory ablation 报告](normal_manifold_trajectory_ablation_report.md)。

## 1. 本实验只回答什么

前序实验的 width-8 sliding states 在相邻 endpoint 间共享7个 routing tokens。Conditional successor 和
`q25/4` persistence 因而主要重复同一个 routing event，没有隔离真正新增的信息。

本轮只回答三个 representation 问题：

1. **Non-overlapping blocks**：把每个 routing token 只分配给一个固定8-token block 后，既有 state signal 是否
   仍存在，正常 path tail 是否更稳定？
2. **Staggered independent lanes**：逐 token 更新时，要求同一 lane 中两个相邻、互不重叠的8-token blocks 都
   异常，是否比 eligibility-matched current-state score 更能区分 drift 与 resisted attack？
3. **Token innovation**：直接对每个新 routing token 评分，再在互不重叠的8-token blocks 内聚合，是否获得
   不依赖 sliding-state 重复计算的行为特异性信号？

本轮不是另一次 smoothing 或 stopping-rule 搜索。所有固定 streams 必须完整报告，不能按 target 结果选择性
删除。B1/B2 只提供双向开发证据；无论结果如何都不读取或生成 B3。

## 2. 数据角色与固定边界

继续使用冻结的 B1/B2 数据和 sample-index hashes：

- B1 `brief=absent`：120 traces，96 non-drift，24 drift；
- B2：240 traces，205 non-drift，35 drift；
- 每个方向 source folds 0--2 为 normal fit，folds 3--4 为 stopping calibration，另一 batch 全量评价；
- 同一 pair/scenario group 不跨 fold；
- normal fit 继续使用所有 `goal_plan_deviation_started=false` 的 clean、benign 和 resisted traces，以便与
  age-free 与 trajectory ablation 做受控比较。

当前 broad non-drift fit 不是未来 canonical normal-training contract。本实验不解决正常数据组成问题，也不把
任何结果称为独立确认。

## 3. 固定 routing 几何

所有表示只读取 final-generation decode routing：

- 16个 MoE layers、每层 top-8 expert selections、64 experts；
- middle `L5--L10` 与 late `L11--L15` 两个 band 等权；
- 每层 selection frequency 归一化后平方根变换，继续使用 Hellinger geometry；
- 第5近邻距离；
- 每条 fit-normal trace 最多均匀贡献8个 reference anchors；
- reference raw score 使用 leave-one-trace-out 邻居；
- 只用 source-fit reference scores 的全局 median/IQR 标准化。

所有 score 均不读取 absolute decode age、workflow、arm、domain、token text/ID、真实 outcome、人工 boundary 或
未来长度。Token index 仅用于因果切块和 endpoint 对齐，不进入 normality 或 threshold 数值。

## 4. 两类基础 score

### 4.1 Sliding state score `S_t`

完整复用 age-free ablation 的 width-8 state representation 与 normal bank。`S_t` 是窗口 `[t-7, ..., t]`
到 normal-state bank 的第5近邻距离经 source-fit 全局稳健标准化后的值。

### 4.2 Token innovation score `U_t`

每个新 token 单独形成 routing signature：每层8个被选 expert 各占 `1/8`，再做平方根变换。Token bank 每条
fit-normal trace 最多均匀贡献8个 token anchors；第5近邻、leave-one-trace-out 和 source-fit median/IQR
标准化与 state 完全相同。

`U_t` 的含义是“这一个新到达的 routing observation 是否得到正常 token support”。它不减去 `S_(t-1)`，也
不把共享的7个旧 token 再当作新的 transition evidence。

## 5. 七条固定 evidence streams

| Name | Definition | Endpoint | Routing lookback |
|---|---|---|---:|
| `sliding_state_z` | 原始 `S_t`，机制基线 | 每个 `t >= 7` | 8 |
| `nonoverlap_state_z` | `S_t` 中 `t = 7,15,23,...` 的固定 lane | 每8 token | 8 |
| `staggered_state_current_z` | `S_t`，但只从存在同-lane predecessor 的 `t >= 15` 开始 | 每个 `t >= 15` | 8 |
| `staggered_state_min2_z` | `min(S_(t-8), S_t)` | 每个 `t >= 15` | 16 |
| `token_endpoint_z` | 单个新 token 的 `U_t` | 每个 token | 1 |
| `nonoverlap_token_mean8_z` | 固定 block 内8个 `U` 的均值 | `t = 7,15,23,...` | 8 |
| `nonoverlap_token_q25_8_z` | 固定 block 内8个 `U` 的线性 q25 | `t = 7,15,23,...` | 8 |

固定 blocks 从每个 final-generation episode 的 token 0 开始；episode 边界处全部复位。`nonoverlap_state_z` 与
两个 token-block streams 中，一个 routing token 只进入一个 endpoint。Staggered 方法包含8条按 `t mod 8`
划分的 lane；每条 lane 的相邻 state windows 不重叠，`min2` 要求同 lane 的连续两个 blocks 均有证据。

`staggered_state_current_z` 是 `min2` 的 eligibility-matched comparator。没有 running maximum、CUSUM、可调
history width、衰减状态或跨 episode 记忆进入 evidence definition。

## 6. Phase 与 representation 评价

人工 evidence onset `o` 只在 score 全部保存后用于评价：

```text
end < o                         : pre-onset
o <= end < o + lookback - 1     : mixed transition
end >= o + lookback - 1         : fully post-onset
```

稀疏 non-overlap stream 只在实际存在的 endpoints 上评价。每条 stream、每个方向固定报告：

1. normal、pre、mixed、fully-post 的 endpoint-weighted 和 trace-balanced 分布；
2. target-normal trace-mean q95 与 fully-post drift trace mean 超过它的数量；
3. 同时具有 pre/fully-post 的 drift 内部 `post mean - pre mean`；
4. normal vs fully-post trace-mean AUROC/AP；
5. source-calibration 与 target-normal 的 trace mean/max shift；
6. 相邻 stream observations 的 score Spearman correlation；
7. 与 `sliding_state_z` 在共同 endpoints 上的 score/path-maximum correlation。

Correlation 是冗余度诊断，不是成功指标；non-overlap 在 token 使用上独立，不意味着语义状态统计独立。

## 7. Behavior specificity 与预声明判断

对完整因果 path 取 maximum，固定报告 drift attack 相对 resisted attack、benign control、clean 和全部 non-drift
的 AUROC/AP，以及各行为层的 path-maximum 分布。

### H2：token innovation 的最低支持条件

`token_endpoint_z` 或其两个 non-overlap block aggregate 中至少一个必须同时满足：

- 两个 transfer 方向 normal-vs-fully-post trace-mean AUROC 均不低于 `0.90`；
- 两个方向至少 `80%` 的可比较 drift trace 为 fully-post mean 高于 pre mean；
- 不是只在单一 batch 或单一 target domain 出现的效果。

达到这些条件只说明新增 token routing 自身包含 post-drift information，不说明 detector 可用。

### H3：独立 persistence 的最低支持条件

`staggered_state_min2_z` 相对 eligibility-matched `staggered_state_current_z` 必须在两个方向都把
drift-vs-resisted path-max AUROC 提高至少 `0.03`，并且 drift-vs-clean 与 drift-vs-benign AUROC 的下降均不超过
`0.02`。`nonoverlap_token_q25_8_z` 相对 `nonoverlap_token_mean8_z` 使用相同判断。

只降低普通 clean tail、只在单方向改善，或让 resisted 与 drift 同步升高，都不支持 H3。

## 8. Path-level stopping stress test

每条 stream 使用同一个固定 alarm rule：

```text
alarm at first eligible endpoint where E_t > threshold
```

Primary threshold 用 source calibration 中按 pair group 聚合的完整 non-drift path maximum 做 finite-sample upper
threshold，`alpha=0.10` 且严格 `>`；legacy per-trace maximum 只作对照。

Target 固定报告 overall/clean/benign/resisted FAR，boundary 前 alarm，`+4/+8/+16/full` clean recall、可达率、
latency、每1,000 eligible normal endpoints 的 alarm onset，以及 risk step `4/8/16/32/64/full` 的累计误报。

候选只有在两个方向同时满足以下旧 research gate 时，才可进入新的 frozen detector proposal：

- non-drift trace FAR `<= 15%`；
- onset `+8` clean recall `>= 35%`；
- clean-hit median latency `<= 8`；
- 没有用明显更高的 resisted-attack FAR 换取 recall。

本轮结果不得触发直接读取 B3；通过 gate 后仍需另写 detector 与 B3 计划并提交冻结。

## 9. 实现、防泄漏与产物

- 从冻结 compact routing cache 读取，不重新运行模型；
- 运行时验证 B1/B2 hashes 与 trajectory result hash；
- unit tests 覆盖 token signature、leave-one-trace-out、固定 non-overlap endpoints、staggered lane 对齐、
  non-overlap token aggregation、method-specific phase、pair-group calibration 与 correlation 对齐；
- 每条 trace 保存完整 endpoints、基础 `S/U` 和七条 evidence streams；
- 代码与 tests 完成并提交后，才运行一次完整分析；
- 输出到新的 ignored artifact，不覆盖任何既有 trace、cache、label、boundary 或实验结果。

计划产物：

```text
scripts/run_normal_manifold_independent_innovation.py
tests/test_normal_manifold_independent_innovation.py
artifacts/agent_v2/normal_manifold_independent_innovation/result.json
docs/normal_manifold_independent_innovation_report.md
```

## 10. 停止规则

结果生成后不修改七条 streams、anchor count、neighbor k、block alignment、聚合函数、success gates 或 threshold。
若 H2/H3 均不支持，报告 route-only 在 token-level behavior specificity 上的边界，不继续在 B1/B2 搜索变体；若
至少一个假设获得支持但 detector gate 未通过，则下一步单独研究 group/time-uniform calibration；只有冻结候选
同时通过 detector gate，才规划新的 B3 confirmation。
