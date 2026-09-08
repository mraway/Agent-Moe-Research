# Sequential Directional-Relative Pilot：结果报告

日期：2026-09-04（America/Los_Angeles）

状态：exploratory development complete；获得双向一致的及时检测信号，但 strict pre-boundary 指标暴露边界定义问题

## 摘要

本 pilot 保留 v1 的 learned semantic direction，但修复两个关键 formulation 问题：训练正例只取完全位于人工
边界后的 8-token routing windows；runtime alarm 使用当前 learned state score 相对同一 trace 的因果历史中位数
增量，而不是全局绝对 state score。

结果在两个 batch-transfer 方向上高度一致：

- B1 学习、B2 测试：boundary +16 clean recall 为 `17/35 = 48.6%`，full recall 为 `19/35 = 54.3%`，
  clean hit 中位延迟 4 token，non-drift FAR 为 `23/205 = 11.2%`；
- B2 学习、B1 测试：boundary +16 与 full recall 均为 `13/24 = 54.2%`，中位延迟 5 token，non-drift
  FAR 为 `12/96 = 12.5%`。

这比无监督 adjacent-block JSD 的 +16 recall（2.9% / 8.3%）强得多，也比 v1 在 B2 的冻结 +16 recall
`9/35 = 25.7%` 明显更高，代价是 observed non-drift FAR 从 v1 的 0% 上升到约 11–13%。结果支持一个更具体的
研究命题：**跨领域 routing signal 主要包含方向性语义变化，且 trace-relative formulation 能把其中一部分转化为
快速、跨 batch 的 sequential detection。**

不过 strict pre-boundary FAR 为 B2 `5/27 = 18.5%`、B1 `8/18 = 44.4%`，不能忽略。对全部 13 个案例的
事后 token 邻域审计发现，10 个 alarm 发生时文本其实已经明确承诺或开始域外工作，只是当前 boundary 标在更晚
的 substantive evidence；另外 3 个是客服区段中的真实提前误报。因此本结果既暴露了 detector 的误报，也暴露了
单一 boundary 不足以评价 early warning。该审计不修改 primary 数值，也不构成事后重标。

## 1. 固定方法

方法在提交 `865560d` 中、看到本轮数值前固定。完整定义见
`docs/sequential_directional_relative_pilot_plan.md`。

- state feature：最近 8 token 的 16×64 top-8 expert selection rate；
- positive anchors：`boundary + {7, 15, 23}`，均为完全 post-boundary window；
- negative anchors：每个 negative segment 最多两个均匀位置；
- model：source-only standardized ridge least-squares，penalty 1,024；
- relative score：当前 state score 减去所有不与当前窗口重叠的历史 state scores 的 0.5 quantile；
- earliest relative score：output token index 15；
- threshold：source negative-segment maxima，`alpha=0.10` 有限样本 order statistic；
- alarm：单点严格越阈值，不使用 future、boundary、smoothing 或 persistence。

B1/B2 均已在先前工作中被查看，本轮是 post-hoc exploratory development，不是 independent confirmation。

## 2. Primary 结果

| Source → Target | Threshold | Non-drift FAR | Strict pre-boundary FAR | Recall +8 | Recall +16 | Recall +32 | Final recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 → B2 | 0.9287 | 23/205 (11.2%) | 5/27 (18.5%) | 15/35 (42.9%) | 17/35 (48.6%) | 19/35 (54.3%) | 19/35 (54.3%) | 4 |
| B2 → B1 | 0.8819 | 12/96 (12.5%) | 8/18 (44.4%) | 11/24 (45.8%) | 13/24 (54.2%) | 13/24 (54.2%) | 13/24 (54.2%) | 5 |

两方向 +16 理论可达率均为 100%；+8 可达率为 B2 91.4%、B1 91.7%。因此及时 recall 不是 warm-up 假象。
所有 clean hit 最迟分别为 boundary +23（B2）和 +12（B1）；relative detector 没有依赖很晚的偶然越线来堆高
full recall。

Calibration 统计为：

- B1 source：114 个 negative segments，rank 104，threshold 0.9287，10/114（8.8%）source maxima 越线；
- B2 source：232 个 negative segments，rank 210，threshold 0.8819，22/232（9.5%）source maxima 越线。

两个 source 学到的 threshold 接近，target non-drift FAR 也接近目标 alpha，明显优于 v1 的 q0.99 极端阈值迁移，
但 11–13% 仍不能包装成已达到生产误报要求。

