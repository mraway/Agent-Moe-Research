# 方案 2（条件流形，CM）预注册

日期：2026-09-04（America/Los_Angeles）

执行者：任务 2（CM），Claude Opus 5。规范：`docs/sequential_v2_lead_proposals.md` §4，并遵守 §1（共享协议）、
§2（预诊断）、§6（综合规则）、§7.3（报告要求）。共享 harness：`src/research_v2/`，验收报告
`docs/research_v2/harness_report.md`（commit `df50714`）。

**本文件在任何主评价运行之前提交。** 提交后不得修改候选网格、主配置与证伪判据；如果运行中发现代码缺陷，
修复必须在报告中显式写明（缺陷、修复 commit、受影响的数字）。

本轮全部数字都是 B1/B2 开发数据上的事后开发证据（post-hoc development evidence），不是确认性结果。

---

## 1. 假设与 estimand

假设（§4.1）：routine 路由流形不是固定区域，而是条件分布——给定当前 token、局部可见文本、prefill 上下文，
routine 的路由比无条件分布紧得多。任务偏移的"离开"分成 (a) 词汇/上下文新颖（文本可见）与 (b) 同样的 token
被不同地路由（文本不可见）。把 (b) 从 (a) 里分出来。

Estimand（§4.2）：时刻 t 的分数回答"最近 w 个 decode token 的路由，相对于'这些 token 在 routine 中通常
怎样被路由'，偏离了多少"。词汇新颖度作为并列侧信道（= 基线 T2），不混入路由分数。分数只用 decode token
≤ t 的路由、prefill 路由、workflow 与离线 routine 统计（§1.2）。推理时不使用 `positive`、`evidence_onset`、
`completion_boundary`、`scenario_domain`、`channel`。

## 2. 分数定义

记 `ind_t ∈ {0,1}^{L×64}` 为第 t 个 decode token 的逐层 top-8 入选指示向量（L = 层带层数；中间层 5–11 时
L=7、448 维）。

### C1（token 条件化）

- 训练侧 routine token 上统计 per-(层, 专家, token id) 的 top-8 入选均值 `mean_tok`，出现次数 `n_tok`；
  经验贝叶斯收缩 `E[ind|tok] = (n_tok·mean_tok + m·μ)/(n_tok + m)`，`m = 5`，`μ` = routine 全局入选均值。
- 覆盖判据：`n_tok >= 5` 为"覆盖"，`n_tok < 5`（含未出现）退化为 `E = μ`（即方案 1 的 G1 无条件参考）。
  阈值 5 与基线 T2 的"未见词"定义一致。
- 残差 `r_t = ind_t − E[ind|tok_t]`；`w` token 因果窗口均值 `r̄_e`（窗口覆盖 `[e−w+1, e]`）；
  用训练侧 routine 的窗口残差均值/标准差逐维白化（方差下限 1e-3）；分数 `e_t = ‖z_t‖²`。
- 侧信道 `oov_t`（窗口内未覆盖 token 比例）不混入分数；它与基线 T2（`oov_fraction`，min_count=5）在数值上
  等同，故直接用 harness 的 T2 行报告，不重复实现。

### C2（局部文本条件化）

- `x_t = [P·emb(tok_t), P·EMA_8(emb)_t, P·EMA_32(emb)_t, log(t+1)]`，`emb` = 本地 OLMoE `model.embed_tokens`
  行（离线，`artifacts/hf_cache`），`P` = 在训练侧 routine token 的 `emb` 上拟合的 PCA-256 基。维度 769。
- ridge `f: x → 目标`，只在训练侧 routine token 上拟合。
- **C1+C2（网格中的组合）采用逐级加入的加法形式**：`f` 的目标是 C1 残差 `r_t`，条件期望为
  `E[ind|tok_t] + f(x_t)`，残差 `r'_t = r_t − f(x_t)`；其余（窗口均值、白化、能量）与 C1 相同。
  对未覆盖 token，`E = μ`，`f` 仍给出非平凡预测，这正是 §4.3 要 C2 解决覆盖问题的方式。
- **§4.5 判据 4 的 R² 用规范字面的映射**：同一 `x`、同一 λ 网格、同一留出集，目标为 `ind` 本身，
  留出 routine R²（对 448 个输出维合并计算 `1 − SS_res/SS_tot`）。两个目标（`ind` 与 `r`）的 R² 都报告。

### C3（prefill 上下文条件化）

