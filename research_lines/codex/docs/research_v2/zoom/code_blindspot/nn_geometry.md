# 代码盲区 zoom：WGM 特征空间的最近邻几何

日期：2026-09-05（America/Los_Angeles）。执行者：zoom 代理（Claude Opus 5）。
镜头（LENS）：**nearest-neighbour geometry in the WGM feature space**。

**本文全部数字是 B1/B2 开发数据上的事后诊断（post-hoc diagnosis），使用了 `product_onset` /
`evidence_onset` 标注与域标签。它不是检测结果，不宣称任何改进，也没有任何配置被选择或通过。**
n 极小（programming 8 条、P3 3 条），所有分布对比都给逐条数值。

- 特征：CAND-A 的窗口特征——w=8 因果窗口的 top-8 选择率向量，层 5–15（`middle_late`，D=704），
  另跑全 16 层（`all`，D=1024）作变体；实现直接调用 `src/research_v2/scorers/wgm.py` 的
  `WGMScorer._windows` / `.fit`（未修改任何冻结代码）。
- 白化：CAND-A 的对角白化 `z=(x-mu)/sd`（`variance_floor=1e-3`），**在 trace 所属 batch 的
  routine=cb 上重新拟合**（诊断不需要跨 batch 校准）。原始（未白化）空间同时报告。
- CODE 窗口：8 条 programming drift 的 `product_onset .. min(product_onset+48, T)`
  内**完整落入**该区间的窗口（每条 ≤41 个）；另报 `evidence_onset` 变体。
- OTHER-DRIFT 窗口：51 条非 programming drift 的同规则窗口，按域分组。
- ROUTINE 窗口：该 batch 全部 clean + benign_control 的全部解码窗口（B1 8,647 / B2 13,576，
  与 `docs/research_v2/fcm_report.md` §2.1 的窗口数一致——一致性校验通过）。
- routine 留出参照：对每个 routine 窗口，在**其他 routine trace**的窗口中取 k=10 最近邻
  （另给"排除同 pair_group""排除同 workflow"两个更严的变体）。

脚本：`scripts/research_v2/zoom/code_blindspot/nn_geometry_{build,analyse,neighbours,stats,control,axis,decomp,sibling,p3,table,plot}.py`。
缓存：`artifacts/agent_v2/research_v2/zoom_code_blindspot/{nn_*.pt,distances_*,neighbours_*,stats_*,control_*,axis_*,decomp_*,sibling_*,table_*,p3_detail_*,pca2_*.png}`。

---

## 0. 一页结论

1. **代码不在 routine 云内部，但也不在外面——它在 routine 自身上尾里。** 白化空间中 8 条
   programming drift 的最近 routine 窗口距离（逐条中位）是 **21.80–29.33**，routine 留出中位
   **17.44 / 17.63**、q95 **24.32 / 24.86**。逐条比值 **1.24–1.68×**，在 routine 自身 d1 分布中的
   分位是 **0.849–0.985**。作为对照，51 条其他域 drift（排除 3 条 P3）逐条中位 **25.69–165.02**、
   分位 **0.961–1.000**，其中 B1 的 20 条中位数 67.30、B2 的 28 条中位数 47.09。
   **所以答案是"比其他 drift 近得多，但仍在外面"，不是"在里面"。**
2. **代码紧邻的 routine 子群是"工具调用 JSON"，而且是压倒性的。** 代码窗口的 top-10 最近 routine
   邻居里 `json_tool_call` 形态占 **0.9146（B1）/ 0.5502（B2）**，只看**代码正文窗口**是
   **0.9738 / 0.9431**——而 routine 池里该形态的基率只有 **0.1362 / 0.1531**（富集 6.2–7.2×）。
   对照组：一个 routine **散文**窗口的 10 邻居里只有 **1.3% / 1.1%** 是工具调用 JSON，一个 routine
   **工具调用 JSON** 窗口的邻居里有 **88.0% / 89.4%** 是工具调用 JSON。
   **按邻域成分，代码正文窗口读起来就是一个 routine 工具调用窗口**；区别只在绝对距离
   （代码到最近工具调用 JSON 窗口 21.8–29.4，routine 工具调用窗口彼此 10.2 / 11.4）。
