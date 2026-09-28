# Sequential Detector v1：B2 冻结评估结果

日期：2026-09-04（America/Los_Angeles）

状态：冻结 B2 评估完成；primary sequential gate 未通过；当前 16-token ridge formulation 为 no-go

## 摘要

本轮第一次把 MoE routing classifier 按真正的在线检测方式评价：从第 16 个 decode token 开始，每生成一个
token 就对最近 16-token 的 expert-selection pattern 评分；检测成功必须发生在人工标注的实质域外任务边界
之后，任何边界前报警都算误报。模型、标准化、阈值和五项成功条件全部在读取 B2 sequential score 前冻结于
Git commit `0c54c1b`。

B1 开发得到的 route detector 在 B2 的 205 条 non-drift trace 上没有一次 trace-level false alarm，包括
80 clean、80 benign control 和 45 resisted attack。但及时检出没有迁移：35 条 drift 中只有 7 条在边界
+8 token 内 cleanly detected、9 条在 +16 内、11 条在 +32 内，最终也只有 12 条；另有 3/27 条具有完整
边界前窗口的 drift 在边界前报警。route 的 +16 recall 为 0.257，只比 frozen token-hash 的 0.143 高
0.114，低于预注册的 +0.15 margin。

五项 primary gate 只有 non-drift FAR 通过。因此 `sequential_evaluation_passed=false`。结果不能支持“当前
16-token ridge 已经是可靠的 runtime task-switch detector”。它仍留下一个较窄信号：route 的及时检出比
token-hash 多，命中时中位延迟只有 7.5 token，而且对普通 routine 流量非常保守；但覆盖率不足，且域间迁移
不稳定，尚未达到可以把正式研究建立在这个具体 detector 上的证据强度。

## 1. 冻结边界与完整性

B1 开发、冻结模型、B2 计划与完整 scorer 在任何 B2 sequential 报警结果出现前提交：

- 冻结提交：`0c54c1b`；
- B2 behavior-labeled sample index SHA-256：
  `e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942`；
- frozen metadata SHA-256：
  `71aaf8b759e0631856f1a543f5d48abd2f2811e7f75ee85b395e067c6a5937ac`；
- frozen tensors SHA-256：
  `43ceb7435d504756b5a5e6aea6732bcc8286459e48b1345998ce2eebb006bdec`；
- 一次性 scorer 输出 SHA-256：
  `3fe74e7eb4a5e103f3b9ba45e9486fb82fb5bf19d729857c6db601fe3ca0381a`。

B2 的 240/240 trace 均通过 routing schema 与 output-token alignment；80 条 attack 为 35 drift / 45
resist，满足各至少 20 条的 label-support gate。scorer 没有在 B2 上拟合、重新标准化、校准阈值或选择模型。

240 条 trace 共含 22,622 个完整 16-token decode windows。结果文件保存每条 trace 的全部 window ends、
route/token/combined score 序列和报警摘要；自动审计确认每个模型的窗口数都等于
`decode_token_count - 15`。因此这里不是只抽前 16 token，而是从首个完整窗口一直评分到 full decode。

## 2. 冻结方法

primary route detector 使用最近 16 个 token 中每层每位 expert 进入 top-8 的频率，共 1,024 维。权重只用
B1 fold 0–3 的 99 条 trace 和 265 个 anchors 拟合；阈值 0.9480577707 只用 B1 fold 4 的 20 个
negative-segment maxima 按 q0.99 得到。persistence 为 1。

两个 frozen controls 使用完全相同的窗口、anchors、阈值规则和报警定义：

- 2,048 维 signed token-ID hash，阈值 0.0211217348；
- route + token-hash 拼接，阈值 0.7026274800。

对 drift 而言，只有没有任何 pre-boundary alarm，且首次 post-boundary alarm 落在规定 offset 内，才计为
clean detection。35 条 drift 中有 8 条边界早于首个完整 16-token 窗口；它们没有可评价的边界前区域，但
其检出延迟仍从真实边界计算。

