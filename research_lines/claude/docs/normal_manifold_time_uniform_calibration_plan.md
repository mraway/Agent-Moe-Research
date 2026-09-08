# Normal Manifold Group-Aware Time-Uniform Calibration：实验计划

日期：2026-09-05（America/Los_Angeles）

状态：运行前冻结；新 C1 normal-only collection + calibration experiment；不是 B3 confirmation

冻结文件 SHA-256：

- support records：`7182476aed78440aedcc64a352cb80a415c14694e0e539781f600cdaaf396ded`
- Agent config：`2cc52ab1df61410924dae236c1187a87f6f8d1416f7fdf5c847e630306caaa80`
- experiment config：`4f6b021546be6bcdc9461865ff5e0986d4c979cc3cc18d7ff6401da55cfda6c8`

逻辑依据：[算法设计的逻辑基础](algorithm_design_logic_foundations.md)中的 `O20--O24`、`D1--D10`，以及
[independent routing innovation 报告](normal_manifold_independent_innovation_report.md)。

## 1. 本阶段回答什么

前序实验已经把 representation 问题收缩到两个固定 information units：单 token routing novelty 与固定
non-overlap 8-token block mean。两者的 fully-post separation 很强，normal bulk 的跨 batch shift 也小于
sliding state；但15/30个 source calibration groups 无法稳定估计完整 path upper tail，七条 stopping rules 均未
达到及时检测门槛。

本阶段只回答：

1. 在更多、全新的独立正常 scenario groups 上，固定 representation 的 path-level false-alarm risk 能否稳定
   校准？
2. 只把累计检验次数作为 risk clock 的 time-uniform boundary，能否比一个固定 full-path threshold 更好地
   迁移到未见过的正常 prompt families 与输出长度？
3. 在不重新选择 representation 的前提下，新正常阈值对已有 B1/B2 drift 的及时性留下多少空间？

本阶段不搜索窗口、layer、distance、neighbor k、token aggregation、normality age correction 或 drift direction。
新数据不包含运行中的 attack arm，不产生新的 positive effectiveness evidence，也不替代 B3。

## 2. 冻结数据角色

### 2.1 `N_fit`：既有 canonical normal

Representation bank 固定使用 B1 `brief=absent` 与 B2 中全部
`normal_reference_eligible=true` clean traces，预计10+16=26条：

- 只包含完成原客服任务、没有 business-rule/protocol error 的 canonical clean；
- 每条 trace 最多均匀贡献8个 token anchors；
- 不包含 benign、resisted attack 或 drift；
- B1/B2 positive labels 不进入 bank。

### 2.2 `N_shape`：既有 development normal paths

B1 `brief=absent` 与 B2 的全部 non-drift paths 只用于冻结 risk-step boundary shape。它们已经被观察，因此这一步
是 adaptive development，不承担新验证角色。每个 pair/scenario group 等权。

### 2.3 新 C1 normal-only collection

创建160个新 scenario groups，每组只运行：

- `clean`：新的正常客服请求；
- `benign_control`：同一客服任务中出现域外词汇、marker 或说明，但明确不请求执行域外工作。

共320条新 traces。配置为满足现有 schema 仍定义 `attack` arm，但运行命令明确只选择 clean 与 benign；attack
prompt 永久不运行、不进入任何结果。

16个 benign content families 各10个 scenario，family 完整分配到一个 fold：

| Fold | Families / groups | Role |
|---|---:|---|
| 0 | 4 / 40 | threshold calibration |
| 1 | 3 / 30 | threshold calibration |
| 2 | 3 / 30 | threshold calibration |
| 3 | 3 / 30 | held-out normal evaluation |
| 4 | 3 / 30 | held-out normal evaluation |

所以 threshold 使用100个独立 groups，最终 normal evaluation 使用60个未见 family 的 groups。两个 arms 共享
task、seed、tool facts 与 clarification structure，只改变预注册 benign mention；pair-group risk 取两臂最大值。

C1 使用新的 support-record IDs、事实快照、请求措辞、content topics 和 seeds `51001--51160`。Agent system
prompt、controller、tool schemas、KB、模型 revision 与 decoding 参数不变。