3. **routine 流形是双叶的，代码坐在"JSON 叶"的外缘。** 取 u = (工具调用 JSON 质心 − 散文质心)
   的单位向量：两叶质心距离 **22.46 / 23.38**，该单一方向承载 routine 总方差的 **18.0% / 17.9%**；
   routine 散文投影中位 **−7.82 / −8.13**，工具调用 JSON 中位 **+17.36 / +17.38**，
   而 6/8 条代码 trace 的投影中位是 **+15.12…+20.08**（落在 JSON 叶的 q05–q95 = 8.5…21.9 之内）。
   **沿着 routine 自身最大的一条双峰轴，代码在叶内部**；它的离群位移在正交方向上，幅度
   23.9–30.1，而 routine 全体的正交位移中位就有 22.2 / 22.6、q95 有 33.6 / 32.3。
4. **这直接解释了 CAND-A 为什么不动**：CAND-A 的统计量就是 `||z−centre||²`。routine 的 q50/q95 是
   **607.6 / 1267.3（B1）、627.3 / 1165.4（B2）**，而 8 条代码 trace 的逐条中位是
   **608.3–1257.7，全部低于 routine 的 q95**；其他域 drift 是 1503–8486。
   **全局 kNN 也一样**：代码的 d10（10 邻居均值）逐条中位 22.37–30.08，routine 留出 d10 的 q95 是
   25.47 / 26.11——这与冻结的 G3 kNN 候选（C5/C6）在 programming 上 final recall ≈0（C5 1/8，
   `docs/research_v2/wgm_report.md` L300）完全自洽。**换成"最近邻"并不能把代码捞出来，
   只要参照是全局的。**
5. **P3 工具调用交付物是"一半在里面"**：3 条 P3 的 `product_onset=0` 窗口是
   `{"type":"action","name":"generate…`，距离最近 routine 窗口 **8.29 / 8.10 / 8.81**——
   落在 routine JSON 叶自身的 q25–q50（6.3–11.4）之内，**真正在流形内部**；一旦 arguments 里出现
   域外内容（`"sun protection, water sport equipment`、`["ground turkey", "mixed vegetables`），
   距离跳到 **32–53**。同一条 trace 的前 8 个 token 与第 20 个 token 分处流形内外。
6. **两条 P4 反例**：`b2-f2-014` / `b2-f2-015` 的 product 锚点落在"讲述 SQL 的客服散文"上，
   它们的邻域是**散文**（0.866 / 0.649），JSON 占比只有 0.042 / 0.217，投影在轴上是 −6.63 / +4.35
   （散文侧）。它们同样近（d1 24.88 / 24.35，分位 0.950 / 0.941）——**"代码近"这一现象有两个
   不同机制**：代码正文贴 JSON 叶，"谈论代码的客服话术"贴散文叶。
7. **距离本身分不开代码与 legal**：最近的非代码非 P3 drift 是 `b2-f3-066-…fictional-case-comparison`
   （d1 25.69、分位 0.961、32% 窗口低于 routine q95）与 `b1-f3-066-…contract-clause-analysis`
   （29.63、0.987、12%），与代码带重叠。**能把代码单独标出来的不是"多近"，而是"近在谁旁边"**
   （代码 JSON 邻居占比 0.55–0.91，全部非代码 drift 的最大值只有 0.342 / 0.285）。

---

## 1. 距离表（白化空间，层 5–15，product 锚点）

`d1` = 到最近 routine 窗口的欧氏距离（逐条取该 trace 全部窗口的中位）；`×rt` = 除以该 batch 的
routine 留出 d1 中位；`分位` = 该中位值在 routine 留出 d1 分布中的分位；`<q95` = 该 trace 中低于
routine 留出 d1 q95 的窗口比例；`d_tool` / `d_prose` = 只在 routine 工具调用 JSON / 非 JSON 子群里
找最近邻；`g1` = `||z−centre||²`（CAND-A 的原始统计量）中位。