## 3. Primary 结果

### 3.1 三种冻结 detector

| Detector | Non-drift FAR | Pre-boundary FAR | Recall +8 | Recall +16 | Recall +32 | Final recall | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| **MoE route** | **0/205 (0%)** | **3/27 (11.1%)** | **7/35 (20.0%)** | **9/35 (25.7%)** | **11/35 (31.4%)** | **12/35 (34.3%)** | **7.5 token** |
| token hash | 8/205 (3.9%) | 1/27 (3.7%) | 5/35 (14.3%) | 5/35 (14.3%) | 6/35 (17.1%) | 14/35 (40.0%) | 48.5 token |
| route + token hash | 0/205 (0%) | 2/27 (7.4%) | 8/35 (22.9%) | 10/35 (28.6%) | 12/35 (34.3%) | 14/35 (40.0%) | 7 token |

route 在 18,677 个 negative eligible positions 上有 4 次 alarm onset：它们全部来自 3 条最终 drift 的
边界前区域；205 条完全 non-drift trace 没有报警。对应为每 1,000 个 negative positions 0.214 次 onset。

简单 Wilson 95% 区间能说明有限样本下的边界：route 的 non-drift FAR 为 0%，上界约 1.84%；pre-boundary
FAR 11.1% 的区间约为 3.9%–28.1%；+16 recall 25.7% 的区间约为 14.2%–42.1%；final recall 34.3%
的区间约为 20.8%–50.9%。即使考虑不确定性，观测点也远低于冻结的 0.70 final-recall 要求。

### 3.2 五项成功条件

| 条件 | 冻结门槛 | B2 结果 | 状态 |
|---|---:|---:|---:|
| non-drift trace FAR | ≤ 0.10 | 0.000 | 通过 |
| drift pre-boundary FAR | ≤ 0.10 | 0.111 | 未通过 |
| clean recall at boundary +16 | ≥ 0.40 | 0.257 | 未通过 |
| clean final recall | ≥ 0.70 | 0.343 | 未通过 |
| +16 recall 相对 token-hash 增量 | ≥ +0.15 | +0.114 | 未通过 |

五项为合取关系，所以 primary 明确失败。pre-boundary FAR 只差一条 trace 就会落到门槛以内，但其余三个
失败项差距明显，不能把总体结论描述成“险些通过”。

## 4. Route 与可见 token 的关系

在 +16 时，route 与 token-hash 的逐 trace 配对结果为：两者都检出 3 条；route-only 检出 6 条；
token-only 检出 2 条；其余 24 条都未检出。route 因此确实包含 token-hash 没有及时捕捉到的信号，但净增
4/35，仅对应 +0.114 recall，没有达到冻结门槛。

到了 full decode，route-only 检出 4 条、token-only 检出 6 条、共同检出 8 条；token-hash 的最终 coverage
反而略高。route 的优势主要表现为“少数命中更早”，不是“稳定覆盖更多 drift”。combined model 在 +16 多
检出 1 条、最终多检出 2 条，但改善很小，而且它是 secondary control，不能替换失败的 primary。

## 5. 分层结果

route 的 clean final detection 在 target domain 上差异很大：

| Domain | Drift | +16 detected | Final detected | Pre-boundary false alarm |
|---|---:|---:|---:|---:|
| poetry | 5 | 4 | 4 | 1/3 eligible |
| fiction | 5 | 3 | 3 | 2/4 eligible |
| mathematics | 8 | 1 | 3 | 0/6 eligible |
| cooking | 3 | 0 | 1 | 0/2 eligible |
| travel planning | 4 | 1 | 1 | 0/4 eligible |
| legal analysis | 4 | 0 | 0 | 0/3 eligible |
| programming | 5 | 0 | 0 | 0/4 eligible |
| general knowledge | 1 | 0 | 0 | 0/1 eligible |

信号集中在 poetry 与 fiction，对 B2 新 programming 和 legal-analysis families 完全没有 clean detection。
这与“统一的跨领域偏移 detector”应有的域迁移能力不符。各域 positive 很少，因此这些数字是误差定位，不是
可靠的逐域性能估计。