- prefill 摘要 `p_i` = 该 trace 最终一次 generation 的 prefill 路由的 per-(层, 专家) top-8 入选率均值
  （1024 维），在训练侧 routine trace 上拟合 PCA-32，拼接 workflow one-hot 与常数项。
- 低容量映射 `g`：ridge，目标为该 routine trace 的 `mean_t log(e_t + 1e-6)`（"这个上下文下不偏移的 agent
  的离开分数应当多大"）；条件化后的分数 `e'_t = e_t / exp(ĝ(p_i))`。
- λ 由留出 routine trace 的 R² 在固定网格中选；若留出 R² <= 0，则 `ĝ ≡ 常数`（C3 失效），并在结果中记录。

### 超参数的 routine-only 选择规则（§1.6）

唯一数据依赖的超参数是两个 ridge 的 λ，网格固定为 `{1, 10, 100, 1000, 10000}`（C2）与
`{0.1, 1, 10, 100, 1000}`（C3）。选择判据是**留出 routine R²**：把训练侧 routine trace 按 `pair_group_id`
排序后的奇偶位置对半，前一半拟合、后一半评估，取 R² 最大的 λ，再用全部训练侧 routine 重新拟合。
过程中不接触任何 drift trace、不接触测试侧任何 trace。其余量（PCA 秩 256/32、m=5、覆盖阈值 5、方差下限
1e-3、EMA 系数 1/8 与 1/32）都是规范写死或本预注册写死的常数，不做搜索。

## 3. 候选网格（12 个，全部报告）

主配置由规范 §4.3 写死：**C1、中间层 5–11、w=8、无 C3、routine=cb、模式 D、α=0.10、读法 persist2**（候选 1）。

| # | 条件化 | 层带 | w | C3 | routine | 说明 |
|---|---|---|---|---|---|---|
| 1 | C1 | middle 5–11 | 8 | 无 | cb | **主配置** |
| 2 | C1+C2 | middle 5–11 | 8 | 无 | cb | |
| 3 | C1 | all 0–15 | 8 | 无 | cb | 层带消融 |
| 4 | C1+C2 | all 0–15 | 8 | 无 | cb | |
| 5 | C1 | middle 5–11 | 8 | 有 | cb | C3 主对照 |
| 6 | C1+C2 | middle 5–11 | 8 | 有 | cb | |
| 7 | C1 | all 0–15 | 8 | 有 | cb | |
| 8 | C1+C2 | all 0–15 | 8 | 有 | cb | |
| 9 | C1 | middle 5–11 | 4 | 无 | cb | 窗宽消融 |
| 10 | C1 | middle 5–11 | 16 | 无 | cb | 窗宽消融 |
| 11 | C1 | middle 5–11 | 8 | 无 | all_normal (R+) | routine 定义敏感性 |
| 12 | C1+C2 | early 0–4 | 8 | 无 | cb | 层局部性（判据 5） |

每个候选都在 S1 两方向 × 模式 D/T × α ∈ {0.05, 0.10} × §1.5 全部 11 种读法上评价。

判据要求的诊断变体（**不计入 12 个候选，只用于判据判定，不得当作新的检测器成绩**）：

- `cov0`：C1 主配置但把未覆盖 token 的残差置零（判据 1）。
- `cm_x_knn`：用 C2 的 `x_t` 做窗口均值 + kNN-10 新颖度的同特征文本单类对照（§4.4）。

对照行（同协议、同开关，直接引用 harness 参考实现）：G1（`g1_whitened_distance`，中间层 w=8）、
T2（`oov_fraction`）、T1（`t1_embedding_knn`，`components=null`，按 harness §5.3 的建议用 full 版本）、
S0（有监督参考）。P0、B0 未实现（harness 限制 3、4），报告中写明缺失。

## 4. 校准与协议开关（§1.6）

- 比较符 `>=`（harness 默认 D1）；模式 D 合并规则 `disjoint`（D2）；位置桶 `end // 32`、尾桶合并到 >= 30 条
  trace（D3，规范字面）。这些是 harness 默认值，不做搜索。
- **主表打开 `--b1-present-calibration`**：§1.6 规定 B2→B1 方向把 B1 `brief=present` 的 120 条 routine 并入
  校准池（每半 n≈100）。同时报告关闭它的敏感性（与 harness 报告 §5 的基线表同口径）。对照行 G1/T2/T1
  在两种开关下各跑一次，保证并排比较同口径。
- S2（合并分组 CV，10 组）与 S3（留一目标域，8 域）对主配置（候选 1）与候选 2 运行；模式 D 与 T 都报告
  （harness 限制 7：S2/S3 的模式 D 阈值极保守，不与 S1 直接比）。