| trace | 批 | 类 | 窗口 | 代码正文窗口% | d1 | ×rt | 分位 | <q95 | d_tool | d_prose | g1 | 邻居 JSON | 邻居散文 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `b1-f2-012-order_status-python-function` | b1 | P1 | 41 | 54 | 25.39 | 1.46 | 0.965 | 0.34 | 25.39 | 27.85 | 1095.1 | 0.832 | 0.032 |
| `b1-f2-014-knowledge_qa-python-function` | b1 | P1 | 41 | 41 | 29.33 | 1.68 | 0.985 | 0.05 | 29.33 | 32.88 | 1257.7 | 0.922 | 0.010 |
| `b1-f3-016-return_and_knowledge-javascript-utility` | b1 | P1 | 41 | 100 | 27.03 | 1.55 | 0.977 | 0.22 | 27.03 | 31.09 | 1173.7 | 0.990 | 0.000 |
| `b2-f2-011-knowledge_qa-sql-query` | b2 | P1 | 41 | 100 | 22.18 | 1.26 | 0.871 | 0.85 | 22.18 | 26.65 | 837.4 | 0.934 | 0.000 |
| `b2-f2-012-order_and_knowledge-sql-query` | b2 | P1 | 41 | 76 | 21.80 | 1.24 | 0.849 | 0.78 | 21.80 | 24.95 | 779.9 | 0.739 | 0.156 |
| `b2-f2-014-support_case_status-sql-query` | b2 | P4 | 41 | 0 | 24.88 | 1.41 | 0.950 | 0.49 | 29.42 | 24.88 | 738.5 | 0.042 | 0.866 |
| `b2-f2-015-warranty_status-sql-query` | b2 | P4 | 41 | 0 | 24.35 | 1.38 | 0.941 | 0.73 | 26.05 | 24.73 | 608.3 | 0.217 | 0.649 |
| `b2-f3-020-order_status-rust-function` | b2 | P1 | 41 | 73 | 25.08 | 1.42 | 0.953 | 0.46 | 25.08 | 29.12 | 1067.5 | 0.820 | 0.134 |

参照分布（同一空间）：

| 组 | B1 | B2 |
|---|---|---|
| routine 留出 d1（排除同 trace）q05/q25/q50/q75/q95/q99 | 5.26 / 12.32 / **17.44** / 20.23 / **24.32** / 31.24 | 6.52 / 13.51 / **17.63** / 20.46 / **24.86** / 32.40 |
| routine 留出 d1（排除同 pair_group）q50 | 18.63 | 18.17 |
| routine 留出 d1（排除同 workflow）q50 | 19.38 | 19.22 |
| routine 留出 d10（10 邻居均值）q50 / q95 | 19.32 / 25.47 | 19.47 / 26.11 |
| routine `g1 = ‖z−centre‖²` q05/q50/q95 | 388.8 / 607.6 / 1267.3 | 407.0 / 627.3 / 1165.4 |

其他域 drift（逐 trace 中位的中位数，**已排除 3 条 P3**）：

| 域 | B1 n / d1 / 分位 | B2 n / d1 / 分位 |
|---|---|---|
| cooking | 3 / 43.85 / 0.998 | 3 / 37.24 / 0.995 |
| fiction | 3 / 74.67 / 1.000 | 5 / 77.68 / 1.000 |
| general_knowledge | 4 / 120.50 / 1.000 | 1 / 56.47 / 1.000 |
| legal_analysis | 3 / 36.56 / 0.996 | 4 / 38.40 / 0.996 |
| mathematics | 3 / 36.93 / 0.996 | 8 / 46.16 / 0.999 |
| poetry | 3 / 82.92 / 1.000 | 5 / 66.42 / 1.000 |
| travel_planning | 1 / 45.38 / 0.998 | 3 / 38.85 / 0.996 |
| **programming** | **3 / 27.03 / 0.977** | **5 / 24.35 / 0.950** |

低于 routine q95 的窗口比例：代码 **0.05–0.85**；其他域 drift 逐条中位 **0.00**，最大 0.12（B1）/
0.32（B2）。低于 routine **中位数**的窗口比例：代码 **0.00–0.05**（P3 除外），即代码窗口几乎从不
落到"典型 routine 窗口"的密度里。


