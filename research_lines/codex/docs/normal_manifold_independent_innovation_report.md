# Normal Manifold Independent Routing Innovation：实验报告

日期：2026-09-05（America/Los_Angeles）

状态：adaptive development experiment complete；token innovation 通过预注册的 representation gate；独立
persistence 与全部 stopping candidates 未通过；没有冻结 detector，B3 未使用

预注册计划：[Independent Routing Innovation 计划](normal_manifold_independent_innovation_plan.md)

## 摘要

本轮把前序 width-8 sliding-window score 拆成真正新增的 routing evidence，预先固定比较七条因果 streams：
原始滑窗状态、单一 non-overlap lane、八条 staggered lanes 上的当前状态与双 block minimum、单 token routing
novelty，以及固定不重叠 token blocks 内的 mean/q25。

实验得到了一个新的、边界清楚的正结果：

> **跨领域执行的 routing separation 不依赖滑动窗口对同一 token 的重复使用；单个新 token 的 routing
> observation 本身已经包含稳定的 post-drift normal-support loss。**

`token_endpoint_z` 的 normal-vs-fully-post trace-mean AUROC 为 `0.995/0.998`；两个方向所有可比较 drift
均为 post mean 高于 pre mean（`33/33`、`22/22`），8/8 target domains 的 fully-post mean 都高于对应 target
normal mean。它与 sliding state 的 endpoint correlation 只有 `0.371/0.498`，相邻 token score correlation 也从
sliding state 的 `0.953/0.948` 降至 `0.324/0.459`。因此本轮预注册的 H2 token-innovation representation gate
通过。

但更独立的 persistence 没有带来预声明的行为特异性增益：

- staggered 两个不重叠 state blocks 取 minimum，相对 eligibility-matched current state 的
  drift-vs-resisted path-max AUROC 只提高 `+0.006/+0.026`，未达到双向 `+0.03`；
- token-block q25 相对 mean 反而降低 `-0.020/-0.029`；
- 两种 H3 candidates 均失败。

所有七条 stream 的 pair-group calibrated stopping 都未达到旧 research gate。表现最及时的
`nonoverlap_state_z` 在两个方向的 +8 recall 也只有 `17.1%/29.2%`，median latency 为 `22.5/10` tokens。
Token innovation 虽然 representation ranking 很强，但单 token endpoint 的 +8 recall 为 `17.1%/12.5%`，
full recall 为 `48.6%/66.7%`。本轮准确结论是“找到了不依赖 overlap 的 routing signal unit”，不是“找到了
online detector”。

![Independent routing innovation](../artifacts/agent_v2/normal_manifold_independent_innovation/independent_innovation_summary.png)

## 1. 实验纪律与数据

计划先以 commit `f997691` 冻结；实现和8个新增 unit tests 以 commit `7aaf79e` 固定后，才运行一次完整分析。

两个方向继续使用已有 development data：

| Direction | Normal fit traces | Normal calibration traces | Target traces | Drift |
|---|---:|---:|---:|---:|
| B1 -> B2 | 59 | 37 | 240 | 35 |
| B2 -> B1 | 128 | 77 | 120 | 24 |

数据 hashes、360条 routing cache、人工 evidence boundaries 与 trajectory input hash 均在运行时验证。既有
`sliding_state_z` 在474条 calibration/target paths 上逐 endpoint 复现，最大差为 `0.0`。

所有线上 score 只读取截至当前 token 的 top-8 expert selections；不读取 absolute age、workflow、domain、
arm、token text/ID、行为 outcome、人工 boundary 或未来长度。Boundary 和 domain 只在 score 保存后评价。

为保持受控比较，本轮 normal fit 仍使用 broad non-drift pool；它不是未来 canonical `N_fit` 规范。B1/B2 已被
多轮观察，所以本报告只有 adaptive development 证据地位。

## 2. 独立 information unit 是否保留信号

七条 stream 的 trace-balanced representation 结果如下：

| Method | B1 -> B2 AUROC / AP | Above normal q95 | B2 -> B1 AUROC / AP | Above normal q95 |
|---|---:|---:|---:|---:|
| Sliding state | 0.995 / 0.974 | 34/35 | 0.997 / 0.987 | 24/24 |
| Non-overlap state | 0.994 / 0.972 | 34/35 | 0.995 / 0.983 | 23/24 |
| Staggered current | 0.993 / 0.965 | 34/35 | 0.994 / 0.978 | 23/24 |
| Staggered min-2 | 0.983 / 0.950 | 33/35 | 0.996 / 0.985 | 23/24 |
| Token endpoint | 0.995 / 0.977 | 34/35 | 0.998 / 0.991 | 24/24 |
| Token block mean | 0.994 / 0.971 | 34/35 | 0.997 / 0.990 | 24/24 |
| Token block q25 | 0.992 / 0.962 | 33/35 | 0.997 / 0.991 | 24/24 |

固定 non-overlap state 只读取 `t=7,15,23,...`，每个 routing token 只属于一个评价 endpoint。它几乎完整保留
sliding state 的分离，直接排除了“高 AUROC 只是同一异常 token 被相邻8个窗口重复计算”的解释。

