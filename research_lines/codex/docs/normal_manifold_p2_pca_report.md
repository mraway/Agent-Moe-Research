# P2：Conditional PCA `T² + Q` 实验报告

日期：2026-09-04（America/Los_Angeles）

状态：exploratory development complete；primary 跨 batch 失稳并触发经验 tail 饱和；P2 不进入 B3

预注册计划：[P2 实验计划](normal_manifold_p2_pca_plan.md)

## 摘要

P2 只使用正常客服 routing，先扣除 workflow-family / causal-age 条件均值，再用 middle 与 late layer 的固定
16维 PCA 子空间计算 `T²` 和 reconstruction residual `Q`。结果在两个 transfer 方向完全不对称：

| Source -> target | Non-drift FAR | Onset +4 | Onset +8 | Onset +16 | Full recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|
| B1 -> B2 | 0/205 | 0/35 | 0/35 | 0/35 | 0/35 | N/A |
| B2 -> B1 | 19/96 = 19.8% | 5/24 = 20.8% | 14/24 = 58.3% | 16/24 = 66.7% | 20/24 = 83.3% | 7 |

B1 -> B2 的零报警不是 routing 完全无差异，而是预注册的 empirical-rarity 变换发生上限饱和：B1 fit 有 472
个 anchors，最大可表示 rarity 为 `log(473)=6.1591`；至少一条正常 calibration trace 已达到这个上限，使
`alpha=0.10` threshold 等于 6.1591。报警条件是严格大于，因此任何 B2 query 都不可能越线。

反方向没有触发同样的绝对封顶，但高 recall 伴随 19.8% overall FAR 和 37.5% resisted-attack FAR。Primary
因此未达到双向 FAR/recall gate。更准确的结论是：**PCA residual 尤其是 `Q` 含有很强的单向区分信号，但
normal subspace 与 tail calibration 在 B1/B2 间严重失配，当前算法不能作为可迁移 detector。**

## 1. 固定方法

P2 使用与 P1 相同的 decode-only 8-token selection signature 和 normal-only source split：

| Direction | Normal fit | Normal calibration | Target traces | Target drift |
|---|---:|---:|---:|---:|
| B1 -> B2 | 59 | 37 | 240 | 35 |
| B2 -> B1 | 128 | 77 | 120 | 24 |

每个 fit trace 最多贡献 8 个 anchors。每个 `(workflow family, age bin)` mean 以16个伪窗口向同 age-bin mean
收缩。Middle L5--L10 与 late L11--L15 各固定保留16个 PCA components。`T²` 和 `Q` 各自按 fit anchors
转换为 empirical upper-tail rarity，二者取最大，再要求连续两个窗口保持高分。每条 normal calibration trace
只贡献一个 full-trace maximum。

所有 drift traces 整条排除于模型与阈值。Target 没有 refit、重新标准化或重新校准。主时间标签为 evidence
onset。

## 2. Primary 结果

### 2.1 B1 -> B2：有限 empirical tail 退化

B1 fit anchor count 为472，所以任何 query 的经验 p-value 最低为 `1/473`，rarity 最高为6.1591。Calibration
normal 中出现 raw `T²` 或 `Q` 超过全部 fit anchors 的窗口后，persistent trace maximum 达到同一 ceiling；
有限样本 threshold 也被推到 ceiling。由于规则要求 `score > threshold`，最终得到：

- 205 条 non-drift：0 alarm；
- 35 条 drift：0 pre-onset alarm、0 post-onset alarm；
- 所有 domain、channel 和 latency horizon：0 recall。

这是一个真实的方法失败，不是把 strict `>` 改为 `>=` 就能解决：若允许等于 ceiling 报警，所有超出 fit
support 的窗口会形成大量 ties，错误率无法再由 rarity 大小排序。需要连续 tail extrapolation、raw-score
calibration 或更大的独立 normal bank，但这些都属于新的算法定义。

### 2.2 B2 -> B1：高 recall 与高 normal shift 同时出现

反方向 threshold 为5.3230，没有等于 `log(1025)=6.9324` 的 fit-rarity ceiling。它得到及时、多 domain 的
正向信号，但 normal FAR 超过 gate：

| Target B1 normal arm | False alarms |
|---|---:|
| Clean | 5/40 = 12.5% |
| Benign control | 8/40 = 20.0% |
| Resisted attack | 6/16 = 37.5% |
| Overall | 19/96 = 19.8% |