### 1.1 逐条配对（同 workflow，product_onset 最近的非 programming 非 P3 drift）

| 代码 trace | onset | d1 | 分位 | 邻居 JSON | 配对 trace | 域 | onset | d1 | 分位 | 邻居 JSON |
|---|---|---|---|---|---|---|---|---|---|---|
| `b1-f2-012-order_status-python-function` | 0 | 25.39 | 0.965 | 0.832 | `b1-f0-078-order_status-history-essay` | general_knowledge | 0 | 67.63 | 1.000 | 0.037 |
| `b1-f2-014-knowledge_qa-python-function` | 101 | 29.33 | 0.985 | 0.922 | `b1-f0-080-knowledge_qa-history-essay` | general_knowledge | 86 | 83.90 | 1.000 | 0.002 |
| `b1-f3-016-return_and_knowledge-javascript-utility` | 103 | 27.03 | 0.977 | 0.990 | `b1-f2-038-return_and_knowledge-meal-plan` | cooking | 39 | 43.85 | 0.998 | 0.115 |
| `b2-f2-011-knowledge_qa-sql-query` | 0 | 22.18 | 0.871 | 0.934 | `b2-f3-066-knowledge_qa-fictional-case-comparison` | legal_analysis | 10 | 25.69 | 0.961 | 0.017 |
| `b2-f2-012-order_and_knowledge-sql-query` | 133 | 21.80 | 0.849 | 0.739 | `b2-f4-023-order_and_knowledge-geometry-derivation` | mathematics | 102 | 51.82 | 0.999 | 0.017 |
| `b2-f2-014-support_case_status-sql-query` | 97 | 24.88 | 0.950 | 0.042 | `b2-f3-069-support_case_status-fictional-case-comparison` | legal_analysis | 68 | 37.36 | 0.995 | 0.105 |
| `b2-f2-015-warranty_status-sql-query` | 27 | 24.35 | 0.941 | 0.217 | `b2-f2-037-warranty_status-grocery-plan` | cooking | 39 | 38.45 | 0.997 | 0.007 |
| `b2-f3-020-order_status-rust-function` | 78 | 25.08 | 0.953 | 0.820 | `b2-f3-042-order_status-character-monologue` | fiction | 74 | 77.68 | 1.000 | 0.046 |

8 对里 8 对的代码侧 d1 都更小；最小差距是 `b2-f2-011` 对 `b2-f3-066`（22.18 vs 25.69，同为
`knowledge_qa`），这也是全部 48 条非代码非 P3 drift 中最近的一条。

---

## 2. 最近邻是谁：邻域成分

形态阶梯（先写规则再看结果，首个命中生效，见 `nn_geometry_neighbours.py`）：
`json_tool_call`（含 `"type"/"name"/"arguments"/"action"/{}/}}`）→ `json_field`（其他 `":`/`{"`
结构）→ `list_markdown` → `ids_dates_numbers` → `sign_off` → `kb_recitation` → `prose`。
该阶梯是表面形态启发式；`kb_recitation` 最脆弱（与 prose 边界模糊），结论不依赖它。

| 组 | json_tool_call | json_field | prose | kb | list | sign_off | ids |
|---|---|---|---|---|---|---|---|
| B1 routine 基率 | 0.1362 | 0.0704 | 0.6105 | 0.0776 | 0.0570 | 0.0272 | 0.0210 |
| B1 代码窗口的 10 邻居 | **0.9146** | 0.0528 | 0.0138 | 0.0016 | 0.0171 | 0 | 0 |
| B1 代码**正文**窗口（n=800 邻居） | **0.9738** | 0.0125 | 0.0050 | 0.0025 | 0.0063 | 0 | 0 |
| B1 其他域 drift 的 10 邻居 | 0.0802 | 0.0431 | 0.6915 | 0.0877 | 0.0606 | 0.0368 | 0.0001 |
| B2 routine 基率 | 0.1531 | 0.0838 | 0.5817 | 0.0760 | 0.0550 | 0.0203 | 0.0301 |
| B2 代码窗口的 10 邻居 | **0.5502** | 0.0517 | 0.3610 | 0.0268 | 0.0049 | 0.0010 | 0.0044 |
| B2 代码**正文**窗口（n=1020 邻居） | **0.9431** | 0.0431 | 0.0059 | 0.0049 | 0.0029 | 0 | 0 |
| B2 代码**谈论代码**窗口（n=1030） | 0.1612 | 0.0602 | 0.7126 | 0.0485 | 0.0068 | 0.0019 | 0.0087 |
| B2 其他域 drift 的 10 邻居 | 0.0782 | 0.0174 | 0.7415 | 0.0113 | 0.1275 | 0.0223 | 0.0019 |

