# P1-LF：无任务标签的单 Agent 正常流形实验报告

日期：2026-09-04（America/Los_Angeles）

状态：exploratory development complete；表示层面获得正信号，当前在线 detector 尚不进入 B3

实验设计：[P1-LF 计划](normal_manifold_p1_label_free_plan.md)

## 摘要

P1-LF 完全移除了原 P1 检测器中的 workflow-family 条件。所有正常订单、退款、工单和知识问答窗口共同组成
一个 per-agent reference bank，query 只使用截至当前 token 的 decode routing 和当前 decode 位置。

实验得到一个重要的正面结果：**不提供任务类别时，跨领域 drift 仍然显著离开该 Agent 的正常路由集合。**

- B1 -> B2 的正常窗口标准化距离均值为 `0.070`，drift-onset 后为 `3.137`；
- B2 -> B1 分别为 `0.043` 与 `2.984`；
- 按 trace 等权后，35/35 与 24/24 条 drift 的 post-onset 平均距离都高于对应 target 正常 trace 的中位数；
- 更严格地，32/35 与 22/24 条 drift 的 post-onset 平均距离超过正常 trace-mean 的第 95 百分位；
- 无标签 top-5 近邻只有 `52.7%` / `54.2%` 来自相同 workflow family，说明正常邻域有自然结构，但没有
  退化为由人工 family 隔开的三个孤岛。

但“窗口可分”还没有自动变成稳定的在线报警器。无记忆 `endpoint_z` 在 B1 -> B2 达到 `3.9%` FAR、
`28.6%` onset+8 recall、`74.3%` full recall；反向达到 `21.9%` FAR、`50.0%` onset+8 recall、`79.2%`
full recall。也就是说，反向 source calibration 对 B1 normal tail 偏松。

预先固定的 primary leaky CUSUM 把反向 FAR 压到 `14.6%`，但在 B1 -> B2 被少数持续且正常的 source
calibration 模式抬高阈值，onset+8 recall 只有 `2.9%`。五种方法均未同时达到两个方向的及时性和 FAR gate。

所以本轮最准确的结论是：

> workflow 标签不是现有正信号的必要条件，单 Agent 多模态正常流形值得继续研究；当前瓶颈已经从 P1 的有限
> cell ceiling，转移到跨 batch 的正常分数校准和如何聚合持续证据。

![P1-LF 主要结果](../artifacts/agent_v2/normal_manifold_p1_label_free/p1_label_free_summary.png)

## 1. 检测器实际使用了什么

### 1.1 不使用 workflow 标签

P1-LF 的 score path 只读取：

1. final-generation decode 的逐 token top-8 expert IDs；
2. causal decode endpoint。

以下字段不会进入 reference pool、距离、位置校正、历史累计或 threshold：workflow、workflow family、攻击渠道、
目标域、token ID/text、drift label、evidence onset。workflow family 只在 score 全部计算完成后用于近邻组成和
误报切片。

两个方向分别使用：

| Source -> target | Normal fit traces | Normal calibration traces | Global normal anchors |
|---|---:|---:|---:|
| B1 -> B2 | 59 | 37 | 472 |
| B2 -> B1 | 128 | 77 | 1,024 |

每条 fit trace 均匀提供 8 个 causal window anchors。任何 query 都在全部 anchors 中寻找第 5 近邻，不再先询问
它属于哪种 workflow。

### 1.2 取消经验 cell ceiling

路由 signature 和距离保持与 P1 一致：causal width-8 selection frequency、逐层平方根、middle L5--L10 与
late L11--L15 的等权 Hellinger distance。

P1 将 raw distance 放入 workflow-family/age cell 的经验 tail，因此 rarity 最大只能达到 `log(N+1)`。P1-LF
不再计算离散 p-value。它从全局 fit reference score 中选择 decode age 最接近的 128 个值，得到连续位置上的
local median 和 robust scale：

```text
z_t = (raw_t - local_median_t) / local_scale_t
```

