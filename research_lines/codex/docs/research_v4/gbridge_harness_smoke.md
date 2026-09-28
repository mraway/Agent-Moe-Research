# G-bridge 上的 NORMAL-ONLY harness 冒烟（gpt-oss-20b × v2.5 布局，2026-09-07）

执行人：Opus 5 研究工程师（受 Claude Fable 5.1 委派）。
依据：`docs/research_v4/detector_harness_g.md`（尤其 §11 开放项 6：G-bridge 布局只有 fallback，需实测一次），
`docs/research_v4/g_bridge_run_log.md`（240 条 trace 的采集记录），
`docs/research_v4/agent_v3_dataset_design.md` §1.3 / §5 / §7 / §15（视图、H 规则、校准列）。

**性质声明。** 本文是**工程冒烟**：把 dataset-G 检测器 harness 接到 G-bridge（v2.5 确定性 controller、
单步 episode、无 commentary）的 gpt-oss 轨迹上，只跑**正常臂**，只报**假警率一侧**。
本轮**没有读取任何攻击臂路由**（见 §1.3 的数据纪律与一条诚实交代）、**没有任何检测结论**、
**没有改动任何既有 config / 数据 / 标签 / 冻结结果 / trace**。
表里的 FAR 数字是这一对 80/80 半份上的**算术结果**，不是"检测器有多好"的结论——
其中主表的 pooled FAR 更是一个**恒等式**（§4.1）。

---

## 1. 文件清单与本轮改动

| 文件 | 行 | 状态 | 作用 |
|---|---|---|---|
| `src/research_v2/io_g.py` | 892 → 1169 | **改（additive）** | G-bridge / v2.5 布局支持：manifest 重建 token 轴、精确通道分段、路径取臂、episode 下标回退 |
| `tests/test_research_v4_gbridge.py` | 311 | 新增 | 23 项：token 轴、词表垫片、精确分段、分派、路径取臂、episode 下标、真实 trace 集成 |
| `docs/research_v4/gbridge_harness_smoke.md` | — | 新增 | 本文 |
| `artifacts/agent_v2/research_v4/detectors_g/gbridge_*` | 33 个 run + 3 个 json | 新增 | 33 次 CLI 调用 = **60 个格**的 `result.json`、切分表、两份 sanity 读数 |

`src/research_v2/trm3_g.py` 与 `scripts/research_v4/run_detectors_g.py` **一字未动**——
本轮所有的表都是用**现有 CLI 原样**跑出来的（33 次调用 / 60 个格），这本身是冒烟的一部分。
`src/research_v2/trm3.py`、四个冻结 scorer、`src/agent_v3/harmony.py` 同样未动。

### 1.1 G-bridge 布局与 Agent v3 布局的三处缺口

`detector_harness_g.md` §11 开放项 6 只说"若无 `episodes`/`channel_segments`，loader 用
`channel_boundaries` 兜底"。实测 240 条 trace，缺口比预期多，而且**兜底口径是错的**：

| # | Agent v3 有 | G-bridge（240/240） | loader 原行为 |
|---|---|---|---|
| G1 | `model_generation.routing_step_index_first_decode` | **无** | `KeyError`——loader 直接跑不起来 |
| G2 | `model_generation.global_token_offset` | **无** | 静默取 0（单步下恰好正确，多步下会错） |
| G3 | `model_generation.channel_segments` | **无**，只有 `channel_boundaries` | 走 `_spans_from_boundaries`，**偏移一位且吃掉终止符** |
| G4 | `model_generation.episode_index` | **无** | 静默取 0（单轮下正确，多轮下会把多个 turn 并成一个 episode） |
| G5 | `trace["episodes"]` | **无** | 已有回退（`{index: {...}}`），可用 |
| G6 | `trace["dataset_role"]` | **无**（`run_summary.json` 里也没有） | 回退成 `""`，探针守卫不触发（G-bridge 本来就不是探针） |

**G3 的错在哪。** `channel_boundaries[c]` 是该通道**通道名 token**的下标（实测：token 0 = `<|channel|>`，
1 = `analysis`，2 = `<|message|>`，3.. 正文）。原 fallback 取 `body_start = marker + 1`（落在 `<|message|>` 上，
差一位），并把 `body_end` 一路推到**下一个通道的 marker**，于是 analysis 段吞掉了 `<|end|>`、
`<|start|>`、`assistant`、`<|channel|>`、`final` 五个 token。`message` 口径下这个错**恰好被 gap 填充抵消掉**
（最终 tag 只差把 `<|start|>assistant` 归给前一条而不是后一条消息），但 `body` 口径下 analysis 正文
会多出 5 个 marker token、final 正文会以 `<|message|>` 开头——敏感性列会直接偏。

### 1.2 修法：用 trace 自己的 manifest，把 v2.5 布局补成 Agent v3 布局

三个新的公开函数，全部 additive，不改任何既有签名的语义：

* **`read_step_manifest(trace_dir)`** —— 读一次 `manifest.jsonl`，缓存，返回
  `(decode 行, {token_id: token_text})`。每个采集运行都写了 `token_texts`，所以 trace **自带一张 id→字面量表**，
  loader 不需要打开 tokenizer（更不用说模型）就能做 harmony 分段。