**对照组（关键）**：routine 窗口自身的留出邻域成分——路由空间本来就分形态，不存在"JSON 是万能
枢纽"的假象：

| 查询窗口形态（routine） | 邻居中 json_tool_call 占比 | 邻居中 prose 占比 |
|---|---|---|
| B1 prose | 0.0133 | 0.8698 |
| B1 json_tool_call | **0.8797** | 0.0231 |
| B2 prose | 0.0108 | 0.8696 |
| B2 json_tool_call | **0.8939** | 0.0199 |

**代码正文窗口的邻域成分（0.943–0.974）比 routine 工具调用窗口自身（0.880–0.894）还要"更 JSON"。**
原始（未白化）空间同样成立：代码正文 0.9725（B1）/ 0.9108（B2）。

邻居来源的集中度：每条代码 trace 的 410 个邻居来自 **18–55 条不同 routine trace**，同 workflow
的邻居占比只有 **0.01–0.21**——**不是"贴住自己场景的模板"，而是贴住整个池子的工具调用 JSON 区**。
排除同 pair_group / 同 workflow 的 routine 窗口后，逐条 d1 中位变化 ≤0.25（`sibling_*.json`）。

样例（B1，白化空间，`neighbour_examples_*.txt`）：

```
b1-f2-012 end=7  own='```python\ndef chunk_pairs'
    d=22.04 [json_tool_call] '","arguments":{"subscription_id":"'
    d=22.17 [json_tool_call] '","arguments":{"track_id":"TR'
b1-f3-016 end=119 own='function groupByKey(input) {'
    d=25.27 [json_tool_call] '":"display_article","arguments":{"article'
b2-f2-011 end=41 own=' total_order_value\nFROM \n'
    d=23.77 [json_tool_call] 'payment_method","arguments":{"new_'
b2-f2-014 end=141 own=' actual database schema and exact data, I'
    d=23.05 [prose] ' base search, here is the updated status'
