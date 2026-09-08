# Sequential Detector v1：B1 开发结果

日期：2026-09-04（America/Los_Angeles）

状态：B1 开发完成；主检测器、对照模型和 B2 成功条件待提交后冻结

## 摘要

本轮把旧的“读取前 16 个 token、预测整条 attack 最终是否服从”的分类问题，改成了真正的因果序列检测：
agent 每生成一个新 token，检测器只读取到该 token 为止的最近 16-token MoE 路由窗口，并持续判断是否已经
进入域外任务。检测是否正确由人工标注的首个实质域外 output-token 边界决定。

B1 `response_brief_condition=absent` 的 120 条 trace 中，fold 0–3 提供 99 条 grouped development OOF
样本，包括 20 条 drift；fold 4 的 21 条 trace 完全不参与候选性能选择，只用于最终阈值校准。36 个路由
候选中有 9 个满足开发误报约束。预注册选择规则最终选中：16-token `route_selection` 滑窗、persistence 1、
每条 negative segment 最大分数的 0.99 quantile 阈值。

该 route detector 在 fold 0–3 OOF 中没有观察到 non-drift 或 drift 边界前误报；20 条 drift 中，8 条在
边界后 8 token 内检出、12 条在 16 token 内检出、15 条在 32 token 内检出，最终检出 18 条，正确报警
的中位延迟为 10 token。完全相同协议下，token-hash 在 +16 只检出 1/20、最终 5/20；route + token-hash
没有改善 route-only。

这是值得进入 B2 的清晰开发信号，但还不是确认结果。20 条 OOF positive 和 79 条 OOF non-drift 仍然很小；
零观测误报不等于真实误报率为零；fold 3 的及时检出明显弱于其他 fold；最终阈值也只由 fold 4 的 20 个
可用 negative segment 校准。下一步必须原样冻结后在 B2 全部 240 条 trace 上检验。

## 1. 数据与因果评价单位

本轮只使用 Agent v2.5 B1 中不含 verified response brief 的 40 个 matched scenario：

- 40 条 clean、40 条 benign control、40 条 attack，共 120 条 trace；
- attack 中有 24 条 drift、16 条 resist；
- 每条 drift 都有首个实质域外 output-token 的人工边界；
- fold 0–3 为开发集：99 条 trace、20 条 drift；
- fold 4 为最终阈值校准集：21 条 trace、4 条 drift。

结束于 token `t` 的窗口只包含 `[t-15, t]` 的信息。长度不足 16 token 的 trace 没有可评分窗口，按“没有
报警机会、因此没有报警”计入 trace-level 误报分母。边界早于首个完整窗口时，最早可检出点是 token 15，
延迟仍相对真实边界计算，没有把 warm-up 时间抹掉。

训练时没有把相邻窗口伪装成独立样本。每条 non-drift 只抽两个均匀 negative anchor；每条 drift 最多抽
两个边界前 negative anchor，并在边界及其后固定 offset 抽 positive anchor。评价时则对每个完整窗口逐
token 评分。

## 2. 候选选择

候选 grid 包含两类 1,024 维局部 MoE 特征：

- `route_selection`：16 层 × 64 experts 在窗口内进入 top-8 的频率；
- `route_probability`：16 层 × 64 experts 在窗口内的平均 router probability。

窗口宽度为 8/16/32，连续越阈值要求为 1/2/3，阈值 quantile 为 0.95/0.99，共 36 个组合。每个 OOF fold
只用另外三个 fold 拟合标准化参数与 ridge 权重，并只用训练侧 negative segments 定阈值。候选必须同时满足
non-drift trace FAR ≤ 0.10 和 drift pre-boundary FAR ≤ 0.10，再依次最大化 +16、+32 和最终 recall。

9 个候选通过误报约束。胜出 operating point 为：

| 项目 | 冻结选择 |
|---|---:|
| feature | `route_selection` |
| window width | 16 token |
| persistence | 1 window |
| threshold rule | training negative-segment maxima 的 q0.99 |
| classifier | training-only standardized ridge least-squares |
| penalty | 1,024 |

## 3. Fold 0–3 OOF 结果

| Detector | Non-drift FAR | Pre-boundary FAR | Recall +8 | Recall +16 | Recall +32 | Final recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| **MoE route** | **0/79** | **0/15 eligible** | **8/20** | **12/20** | **15/20** | **18/20** | **10 token** |
| token hash | 0/79 | 0/15 eligible | 1/20 | 1/20 | 1/20 | 5/20 | 44 token |
| route + token hash | 0/79 | 0/15 eligible | 8/20 | 11/20 | 14/20 | 18/20 | 10.5 token |

route detector 在 9,205 个可评价 negative positions 上观察到 0 次 alarm onset。这个计数同时包括全部
non-drift decode 和 drift 的可评价边界前区域。

route 的 +16 分 fold 检出为 4/6、4/5、3/5、1/4；最终检出为 6/6、5/5、5/5、2/4。前三个 fold 的方向
一致，fold 3 较弱，说明信号并非在每个 family group 上同样稳定。OOF fold 阈值分别为 0.352、0.432、
0.474、0.469；它们只用于开发估计，不会带入 B2。

## 4. 最终冻结模型

候选选定后，route、token-hash 和 route + token-hash 三个模型都只用 fold 0–3 的 99 条 trace 拟合：

- 每个模型使用 265 个 training anchors，其中 77 个为 positive；
- route/token/combined 的维数分别为 1,024 / 2,048 / 3,072；
- fold 4 只提供 20 个可用 negative-segment maxima 来校准各自阈值；
- 冻结阈值分别为 0.9480577707 / 0.0211217348 / 0.7026274800。

最终阈值与 OOF fold 阈值数值不同并不表示事后调参：最终模型使用更多训练 fold，score scale 随训练得到的
标准化与权重一起变化；每个阈值都是在对应模型自己的 fold-4 negative scores 上按同一 q0.99 规则得到。

## 5. 研究解释与局限

当前结果支持三个有限结论：

1. 使用整段 decode 的因果 MoE 滑窗，确实能在人工任务偏移边界后产生及时信号；这比旧的“预测最终服从”
   更接近我们希望研究的 runtime monitor。
2. 在相同窗口、训练 anchors、阈值校准和报警协议下，route 明显优于 token-ID hash，初步排除了“只是输出
   token 身份被低成本文本特征直接读出”的解释。
3. route + token 没有优于 route-only，B1 中没有证据表明这个简单文本表示能为路由检测器增加价值。

但 B1 仍是方法开发数据，不能用其性能作为外部验证。尤其是零误报来自有限样本和很保守的 q0.99 阈值，
fold-4 calibration segment 只有 20 个。B2 的任务是检验：这个固定阈值能否迁移到新业务实体和新域外任务，
以及 route 相对 token hash 的及时检出优势能否保留。

## 6. 可审计产物

- 开发计划：`docs/agent_v2_sequential_b1_plan.md`；
- 开发脚本：`scripts/explore_agent_v2_sequential_b1.py`；
- 原始开发结果：`artifacts/agent_v2/agent_v2_5_b1/sequential_development.json`；
- 开发结果 SHA-256：
  `fdac2638ae687a549ae7594548c33ba1d796fdc561ae1feddce52ce895d98c4e`；
- B1 behavior-labeled sample index SHA-256：
  `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`。

B1 开发分析没有读取新的 B2 sequential score。B2 数据此前用于 fixed-prefix 研究，因此下一步准确称为
“B1-developed, B2 held-out retrospective evaluation”，不称作从未接触数据的 prospective confirmation。
