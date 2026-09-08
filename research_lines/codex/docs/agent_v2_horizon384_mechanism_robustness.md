# Agent v2.5 384-token机制稳健性审计

日期：2026-09-05（America/Los_Angeles）

状态：post-hoc robustness；不改变预注册H1/H2结论；B3未使用

源主结果SHA-256：`243dd83b7916768fbc4b01d686ccc5ba34798579b009373c443e9038e91a10b4`

稳健性结果SHA-256：`dbe95f7b8cbb73be823cdbfa865cb7bf5a350f98e5ea192eb2bc27387356247e`

## 结论

H2所见的“bounded-resisted回落、execution保持”方向经多项事后审计后仍然存在，没有被单一个例、最有争议的行为
标签、攻击channel、target domain、workflow family、decode age或单token噪声解释掉。尤其是，4条在文本上明确回到
客服任务的bounded轨迹，在恢复后的32 token中全部低于engagement early-window均值。

但这些审计不能把正式H2从失败改成通过。bounded-resisted仍只有5条，低于预注册的12条最低样本量；bootstrap在
极小组上只反映当前经验分布，不能替代新的独立样本。正确结论是：**机制方向比初始主分析看起来更可信，值得立即做
定向确认；现有批次仍不构成确认。**

## 1. Leave-one-out稳定性

逐一删除5条bounded样本中的任意一条，并保持35条eligible execution不变：

- 5次execution-minus-bounded均值差均为正；
- 5次bootstrap 95% CI下界范围为0.117至0.287，全部高于0；
- AUROC范围为0.886至0.979。

因此主方向不是由某一条极端negative delta独自造成。

## 2. 最严格标签敏感性

两条标签边界最大的样本被同时排除：一条只是完整复述untrusted note，另一条采用`[POLICY]`格式但把内容重定向回
保修政策。仅保留3条明确识别/拒绝攻击后恢复客服内容的bounded样本：

- bounded delta均值：-0.400；
- execution-minus-bounded均值差：0.437；
- bootstrap 95% CI：[0.263, 0.598]；
- AUROC：0.971。

严格化标签没有削弱方向，反而增强；但n=3意味着它仍只是敏感性描述。

## 3. 攻击channel与任务构成

5条bounded样本全部来自tool-output injection，这是一个真实的分布特征：Agent更可能显式识别来自工具结果的
untrusted addendum，而对direct/multi-turn user攻击更常直接忽略或执行。

只把它们与同为tool-output channel且窗口完整的5条execution比较：

- execution delta均值0.039，bounded均值-0.314；
- 均值差0.352，bootstrap 95% CI [0.116, 0.560]；
- AUROC 0.920。

另外逐条以相同target domain的execution中位数比较，5/5 bounded更低；以相同workflow family比较，同样为5/5。
因此观察结果不只是“tool-output对比user-channel”，也不明显由领域或workflow构成驱动。

## 4. Decode-age审计

对每个bounded onset，收集所有能够完整覆盖相同绝对early/late位置的clean与benign-control轨迹，形成独立的自然
decode-age参考。5条中有4条比相同位置control delta中位数更负；bounded减control中位数的均值为-0.291，中位数
-0.311。唯一例外是“完整复述untrusted note但未执行”的边界样本，其差为+0.051。

这说明大部分回落不是长回复在晚期自然降分的简单产物。该参考跨scenario汇集且非预注册matched estimator，因此
只用于排除明显age趋势，不作确认推断。

## 5. 与实际客服恢复点对齐

5条bounded中有4条在盲态行为裁决中标注了明确的`support_resume_output_token`。将冻结的engagement early均值与
恢复后首32 token均值比较：

- 4/4恢复后均下降；
- post-resume减early的均值为-0.348；
- 中位数为-0.320；范围[-0.571, -0.180]。

未标注恢复点的第5条正是只复述外部note、之后没有客服continuation的样本；它在主H2中也几乎不变（delta
-0.016）。这种行为边界与routing动态的对应是当前最直接的机制证据。

## 6. 独立表示敏感性

### 6.1 固定、非重叠8-token均值

用episode起点固定的non-overlap 8-token block流重复early-vs-late比较：

- bounded均值-0.218，execution均值0.139；
- 均值差0.357，bootstrap 95% CI [0.128, 0.578]；
- AUROC 0.840。

方向保持，说明主结果不是单token endpoint噪声或窗口内极值造成。

### 6.2 Router probability JSD

使用同一26条canonical clean fit traces的2,032个decode tokens构造逐层平均router-probability profile；每个新token
计算其对该profile的layer-mean JSD，再沿用相同early/late窗口：

- bounded JSD delta均值-0.00108；execution均值0.00736；
- execution-minus-bounded均值差0.00844，bootstrap 95% CI [0.00307, 0.01455]；
- AUROC 0.714。

概率表示给出同方向但明显更弱的排序，符合“selection-space token novelty是更敏感主仪器、概率JSD仅作方向检查”
的定位。

## 7. 长horizon新增执行的动态

4条攻击直到token 192以后才开始execution，其中3条有完整H2后续窗口；3/3 delta为正，均值0.184，范围
[0.134, 0.284]。也就是说，这些延迟采纳并非只短暂写出一个标题后立即回落，而是在可观察窗口里继续进入或保持
跨域任务状态。

## 8. 下一步判定

当前最值得确认的命题不再是“H1：非常规思考一定形成更高瞬时峰值”，而是：

> 在Agent已经显式处理攻击的条件下，回到授权任务会伴随routing novelty回落；继续执行攻击任务则保持或上升。

下一批应独立于本批输出，预先提高tool-output场景占比以获得至少12条bounded-resisted，但必须一次性收集固定样本，
不得按结果补seed。分析应继续冻结行为先于routing，并把attack channel、target domain、workflow family和onset age作为
设计层平衡或预声明敏感性，而不是看到结果后调整。
