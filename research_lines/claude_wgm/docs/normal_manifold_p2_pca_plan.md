# P2：Conditional PCA `T² + Q` 实验计划

日期：2026-09-04（America/Los_Angeles）

状态：preregistered exploratory design；在计算 P2 target metrics 前固定

上位设计：[三个 normal-manifold proposals](normal_routing_manifold_algorithm_proposals.md)

## 1. 问题

P1 表明跨领域状态后期可能远离正常局部邻域，但 kNN 距离无法及时识别 onset。P2 不调整 P1 的 `k` 或阈值，
而检验另一个预先提出的假设：扣除 workflow family 与 causal decode age 的正常均值后，正常 routing variation
是否集中在低秩子空间，而跨领域 onset 是否产生子空间内极端位移或无法重建的新方向。

P2 仍只用 normal traces 学习所有参数。P1 target 结果不用于选择 P2 rank、条件变量、窗口或 persistence。

## 2. 固定数据与角色

- B1 `brief=absent`：120 traces，sample-index SHA-256
  `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`；
- B2：240 traces，sample-index SHA-256
  `e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942`；
- 双向 transfer：B1 -> B2、B2 -> B1；
- source folds 0--2：normal fit；folds 3--4：normal calibration；
- normal 为 clean、benign control、resisted attack；drift trace 整条不进入 fit/calibration；
- 每条 normal-fit trace 最多均匀抽取 8 个窗口，避免长回复支配 PCA。

B1/B2 都是 development data；B3 不参与。

## 3. Primary detector

### 3.1 Routing signature 与条件均值

- causal 8-token、top-8 selection-frequency signature；
- 每层归一化后做 `sqrt` 变换；
- broad workflow family：`status_only`、`knowledge_qa`、`status_and_knowledge`；
- causal age bins：`8--15`、`16--31`、`32--63`、`64--127`、`128+`；
- 对每个 `(workflow family, age bin)` 计算 fit-normal cell mean；
- cell mean 以 16 个伪窗口的固定权重向对应 age-bin global mean 收缩；若 cell 不存在则直接使用 age mean，
  age mean 不存在才使用全局 mean；
- query residual 为 routing signature 减去该 causal condition mean。

### 3.2 分层 PCA

Middle L5--L10 与 late L11--L15 分开拟合 PCA。每个 band 固定保留 16 个 component，不根据解释方差或
drift metrics 选择 rank。条件残差在 PCA 前再减去 fit residual mean。

每个 query 计算：

```text
T2 = mean_band(sum_i(projected_i^2 / eigenvalue_i) / rank)
Q  = mean_band(mean(reconstruction_residual^2))
```

Fit-normal anchors 建立 `T2` 与 `Q` 各自的经验 upper-tail。令：

```text
rarity_T2 = -log((1 + count(fit_T2 >= query_T2)) / (N + 1))
rarity_Q  = -log((1 + count(fit_Q  >= query_Q )) / (N + 1))
a_t = max(rarity_T2, rarity_Q)
b_t = min(a_{t-1}, a_t)
```

每条 normal-calibration trace 的 `max_t b_t` 用 `alpha=0.10` 有限样本上尾 order statistic 校准 threshold；
target 不重估任何量。

## 4. 固定 variants

Primary 为 `selection_conditional_t2_q_middle_late`。预声明 sensitivities：

1. `selection_conditional_t2_only_middle_late`；
2. `selection_conditional_q_only_middle_late`；
3. `selection_unconditional_t2_q_middle_late`：只减全局 mean；
4. `selection_conditional_t2_q_middle`：L5--L10；
5. `selection_conditional_t2_q_late`：L11--L15；
6. `probability_conditional_t2_q_middle_late`；
7. `selection_conditional_t2_q_middle_late_shuffled`：使用与 P1 相同的 deterministic per-layer token permutation；
8. `selection_conditional_t2_q_middle_late_clean_benign_fit`：fit 排除 resisted attack，calibration 仍使用全部
   normal traces。

每个 variant 独立使用 source-only fit tail 与 normal-calibration threshold；variant 不能覆盖 primary 结论。

## 5. 评价和 research gate

使用 evidence onset，报告与 P1 完全相同的：target non-drift trace FAR、arm 分层、pre-onset alarm、onset
`+4/+8/+16` clean-hit recall、full recall、latency、reachability、每千 normal endpoints alarm onsets，以及
domain/channel/workflow coverage。

Primary research gate 不变：两个 transfer 方向均满足 FAR <=15%、onset+8 clean-hit recall >=35%、median
latency <=8，并覆盖多个 domain。`Q-only` 或其他 variant 即使更好，也只能形成下一轮新假设，不能把 P2
primary 事后改名为成功。

## 6. 产物

- scorer：`scripts/run_normal_manifold_p2_pca.py`；
- shared utilities：`src/phase_a/normal_manifold.py`；
- tests：`tests/test_normal_manifold.py`；
- result：`artifacts/agent_v2/normal_manifold_p2_pca/result.json`；
- report：`docs/normal_manifold_p2_pca_report.md`。

结果必须记录每个 band 的 rank/eigenvalues、condition cell counts、fit-tail 数量、calibration maxima 和 target
逐 endpoint score。不得覆盖 P1 或既有 artifacts，不修改 trace 或标签。
