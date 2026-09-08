# Proposal 4：Normal Transition Retrieval（NTR）冻结实验报告

日期：2026-09-05（America/Los_Angeles）

分析 ID：`agent-v2-proposal4-ntr-development-v1`

预注册提交：`58edc3e`（`docs: preregister four routing mechanism proposals`）

结论状态：`invalid_or_inconclusive`（冻结的正常校准样本门未满足）

## 主管摘要

本次实验没有得到 NTR 有效或无效的性能结论。冻结程序完成了正常 reference bank 构建以及全部320条 C1 normal
trace 的 NTR 评分，但随后在预注册样本门停止：100个校准 matched groups 中，只有59组的 clean 与
benign-control 两条输出都达到48 tokens，低于冻结要求的80组。

程序按预注册原样失败，没有降低最小group数、缩短窗口、放宽“双arm均需完整暴露”的规则或改用单trace校准；
也没有进入 B2 384-token目标replay的读取和评分。因此：

- 正常FAR、execution召回、recovery召回和execution-vs-resisted AUROC均未产生；
- 不能把本次停止解释为NTR算法的负性能，也不能声称算法通过；
- B3没有生成、读取或使用；
- 预注册的 `result.json` 没有创建，因此不存在结果artifact SHA-256。

直接原因是数据与算法时域不匹配：NTR需要16-token前驱和32-token后继，而现有C1中相当多的正常输出不足48
tokens。未来若继续验证，应先独立、前瞻性地收集足够长的正常输出，再重新预注册实验；不能修补本次冻结分析。

## 1. 冻结方法

NTR只使用生成期MoE top-k routing，不读取文本、任务、workflow、attack channel、领域、arm或绝对token位置。

每个候选transition由三个互不重叠的16-token routing blocks组成：

1. predecessor `[s,s+15]`；
2. near successor `[s+16,s+31]`；
3. far successor `[s+32,s+47]`。

每个block按层统计top-8 expert selection frequency，并使用Hellinger距离。query predecessor先从normal bank检索
16个最近前驱；在固定的这组edge中，near与far successor分别取第5近邻距离，得到共享normal尺度下的
`z_near`和`z_far`。

两个冻结head为：

- `Recovery = z_near - z_far`；
- `Persistence = min(z_near, z_far)`。

两个阈值本应分别使用C1 folds 0--2的matched-group完整路径maximum、`alpha_head=0.05`校准；folds 3--4只用于
held-out normal risk。首次严格越线即冻结状态。长度不足48 tokens的trace记为删失，不作为正确负例。

## 2. 分析前reference stride可行性修正

在提交预注册、读取目标routing之前，曾以输出长度元数据检查reference bank可执行性：

- 若不同reference edges也使用48-token stride完全互斥，只能得到32条edges、来自19条source traces，低于原定
  64-edge下限；
- 因此在正式冻结前把reference stride设为16 tokens，得到预期65条edges、19条source traces；
- 关键机制约束没有改变：每条edge内部的16-token predecessor、near successor和far successor仍共享0 token；
- 同一normal source产生的所有edges在reference normalization时整体leave-one-source-trace-out，避免同trace
  future泄漏。

该修正发生在预注册提交前，不是看到目标结果后的调整。冻结主运行的bank audit与长度预审一致。

## 3. 执行命令与停止位置

唯一主分析命令为：

```bash
.venv/bin/python scripts/run_agent_v2_proposal4_ntr.py --execute-routing-analysis
```

执行顺序和结果：

1. 冻结plan及C1/replay输入hash检查通过；
2. 26条canonical clean fit traces加载完成；
3. NTR normal transition bank构建完成并通过bank最小样本门；
4. C1评分完成：`320/320`；
5. 在 `calibrate_group_thresholds` 中触发冻结错误：
   `ValueError: NTR has too few eligible C1 calibration groups`；
6. 程序以exit code `1`结束；
7. 阈值尚未生成，B2目标replay loader/scorer尚未执行。

## 4. 完整性与bank audit

| 检查项 | 结果 |
|---|---:|
| 预注册plan SHA-256 | `cba199e206b55ca4b476dace5ccbaa94e2aa486e83a796866398bf4ea13d9beb` |
| C1 sample index SHA-256 | `e3822468098e25fb21fb3b67753c07fd73bb765777cc22b83548d06f8970755f` |
| C1 behavior-only report SHA-256 | `ea44fc3be535e47077c4f3557256285f060c9f8180bfba4e1d279de704f7b7e5` |
| replay sample index SHA-256 | `5399b0b32769c8ef98722080da9ef1f159f586fba6d1d907656afcc8e34eac10` |
| exact-prefix audit SHA-256 | `3405298d0cc5d29a07899f32dc113bb84d08334de51bab6ce1dd442053a41359` |
| routing-blind labels SHA-256 | `8ad05186bbe7e508f3019a8c91aa71df8e369922bcdd4562b367bd7f298839d7` |
| engagement summary SHA-256 | `50b890ee4e30ff81855db5547cef53636669a5c4207c6930a969844736fae5e3` |
| exact paired replay gate | 通过，但目标routing未读取 |
| B3使用 | 0 |

Bank audit：

| 项目 | 值 | 冻结要求 | 结果 |
|---|---:|---:|---|
| reference edges | 65 | `>=64` | 通过 |
| source traces | 19 | `>=18` | 通过 |
| edge内部共享tokens | 0 | 0 | 通过 |
| 跨reference edge重叠对 | 46 | 允许，且LOTO按source排除 | 审计值 |
| pooled LOTO center | 0.4277294874 | fit-only | 已计算 |
| pooled LOTO scale | 0.04048178345 | `>0` | 通过 |

