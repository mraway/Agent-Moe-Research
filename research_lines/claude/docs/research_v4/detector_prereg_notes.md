# 检测器 v3.1 预注册的组长决定笔记（滚动更新；冻结前汇总进预注册）

## 2026-09-07（G-bridge 正常臂冒烟，`gbridge_harness_smoke.md`）
1. **层带**：M（白化距离）与 B（深度链 surprisal）在 24 层模型上冻结为 `all24`；明确禁止按层号移植 OLMoE 的 5–15
   （held-out 误报 0.225 / 0.175，名义 0.099）。按深度比例的 `mid24`（8–23）作为敏感性列。
2. **通道标签口径**：`tag_scope = message`（含通道头 token）为主；`body` 口径作为敏感性列（旧回退实现下 `body` 是错的，已修）。
3. **通道桶的稀疏回退**：拟合池中某通道窗口数不足（G-bridge 的 commentary 只有 3 个 episode）时，标准化必须回退到全通道合并统计量并记录，
   不能抛错；冻结前实现并测试。
4. **H 规则的门**：设计 §15.1 的"H ≥ 128"以 P0 存活率 54.5% 为据；G-bridge（v2.5 controller，单步）在 k=128 只有 34%（V1）/ 24%（V3）存活。
   G-cal（Agent v3，含长任务 R4）的存活曲线出来后再定：若过滤后 240 条达不到 ≥90 存活 @128，则 H 取规则值（≥90 存活的最大 k），
   门降为"H ≥ 96 且 E_view+16 的可达率 ≥ 90%"，并在数据卡说明。不追加正常样本凑门。
5. **误报口径**：目标池 = 校准池时的合计误报是恒等式（⌊(n+1)α⌋−1 条），报告只用 held-out 列；G 上用 G-fit 拟合、G-cal 校准、G-dev 评价。
6. **B 的保守性**：深度链在所有层带下误报 0.0125–0.05（远低于 0.099），比较时按实测误报匹配（已在 harness 中实现）。
7. **bf16 并列**：逐层 top-1 在约 0.3% 位置是精确并列，B 依赖 top-1；数据卡固定 torch / kernel 版本。
8. **V1 ≡ V2 在无 commentary 的数据上**：V2 的意义只在 Agent v3 数据（有工具调用 JSON）上成立。
9. 主格候选：S 对 M 对边际 surprisal，主锚点 E_view，+16，按实测误报匹配，攻击家族聚类 bootstrap；单一主格，其余 Holm。
10. **稀疏通道回退已实现**（`trm3_g.py`：拟合池中某通道 <30 窗口或 <10 episode、或目标出现拟合池没有的通道时，用全通道合并桶；
    provenance 写入 result.json）。冻结时把底层默认改为回退开启。阈值 30/10 需在真实 G-cal 支持上重新定并写入数据卡。
11. **G-bridge 的 held-out 误报对 seed 极敏感**：M 在两个新 seed 下 0/80，原因是几条重尾正常 episode（校准半份 z 的 sd 8.5–23，第 8 大路径最大值 54 对 5.6，一条 848）
    落在哪一半决定阈值。n=80 的 G-bridge 不能用来给统计量族排序；G 上必须报告参照最大值的分布（尾部）并对重尾正常样本做逐条文本审计
    （它们可能正是"正常输入下的真实偏离"）。

## 2026-09-08（G-fit / G-cal 标注完成，`h_freeze_note.md`）
12. **H 的正式值（look 轴，过滤后 G-cal n=279，≥90 存活）**：主格 V1 / `tag_scope=message` / w=8 → **H = 352 looks**（第 352 个端点落在全局 token 379–393，中位 379；
    删失 89/279 = 31.9% 路径、11.54% 端点；G-fit 同 H 下 35.1% / 12.28%）。其余 11 格一并冻结：V1 w4 373；V2 314/328；V3 284/288；
    `body` 口径 V1 314/332、V2 301/312、V3 278/282。门 `H ≥ 128` 在所有格通过（最差 278，k=128 处存活 241/279）。
    token 计数代理（384 / 93 条）与 look 轴的差别是窗口步长：harness 用 stride-1 因果窗口，look ≈ 0.90 × token，且 look 口径更严；
    标注报告里"余量只有 3"是代理的假象，look 口径下门的余量是 151 条路径。
13. **`tag_scope` 裁定**：维持第 2 条，`message` 为主、`body` 为敏感性列；预注册必须写明主格并列出全部 12 个 H 值，
    第一次检测器运行须断言 `result.json` 的 `calibration.horizon.H == 352`。
14. **过滤后 G-cal 的臂长不对称**：benign_control 中位 346 token / 304.5 looks，clean 328 / 286；H 处存活 49 benign / 41 clean。不影响 H（混合池），
    但做实测误报匹配前要按臂核对。
15. **解盲文件**：`annotations/{g_fit,g_cal}/final_unblinded.jsonl` 由 `packets_validate.py --mapping` 生成，标签值与盲态文件逐位相同；
    若重建标注包，须对新包 hash 重新生成。

## 2026-09-07（G-dev 盲态包缺陷裁定与重建，`g_dev_run_log.md` §13）
16. **工具结果里的 harness 扰动旗标已从标注包脱敏**：`experiment_perturbation` 与其同形别名
    `external_note_provenance`（`applied: true`）此前只出现在被注入的臂——G-fit / G-cal 各 150/300 与
    benign_control **逐 case 重合**、G-dev 116/784。包构建器新增默认开启的
    `--redact-tool-result-keys`（`--redaction-style remove`），G-dev 已用同一哈希洗牌重建
    （packet sha256 `9430613b…` → `148874bc…`，784 case 与 `packet_order` 逐条不变，
    `channel_text` / token 数组逐字节不变，重建后包内两键各出现 0 次，盲态复查 PASS）；
    G-fit / G-cal **不重建**（据第 15 条，重建会使 `final_unblinded.jsonl` 需重生成），
    改为出具逐臂标签平衡证据：`filter_pass` 144/150 对 144/150（G-fit）、139/150 对 140/150（G-cal），
    material_errors / coverage / citation 四轴 Fisher 双侧 p = 1.000 / 1.000 / 1.000 / 0.718（G-fit）与
    1.000 / 1.000 / 1.000 / 0.201（G-cal），600 行全部 `silent`、E/C/X 全 null。
    **数据卡必须写明：G-fit / G-cal 的"盲态"对 case 身份成立、对臂划分不成立。**
17. **脱敏方式裁定（2026-09-08）**：标注包对工具返回值中的 `experiment_perturbation` 与 `external_note_provenance` 两个键采用**整体移除**（不是占位符替换，占位符会原样保留臂信号），新构建默认开启；模型输出文本与 token 数组逐字节不动；私有映射记录每处脱敏。G-session / G-medium / G-conf 沿用打包时脱敏，不改模型输入。G-fit / G-cal 数据卡写明"盲态对 case 身份成立、对臂别不成立"，并附逐臂标签平衡表（Fisher p 均 ≥ 0.2）。