Onset+8 的14个 clean hits 覆盖 cooking、fiction、general knowledge、mathematics 和 poetry；programming、
legal analysis、travel planning 为零。三个 drift 在 onset 前报警。这个方向说明 PCA score 不是纯噪声，但
它把大量 B1 正常响应、尤其攻击拒绝，也判成 B2 normal subspace 之外。

## 3. `T²` / `Q` 分解与固定 variants

| Variant | B1 -> B2 FAR / +8 / +16 / full | B2 -> B1 FAR / +8 / +16 / full |
|---|---|---|
| Primary conditional `max(T²,Q)` | 0.0% / 0.0% / 0.0% / 0.0% | 19.8% / 58.3% / 66.7% / 83.3% |
| `T²` only | 2.9% / 11.4% / 14.3% / 37.1% | 16.7% / 16.7% / 25.0% / 66.7% |
| `Q` only | 0.0% / 0.0% / 0.0% / 0.0% | 17.7% / 62.5% / 66.7% / 83.3% |
| Unconditional `max(T²,Q)` | 0.0% / 0.0% / 0.0% / 0.0% | 20.8% / 58.3% / 66.7% / 83.3% |
| Middle only | 0.0% / 0.0% / 0.0% / 0.0% | 15.6% / 50.0% / 70.8% / 87.5% |
| Late only | 0.0% / 0.0% / 0.0% / 0.0% | 18.8% / 58.3% / 66.7% / 87.5% |
| Full probability | 0.0% / 0.0% / 0.0% / 0.0% | 11.5% / 54.2% / 83.3% / 91.7% |
| Shuffled expert timing | 0.0% / 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% / 0.0% |
| Clean+benign-only fit | 0.0% / 0.0% / 0.0% / 0.0% | 24.0% / 54.2% / 66.7% / 83.3% |

主要诊断是：

1. `Q` 驱动了反方向的大部分及时 recall，也驱动了 forward 的 ceiling。它说明不同 batch 之间“正常 PCA
   无法重建的方向”本身就在变化；
2. `T²` only 避免了 B1 -> B2 的完全封死，但两个方向 +8 只有11.4%和16.7%，且反方向 FAR 仍为16.7%；
3. Conditional 与 unconditional 几乎相同，说明当前粗粒度 workflow/age mean 没有解决主要 batch shift；
4. Probability variant 在 B2 -> B1 单方向达到 FAR 11.5%、+8 recall 54.2%、median latency 7，但 forward
   仍完全饱和，不能算复制；
5. Shuffled control 两向都零，部分符合 expert timing 有信息的预期，但它也受 tail ceiling 影响，不能据此量化
   expert identity 的独立贡献；
6. 排除 resisted attacks 的 fit 没有改善跨批稳定性，反而使 B2 -> B1 FAR 升至24.0%。

## 4. P2 的研究含义

P2 没有证明 normal-only PCA detector 可用，但给出了比“零 recall”更具体的诊断：

- 静态 normal subspace 对 source normal 的覆盖不足，fit-window empirical CDF 不适合直接承担 trace-level
  open-set tail；
- B1/B2 的正常 residual distribution 有明显 batch shift，尤其体现在子空间外 `Q`；
- 一旦阈值没有封顶，PCA residual 与 drift onset 可以非常同步，但同一 residual 也会击中大量 target normal；
- 因此不能把单向高 recall 解释为任务流形已经成立。

可能的新方法包括对 raw `T²/Q` 做独立 calibration、使用 generalized Pareto tail 或扩大 normal reference。
但本阶段不会对 P2 结果后修补；这些改动既会改变阈值排序，也可能改变 FAR，应作为未来独立 proposal。

下一步按原顺序进入 P3。P3 的 CUSUM 累积多个 block surprise，虽然也使用 empirical tail，但累计分数没有 P2
这种单窗口硬 ceiling；更重要的是，它检验正常 transition prediction，而不是第三次尝试静态状态边界。

## 5. 实现与审计

- plan commit：`17a1f87`，先于 target metric 计算；
- implementation：`scripts/run_normal_manifold_p2_pca.py`；
- shared cache/metadata：`src/phase_a/normal_manifold.py`；
- normal-manifold unit tests：8/8 通过；
- result：`artifacts/agent_v2/normal_manifold_p2_pca/result.json`；
- result size：82,802,923 bytes；
- result SHA-256：`62d94876ecc3f6b39bcc2488147f566a69e9866b81f410c9bc6dd17e99cb5dcb`；
- sample-index hashes 与固定 trace counts 在运行时重新检查；compact routing cache 来自 P1 已验证的360条原始
  trace；
- 原始 trace、labels、boundaries 和 P1 artifacts 未修改；B3 未使用。
