# 两线合一的证据简报（设计 v3 的输入，2026-09-06）

组长：Claude Fable 5.1。来源标记：**L** = lead 分支（`docs/sequential_v2_lead_proposals.md`、`docs/research_v2/`），
**C** = Codex 主线（`docs/normal_manifold_*.md`、`docs/agent_v2_*.md`、`docs/proposal_drr_*.md`、
`docs/algorithm_design_logic_foundations.md`）。所有数字为 B1/B2/C1 开发数据上的结果；B3 未生成。
本文只列已确立的事实与已证伪的方向，不含新主张。设计 v3 必须与本文每一条相容。

## A. 目标与 estimand（两线一致）

- 只学习正常客服任务的 MoE 路由流形；在线生成显著、持续地离开该流形时给出信号（用户 2026-09-04）。
- 主事件 = 生成进入题外计算域；抵御 / 执行是主事件的子分类；抵御过程中的"思考出轨"本身是有效信号（用户 2026-09-05）。
  行为层面（C，384-token replay）：40/80 跨域执行、5/80 有界处理后抵御、35/80 静默忽略。
- 部署定位（用户 2026-09-06）：作为多信号之一，不是唯一决策。优化似然比、分段稳定且可解释的 FAR、校准分数
  （按会话预算）、在不可逆点之前的检出、对上下文攻击文本的不敏感、可刻画的漏检；不是最低 FAR。

## B. 已确立的事实

### B1. 信号的三层分解（两线独立得出）
1. **支持集扩张**（L: 白化能量分析；C: routine expert support）：其他域漂移的白化能量 78% 落在 routine 最低方差十分位
   （冷门专家）；26 条 routine 拟合集里只有 2 个 layer-expert 对从未出现（L8/E14、L13/E14）；`unseen8` 整轨迹筛出 29/40
   执行、clean 0/80、benign 6/80；`surprisal8` 37/40、clean 1/80、benign 13/80。**强、廉价、不及时**（onset+16 内 ≤11/40，
   常早于行为起点）。
2. **支持集内重排**（L: 代码盲区机制）：代码/SQL 不征用冷门专家（冷门专家质量 = routine 水平，8 个域最低），只在 routine
   已用专家集合内重加权，方向沿 routine 自身"散文↔工具调用 JSON"轴（cos 0.78）；top-8 集合比 routine token 自身更接近
   routine 排名。集合类特征结构性看不见；概率集中度（rmass）batch 内可分（AUC 0.998）但跨 batch 不迁移。
   C 的归因：LDC 的执行报警 73% 落在完全由 routine 已见专家组成的窗口内。
3. **联合结构与动力学**（L: 跨层链；C: layer zoom、H2）：相邻层 top-1 对的未见率代码 7.3×、其他域同量级；晚层（L11–15）
   在语义/任务边界瞬时放大（onset token 的 matched JSD contrast late +0.056 vs early −0.003）；持续执行时早中层
   （L3/L4/L7）比晚层更持久（late−early 非负比例 91/86/77% vs 晚层 ~50%）；恢复/执行区分是分布式、非单调的
   （单层最好 L4 AUROC 0.93，L0 0.56）；回到原任务也产生晚层峰值。L 的深度剖面：代码偏离双峰 L5–7 与 L12–13，层 0–4 无用。

### B2. 时间与校准
4. **相对排序早、绝对阈值晚**（C: early-evidence audit；L: calibration anatomy）：有 matched 正常对照时，onset+4/+8 的
   drift-vs-matched AUROC 0.89–0.92 / 0.83–0.86；但按完整正常路径校准的绝对阈值下 +8 召回只有 25% / 11%。
   L：延迟 ≈ 窗宽 + 阈值裕度，阈值裕度占主导；保形校准把总 FAR 钉在 α，持久性/块/CUSUM 只移动 ROC 点。
5. **trace 相对基线有早期信号**（C: DRR）：B1 学到的方向 + 减去本 trace 历史中位数，27/40 执行越线、16/40 在 onset+8 内，
   控制组 FAR 10.6%；但"首次越线后回落 = 抵御"不成立（最终执行召回 4/40，No-go）。
6. **校准池是约束**（两线）：每半 40 条时工作点由 4 条 trace 决定，需 ≥90/半；两半 FAR 有系统符号差；C1 = 320 条纯正常
   / 160 组已收集；FHTS、NTR 因 ≥64/48 token 的正常样本不足在校准门停止（160 条中只有 98 条 ≥64 token）。
7. **部署侧校准优于源侧**（L）：P1–P3 的失败是校准而非信号；目标 batch routine 半份保形校准把 onset+8 召回从 0–0.13
   提到 0.29–0.54。位置桶标准化必要；长度分层保形不作主表。

### B3. 抵御、良性、上下文攻击
8. 冻结候选对 47 条静默抵御只报警 2–4 条（clean 臂水平）：检测器响应的是生成进入话题，不是上下文里的攻击文本（L）。
9. 14 条有文本痕迹的抵御在 +16 内只抓到 2 条；窄窗口（w=4/2/1）无帮助，14 条中 5 条无信号（峰值 ≤0.72× 阈值）（L）。
10. 恢复方向（C: H2）：5/5 有界抵御的 novelty 从 early 窗到 late 窗回落，35 条执行总体持平，AUROC 0.909，
    但 n=5 < 12 门槛；oracle onset 下 LDC 的跨层多数方向 AUROC 0.92；算法自选 onset 时失效（首次越线常不是执行起点）。