`z_t` 没有由 reference 数量决定的理论上限。age 仍被使用，因为线上天然知道当前 decode 位置；但 age 是连续
局部校正变量，不形成硬 cell。

### 1.3 五种因果证据流

| 方法 | 在线状态 | 用意 |
|---|---|---|
| `endpoint_z` | 当前 `z_t` | 无记忆对照 |
| `rolling_mean_4` | 最近 4 个 z 的均值 | 短时持续性 |
| `rolling_mean_8` | 最近 8 个 z 的均值 | 更强平滑 |
| `cusum_0_5` | `max(0,C[t-1]+z[t]-0.5)` | 不衰减累计面积 |
| `leaky_cusum_0_95_0_5` | `max(0,.95*C[t-1]+z[t]-0.5)` | 有限记忆累计；本轮 primary |

每种状态仍用 source normal-calibration trace 的 `max_t state_t` 校准“完整 trace 是否曾报警”。因此没有取消
trace maximum；改变的是 maximum 之前的在线状态是否保存历史信息。

## 2. 完整报警结果

### 2.1 B1 fit/calibration -> B2 target

| 方法 | Normal FAR | Pre-onset alarm | +4 recall | +8 recall | +16 recall | Full recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| Endpoint z | 8/205 = 3.9% | 3/35 | 11.4% | 28.6% | 42.9% | 74.3% | 14 |
| Rolling mean 4 | 11/205 = 5.4% | 3/35 | 8.6% | 25.7% | 42.9% | 74.3% | 9 |
| Rolling mean 8 | 7/205 = 3.4% | 2/35 | 0.0% | 17.1% | 45.7% | 77.1% | 11 |
| CUSUM | 4/205 = 2.0% | 0/35 | 0.0% | 0.0% | 8.6% | 77.1% | 40 |
| **Leaky CUSUM primary** | **1/205 = 0.5%** | **2/35** | **0.0%** | **2.9%** | **17.1%** | **65.7%** | **21** |

### 2.2 B2 fit/calibration -> B1 target

| 方法 | Normal FAR | Pre-onset alarm | +4 recall | +8 recall | +16 recall | Full recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| Endpoint z | 21/96 = 21.9% | 3/24 | 29.2% | 50.0% | 62.5% | 79.2% | 6 |
| Rolling mean 4 | 20/96 = 20.8% | 4/24 | 25.0% | 41.7% | 62.5% | 79.2% | 6 |
| Rolling mean 8 | 19/96 = 19.8% | 2/24 | 8.3% | 41.7% | 70.8% | 87.5% | 9 |
| CUSUM | 17/96 = 17.7% | 3/24 | 4.2% | 16.7% | 66.7% | 83.3% | 12 |
| **Leaky CUSUM primary** | **14/96 = 14.6%** | **3/24** | **0.0%** | **33.3%** | **66.7%** | **83.3%** | **11** |

Primary 没有达到旧 normal-manifold gate：B1 -> B2 的 +8 远低于 35%，两个方向 latency 也都高于 8。
其余方法同样没有在两个方向同时做到 FAR 不超过 15% 且 +8 recall 至少 35%。本实验不选择一个事后表现较好
的方法重命名为 primary。

## 3. 不使用标签后，流形信号还在吗

答案是明确的“在”，而且比报警指标更清晰。

| Direction / phase | Endpoint 数 | Mean z | Median z | P95 z |
|---|---:|---:|---:|---:|
| B1 -> B2 normal | 18,783 | 0.070 | 0.133 | 2.072 |
| B1 -> B2 drift pre-onset | 1,591 | 0.579 | 0.484 | 2.822 |
| B1 -> B2 drift post-onset | 4,168 | 3.137 | 3.209 | 5.955 |
| B2 -> B1 normal | 10,713 | 0.043 | 0.064 | 1.782 |
| B2 -> B1 drift pre-onset | 1,054 | 0.208 | 0.214 | 1.795 |
| B2 -> B1 drift post-onset | 2,873 | 2.984 | 3.328 | 5.258 |

