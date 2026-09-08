# Normal Manifold Absolute-Age-Free Ablation：实验报告

日期：2026-09-04（America/Los_Angeles）

状态：adaptive development ablation complete；H1 获得强支持，H4 获得混合证据；没有冻结新 detector

预注册计划：[Absolute-age-free ablation 计划](normal_manifold_age_free_ablation_plan.md)

## 摘要

本实验完整移除了 P1-LF 的 absolute decode-age normalization，同时保持 width-8 routing representation、
middle/late layer geometry、正常 fit/calibration folds 和第5近邻距离不变。核心结果非常清晰：

> **P1-LF 的 normal-vs-cross-domain representation signal 不依赖 absolute decode age。**

最少假设的 raw global kNN distance 在两个 transfer 方向都保留了近乎完整的 trace-level 分离：

| Source -> target | Normal trace mean | Fully-post drift trace mean | Drift mean > normal q95 | Paired post > pre | AUROC / AP |
|---|---:|---:|---:|---:|---:|
| B1 -> B2 | 0.4088 | 0.5549 | 34/35 | 29/29 | 0.995 / 0.974 |
| B2 -> B1 | 0.4014 | 0.5511 | 24/24 | 17/18 | 0.997 / 0.987 |

这比 position-conditioned 结果更重要：一个跨领域窗口平均离开全局 normal support 的现象，在不假设“不同
trace 的 token 100 处于相似生成阶段”时仍然成立。

但这仍未自动得到在线 detector。沿用 legacy full-trace endpoint-maximum calibration 时，raw score 的
B1 -> B2 FAR 为 `2/205 = 1.0%`，反向仍为 `21/96 = 21.9%`。反向 target normal 的 raw trace mean/max 均
高于 source calibration normal，说明主要问题是 normal support/density 的跨 batch shift，不是 age correction
缺失。

预先固定的 local-density score 得到混合结果。它显著降低了 score 与 absolute age/length 的相关性，也缩小了
normal bulk 的跨 batch location shift；但正常单点 tail 变得极重，calibration thresholds 达到约11，而
fully-post drift endpoint q95 只有约5。结果是 B1 -> B2 `0/35` drift 被 endpoint rule 检出，B2 -> B1 也只有
`2/24` full recall。这个具体 local-density normalization 不应成为下一版 endpoint detector。

![Absolute-age-free ablation](../artifacts/agent_v2/normal_manifold_age_free_ablation/age_free_ablation_summary.png)

## 1. 方法与数据纪律

实验计划先以 commit `f12d515` 冻结；在运行前以 `c16e4bd` 明确区分 mixed transition window 和 fully-post
window；实现与单元测试在 commit `bb4f4dc` 固定后才运行一次全量分析。

两个方向均使用既有 development data：

| Direction | Normal fit traces | Normal calibration traces | Reference anchors | Target traces |
|---|---:|---:|---:|---:|
| B1 -> B2 | 59 | 37 | 472 | 240 |
| B2 -> B1 | 128 | 77 | 1,024 | 120 |

为了隔离 age 变量，本轮沿用 P1-LF 的 broad non-drift fit pool，而没有切换到新的 canonical-only `N_fit`。
B1/B2 当前只有10/16条 `normal_reference_eligible=true`，不足以建立独立 fit/calibration。因而本实验验证的是
age 假设，不解决正常数据三集合的最终组成。

三个固定 score 为：

1. `raw_knn`：query 到全局 normal bank 的第5近邻 Hellinger distance；
2. `global_robust_z`：raw distance 相对全部 source-fit reference distances 的 median/IQR 标准化；
3. `local_density_z`：raw support radius 相对 query 五个 normal neighbors 自身的典型 support radius 取
   log-ratio，再做全局稳健标准化。

三者都不读取 absolute age、workflow、domain、arm、token text/ID、boundary 或未来长度。Boundary 只在全部
score 保存后用于评价 phase。

对于 onset `o`，`o <= end < o+7` 的 width-8 窗口被单列为 mixed transition；只有 `end >= o+7` 才进入
fully-post phase。

## 2. 不使用 age 时，信号如何展开

Global robust z 只是 raw score 的 affine transform，其 trace-level phase progression 为：

| Direction | Normal | Drift pre-onset | Mixed transition | Fully post |
|---|---:|---:|---:|---:|
| B1 -> B2 | -0.289 | 0.312 | 1.884 | 2.692 |
| B2 -> B1 | -0.011 | 0.133 | 1.213 | 2.085 |

这些值先在每条 trace/phase 内求均值，再跨 trace 等权。它们显示：

- pre-onset 与 normal 相近或只有有限抬升；
- mixed transition 已明显升高；
- 8-token window 全部进入跨领域生成后进一步升高；
- 两个方向具有相同次序，没有依赖一个方向的偶然 scale。