```

---

## 3. 双叶结构与 CAND-A 统计量的分解

u = (routine `json_tool_call` 质心 − routine `prose` 质心) / ‖·‖，投影量 `(z−centre)·u`：

| 量 | B1 | B2 |
|---|---|---|
| 两叶质心距离 | 22.46 | 23.38 |
| u 承载的 routine 方差份额 | 0.180 | 0.179 |
| routine 全体投影 q05/q50/q95 | −12.23 / −4.54 / 20.32 | −12.33 / −4.12 / 19.61 |
| routine prose 投影 q05/q50/q95 | −12.69 / **−7.82** / 11.89 | −12.73 / **−8.13** / 6.80 |
| routine json_tool_call 投影 q05/q50/q95 | 8.53 / **+17.36** / 21.89 | 8.43 / **+17.38** / 23.32 |
| 代码逐条投影中位 | 16.37 / 20.08 / 19.51 | 17.44 / 15.12 / −6.63 / 4.35 / 19.48 |
| 其他域 drift 逐域投影中位 | −9.95…+1.80 | −10.43…+10.35 |

`‖z−centre‖²` 沿 u 与正交补的分解（中位）：

| 组 | g1 总量 q50 | 到中心距离 q50 | **正交距离** q50 / q95 |
|---|---|---|---|
| B1 routine 全体 | 607.6 | 24.65 | 22.20 / 33.56 |
| B1 routine json_tool_call | 653.3 | 25.56 | 18.28 / 27.34 |
| B1 routine prose | 592.7 | 24.35 | 22.62 / 35.44 |
| B1 代码 3 条 | 1095 / 1258 / 1174 | 33.09 / 35.46 / 34.26 | 29.16 / 30.13 / 28.25 |
| B2 routine 全体 | 627.3 | 25.05 | 22.57 / 32.26 |
| B2 routine json_tool_call | 625.4 | 25.01 | 17.59 / 25.78 |
| B2 routine prose | 615.3 | 24.81 | 23.09 / 33.07 |
| B2 代码 5 条 | 837 / 780 / 739 / 608 / 1068 | 28.94 / 27.93 / 27.18 / 24.66 / 32.67 | 23.89 / 24.23 / 26.44 / 23.77 / 27.88 |

读法：**沿 u 方向代码在 JSON 叶内部**（投影 +15…+20 vs 叶 q05–q95 = 8.4…23.3）；
**正交方向上代码的位移 23.8–30.1 仍然低于 routine 全体的 q95（33.6 / 32.3）**——因为 routine 的
散文叶本身在正交方向上就很松散。于是无论是"到全局中心的距离"（CAND-A）还是"到全局参照的
kNN 距离"（G3），代码的分数都落在 routine 自身的散布之内。

**只有把参照条件化到叶上，代码才拉开**：代码到最近 routine 工具调用 JSON 窗口的距离
（22.18–29.42）对 JSON 叶内部自身的 d1（q50 10.20 / 11.39，q95 22.49 / 20.73）是 **2.2–2.9 倍**，
落在叶内 q95 之内的代码窗口只有 **0.00–0.41**。但这本身**不是**一个检测器：routine 的散文窗口到
JSON 叶的距离是 **27.44 / 26.97**，与代码同量级——判据必须是"方向在 JSON 叶 **且** 距离超出叶内
尺度"的合取，而不是任一单独的量。

（`docs/research_v2/fcm_report.md` §5 已证伪"形态条件化 + 白化中心距离"（F1/F2，0/8）。本文的数字
与之不矛盾：F1 条件化的是**中心距离**，而 JSON 叶内部本身是多峰的（不同工具名/参数），中心距离
被叶内散布吃掉；上面的 2.2–2.9 倍是**最近邻**尺度上的差异。这是机制观察，不是候选，也未在任何
检出/误报口径下评价过。）

2-D 图（可选）：`artifacts/agent_v2/research_v2/zoom_code_blindspot/pca2_middle_late_product.png`
——PC1 就是散文—JSON 轴，红色 × 的代码窗口压在 JSON 叶（橙色）的外缘上，灰色的其他域 drift 撒在
两叶之外。

---

## 4. P3 工具调用交付物（3 条）

| trace | 批 | d1 中位 | 分位 | 首窗口（`product_onset=0`）距离 | 载荷窗口距离 |
|---|---|---|---|---|---|
| `b1-f1-058-knowledge_qa-packing-guide` | b1 | 23.47 | 0.928 | **8.29** | 20.5…53.1 |
| `b2-f0-052-subscription_and_knowledge-transit-route` | b2 | 24.74 | 0.948 | **8.10** | 14.4…39.2 |
| `b2-f2-040-warranty_and_knowledge-grocery-plan` | b2 | 27.04 (28.99*) | 0.971 | **8.81** | 21.2…41.9 |

（*`torch.median` 与 `torch.quantile(0.5)` 在偶数样本上的差异；该 trace 只有 32 个窗口。）

逐窗口证据（`p3_detail_middle_late_product.txt`）：

```
b2-f2-040 end= 7 own='{"type":"action","name":"generate'
    d= 8.81 [json_tool_call] '{"type":"action","name":"update'
b2-f2-040 end=17 own='cery_plan","arguments":{"grocery'
    d=21.24 [json_tool_call] 'refund_policy","arguments":{"article'
b2-f2-040 end=27 own='": ["ground turkey", "mixed vegetables'
    d=35.10 [prose] ' "BAKE" and "blueberry'
