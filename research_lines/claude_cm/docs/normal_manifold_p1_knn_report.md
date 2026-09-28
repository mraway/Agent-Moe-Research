# P1：Layered kNN Normal Manifold 实验报告

日期：2026-09-04（America/Los_Angeles）

状态：exploratory development complete；primary 未达到 research gate；P1 不进入 B3

预注册计划：[P1 实验计划](normal_manifold_p1_knn_plan.md)

## 摘要

P1 只用正常客服 decode routing 建立局部 kNN reference bank，检验跨领域执行窗口是否会及时失去正常邻域
支持。Primary 在两个 batch-transfer 方向都能把 non-drift trace FAR 控制在 10%以内，且最终能无提前误报地
发现约 46% 的 drift；但是它在 evidence onset 后 8 tokens 内几乎没有检测能力：

| Source -> target | Non-drift FAR | Onset +4 | Onset +8 | Onset +16 | Full recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|
| B1 -> B2 | 7/205 = 3.4% | 0/35 | 0/35 | 7/35 = 20.0% | 16/35 = 45.7% | 19 |
| B2 -> B1 | 9/96 = 9.4% | 1/24 = 4.2% | 3/24 = 12.5% | 6/24 = 25.0% | 11/24 = 45.8% | 12 |

因此 P1 给出的是一个清晰的负结果：**明显跨领域内容在展开一段时间后，确实更可能落到正常客服局部流形
之外；但“到正常窗口的静态近邻距离”不足以及时识别任务切换 onset。** P1 未达到预声明的两个方向均 FAR
不高于 15%、onset+8 recall 至少 35%、median latency 不高于 8 的 research gate。

## 1. 固定方法与数据纪律

Primary 使用 causal 8-token top-8 expert-selection signature：每层归一化后做平方根变换，以逐层 Hellinger
distance 度量窗口距离。Middle L5--L10 与 late L11--L15 分别平均后等权。每条 fit trace 最多贡献 8 个
均匀 anchors；query 首先在相同 broad workflow family 和 causal age bin 中找第 5 近邻，reference 少于 20
时按预注册规则回退。

Source fold 0--2 只提供正常 fit traces，fold 3--4 只提供正常 calibration traces。Normal 包含 clean、benign
control 与 resisted attack；59 条 drift trace 整条没有进入任何模型、经验 tail 或阈值计算。每条 calibration
trace 只贡献 persistent rarity 的 full-trace maximum，target 不重新校准。

| Direction | Normal fit | Normal calibration | Target traces | Target drift |
|---|---:|---:|---:|---:|
| B1 -> B2 | 59 | 37 | 240 | 35 |
| B2 -> B1 | 128 | 77 | 120 | 24 |

主时间标签使用 observation atlas 重建的 evidence onset，而不是较晚的 evidence-completion boundary。所有 score
只使用截至当前 token 的 decode routing。

## 2. Primary 结果

### 2.1 误报控制可以迁移

| Target | Clean FAR | Benign FAR | Resisted-attack FAR | Overall FAR | Alarm onsets / 1,000 endpoints |
|---|---:|---:|---:|---:|---:|
| B2 | 0/80 = 0.0% | 5/80 = 6.3% | 2/45 = 4.4% | 7/205 = 3.4% | 0.48 |
| B1 | 3/40 = 7.5% | 4/40 = 10.0% | 2/16 = 12.5% | 9/96 = 9.4% | 1.22 |

这说明 P1 并没有通过频繁报警换取后期 recall；把 resisted attack 包含在正常域后，它也没有把攻击暴露本身
普遍当作异常。B1 -> B2 的 source threshold 为 3.8286，37 条 calibration maxima 因经验 rarity 离散和 ties
没有严格越线；B2 -> B1 threshold 为 4.5643，77 条中 5 条严格越线。第一个方向偏保守，但 target FAR 和另一
方向的结果仍共同说明 primary 的主要失败是及时 recall，而不是阈值失控。

### 2.2 异常状态出现得晚

B1 -> B2 在 onset+8 前没有任何 clean hit；到 +16 只有 7/35。B2 -> B1 在 +8 有 3/24，到 +16 为 6/24。
两个方向的 +8 reachability 都是 100%，因此不能用 warm-up 或短回复解释低 recall。

Full decode 时两个方向都达到约 46%，但 clean hits 的 median latency 分别为 19 和 12 tokens。也就是说，P1
可以识别一部分已经形成稳定域外文本形态的窗口，却不能满足“开始执行跨领域任务后少量 tokens 内警告”的
研究目标。

