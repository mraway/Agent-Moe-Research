# 机制 M12：局部 token 历史控制与匹配支持损失

2026-09-08。**G-dev development mechanism evidence；不做检测器、调阈值或确认性检验。**
先提交本方案，再提交通过合成测试的实现及独立审计，之后才读取本轮匹配结果/路由。
M7–M11、算法线与 Claude 工作区保持不变；G-conf 继续封存。

## 1. 问题与范围

M11在当前token相同的E三元组上，U/W/P的全向量距离均超出正常—正常背景。
本轮主问题改为：**E处差异在更长局部生成历史相同时是否仍可观察，及这种比较覆盖谁？**
E是看过M11后的明确开发目标，不是事前独立发现。X_pre/X_at全部保留为范围对照，
不将三个阶段不同样本的横截面点估计当成同一episode的时间轨迹。

只读M7–M11冻结产物、M7记录SHA的M3私有G-dev top-k/logit缓存。
不读原始trace、tokenizer、模型、G-conf内容/标签/封存清单；不生成、不前向、不拟合。
输出`artifacts/agent_v2/codex_g/mechanism_m12_history_control_v1/`。
使用M8全部5562个冻结query：E_at1453、X_pre1967、X_at2142，来自最终有X的126条episode。
沿用完整episode端点，不恢复旧H=352截断；匹配失败是**缺可比对照**，不是算法漏检或观察不到。

## 2. 分数盲的匹配规则

L ∈ {1,2,4,8}是**含当前token**的后缀长度：L8控制`t-7,…,t`，不是8个历史token再加当前。
M8窗口均在同一步/通道内；L1必须逐项重现M10 `filtered_distinct` 主格。
只用293条过滤通过正常episode作银行，三者同M8结构六元组
（fold、harmony通道、episode index、生成step、通道端点序号//32、生成端点//64）。
所有三条边的8-token TU距离≤0.25；正常episode不同、正常scenario互异，均排除query自身scenario。
生成端点不是含输入prompt的attention绝对位置；TU不是完整条件语言模型概率。

**主分析：每个L重新匹配**。对每个query：结构/对query的TU过滤后，增加L-token后缀逐ID完全相同，
**在每episode保留最佳look之前**过滤历史。其余完全沿用M10：按
`(|TU_d-TU_q|, |end_d-end_q|, end_d, episode_key)`排序，每正常episode取第一名，
按排序索引(i,j)词典序选第一对符合scenario及相互TU约束的donor。
API只接受结构、tokens、TU、scenario和银行成员；不接受路由、分数、未来长度/行为。
L1沿用M10失败码；增加`history_suffix_mismatch`区分当前token相同但更长后缀不符。
记录后缀合格look/episode/scenario候选数；原始后缀候选嵌套，
但**每episode最佳look可能更换，因此最终可行pair集合不保证嵌套**；落盘相邻L的新增/退出query，不能强制删去例外。

**固定旧图敏感性**：不重选M10主格的任何donor，只筛选三者L-token后缀相同的三元组。
此支持集必须随L嵌套；同时报告保留与退出部分，避免把选择性退出误称为历史“解释掉”的效应。
不放宽TU、结构、scenario、正常过滤或改用标签择优补对照。

## 3. 测量与可比汇总

当前token每层U（实际4专家等权）、W（实际入选logits的selected-softmax）、P（全32专家softmax）
和TV/归一化JS、rare/common可加分解全部沿用M11。实际IDs不重新top-k；W来自bf16缓存重建，
P不是模型实际专家组合权重，U的JS与TV恒等。主读数为全部24层平均TV的
`A=(d(q,a)+d(q,b))/2`、`N=d(a,b)`、`D=A-N`。
保存预定三层带及24层profile、TV rare/common和JS，不搜索最强层/距离。

每阶段/每L报告：

1. 重匹配全部支持的A/N/D、失败码、query分布、donor复用/有效权重和三边平衡；
   三条生成端点差均≤16子集为固定位置敏感性。
2. **L1与当前L共同query**上同时重算两种匹配的A/N/D；逐query计算
   `Delta = D_L-D_L1`，再episode平权及家族区间。并落盘A/N变化，避免只看D。
   四种L全部共同query上也并列四列；若为空，明确不可估。
3. 固定M10图的历史保留/退出两部分；不能把两者或不同L全支持均值相减称为控制效果。
4. 每L中三者实际选择集合相同的层，复用M11同mask的W/P A/N/D及层/episode/look分母；
   这是路由条件化描述，不是选择之外的因果权重效应。正常pair同集合层的N同时保存。

先look内平均层，再episode内平均，最后episode平权；不按look数或可匹配数加权episode。
主区间家族bootstrap2000次seed802608，family×tier稳健列；均值/中位数/正差比例全报。
数值零容差1e-12，不是检测阈值。区间条件于固定正常银行/匹配/条件mask，
不含donor复用、匹配选择、反复开发与多重比较的不确定性；少家族必须明确。

## 4. 审计与推论边界

合成测试覆盖L1重放、后缀包含当前、过滤先于episode去重、分数盲/确定性、三边TU、
scenario互异、原始候选嵌套但贪心pair可非嵌套、固定图嵌套、空支持、共同query配对与episode平权。
独立实现逐query穷举银行候选以重放全部匹配及失败/候选计数；路由从同SHA缓存用独立
softmax/距离公式重建，核对M11重叠向量、所有边、三元组、汇总/区间/支持mask。
新图先落盘取SHA，再测量路由；运行前后验证所有引用旧产物/源文件/缓存SHA。

残差仍在只排除“当前token及给定长度局部后缀完全解释差异”这一窄解释，
不排除更长历史、输入注入文本、语义/任务状态、位置或其它上下文。
残差消失也不证明完整上下文外没有信号；更严格匹配可能只剩模板化少数样本。
尤其L8全同必使TU相同（同fold/通道），但**不使Transformer的完整隐藏状态相同**。
缺支持时报告不可识别，不以扩银行、补数据或开启G-conf代替。