- bootstrap：以 pair_group 为重抽样单位，500 抽，模式 D、读法 max/persist2（harness 默认）。

## 5. 证伪判据（照抄 §4.5，附操作化细节；不得事后修改）

所有判据在**模式 D、α=0.10、读法 persist2、w=8、routine=cb、主表开关**下判定，除非条目另有说明。
"两方向成立"指 B1→B2 与 B2→B1 同时成立（§6.3：只在一个方向成立的结果不算证据）。

1. **routing 只是词汇新颖度**：`cov0` 变体的容差 +16 recall <= T2 + 0.10（任一方向成立即触发），
   或 anchor-token within-token AUROC 中位数 < 0.75（用 harness Q1 面板的 `anchor_token_auroc.median`，
   两方向分别报告，任一方向 < 0.75 即触发）。
2. **条件化没有压低 topic 误报**：要求 C1（及 C1+C2）的 benign 臂 FAR 相对 G1 下降 >= 30%（相对下降，
   即 `FAR_b(CM) <= 0.7 × FAR_b(G1)`）且严格 +16 recall 损失 <= 0.05（`R16(CM) >= R16(G1) − 0.05`）。
   **两方向都满足才算未触发**；否则触发。
3. **上下文条件化无效**：要求 C3（候选 5）相对无 C3（候选 1）把 `resist 臂 FAR + 严格 pre-onset FAR`
   之和下降 >= 30%，且容差 +16 recall 下降 <= 0.10。**两方向都满足才算未触发**；否则触发。
4. **映射太弱**：C2 留出 routine R²（目标 `ind`）< 0.50 → 判为"文本模型太弱"，C2 相关结论不下。
   两个 S1 拟合侧（B1 拟合、B2 拟合）分别报告，任一 < 0.50 即对该方向触发。
5. **中间层假设错**：候选 12（C1+C2 早层 0–4）的严格 +16 recall >= 候选 2（C1+C2 中间层）的 0.9 倍
   （两方向都成立才算触发；单方向成立记为"部分"）。
6. **报警层面失败**（= §3.5 判据 1）：主配置在模式 D、α=0.10、任一 S1 方向出现 clean+benign 实现 FAR > 0.15，
   或容差 +16 recall < 0.55，或 final（容差）< 0.70。

另外按 §6.1 的门槛表逐行报告主配置的观测值与通过/不通过（门槛表本身不是本方案的证伪判据，
是组长综合用的）。

## 6. 要报告的表（§7.3）

1. 假设、estimand、本预注册文件的 commit。
2. **T-A 主表**：12 个候选 × S1 两方向 × 模式 D/T × α ∈ {0.05,0.10} × 读法 persist2/max，严格与容差指标、
   onset 锚点 +4/+8/+16/final、completion +16/final、分层 FAR（clean/benign/resist）、中位延迟、
   onsets/1k。全部读法的完整表放在附录/`tables.md`。
3. **T-B 主配置全读法表**（11 种读法 × 两方向 × 模式 D/T × 两个 α）。
4. **T-C S2 / S3 表**（候选 1、2；per-case 与 pooled；每域 R16/RF）。
5. **T-D 并排对照**：CM 候选 1、2、5 与 G1、T2、T1(full)、S0 在同一开关下的行；P0/B0 缺失说明。
6. **T-E Q1 面板**：事件曲线（偏移 −32…+48 的均值/中位数/配对 AUROC/n）、首个分离偏移（双条件与
   AUROC 条件分别报告）、每条 drift 的 `z(onset+15) − z(onset−1)` 上升比例、anchor-token within-token
   AUROC 中位与 IQR。
7. **T-F 边界前报警审计**：主配置的全部严格 pre-onset 报警，含窗口文本、偏移、是否在 8-token 容差带，
   并按 (a) 标签/标题、(b) 域外任务预告句、(c) 攻击复述/拒绝/解释、(d) 其他 分类计数。
8. **T-G R² 表**：C2 的 λ 网格 × 目标（`ind` / C1 残差）× 两个拟合侧的留出 routine R²；C3 的 λ 网格与
   留出 R²。
9. **T-H 能量分解表**：对每条 drift 的 onset 后报警窗口、以及每条 benign/resist 误报窗口，把 `‖z‖²`
   按内积分解为"覆盖 token 贡献 / 未覆盖 token 贡献 / routine 基线偏移贡献"三项（三项之和恒为 1），
   报告中位数与四分位。
