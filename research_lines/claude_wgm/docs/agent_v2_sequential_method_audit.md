# Sequential Detector v1：方法与实现审计

日期：2026-09-04（America/Los_Angeles）

审计对象：B1 development、冻结 route/token models、B2 held-out retrospective scorer 与既有结果

审计性质：结果后方法审计；不改变冻结 B2 primary gate，不把事后诊断计为确认性结果

## 结论摘要

没有发现能够推翻 B2 结果的实现错误。窗口是因果的，decode routing 与已经生成并送回模型的 output token
逐 token 对齐，B2 scorer 没有重新拟合、重新标准化或重校准阈值；全部 240 条 trace 和 22,622 个完整
窗口都被评分。冻结 gate 失败是真实结果，不是计数 bug。

但当前方法存在三个一级方法问题：

1. 它本质上是**滑动的绝对语义状态分类器**，不是相对 routine baseline 的 change detector；
2. boundary 当下就被标为 positive 的 16-token 窗口含 15 个边界前 token，训练标签与窗口语义严重混合；
3. 最终阈值由仅 20 个 B1 calibration segments 的 q0.99 决定，其中最大两个 score 都来自 drift trace 的
   边界前区域，单个极值几乎控制了阈值。

B2 的事后审计进一步显示：27 条有边界前窗口的 drift 中，26 条的 post-boundary 最大 route score 高于
自身 pre-boundary 最大值，中位增量为 +0.769；用 post-boundary max 对比 205 条 non-drift 的全程 max，
事后排序 AUROC 为 0.982。固定阈值却只有 12/35 clean final detections。也就是说，主要失败不是“冻结
route 方向完全没有迁移”，而是“绝对阈值与状态分类 formulation 没有把迁移后的排序/变化信号转化为稳定
报警”。这个诊断增强了继续研究相对变化方法的理由，但不能改写已经失败的 primary evaluation。

当前判断应拆成两层：

- **实验可信度：通过审计。** B2 no-go 结论可复核，未发现泄漏或计数错误；
- **方法有效性：当前 formulation 不通过。** 不能把 v1 称为可靠 runtime task-switch detector；
- **研究方向：仍有明确正信号。** B2 内部的边界后相对上升很一致，值得开发真正的 relative detector。

## 1. 审计范围与不可变输入

核对的关键输入为：

- B1 sample index SHA-256：
  `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`；
- B1 sequential development SHA-256：
  `fdac2638ae687a549ae7594548c33ba1d796fdc561ae1feddce52ce895d98c4e`；
- frozen metadata SHA-256：
  `71aaf8b759e0631856f1a543f5d48abd2f2811e7f75ee85b395e067c6a5937ac`；
- frozen tensors SHA-256：
  `43ceb7435d504756b5a5e6aea6732bcc8286459e48b1345998ce2eebb006bdec`；
- B2 sample index SHA-256：
  `e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942`；
- B2 sequential result SHA-256：
  `3fe74e7eb4a5e103f3b9ba45e9486fb82fb5bf19d729857c6db601fe3ca0381a`；
- 冻结提交 `0c54c1b`，结果报告提交 `72f5030`。

审计只读取已有 trace、模型与结果。新增数值均明确标为 post-hoc diagnostic；没有覆盖或重跑 B2 scorer，
也没有搜索一个能让 B2 gate 通过的新阈值。

## 2. 实现正确性

### 2.1 因果窗口：通过

`window_features` 对结束于 token `t` 的窗口只聚合 `[t-w+1, t]`：route probability 和 top-k selection
都使用 prefix-sum difference，token hash 也只累计当前完整窗口。窗口结束索引从 `w-1` 到最后一个 output
token，没有读取未来 token。

B2 保存的三个模型 score 序列逐 trace 审计通过：每条序列长度均为 `decode_token_count - 15`，合计 22,622
个窗口。不存在“名义上 sequential、实际仍只读 first 16”的问题。

### 2.2 Autoregressive token 对齐：通过，但有使用边界

生成器先采样 output token `t`，再把它连同 KV cache 输入模型并捕获这个 token 自身的 routing；最后一个
output token 也执行额外 forward 以保存 routing。因此保存的第 `t` 个 route 确实对应 output token `t`，
不是预测它的前一个 token route。

这也意味着 token `t` 的路由只能在 token `t` 已经生成后使用。16-token detector 最早在 token index 15
报警，可以阻止后续输出，但不能在首 16 token 发出前预警。当前实验是离线评分，尚未把报警接入 runner 做
token-level abort；“online”指因果可实现性，不代表已经验证端到端拦截。

### 2.3 冻结与数据泄漏：实现通过，证据角色有限