* **`with_token_axis(trace_dir, steps)`** —— 补 G1/G2。第 k 个生成步按 `output_token_count` 顺序消费 decode 行；
  **三道自检**：分片下标必须连续、`agent_steps` 必须等于该步声明的 `agent_step`、
  拼出来的 token id 必须**逐位等于** `output_token_ids`。映射错了在这里抛错，而不是变成静默的分数。
  已带 `routing_step_index_first_decode` + `global_token_offset` 的步（Agent v3）**原样返回**，不碰 P0 路径。
* **`spans_from_token_ids(ids, vocabulary, ...)`** —— 补 G3。用一个 `TokenTextVocabulary` 垫片
  （只实现 `convert_tokens_to_ids` / `decode` 两个方法，未出现过的 harmony piece 给**负数哨兵**，
  真实 token id 永远不可能等于它）驱动**未经修改的 `agent_v3.harmony.segment_channels`**。
  于是 v2.5 布局与 Agent v3 布局由**同一份分段实现**产出，spans 的字段含义逐个对齐
  （`header_start` / `body_start` / `body_end` / 终止符）。

  `segment_spans(steps, vocabulary=...)` 是新的 keyword；**不传 `vocabulary` 时行为与之前逐位相同**
  （旧的 `channel_boundaries` 兜底保留、既有单测保持绿），loader 只在某个步缺 `channel_segments` 时才去建词表。

另两处：

* **G4**：`_generation_steps` 在事件没有 `episode_index` 时，按 `conversation_turn` 的**秩**定 episode 下标
  （设计 §1.1：一个 episode = 一个用户轮）。G-bridge 240/240 都是单轮，落到 episode 0。
* **臂从路径取**：新增 `_variant_of(trace, trace_dir)`。批次布局是 `<run>/<pair_group_id>/<arm>/trace.json`，
  目录名在 `KNOWN_VARIANTS` 里就以目录名为准；`perturbation.arm` 与目录名**不一致直接抛错**。
  实测 240/240 两者一致，所以这是一道守卫而不是一次替换。

### 1.3 数据纪律（本轮攻击臂路由的处置）

* `--normal-only-smoke` 让 CLI 用 `variants=NORMAL_VARIANTS` 调 `load_g`；**臂过滤发生在读任何 shard 之前**
  （`load_g` 的循环里 `variant` 不在集合内就 `continue`，`_episodes_of_trace` 根本不被调用）。
  随后 CLI 再做一次"池中出现非正常臂就 `SystemExit`"的复查。本轮 33 次 CLI 调用全部带这个开关。
* **旁证**：路由缓存 `artifacts/agent_v2/research_v4/g_routing_cache/g_bridge_gpt_oss_20b/` 落盘
  **恰好 160 个文件 / 4.0 MB**，等于 160 条正常 episode，**0 条攻击 episode**。
* **一条诚实交代**：在 loader 还没修好、定位 `KeyError` 的调试阶段，我用不带 `variants` 的 `load_g` 加载过
  **1 条攻击臂 episode**（`b2-f0-001…/attack`），读到的是 `top_k_ids` 张量并只统计了通道 tag 计数
  （`{'analysis': 31, 'commentary': 0, 'final': 91}`）。**没有计算任何路由统计量、没有比较、没有落盘**
  （那次调用 `cache_dir=None`）。此后所有加载都显式带 `variants=NORMAL_VARIANTS`。记录在此。

---

## 2. 池与切分

### 2.1 切分规则（确定性、有种子、场景不交叉、同场景两臂同侧）

```
seed = 20260907        # 设计冻结日
分层 = split_group_id（预注册 fold，b2-fold-0..4，规模 20/15/15/15/15，不等）
for fold in sorted(folds):          # 每个 fold 内 random.Random(seed).shuffle 一次
    for scenario in shuffled:       # 依次发给当前较小的一半（并列给 fit）
        (fit if len(fit) <= len(cal) else cal).append(scenario)
```

得到**恰好 40 / 40 个 scenario**，fold 混合平衡到 ±1：
fit `{f0:10, f1:8, f2:7, f3:8, f4:7}`，cal `{f0:10, f1:7, f2:8, f3:7, f4:8}`。
一个 scenario 的 **clean 与 benign_control 永远在同一半**，所以两半各 **80 条正常 episode**。
完整清单与哈希落在 `artifacts/agent_v2/research_v4/detectors_g/gbridge_normal_split.json`：

```
fit  sha256(",".join(fit)) = cc44b98b265ebc81eae9fc927ebf7081c78278586eeeb3612c1fab84aa49a9e4
cal  sha256(",".join(cal)) = 9b254bedb49c0903fceebc8aaac459b2d8e443e2ecbc10294f495f33d6c435b0
```

（G-bridge 没有 `benign_lexical` 臂，也没有质量标注，所以 `filtered` 分母退化成 `all`，
`filter_status = "unlabelled"`；`filtered` 列与 `all` 列在全部 **60 / 60** 个格里逐位相同。）

### 2.2 池的规模与通道构成

| 池 | episode | 会话 | scenario | token | analysis | commentary | final | other |
|---|---|---|---|---|---|---|---|---|
| fit | 80（clean 40 / benign 40） | 80 | 40 | 9 440 | 1 496 | **33** | 7 911 | 0 |
| cal（= target） | 80（clean 40 / benign 40） | 80 | 40 | 9 685 | 1 698 | **0** | 7 987 | 0 |
| 合计 160 | | | 80 | 19 125 | 3 194（16.7%） | 33（0.17%） | 15 898（83.1%） | 0 |

