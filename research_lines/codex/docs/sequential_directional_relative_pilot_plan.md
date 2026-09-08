# Sequential Directional-Relative Pilot：实验计划

日期：2026-09-04（America/Los_Angeles）

状态：exploratory design；在计算本文定义的指标前固定

## 1. 研究问题

Sequential Detector v1 的 learned routing state score 在 B2 有很强的 post-hoc pre/post 排序，但全局绝对
threshold 迁移失败。随后固定的 adjacent-block JSD pilot 又表明，无方向的 routing 变化幅度与人工任务切换
边界没有稳定对应关系。

本 pilot 检验：

> 如果先学习 routine-to-cross-domain 的 routing 语义方向，再把该方向上的当前状态与同一 trace 的历史
> routine 水平比较，是否能得到更稳定、及时的 sequential signal？

这是已经看过 B1/B2 后提出的 exploratory hypothesis，不是独立 confirmation。

## 2. 数据与双向评价

- B1：只使用 `response_brief_condition=absent` 的 120 条 trace、24 drift；
- B2：使用全部 240 条 trace、35 drift；
- B1→B2：只用 B1 学习模型与阈值，在 B2 评价；
- B2→B1：只用 B2 学习模型与阈值，在 B1 评价；
- 两方向使用完全相同的方法和超参数。

原始 trace、行为标签和 boundary 保持不变。B3 不参与。

## 3. 固定方法

### 3.1 Learned state direction

固定使用 width `w=8` 的 `route_selection` feature：每层 64 个 expert 在最近 8 token 中进入 top-8 的频率，
共 1,024 维。

每条 non-drift trace 从完整序列均匀取最多两个 negative anchors。每条 drift trace：

- 从 `end < boundary` 的区间均匀取最多两个 negative anchors；
- 只在 `boundary + {7, 15, 23}` 取 positive anchors；
- 不 clamp anchor，不把缺少的 anchor 映射到其他位置。

因此每个 positive anchor 的 8-token window 都完全位于人工边界内/后；边界附近的 mixed window 不作为正例。

在 source batch anchors 上做 source-only standardization，再拟合 fixed-penalty ridge least-squares classifier；
penalty 固定为 feature dimension 1,024，不搜索超参数。

### 3.2 Trace-relative score

state model 对每个结束于 `t >= 7` 的窗口产生 `s_t`。对每个 `t >= 15`，历史 baseline 为所有满足
`end <= t - 8` 的 state scores 的 0.5 quantile：

`r_t = s_t - median({s_j: end_j <= t - 8})`

历史集合与当前 8-token window 不重叠；`r_t` 只使用当前及过去信息。baseline 随轨迹因果更新，不使用人工
boundary，也不使用 future token。固定不加 smoothing 或 persistence。

### 3.3 Threshold

用 source batch 的 relative scores 校准。每条 non-drift trace 的完整 relative sequence，以及每条 drift trace
中 `t < boundary` 的 relative prefix，分别作为 negative segment；每段贡献一个最大值。

沿用上一 pilot 的固定规则：`alpha=0.10`，`k=ceil((n+1)*(1-alpha))`，threshold 为第 `min(k,n)` 小的
segment maximum，只有 `r_t > threshold` 才报警。不根据 target batch 调整。

## 4. 固定评价与解释

评价口径与 adjacent-block JSD pilot 相同：

- non-drift trace FAR，及 clean / benign / resisted attack 分层；
- drift pre-boundary FAR；
- boundary +8 / +16 / +32 / full-decode clean recall；
- clean hit latency；
- 每 1,000 negative positions 的 alarm onset；
- warm-up 可达率和 drift domain 分层。

不预设 pass/fail gate。解释规则：

- 两个方向都在可接受误报下出现明显及时 recall，才称为初步 batch-stable evidence；
- 单方向成功只支持 batch-difference diagnosis；
- 相比 v1 或 JSD 的改善是描述性比较，不是新独立显著性检验；
- 失败只否定这个固定的 state-axis + causal-history-median 实例；
- 本轮没有强 text baseline，不能声称信号为 routing 独有；
- 运行后提出的变体必须与本 pilot 结果分开标记。