Raw score 的 paired fully-post-minus-pre 平均增量为 `+0.1225`（B2 target）和 `+0.1396`（B1 target）；29/29
与17/18个可比较 drift 为正。B2 的6条、B1 的6条 early-onset drift 在首个 width-8 endpoint 前没有完整 pre
phase，因此不进入 paired 分母，但全部仍进入 normal-vs-fully-post 排序。

最弱的 raw fully-post trace 是 B2 SQL-query attack，mean `0.4560`，略低于 B2 normal trace-mean q95
`0.4564`；其余34/35全部越过。反向24/24全部越过 B1 normal q95 `0.4406`。

所以 H1 当前获得的准确支持是：

> 在这个固定 Agent/模型的 B1/B2 中，跨领域执行展开后的 routing windows 通常失去全局 normal-neighbor
> support，而且这一现象无需 absolute position correspondence。

它仍不是“每个 endpoint 都可低误报报警”的结论。

## 3. Age 与 length 确实相关，但不应进入正常性定义

在 target normal 上，未使用 age 的 score 仍显示以下描述性相关：

| Score / direction | Endpoint score vs index | Trace mean vs length | Trace max vs length |
|---|---:|---:|---:|
| Raw, B1 -> B2 | 0.374 | 0.546 | 0.744 |
| Raw, B2 -> B1 | 0.376 | 0.422 | 0.648 |
| Local density, B1 -> B2 | 0.067 | 0.087 | 0.226 |
| Local density, B2 -> B1 | 0.040 | 0.261 | 0.510 |

这是 Spearman correlation，仅用于诊断。它说明后期/长 trace 的 raw support distance 往往更大，但不能说明
token 100 是一个跨 trace 可交换的语义阶段。

更重要的是：即使不校正这种相关，representation separation 仍近乎完整。因此正确处理不是重新设计 age bins，
而是把两个问题分开：

- `raw/local state novelty` 描述是否离开正常集合；
- `time-at-risk` 控制长 episode 进行了更多次检验。

Absolute age 可以继续用于画风险曲线和失败切片，不能返回 score definition。

## 4. 反向 FAR 的根因是跨 batch normal support shift

Raw normal distribution 的 source/target 位置为：

| Direction | Source-cal mean | Target mean | Difference | Source-cal max mean | Target max mean | Difference |
|---|---:|---:|---:|---:|---:|---:|
| B1 -> B2 | 0.4195 | 0.4088 | -0.0107 | 0.5136 | 0.4927 | -0.0209 |
| B2 -> B1 | 0.3761 | 0.4014 | +0.0254 | 0.4600 | 0.4927 | +0.0326 |

B1 bank 对 B2 normal 提供更近的支持，所以 forward threshold 保守；B2 bank 到 B1 normal 的距离整体更远，
所以相同 source-tail calibration 在反向偏松。Global robust z 只是 source affine transform，不会改变该排序或
报警结果。

用 legacy trace maxima 做 secondary endpoint diagnostic：

| Direction | Raw threshold | Normal FAR | Pre-onset alarm | +4 | +8 | +16 | Full | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 -> B2 | 0.5966 | 2/205 = 1.0% | 3/35 | 20.0% | 28.6% | 45.7% | 74.3% | 10.5 |
| B2 -> B1 | 0.5275 | 21/96 = 21.9% | 3/24 | 37.5% | 58.3% | 70.8% | 83.3% | 5.5 |

与 P1-LF 的 age-standardized endpoint 相比，移除 age 后 forward FAR 从 `3.9%` 降到 `1.0%`，`+8/full`
保持 `28.6%/74.3%`；反向 FAR 保持 `21.9%`，+8 从 `50.0%` 变为 `58.3%`，full 从 `79.2%` 变为
`83.3%`。两个反向 alarm 集合21条中有19条相同。

因此没有证据表明 age correction 是核心信号来源，也没有证据表明保留它能解决 calibration transfer。

## 5. Local density 为什么“bulk 更稳、endpoint 更差”

Local-density score 在 representation 层仍然有强信号：

| Direction | Normal trace mean | Fully-post mean | Above normal q95 | Paired post > pre | AUROC / AP |
|---|---:|---:|---:|---:|---:|
| B1 -> B2 | 0.329 | 2.958 | 29/35 | 28/29 | 0.972 / 0.860 |
| B2 -> B1 | 0.486 | 3.112 | 22/24 | 15/18 | 0.965 / 0.823 |

它还把 source-target normal trace-mean shift 从 global-z 的 `-0.219/+0.355` 缩到 `+0.114/+0.073`，并明显
降低 score-age/length correlations。这部分支持 H4 的动机。

但 reference local-log-ratio 的 robust scale 只有约 `0.071`。少数正常 query 位于一个密集 normal cluster
附近、却比该 cluster 内部半径远很多时，会产生 `z > 10` 的极值。Calibration 的高端不是 drift，而是 clean、
benign 和 resisted normal 的混合；forward top-3 分别包括 dialogue-scene clean、JavaScript benign control
和 contract-analysis resisted attack。

