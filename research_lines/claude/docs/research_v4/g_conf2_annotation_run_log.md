# G-conf-2 文本标注运行日志（2026-09-08，进行中）

路由保持封存：本轮只读 `packets/g_conf2` 的盲态包与渲染批，不打开任何 trace 目录或张量。

## 1. 已完成

| 步骤 | 产物 | 状态 |
|---|---|---|
| Sonnet A 双盲标注（74 批 × 12 = 888 例，reviewer `sonnet-A-batch_NN`） | `artifacts/agent_v2/dataset_g/annotations/g_conf2/A/all.jsonl`（sha256 前 16 位 `e7c99c5b69d7e208`）、`A/aligned.jsonl`（`5dc95298b125656e`） | 888/888，校验 `valid: true`（无 `--partial`、无 `--mapping`） |
| Sonnet B 双盲标注（reviewer `sonnet-B-batch_NN`） | `B/all.jsonl`（`ab3461798854675d`）、`B/aligned.jsonl`（`713dd42cc7dd5d28`） | 888/888，校验 `valid: true` |
| 合并与一致性（Opus） | `disagreements.jsonl`（115 行，`17dca212bfd12e8d`）、`docs/research_v4/g_conf2_annotation_agreement.md`（已提交 6687a27） | 完成 |
| Opus 裁决 | `adjudication/chunk_9.jsonl`（7 行，`19e8e4b9c545b7b4`） | **10 块只完成 1 块**；块 0–8（108 例）未做 |
| 预复核共识（确定性脚本，替代原 LLM 步骤） | `pre_sweep_consensus.jsonl` / `pre_sweep_provenance.jsonl` / `pre_sweep_filter_pass.json` / `pre_sweep_filter_pass_lists.json` | 以 `--partial` 生成，108 例带 `disputed_without_adjudication` 旗标；**裁决完成后必须不带 `--partial` 重新生成** |

一致性要点（详见一致性报告）：轨迹类精确一致 97.97 %（κ 0.931）、E 存在 κ 0.995、X 存在 κ 1.000、onset ±5 内 96.75 %（精确一致只有 80.22 %）；
**质量轴不达 Opus 水平**：citation κ 0.824、material_errors 存在 κ 0.597（52 次翻转、方向对称）；裁决队列 12.95 %（115/888），G-conf 为 4.50 %。

## 2. 阻塞原因

裁决块 0–8、共识、复核、定稿在 2026-09-08 两次因账户用量上限失败（先是周上限，续跑后是月支出上限；周上限 9 月 13 日 16:00 太平洋时间重置）。一字探针在两次之间短暂成功过，之后 Opus 与 Sonnet 子代理全部拒绝。主会话仍可运行。

## 3. 组长在续跑前做出的协议修正（先于 G-conf-2 任何路由或检测器输出）

1. **Opus 定向复核改为双向**：除 `filter_pass = true` 的行（查漏报）外，还复核**仅因 `material_errors` 非空**而被排除出过滤池的行（查误报）。理由：全量 Sonnet 对在这两轴的 κ 远低于 60 例试点，且翻转对称。这是标签生产决策，写入定稿报告与等价性声明。
2. **共识步骤改为确定性脚本** `scripts/research_v4/annotation_consensus.py`（规则与 G-dev/G-conf 相同：分歧且有裁决行取裁决行，否则取 A；`filter_pass` 用 harness 的 `research_v2.io_g._filter_pass`）。回归：在 G-conf 上重建的预裁定共识与冻结的 `final.jsonl` 在全部 878 条非组长裁定行上逐字节相同，来源列与 `final_provenance.jsonl` 零不一致。单元测试 `tests/test_research_v4_annotation_consensus.py`（5 项）。
3. 定稿报告须记录：onset 精确一致 80.22 % 对 ±5 内 96.75 %；X 偏差 257 token 的 `g-edc7a76000dd` 及其裁决；两位标注者均未使用 `legitimate_refusal`；`onset_interval` 只记录 1/2 行（可选字段，仅数据门信息列使用，harness 不消费）。

## 4. 续跑步骤（用量恢复后）

1. 续跑工作流（裁决块 0–9 重跑；已成功的标注与合并 agent 走缓存）：
   `Workflow({scriptPath: ".../workflows/scripts/g-conf2-attack-annotation-wf_03b9a5f5-1c1.js", resumeFromRunId: "wf_03b9a5f5-1c1"})`
   若只想先跑裁决，可在裁决阶段之后中止。
2. 裁决齐全后：
   ```bash
   PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v4/annotation_consensus.py --packet artifacts/agent_v2/dataset_g/packets/g_conf2/packet.jsonl --annotations artifacts/agent_v2/dataset_g/annotations/g_conf2
   PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v4/annotation_consensus_args.py artifacts/agent_v2/dataset_g/annotations/g_conf2 > <scratch>/consensus_args.json
   ```
   然后带 `args`（该 JSON）续跑同一工作流：共识步骤被跳过，直接进入双向复核与定稿。
3. 定稿后：提交标注文档；标签冻结 B″（`docs/research_v4/label_freeze_b3.md`，规则见 `freeze_a3_checklist.md` §4.2）；数据门；阶段 1a/1b；阶段 2a/2b/2c；确认报告。
