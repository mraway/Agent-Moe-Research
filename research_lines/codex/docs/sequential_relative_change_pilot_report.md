# Sequential Relative-Change Pilot：结果报告

日期：2026-09-04（America/Los_Angeles）

状态：exploratory development complete；固定的无监督 adjacent-block JSD 不支持继续作为主 detector

## 摘要

本 pilot 检验一个不学习域外语义方向的局部 change detector：每 8 个 output token 统计一次逐层 top-8
expert-selection 分布，并用相邻两个 8-token block 的平均 Jensen–Shannon divergence（JSD）作为逐 token
score。阈值只由另一批数据的 negative-segment maxima 按 `alpha=0.10` 的有限样本 order statistic 校准。

结果为清晰的负面结果：

- B1 校准、B2 测试时，non-drift FAR 为 `10/205 = 4.9%`，但 boundary +16 clean recall 只有
  `1/35 = 2.9%`，full-decode recall 为 `7/35 = 20.0%`；
- B2 校准、B1 测试时，non-drift FAR 为 `15/96 = 15.6%`，boundary +16 recall 为
  `2/24 = 8.3%`，full-decode recall 为 `9/24 = 37.5%`；
- 两个方向的 +16 理论可达率均为 100%，及时 recall 低不是 detector warm-up 所致；
- clean hit 的中位延迟分别为 51 和 55 token，不符合任务切换后及时报警的目标。

因此，无方向的局部 routing 变化幅度不能解释此前 v1 classifier 的强 post-hoc 排序。更合理的下一假设是：
routing 中存在与域外语义相关的**方向性变化**，需要学习该方向，但 runtime score 应相对同一 trace 或 routine
baseline 计算，而不是依赖不稳定的全局绝对阈值。

## 1. 固定方法与数据

方法在提交 `01d76de` 中、看到本轮数值前固定。完整定义见
`docs/sequential_relative_change_pilot_plan.md`。

- B1 cohort：brief-absent 40 scenarios、120 traces、24 drift；
- B2 cohort：80 scenarios、240 traces、35 drift；
- block width：8 token；
- 最早 score：output token index 15；
- feature：逐层 top-8 expert-selection categorical distribution；
- score：相邻 block 的 per-layer JSD 再跨 16 层取均值；
- threshold：calibration negative-segment maxima 的有限样本上尾 order statistic；
- alarm：单个 score 严格大于 threshold，无 smoothing 或 persistence；
- 两个方向使用同一方法，不根据 test batch 调参。

两批数据在此前实验中都已被研究者查看，因此这是 post-hoc exploratory development，不是 confirmation。

## 2. Primary 结果

| Calibration → Test | Threshold | Non-drift FAR | Pre-boundary FAR | Recall +8 | Recall +16 | Recall +32 | Final recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 → B2 | 0.4550 | 10/205 (4.9%) | 3/27 (11.1%) | 1/35 (2.9%) | 1/35 (2.9%) | 2/35 (5.7%) | 7/35 (20.0%) | 51 |
| B2 → B1 | 0.4429 | 15/96 (15.6%) | 4/18 (22.2%) | 1/24 (4.2%) | 2/24 (8.3%) | 2/24 (8.3%) | 9/24 (37.5%) | 55 |

Calibration 本身按声明规则工作：

- B1 有 114 个可用 negative segments，rank 104，10 个 maxima 严格超过 threshold，比例 8.8%；
- B2 有 232 个可用 negative segments，rank 210，22 个 maxima 严格超过 threshold，比例 9.5%。

测试侧每 1,000 个 negative eligible positions 的 alarm onset 分别为 0.964（B2）和 2.009（B1）。B2 的
non-drift 分层 FAR 为 clean 8.8%、benign 1.3%、resisted attack 4.4%；B1 分别为 15.0%、12.5%、25.0%。
这说明 threshold 的 batch transfer 也不稳定，但它不是及时 recall 极低的唯一原因。

## 3. Warm-up 与 domain 检查

JSD 最早在 token index 15 评分。+8 理论可达率为 B2 32/35（91.4%）、B1 22/24（91.7%）；+16 和 +32
在两批均为 100%。因此 +16 recall 低不能归因于没有报警机会。

B1→B2 的 +16 唯一 clean hit 来自 fiction；cooking、general knowledge、legal、mathematics、poetry、
programming 和 travel 均为零。B2→B1 的两个 +16 clean hits 来自 fiction 和 legal。低及时 recall 广泛存在，
不是某一个 target domain 单独拖累。

## 4. 事后边界响应诊断

以下数值在 primary 结果后计算，只用于解释失败，不能作为另一个 detector 的性能：

| Test batch | `post max > pre max` | `boundary..+16 max > pre max` | 局部增量中位数 | post-max vs non-drift-max AUROC |
|---|---:|---:|---:|---:|
| B2 | 19/27 | 14/27 | +0.00026 | 0.744 |
| B1 | 12/18 | 8/18 | -0.01541 | 0.681 |

这里的 pre/post 比较只包含有可用 pre-boundary JSD score 的 drift。即使允许查看完整 post-boundary future，
JSD post-max 的区分度也远低于 v1 frozen semantic score 在 B2 上的 post-hoc AUROC 0.982。更关键的是，边界后
16 token 内的 JSD 增量中位数接近零或为负，说明任务切换并不通常表现为比 routine generation 更剧烈的无方向
routing 跳变。

## 5. 结论与下一假设

本 pilot 否定的是一个具体而重要的简化解释：

> “只要 agent 切换任务，expert selection 的任意局部分布变化都会显著增大。”

数据不支持该解释。正常客服输出自身也包含局部语义和措辞变化；简单 JSD 把这些变化与跨领域切换一起测量，
既丢失了 v1 学到的语义方向，又产生了 batch-dependent tail。

这不会否定 relative detection 整体。它把下一步问题收窄为：能否从完全位于边界后的窗口与 routine 窗口中
学习一个跨领域方向，再对该方向上的**相对位移**而非绝对位置报警。下一实验应避免 v1 的混合 positive 窗口，
并保持 trace-level calibration 与双向 batch transfer 检查。

## 6. 实现与运行审计

- 原始 trace、标签、boundary 和 v1 artifacts 未修改；
- 480 条原始 trace 均通过既有 loader 的 routing validation；
- 新增因果 JSD 与 finite-sample threshold 单元测试，10/10 通过；
- 首次全量运行因同时保留 B1/B2 raw routing tensors 造成严重内存压力，在无结果产物时人工中断；
- 改为逐 batch 评分、只保留 score 与元数据后重跑；
- 第一次优化后运行在计算指标前因已释放变量仍被元数据计数引用而失败，没有生成结果；
- 修正为读取 retained score 数量后完整重跑，固定 detector 与评价逻辑均未改变；
- 结果 artifact：`artifacts/agent_v2/sequential_relative_change_pilot.json`；
- artifact SHA-256：`da68e95e7b4d63558ebac95b5edca16cca2ba7aef6a5235cff46b034d8cf71dd`。
