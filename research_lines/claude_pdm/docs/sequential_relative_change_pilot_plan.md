# Sequential Relative-Change Pilot：实验计划

日期：2026-09-04（America/Los_Angeles）

状态：exploratory design；在计算本文定义的指标前固定

## 1. 研究问题

Sequential Detector v1 使用当前 16-token routing profile 的绝对分类分数。B2 审计发现，冻结分数在多数
drift trace 的人工边界后相对自身边界前明显上升，但跨 batch 的绝对阈值没有稳定迁移。

本 pilot 检验一个更小的问题：

> 不学习域外任务的绝对方向，只测量相邻输出区段之间的 MoE expert-selection 分布变化，能否在另一批数据上
> 以可接受误报率追踪人工任务偏移边界？

这是 B1/B2 上的事后探索，不是独立确认实验。无论结果正负，都不能替代未来 B3 泛化验证。

## 2. 数据与证据角色

- B1：只使用 `response_brief_condition=absent` 的 40 个 matched scenarios，共 120 条 trace；
- B2：使用全部 80 个 matched scenarios，共 240 条 trace；
- 两批数据都已经被研究者查看，现均作为 development data；
- 保留既有行为标签和首个实质域外 output-token boundary，不修改 trace、标签或边界。

进行两个对称方向的测试：

1. 用 B1 negative segments 校准阈值，在 B2 评价；
2. 用 B2 negative segments 校准阈值，在 B1 评价。

两方向共享完全相同的 detector 和 threshold rule，不按测试结果调参。

## 3. 固定 detector

### 3.1 因果 routing score

固定 block width `w=8`。对每个结束于 output token `t` 的区段：

- previous block：`[t-2w+1, t-w]`；
- current block：`[t-w+1, t]`。

每层分别统计两个 block 中 64 个 expert 的 top-8 selection frequency，将其归一化为 categorical
distribution，计算两者的 Jensen–Shannon divergence；16 层 divergence 的算术平均为时刻 `t` 的 score。

score 只依赖 `t` 及之前的 routing，最早在 token index 15 可用。固定使用 natural logarithm；不做监督拟合、
层选择、平滑或 persistence。

### 3.2 Calibration

每条 non-drift trace 的完整 score sequence 是一个 negative segment。每条 drift trace 中满足 `t < boundary`
的 score prefix 是一个 negative segment；没有可用 score 的 segment 不参加 calibration。

每个 segment 只贡献一个最大 score。对这些 maxima 使用目标尾部概率 `alpha=0.10` 的有限样本 order statistic：

`k = ceil((n + 1) * (1 - alpha))`，threshold 为第 `min(k, n)` 小的 calibration value。只有
`score > threshold` 才报警。这样避免再次用极小样本估计 q0.99 尾部。

## 4. 固定评价

在 test batch 的每个可用 token 持续评分。一次 trace 的首次越阈值为 alarm：

- non-drift trace 任意 alarm 都是 false alarm；
- drift trace 的 `t < boundary` alarm 是 pre-boundary false alarm；
- drift 只有在没有 pre-boundary alarm 时，其首次 boundary 后 alarm 才计为 clean detection。

分别报告：

- negative calibration segment 数量与 threshold；
- non-drift trace false-alarm rate，并按 clean、benign control、resisted attack 分层；
- drift pre-boundary false-alarm rate；
- boundary `+8`、`+16`、`+32` 和 full-decode clean recall；
- clean hit 的 latency 分布；
- 每 1,000 个 negative eligible positions 的 alarm onset；
- 按 target domain 分层的 drift 结果；
- warm-up 对理论可达 recall 的限制。

## 5. 解释规则

本 pilot 没有预设 pass/fail gate。解释时遵循：

- 两个方向都改善，才可称为具有初步 batch-stable evidence；
- 单方向有效只能说明值得定位 batch 差异；
- 高 recall 伴随高误报不构成可用 sequential signal；
- 低 recall 不能否定所有 relative detector，只否定这个固定的、无监督相邻区段 JSD 实例；
- 结果不得被描述为 routing 独有信息，因为本 pilot 没有建立强 text baseline；
- 运行后提出的任何变体都必须标为新的 post-hoc hypothesis，不能混入本 pilot 指标。