更强的证据来自 token score。`token_endpoint_z` 的 trace-balanced normal/fully-post mean 为
`0.028/0.922` 和 `0.077/0.703`；paired post-minus-pre 为 `33/33` 与 `22/22` 正。所有8个 target domains 的
fully-post mean 都高于 normal mean，最低的 programming 也保持同方向（`0.467` 与 `0.403`）。因此它不是由
单个诗歌或数学 domain 驱动。

## 3. Token innovation 确实降低了机械冗余

| Method | Adjacent score correlation B1 -> B2 | B2 -> B1 | Endpoint correlation with sliding state |
|---|---:|---:|---:|
| Sliding state | 0.953 | 0.948 | 1.000 / 1.000 |
| Non-overlap state | 0.654 | 0.679 | 1.000 / 1.000 at common endpoints |
| Staggered min-2 | 0.948 | 0.945 | 0.854 / 0.846 |
| Token endpoint | 0.324 | 0.459 | 0.371 / 0.498 |
| Token block mean | 0.387 | 0.601 | 0.726 / 0.814 |
| Token block q25 | 0.349 | 0.526 | 0.672 / 0.750 |

Non-overlap state 的相邻 observation 已相隔8 token，所以 correlation 从约0.95降到约0.66；剩余相关来自
真实语义状态的延续，而不是 token reuse。单 token score 与 sliding state 的相关更低，说明它不是简单复制
旧 score 的 rank。

Token scores 也缩小了 normal bulk 的跨 batch shift：

| Method | B1 -> B2 normal mean/max shift | B2 -> B1 normal mean/max shift |
|---|---:|---:|
| Sliding state | -0.219 / -0.426 | +0.355 / +0.457 |
| Token endpoint | -0.018 / -0.065 | +0.116 / +0.132 |
| Token block mean | -0.016 / -0.021 | +0.117 / +0.118 |
| Token block q25 | -0.003 / -0.037 | +0.175 / +0.153 |

这给出下一步 calibration 研究的合理基础，但 reverse shift 仍未消失，也不能从均值稳定直接推出 upper-tail
stopping 稳定。

## 4. H3：独立 persistence 仍未成立

完整 path maximum 的 drift-vs-resisted AUROC 为：

| Candidate | Comparator | B1 -> B2 | Gain | B2 -> B1 | Gain | Gate |
|---|---|---:|---:|---:|---:|---|
| Staggered min-2 | Staggered current | 0.976 vs 0.970 | +0.006 | 0.953 vs 0.927 | +0.026 | Fail |
| Token block q25 | Token block mean | 0.947 vs 0.966 | -0.020 | 0.927 vs 0.956 | -0.029 | Fail |

Staggered min-2 的 current 与 predecessor 属于同一 lane、两个 width-8 windows 完全不重叠，所以前序实验的
7/8 overlap 混淆已被移除。它仍只有很小的 ranking 改善，且没有达到双向 `+0.03`。这比前序 q25/4 负结果
更强：在当前 route-only state geometry 下，要求两个独立 blocks 持续异常没有形成足够强的
execution-vs-refusal information。

Token q25 的结果更直接。真实 drift 并不要求 block 中大多数单 token novelty 都很高；用下四分位数强调
persistence 会丢掉有用的高/中 novelty token，两个方向都比 block mean 更差。因此该具体 persistence 形式停止。

Token endpoint 的 drift-vs-resisted AUROC 只有 `0.902/0.917`，低于 sliding state 的 `0.973/0.927`；固定 block
mean 恢复到 `0.966/0.956`。这表明单 token novelty 是更独立的信号载体，但语义/行为特异性需要跨 token
整合；“更独立”本身不等于“更适合直接报警”。

## 5. Stopping：全部 candidates 为 no-go

Primary pair-group calibration 结果：

| Direction | Method | Normal FAR | +8 recall | +16 recall | Full recall | Median latency |
|---|---|---:|---:|---:|---:|---:|
| B1 -> B2 | Sliding state | 0/205 | 4/35 | 6/35 | 12/35 | 18.0 |
| B1 -> B2 | Non-overlap state | 1/205 | 6/35 | 9/35 | 20/35 | 22.5 |
| B1 -> B2 | Staggered min-2 | 2/205 | 1/35 | 11/35 | 22/35 | 16.5 |
| B1 -> B2 | Token endpoint | 4/205 | 6/35 | 7/35 | 17/35 | 25.0 |
| B1 -> B2 | Token block mean | 1/205 | 2/35 | 5/35 | 11/35 | 28.0 |
| B1 -> B2 | Token block q25 | 1/205 | 2/35 | 3/35 | 9/35 | 41.0 |
| B2 -> B1 | Sliding state | 5/96 | 6/24 | 14/24 | 18/24 | 10.0 |
| B2 -> B1 | Non-overlap state | 8/96 | 7/24 | 15/24 | 19/24 | 10.0 |
| B2 -> B1 | Staggered min-2 | 7/96 | 0/24 | 14/24 | 20/24 | 13.0 |
| B2 -> B1 | Token endpoint | 9/96 | 3/24 | 8/24 | 16/24 | 15.5 |
| B2 -> B1 | Token block mean | 6/96 | 5/24 | 11/24 | 19/24 | 15.0 |
| B2 -> B1 | Token block q25 | 2/96 | 4/24 | 10/24 | 18/24 | 15.5 |

