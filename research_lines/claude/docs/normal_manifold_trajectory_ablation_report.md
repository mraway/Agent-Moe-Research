# Normal Manifold Finite-Memory Trajectory Ablation：实验报告

日期：2026-09-05（America/Los_Angeles）

状态：adaptive development ablation complete；H2 未获得独立 transition signal 的支持，H3 的固定
overlapping-window persistence 只获得很弱的 ranking 改善、没有 stopping 改善；没有冻结新 detector

预注册计划：[Finite-memory trajectory ablation 计划](normal_manifold_trajectory_ablation_plan.md)

## 摘要

本轮在完全不使用 absolute decode age 的条件下，对比了五条预先固定的因果 evidence streams：即时正常状态
距离、正常状态条件下的 successor residual、两者各自最近4步的 q25，以及二者的严格 AND。

结果首先再次确认了状态表示信号。五条 stream 的 normal-vs-fully-post trace-mean AUROC 在两个方向都为
`0.992--0.997`，说明跨领域执行进入了持续离开正常 support 的 routing 区域。

但 transition 和 finite-memory 变体没有形成足够独立的新信息：

- state 与 transition endpoint score 的 Spearman correlation 在 target normal 为 `0.974/0.963`，在 drift 为
  `0.994/0.994`；path maxima correlation 为 `0.984/0.984`；
- state endpoint 与 `state_floor_4` path maxima correlation 为 `0.988/0.992`；
- drift-vs-resisted path-max AUROC 从 state 的 `0.973/0.927` 只变为 state-floor 的 `0.978/0.943`；
- pair-group stopping 下，state-floor 没有降低 FAR：forward 保持0但 recall 更低，reverse FAR 从
  `5/96` 升至 `7/96`；
- conditional transition 在相同 FAR 下有小幅 timing gain，但表示 AUROC略低、反向 drift-vs-resisted AUROC
  完全不变，且误报集合与 state 相同。

关键机制是**滑窗相关性**。相邻 width-8 state windows 重叠7个 token；一个 routing event 会自然影响多个连续
`S_t`。而 conditional edge `(x_{t-1},x_t)` 的两个端点也重叠7/8。因此，在这些 endpoint 上再次要求连续4步，
并没有获得4份独立持续证据；它主要是对同一 routing token 的重复观察。

本轮否定的是这两个具体实现，不是否定 trajectory 思路本身。下一次若继续 H2/H3，必须先隔离新增 routing
information，例如用非重叠/低重叠 block、显式 token innovation 或 staggered independent lanes；不能继续在
overlapping score 上替换 mean、median、persistence 或 CUSUM。

![Finite-memory trajectory ablation](../artifacts/agent_v2/normal_manifold_trajectory_ablation/trajectory_ablation_summary.png)

## 1. 实验纪律与方法

实验计划在 commit `344ba5a` 冻结，实现与135项测试在 commit `2e74ec7` 固定，然后运行一次完整 B1↔B2
分析。数据角色保持不变：

| Direction | Normal fit traces | Normal calibration traces | State anchors | Transition edges | Target traces |
|---|---:|---:|---:|---:|---:|
| B1 -> B2 | 59 | 37 | 472 | 472 | 240 |
| B2 -> B1 | 128 | 77 | 1,024 | 1,024 | 120 |

状态 score `S_t` 与前序 age-free result 逐 endpoint 完全一致，两个方向的 calibration/target 最大绝对差均为
`0.0`。这排除了重实现造成的基线变化。

Transition score `T_t` 的计算为：先按 query predecessor 找16个最近 normal predecessor edges，再在这些 edge
的 successor 中计算 query successor 的第5近邻距离，最后用 fit-normal leave-one-trace-out residual 的
median/IQR 标准化。Reference raw residual 为：

| Fit batch | Count | Mean | Median | q95 | Robust scale |
|---|---:|---:|---:|---:|---:|
| B1 | 472 | 0.4108 | 0.4298 | 0.4975 | 0.04725 |
| B2 | 1,024 | 0.3858 | 0.4069 | 0.4882 | 0.07334 |