对照 Agent v3 的 P0（`detector_harness_g.md` §2）：analysis 12.5% / commentary 8% / final 79.5%。
G-bridge 的 commentary 几乎不存在（controller 走完工具，模型只写最终答复），
analysis 反而**更长**：160 条里 154 条有 analysis 段，段长 min 7 / 中位 **18** / max 70
（P0 的中位是 10）；final 段长 min 33 / 中位 88.5 / max 341。

**通道分布是不对称的，而且刚好偏向可用的一侧**：
3 条带 commentary 的 trace 全部落在 **fit** 半，3 条没有 analysis 的 trace 全部落在 **cal** 半
（这是 seed 20260907 的结果，不是设计）。见 §11 开放项 3。

### 2.3 端点（因果窗口不跨通道边界）

| 统计量 | w | 池 | V1 | V2 | V3 | → analysis | commentary | final |
|---|---|---|---|---|---|---|---|---|
| S / M / P | 8 | fit | 8 320 | 8 308 | 7 351 | 957 | 12 | 7 351 |
| S / M / P | 8 | cal | 8 586 | 8 586 | 7 427 | 1 159 | 0 | 7 427 |
| B | 4 | fit | 8 960 | 8 936 | 7 671 | 1 265 | 24 | 7 671 |
| B | 4 | cal | 9 214 | 9 214 | 7 747 | 1 467 | 0 | 7 747 |

端点密度：V1 / w=8 在 cal 上 **0.887 端点/token**（P0 是 0.81），V3 0.767。
154 个 analysis 段里只有 1 段（7 token）短到在 w=8 下不产生端点——
P0 里"analysis 极短所以几乎没有端点"的问题在 G-bridge 上**基本不存在**，
因为 v2.5 的一次性答复比 Agent v3 的多步 analysis 长。

**V1 与 V2 在 cal / target 上完全等价**（8 586 = 8 586，9 214 = 9 214），
因为 cal 半份一个 commentary token 都没有；两者只在 fit 池差 12（w=8）/ 24（w=4）个端点，
即 3 条 commentary trace 的贡献。下面所有 V1 / V2 行的差异只可能来自标准化桶。

### 2.4 通道条件化位置桶（在 fit 池上估计，桶宽 32，`min_bucket_traces` = 冻结默认 30）

| 视图 | 统计量 | analysis | commentary | final |
|---|---|---|---|---|
| V1 | S / M / P | 1 桶（76 episode / 957 窗口） | 1 桶（**3 episode / 12 窗口**） | 3 桶（80 / 76 / 49 episode；2 548 / 1 973 / 2 830 窗口） |
| V1 | B | 1 桶（77 / 1 265） | 1 桶（**3 / 24**） | 3 桶（80 / 79 / 49；2 559 / 2 086 / 3 026） |
| V2 | S / M / P | 1 桶（76 / 957） | — | 同上 |
| V2 | B | 1 桶（77 / 1 265） | — | 同上 |
| V3 | S / M / P | — | — | 3 桶（80 / 76 / 49；2 548 / 1 973 / 2 830） |
| V3 | B | — | — | 3 桶（80 / 79 / 49；2 559 / 2 086 / 3 026） |

全部 60 个格 **0 个复用桶**。analysis 只有 1 个桶：analysis 段中位 18 token → 每 episode 中位 11 个端点，
远不到桶宽 32 的第二桶。**commentary 的 μ/σ 建立在 3 条 episode / 12 个窗口上**——
在 G-bridge 上它不影响任何目标端点（cal 半没有 commentary），但这是一个必须写进预注册的脆弱点（§11 开放项 3）。

---

## 3. H 规则与存活曲线

**设计要求**（§15.1 / §6.4，冻结）：`H = 过滤后 G-cal 中存活路径 ≥ 90 的最大 look 数`，
目标 384（已知不可达，P0 0/22），期望落在 150–250，**出厂门 H ≥ 128**，删失比例必须报告。

**在这个池子上 ≥90 存活是结构性不可能的**：校准半份只有 **80** 条路径，`min_survivors = 90 > 80`
会让 `trm3_g.h_horizon` 返回 `H = 0`（"池子太小"，按设计直接报错而不是悄悄退化）。
所以本轮报**存活曲线**本身，并按任务要求给出 ≥40 与 ≥60 两档。

### 3.1 存活曲线：给定存活数下限求 k（校准半份，80 条路径）

| 视图/统计量 | w | 最短 | 中位 | 最长 | k@≥80 | k@≥70 | k@≥60 | k@≥50 | **k@≥40** | k@≥30 | k@≥20 | k@≥10 | k@≥90 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V1 / V2 · S,M,P | 8 | 38 | 99 | 243 | 38 | 57 | **63** | 78 | **100** | 124 | 144 | 166 | **不可达** |
| V1 / V2 · B | 4 | 42 | 107 | 251 | 42 | 65 | **71** | 86 | **108** | 132 | 152 | 174 | **不可达** |
| V3 · S,M,P | 8 | 26 | 88 | 242 | 26 | 47 | **52** | 64 | **91** | 106 | 126 | 153 | **不可达** |
| V3 · B | 4 | 30 | 92 | 246 | 30 | 51 | **56** | 68 | **95** | 110 | 130 | 157 | **不可达** |

### 3.2 反过来：给定 k 求存活数（同一池）

