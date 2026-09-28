# P3：Normal Route Forecast + CUSUM 实验报告

日期：2026-09-04（America/Los_Angeles）

状态：exploratory development complete；primary 只能晚期检测，未达到 onset timing gate；P3 不进入 B3

预注册计划：[P3 实验计划](normal_manifold_p3_forecast_plan.md)

## 摘要

P3 只用正常客服 routing 学习4-token block 的低维状态和下一 block 预测器，再对预测残差的 normal-tail
surprise 做 CUSUM。它在两个 transfer 方向都保持了较低的 non-drift FAR，并最终检测到约一半 drift；但是
报警发生得很晚，几乎没有 onset 附近能力：

| Source -> target | Non-drift FAR | Onset +4 | Onset +8 | Onset +16 | Full recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|
| B1 -> B2 | 3/205 = 1.5% | 0/35 | 0/35 | 0/35 | 17/35 = 48.6% | 55 |
| B2 -> B1 | 8/96 = 8.3% | 0/24 | 1/24 = 4.2% | 1/24 = 4.2% | 13/24 = 54.2% | 71 |

`onset+8` 的理论可达率分别为94.3%和91.7%，不能解释接近零的 recall。结果表明：**持续域外生成最终会让
normal predictor 累积大量 surprise，但当前低容量 forecasting residual 没有在任务切换发生时立即突升。**
P3 未达到预声明的 timely-recall gate。

## 1. 固定方法

Decode 从 token 0 开始切成不重叠4-token blocks。每个 block 的 top-8 selection distribution 逐层归一化并
做平方根变换；middle L5--L10 与 late L11--L15 分别以 trace-equal weighting 拟合16维 PCA，形成32维状态。

从第三个完整 block 开始，normal-only ridge 使用：

```text
[previous state, previous state delta, workflow-family one-hot, causal-age one-hot]
```

预测当前状态。32维 state 的 predictor input 为72维，ridge penalty 固定72。B1/B2 source 分别提供1,627和
3,025个 normal forecast transitions。Residual 以 weighted median/MAD 做 diagonal robust scaling，再转成
fit-normal empirical surprise；CUSUM 每个 block 累积 `surprise - 1`，低于0时复位。

| Direction | Normal fit | Normal calibration | Target traces | Earliest score |
|---|---:|---:|---:|---:|
| B1 -> B2 | 59 | 37 | 240 | token 11 |
| B2 -> B1 | 128 | 77 | 120 | token 11 |

所有 drift trace 整条排除于 state PCA、predictor、residual tail 和 threshold。每条 calibration normal trace
只贡献一个 full-trace maximum；target 不 refit。Evidence onset 是主时间标签。

## 2. Primary 结果

### 2.1 FAR 控制稳定，但阈值要求长时间累计

| Target | Clean FAR | Benign FAR | Resisted-attack FAR | Overall FAR | Alarm onsets / 1,000 endpoints |
|---|---:|---:|---:|---:|---:|
| B2 | 2/80 = 2.5% | 0/80 | 1/45 = 2.2% | 3/205 = 1.5% | 0.65 |
| B1 | 3/40 = 7.5% | 4/40 = 10.0% | 1/16 = 6.3% | 8/96 = 8.3% | 3.80 |

两个方向 calibration thresholds 分别为32.46和16.16，target FAR 都不超过 source `alpha=0.10`。Primary
没有在任何 drift 的 evidence onset 前报警。低 FAR 不是攻击拒绝被排除出 normal model 的产物：resisted
attack FAR 与 clean/benign 相近。

然而，达到如此高的 CUSUM threshold 通常需要许多异常 blocks。Full clean-hit recall 为48.6%和54.2%，但
median latency 是55和71 tokens；这已经不是 onset detection，而是对长篇域外输出的迟到确认。

### 2.2 Domain 结果进一步说明是“持续累积”

B1 -> B2 在 +8 的八个 domain 全为零；full hits 主要包括 poetry 5/5、fiction 4/5、travel 3/4，以及部分
cooking/legal/general knowledge，mathematics 与 programming 均为零。B2 -> B1 的唯一 +8 hit 是 legal；full
hits 覆盖六个 domain，但 poetry 与 programming 为零。

这种 pattern 更像某些输出风格在持续生成时不断积累 prediction surprise，而不是统一的任务切换事件响应。

## 3. 固定 variants