五条 streams 及其真实 lookback 为：

| Stream | Definition | Routing lookback |
|---|---|---:|
| State | `S_t` | 8 tokens |
| Transition | `T_t` | 9 tokens |
| State floor | last-4 `q25(S)` | 11 tokens |
| Transition floor | last-4 `q25(T)` | 12 tokens |
| Joint floor | `min(state floor, transition floor)` | 12 tokens |

人工 onset 只在 score 保存后用于评价。Fully-post 边界按各方法 lookback 分别计算，而不是统一使用 `o+7`。

## 2. 表示层：所有方法都看到了 drift，但 transition 没有新增分离

### 2.1 Normal vs fully-post

| Method | B1 -> B2 AUROC | Above normal q95 | Paired post > pre | B2 -> B1 AUROC | Above normal q95 | Paired post > pre |
|---|---:|---:|---:|---:|---:|---:|
| State | 0.9950 | 34/35 | 29/29 | 0.9965 | 24/24 | 17/18 |
| Transition | 0.9932 | 34/35 | 27/27 | 0.9944 | 23/24 | 17/18 |
| State floor | 0.9933 | 34/35 | 26/26 | 0.9965 | 24/24 | 17/18 |
| Transition floor | 0.9921 | 34/35 | 26/26 | 0.9948 | 23/24 | 17/18 |
| Joint floor | 0.9923 | 34/35 | 26/26 | 0.9961 | 23/24 | 17/18 |

Fully-post trace-balanced means 也呈一致 progression：

| Direction | Method | Normal | Pre-onset | Mixed | Fully post |
|---|---|---:|---:|---:|---:|
| B1 -> B2 | State | -0.289 | 0.312 | 1.884 | 2.692 |
| B1 -> B2 | Transition | -0.351 | 0.299 | 2.085 | 2.709 |
| B1 -> B2 | State floor | -0.407 | 0.207 | 1.691 | 2.563 |
| B2 -> B1 | State | -0.011 | 0.133 | 1.213 | 2.085 |
| B2 -> B1 | Transition | 0.000 | 0.167 | 1.288 | 2.014 |
| B2 -> B1 | State floor | -0.094 | 0.050 | 1.074 | 2.012 |

因此 H1 再次得到支持。但 transition AUROC 在两个方向都比 state 略低，并没有显示更强的 normal-vs-drift
representation。

### 2.2 Transition 为什么看起来几乎等于 state

Post-hoc correlation audit（不进入任何 score 或选择）为：

| Direction | Normal endpoint `corr(S,T)` | Drift endpoint `corr(S,T)` | Path-max `corr(S,T)` |
|---|---:|---:|---:|
| B1 -> B2 | 0.974 | 0.994 | 0.984 |
| B2 -> B1 | 0.963 | 0.994 | 0.984 |

原因有两层：

1. `x_{t-1}` 和 `x_t` 是相邻 width-8 windows，本身共享7/8的 token routing；
2. conditional-successor score 虽先按 predecessor gating，但最终仍用 successor state distance，第5近邻排序高度
   受当前状态离开 normal support 的程度支配。

这意味着本轮 `T_t` 并没有隔离“新增 token 导致的 transition innovation”。H2 需要的不是另一个强相关 state
distance。

## 3. 行为特异性：小幅 ranking 改善不足以支持 H3

### 3.1 Drift vs resisted 是核心对照

Path-maximum AUROC：

| Method | B1 -> B2 drift vs resisted | B2 -> B1 drift vs resisted |
|---|---:|---:|
| State | 0.9727 | 0.9271 |
| Transition | 0.9810 | 0.9271 |
| State floor | 0.9784 | 0.9427 |
| Transition floor | 0.9778 | 0.9349 |
| Joint floor | 0.9778 | 0.9349 |

State-floor 相对 state 的变化是 `+0.0057/+0.0156`，方向一致但很小。它也没有形成明显优于其他 negatives
的特殊增益：drift-vs-all-non-drift 仅从 `0.9844/0.9544` 变为 `0.9873/0.9622`。