这些是 endpoint-weighted 数字。为避免长回复主导结论，我们又先在每条 trace 内求 phase mean，再跨 trace 等权：

- B1 -> B2 normal trace mean 平均为 `-0.077`，35 条 drift 的 post-onset trace mean 平均为 `3.156`；
- B2 -> B1 对应为 `0.032` 与 `2.821`；
- 32/35 和 22/24 条 drift 的 post-onset trace mean 高于 target normal trace mean 的第 95 百分位。

这说明结果不是由少数超长 drift 输出或单个 peak 驱动。路由窗口在 drift 展开后持续远离正常集合，是当前数据
中相当稳定的现象。

pre-onset 分布高于 normal，尤其 B1 -> B2 为 `0.579`。这可能混合了三种因素：攻击上下文造成的合法响应变化、
人工 evidence onset 晚于真实 semantic commitment，以及 normal bank 覆盖不足。本轮按冻结 onset 原样评价，
没有重新标注以改善指标。

## 4. 无监督近邻有没有偷偷学习 workflow

| Direction | 随机从 bank 抽取时同 family 概率 | 实际 top-5 同 family 比例 | Lift |
|---|---:|---:|---:|
| B1 -> B2 | 38.5% | 52.7% | 1.37x |
| B2 -> B1 | 41.6% | 54.2% | 1.30x |

近邻确实更倾向相同 family，说明 routing 空间包含正常任务模式；但仍有约 46%--47% 的近邻跨 family。检测器
无需先把 query 分类到一个人工 cell，便能在正常模式的并集中找到局部支持。

更直接的证据来自 B1 -> B2 endpoint FAR：knowledge-QA 为 1/18，status+knowledge 为 3/104，status-only 为
4/83。旧 P1 的 B2 全部 alarm activity 都集中在 status-only；P1-LF 在三个 family 都有有限而非结构性关闭的
报警支持。反向 endpoint FAR 在三个 family 都偏高，因此剩余问题是整体校准迁移，而不是某个 family 被分数上限
封死。

## 5. 与旧 P1 的直接比较

| Direction / 方法 | FAR | +8 | +16 | Full | Median latency |
|---|---:|---:|---:|---:|---:|
| B1 -> B2 / old P1 | 3.4% | 0.0% | 20.0% | 45.7% | 19 |
| B1 -> B2 / label-free endpoint z | 3.9% | 28.6% | 42.9% | 74.3% | 14 |
| B2 -> B1 / old P1 | 9.4% | 12.5% | 25.0% | 45.8% | 12 |
| B2 -> B1 / label-free endpoint z | 21.9% | 50.0% | 62.5% | 79.2% | 6 |

在 B1 -> B2，几乎相同 FAR 下，label-free endpoint 把 full recall 从 45.7% 提高到 74.3%，并第一次在 +8
看到 10/35 个 clean hits。旧 P1 的 13 条零报警漏报中，endpoint z 为 8 条产生了新的 clean post-onset
alarm，4 条仍漏报，1 条变成 pre-onset alarm；其中 2 条在 onset+8 内命中。这与先前“cell ceiling 是大量漏报
直接原因”的逐条审计一致。

反向 recall 也明显上升，但 FAR 同时从 9.4% 升到 21.9%，不能只报告收益。它暴露了新的主要失败模式。

## 6. 为什么校准仍不稳定

### 6.1 正常 tail 存在方向性 batch shift

在 B1 -> B2 中，source normal-calibration endpoint z 的 P95 为 `2.449`，target normal 为 `2.072`；source
更宽，所以 target FAR 较低。反向时 source P95 为 `1.443`，target normal 为 `1.782`；source threshold 对较宽
的 B1 normal tail 偏松，endpoint FAR 上升到 21.9%。

这不是 workflow label 缺失本身造成的结构性无解，因为 B1 -> B2 三个 family 都工作；它说明当前“fit bank
局部 median/IQR + 另一批 trace-max threshold”的尺度还不能完全跨 batch 对齐。