| Variant | B1 -> B2 FAR / +8 / +16 / full / latency | B2 -> B1 FAR / +8 / +16 / full / latency |
|---|---|---|
| Primary selection CUSUM | 1.5% / 0.0% / 0.0% / 48.6% / 55 | 8.3% / 4.2% / 4.2% / 54.2% / 71 |
| Single-block surprise | 8.3% / 5.7% / 8.6% / 28.6% / 22.5 | 15.6% / 4.2% / 8.3% / 37.5% / 46 |
| Middle-only CUSUM | 2.0% / 0.0% / 2.9% / 42.9% / 33 | 5.2% / 0.0% / 4.2% / 45.8% / 73 |
| Late-only CUSUM | 4.9% / 2.9% / 2.9% / 48.6% / 52 | 6.3% / 0.0% / 0.0% / 54.2% / 62 |
| Full-probability CUSUM | 6.8% / 2.9% / 2.9% / 74.3% / 29.5 | 11.5% / 0.0% / 8.3% / 91.7% / 34.5 |
| Shuffled expert timing | 5.9% / 0.0% / 0.0% / 20.0% / 34 | 7.3% / 0.0% / 4.2% / 16.7% / 99.5 |
| Clean+benign-only fit | 2.4% / 0.0% / 2.9% / 48.6% / 54 | 8.3% / 4.2% / 8.3% / 58.3% / 69.5 |

解释如下：

1. Single-block surprise 缩短了一部分 latency，但及时 recall 仍低，而且 B2 -> B1 FAR 达15.6%；问题不只是
   CUSUM 累积得太慢，单步 predictor residual 在 onset 也不强；
2. Middle-only 在一个方向把 latency 降到33，但 +8 仍为0；late-only 也没有及时信号。P1 中 middle layer
   的相对优势没有转化成可预测的 transition anomaly；
3. Full probability 大幅提高 full recall 至74.3%和91.7%，且 FAR 尚可，但 median latency 仍为29.5和34.5，
   +8 分别只有2.9%和0。这是有价值的晚期状态信号，不是当前目标要求的 onset detector；
4. 打乱 token-to-expert timing 后 full recall 降至20.0%和16.7%，说明真实时间顺序对长期 signal 有明显贡献；
5. Fit 是否包含 resisted attacks 对 primary 影响不大，再次说明失败不是 normal coverage 中缺少攻击拒绝。

## 4. 三个 normal-only proposal 的横向结论

| Primary | B1 -> B2 FAR / +8 / full / latency | B2 -> B1 FAR / +8 / full / latency | 主要失败 |
|---|---|---|---|
| P1 kNN manifold | 3.4% / 0.0% / 45.7% / 19 | 9.4% / 12.5% / 45.8% / 12 | 静态局部距离变异常太晚 |
| P2 conditional PCA | 0.0% / 0.0% / 0.0% / N/A | 19.8% / 58.3% / 83.3% / 7 | empirical-tail 饱和 + normal subspace batch shift |
| P3 forecast CUSUM | 1.5% / 0.0% / 48.6% / 55 | 8.3% / 4.2% / 54.2% / 71 | residual 需长时间累积，onset 不突变 |

三个 primary 都没有达到双向 research gate，因此当前不应生成 B3，也不应声称 normal-only routing manifold
已经能够形成及时在线 detector。

但三个结果共同保留了一个较窄的正面发现：P1/P3 只用正常数据，在合理 FAR 下最终都能识别约一半 drift；
P3 probability variant 更能识别大部分长篇 drift。结合已有 matched onset study，这说明 routing signal 确实
存在，困难集中在 **normal-only decision boundary 的跨批校准与 onset timing**，而不是完整域外输出完全不可分。

与此前 supervised directional-relative pilot 对比也很关键：后者在两个方向的 +8 recall 约43%--46%，median
latency 4--5，但必须用 drift positives 学习语义方向。当前三个 normal-only 方案表明，这个及时方向没有通过
普通 one-class geometry 或低容量 forecasting 自动恢复出来。

因此下一研究选择不应是继续微调这三个阈值，而应在以下两条路线间重新判断：

1. 接受少量、多样的已知跨领域正例，用它们学习通用“离开客服域”的方向，再研究对 unseen domains 的开放集
   泛化；
2. 保持严格 normal-only，但扩充正常任务覆盖、使用更稳定的 raw-score/tail calibration，并显式学习正常
   semantic phase，而不是把 workflow/age 当作粗代理。

这两个方向都需要新的 proposal；它们不是本轮任一失败 primary 的事后修补。

## 5. 实现与审计

- plan commit：`cd596fb`，早于 target metric 计算；
- implementation：`scripts/run_normal_manifold_p3_forecast.py`；
- shared cache/metadata：`src/phase_a/normal_manifold.py`；
- normal-manifold unit tests：10/10 通过；
- result：`artifacts/agent_v2/normal_manifold_p3_forecast/result.json`；
- result size：11,594,477 bytes；
- result SHA-256：`f8a0c7f95ff8d49df8244e98442757c1d60250bd8e9c664d6034a0be17093ecd`；
- sample-index hashes 与360条 trace metadata 在运行时检查；routing cache 复用 P1 已逐条验证的原始数据；
- P1/P2 artifacts、原始 traces、labels 与 boundaries 未修改；B3 未使用。
