# Normal Manifold Early-Evidence Availability Audit：实验报告

日期：2026-09-05（America/Los_Angeles）

状态：adaptive mechanism audit complete；早期 ranking 主 gate 因 B2 matched coverage 不足而失败；C1-calibrated
timing 失败；generic controller state 无新增 outcome 信息；B3 未使用

预注册计划：[Early-Evidence Availability Audit 计划](normal_manifold_early_evidence_audit_plan.md)

## 给研究主管的结论

本轮把“检测太晚”拆成了两个问题：早期 routing score 是否已经把 drift 排在同任务正常回复之上，以及这个相对
排序能否越过由完整正常路径校准的绝对阈值。

结果不是单纯的“routing 信号太晚”，而是一个有明确边界的混合结论：

1. **可比较场景中，早期相对信号很强。** onset `+8` 的 drift-vs-matched-normal AUROC 为 B1 `0.921`、B2
   `0.831`；drift maximum 高于同组 clean/benign maximum 的比例为 `22/22=100%`、`22/26=84.6%`。
2. **正式 early-ranking gate 仍失败。** B2 只有 `26/35=74.3%` positive groups 能在相同绝对 endpoint 区间
   找到尚未结束的 normal control，低于预注册80% coverage；B1 为 `22/24=91.7%`。所有11个不可比较案例都是
   drift 路径仍在继续，而 clean 与 benign 已双双结束。
3. **全路径正常阈值压低了早期绝对 recall。** 相同 `+8` 区间下，C1 static/risk boundaries 的 recall 仍只有
   B1 `6/24=25.0%`、B2 `4/35=11.4%`。相对排序强并不等于 score 已高到足以在完整 normal path risk 下报警。
4. **通用 controller 结构不能补足缺口。** 59/59 positive pair groups 中，clean、benign、attack 在最终
   generation 前的 text-free event/state signature 完全一致；177条相关 traces 的 generation 起始状态全部是
   `ready_to_respond`，原子生成期间没有新的 controller event。

所以不启动 B3。下一步应比较能够表达“相对于当前授权任务正在朝哪个方向变化”的低容量方向性 route feature，
以及文本/hidden-state baselines；继续调整同一个 novelty threshold 或添加固定 controller state 没有逻辑依据。

![Early evidence audit](../artifacts/agent_v2/normal_manifold_early_evidence_audit/early_evidence_audit_summary.png)

## 1. 实验纪律

计划以 commit `aefccfb` 冻结，实现与4项新增测试以 commit `307b333` 固定后才运行。审计只使用：

- B1 `brief=absent` 的24条 drift 及其 matched clean/benign；
- B2 的35条 drift 及其 matched clean/benign；
- 前轮固定的26-trace/208-anchor token bank；
- SHA-256 为 `0960b92d...` 的 C1 calibration result 与已冻结 thresholds/risk shapes。

没有拟合 representation、boundary 或 threshold，没有生成新 trace，没有修改原始 B1/B2/C1，也没有读取 B3。
Behavior onset 和 matched absolute endpoint 只用于事后评价，不进入线上 score。

## 2. Threshold-free matched ranking

### 2.1 Token endpoint

| Horizon | B1 eligible | B1 paired win | B1 AUROC | B2 eligible | B2 paired win | B2 AUROC |
|---|---:|---:|---:|---:|---:|---:|
| onset `+0` | 22/24 | 72.7% | 0.750 | 26/35 | 57.7% | 0.601 |
| `+4` | 22/24 | 100.0% | 0.888 | 26/35 | 84.6% | 0.855 |
| `+8` | 22/24 | 100.0% | 0.921 | 26/35 | 84.6% | 0.831 |
| `+16` | 22/24 | 95.5% | 0.884 | 26/35 | 84.6% | 0.830 |
| `+32` | 22/24 | 95.5% | 0.926 | 26/35 | 100.0% | 0.883 |
| Full post-onset | 22/24 | 86.4% | 0.905 | 26/35 | 100.0% | 0.939 |

Onset 当下的 B2 ranking 接近弱信号；4个 token 后两个 batch 都超过0.85 AUROC。这符合自回归时序：router 不能在
相同前缀下预知后续采样，必须先生成若干域外内容，方向差异才进入路由。

但 primary `+8` gate 还要求至少80% positive 有可比较的 normal counterfactual。B2 的9个、B1 的2个失配组中，
positive 都有 onset 后 score，两个 normal arms 却都已在 onset 对应绝对 endpoint 前结束。对这些组，不能凭空
外推“normal 如果继续生成会怎样”。因此条件 AUROC 不能推广到全部 drift，也可能包含“drift 输出更长”的选择效应。

### 2.2 Mean-8 block