结果是：

| Direction | Local threshold | Normal FAR | +8 recall | Full recall |
|---|---:|---:|---:|---:|
| B1 -> B2 | 11.047 | 26/205 = 12.7% | 0/35 | 0/35 |
| B2 -> B1 | 10.589 | 5/96 = 5.2% | 1/24 | 2/24 |

Fully-post drift endpoint q95 仅为 `5.01/5.49`，远低于 normal calibration maximum threshold。也就是说，
drift 产生的是广泛、持续的中高 local-density score；normal hard transitions 则产生更少但更极端的 peaks。
对 endpoint maximum 而言，后者完全控制 threshold。

Behavior specificity 也没有改善。Target non-drift 的 local-density trace mean 为：

| Direction | Clean | Benign | Resisted attack |
|---|---:|---:|---:|
| B1 -> B2 | 0.164 | 0.342 | 0.601 |
| B2 -> B1 | 0.388 | 0.428 | 0.878 |

Local density 反而更敏感于 attack mention/refusal。因此这个具体 S2 score 的结论是：**不作为 endpoint detector
继续推进**；它提供的 bulk normalization 思路可以保留，但不能忽略 tail shape 和 hard negatives。

## 6. Pair-group calibration 是正确提醒，不是现成修复

同一 scenario 的多个 arms 高度相关。把 calibration 先按 pair group 聚合后，raw diagnostic 变为：

| Direction | Independent groups / rank | Threshold | Normal FAR | +8 | +16 | Full |
|---|---:|---:|---:|---:|---:|---:|
| B1 -> B2 | 15 / 15 | 0.6526 | 0/205 | 11.4% | 17.1% | 34.3% |
| B2 -> B1 | 30 / 28 | 0.5800 | 5/96 = 5.2% | 25.0% | 58.3% | 75.0% |

Forward 只有15个独立 calibration groups；`alpha=0.10` 的 finite order statistic 只能取最大值，source 严格
exceedance 为0。它消除了一个 scenario 三次占 rank 的问题，却使 threshold 极保守。

因此 O13 得到确认：风险单位应按生成结构分组；同时当前独立 group 数不足以稳定估计 upper tail。不能在
legacy trace-max 和 pair-group-max 中事后挑结果更好的一方作为新 primary。

## 7. 对逻辑基础和下一步的影响

### H1：支持继续

Global normal state support 在不使用 absolute age 时仍产生双向、trace-balanced 的强 post-drift separation。
这是本轮最重要的正结果。

### H4：混合证据

局部密度确实减少了 age/length dependence 和 normal bulk shift，但当前公式损害 tail calibration、及时 recall
和 execution-vs-refusal specificity。不能说 H4 已成功，也不值得围绕这个公式调 k 或截断 z。

### Absolute decode age：退出主 score

本轮没有观察到任何需要恢复 absolute-age normalization 的证据。后续只把 age 当作 time-at-risk 和诊断变量。

### 真正剩余的问题

现有数据现在把问题进一步收缩为：

> Raw normal-state distance 能看到跨领域执行展开，但什么样的平移不变 trajectory evidence 能区分持续
> execution、合法 mention/refusal 和正常生成退化，并在不同 episode 长度下得到稳定 stopping risk？

因此下一轮应优先检验 H2/H3：state trajectory 的进入深度、持续时间、转移与回归形态，而不是继续发明新的
absolute position normalization。任何 history statistic 都应有限记忆、group-calibrated，并把 resisted/benign
作为核心对照。

## 8. 局限

- B1/B2 已被反复观察，本结果是 adaptive development evidence；
- broad non-drift fit 不等于未来 canonical `N_fit`；
- raw/global 排序很强，但使用了完整 fully-post segment 做描述，不能直接作为在线 score；
- AUROC/AP 比较的是每条 trace 的未来 phase mean，证明 representation signal，不证明 stopping rule；
- pair-group calibration 独立单位太少；
- 仍只覆盖一个 Agent、一个 MoE 模型和 final-generation episode。

## 9. 产物与验证

运行：

```bash
.venv/bin/python scripts/run_normal_manifold_age_free_ablation.py

MPLCONFIGDIR=/tmp/agent-moe-matplotlib \
  .venv/bin/python scripts/plot_normal_manifold_age_free_ablation.py
```

结果：`artifacts/agent_v2/normal_manifold_age_free_ablation/result.json`

- result size：8,158,348 bytes；
- SHA-256：`cb1feab85b939a66b830ee4de2c08fcf083ac04ada4555f3ee4e3e89715f3988`；
- 运行前 tests：128/128 通过；
- 原始 traces、routing cache、P1/P1-LF results、labels 和 boundaries 均未修改；
- B3 未使用。
