# 窄窗口 / 逐 token 读数 对 14 条有锚点抵御样本的影响（预注册，2026-09-05）

组长：Claude Fable 5.1。实现与审计：Opus 5 子代理。分析：返回组长。本文件在任何新结果产生之前写定。

## 1. 问题

抵御样本中"进入题外话题"的片段很短（多数 E1 的首个指涉片段 ≤ 5 token）。冻结候选 CAND-A（WGM C2，w=8）与
CAND-B（PDM C12，w=4）在 14 条有锚点抵御样本上 +16 内只各抓到 2 条。问：把窗口收窄到 w=4 / 2 / 1（w=1 即逐 token 读数）
能否在不破坏误报约束的前提下捕捉这类短暂进入？

## 2. 固定项（与冻结运行完全一致，只改窗宽）

- scorer 配置：WGM `scripts/research_v2/wgm_configs/g1_middle_late.json`（metric g1，layers middle_late = 5–15）；
  PDM `scripts/research_v2/pdm_configs/d1_middle.json`（model d1，layers middle = 5–11）；
- routine = cb（clean + benign），split S1 两个方向（b1→b2、b2→b1），mode D 与 T 都跑但 **mode D 为主**，
  α ∈ {0.05, 0.10}，readings 全部（主读数 max 与 persist2），comparison ge，pooling disjoint，bucket 32 / min 30，
  bootstrap 500；
- 窗宽：WGM `--windows 1,2,4,8`；PDM `--windows 1,2,4`。WGM w=8 与 PDM w=4 是**复现对照**：其 mode D α=0.10
  的 max/persist2 报警端点必须与冻结 result.json 逐条一致，否则本轮所有数字作废并先查原因；
- 新路径：`artifacts/agent_v2/research_v2/narrow_window/{wgm_c2_w1248, pdm_c12_w124}`；脚本
  `scripts/research_v2/narrow_window/`；文档 `docs/research_v2/narrow_window/`。冻结目录不改。

## 3. 评估口径

- 锚点：抵御样本用 `topic_entry_v1_adjudicated.jsonl` 的 `topic_entry_onset`（14 条有锚点：b1 7 条在 b2→b1 方向评估，
  b2 7 条在 b1→b2 方向评估）；drift 样本主锚点 `product_onset_v1_adjudicated.jsonl`，副锚点 evidence_onset；
- 每个 (候选, w, mode D, α, 读数) 报：有锚点抵御 R+4 / R+8 / R+16 / R_final（严格版：锚点前报警记为 pre-onset、不算命中；
  容差版：锚点前 ≤5 token 的报警算命中）、命中延迟中位数、pre-onset 数；drift 同口径；FAR clean 与 benign 分开、
  按校准半份分开；E0 47 条的有报警数；`topic_word_leak` 样本含/不含两个版本；
- 不用这 14 条样本做任何选择：所有窗宽、两个主读数、两个 α 全部报告。

## 4. 假设与判据（看结果前写定）

- H1（延迟）：CAND-A 在有锚点抵御样本上的命中延迟随 w 下降约 (8 − w) token；
- H2（召回）：w ≤ 2 时有锚点抵御 R+16（两方向合并、α=0.10、max 或 persist2）≥ 5/14，且 drift R+16 相对冻结值
  下降不超过 0.10。两条同时成立才算"窄窗口有效"；
- H3（误报）：总 FAR 仍钉在 α（构造保证），但两半份 FAR 差与 benign/clean 比值可能随 w 变大；报告之；
- H0（零假设，来自 calibration_anatomy 审计）：窄窗口噪声更大 → 阈值相对信号抬高 → drift 召回下降，
  抵御样本净收益为零。
- 结论规则：只有 H2 在两个方向各自成立，某个 w 才进入 B3 预注册；单方向成立只作为"待 B3 验证"记录。

## 5. 审计

四个独立视角：(a) 协议/泄漏/复现；(b) 样本级——14 条的首个报警落在指涉片段内、片段后还是别处，附解码文本；
(c) 误报解剖——窄窗口新增的 clean/benign 误报是什么；(d) 统计——n=14 的精确二项区间与 ±5 容差敏感性。