| 视图/统计量 | k=64 | k=80 | k=100 | **k=128（出厂门）** | k=150 | k=192 | k=240 | **k=384（设计目标）** |
|---|---|---|---|---|---|---|---|---|
| V1 / V2 · S,M,P | 59 | 49 | 40 | **27 (34%)** | 19 | 3 | 1 | **0** |
| V1 / V2 · B | 72 | 53 | 45 | **30 (38%)** | 21 | 6 | 1 | **0** |
| V3 · S,M,P | 50 | 44 | 35 | **19 (24%)** | 11 | 2 | 1 | **0** |
| V3 · B | 52 | 45 | 36 | **23 (29%)** | 11 | 2 | 1 | **0** |

**三条可以直接用的事实：**

1. **H = 384 在 G-bridge 上存活 0/80**，与 P0 的 0/22 同向。设计 §15.1 已经写明 384 不可达，本轮在 4 倍大的
   正常池上确认了这一点。
2. **出厂门 H ≥ 128 在 look 轴上等价于"约 34% 的路径要活过 128 个 look"**（V1）。要凑够 90 条存活，
   过滤后的 G-cal 至少要 `90 / 0.34 ≈ 265` 条 episode——比设计的"过滤后 ≥240"**还要多**，
   而且这是**乐观**的换算：G 是模型自己调工具的多步 runtime，episode 更长（P0 平均 3 174/22 ≈ 144 token），
   长度分布可能比 G-bridge 更友好，但也可能因为丢 token 的视图（V3）更差（V3 这里只有 24%）。
   **建议**：G-cal 冻结 H 时，把 §3.2 这张"给定 k 求存活"的表在真实 G-cal 上重算一遍，
   如果 V3 的 128-存活率仍在 25% 上下，就要么接受 V3 的 H 低于 V1，要么把 §6.4 的门写成分视图的。
3. **H 的单位仍是 look，不是 token**（`detector_harness_g.md` §5 的 D6）。第 H 个端点落在哪个 token：

| 视图/统计量 | H@≥40 | 第 H 个端点的 token（min / 中位 / max，取存活的 40 条） | H@≥60 | 同左 |
|---|---|---|---|---|
| V1 / V2 · S,M,P | 100 | 113 / 113 / 113 | 63 | 69 / 76 / 76 |
| V1 / V2 · B | 108 | 113 / 113 / 113 | 71 | 73 / 76 / 76 |
| V3 · S,M,P | 91 | 97 / **118** / 167 | 52 | 58 / 78 / 128 |
| V3 · B | 95 | 97 / **118** / 167 | 56 | 58 / 78 / 128 |

   **V1 的 look↔token 是仿射的**：保留全部通道时端点网格只在两个通道段的开头各缺 w−1 个，
   所以第 k 个端点恒在 token `k + 2(w−1) − 1`（k=100, w=8 → **113**），**与 analysis 段长无关**，
   于是 min = 中位 = max。**V3 不是**：丢掉长度逐条不同的 analysis 前缀后，第 91 个端点散在 token 97–167。这正是 D6 要说的事，
   在 G-bridge 上第一次有了具体数字：**V3 的 H 必须按 look 冻结，按 token 冻结会引入 70 token 的抖动。**

### 3.3 两档 H 下的删失

| 档 | 视图/统计量 | H | 存活 | 被删失的路径 | 被删失的端点 | 占校准端点 |
|---|---|---|---|---|---|---|
| `min_survivors = 40` | V1/V2 S,M,P | 100 | 40/80 | 36 | 1 911 | 22.3% |
| | V1/V2 B | 108 | 40/80 | 36 | 1 911 | 20.7% |
| | V3 S,M,P | 91 | 40/80 | 38 | 1 586 | 21.4% |
| | V3 B | 95 | 40/80 | 38 | 1 586 | 20.5% |
| `min_survivors = 60` | V1/V2 S,M,P | **63** | 60/80 | 59 | **3 717** | **43.3%** |
| | V1/V2 B | **71** | 60/80 | 59 | 3 713 | 40.3% |
| | V3 S,M,P | **52** | 61/80 | 59 | 3 436 | **46.3%** |
| | V3 B | **56** | 61/80 | 59 | 3 436 | 44.4% |

把存活门从 40 提到 60，H 掉 35–43%（100→63、91→52），删失的校准端点从约 21% 涨到 **40–46%**。
这是 H 规则在小池上的核心张力，真实 G-cal 上会缓和但不会消失。

---

## 4. 主表：12 个格（视图 × 统计量），α = 0.10，target = 校准半份

`min_survivors = 40`（≥90 在 80 条路径上不可能，见 §3），其余全部是 harness 默认
（层带全 24 层、rare 阈值 0.02、桶宽 32、`min_bucket_traces` 30）。
每一格 `n_ref = 80`，`alpha_eff = ⌊81 × 0.1⌋ / 81 = 8/81 = 0.0988`。