完整 path maxima 的均值显示 floor 同时压低所有行为，而不是只压低正常 peaks：

| Direction | Method | Clean | Benign | Resisted | Drift |
|---|---|---:|---:|---:|---:|
| B1 -> B2 | State | 1.369 | 1.411 | 1.544 | 4.310 |
| B1 -> B2 | State floor | 1.015 | 1.020 | 1.190 | 4.003 |
| B2 -> B1 | State | 1.157 | 1.281 | 1.506 | 3.107 |
| B2 -> B1 | State floor | 0.904 | 1.045 | 1.285 | 2.930 |

这可以产生小幅 ranking 改善，但还不足以证明 execution 与 refusal 拥有不同的可利用 trajectory shape。

### 3.2 Overlap 使“连续四步”不等于持续行为

State endpoint 与 state-floor path maxima 的 Spearman correlation 为 `0.988/0.992`；state-floor 与 joint-floor
更达到 `0.995/0.997`。这不是偶然：

```text
x_t     covers tokens [t-7, ..., t]
x_(t+1) covers tokens [t-6, ..., t+1]
```

一个特殊 token 最多连续出现在8个 state windows 中。最近4个 endpoint 的 q25 要求的是“同一个 routing event
在重叠窗口中的重复影响”，不等价于跨4个独立时间块维持域外计算。因此本轮只否定
`q25(overlapping S_t, width=4)`，不能据此否定 H3 本身。

## 4. Stopping：没有方法解决跨批风险

Primary pair-group calibration 的结果：

| Direction | Method | Normal FAR | +8 recall | +16 recall | Full recall | Pre-onset alarms |
|---|---|---:|---:|---:|---:|---:|
| B1 -> B2 | State | 0/205 | 4/35 | 6/35 | 12/35 | 1/35 |
| B1 -> B2 | Transition | 0/205 | 7/35 | 7/35 | 15/35 | 1/35 |
| B1 -> B2 | State floor | 0/205 | 3/35 | 5/35 | 11/35 | 1/35 |
| B1 -> B2 | Transition floor | 0/205 | 6/35 | 7/35 | 12/35 | 1/35 |
| B1 -> B2 | Joint floor | 0/205 | 4/35 | 5/35 | 11/35 | 1/35 |
| B2 -> B1 | State | 5/96 | 6/24 | 14/24 | 18/24 | 1/24 |
| B2 -> B1 | Transition | 5/96 | 9/24 | 14/24 | 19/24 | 1/24 |
| B2 -> B1 | State floor | 7/96 | 8/24 | 14/24 | 19/24 | 1/24 |
| B2 -> B1 | Transition floor | 9/96 | 8/24 | 14/24 | 19/24 | 2/24 |
| B2 -> B1 | Joint floor | 9/96 | 8/24 | 14/24 | 19/24 | 2/24 |

Forward calibration 只有15个独立 groups，`alpha=0.10` 的 finite rank 是第15名，即 source maximum；0 FAR
对应的 recall 因而非常保守，不能视为 detector 优势。

Transition endpoint 在两个方向都以相同 FAR 提高 +8/full recall：`4→7 / 12→15` 和
`6→9 / 18→19`。这是 H2 唯一一致的弱正面结果。但它同时满足：

- representation AUROC 略低；
- drift-vs-resisted 反向完全不变；
- 反向5条 false alarms 与 state 完全相同；
- score/path correlation 约0.98。

因此更合理的结论是同一 state signal 的 scale/rank 变化改善了一些 threshold crossing，而不是已经发现独立
transition mechanism。

Legacy per-trace calibration 也没有显示 persistence 改善：forward FAR 从 state 的 `2/205` 变为 state-floor 的
`3/205`，reverse 二者均为 `21/96`。Floor 压低 target normal maxima 的同时也压低 source threshold，跨批
不可交换性没有消失。

## 5. Time-at-risk audit