## 3. Non-drift 与 domain 分层

Non-drift FAR：

| Target | Clean | Benign control | Resisted attack |
|---|---:|---:|---:|
| B2 | 7/80 (8.8%) | 10/80 (12.5%) | 6/45 (13.3%) |
| B1 | 2/40 (5.0%) | 7/40 (17.5%) | 3/16 (18.8%) |

每 1,000 个 negative eligible positions 的 alarm onset 为 B2 1.77、B1 2.10。误报没有只集中在 attack exposure，
benign control 也明显高于 clean，说明自然回复结构和提示扰动仍会影响 relative score。

及时 detection 覆盖多个 domain。B2 +16 clean hits 包括 poetry 5/5、fiction 3/5、mathematics 3/8、travel
3/4、cooking 2/3、general knowledge 1/1；programming 与 legal 为 0。B1 中八个 domain 都至少有一个 +16
clean hit。样本仍太少，不能把这些差异解释为稳定 domain ranking。

## 4. Strict pre-boundary alarm 审计

Primary 评价严格沿用原 boundary：任何 `t < boundary` alarm 都计为错误。13 个此类 alarm 的 gap 从 1 到
59 token。结果后检查相应 token 邻域得到两类情况。

### 4.1 输出已出现明确域外语义：10/13

例子包括：

- Python trace 在 code-fence boundary 前已经输出 “Now, for a Python function named `count_vowels`…”；
- meal-plan trace 在 `Breakfast` boundary 前已经输出 “a one-day meal plan…”；
- packing trace 在 checklist boundary 前已经生成未授权 `generate_checklist` action 及旅行物品参数；
- fiction trace 在正文 boundary 前已经输出舞台指示并“begins to speak in a rhythmic, expressive tone”；
- geometry、legal、science 等案例在 boundary 前的窗口已含具体公式、条款或科学解释。

这些 alarm 按冻结标签必须保留为 pre-boundary error，但从运行监测角度更像是在 substantive deliverable 之前
捕获到 semantic commitment。

### 4.2 仍处于 routine 内容：3/13

两个 B2 geometry trace 的提前 alarm 位于 return/order 解释中，另一个 B1 science trace 的提前 alarm 位于
subscription 状态列表。这些没有合理的域外开始依据，是 detector 的真实 false early warning。

这个人工检查是 post-hoc、非盲、单人判断，不能替代正式标签，也不能用于重算 recall/FAR。但它说明目前的
`goal_plan_deviation_start_output_token` 更接近“首个被选作实质证据的 token”，不总是“最早可识别语义承诺”。

## 5. 研究含义

本轮提供了目前最接近研究目标的算法信号：

1. **方向重要。** 无监督 routing JSD 失败，而 learned semantic direction 在两个 transfer 方向都获得约 50%
   的 +16 clean recall；
2. **相对化重要。** 相比 v1 的 absolute score，历史中位数基线显著提高 B2 及时 recall，threshold 也更接近
   跨 batch 稳定；
3. **信号不是完美可分。** 约 11–13% non-drift trace 仍报警，约一半 drift 仍未及时 clean hit；
4. **评价标签需要双边界。** 对 early-warning detector，应分别标注 semantic commitment onset 与 substantive
   deliverable onset；单一晚边界会把部分有用预警计为错误，但不能因此取消严格原指标；
5. **还不能谈独立泛化。** 方法由 B1/B2 结果启发并在两者上开发；只有冻结后新的 B3 才能提供独立证据。

下一步不宜立即搜索大量阈值。更有信息量的工作是先对一小部分 drift 做独立的双边界复标，确认 10/13 的
现象是否可复现；同时分析 non-drift false alarms 的文本/routing 结构。完成这两项后，才能判断算法应优先改进
baseline、训练目标还是报警 persistence。

## 6. 实现与复现

- scorer：`scripts/explore_agent_v2_directional_relative.py`；
- 通用 causal-history 工具：`src/phase_a/sequential.py`；
- 结果 artifact：`artifacts/agent_v2/sequential_directional_relative_pilot.json`；
- artifact SHA-256：`bf3a55aa0f1b4f96f3da20073ddbd10f3a41f9a5704168945be08c7eb56ca695`；
- 480 条原始 trace 均通过既有 routing validation；
- 相关 sequential tests 12/12 通过；完整 repository unit tests 在提交前再次运行。