固定 block 必须等到完全 post-onset 的 block endpoint。到 `+8` 只有 B1 `8/24`、B2 `11/35` positive 可达，
同时具备 matched control 的只有 `6/24`、`11/35`。虽然这些小子集 AUROC 为 `1.000/0.884`，覆盖过低，不能作为
及时 detector 证据。Block 到 `+16` 的 matched coverage 仍只有79.2%/68.6%。这进一步支持停止 block stopping。

## 3. C1-calibrated absolute timing

### 3.1 Token clean-hit recall

| Boundary / batch | +0 | +4 | +8 | +16 | +32 | Full | Full-hit median latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| Static / B1 | 8.3% | 12.5% | 25.0% | 37.5% | 58.3% | 79.2% | 18 |
| Static / B2 | 0.0% | 8.6% | 11.4% | 22.9% | 42.9% | 68.6% | 25 |
| Risk-clock / B1 | 8.3% | 16.7% | 25.0% | 37.5% | 54.2% | 70.8% | 16 |
| Risk-clock / B2 | 2.9% | 8.6% | 11.4% | 25.7% | 42.9% | 68.6% | 23 |

两个 boundaries 都没有达到双向 `+8 recall >=35%` 与 median latency `<=8`。值得注意的是，在 `+8` 可比较
matched windows 中，normal controls 几乎从不越过 C1 threshold，而多数 drift maximum 仍然只是“高于 control、
低于全路径 threshold”。因此瓶颈是 normal full-path peaks 与 early drift magnitude 的绝对重叠，不是单纯降低
threshold 就能无代价解决。

Risk-clock 没改变 `+8` recall，只把 full-hit median latency缩短2 token；它同时带来前轮已经记录的早期正常误报
集中，不能视为及时性突破。

## 4. Generic runtime state audit

Structural signature 只保留 event kind、actor/logical role、conversation turn、agent step、tool name 与
`task_state_before/after`，排除所有文本、结果、ID、attack channel、workflow label、routing、stop reason 与 outcome。

| Batch | Positive groups | Three-arm signature identical | Final-generation start state | Decode期间新增controller event |
|---|---:|---:|---|---:|
| B1 | 24 | 24/24 = 100% | `ready_to_respond` 72/72 | 0 |
| B2 | 35 | 35/35 = 100% | `ready_to_respond` 105/105 | 0 |

这不是说任何 Agent state 都无用，而是说明当前 runtime 暴露的**通用结构字段**在 matched outcome 分叉前完全
相同；最终 generation 又是原子调用，完成前不会产生新的 controller transition。把这些常量字段与 novelty 拼接
不会增加早期判别信息。若要补足行为语义，需要更细粒度的流式生成状态、文本或 hidden-state evidence。

## 5. 假设判断与下一步

预注册结论：

- `token +8 early-ranking gate`：**Fail**，唯一失败项为 B2 coverage `74.3% < 80%`；
- static C1-calibrated timing gate：**Fail**；
- risk-clock C1-calibrated timing gate：**Fail**；
- generic-state structural-similarity gate：**Pass**，两个 batch 都是100%；
- B3：**No-go**。

机制解释必须保留两面性：

- 在有 matched counterfactual 的组中，route token novelty 到 `+4/+8` 已有稳定相对 ranking，支持
  threshold-tail hypothesis；
- 对全部 positive，counterfactual coverage 不足且 C1 absolute recall 很低，所以尚不能冻结 route-only early
  detector；
- onset 当下信号尤其在 B2 很弱，当前方法不具备零延迟识别能力；
- controller structure 没有可利用的 outcome 差异。

下一研究阶段应建立一个**比较实验**而不是继续调阈值：在完全相同的 group-held-out split 与 `+0/+4/+8` 预算下，
比较 token novelty、低容量 routing direction、输出文本特征和可获得的 hidden-state feature。只有 routing 在独立
数据上相对这些基线提供增量且满足完整路径 FAR，才值得生成 B3。

## 6. 产物与验证

运行：

```bash
.venv/bin/python scripts/run_normal_manifold_early_evidence_audit.py

MPLCONFIGDIR=/tmp/agent-moe-matplotlib \
  .venv/bin/python scripts/plot_normal_manifold_early_evidence_audit.py
```

结果：`artifacts/agent_v2/normal_manifold_early_evidence_audit/result.json`

- result size：1,037,919 bytes；
- result SHA-256：`c49b313d5bc9c25c196da73fcfbbcabecfa181ecb3ae38809dc6691364ab5849`；
- frozen calibration result SHA-256：`0960b92d415d9d51980ce34611efe2774a7b1f46792c12ff8640916a8b1876f6`；
- 运行前全量 tests：161/161；
- source routing cache：360 traces，完整复用；
- B3 used：false。