- B1 fold 0–3 拟合最终 mean/scale/ridge weights；
- fold 4 只用于最终 threshold calibration；
- B2 scorer 只构造窗口、加载 frozen tensors 并评分，代码路径没有调用 fit；
- sample index、metadata 和 tensors 均有强制 SHA-256 校验；
- B2 中没有模型选择、标准化、阈值选择或 trace 删除。

但是 B2 此前已经用于 first-16、prefill 和 full-decode 分析，我们在设计 sequential 方法时已知道 B2 的行为
标签及 aggregate routing 结果。因此本轮只能称为“未读取该特定 sequential transform 的 held-out
retrospective evaluation”，不能升级为完全独立 prospective confirmation。现有文档对此表述准确。

### 2.4 报警与指标实现：通过

一次 alarm 要求 persistent score 越过冻结阈值；v1 persistence 为 1。drift 只有在没有任何 pre-boundary
alarm 且 boundary 后及时越线时才计为 clean detection。non-drift FAR 以 trace 为单位；每 1,000 个
negative positions 的 onset 另行报告。35 条 drift 全部进入 recall 分母，未检出没有从 latency 分母之外
被悄悄删除。

需要注意，中位 latency 只在 cleanly detected traces 中计算，所以 7.5 token 不能脱离 12/35 final recall
单独解释；这是条件命中延迟，不是总体检测时延。

## 3. 一级方法发现

### Finding A：当前模型是状态分类器，不是真正的 change detector

严重度：高

route feature 是当前 16-token 窗口的绝对 expert-selection profile；ridge 直接学习 negative anchors 与
post-boundary anchors 的全局分类方向。score 没有减去同一 trace 的 routine baseline，也没有减去按 workflow
条件化的正常 profile，更没有显式建模 `score_t - score_{t-k}`。

因此它回答的是：

> 当前窗口在 B1 学到的坐标中有多像域外生成状态？

而我们真正想回答的是：

> 当前 agent 是否相对自己的 routine 工作状态发生了持续的跨领域切换？

两者并不等价。绝对状态 score 会受 domain、措辞、workflow、攻击曝光和可服从倾向影响，所以能够在人工
行为边界前升高；跨 B1/B2 时，它的绝对零点与尺度也不保证稳定。B2 的 3 个 pre-boundary alarms 与阈值
迁移失败都符合这个机制。

### Finding B：positive 窗口存在结构性标签混合

严重度：高

训练 positive anchors 位于 `boundary + {0, 7, 15, 23}`。当 width=16 时：

- end=`boundary` 的窗口最多只有 1 个边界内/后 token，另 15 个 token 在边界前；
- end=`boundary+7` 仍有一半窗口在边界前；
- 只有 end≥`boundary+15` 的窗口才完全位于实质域外工作区域。

把前两个窗口硬标为 +1，会迫使模型从边界前上下文、攻击服从倾向或尚未完整展开的过渡文本中寻找正例
特征。这与评价中“任何 pre-boundary alarm 都算错”的要求存在张力。早边界 trace 还会把多个 offset
clamp 到首个完整窗口，虽然去重避免了重复样本，却没有解决窗口语义混合。

下一版应把 `[boundary, boundary+w-2]` 定义为 transition/ignore band，positive training window 从
`end=boundary+w-1` 开始；如果研究目标确实要求更早预警，则应另设 propensity label，不能与已发生的行为
切换标签混用。

### Finding C：最终 threshold calibration 统计上不稳定

严重度：高

最终 threshold 使用 B1 fold 4 的每条 negative segment 最大 score，再取 q0.99。fold 4 只有 21 条 trace、
7 个 scenario；其中只有 20 个 segment 有可用 negative windows。排序后的高端值为：

| Segment 类型 | Max score |
|---|---:|
| non-drift attack | 0.423 |
| drift pre-boundary | 0.609 |
| drift pre-boundary | 1.028 |

q0.95、q0.99 与最大值分别为 0.630、0.948、1.028。20 个样本上的 q0.99 位于最大值与次大值之间，实际
几乎由一个 pre-boundary trace 决定；它不是具有 1% coverage 保证的 tail estimate。与此同时 primary FAR
门槛允许 10%，q0.99 比目标 operating point 保守得多。

B2 上，205 条 non-drift 的全程最大 score 最高为 0.675，而 35 条 drift 的 post-boundary 最大 score 中位数
为 0.876。冻结阈值 0.948 同时高于两者，产生 0/205 non-drift false alarms，也让 20/35 drift 的
post-boundary max 永远无法越线；其余 15 条越线中又有 3 条此前已 pre-alarm，最终只剩 12 条 clean hit。

