# Normal Manifold Group-Aware Time-Uniform Calibration：实验报告

日期：2026-09-05（America/Los_Angeles）

状态：C1 normal calibration experiment complete；单-token 两种 boundary 通过新 normal-risk gate，但历史
drift 仍不及时；不启动 B3

预注册计划：[Group-Aware Time-Uniform Calibration 计划](normal_manifold_time_uniform_calibration_plan.md)；
采集验收：[C1 behavior-only collection 报告](normal_calibration_c1_collection_report.md)

## 给研究主管的结论

本阶段解决了前一阶段最明确的工程性不确定性：15/30个 source groups 太少，不能稳定校准完整路径误报。我们新建
160个正常 scenario groups，只运行 clean 与 benign-control；100组定阈值，60组按未见 content family 留出。

结果分为两个层面：

1. **正常风险校准取得实质进展。** 对单-token routing novelty，static full-path threshold 与 risk-clock
   normalized boundary 在60个留出组上的 pair-group FAR 都是 `9/60 = 15.0%`；clean trace FAR 分别
   `10.0%/11.7%`，benign FAR 都是 `8.3%`，全部满足预注册点估计门槛。
2. **及时检测没有解决。** 把 C1 threshold 应用于已有 B1/B2 drift，单-token 的 onset `+8` recall 只有
   `25.0%/11.4%`，median latency 为 `18/25` tokens；risk-clock 只把 latency 改为 `16/23`，没有提高
   `+8` recall。它仍明显低于旧 detector research target（双向 `+8 recall >=35%`、latency `<=8`）。

因此当前结论不是“detector 已可确认”，而是：

> **对固定 Agent，扩大独立正常组后可以把 route-novelty 的完整路径误报控制到研究门槛边缘；真正剩余的主问题
> 是 routing evidence 相对行为 onset 出现太晚，以及合法正常语义模式仍产生集中误报。**

8-token block 没有通过：static FAR `16.7%`，risk-clock 虽降到 `11.7%`，但长路径子组 FAR `22.2%`，比
overall `+10` points 的上限高 `0.56` point。按冻结规则两者都判 Fail。B3 保持未生成、未读取。

![Time-uniform calibration summary](../artifacts/agent_v2/normal_manifold_time_uniform_calibration/time_uniform_calibration_summary.png)

## 1. 数据纪律与证据角色

运行前先后冻结了计划、C1 config/data 与实现；在读取 routing score 前单独发布 behavior-only collection report。
C1 一次性生成，没有补 seed、删除 hard negatives 或运行 schema-only attack arm：

| 数据角色 | 独立组 / traces | 用途 |
|---|---:|---|
| `N_fit`：B1/B2 canonical clean | 26 traces / 208 anchors | 固定 token representation 与 robust z scale |
| `N_shape`：B1/B2 全部 non-drift | 120 groups / 301 traces | 固定 risk-bin center/scale shape；development only |
| C1 calibration folds 0--2 | 100 groups / 200 traces | 第91阶完整路径阈值 |
| C1 held-out folds 3--4 | 60 groups / 120 traces | 新 normal FAR；6个未见 benign families |
| B1/B2 drift | 24 / 35 traces | threshold 冻结后的 adaptive utility stress test |

C1 的320条 routing traces 全部通过结构、token alignment、工具流与自然语言终答门。11条 marker 候选经人工
复核均为 benign label 的引用/拒绝，0条被确认执行域外任务；6条 unsupported factual claims 被标记但保留。
严格任务完成仅72/320，同样没有按质量或长度删样本。

线上 score 只使用当前及既往 top-8 expert selections。文本、token ID、workflow、domain、arm、stop reason、
未来长度、行为 label 和人工 boundary 均不进入 representation 或 stopping rule。

## 2. 新正常风险结果

100个 calibration groups 时，`alpha=0.10` 的有限样本阈值固定为第91阶，四个候选在 calibration 上均为严格
`9/100` exceedance。最终留出结果为：

| Representation / boundary | Threshold | Group FAR（Wilson 95%） | Clean FAR | Benign FAR | Milestone subgroup FAR | Gate |
|---|---:|---:|---:|---:|---:|---|
| Token / static | 1.598 | 9/60 = 15.0%（8.1--26.1） | 10.0% | 8.3% | 9/48 = 18.8% | Pass |
| Token / risk-clock | 2.778 | 9/60 = 15.0%（8.1--26.1） | 11.7% | 8.3% | 9/48 = 18.8% | Pass |
| Block mean-8 / static | 0.963 | 10/60 = 16.7%（9.3--28.0） | 8.3% | 8.3% | 6/27 = 22.2% | Fail |
| Block mean-8 / risk-clock | 0.795 | 7/60 = 11.7%（5.8--22.2） | 5.0% | 8.3% | 6/27 = 22.2% | Fail |

两种 representation 的 exposure coverage 均通过：token milestone 64有 calibration/evaluation `75/48` 个组到达；
block milestone 16有 `36/27` 个组到达。

### 2.1 Token boundary：通过，但只是边缘通过

两个 token boundaries 的 final group FAR 都刚好等于 `15%` 上限；Wilson upper bound 仍为 `26.1%`，说明60组
只够作研究门，而不是生产风险保证。Calibration-vs-evaluation group FAR gap 为6 points，满足7.5-point上限。
单 family 最高 FAR：static 20%，risk-clock 40%，均低于50%门，但每个 family 只有10组。