跨reference edge重叠不是旧conditional-successor问题的复现。旧方法在同一transition的predecessor与successor
之间共享7/8 tokens；NTR中同一edge的三个block严格零重叠。46只表示使用16-token stride时，不同normal
reference examples之间有共享tokens；训练期同source整体排除，正常FAR则按独立matched group校准。

## 5. 样本分母与删失

### 5.1 C1 calibration（folds 0--2）

| 项目 | 数量 |
|---|---:|
| 总traces | 200 |
| 总matched groups | 100 |
| `T>=48` eligible traces | 145 |
| censored traces | 55 |
| clean censored traces | 26 |
| benign-control censored traces | 29 |
| 双arm均eligible groups | 59 |
| censored groups | 41 |
| 冻结最小eligible groups | 80 |

主停止门为 `59 < 80`。校准trace长度范围为17--192 tokens；短输出不是零星异常，而是造成41%的group不能进入
冻结校准。

### 5.2 C1 held-out normal（folds 3--4）

虽然程序已完成这些trace的routing score计算，但因校准门先失败，没有产生阈值或FAR。仅按输出长度审计：

| 项目 | 数量 |
|---|---:|
| 总traces | 120 |
| 总matched groups | 60 |
| `T>=48` eligible traces | 92 |
| censored traces | 28 |
| clean censored traces | 13 |
| benign-control censored traces | 15 |
| 双arm均eligible groups | 38 |
| censored groups | 22 |
| 冻结最小eligible groups | 48 |

该集合也会触发第二个样本门：`38 < 48`。因此即使事后绕过校准门，held-out normal risk仍不满足预注册的最小
分母要求。

### 5.3 B2目标replay

| 项目 | 结果 |
|---|---|
| 目标trace routing读取/评分 | 未执行 |
| bounded-resisted分母 | 未评价 |
| cross-domain execution分母 | 未评价 |
| silent-resistance分母 | 未评价 |
| oracle-onset窗口覆盖 | 未评价 |

## 6. 全部门槛状态

| 冻结门 | 状态 | 证据 |
|---|---|---|
| 输入hash与behavior-only检查 | 通过 | 主程序进入bank/C1评分 |
| reference bank至少64 edges | 通过 | 65 |
| reference bank至少18 sources | 通过 | 19 |
| C1 calibration至少80 eligible groups | **失败** | 59/100 |
| C1 held-out至少48 eligible groups | **失败（长度审计）** | 38/60 |
| held-out group any-head FAR `<=0.15` | 未评价 | 无阈值 |
| held-out clean FAR `<=0.15` | 未评价 | 无阈值 |
| held-out benign FAR `<=0.15` | 未评价 | 无阈值 |
| execution eligible `>=20` | 未评价 | 未读取目标routing |
| execution recall `>=0.50` | 未评价 | 未读取目标routing |
| bounded eligible `>=3` | 未评价 | 未读取目标routing |
| bounded recovery recall `>=0.60` | 未评价 | 未读取目标routing |
| pre-onset alarm `<=0.10` | 未评价 | 未读取目标routing |
| ambiguous rate `<=0.20` | 未评价 | 未读取目标routing |
| oracle AUROC `>=0.70` | 未评价 | 未读取目标routing |
| oracle bootstrap CI下界 `>0` | 未评价 | 未读取目标routing |
| mechanism confirmation各类 `n>=12` | 未评价且既知当前bounded总数仅5 | 当前批次不能确认 |

因此最终状态依据预注册停止规则为 `invalid_or_inconclusive`，而不是`development_go`或
`development_no_go`。

## 7. 负结果及其含义

本次结果否定的不是“normal transition retrieval存在信号”，而是以下实验可行性假设：

> 现有C1包含足够多完整的48-token正常轨迹，可以在不删失偏差过大的条件下校准和评价NTR-v1。

该假设被明确否定。若用现有数据强行继续，将面临两个问题：

1. 只保留较长正常输出会改变被评价的正常总体；
2. 降低group门或允许单arm group会破坏已冻结的独立风险单位和统计可信度。

因此没有采用任何事后替代。

## 8. 局限与后续研究条件

- 48-token时域带来至少32-token的状态分类等待时间，并天然排除短输出；这是NTR-v1的通用算法成本，不是某种
  特殊输出格式问题。
- normal reference只有65条edges/19个source traces；即使通过样本门，conditional successor support仍可能稀疏。
- 当前replay行为标签已被观察，因此后续在B2上的结果最多是development evidence。
- bounded-resisted总数只有5，无法满足未来机制确认所需的12条。

若继续这一proposal，合规路径是新建独立实验：预先收集所有arm至少48 tokens、具有足够独立matched groups的
normal calibration batch，然后在不查看目标routing的情况下重新冻结NTR。当前实验及其失败状态应保持不变。

## 9. 产物与SHA-256

- 计划结果路径：`artifacts/agent_v2/proposal4_ntr/result.json`
- 实际结果：未创建；程序在阈值校准前按冻结样本门退出。
- `result.json` SHA-256：`N/A (artifact not created by frozen run)`。

不应人为创建一个看似完整的`result.json`，因为它会错误暗示阈值、目标评分和性能评价已经完成。本报告是本次冻结
失败的唯一新增研究产物。