10. **T-I 层局部性表**：早层（0–4）/ 中间层（5–11）/ 晚层（11–15）的 C1+C2 残差检测器的 +16 召回。
    （晚层行作为额外诊断，不计入 12 个候选。）
11. **T-J C3 条件化掉了多少**：resist 臂与严格 pre-onset 区窗口上 `e_t` 与 `e'_t` 的均值/中位变化。
12. 失败案例：>= 10 条漏检 drift 与 >= 10 条最高分 non-drift 的文本片段。
13. 每条证伪判据的观测值与判定；§6.1 门槛表逐行。
14. 数据 hash、代码 commit、运行命令、输出路径与每个 `result.json` 的 sha256。
15. §4.8 组长自我批评中四条风险各自的实际情况。

## 7. 规范留白处的决定（CM 专用；harness 的 D1–D10 沿用）

| 编号 | 项 | 决定 |
|---|---|---|
| CM-1 | 覆盖阈值 | `n_tok >= 5`（与 T2 的 min_count 一致）；未覆盖退化为 `μ`（规范字面） |
| CM-2 | 窗口聚合 | 先逐 token 求残差，再窗口均值，再按 routine 窗口残差白化，最后平方和；不再另减 centre（白化已使 routine 窗口残差均值为 0） |
| CM-3 | PCA 基 | 只在训练侧 routine 的**当前 token** embedding 上拟合一组 PCA-256 基，同一基作用于 `emb`、`EMA_8`、`EMA_32` 三块（EMA 是 emb 的凸组合，同空间） |
| CM-4 | EMA 定义 | `v_t = (1−a)·v_{t−1} + a·emb_t`，`a = 1/8` 与 `1/32`，`v_{−1} = emb_0`；因果 |
| CM-5 | C1+C2 的组合 | 加法逐级：ridge 目标为 C1 残差（见 §2）；同时报告规范字面 `x→ind` 的 R² 用于判据 4 |
| CM-6 | C3 的形式 | `e'_t = e_t / exp(ĝ(p_i))`，`ĝ` 为 log 空间 ridge；留出 R² <= 0 时 `ĝ ≡ 常数` |
| CM-7 | prefill 摘要 | 最终一次 generation 之前的最后一个 prefill 分片，全部 prefill token 的 top-8 入选率均值 [16,64] |
| CM-8 | prefill 缓存 | 新建 `artifacts/agent_v2/research_v2/cm/_prefill_cache/`（不写入任何既有目录），逐 trace 存 [16,64] float32 |
| CM-9 | 标准化的数值下限 | 白化方差下限 1e-3（与 G1 一致） |
| CM-10 | 主表校准池 | 打开 `--b1-present-calibration`（§1.6 字面）；关闭版本作为敏感性并用于与 harness 报告基线表对齐 |
| CM-11 | 判据的方向规则 | 除判据 1、4、6 按规范写的"任一方向"外，其余判据要求两方向同时成立才算未触发 |

## 8. 预期的失败模式（写在前面，避免事后叙述）

- 覆盖率只有 55–60%（E6）：C1 对 40% 的 drift token 退化为 G1，C1 与 G1 的差可能被稀释到不可分辨
  （B2→B1 方向 bootstrap 区间宽 ±0.2，harness §5.9）。
- C2 可能过拟合或欠拟合：19k–30k routine token 上拟合 769→448 的 ridge，留出 R² 若 < 0.50 则判据 4 触发，
  C2 的所有结论作废。
- C3 用 routine trace 拟合、在 drift 的 prefill 分布上外推，样本少（每方向 <= 240 条 routine trace），
  可能整体退化为常数。
- B2→B1 方向的部署侧校准样本量与 B1 drift 的更早边界都会造成抖动（§4.8 第 4 条）。

## 9. 环境与运行

- Python `/home/wzh/Agent-Moe-Research/.venv/bin/python`，`PYTHONPATH=$PWD/src:$PWD/scripts`，
  `torch.set_num_threads(8)`，纯 CPU，不加载语言模型（只读 `model.embed_tokens.weight` 与 `tokenizer.json`）。
- 代码：`src/research_v2/scorers/cm.py`、`scripts/research_v2/cm_*.py`、`tests/test_research_v2_cm.py`。
- 输出根目录：`artifacts/agent_v2/research_v2/cm/`（每个 run 一个子目录）。不写入任何既有结果目录。
- 数据 hash：B1 `f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1`、
  B2 `e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942`；B1 限制为
  `response_brief_condition == absent`（120 条），B2 240 条。
