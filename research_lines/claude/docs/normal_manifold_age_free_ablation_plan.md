# Normal Manifold Absolute-Age-Free Ablation：实验计划

日期：2026-09-04（America/Los_Angeles）

状态：运行前冻结；B1/B2 adaptive development ablation，不是新 detector 的确认实验

逻辑依据：[算法设计的逻辑基础](algorithm_design_logic_foundations.md)中的 `R2/R3`、`M4`、`O9--O12`、
`A1/A7`、`D3/D7` 与 `H1/H4`。

## 1. 本实验只回答什么

主问题：

> P1-LF 观察到的 normal-vs-post-drift 路由距离分离，在完全移除 absolute decode age 后是否仍然存在？

次问题：

> 不依赖 token position 的局部密度校正，是否比全局 raw kNN distance 更能稳定 B1/B2 双向 normal score？

本轮首先是 representation ablation，不选择最终 evidence accumulator，也不把“任意 endpoint 越线”重新确立为
主报警逻辑。Full-path maximum 只作为与 P1-LF 可比的 secondary stress diagnostic。

## 2. 数据角色与当前限制

继续使用已经固定并反复观察的 development data：

- B1 `brief=absent`：120 traces，96 non-drift，24 drift；
- B2：240 traces，205 non-drift，35 drift；
- 每个方向 source folds 0--2 拟合，folds 3--4 校准，另一 batch 全量评价；
- pair group 不跨 split；B1/B2 均不再称为 held-out confirmation；B3 不使用。

为了只隔离 age normalization，本轮沿用 P1-LF 的 broad-null fit pool：所有
`goal_plan_deviation_started=false` 的 clean、benign 和 resisted attack traces。该选择不是对逻辑基础 `Q1` 的
最终回答。

当前 B1/B2 只有 10/16 条 `normal_reference_eligible=true`，其中 folds 0--2 只有 8/9 条、folds 3--4 只有
2/7 条，不能支持独立的 canonical `N_fit/N_cal`。因此本实验只能回答“现有 P1-LF 信号是否依赖 absolute age”，
不能证明严格 canonical-normal detector 已成立。

## 3. 固定 routing 表示与邻域

除 score normalization 外与 P1-LF 保持相同：

- 只读 final-generation decode routing；
- causal width-8 top-8 expert-selection frequency；
- 每层归一化后做平方根变换，对应 Hellinger 几何；
- middle `L5--L10` 与 late `L11--L15` 分别计算距离后等权；
- 每条 source-fit trace 最多均匀贡献8个 anchors；
- query raw nonconformity `d(q)` 是到全局 normal bank 的第5近邻距离；
- 邻居搜索不使用 workflow、arm、domain、attack、文本、token ID、boundary、future length 或 absolute age。

窗口 endpoint 只用于 token 对齐、phase 划分和 time-at-risk 诊断，不进入任何 score。

## 4. 三个预先固定的 age-free score

令每个 reference anchor `i` 的 leave-one-trace-out 第5近邻距离为 `r_i`。

### S0：Raw global kNN

```text
s_raw(q) = d(q)
```

这是最少假设的状态支持距离。

### S1：Global robust z

```text
m = median({r_i})
u = max(IQR({r_i}) / 1.349, float32 epsilon)
s_global(q) = (d(q) - m) / u
```

它只做 source-fit 全局 affine normalization，不使用位置。它与 raw score 排序相同，用于比较两个 transfer
方向的量纲和复现 P1-LF 的 phase-summary 表达。

### S2：Local-density z

对 query 的5个最近 anchors `N5(q)`，先定义局部密度比：

```text
v(q) = log((d(q) + epsilon) / (median({r_j : j in N5(q)}) + epsilon))
```

对每个 reference anchor `i`，用其 leave-one-trace-out `N5(i)` 同样计算 `v_i`。最后只用 source-fit reference
values 做全局稳健标准化：

```text
s_local(q) = (v(q) - median({v_i})) / robust_scale({v_i})
```

该 score 问的是“query 的支持半径，相对它所处正常邻域的典型支持半径是否异常”，不把稀疏但正常的局部模式
与密集模式强行使用同一 raw distance 尺度。它不使用 absolute age 或 workflow label。

## 5. Primary：representation-level 检查

每个方向、每种 score 固定报告：

1. endpoint-weighted normal、drift pre-onset、mixed transition window、fully-post-onset 的
   count/mean/median/q90/q95；为与 P1-LF 对照，另报合并的 `end >= onset`；