| 视图 | 统计量 | w | fit 端点 | cal 端点 | 目标端点 | H | 存活 | 删失路径 | FAR pooled | FAR clean | FAR benign | onset/1k | 拟合 s | 打分 s | s/1k 端点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V1 | S | 8 | 8 320 | 8 586 | 8 586 | 100 | 40/80 | 36 | **0.0875** | 0.100 | 0.075 | 1.049 | 0.02 | 0.046 | 0.0054 |
| V1 | M | 8 | 8 320 | 8 586 | 8 586 | 100 | 40/80 | 36 | **0.0875** | 0.100 | 0.075 | 1.049 | 0.09 | 0.082 | 0.0096 |
| V1 | P | 8 | 8 320 | 8 586 | 8 586 | 100 | 40/80 | 36 | **0.0875** | 0.075 | 0.100 | 1.049 | 0.02 | 0.043 | 0.0050 |
| V1 | B | 4 | 8 960 | 9 214 | 9 214 | 108 | 40/80 | 36 | **0.0875** | 0.100 | 0.075 | 0.959 | 0.06 | 0.044 | 0.0047 |
| V2 | S | 8 | 8 308 | 8 586 | 8 586 | 100 | 40/80 | 36 | **0.0875** | 0.100 | 0.075 | 1.049 | 0.02 | 0.047 | 0.0054 |
| V2 | M | 8 | 8 308 | 8 586 | 8 586 | 100 | 40/80 | 36 | **0.0875** | 0.100 | 0.075 | 1.049 | 0.18 | 0.081 | 0.0094 |
| V2 | P | 8 | 8 308 | 8 586 | 8 586 | 100 | 40/80 | 36 | **0.0875** | 0.075 | 0.100 | 1.049 | 0.01 | 0.042 | 0.0049 |
| V2 | B | 4 | 8 936 | 9 214 | 9 214 | 108 | 40/80 | 36 | **0.0875** | 0.100 | 0.075 | 0.959 | 0.08 | 0.046 | 0.0050 |
| V3 | S | 8 | 7 351 | 7 427 | 7 427 | 91 | 40/80 | 38 | **0.0875** | 0.150 | 0.025 | 1.198 | 0.02 | 0.045 | 0.0060 |
| V3 | M | 8 | 7 351 | 7 427 | 7 427 | 91 | 40/80 | 38 | **0.0875** | 0.125 | 0.050 | 1.198 | 0.16 | 0.080 | 0.0108 |
| V3 | P | 8 | 7 351 | 7 427 | 7 427 | 91 | 40/80 | 38 | **0.0875** | 0.150 | 0.025 | 1.198 | 0.01 | 0.037 | 0.0049 |
| V3 | B | 4 | 7 671 | 7 747 | 7 747 | 95 | 40/80 | 38 | **0.0875** | 0.125 | 0.050 | 1.136 | 0.08 | 0.039 | 0.0050 |

`filtered` 列与 `all` 列逐位相同（无质量标注）。`session_far = 0.0125`、`α_ep = 0.025`（会话 = episode，
G-bridge 没有多 episode 会话），`positives = 0, excluded = {unlabelled: 80}`（无标注 = 无正例，符合预期）。
长度三分位、matched-group FAR 等分母都在各 `result.json` 的 `metrics.far` 里。

### 4.1 pooled FAR = 0.0875 是恒等式，不是测量

**36 个 target = cal 半份的格里，报警数全部恰好是 7，pooled FAR 全部恰好是 7/80 = 0.0875。**
（24 个留出格的报警数则在 1–18 之间散开。）这是算术，不是巧合：

* target 池 = cal 池，参照集就是这 80 条路径**自己**在 H 处截断后的全路径最大值；
* running max 单调 ⇒ `p = (1 + #{Z ≥ R}) / 81` 单调不增 ⇒ "是否报警"只由**终点**的 p 决定；
* 每条目标路径的终点最大值**等于它自己在参照集里的那个值**，所以 `#{Z ≥ R}` = 它在 80 个值里的降序名次；
* 报警条件 `p ≤ 0.10` ⇔ 名次 ≤ 7 ⇒ **恰好前 7 条报警，与统计量、视图、层带、H 档全部无关**。

所以主表里**唯一带信息的列是 clean / benign 的拆分**（哪 7 条报警随统计量而变：0.100/0.075、
0.075/0.100、0.125/0.050、0.150/0.025 四种组合都出现了）与 **onset/1k**（= 7000/合格端点，也几乎是恒等式）。
把 α 定在 0.10、n_ref = 80 时**可达的名义值**是 0.0988，实测 0.0875 落在它下面一格——
这正是保形保证的形式，而不是对 G-bridge 正常流量的经验估计。

**结论**：用校准半份自己当 target 只能验证"管道通了、名义 α 可达、状态机与删失语义生效"。
要看真的 FAR，必须换一个目标池 —— 见 §5。

---

## 5. 留出诊断列：target = 拟合半份（同一份校准，α = 0.10，min_survivors = 40）

拟合半份与校准半份**场景不交叉**，所以目标路径不在参照集里，`p` 不再自计。
（注意这仍不是干净的留出：μ/σ 就是在这 80 条上估的，所以这一列对**标准化**是乐观的，
对**保形比较**是诚实的。真正的第三方池要等 G-fit / G-cal 产出。）

| 视图 | 统计量 | H | FAR pooled | FAR clean | FAR benign | onset/1k |
|---|---|---|---|---|---|---|
| V1 | S | 100 | **0.1250** | 0.100 | 0.150 | 1.548 |
| V1 | M | 100 | **0.0875** | 0.050 | 0.125 | 1.083 |
| V1 | P | 100 | **0.0625** | 0.050 | 0.075 | 0.774 |
| V1 | B | 108 | **0.0375** | 0.025 | 0.050 | 0.422 |
| V2 | S | 100 | **0.1250** | 0.100 | 0.150 | 1.550 |
| V2 | M | 100 | **0.0875** | 0.050 | 0.125 | 1.085 |
| V2 | P | 100 | **0.0625** | 0.050 | 0.075 | 0.775 |
| V2 | B | 108 | **0.0375** | 0.025 | 0.050 | 0.424 |
| V3 | S | 91 | **0.1250** | 0.100 | 0.150 | 1.780 |
| V3 | M | 91 | **0.0750** | 0.050 | 0.100 | 1.068 |
| V3 | P | 91 | **0.1000** | 0.075 | 0.125 | 1.424 |
| V3 | B | 95 | **0.0500** | 0.050 | 0.050 | 0.674 |

