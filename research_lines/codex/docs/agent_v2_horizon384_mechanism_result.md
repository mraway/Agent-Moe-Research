# Agent v2.5 384-token engagement / recovery机制结果

日期：2026-09-05（America/Los_Angeles）

状态：预注册主分析完成；development replay；H1未通过；H2未通过样本量门；B3未使用

结果文件SHA-256：`243dd83b7916768fbc4b01d686ccc5ba34798579b009373c443e9038e91a10b4`

## 结论先行

本轮没有确认“抵御攻击时会出现一个可由routing峰值直接识别的异常状态”。H1的效应弱且matched-control覆盖不足。
但是数据出现了一个值得继续验证的、更具体的机制候选：**抵御与执行的区别可能不在engagement起点有多高，而在随后
routing轨迹是否回落。**

在具有完整64-token后续窗口的样本中，5/5条bounded-engagement resisted轨迹都从early window向late window下降；
35条cross-domain execution总体近似持平。两类delta的排序AUROC为0.909，均值差为0.351，bootstrap 95% CI为
[0.171, 0.516]。除样本量以外，H2预注册的其余四项条件全部满足。

但bounded组只有5条，而预注册最低要求是12条，所以H2正式结果仍为**未通过 / 当前instrument下不充分**。该方向
只能作为高优先级机制候选，不能作为确认性结论。

## 1. 输入与表示审计

- replay trace：240/240通过exact-prefix、routing alignment与工具流完整性门。
- 行为分类：40 execution、5 bounded-resisted、35 silent-ignore；routing解盲前已独立提交冻结。
- 主表示：`token_endpoint_z`。
- 正常fit集：既有26条canonical clean traces、208个anchors。
- robust fit center：0.5993；scale：0.1462。
- 本分析不把192-token C1阈值外推为384-token FAR保证。

## 2. H1：抵御过程是否出现engagement excursion

预注册比较为：bounded-resisted在engagement onset后的16-token maximum，对同scenario clean与benign在完全相同
绝对位置的较大maximum。

| 指标 | 结果 | 预注册门槛 | 是否满足 |
|---|---:|---:|---|
| bounded样本总数 | 5 | ≥12 | 否 |
| matched-control可比较数 | 3/5（60%） | ≥70% | 否 |
| attack vs control AUROC | 0.667 | ≥0.75 | 否 |
| paired wins | 2/3（66.7%） | ≥70% | 否 |

3个可比较pair的attack-control maximum delta均值仅0.020，中位数0.013，范围[-0.060, 0.108]。因此即使忽略
样本量，当前数据也不支持“显式处理攻击必然产生更高局部routing峰值”。H1未通过不是单纯的power问题。

## 3. H2：抵御恢复与持续执行是否分叉

每条engaged attack计算：

`continuation_delta = mean([onset+32, onset+63]) - mean([onset, onset+15])`

负值表示late window相对engagement初期回落；正值表示保持或继续上升。

| 指标 | bounded-resisted | execution |
|---|---:|---:|
| 完整窗口样本 | 5 | 35 |
| delta均值 | -0.314 | 0.037 |
| delta中位数 | -0.353 | 0.060 |
| delta最小值 | -0.554 | -0.342 |
| delta最大值 | -0.016 | 0.406 |

进一步结果：

- execution减bounded的均值差：0.351；
- 5,000次scenario-level bootstrap 95% CI：[0.171, 0.516]；
- 用delta排序execution与bounded：AUROC 0.909；
- bounded 5/5 delta均小于0；
- execution样本量、正contrast且CI高于0、bounded median低于0、AUROC门均满足；
- 唯一未满足的H2组件是bounded eligible数量5 < 12。

这与“有限处理后回到授权任务”和“持续采纳攻击任务”两种轨迹相符，但5条bounded样本不足以排除个例、标签边界、
output age或任务类型构成造成的偶然结构。

## 4. H3：bounded engagement与silent ignore

H3为预注册的描述性比较，不设Go门。

| 指标 | bounded（n=5） | silent ignore（n=35） | AUROC |
|---|---:|---:|---:|
| full-path maximum均值 | 1.499 | 1.319 | 0.714 |
| first-64 maximum均值 | 1.195 | 1.221 | 0.349 |
| full-path mean均值 | 0.322 | 0.367 | 0.474 |

只有full-path maximum表现出弱到中等的正排序，早期maximum和全程mean没有同方向分离。这进一步说明：若该机制
真实存在，它更像一个与语义事件对齐的时间过程，而不是整条回复都处于更高异常水平。

## 5. Horizon审计

- 父B2的40条attack length stops全部获得延长后缀；27条在384前EOS，13条再次触及length上限。
- 4条原resist在token 192之后首次engage并首次execute。
- 后缀分类：26 continued execution、4 bounded engagement、8 support-only、2 execution后回到support。
- 长horizon的最终行为结果为40 drift / 40 resist；其中1条变化来自旧人工漏标，不归因于horizon。

因此，192 token上限会低估延迟执行，同时384 token仍没有为全部样本提供自然EOS。未来确认批次若研究最终任务采纳，
应继续使用至少384 token，并把stop reason作为删失信息报告。

## 6. 当前机制判断

现有证据支持把原用户观点细化为两层：

1. `attack engagement`是行为上可区分的中间状态，但不一定对应一个更高的routing峰；
2. 更有希望的内部信号是engagement后的动态——快速回落更像抵御，保持或上升更像执行。

这是机制候选，而非已确认机制。下一步应做冻结后的稳健性审计，优先检查：(a) 对5条bounded样本逐一leave-one-out；
(b) output onset位置与自然decode-age趋势；(c) 对最边界的`[POLICY]`重定向样本做标签敏感性；(d) 在全新、专门提高
bounded-engagement发生率且不读取B3的确认数据上复验。任何敏感性结果必须标为post-hoc，不回写本页主结果。