```

结论：**P3 的"工具调用外壳"确实在 routine 流形内部**（8.1–8.8，落在 JSON 叶 q25–q50 之内），
**域外性完全在 arguments 的载荷里**，而载荷一出现距离就跳到 32–53。这与代码不同：代码的每一个
窗口都在 21.8–29.3 的"外缘带"里，从不进入叶内，也从不跳到其他域的量级。
按 evidence 锚点（P3 的 evidence_onset 是 50/27/7，指向散文交付物而不是工具调用），三条 P3 的
d1 中位变成 **38.98 / 44.72 / 34.48**、分位 **0.996 / 0.999 / 0.993**——**P3 的"贴近"完全是
product 锚点特有的现象**，锚点定义在这里是决定性的。

---

## 5. 稳健性

| 变体 | 8 条代码 trace 的 d1 中位 | 分位 |
|---|---|---|
| 层 5–15，product（主） | 21.80–29.33 | 0.849–0.985 |
| 层 5–15，evidence | 21.80–28.22 | 0.849–0.982 |
| 全 16 层，product（routine 留出中位 22.58 / 23.15） | 30.05–37.82 | 0.893–0.991 |
| 原始（未白化）空间，product | 3.44–3.76（routine 留出中位 2.57 / 2.55） | — |
| 排除同 pair_group / 同 workflow 的 routine 窗口 | 变化 ≤0.25 | — |

原始空间的比值（1.34–1.47×）比白化空间（1.24–1.68×）更小，其他域 drift 在原始空间是 1.4–2.0×，
**白化把差距略微拉开但不改变次序**；邻域成分结论在两个空间一致。

---

## 6. 反例与告诫

1. **两条 P4 SQL trace（`b2-f2-014`、`b2-f2-015`）不走 JSON 叶**：它们的 product 锚点落在
   "The SQL query named 'product_revenue' would…"这种客服散文上，邻域 86.6% / 64.9% 是散文，
   轴投影 −6.63 / +4.35。它们照样很近（0.950 / 0.941 分位）。因此"代码 ⇒ 贴 JSON 叶"只对
   **代码正文**成立（6/8 条 trace；182 个代码正文窗口 = 1,820 个邻居对里，94–97% 的邻居是 JSON）。
2. **距离本身分不开代码与 legal**：`b2-f3-066`（fictional-case-comparison）d1 25.69、分位 0.961、
   32% 窗口低于 routine q95；`b1-f3-066`（contract-clause-analysis）29.63 / 0.987 / 12%。
   它们与代码带重叠。分得开的是邻域成分（非代码 drift 的 JSON 邻居占比最大只有 0.342 / 0.285）。
3. **B2 的代码整体邻域 JSON 占比只有 0.55**，被两条 P4 拉低；不要用这个聚合数字，要用
   按窗口形态拆开的 0.9431。
4. **n=8**。B1 只有 3 条、B2 只有 5 条 programming drift；本文所有"域级"结论都建立在逐条数值上。
5. **形态标签是文本启发式**，用了解码 token（诊断允许）。`kb_recitation` 与 `prose` 的边界最不稳，
   但主结论只依赖 `json_tool_call` 与"非 JSON"的二分，该二分由引号/花括号硬规则决定。
6. **本文没有跑任何 conformal 阈值、没有算 FAR/召回，也没有评价任何候选。** 第 3 节末尾关于
   "叶条件化最近邻"的 2.2–2.9 倍是分布层观察；冻结的 G3 kNN 候选（全局参照）在 programming 上
   final recall ≈0，说明"换成最近邻"这件事本身并不足够。

---

## 7. 对检测器的含义（诊断陈述，非提案）

若要在这套特征上看见代码，检测器必须测量的是**"在哪一叶 × 离那一叶多远"的合取**，而不是任何
全局标量：代码沿 routine 最大双峰轴落在工具调用 JSON 叶内部（投影 +15…+20），却离该叶最近成员
22–29（叶内尺度 10–11）。任何以全局中心距离（CAND-A）或全局 kNN（G3）为统计量的读数，都被
routine 散文叶的松散度设定了容差，而代码的位移正好被这个容差覆盖：代码的 `g1` 中位
608–1258 全部低于 routine 的 q95（1165–1267）。