12 个格的均值 0.083，名义可达值 0.0988；S 在三个视图上都**超出**名义值（0.125，即 10/80），
B 在三个视图上都明显**保守**（0.0375–0.05）。`n = 80` 时 0.0988 的 Wilson 95% 区间是
[0.051, 0.184]，所以单看任何一格都不能说"S 失控"或"B 太紧"；
可以说的是**四个族在同一池上的排序是 S > M ≈ P > B，且三个视图之间一致**。

---

## 6. 稳健性列：冻结的 OLMoE 层带 vs 24 层默认（M 与深度链 B，FAR 一侧）

`detector_harness_g.md` 的偏离 D8：冻结的 M 是 WGM `g1` **层 5–15**、B 是 `pdm` d1 **层 5–11**，
都是 16 层模型上的选择；harness 在 24 层上把两者的默认改成"全部 MoE 层"。本节量这个改动。

三个层带：

| 记号 | M | B | 定义 |
|---|---|---|---|
| `all24` | 0–23（24 层） | 0–23（24 层） | harness 当前默认 |
| `frozen` | **5–15**（11 层） | **5–11**（7 层） | 冻结值的**字面移植**（同样的层号，24 层模型上） |
| `mid24` | **8–23**（16 层） | **8–17**（10 层） | **按比例**移植：冻结带在 16 层上的相对深度区间乘 24/16（M = "去掉最前 31%"，B = 相对深度 [0.31, 0.75)） |

**主表口径（target = cal 半份）在这一列是空的**：FAR 恒等于 0.0875（§4.1），只有 clean/benign 的拆分会动。
所以下表用**留出口径**（target = fit 半份），这是唯一能分辨层带的口径。

| 视图 | 统计量 | 层带 | 层数 | H | FAR pooled | FAR clean | FAR benign | onset/1k | 打分 s |
|---|---|---|---|---|---|---|---|---|---|
| V1 | M | all24 | 24 | 100 | 0.0875 | 0.050 | 0.125 | 1.083 | 0.082 |
| V1 | M | **frozen 5–15** | 11 | 100 | **0.2250** | 0.250 | 0.200 | **2.786** | 0.042 |
| V1 | M | mid24 8–23 | 16 | 100 | 0.1000 | 0.075 | 0.125 | 1.238 | 0.044 |
| V1 | B | all24 | 24 | 108 | 0.0375 | 0.025 | 0.050 | 0.422 | 0.044 |
| V1 | B | frozen 5–11 | 7 | 108 | **0.0125** | 0.000 | 0.025 | 0.141 | 0.048 |
| V1 | B | mid24 8–17 | 10 | 108 | **0.0125** | 0.000 | 0.025 | 0.141 | 0.046 |
| V2 | M | all24 | 24 | 100 | 0.0875 | 0.050 | 0.125 | 1.085 | 0.086 |
| V2 | M | **frozen 5–15** | 11 | 100 | **0.2250** | 0.250 | 0.200 | 2.791 | 0.043 |
| V2 | M | mid24 8–23 | 16 | 100 | 0.1000 | 0.075 | 0.125 | 1.240 | 0.045 |
| V2 | B | all24 | 24 | 108 | 0.0375 | 0.025 | 0.050 | 0.424 | 0.043 |
| V2 | B | frozen 5–11 | 7 | 108 | 0.0125 | 0.000 | 0.025 | 0.141 | 0.045 |
| V2 | B | mid24 8–17 | 10 | 108 | 0.0125 | 0.000 | 0.025 | 0.141 | 0.048 |
| V3 | M | all24 | 24 | 91 | 0.0750 | 0.050 | 0.100 | 1.068 | 0.076 |
| V3 | M | **frozen 5–15** | 11 | 91 | **0.1750** | 0.225 | 0.125 | 2.492 | 0.037 |
| V3 | M | mid24 8–23 | 16 | 91 | 0.0625 | 0.050 | 0.075 | 0.890 | 0.042 |
| V3 | B | all24 | 24 | 95 | 0.0500 | 0.050 | 0.050 | 0.674 | 0.040 |
| V3 | B | frozen 5–11 | 7 | 95 | 0.0125 | 0.000 | 0.025 | 0.168 | 0.039 |
| V3 | B | mid24 8–17 | 10 | 95 | 0.0125 | 0.000 | 0.025 | 0.168 | 0.043 |

**读数（FAR 一侧，无攻击臂，所以只能谈校准而不能谈判别力）：**

1. **M 对层带高度敏感，而且冻结值的字面移植是最坏的一档**：`frozen 5–15` 在三个视图上都把 FAR 顶到
   0.175–0.225，是名义 0.0988 的 **1.8–2.3 倍**；`all24` 与 `mid24` 都在名义值附近
   （0.0625–0.100）。层带只影响特征维度与白化，不影响 H（三档 H 完全相同），所以差异全部来自统计量本身。
   层号 5–15 在 16 层上是相对深度 31%–97%，在 24 层上却只有 **21%–65%**——同一个层号在两个模型上
   指的是完全不同的深度区间，M 因此变成一个偏低-中层的统计量，它在正常池上的白化平方距离尾部更重。