### 6.2 长期累计会放大少数正常模式

B1 source calibration 中，leaky-CUSUM 最大值最高的三个 normal traces 全来自同一
`b1-f4-048-order_and_knowledge-dialogue-scene` pair group，最大值为 `53.31`、`51.42`、`47.47`。有限样本
10% order statistic 恰好把第三个值 `47.47` 设为 threshold。这组三个 arm 含持续、非常规但被行为标签视为
non-drift 的 decode 模式；其中两个生成到 length stop，并有长段重复空白。它们不必被事后删除：对“只学习这个
Agent 的正常行为”而言，这正是累计器必须应对的正常异质性。

普通 CUSUM 更极端：同一 pair 的 calibration maxima 达到 `279.32`、`262.00`、`150.85`，造成 B1 -> B2
median latency 40。Leaky CUSUM 限制了长期累积，却仍无法消除由一个持续 normal mode 决定阈值的问题。

因此，先前“单点 maximum 丢失持续信息”的担心是对的，但本轮数据也给出了另一半答案：**无条件地累计所有正
偏差并不更好。** 历史统计必须区分“持续但正常的另一模式”和“持续离开全部正常模式”，或者采用对单个校准
scenario 更稳健的风险聚合。

## 7. 研究判断与下一步

本轮支持继续研究，但不支持冻结 detector：

1. **核心命题获得加强。** 人工 workflow-family label 不是路由分离的必要条件；一个固定 Agent 的正常 routing
   并集已经能对 post-onset drift 给出强距离信号。
2. **旧 P1 的主要实现缺陷被实质修复。** 分数不再受小 cell ceiling 限制，13 条旧漏报中多数恢复为可报警。
3. **在线决策仍未解决。** 双向结果揭示正常 tail shift；primary 累计器在一个方向过于保守，其他短期方法在另
   一方向误报过高。
4. **暂不进入 B3。** B1/B2 已是开发数据，下一轮应只在现有数据上研究 score calibration 与局部历史结构；形成
   稳定规则后再冻结并申请新数据。

下一步不应重新引入任务标签，也不应立即搜索大量模型。最有信息量的是 zoom in 两类 normal case：

- B1 target 中触发 endpoint false alarm 的 21 条正常 trace，检查它们是统一 batch scale shift，还是几个未被
  reference bank 覆盖的正常局部模式；
- B1 source calibration 中抬高 CUSUM threshold 的 pair，逐 token 对比其 raw distance、age correction 和
  邻居来源，确认问题发生在几何覆盖还是累计器。

完成这两个观察后，再决定下一版是改进正常 score 的可迁移校准、采用局部密度/邻居可信度，还是使用有限窗口
的证据形状而非无界 CUSUM。

这项 zoom-in 已完成，结果见
[P1-LF 正常误报与 CUSUM 校准异常审计](normal_manifold_p1_label_free_zoom_report.md)。审计将反向误报拆成
decode-horizon mismatch、128+ late-tail shift 与合法域外语义 mention 三部分，并逐 token 定位了正向 CUSUM
阈值被一个 matched pair 控制的原因。

## 8. 复现与产物

```bash
.venv/bin/python scripts/run_normal_manifold_p1_label_free.py
MPLCONFIGDIR=/tmp/agent-moe-matplotlib \
  .venv/bin/python scripts/plot_normal_manifold_p1_label_free.py
.venv/bin/python -m unittest tests.test_normal_manifold_label_free -v
```

- result：`artifacts/agent_v2/normal_manifold_p1_label_free/result.json`，17 MB，SHA-256
  `cda064cecb220c39336bdd1e9b846a347d1a307b379a0cc6064113fa63a45f29`；
- figure：`p1_label_free_summary.png`；
- tables：`method_metrics.csv`、`score_phase_summary.csv`；
- 新增 label-free 单测 5 项；完整测试集 121/121 通过；
- 原 P1 artifacts、360 条原始 traces、路由 cache、行为标签和 boundary 均未修改；
- B3 未生成或读取。