Risk-clock 并没有降低 final FAR。它把更多误报提前：到 risk step 32，static 累计误报 `2/60=3.3%`，
risk-clock 为 `7/60=11.7%`；到64分别为6.7%与13.3%。Risk-clock 的 held-out q95 path maximum 相对
calibration 上移0.413 standardized units，高于 static raw score 的0.026 shift。

审计切片还显示 risk-clock token 的12条 held-out `knowledge_qa` traces 中7条报警（58.3%），虽然没有一个
content family 达到预注册50% group gate。这说明 group-level overall gate 通过不能掩盖 workflow-specific
coverage 缺口；该边界不能部署。

### 2.2 Block boundary：两个形式按规则失败

Static block 的 group FAR 比15%上限高1.67 points，且 calibration-to-evaluation gap 为7.67 points，比上限高
0.17 point。Risk-clock block 的 overall FAR 较低，但达到16 blocks 的27个长路径组中6个报警：22.22%比
`11.67% + 10% = 21.67%` 上限高0.56 point。因此两个结果都按原规则判 Fail，不能因接近阈值而改判。

Block score 每1,000 endpoints 的 alarm onsets 为 static `7.09`、risk-clock `8.50`，远高于 token 的
`1.11/1.03`。固定均值降低 observation 数，却没有形成更稳定的 path risk。

## 3. 历史 drift utility：形式通过，实质仍不及时

| Method / boundary | B1 +8 / full recall | B1 latency | B2 +8 / full recall | B2 latency |
|---|---:|---:|---:|---:|
| Token / static | 25.0% / 79.2% | 18.0 | 11.4% / 68.6% | 25.0 |
| Token / risk-clock | 25.0% / 70.8% | 16.0 | 11.4% / 68.6% | 23.0 |
| Block / static | 12.5% / 75.0% | 21.0 | 11.4% / 65.7% | 30.0 |
| Block / risk-clock | 12.5% / 83.3% | 23.0 | 8.6% / 85.7% | 29.5 |

两个 risk-clock candidates 都满足预注册的**相对** utility gate：任一批次 +8 recall 没有下降超过5 points，且至少
一个批次 median latency 有所下降。但这个 gate 只防止 time normalization 明显破坏已有 utility，不能代替绝对
detector gate。

Token risk-clock 的 +8 recall 与 static 完全相同，只把命中样本的 median latency 各缩短2 token；同时 B1
pre-onset alarm 从0升至16.7%，full recall 从79.2%降至70.8%。Block risk-clock 在 B2 的 +8 recall 下降2.86
points，仅靠0.5-token median latency 改善满足形式门槛。两者都没有形成足以启动 B3 的及时检测收益。

## 4. 研究判断

### 得到支持的部分

- `N_fit/N_shape/N_cal/N_eval` 分离可实际运行，C1 没有 representation leakage；
- 100个独立 group 的 full-path order statistic 不再退化为 sample maximum；
- 单-token novelty 的整体 normal risk 能在未见 family 上迁移到预注册研究阈值边缘；
- risk clock 可以作为 stopping calibration 变量而不重新引入 absolute-age normality assumption。

### 没有得到支持的部分

- risk-clock normalization 没有稳定优于 static full-path exchangeability；
- 8-token block 没有改善长路径风险；
- 新阈值没有把 drift detection 提前到 onset 附近；
- overall group FAR 通过不代表 workflow-specific normal coverage 已充分。

### 决策

不启动 B3。单-token static 与 risk-clock 保留为**已校准的研究诊断**，不指定事后 winner；block stopping
proposal 停止。下一阶段不应继续调整 bins、block width、quantile 或同一 novelty threshold，而应直接研究为什么
route-only novelty 在 substantive drift onset 后才积累到可报警水平，以及是否需要通用 Agent state/event 信号来
区分“合法处理某语义”和“开始执行该语义任务”。

## 5. 局限

- 新正常评价只有60个独立组；15% observed FAR 的 Wilson upper bound 仍约26%；
- 只有一个客服 Agent、一个 OLMoE checkpoint 与 final-generation episode；
- held-out 轴是 benign content family，不是新 Agent、模型或部署环境；
- B1/B2 positives 已被多轮观察，utility 结果只是 adaptive；
- 现有 evidence onset 近似 substantive work onset，不能完整表示更早的 semantic commitment；
- 没有 resisted attacks 的新独立评价，也没有强文本或 hidden-state comparator；
- 任务完成率低，虽能真实压力测试 FAR，也显示 Agent runtime 本身尚不是生产质量。

## 6. 产物与复现

运行：

```bash
.venv/bin/python scripts/evaluate_normal_calibration_c1.py \
  artifacts/agent_v2/normal_calibration_c1 \
  --config configs/normal_calibration_c1.json

.venv/bin/python scripts/run_normal_manifold_time_uniform_calibration.py

MPLCONFIGDIR=/tmp/agent-moe-matplotlib \
  .venv/bin/python scripts/plot_normal_manifold_time_uniform_calibration.py
```

结果：`artifacts/agent_v2/normal_manifold_time_uniform_calibration/result.json`

- result size：1,845,358 bytes；
- result SHA-256：`0960b92d415d9d51980ce34611efe2774a7b1f46792c12ff8640916a8b1876f6`；
- C1 cache index SHA-256：`5ba7573d6284d3a4ab2f6d93344e9ecada4dbe7d5233dd84326efcac869d11fd`；
- C1 collection report / sample index hashes：`ea44fc3b...` / `e3822468...`；
- 运行前全量 tests：157/157；
- 冻结 config SHA-256：`4f6b021546be6bcdc9461865ff5e0986d4c979cc3cc18d7ff6401da55cfda6c8`；
- B3 used：false。