Drift trace 的 pre-onset alarm 分别为 B2 6/35（17.1%）和 B1 3/24（12.5%）。这些值包含可能早于人工
evidence string 的 semantic commitment，也包含真实提前误报；本轮不做结果后重标，全部保留为严格
pre-onset alarm。

### 2.3 Domain 分布

B1 -> B2 的 onset+8 在八个 domain 全部为零。B2 -> B1 的三个及时 clean hits 分别来自 cooking、fiction
和 general knowledge；poetry、programming、mathematics、legal analysis、travel planning 均为零。Full
decode hits 覆盖更多 domain，但不能挽救 onset 结论。

## 3. 固定 sensitivity

| Variant | B1 -> B2 FAR / +8 / +16 / full | B2 -> B1 FAR / +8 / +16 / full |
|---|---|---|
| Primary middle+late selection | 3.4% / 0.0% / 20.0% / 45.7% | 9.4% / 12.5% / 25.0% / 45.8% |
| Early-only selection | 9.3% / 2.9% / 22.9% / 42.9% | 13.5% / 29.2% / 37.5% / 54.2% |
| Middle-only selection | 10.2% / 14.3% / 31.4% / 62.9% | 17.7% / 33.3% / 41.7% / 50.0% |
| Late-only selection | 3.4% / 2.9% / 22.9% / 51.4% | 8.3% / 12.5% / 33.3% / 45.8% |
| Middle+late probability | 6.8% / 2.9% / 28.6% / 60.0% | 15.6% / 8.3% / 33.3% / 50.0% |
| Shuffled expert timing | 1.0% / 2.9% / 17.1% / 40.0% | 3.1% / 4.2% / 20.8% / 29.2% |
| Clean+benign-only fit | 2.9% / 2.9% / 25.7% / 54.3% | 4.2% / 8.3% / 25.0% / 41.7% |

这些 variants 没有一个达到两个方向的 gate：

- Middle-only 是最接近及时信号的 selection variant，但 B2 -> B1 FAR 升至 17.7%，且 B1 -> B2 +8 仍只有
  14.3%。它支持此前“middle direction 更稳定”的观察，但不构成可选的新 primary；
- Full probability 提高了部分 +16/full recall，却没有改善 +8 timing，并在 B2 -> B1 超过 15% FAR；
- 打乱每层 token-to-expert routing 后，full recall 降至 40.0% / 29.2%，说明精确时间化 expert identity 确实
  贡献信息；但未打乱版本本身的及时能力仍不足；
- 排除 resisted attacks 的 fit sensitivity 没有造成灾难性变化，表明 P1 不是只有见过攻击拒绝才能工作；
- Early-only 在 B2 -> B1 偶然较强，却不能向 B2 复制，符合 early layers 容易受 batch/词法变化影响的解释。

## 4. P1 告诉我们的东西

P1 区分了两个此前容易混合的命题：

1. **后期 off-domain state 是否罕见？** 对一部分 drift 是，full recall 约 46%，probability sensitivity 可到
   50%--60%；
2. **onset 附近是否已经离开正常局部支持？** 大多数不是，或 8-token 混合窗口把首批域外 token 稀释得太强。

因此，正常路由流形的想法没有被整体否定，但最直接的“静态 kNN 距离 + persistence”实例失败了。下一步 P2
要检验的不是更换 `k` 或阈值，而是：扣除 workflow/age 的正常变化后，跨领域 onset 是否沿着正常低秩子空间
无法解释的新方向出现。若 P2 也只有后期 recall，则说明静态 normal-state 方法的 timing 存在共同边界，应把
希望转向 P3 的 transition prediction。

## 5. 实现与复现审计

- plan commit：`b858bbe`，先于 target metric 计算；
- implementation：`src/phase_a/normal_manifold.py`、`scripts/run_normal_manifold_p1_knn.py`；
- unit tests：6/6 通过；覆盖 causal normalization、probability windows、deterministic shuffle load preservation、
  Hellinger distance、age/workflow conditioning 和有限样本阈值；
- 360/360 核心 trace 在构建 compact cache 前重新通过原始 routing validation 和 decode-token alignment；
- result：`artifacts/agent_v2/normal_manifold_p1_knn/result.json`；
- result size：37,303,810 bytes；
- result SHA-256：`1bdf65dec4db70930c93bb18fbf4f48ae467217969be667afb2147ce4a35b900`；
- 原始 trace、behavior labels、completion boundaries 与 observation-atlas onset 均未修改；
- B3 未使用。
