# M15：逐token路由几何与协议感知的行为返回

2026-09-09。GPT-OSS-20B / Agent v3 / OPEN G-dev **development mechanism evidence**。
依据M14与组长“继续”，先冻结方案、实现/测试/审计，再算新路由结果。
不设计检测器、不调报警阈值、不碰算法副线/Claude/G-conf，不生成数据或加载模型。

## 1. 问题与两个互不替代的队列

H15a：M14的稀有读出回落，是否也表现为完整路由向正常背景靠近？
H15b：M14因8-token窗口缺前块的恢复，扣除协议头部并对齐正常通道转场后，是否也有这种变化？
如果稀有S/CW回落但整体几何不靠近，接受“读出方向特定”的解释；
若所谓前状态只是header/分隔符，不将其与正文的差异叫任务恢复。

**A：固定复查。** 原样复用M14第一恢复段位于X后的10条query及四后块共同可用的22个正常donor episode。
不重选donor、不混进另一条episode的第二恢复段；原M14每query有1–3个donor。
每个query与其正常pseudo-R取相对位置−8..31；保留M14的同query同donor比较，先复算S/CW原分数。
只有1个donor者可估计query–normal距离A，不能凭空估计normal–normal背景N/D；至少2个donor才做背景校正。

**B：边界队列。** M14第一恢复段中`pre=-1`的144条载注入episode，固定全纳入覆盖账本。
正常池filtered293；不要求整条输出/整段等长，不按未来回落或未来可观察长度挑对照。
这是新控制设计，不能把A/B效果差说成同一批样本的逐步控制效应。

## 2. token轴与协议审计

只读M7/M8/M14冻结产物、M7 SHA记录的私有G-dev top-k/logits缓存，以及M9记录SHA的固定tokenizer.json。
分词器仅用于既有token的解码和Harmony协议分段，不加载权重、不做模型前向。
用既有Harmony解析器与每step长度重建header/body/terminator；逐look复核M8 message通道和token8对齐。
检查全部166条第一恢复段：R与R−1的角色、R前同正文token数、最近前一正文的通道及step。
路由处理的是已采样当前token，不是采样前意图。

B的前状态定义：

- R在正文内部：取同正文最后`min(8,R-body_start)`个token作为前块；长度必须>=1，不穿插header。
- R恰为正文起点：取**紧邻的前一条非空正文消息**最后8个token；不足8或不存在则缺失，不向更早消息回退。
- R不在解析出的正文：标不支持，不能改R或把特殊token当正文。

这并非全部使用相同的“前8 token”；正文内短前块、前一消息8-token前块分层报告。
跨step可能包含工具返回/新上下文，必须与正常同转场对照，仍不具因果解释。

## 3. B的正常转场匹配（路由盲选）

正常候选在每条正文中取与query相同的`R-body_start`位置。
要求同fold、episode序号、当前step和通道、前状态模式/前块长度；
前一消息模式还要求前通道和前后step差相同。
当前R的**token ID相同**，R绝对位置差<=64，query与donor scenario不同。
按位置差、前块起点位置差、episode key、消息编号排序，最多3个不同episode且不同scenario。
正常不存在“恢复”标签；只是具有相同协议/正文位置的pseudo-R，不凭空赋予行为恢复事件。
不使用路由、S/CW/TU大小、未来token/长度/标签或未来可观察性选donor。先写死token匹配图，再读logits算几何。
不使用正常为空的`family`字段做匹配；query家族仅供事后分层/聚类，不回填旧元数据。

每个后块为R..R+7、R+8..R+15、R+16..R+23、R+24..R+31。
query须落在该标注恢复段及当前正文内；donor须落在同一条对应正文内。
选定后逐块记录缺失，不能以未出现后块填零或替换donor。
主曲线使用四块完整的同query/同donor集合；另保留逐块可用集合，不能拼接不同分母平均曲线。
未观察到的未来、无前正文和无控制分别计数，不叫算法漏检。

## 4. 逐token表示与背景校正

U为实际top4等权分布，W为实际入选logits的softmax，P为全32专家softmax。
使用缓存实际IDs，不以bf16 logit重新argtopk。float64重建，W不声称与存储bf16权重逐位一致。
主距离TV；同时保留TV_rare/TV_common和归一化JS，rare为该折M7正常拟合`q<.02`的固定坐标。
TV总量=rare+common；不重归一化稀有子集，不挑专家/层/表示。

每个相对token位置：A=所有query–donor距离的均值；
N=所有不同donor两两距离的均值；D=A−N。只有>=2个donor才有N/D。
前块先逐token算距离再平均；后块同理，**不是先平均概率向量再算距离**。
B短前块对照长度完全一致；每个比较的前后使用同一批donor。
主量为`post_D - pre_D`（负值支持向正常背景靠近）；同时报A/N/D的前值、后值、变化，
避免把normal–normal背景变宽误写成query确实靠近。
全部24层均值为主；前/中/后8层描述性列保存，不选“最佳层”。

S/CW逐token值仅做M14重放和并列方向检查；它们不替代完整几何，也不重新标准化/校准。
A按M14原分数重放；B不拟合新的rarity，只用相应M7正常状态。
同token只约束B的R当前token，不约束前块或全部后续文本；余下内容/上下文混杂必须报告。

## 5. 汇总与完整性

query一个episode一个第一恢复段；donor等权，episode等权。family及family×tier bootstrap各2000次，seed802608。
区间为开发性、条件于参照/匹配，不含共享donor与多轮探索不确定性；不做McNemar/算法召回主张。
A/B分开；B按X前/无X、前状态模式、当前通道、注入渠道、fold/tier/domain分层，记录家族数及donor复用。
主曲线至少2个donor，单donor只报A；无同类支持仍报告覆盖。没有“只有效果好才继续”的结果后停止规则。
所有新代码、合成测试、独立算术审计先提交冻结；真实运行必须exact clean tracked HEAD，输出拒绝覆盖。
产物仅`artifacts/agent_v2/codex_g/mechanism_m15_token_return_v1/`，旧缓存和产物只读，源码不改共享harness。
本轮不能验证长会话/跨模型泛化、内部自我纠正、持久安全或任务授权性的因果机制。