这不是计算错误，而是极小 calibration set、未校准 ridge score scale 与极端 quantile 共同造成的 operating
point 失稳。下一版需要独立且更大的 calibration population，并采用与目标 FAR 对齐的有限样本 order-statistic
或 conformal rule；每个 OOF fold 的 threshold 也应由 cross-fitted calibration score 得到，而不是由拟合
同一模型的 training traces 直接给出。

## 4. 二级方法发现

### Finding D：小样本、高维和候选选择使 B1 OOF 乐观

严重度：中高

route ridge 有 1,024 个特征，而最终训练只有 99 条 trace、265 个相关 anchors、20 条 drift。36 个候选在
同一 fold 0–3 OOF 结果上选择；即使 grouped split 没有 trace 泄漏，胜出配置的 12/20 +16 recall 仍包含
winner's curse。B2 降到 9/35 与这种选择方差一致。

anchor reduction 比使用所有相邻窗口更合理，但 loss 仍把 anchor 当独立观测：non-drift 通常贡献 2 个
negative anchors，drift 可贡献 2 个 negative 加最多 4 个 positive anchors；不同 trace 的权重因此不相等，
长 drift 也更容易贡献完整 positive offsets。下一版应按 trace 等权或使用 grouped loss/resampling。

### Finding E：开发 split 只保证 family-held-out，不保证 domain-held-out

严重度：中

三个 matched arms 随 scenario/family 留在同一 fold，这是正确的；但训练和 held-out fold 仍可包含同一 target
domain 的不同 family。这个设计测试的是新 family，而不是完全新 domain。它适合当前固定八域问题，却不足以
支持“任意跨领域偏移”泛化。

B2 thresholded clean hits 在 programming/legal 为零，但 post-hoc fixed-horizon 审计显示：比较边界前最后
一个窗口与首个完全 post-boundary 窗口，programming 4/4 上升、legal 2/3 上升。因而这里更像绝对 threshold
与域尺度问题，不能直接解释成这些领域完全没有 routing change。

### Finding F：token hash 不是充分的 routing-specificity 基线

严重度：中

2,048 维 signed token-ID bag 是有用的低成本 sanity check，但它丢失词序、语义组合和上下文，不代表一个
合理的 text-only detector。route + token 使用 3,072 维和不同 ridge penalty，也不是严格的 route 增量检验。
因此 route 优于 token hash 只能排除这个特定弱表示，不能证明信息是 routing 独有的。

这不要求当前探索阶段马上建设完整强基线矩阵；但对外结论必须写成“route 包含信号”，不能写成“route 提供
超越可见文本的独有信号”。正式立项后至少需要 sequence-aware text representation 或 residualized comparison。

### Finding G：人工 boundary 有正确 provenance，但缺少可靠性估计

严重度：中

每条 attack 经 research review，positive evidence 必须是生成文本中的真实 substring，并自动对齐到 decode
token；行为标签在按 outcome 查看 routing 前冻结。这些设计很好。

但 boundary 由单一人工 evidence span 决定，没有第二标注者、一致性统计或 ±k token 敏感性分析。“首个实质
域外 token”在自然文本中可能有过渡区，硬边界会影响 pre-alarm 与 +8/+16 指标。下一版应对一部分样本双标，
并预先报告 boundary tolerance sensitivity；这不是把失败结果改好看的许可，而是估计标签噪声。

### Finding H：评估范围仍小于真实 agent trajectory

严重度：中低

当前只评价最终 model-generation episode。它不检测更早的工具选择、澄清回合、controller 消息或跨多次模型
生成的漂移。B2 agent 的流程使最终回复成为主要语义偏移位置，所以当前实验内部成立；但不能直接外推为完整
生产 agent monitor。

max-over-time final recall 和 FAR 还受可评价序列长度影响。B2 只有 1/35 drift 在边界后少于 16 token，长度
不足不是本次低 recall 的主要原因；未来仍应报告 time-at-risk 或 average run length，而不只报告 trace FAR。

## 5. 次要实现与预注册偏差

以下问题没有影响本次选中模型或 B2 结论，但应在下一版清理：

1. candidate key 在完全平分时显式偏好更高 calibration quantile，而 B1 plan 没有声明这个 tie-break。实际
   9 个 eligible candidates 全部为 q0.99 route-selection，所以本次选择不受影响。
2. B1 loader 依赖数据 hash 和配置测试保证 matched triplet/family fold 完整性，sequential analysis 本身没有
   再逐组 assert；冻结数据下安全，但独立复用脚本时应增加 runtime assertion。