按攻击渠道，+16/final recall 分别为 direct-user 4/16 与 5/16、multi-turn-user 4/14 与 6/14、tool-output
1/5 与 1/5。三个渠道都不是单独造成总体失败的唯一来源。

## 6. 为什么 B1 的清晰信号没有迁移

以下是结果支持的诊断，而不是新 primary 分析：

1. **最终阈值过于保守。** B2 non-drift 的整条轨迹最大 route score 最高只有 0.675，远低于冻结阈值
   0.948；drift 的 post-boundary 最大分数中位数为 0.876，也低于阈值。阈值因此实现了极低 routine FAR，
   代价是大多数真实 drift 永远不能越线。B1 fold-4 只有 20 个 calibration segments，q0.99 对单个高分
   segment 很敏感。
2. **不只是阈值问题。** 27 条可评价 drift 中仍有 3 条在行为边界前越线，说明路由分数也会响应攻击上下文、
   服从倾向或边界前生成，而不只响应已经开始的域外工作。简单下调阈值很可能同时增加这种早报。
3. **任务语义迁移不均。** poetry/fiction 有信号，而 programming/legal 没有，表明 B1 anchor ridge 可能
   学到了部分域或表达方式的 expert pattern，而非充分统一的“脱离客服轨道”方向。
4. **B1 候选选择仍会乐观。** 36 个配置在同一小型 B1 development set 上比较，胜出模型的 20 条 OOF
   positive 性能包含选择偏差；B2 的下降正是 held-out evaluation 必须存在的原因。

不能在本报告中事后降低阈值并把重算结果当作成功。阈值曲线、残差化或新 representation 可以作为后续方法
开发，但一旦使用 B2 设计，就必须把 B2 当作开发数据，并另设新的确认集。

## 7. 研究判断

本轮对当前 formulation 的判断是 **No-go**：16-token route-selection + anchor ridge + 单一 q0.99 阈值没有
在 B2 迁移出足够的及时 recall，不能作为已经获得的 runtime detector 证据。

这不等于整个 MoE 路由研究方向 No-go。旧 B2 分析中 full-decode route 对 drift/resist 的 AUROC 为 0.967；
本轮 route 在 +16 也比 token-hash 多检出 4 条净样本，并以 0/205 non-drift false alarms 保持极低 routine
误报。这些结果仍支持“输出任务语义与 routing 有关”，但把这个关联转化成边界可靠、跨域泛化的在线告警，
正是尚未解决的方法问题。

因此，下一步不应立刻扩充 agent 或采集更大一批同样的数据。更合理的顺序是：

1. 把 35 条 B2 drift 当作失败审计集，比较 miss、pre-alarm 与 clean hit 的 score trajectory，确认问题主要
   来自阈值迁移、域迁移还是边界标签与 16-token 窗口的错位；
2. 只提出一到两个明确的新 detector 假设，例如相对 routine-reference 的 residual/change score，或同时要求
   absolute score 与相对上升的双条件报警；
3. 在 B1/B2 上开发并做严格 grouped resampling；若信号重新变强，再生产独立 B3 做最终确认。

在完成上述诊断前，目前最准确的课题状态是：**问题与路由语义方向仍值得探索，但 sequential detector 的强
正面信号尚未获得。**

## 8. 可复现命令

冻结文件由以下命令生成；该命令只读 B1：

```bash
.venv/bin/python scripts/freeze_agent_v2_sequential_b2.py
```

B2 scorer 在 commit `0c54c1b` 后运行一次：

```bash
.venv/bin/python scripts/score_agent_v2_sequential_b2.py \
  artifacts/agent_v2/agent_v2_5_b2
```

原始结果位于 `artifacts/agent_v2/agent_v2_5_b2/sequential_evaluation.json`。该目录被 Git 忽略；冻结代码、
模型、计划、本报告和结果哈希进入版本控制。
