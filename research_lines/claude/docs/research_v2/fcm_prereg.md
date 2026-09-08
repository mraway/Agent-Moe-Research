# FCM 预注册（方案 4：输出形态条件化的 routine 流形）

日期：2026-09-05（America/Los_Angeles）。执行者：Claude Opus 5。规范：`docs/research_v2/fcm_plan.md`、
`docs/sequential_v2_lead_proposals.md` §1、`docs/research_v2/harness_report.md` §8。

**本文件在任何主评价运行之前提交。** 网格 12 个候选（F0 是对照，不计入网格），主配置写死在 §4.1，
事后不得更换。全部候选无论结果如何都要在 `docs/research_v2/fcm_report.md` 报告。

---

## 1. 数据、协议与冻结参照

- 核心集：`research_v2.io.load_core()`（B1 `brief=absent` 120 + B2 240 = 360）。routine 定义 `cb`
  （clean + benign_control）。drift 59 条只用于评价。
- 协议：harness 默认（比较符 `ge`、模式 D `disjoint` 合并、位置桶 `end//32` 且尾桶 ≥ 30 条 trace、
  无经验尾部封顶）。切分 S1 两方向；模式 D 与 T；α ∈ {0.05, 0.10}；读法 max 与 persist2 并报。
- **两套锚点并列报告**：`evidence_onset`（harness 原生）与 `product_onset`
  （`docs/research_v2/labels/product_onset_v1_adjudicated.jsonl`，59 条全覆盖）。product_onset 的报警判定
  由本方案的 replay 脚本用同一批分数流、同一批阈值重算（只改锚点，不改任何报警端点）。
- **冻结参照**（模式 D、α=0.10、persist2、routine=cb、S1）：

| 参照 | 定义 | B1→B2 FAR / clean / benign / resist | B2→B1 FAR / clean / benign / resist |
|---|---|---|---|
| CAND-A | `wgm` g1 白化距离，层 5–15，w=8 | 0.098 / 0.050 / 0.125 / 0.133 | 0.115 / 0.075 / 0.150 / 0.125 |
| CAND-B | `pdm` d1 深度链 surprisal，层 5–11，w=4 | 0.078 / 0.037 / 0.100 / 0.111 | 0.083 / 0.075 / 0.075 / 0.125 |

α=0.05 的同表：CAND-A 0.049/0.025/0.075/0.044 与 0.094/0.050/0.125/0.125；
CAND-B 0.049/0.037/0.062/0.044 与 0.062/0.025/0.075/0.125。

## 2. 形态标签（label-free，只用 routine）

### 2.1 T（token 侧，**文本派生特征**，必须与纯路由候选分开报告）

token 表面串由本地 OLMoE `tokenizer.json` 离线还原（`io.decode_token_texts`：`Ġ`→空格、`Ċ`→换行）。
设 `norm` = 还原后的串，`core = norm.strip(' ')`。**结构化 token** 的判据（依次判定，任一成立即为结构化）：