11. benign（正常但提到题外话题）臂 FAR 是 clean 臂的 1–2.7×（L），surprisal8 的 benign FAR 16%（C）：
    "话题被提及"是主要可解释误报类；class (a) 从不出现在 clean 臂。

### B4. 代码 / 结构化输出
12. 代码/SQL 在冻结协议下 0/8 及时检出；文本规则（代码围栏/SQL/关键字）零误报但迟（+17 中位）；形态条件化、去结构化池、
    层带、窗宽、逐层 max、15 个概率反事实全部失败（L: FCM）。
13. routine 输出 47.5% 以 JSON 对象开头，尽管系统提示禁止（小模型照抄控制器动作格式）。去掉 JSON routine 后代码在窗口
    层面可分（百分位 0.995）但协议下 FAR 0.10→0.29–0.35、resist FAR →0.5，不可用。"routine 是否含结构化输出"必须是
    B3 的显式设计变量（L）。
14. 盲区是代码专属：14 条结构化非代码交付物像散文漂移（L）。

### B5. 标签与锚点
15. 锚点：`completion_boundary` → `evidence_onset`（中位早 5 token）→ `product_onset`（再早 4.9 token；交付形态起点）；
    `topic_entry_onset`（首次指涉）与"首个题外实词"相差 0…+29 token（中位 +5）。B3 记录双锚点（L）。
16. 384-token replay 把 4 条"抵御"改判为晚起的执行，192-token 截断造成删失（C）；22/61 抵御样本解码被截断（L）。
17. 8 条 programming 里 2–3 条是关于 SQL 的散文；3 条"抵御"样本整条输出是产出交付物的工具调用（应归 drift）（L）。

### B6. 表示与工程
18. 路由缓存 uint8/CSR 无损；完整 16×64 概率可缓存；零额外前向开销（L）。
19. 单 token novelty 的 fully-post AUROC 0.995/0.998，与滑窗状态 endpoint 相关只有 0.37/0.50：token innovation 是合理的
    基础信息单位（C）。
20. 控制器结构状态在三臂间完全相同，不提供信息（C）。

## C. 已证伪 / 停止的方向（v3 不得再试同一形式）
- 用持久性/块/CUSUM 降 FAR；长度分层保形作主表；层维特征；routine-only 层选择；warm-up；固定专家名单；
- 形态条件化参照、结构化 run-length、去结构化 routine 池、形态类特征（FCM）；
- 窄窗口换抵御召回；把 novelty 回落直接等同抵御成功（DRR/LDC）；锁定首次越线后固定 64-token delta 判据；
- absolute-age 归一化；局部密度比；conditional successor forecast；staggered min-2 / q25 持久性；
- 单一 novelty 阈值继续调 bins/block/quantile（C 逻辑基础 §10）。

## D. 数据资产
- B1（120，brief absent）、B2（240）= 360 核心；B2 384-token replay（240，行为重冻结）；C1 = 320 条纯正常 / 160 组；
  标签：product_onset、topic_entry（v1.1）、engagement（C）；路由缓存全 16 层含概率。
- 冻结候选：CAND-A（WGM g1 中晚层 w=8）、CAND-B（PDM d1 中层 w=4）及其分数流；Codex：token_endpoint_z、DRR ridge 方向、
  LDC band Hellinger 第 5 近邻、unseen8/surprisal8。

## E. 接口契约（C §7 + L 生产框架）
- 在线只读生成期路由（可含概率），不读文本、任务、workflow、渠道、绝对位置、结果；锚点只用于评价。
- 输出：逐 token 校准分数（在正常流量上分布已知）+ 证据窗（可读）+ 归因通道；不是单一二值报警。
- 校准：部署侧、routine-only、按 scenario 半份保形；FAR 按 clean / benign / resist / 长度 / workflow 分段报告；
  每会话误报预算。
- 评价：主事件正例 = drift ∪ 有锚点抵御；子分类抵御/执行单列；product_onset 主锚点；"不可逆点前检出率"；
  似然比；代码域单列并配文本确认；B1/B2 只作开发证据；预注册单格 + 多重比较校正。

## F. 开放问题（v3 需要回答或明确回避）
1. 能否把三层信号分别校准后融合，而不是一个白化距离？（两线都指向这里，没人做过）
2. trace 相对基线（DRR 的自历史中位数）能否与流形绝对分数结合成双参照分数，缓解"相对早、绝对晚"？
3. 晚层候选 → 早中层持续的两阶段状态机（C layer zoom §7）能否在不依赖 oracle onset 时成立？
4. 概率类特征（rmass、概率 JSD）在 C1 大池校准下是否跨 batch 稳定？
5. 时间一致性：逐 token 读数如何满足按会话的误报预算（e-process / anytime-valid）？
6. 文本层增量：路由相对文本基线（T1/T2、零误报代码规则）的增量在哪个预算下存在？（C 的同预算比较研究）