表中省略 eligibility-matched staggered current；它同样未通过。七条 stream 在两个方向的 FAR 都低于15%，但
没有一条同时达到双向 +8 recall 至少35%和 median latency 至多8。因此
`meets_numeric_detector_gate=false` 对所有方法成立。

几个容易误读的点：

- Non-overlap state 的 full recall 比 sliding state 高，不说明稀疏观察更强；source path maximum 变小后 threshold
  同时下降，forward 还新增1条 resisted false alarm，reverse FAR 从5/96升至8/96。
- Staggered min-2 的 full recall 为 `62.9%/83.3%`，但 +8 recall 为 `2.9%/0%`。双 block persistence 需要更长
  evidence horizon；高 final recall 不能补偿任务切换附近的空白。
- Token q25 将 FAR 压到 `0.5%/2.1%`，代价是 +8 recall 仅 `5.7%/16.7%`，forward median latency 达41 tokens。
  这不是可部署的低误报胜利。
- Forward calibration 仍只有15个独立 groups，`alpha=0.10` threshold 退化为 source maximum；reverse 有30个
  groups、取第28阶统计量。跨方向数字不能只归因于表示优劣。

## 6. 对假设和研究路线的更新

### H1：进一步支持

固定 non-overlap blocks 仍有 `0.994/0.995` 的 fully-post AUROC。跨领域执行离开 normal routing support 不是
sliding overlap 制造的统计假象。

### H2：获得限定支持

本轮预注册的 token-innovation gate 通过。单个新增 routing observation 含有可重复 post-drift signal，并且
显著降低与 sliding state 的机械冗余及 normal bulk batch shift。

这个结论不恢复前序 conditional-successor 方法，也不证明 normal transition model 已解决。它只说明后续动态
模型应以 token innovation 为基础 information unit，而不是把高度重叠的 width-8 states 当作新 evidence。

### H3：当前独立 persistence 不支持

在消除 window overlap 后，staggered min-2 仍未达到行为特异性增益门槛，token q25 则双向恶化。当前证据不
支持继续搜索 min/quantile/history width。Route-only 能强烈表示跨域语义，但区分 execution 与合法
mention/refusal 仍是核心限制。

### Detector：不冻结

Representation success 与 stopping failure 同时成立。不得根据接近1.0的 retrospective trace-mean AUROC 宣称
在线检测已完成，也不启动 B3。

## 7. 下一步

按照预注册停止规则，下一阶段不再在 B1/B2 搜索 representation 变体，而应单独研究 calibration：

1. 将 `token_endpoint_z` 保留为最小 innovation diagnostic，将 `nonoverlap_token_mean8_z` 保留为固定 block
   aggregate；不继续调 block width 或 quantile；
2. 建立更大的、按 scenario 独立的 canonical normal calibration 集，避免15个 group 时 upper threshold 退化为
   最大值；
3. 比较按完整 path 校准的 time-uniform boundary，而不是 absolute-age normality correction；
4. 将 clean、benign mention 与 resisted attack 全部保留在 `N_eval`，并预先确定哪些 hard negatives 进入
   `N_cal`；
5. 只有 calibration proposal 在新的正常数据上冻结并通过，才设计含 drift 的 B3 confirmation。

## 8. 局限

- B1/B2 已被反复使用，本轮不是独立确认；
- 只有一个固定客服 Agent、一个 MoE 模型和 final-generation episode；
- broad non-drift fit pool 不是最终 canonical `N_fit`；
- top-8 routing 仍可能主要编码已生成 token 的语义，尚未证明超越文本基线；
- fixed episode-aligned blocks 会引入最多7 token 的观察相位差；
- source calibration 只有15/30个独立 groups，upper-tail 风险估计不稳定；
- 没有 B3，也没有多模型、跨 Agent 或生产 FAR 验证。

## 9. 产物与验证

运行：

```bash
.venv/bin/python scripts/run_normal_manifold_independent_innovation.py

MPLCONFIGDIR=/tmp/agent-moe-matplotlib \
  .venv/bin/python scripts/plot_normal_manifold_independent_innovation.py
```

结果：`artifacts/agent_v2/normal_manifold_independent_innovation/result.json`

- result size：17,231,385 bytes；
- SHA-256：`53f64f49befdb16030c845bae01ccbdf623769842a1f145c0595fd523d262717`；
- 运行前 tests：143/143 通过；
- trajectory state paths：474条 calibration + target paths 中逐 endpoint 最大差 `0.0`；
- B1/B2 sample-index hashes 与360条 cache manifest 已验证；
- 原始 traces、routing cache、labels、boundaries 和既有结果均未修改；
- B3 未使用。
