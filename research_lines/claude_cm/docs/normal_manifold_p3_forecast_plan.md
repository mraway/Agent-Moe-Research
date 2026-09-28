# P3：Normal Route Forecast + CUSUM 实验计划

日期：2026-09-04（America/Los_Angeles）

状态：preregistered exploratory design；在计算 P3 target metrics 前固定

上位设计：[三个 normal-manifold proposals](normal_routing_manifold_algorithm_proposals.md)

## 1. 问题

P1/P2 都以当前 routing state 的静态正常性为检测对象。P3 检验不同假设：即使某个状态本身在正常客服中
出现过，从当前客服历史转入该状态的过程是否仍然可以由 normal-only dynamics 预测；跨领域任务切换是否会
表现为连续、异常大的预测残差。

P3 的表示、预测器、residual scale、tail 和 CUSUM threshold 全部只从 normal traces 获得。P1/P2 target
结果不用于修改 P3 block width、PCA rank、predictor input 或累计规则。

## 2. 固定数据角色

- B1 `brief=absent`：120 traces；SHA-256
  `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`；
- B2：240 traces；SHA-256
  `e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942`；
- 双向 transfer：B1 -> B2 与 B2 -> B1；
- source folds 0--2 normal fit，folds 3--4 normal calibration；
- normal 包含 clean、benign control、resisted attack；drift trace 整条排除于 fit/calibration；
- B1/B2 均为 development data；不使用 B3。

## 3. Primary detector

### 3.1 Normal routing state

- 把 decode 划分为从 token 0 开始的不重叠 4-token blocks；不足4-token的尾部丢弃；
- 每个 block 计算 per-layer normalized top-8 selection distribution，并做 `sqrt` 变换；
- middle L5--L10 与 late L11--L15 分开用 normal-fit blocks 做 weighted PCA，各固定保留16维；
- PCA mean/covariance 与后续 predictor loss 都按 trace 等权：一条长回复的所有 blocks/transition weights 之和
  与一条短回复相同；
- 两个 band 的 projected coordinates 拼成 32维 state `z_j`。

### 3.2 Normal predictor

从第三个完整 block 开始预测：

```text
input_j = [z_(j-1), z_(j-1)-z_(j-2), workflow_family_one_hot,
           current_age_bin_one_hot]
target_j = z_j
```

Workflow family 固定3类，age bin 固定5类，所以 predictor input dimension 为72。Input 用 weighted normal-fit
mean/std 标准化；target 减 weighted mean。使用 multi-output primal ridge，penalty 固定为 input dimension 72，
不搜索超参数。

### 3.3 Residual surprise 与 CUSUM

Fit residual 每维以 median 居中；diagonal robust scale 固定为：

```text
scale_d = max(1.4826 * MAD_d, 0.1 * standard_deviation_d, 1e-6)
q_j = mean_d(((residual_jd - median_d) / scale_d)^2)
```

Fit-normal `q` 建立经验 upper tail：

```text
p_j = (1 + count(fit_q >= q_j)) / (N + 1)
u_j = -log(p_j)
C_j = max(0, C_(j-1) + u_j - 1)
```

CUSUM 每条 trace 从0重新开始。Primary score 为 `C_j`，endpoint 是当前4-token block 的最后一个 token。最早
score 位于 token index 11，必须报告各 latency horizon 的 reachability。

每条 source normal-calibration trace 贡献一个 `max_j C_j`，以 `alpha=0.10` 有限样本 order statistic
得到 threshold；target 不重新拟合或校准。

## 4. 固定 variants

Primary 为 `selection_forecast_cusum_middle_late`。预声明：

1. `selection_forecast_single_middle_late`：不用 CUSUM，直接以 `u_j` 作 score，仍按 trace maximum 校准；
2. `selection_forecast_cusum_middle`：仅 L5--L10，state rank16；predictor input dimension40，ridge penalty40；
3. `selection_forecast_cusum_late`：仅 L11--L15，同为16维 state；
4. `probability_forecast_cusum_middle_late`：改用完整 router probability block mean；
5. `selection_forecast_cusum_middle_late_shuffled`：使用固定的 per-layer token permutation；
6. `selection_forecast_cusum_middle_late_clean_benign_fit`：fit 排除 resisted attacks，calibration 仍使用全部
   normal traces。

每个 variant 独立 fit/calibrate。Variants 只解释表示、层、累计和 normal coverage，不覆盖 primary 结论。

## 5. 评价与 gate

与 P1/P2 使用完全相同的 evidence-onset 和 trace-level 指标：non-drift FAR 及 arm 分层、pre-onset alarm、
onset `+4/+8/+16` clean-hit recall、full recall、latency、reachability、每千 endpoints alarm onsets，以及
domain/channel/workflow coverage。

Primary research gate 不变：两个 transfer 方向同时达到 FAR <=15%、onset+8 clean-hit recall >=35%、median
latency <=8，并覆盖多个 domain。由于最早 endpoint 为11，early-onset unreachable cases 必须留在总体 recall
分母中，同时单独报告 reachability，不能事后排除。

## 6. 产物

- scorer：`scripts/run_normal_manifold_p3_forecast.py`；
- shared utilities：`src/phase_a/normal_manifold.py`；
- tests：`tests/test_normal_manifold.py`；
- result：`artifacts/agent_v2/normal_manifold_p3_forecast/result.json`；
- report：`docs/normal_manifold_p3_forecast_report.md`。

Result 保存 PCA ranks/eigenvalues、weighted training counts、predictor dimensions/penalty、residual scales、fit-tail
范围、calibration maxima 与 target 逐 endpoint `q/u/C`。不得覆盖前两个实验或改动原始 trace/labels。