在 B2 -> B1 的 primary pair-group threshold 下，所有 state、transition 和 floor false alarms 都出现在第64个
eligible endpoint 之后。State 的5条误报首次越线位于 risk step `89--178`；涉及4个 pair groups：recipe benign、
packing-guide clean、contract-analysis resisted，以及 dialogue-scene 的 resisted/benign 两个相关 arms。

这说明：

- 这些不是输出开头的单点初始化噪声；
- 长 episode 暴露更多 late hard transitions，必须以完整 path 控制风险；
- absolute token age 仍不应进入 normality score，但 risk step 必须进入 time-at-risk 评价或 time-uniform boundary；
- 同一 dialogue-scene group 的两个 arms 同时报错，再次说明 trace 数不能当作独立 calibration units。

Finite-memory floor 反而扩展了误报集合：state-floor 为7条，transition/joint floor 为9条。这直接否定了“只要
要求四步持续就能压制正常误报”的实现假设。

## 6. 对 H1--H3 的更新

### H1：进一步支持

五种因果 streams 均保留双向 fully-post separation；跨领域执行离开 agent-specific normal routing support 的
信号稳定存在。

### H2：当前实现不支持

Conditional successor 有小幅 early-recall 增益，但没有独立 representation 或 behavior-specific signal，并与
state 高度共线。不能据此声称正常局部转移比绝对状态更可迁移。H2 仍可用真正隔离 innovation 的表示继续检验。

### H3：当前 overlapping persistence 不支持

Drift-vs-resisted ranking 有很小的双向改善，但 stopping FAR 没有改善，联合 AND 也几乎复制 state floor。当前
证据不支持把 `q25/4` 作为 detector；由于滑窗重叠混淆了独立持续性，H3 本身尚未被充分检验。

## 7. 下一步算法问题

下一步不应调 `history_width=3/5/8`，也不应尝试另一个 CUSUM。数据要求先改变 evidence unit：

> 如何构造只包含“自上一个独立观察以来新增 routing information”的 causal innovation，使持续性不再由
> width-8 sliding-window overlap 人为制造？

值得预注册的最小后续对照包括：

1. **Non-overlapping blocks**：连续 width-8 blocks，每个 routing token 只进入一个 state observation；
2. **Staggered lanes**：保持逐 token 更新，但每条 lane 只比较相隔8 token 的不重叠窗口，最后按预声明方式合并；
3. **Token innovation**：直接对新进入窗口的单 token routing 与正常 token/transition support 比较，不把共享的
   7个 token 重算为新证据。

先比较这些表示能否降低 state/transition 共线性、是否在 drift-vs-resisted 上有双向增益，再讨论 stopping
boundary。跨批 normal shift 与 time-at-risk calibration 是另一条问题轴，不能用 trajectory smoothing 掩盖。

## 8. 局限

- B1/B2 都是 adaptive development evidence；
- broad non-drift fit 不是最终 canonical `N_fit`；
- correlation audit 和 overlap 机制解释是结果后的诊断，不进入冻结算法；
- pair-group source calibration 只有15/30个独立 groups，tail threshold 很不稳定；
- 只研究 final-generation episode，没有验证跨 agent turn 的状态继承；
- path-max ranking 是离线行为特异性诊断，不是生产报警性能；
- 没有使用 B3。

## 9. 产物与验证

运行：

```bash
.venv/bin/python scripts/run_normal_manifold_trajectory_ablation.py

MPLCONFIGDIR=/tmp/agent-moe-matplotlib \
  .venv/bin/python scripts/plot_normal_manifold_trajectory_ablation.py
```

结果：`artifacts/agent_v2/normal_manifold_trajectory_ablation/result.json`

- result size：17,442,931 bytes；
- SHA-256：`b3b6b04cb4859649698aeba80c6fe6d19c28ad1de8996c41d019a18f2bd98e8b`；
- 运行前 tests：135/135 通过；
- age-free state paths：474条 calibration + target paths 中逐 endpoint 最大差 `0.0`；
- 原始 traces、routing cache、labels、boundaries 和既有结果均未修改；
- B3 未使用。