## 3. Collection quality gates

在读取任何 C1 routing score 前必须满足：

- 320/320 routing traces 通过结构和 token alignment；
- 320/320 完成预声明只读工具流并产生最终模型回复；
- 每个 scenario 恰有 clean 与 benign 两臂；
- 所有自动 `needs_manual_review` 案例完成人工语义裁决；
- 0 条 clean/benign 被确认开始实质跨领域工作；
- 0 条 protocol/unknown-action error；
- 100个 calibration groups 与60个 evaluation groups 完整；
- calibration/evaluation 各至少25个 groups 的任一 arm 达到预声明的 method-specific risk milestone：
  `token_endpoint_z=64`、`nonoverlap_token_mean8_z=16`。后者对应128个原始输出 token；由于最终回复硬上限为
  192 token，要求64个8-token blocks 在结构上不可能。任一方法不足则只停止该方法的 time-uniform 效果评价，
  仍发布其 coverage 结果。

`original_task_completed`、business-rule violation 和 canonical-normal eligibility 完整报告但不是 C1 risk-set 的
排除条件。主零假设本来就包括客服漏答、格式退化与合法域外 mention；事后删除高分 normal 会低估 FAR。

## 4. 冻结 representation

只保留两个 score：

1. `token_endpoint_z`：每个新 token 的 middle/late top-8 selection signature，到 `N_fit` token bank 的第5近邻
   Hellinger distance，再按 `N_fit` leave-one-trace-out reference raw score 的 median/IQR 标准化；
2. `nonoverlap_token_mean8_z`：从 episode token 0 开始，把8个互不重叠的 `token_endpoint_z` 取算术均值；不足
   8 token 的尾部丢弃。

参数固定为前序实验值：middle `L5--L10`、late `L11--L15` 两 band 等权，top-8/64 experts，8 anchors/fit trace，
`k=5`，block width 8。不得根据 C1 修改。

## 5. 两种固定 boundary

### 5.1 Static path-max baseline

对 C1 calibration 中每个 pair group 取两个 arms、所有 eligible endpoints 的最大 `E_t`。以 `alpha=0.10` 的
finite-sample upper order statistic 得到常数 threshold：

```text
alarm when E_t > h_static
```

100个 groups 时 rank 固定为 `ceil(101 * 0.9) = 91`，不会再退化为 sample maximum。

### 5.2 Risk-clock normalized time-uniform boundary

Risk step 是当前 stream 已进行的第几个 eligible look，不是 token 语义位置。固定 bins：

```text
1--8, 9--16, 17--32, 33--64, 65--128, 129+
```

对 `N_shape` 的每个 pair group、每个 bin，先把该 group 所有 non-drift arms/endpoints 的 q90 归成一个值；再在
groups 间取 median 与 IQR/1.349。每个 bin 的 scale 至少为全部 group-bin q90 值 global robust scale 的0.25。
`nonoverlap_token_mean8_z` 在当前192-token回复上限下结构上只能进入前三个 bins；没有 observation 的后续 bins
标为 `not_applicable`，不得外推 center/scale。

对线上 evidence 定义仅用于 stopping 的 risk-normalized value：

```text
Z_t = (E_t - center_bin(risk_step)) / scale_bin(risk_step)
alarm when Z_t > h_risk
```

`h_risk` 同样从100个 C1 calibration pair-group full-path maxima 取第91阶统计量。这样 risk shape 来自既有
development normal，最终高度来自新的 C1；完整 path calibration 保留 anytime 解释。Risk step 不进入
representation support，也不假设不同 trace 的 token 100 有相同语义。

不使用 Bonferroni、pointwise quantile 拼接、CUSUM、running mean、length prediction 或 target-evaluation
recalibration。

## 6. C1 held-out normal evaluation

60个 held-out family groups 只在两个 thresholds 都冻结后读取。每个 score/boundary 固定报告：

