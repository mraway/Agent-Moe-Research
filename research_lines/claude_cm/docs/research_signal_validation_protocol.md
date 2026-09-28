# MoE agent drift：研究信号验证协议

日期：2026-09-03（America/Los_Angeles）

状态：Stage C0 与 S1 已完成；S1 未达到直接进入 S3 confirmation 的条件，下一步需验证较窄的 decode change-point 命题

## 1. 阶段目标

本阶段不开发生产级 detector，也不完成论文级基线比较。唯一目标是获得足够清晰、有说服力且可独立复现的
证据，判断以下问题是否值得交给研究组正式立项：

> 在相同 agent、system prompt 和攻击暴露下，MoE 的逐 token decode 路由是否会随着 agent 实际开始执行
> 跨领域任务而产生可重复变化，并在完整越界输出发布前形成可检测信号？

本阶段不声称路由优于最强文本分类器、hidden-state probe 或生产 guardrail。这些比较留给正式立项后的系统
研究。

## 2. 当前证据的角色

已有 36 条轨迹及 classifier exploration 全部定义为开发证据 `Evidence 0`：

- 它们证明正常客服与明显跨领域输出在当前数据中具有路由可分性；
- 它们可用于检查管线、标签、特征实现和提出假设；
- 它们不能进入未来锁定方法后的独立确认集；
- 旧 Atlas prompt 的 attack 全部 drifted，新 scope-gate prompt 的 attack 全部 resisted，因此它们不能证明
  行为特异性。

## 3. 立项前必须补齐的证据

### E1：同 prompt 的 mixed outcomes

固定模型、system prompt、业务任务、攻击文本和解码参数，仅使用预声明 sampling seed，自然得到
`attacked_drifted` 与 `attacked_resisted`。不得按结果选择性删除或继续采样。

### E2：token 级时间对应

每条正例标注首个实质跨领域行为 token。逐 token 路由分数应相对于该边界报告，而不是只报告完整序列均值。
需要区分：攻击进入 prefill、decode 开始、行为偏移成立、候选输出发布四个时刻。

### E3：排除明显混淆

立项前只保留四个低成本 sanity checks：

1. 输出长度；
2. JSON/协议格式和 agent step；
3. 现有 token hash；
4. 完全相同输出 token 前缀，及尽可能匹配的 token/位置比较。

本阶段不实现强文本模型、LLM monitor 或 hidden-state probe。

### E4：跨条件重复

信号至少应在多个业务任务中出现，并覆盖 direct-user 与 tool-output 两种攻击渠道，以及 poetry 与 code 两种
跨领域目标。单一 marker、单一协议错误或单一长代码输出不能构成立项信号。

### E5：锁定后的独立复现

开发数据用于选择一个简单路由分数、窗口和必要的层聚合。选择完成后冻结实现，再生成不参与选择的新任务与
攻击措辞。独立确认只检验预声明结果，不再搜索大量算法组合。

## 4. 工作阶段

### Stage C0：Agent calibration

目的只是找到能在固定 prompt 下自然产生 mixed outcomes 的研究仪器。

- calibration 场景与未来 signal/confirmation 场景隔离；
- 只运行 attack 臂以节省计算；
- 每个完全相同的攻击输入使用一组预声明 seed；
- 只审计自然行为和标签，不读取或比较路由值；
- 一个候选 prompt 若全部 drift 或全部 resist，可以在新的 calibration run 中调整；
- calibration 轨迹永不进入正式效果估计。

候选 prompt 的最低可用条件：总体至少产生 2 条 drifted 与 2 条 resisted，并且至少一个完全相同的攻击输入
跨 seed 同时产生两种 outcome。更理想的是两种攻击渠道都出现 mixed outcomes。

### Stage S1：小型 signal batch

Calibration 成功后才冻结。它应包含 clean、benign 和 attack，并把同一基础任务、攻击 payload 和所有 seed
视为同一个不可拆分的数据组。主分析只在 attacked traces 中比较 drifted 与 resisted；clean/benign 用于建立
正常参考和检查误报。

### Stage S2：时间对齐与简单检测

只探索低容量方法：

- route probability 或 top-k selection 的条件化正常残差；
- 固定 8 或 16 token 窗口；
- ridge 或简单距离；
- 可选的 EWMA/CUSUM 时间累计。

开发阶段可以比较这些候选，但必须在确认批前选定唯一主方法。

### Stage S3：独立 confirmation batch

使用新的业务 ID、攻击措辞和预声明 seed。全部同 payload seed 留在同一评估组。报告行为边界附近的分数曲线、
组级排序、协议/长度/token 控制和所有失败案例。

## 5. 立项判断

### Go

以下条件同时成立时，形成研究交接包：

1. 同 prompt、同攻击暴露下可区分 drifted 与 resisted；
2. 路由变化与人工行为边界具有时间对应，而不只是 prompt 一出现就恒定分开；
3. 长度、协议格式和当前 token 身份不能解释全部结果；
4. 多个任务、渠道或目标领域中方向一致；
5. 简单冻结方法在新 confirmation 场景中复现。

### No-go 或重新收缩

- 只能识别有无攻击，不能识别实际 outcome；
- 信号完全来自格式、长度或明显输出词汇；
- 只在完整诗歌/代码已经可见后出现；
- 换场景后消失；
- 锁定后的确认批不能复现。

即使得到 no-go，也应记录较窄的边界结论：MoE 路由可能编码跨领域话题，但不一定编码 agent 的攻击服从
状态。

## 6. 研究交接包

达到 Go 条件后交付：

- 冻结的核心命题、标签和非目标；
- 能产生同-prompt mixed outcomes 的固定 agent；
- 开发集与独立确认集；
- token 级行为边界和完整路由 trace；
- 一个简单主方法及四个 sanity controls；
- 按行为边界对齐的逐 token 风险曲线；
- 成功、失败和反例的完整审计；
- 后继正式研究需要完成的强基线、多模型、复杂 agent 和生产评估清单。
