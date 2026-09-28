# 机制M9：固定匹配图上的当前token、窗口历史与层分解

2026-09-08。**G-dev development mechanism evidence；不是算法实验、因果干预或确认性检验。**
方案提交、实现测试冻结后运行；不变更M7阈值/M8匹配，不根据结果重选窗口或层。

## 1. 问题与源码时序

M8在当前token相同的45个X窗口上仍有路由残差，但原统计量平均了8个token。
首先排除测量粒度混淆：剩余差异是否只是前7个未匹配token贡献的？若当前token也有差异，
是哪些层贡献的，是否仅来自极少数匹配token？这仍不能回答“独立于完整上下文的授权/意图”。

源码链`agent_v3/episode.py:_generate_step → phase_a/generation.py:generate_routed_turn → routing/capture.py:record_step`
表明：先采样并记入y_t，再以y_t为input执行forward并记录路由，用forward的logits采样y_(t+1)。
因此R_t是**处理已采样y_t时**的路由；不是预测y_t之前的状态。loader核验缓存token IDs与生成序列一致。
本轮通过合成调用时序测试及缓存重放核验这一口径，不据源码顺序推断对用户实际发送时刻。

## 2. 输入、作用域与固定分组

只读M7冻结manifest/流、M8冻结inventory/match_graph/metadata/residuals/audit，
以及M7已记录SHA的M3 G-dev私有top-k和logit缓存。只为解码说明读取固定模型revision的本地tokenizer.json，
不加载模型、不下载、不读取原始trace/路由分片或G-conf任何内容。输入前后核对SHA。
产物：`artifacts/agent_v2/codex_g/mechanism_m9_window_decomposition_v1/`。

复用M8全部3阶段×6donor池×4匹配档及其最多3个donor的**原样匹配图**，不重新匹配。
主描述列仍为X / filtered正常 / L1（28个episode、45个look）；不能只保留正残差或高分窗口。
L1_tight、normal_all、拒绝/未执行池和E/X前列原样作范围/敏感性，零支持保留不可估计。
至少1与至少3 donor分列，候选仍为全部126条X正例；不产生新recall或FAR。

## 3. 精确加性分解

用M7逐评价fold冻结的q和rare mask重建每token、每层的贡献。
S贡献为rare-selected的−log q之和；CW贡献为S的各专家项乘以`1+logit_e−min(actual_selected_logits)`。
直接使用实际top-k，不重新排序，不重拟合；权重余量只作既有CW的测量重放，不代表全概率信息。

对S/CW分别令u_(t,l)为该层该token贡献，D为原M8的query减平权donor：

`D(window) = D(sum_l u_(t,l))/8 + D(sum_(j=1..7,l) u_(t-j,l))/8`。

报告当前token**未除8**的差、其对原窗口的贡献（除8）、历史7-token总贡献及窗口总差；
先逐look精确验证，再episode内平权、episode间平权。分量相加必须重放M8原始S/CW残差。
不把“除8后只占小部分”误读成当前token没有信号，也不把相加分量称作因果解释比例。
有符号均值比`mean(current contribution)/mean(window)`只作描述；分母近零记不可解释，
允许负值或大于1（抵消），不截到[0,1]、不称解释方差，不为该比值造显著性检验。

层分析固定为零基0–7、8–15、16–23三带的**求和贡献**，不搜索最强层；
报告当前token与完整窗两组，并保存全部24层profile。主L1的filtered/all正常三阶段提供逐层区间。
每个向量维度均输出episode均值/中位数/正差比例、家族bootstrap2000及family×tier稳健列；
区间条件于固定匹配图/银行，未计donor不确定性、重复开发或多重比较，仅描述性。

## 4. 词汇说明与禁止推断

X / filtered正常 / L1的45个query全部按episode key、端点排序保存说明卡，不按效应挑例。
卡片包括当前token解码、原8-token文本、截至当前的最多32-token同step生成前缀、全部donor对应内容及分解。
文本只说明剩余上下文差异，不修改E/X标注，不用人工内容分类反向筛选分数。
当前token的自动类别固定为：special、纯空白、含字母、含数字、纯标点/符号、other，按此优先顺序；
仅做支持度与贡献来源说明，不成为检测规则。保存token ID及special定义以便重放。

若当前差接近零而历史贡献大，只能说M8窗口关联主要在未匹配位置；不能否定整个路由机制。
若当前差为正，则静态“token身份唯一决定路由”不能解释这些观测，但更长上下文/位置仍可能解释。
若差异集中后层，只称层定位，不称因果中介、任务意图专家或跨任务通用模块。
更广的输出内容控制、正常-正常变异基线及授权反事实仍另需设计；本轮不补数据或启动干预。

## 5. 验证与交付

合成测试覆盖同token不同上下文的分解、只有历史有差异的反例、因果前缀、所有层/权重守恒、
独立逐专家枚举、step边界、episode平权、空支持、词汇分类和生成/捕获调用顺序。
独立审计不调用生产分解函数：从相同缓存逐专家重建全部所用窗口，重放全部匹配残差与分量恒等式，
检查分母、层求和、主列及逐层bootstrap；再次核对M7/M8清单与实际输入SHA。
代码/报告仅写机制侧命名空间；不给副对话算法文件或Claude工作区打补丁。