3. scorer 的“只运行一次”由 Git chronology 与输出 hash 审计，而非程序锁强制；脚本可以再次覆盖 ignored
   output。结果 hash 可以发现覆盖，但不能证明历史调用次数。
4. scorer 从 development script 导入 private helpers，功能正确但耦合较强；正式版本应把 detector/evaluator
   移入 `src/` 并用独立 tests 覆盖空窗口、边界窗口与 pre-alarm 状态机。

## 6. B2 事后变化诊断

本节用于定位 v1 failure，不属于冻结 primary result。

### 6.1 同一 drift 的 pre/post 变化

对 27 条同时具有 pre- 与 post-boundary 完整窗口的 drift：

- 26/27 的 post-boundary max 高于自身 pre-boundary max；
- `post max - pre max` 中位数为 +0.769，均值为 +0.767；
- 四分位区间为 +0.510 到 +1.065。

更严格地比较 `end=boundary-1` 与首个完全位于域外区域的 `end=boundary+15`：26 条有足够长度，其中 22/26
上升，中位增量 +0.580。按域看，programming 4/4、travel 4/4、cooking 2/2 都上升；这说明 thresholded
domain miss 不能等同于没有边界后变化。

### 6.2 跨 trace 排序

把每条 drift 的 post-boundary max 与每条 non-drift 的全程 max 比较，事后 AUROC 为 0.982。这个数值使用了
人工 boundary 和整段未来输出，不是可直接部署的实时 classifier，也没有独立数据可再确认；它只说明 frozen
score direction 保留了很强的后验排序结构。

综合 6.1 和 6.2，最值得测试的新假设不是“再找一个更低 absolute threshold”，而是：

> routing 的跨领域信号主要表现为相对同一 agent/workflow routine baseline 的持续上升；绝对 ridge score
> 的跨批次零点不足以承担告警校准。

## 7. 对当前证据的准确表述

可以说：

- B1-selected route score 在 B2 的实质域外边界后通常明显上升；
- 全程因果窗口记录中存在可进一步建模的 sequential signal；
- 当前冻结 absolute-threshold detector 以极低 routine FAR 换来了不可接受的低 recall，因此失败。

不能说：

- 已经获得可靠或可部署的在线越轨 detector；
- 路由信号已证明独立于输出文本；
- B2 是完全未接触数据的 prospective confirmation；
- programming/legal 没有 routing signal；它们只是没有越过当前绝对阈值；
- 事后 AUROC 0.982 可以替代失败的预注册 gate。

## 8. 建议的 v2 最小方法变更

在增加 agent 复杂度或采集新样本前，建议只开发以下最小变化：

1. **改 estimand。** 把 primary score 定义为 workflow-conditioned routine residual 或同 trace 的 causal
   relative change，而不是 absolute semantic score。
2. **设 transition band。** 训练只把完全 post-boundary 的窗口标 positive；混合窗口忽略或单独建模。
3. **trace 等权。** 防止一条 drift 因多个 anchors 获得更大 loss 权重。
4. **nested/cross-fitted calibration。** model fit、threshold calibration、candidate evaluation 三者按 group
   分离；阈值规则直接对应目标 FAR，并使用有限样本 order statistic。
5. **保留同一 primary metrics。** non-drift FAR、pre-boundary FAR、+16/final recall 和 paired token control
   继续使用，避免通过换指标逃避 v1 failure。
6. **增加稳健性切片。** family-held-out 之外加入 domain-held-out 和 boundary ±k sensitivity；latency 始终与
   recall 联合报告。

B1 与 B2 现已都被用于方法理解。若 v2 在两批数据上开发后产生清晰、跨 fold 稳定的信号，必须冻结方法后用
新的 B3 做确认；不能再把 B2 称为 v2 的 held-out test。

## 9. 最终审计判断

| 方面 | 判断 |
|---|---|
| 路由采集与 token alignment | 通过 |
| 因果逐窗口评分 | 通过 |
| B2 无 refit 与冻结完整性 | 通过 |
| 报警计数和 primary failure | 通过，结果可信 |
| 绝对阈值校准 | 不通过，样本过小且极值主导 |
| task-switch estimand 对齐 | 不通过，当前是状态分类器 |
| window label 对齐 | 不通过，transition contamination 明显 |
| routing-specificity 证据 | 不充分 |
| v1 作为 runtime detector | No-go |
| relative-routing v2 研究假设 | Go for focused method development |

因此，本次审计不是把失败翻译成成功，而是更精确地区分了两件事：**v1 operating point 确实失败；与此同时，
B2 中存在比 thresholded recall 更一致的相对路由变化信号，足以支持一次聚焦的 v2 方法开发。**