2. **`mid24`（按相对深度移植）与 `all24` 几乎等价**（V1 0.100 vs 0.0875，V3 0.0625 vs 0.0750）——
   相对深度移植是安全的，层号移植不是。
3. **B 在任何层带下都保守**（0.0125–0.05，全部低于名义值），窄带比全层更保守
   （0.0125 = 1/80，两个窄带给出**相同的 FAR 与 onset 读数**）。这与 P0 上"B 的 H 更长、端点更多"一致：
   d1 深度链在 w=4 上产生更多、更相关的端点，全路径最大值的分布更集中。
4. **成本**：层带只影响拟合与特征装配（M 的 `all24` 打分 0.08 s vs 窄带 0.04 s），量级都可忽略。

**给组长的一条具体建议**：`detector_harness_g.md` §11 开放项 3 要求在 G 预注册前**冻结** M / B 的层带。
本轮的证据支持"冻结成 `all24`（或按相对深度的 `mid24`），并**明确排除**冻结层号的字面移植"，
理由是校准而不是判别力——判别力要等攻击臂解封后才能谈。

---

## 7. Sanity（5 条正常臂 trace，`gbridge_sanity_topk.json` / `gbridge_sanity_channels.json`）

抽样：`b2-f0-002…free-verse/clean`、`b2-f0-003…free-verse/benign_control`、
`b2-f0-077…economics-explanation/clean`、`b2-f0-027…probability-calculation/benign_control`、
`b2-f2-012…sql-query/clean`（cal 半 3 条 + fit 半 2 条，两臂都覆盖）。

### 7.1 `top_k_ids` 是不是存下来的 logits 的 top-k

逐 shard 复算 `torch.topk(router_logits.float(), 4)`，共 **16 896 个 (层, token) 位置**：

| 检查 | 结果 |
|---|---|
| top-4 **集合**与存储不一致 | **73 / 16 896 = 0.43%** |
| 其中"存储的 4 个 id 与复算的 4 个 id 携带**完全相同的 logit 多重集**" | **73 / 73 = 100%** |
| top-4 **顺序**与存储不一致 | 303 / 16 896 = 1.79% |
| `argmax` 与 `top_k_ids[..., 0]` 不一致 | 51 / 16 896 = 0.30% |
| 其中"存储的第一个 id 的 logit 等于最大 logit" | **51 / 51 = 100%** |
| `max │ softmax(gather(logits, top_k_ids)) − top_k_weights │` | 0.001883 – 0.001949 |

**结论**：存储的 `top_k_ids` **是**存储的 logits 的一个合法 top-k；全部不一致都是 **bf16 下的精确并列**
（第 4 名与第 5 名、或第 1 名与第 2 名的 logit 逐位相等），选择集合本身没有分歧，只有并列时的 tie-break 不同。
权重误差 0.001883–0.001949 与采集日志 §4 记录的 0.001935–0.001953 同量级，
并且独立确认了 `top_k_weight_semantics = softmax_over_selected_logits_only`（本轮是拿存储 logits
重新 softmax 得到的）。

**对统计量的含义**：四个族只读 `top_k_ids`，所以管道内部是**自洽的**（永远用存储值）；
但 **B（深度链 d1）条件在逐层 top-1 上**，而 top-1 在 ~0.3% 的位置是并列的 ——
换任何影响 router 数值的依赖版本都可能翻转这些位置。记为开放项。

### 7.2 通道边界是否与解码文本对齐

对 5 条 trace 逐条（`gbridge_sanity_channels.json`）：

| 检查 | 5 / 5 |
|---|---|
| 各通道 span 互不重叠且按 `header_start` 严格有序 | ✅ |
| header 片段逐字为 `<\|channel\|>analysis<\|message\|>` / `<\|channel\|>final<\|message\|>` | ✅ |
| `analysis` 的 `end` < `final` 的 `header_start`（**analysis 在 token 轴上先于 final**） | ✅ |
| analysis 正文文本在 `model_generation.content` 里出现的位置**早于** final 正文文本 | ✅ |
| `generation_channels.boundaries[c]` 指的那个 token，其通道 tag 恰为 `c` | ✅ |
| `other` tag 数 = 0（每个生成 token 都归属某个通道） | ✅ |

一个具体例子（`b2-f0-001…/clean`，112 token）：
analysis `header_start=0, body=[3,21), 终止符 21`，final `header_start=24, body=[27,111), 终止符 111`；
`<|start|>assistant`（22–23）按 `message` 口径归给**后一条**消息（final），
最终 tag = analysis 0–21 / final 22–111。修好之前的 fallback 会把 22–23 归给 analysis，
并且在 `body` 口径下把 5 个 marker token 算进 analysis 正文。

---

## 8. 成本

| 项目 | 值 |
|---|---|
| 60 个格的打分端点合计 | 498 088 |
| 打分墙钟合计 | **2.96 s**（CPU，单线程） |
| 每 1000 端点 | min 0.0046 s / 中位 **0.0052 s** / max 0.0108 s（max 是 M：白化平方距离） |
| 单格拟合墙钟 | 0.01 – 0.18 s |
| 单格打分墙钟 | 0.034 – 0.086 s |
| 单次 CLI 调用（含加载 3 个池 240 条 episode） | ≈ 6 s（首次冷缓存），≈ 5 s（热缓存） |
| 路由缓存 | 160 个 safetensors / **4.0 MB**（= 160 条正常 episode，0 条攻击） |
| `result.json` 产物 | 33 个 run，15 MB |