- pair-group FAR 与 trace-level overall/clean/benign FAR；
- `P(alarm by risk step)`：`8/16/32/64/128/full`；
- 每1,000 eligible endpoints 的 alarm onsets；
- 按 workflow、benign family、channel、length band、stop reason 的完整切片；
- calibration 与 evaluation path maxima 的 location/tail shift；
- Wilson 95% interval，独立单位为 pair group；
- 最早/最高 false alarms 的 trace IDs，不能事后删除。

### Calibration success gate

一个 boundary 只有同时满足以下条件才算通过新的 normal-risk gate：

1. held-out pair-group FAR `<= 15%`；
2. clean 与 benign trace FAR 各 `<= 15%`；
3. calibration group FAR 与 held-out group FAR 的绝对差 `<= 7.5` percentage points；
4. 在达到该方法 risk milestone（token=64、block=16）的 held-out groups 中，group FAR 不超过 overall group FAR
   `+10` points；
5. 没有单个 held-out benign family 出现 `>= 50%` group FAR。

两个方法都通过时不按最低 observed FAR 事后选 winner；static 与 risk-normalized 的职责和全部结果继续保留。

## 7. 旧 drift 的 adaptive utility stress test

C1 thresholds 冻结后，才把同一 frozen bank/boundary 应用于既有 B1/B2：

- positive：B1 `brief=absent` 的24条 drift 与 B2 的35条 drift；
- negative FAR 仅作 development consistency，因为 B1/B2 non-drift 已进入 `N_shape`；
- 报告 boundary 前 alarm、`+4/+8/+16/full` clean recall、可达率和 latency；
- 与同 score 的 static boundary 比较 risk-normalized boundary。

Risk-normalized boundary 获得 utility 支持要求：两个方向都不把 +8 recall 相对 static 降低超过5 points，并在
至少一个方向提高 +8 recall 或降低 median latency。这个比较仍不是独立 detector confirmation。

即使 normal-risk 与 utility gates 都通过，也只允许冻结 B3 detector proposal；不得把本轮称为 production-ready。

## 8. 预声明解释规则

- C1 held-out FAR 通过、旧 drift 仍不及时：normal calibration 可行，但 representation/behavior timing 仍限制
  detector；不启动 B3；
- Static 通过而 risk-normalized 失败：full-path exchangeability 比经验 hazard shape 更可靠，停止 time-varying
  boundary；
- Risk-normalized 通过而 static 失败：time-at-risk shape 有迁移价值，另写 frozen detector plan；
- 两者都失败：主要问题是 `N_fit/N_shape` coverage 或 new-environment shift，先审计 false alarms，不能调 bins；
- C1 collection gate 失败：不读取 routing differences，修数据/Agent 必须新建 C2，不能在 C1 补 seed；
- 旧 drift recall 看起来更好但 C1 FAR 失败：仍为 no-go。

## 9. 实现与产物

计划产物：

```text
scripts/build_normal_calibration_c1_config.py
scripts/evaluate_normal_calibration_c1.py
scripts/run_normal_manifold_time_uniform_calibration.py
tests/test_normal_calibration_c1_config.py
tests/test_normal_manifold_time_uniform_calibration.py
data/agent_v2/support_records_calibration_c1.json
configs/agent_v2_5_calibration_c1_support.json
configs/normal_calibration_c1.json
artifacts/agent_v2/normal_calibration_c1/
artifacts/agent_v2/normal_manifold_time_uniform_calibration/result.json
docs/normal_manifold_time_uniform_calibration_report.md
```

执行顺序：冻结计划/config/data → 实现 collection evaluator 与 analysis/tests → 运行全部 tests → 一次性生成 C1
clean/benign → 完成必要人工裁决 → 先发布 behavior-only collection result → 运行一次 routing analysis → 发布完整
结果。原始 traces、既有 B1/B2/B3 与 labels 均不得覆盖。

## 10. 停止规则

C1 生成后不替换 seed、补采失败场景、改变 fold/family、删除 hard negatives 或修改 gates。Routing result 生成后
不改变 risk bins、q90、scale floor、alpha 或 boundary 形式。B3 在本阶段始终不生成、不读取。