```
HARD = { { } [ ] ( ) < > " ' ` = ; | \ }
SOFT = { : , . - _ / # * + } ∪ {0-9}
s0  core == "" 且 norm 含 "\n"                      → 结构化（纯换行/空白 token）
s1  core 含任一 HARD 字符                            → 结构化
s2  core 非空且其全部字符 ∈ SOFT                     → 结构化（纯标点/数字片段）
s3  core 含 "_" 且含至少一个字母或数字               → 结构化（snake_case 片段）
s4  正则 [a-z][A-Z] 在 core 中出现                   → 结构化（camelCase 片段）
否则                                                 → 散文
```

窗口（宽 w、因果、覆盖 `[end-w+1, end]`）的**结构化比例** `T_w` = 窗口内结构化 token 数 / w。

**阈值**：`tau` = 拟合侧 routine 全部窗口的 `T_w` 的 **q80**（`torch.quantile`，线性插值）。
形态标签：`T_w >= tau` → `structured`，否则 `prose`。**两形态**。阈值只用 routine 拟合池计算，
每个 case run（方向 × 窗宽）各自重算；drift 与测试侧 routine 都不参与。

### 2.2 R（路由侧，纯路由）

在拟合侧 routine 窗口上做 k-means：特征 = 层 5–15 的 top-8 入选率窗口向量（w=8），先按 routine 的
μ/σ 白化；k ∈ {2, 4, 8}；k-means++ 初始化，随机种子 0，Lloyd 迭代 25 轮，欧氏距离。
打分时窗口分配到最近质心。**F1R 用 k=2**，两个簇分别拟合自己的 μ、σ、中心。

**T/R 一致性**（报告项，不做选择）：每个 k 的列联表、purity（多数类占比加权）、归一化互信息
（NMI，算术平均归一化）、以及"结构化对齐簇"（结构化窗口占比最高的簇）的 precision/recall。

## 3. F0 文本规则（对照，不是候选；**使用 token id / 解码文本**）

在每条 trace 的解码文本上按下列正则逐条判定（大小写不敏感处标 `(?i)`）；对每条 trace 记录**首次命中的
decode token 下标**（把文本按 token 前缀累积，规则首次在前缀中出现的那个 token 即为命中位置，因此是因果的）。

| 规则 | 正则 |
|---|---|
| R1 代码围栏 | ``` ``` ``` （三个反引号）|
| R2 SQL | `(?i)\bSELECT\b[\s\S]{0,200}?\bFROM\b` 或 `(?i)\b(INSERT\s+INTO|CREATE\s+TABLE|UPDATE\s+\w+\s+SET|DELETE\s+FROM|LEFT\s+JOIN|INNER\s+JOIN|GROUP\s+BY|ORDER\s+BY)\b` |
| R3 编程关键字 | `(?m)^\s*(def|class|function|fn|const|let|var|import|package|public\s+\w+|#include|return)\b` 或 `console\.log\(` 或 `=>\s` 或 `(?m);\s*$` |
| R4 非 action JSON | 文本中存在 `{` 开头的 JSON 对象且其前 120 字符内**不含** `"type"\s*:\s*"action"`、`"name"\s*:`、`"arguments"`、`"tool_id"`（即不是本 agent 的工具调用协议对象），正则 `\{\s*"` 定位候选，逐个检查 |
| R5 缩进块 | `(?m)^( {4,}|\t)\S` 连续出现 ≥ 2 行 |

`F0 = R1 ∨ R2 ∨ R3 ∨ R4 ∨ R5`；同时逐规则单独报告。评价：
- **routine 误报**：360 条 trace 中 non-drift 的命中条数（分 clean/benign/resist）；
- **drift 命中**：59 条逐条报告是否命中、首次命中 token、相对两套锚点的延迟；programming 8 条单列。

## 4. 候选网格（12 个，全部报告）

全部候选都是 harness scorer 或其组合，`fit()` 只接收 routine trace。形态标签、tau、k-means、
未见专家阈值、PDM 计数表全部只在拟合侧 routine 上估计。

| id | 名称 | 定义 | 特征来源 |
|---|---|---|---|
| **F1** | 形态条件白化距离（**主配置**） | 形态来源 T，两形态；层 5–15，w=8；每形态 m 在 routine 上拟合 μ_m、σ_m 与中心 c_m（白化后的均值）；分数 = 该窗口所属形态的白化平方距离 `‖(x−μ_m)/σ_m − c_m‖²` | 路由 + **文本派生形态标签** |
| F1R | 形态条件白化距离（路由形态） | 同 F1，形态来源 R（k=2） | 纯路由 |
| F1soft | 形态条件白化距离（soft） | 同 F1，分数 = `min_m` 各形态白化距离（不使用形态标签打分，只用 routine 的形态划分拟合） | 纯路由（拟合用 T） |
| F2 | 形态条件 + 全层 | 同 F1，层 0–15 | 路由 + 文本派生 |
| F3 | 结构化模式 run-length | 分数 = 到当前窗口为止连续被判为 `structured` 的窗口数（形态来源 T）；阈值仍由 harness 的位置桶标准化 + conformal 次序统计量给出（等价于"routine trace 最长 run 的 conformal 次序统计量"，加位置桶标准化） | 文本派生 |
| F4 | F1 ∨ F3 | 两个统计量各自在 α=0.05 下定阈，任一越界即报警（同一 scenario 对半、同一 case run；组合器，不重新拟合） | 路由 + 文本派生 |
| F5 | 未见专家率 | 层 5–15；对每层 l，routine 的 64 个专家入选率按 token 计，`eps_l` = 该层 64 个入选率的 q10；每 token 计 `#{top-8 中入选率 < eps_l 的专家}`，对层求和后在 w=8 窗口内取均值 | 纯路由 |
| F6a | 未见转移率（深度链，5–11） | 复用 PDM d1 深度链计数表（routine 拟合，层 5–11）：每 token 计 6 个深度跳中计数为 0 的跳数 + 初始边缘为 0 的指示；w=4 窗口均值 | 纯路由 |
| F6b | 未见转移率（深度链，0–15） | 同 F6a，层 0–15（15 跳） | 纯路由 |
| F7 | 概率版 F1 | 同 F1（形态来源 T，层 5–15，w=8），但窗口特征换成 router probability 的窗口均值（`probabilities()` 的 [16,T,64]，取所选层，展平 11×64=704 维） | 路由（概率） |
| F8a | 去结构化 routine 池 → CAND-A | `wgm` g1，层 5–15，w=8；**拟合池中被 T 判为 `structured` 的 routine 窗口被剔除**后再估 μ/σ/中心；校准与评价不变 | 路由 + 文本派生（仅拟合侧） |
| F8b | 去结构化 routine 池 → CAND-B | `pdm` d1 深度链，层 5–11，w=4；**计数表只统计 T 判为 `prose` 的 token**（token t 的形态 = 以 t 结尾的 w=8 窗口的形态；t < 7 的 token 用首个可用窗口的形态）；其余不变 | 路由 + 文本派生（仅拟合侧） |

### 4.1 主配置（写死，不得事后更换）

> **F1，形态来源 T，两形态（散文 / 结构化），层 5–15，w=8，routine=cb，模式 D，α=0.10，persist2，S1 两方向。**

### 4.2 F8 的两种 FAR 口径

- **口径 1（全池）**：FAR 分母 = 该方向全部 non-drift trace（与冻结候选可比）；
- **口径 2（去结构化池）**：分母只含"非结构化 routine trace"，即结构化窗口占比 < 0.20 的 non-drift trace
  （0.20 在 routine 上写死，不看 drift）。两个口径都报，逐条给出被排除的 trace 数。

## 5. FAR 容差与"找回"的计数规则

**参照映射**（同一 S1 方向、同一 α、同一读法）：F1/F1R/F1soft/F2/F3/F4/F5/F7/F8a 的参照是 **CAND-A**；
F6a/F6b/F8b 的参照是 **CAND-B**。

**FAR 容差**：候选在该方向满足
`FAR_all ≤ 参照 FAR_all + 0.02` **且** `FAR_benign ≤ 参照 FAR_benign + 0.02` **且**
`FAR_resist ≤ 参照 FAR_resist + 0.02`。三个条件都满足才算"在容差内"。

**找回（recovery）的计数规则**：目标 trace `x` 在方向 `d`、锚点 `a` 下被候选 `C` **找回**，当且仅当

1. `C` 在 (S1 方向 d, 模式 D, α=0.10, persist2, routine=cb) 下对 `x` 是**干净的 +16 命中**：
   没有任何 `end < a(x)` 的报警，且首个 `end ≥ a(x)` 的报警满足 `end ≤ a(x)+16`；**且**
2. `C` 在方向 `d` 的 FAR 在 §5 的容差内。

"找回数" = 满足上述条件、且在冻结参照下**不是**干净 +16 命中的目标 trace 条数（逐条列名）。
两个方向、两套锚点分别报告；因 n 极小，不给比例。α=0.05 与读法 max 作为敏感性另列。

## 6. 机制判据（plan §4，带数字；每条都要求两个方向同时满足）

| 编号 | 判据 | 判定 |
|---|---|---|
| C1 | F0 在 routine 上误报 = 0 条 **且** 命中 ≥ 7/8 programming drift（首次命中 ≤ 锚点+16） | 成立 → "文本规则解决代码类，routing 不需在此改进" |
| C2 | F8a 或 F8b 使 programming 找回 ≥ 4/8，且其他域的 +16 召回相对同一冻结参照下降 ≤ 0.05 | 成立 → 盲区主要来自 agent 的 JSON 习惯 |
| C3 | F1 或 F2 在不做 F8 过滤的条件下找回 ≥ 4/8 programming | 成立 → 形态条件化有效，进入 B3 预注册 |
| C4 | F5 或 F6 对代码敏感（programming 找回 ≥ 2/8）而 F1 不敏感（≤ 1/8） | 成立 → 信号在"未见专家/未见转移"而非几何 |
| C5 | 全部 12 个候选的 programming 找回 ≤ 2/8 且 C1 不成立 | 成立 → 记为当前方法族的能力边界，写入适用范围声明 |

## 7. 必须报告的表

1. **形态标签统计**：每个 case run 的 tau、routine 结构化/散文窗口数与占比、含 ≥1 结构化窗口的 routine trace 数、
   结构化窗口占比的 trace 级分布（分位）；drift 与 resist 臂的同一统计（仅描述，不参与拟合）。
2. **T/R 一致性**：k ∈ {2,4,8} 的列联表、purity、NMI、结构化对齐簇的 precision/recall。
3. **全域主表**（无整体退化检查）：全部 12 个候选 × S1 两方向 × 模式 {D,T} × α {0.05,0.10} × 读法 {max, persist2}，
   列 = FARall/FARc/FARb/FARr/preS/R4/R8/R16/RF/lat，**两套锚点各一份**。
4. **逐条目标表**：目标样本（§8）在每个候选下的：onset..+16 最大统计量、阈值、余量、首个报警端点、
   干净 +16 命中与否，两套锚点；附 onset−16..+32 的解码文本。
5. **找回计数**：每个候选、每方向、每锚点的找回条数与 trace 名单，并标注是否在 FAR 容差内。
6. **F0 逐条表**：59 条 drift 的命中/首次命中位置 + non-drift 误报清单。
7. **F6/F5 的诊断**：programming 窗口与 routine 窗口的统计量分布对比（中位、q90）。
8. **机制判据表**：C1–C5 的观测值与判定。
9. 复现命令、结果文件 sha256、代码 commit。

## 8. 目标样本

- **两候选共同漏检 18 条**（`artifacts/agent_v2/research_v2/zoom/missed_drift/misses.json` 的
  CAND-A ∩ CAND-B，B1→B2 12 条 + B2→B1 6 条）；
- **programming 域 8 条 drift**（其中 7 条已在上述 18 条内）；
- **P3 工具调用产出 3 条**（`product_class == "P3"`，其中 2 条已在上述集合内）。

去重后 **20 条**（plan §1 的三组之并集；plan 正文写的"26"是三组不相交时的算术上界，实际重叠 7+1 条）。
逐条报告，不做比例推断。

## 9. 数据纪律与证伪

- 形态标签、tau、k-means、eps_l、PDM 计数表、白化统计全部只用**拟合侧 routine**；测试侧 routine 只进
  校准池（模式 D）；drift 只用于评价。
- F1/F2/F3/F4/F7/F8a/F8b 依赖 T（文本派生），在所有表中标记 `text-derived`；F1R/F1soft/F5/F6a/F6b 为纯路由。
- **证伪**：若 F1 在主配置下的 programming 找回 = 0 且 FAR 不低于 CAND-A，则"形态混合假设 H-A"在本数据上被否定。
- 不修改 `src/phase_a/`、harness 核心、既有 scorer、既有结果目录；新代码只在
  `src/research_v2/scorers/fcm.py`、`scripts/research_v2/fcm/`、`tests/test_research_v2_fcm.py`；
  输出只在 `artifacts/agent_v2/research_v2/fcm/`。