打分成本 0.0052 s / 1000 端点与 P0 冒烟记录的 0.007 一致。

---

## 9. 测试

```
去掉本轮新增文件后：823 passed, 72 subtests
本轮结束（含本轮新增的 23 项）：847 passed, 72 subtests   —— 全绿
```

（另一路 agent 正在并发向 `src/agent_v3/packets/` 落盘，所以全仓计数在本轮内从 841 漂到 847；
与 io_g 直接相关的 6 个文件 `tests/test_research_v4_{io_g,detectors_g,statistics,gbridge}.py`、
`tests/test_agent_v3_{harmony,packets_validate}.py` 单独跑 **151 passed**。）

`tests/test_research_v4_gbridge.py` 的 23 项：
词表垫片（piece 解析、负哨兵不与真实 id 碰撞）、
`spans_from_token_ids` 的精确 span 与偏移、`message`/`body` 两个口径的 tag、
`segment_spans` 的分派（有词表走精确、无词表仍走旧 fallback，逐位保持旧行为）、
`with_token_axis` 的单步 / 多步重建与**三种不一致各自抛错**、Agent v3 步原样返回、
路径取臂（补齐 / 冲突抛错 / 非臂目录不干预）、episode 下标按 conversation_turn 的秩、
以及对真实 G-bridge trace 的 6 项集成（只加载正常臂、单 episode、24/32/4 几何、
tag 覆盖率、边界与 `generation_channels` 一致、analysis 先于 final）。

---

## 10. 复现

```bash
# 切分（写死在 artifacts/.../detectors_g/gbridge_normal_split.json）
seed = 20260907, 按 split_group_id 分层，同场景两臂同侧，40/40

B=artifacts/agent_v2/g_bridge_gpt_oss_20b/batch
python scripts/research_v4/run_detectors_g.py \
  --fit $B --fit-scenarios "<40 个 fit scenario>" \
  --cal $B --cal-scenarios "<40 个 cal scenario>" \
  --target $B --target-scenarios "<40 个 cal scenario>" \
  --view V1 --statistic S,M,P,B --alpha 0.10 --h-min-survivors 40 \
  --cal-name gbridge_cal --normal-only-smoke --outputs primary \
  --run-name gbridge_main_V1_h40
# 留出列：--target-scenarios 换成 fit 那 40 个，--run-name gbridge_heldout_V1_h40
# 层带列：--statistic M --layers 5,6,...,15（或 8,...,23），B 同理
```

---

## 11. 开放项（每条一行 + 一句展开）

1. **主表的 pooled FAR 是恒等式（0.0875 = 7/80），不能当成 G-bridge 的假警率**——target 池 = cal 池时报警数被
   `⌊(n+1)α⌋−1` 锁死；有信息的是 §5 的留出列与 clean/benign 拆分。
2. **出厂门 H ≥ 128 在 look 轴上要求约 34%(V1) / 24%(V3) 的路径活过 128 个 look**——按 G-bridge 的比例外推，
   过滤后的 G-cal 需要 ≈265 条（V1）到 ≈375 条（V3）episode 才能凑够 90 条存活，比设计的 240 更多。
3. **commentary 通道的 μ/σ 只有 3 条 episode / 12 个窗口，而且这 3 条全部落在 fit 半是 seed 的巧合**——
   换一个 seed 就可能出现"cal 半有 commentary、fit 半没有"，此时 `ChannelStandardiser.standardize` 会
   `KeyError`（现在会抛错，不会静默）；G 预注册需要规定 commentary 桶的最小支撑或规定 V1 在无 commentary 时退化为 V2。
4. **M 的层带必须在 G 预注册前冻结，且必须排除冻结层号的字面移植**——`frozen 5–15` 在 24 层上把留出 FAR 顶到
   名义值的 1.8–2.3 倍；`all24` 与按相对深度的 `mid24` 都接近名义值。
5. **B 在所有层带下都保守（0.0125–0.05 vs 名义 0.0988）**——预算没用满意味着召回被白白让掉，
   需要在攻击臂解封后用 matched measured-FAR 而不是名义 α 来比。
6. **逐层 top-1 在 ~0.3% 的 (层, token) 位置是 bf16 精确并列**——B 统计量条件在 top-1 上，
   换任何影响 router 数值的依赖版本都可能翻转这些位置，建议把 `torch` / kernel 版本写进数据卡。
7. **G-bridge 没有质量标注，`filtered` 分母退化为 `all`**——设计 §7 的"过滤后 / 全部"两分母在本轮不可分辨，
   `filter_status = "unlabelled"`；正常臂过滤率还受 `g_bridge_run_log.md` 问题 1（U+2011）影响，须先归一化重算。
8. **V1 与 V2 在 G-bridge 上不可分辨**（cal 半 commentary token = 0，两者端点逐位相同）——
   "去掉工具调用 JSON"这条对照只能在 Agent v3 的模型驱动集上做。
9. **`with_token_axis` 的多步路径只有合成测试覆盖**——G-bridge 240/240 都是单步，
   多 agent step 的 v2.5 布局（若存在）需要真实样本再测一次。
10. **本轮只跑了 `tag_scope=message`**——`body` 口径（`detector_harness_g.md` D14，在 P0 上把 H 从 111 压到 86）
    在 G-bridge 上未测；修好 G3 之后 `body` 口径才第一次有意义（旧 fallback 下它是错的）。