2. 先在每条 trace/phase 内求均值，再跨 trace 等权的相同摘要；
3. target normal trace-mean q95，以及 fully-post drift trace mean 超过该 q95 的数量；
4. 对同时存在 pre/fully-post endpoint 的 drift，`fully-post mean - pre mean` 的摘要和正值比例；
5. normal vs post-drift trace-mean AUROC/AP，仅作为开发描述；
6. clean、benign、resisted 三类 negative 的 trace-mean/max 分布；
7. target normal 与 source calibration normal 的 trace-mean/max location shift。

对于 width 8 和 evidence onset `o`，phase 固定定义为：`end < o` 是 pre-onset，
`o <= end < o + 7` 是仍混合正常/异常 token 的 transition window，`end >= o + 7` 才是8个 token 全部位于
onset 后的 fully-post window。这样不会再次把只含一个异常 token 的窗口当作完整 post-drift 状态。

核心解释规则预先固定：

- 两个方向均保持大多数 drift 的 post mean 高于 normal q95，且 paired post-minus-pre 主要为正：支持 H1 继续；
- global score 失败而 local-density score 双向恢复：支持 H4；
- 两种 age-free score 都显著失去分离：说明 P1-LF 正信号高度依赖未经支持的 age normalization；
- drift 与 resisted/benign 同时升高：说明测到 topic exposure/mention，不能称为 execution-specific signal。

本轮不设为了“通过”而任意选择的数值 gate；保存全部预声明 summary，由结果形态决定 H1/H4 是支持、反对还是
仍不确定。

## 6. Secondary：full-path stress diagnostic

对 `s_raw/s_global/s_local` 分别运行无记忆 endpoint stopping rule，目的仅为暴露 score calibration，而不是选择
primary detector：

```text
alarm at first t where s_t > threshold
```

每种 score 同时报告两种 source calibration：

1. `legacy_trace_max`：每条 normal calibration trace 贡献一个 maximum，复现 P1-LF 风险单位；
2. `pair_group_max`：同一 calibration pair 内所有 non-drift arms 先合并为一个 maximum，再以 pair group 为
   order-statistic 单位，检验 O13 的相关性影响。

二者均使用 `alpha=0.10` finite upper order statistic 和严格 `>`。Target 报告 normal FAR、pre-onset alarm、
`+4/+8/+16/full` clean recall、latency 和每千 normal endpoints alarm onsets。

如果完整 source/target paths 不可交换，该规则不保证 target FAR；这正是 stress diagnostic 要显示的内容。不得从
两种 calibration 中挑一个 target 数字更好的版本作为新 primary。

## 7. Absolute age 只作为诊断变量

为量化旧假设，本轮事后表固定报告：

- normal endpoint score 与 endpoint index 的 Spearman correlation；
- normal trace mean/max 与 decode length 的 Spearman correlation；
- `7--31/32--63/64--127/128+` 的 score 分布与 exposure count；
- 按 stop reason 和 length band 的 false-alarm stress slice。

这些字段不会反馈到 score、threshold 或邻居选择。任何 age-specific 表现都只能解释失败，不能产生新的 age bin
或位置阈值。

## 8. 实现与防泄漏检查

- 从冻结 compact routing cache 读取；不重新运行模型；
- 运行时重新检查 B1/B2 sample-index hashes 与 120/240 trace counts；
- 复用 P1-LF bank 时重新计算并验证 raw第5近邻距离；
- unit tests 覆盖 global z、local-density ratio、leave-one-trace-out reference 邻域、pair-group maximum；
- 输出每条 trace 的完整 endpoint scores，支持逐 token 重建；
- 结果写入新的 ignored artifact 目录，不覆盖 P1/P1-LF 或原始数据；
- 代码完成后先运行 tests，再运行一次全量 ablation；不依据中间 target 输出修改定义。

计划产物：

```text
scripts/run_normal_manifold_age_free_ablation.py
artifacts/agent_v2/normal_manifold_age_free_ablation/result.json
docs/normal_manifold_age_free_ablation_report.md
```

## 9. 停止规则

结果生成后不调 `k`、width、layers、anchor count、normal pool 或 local-density 公式。若 H1/H4 均不支持，先做
failure zoom-in；若至少一个 score 保留双向 representation signal，再基于
[算法设计的逻辑基础](algorithm_design_logic_foundations.md)另写 state/transition stopping-rule proposal。
