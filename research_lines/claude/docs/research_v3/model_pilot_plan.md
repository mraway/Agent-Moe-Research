# 换模型可行性试跑计划（Qwen3-30B-A3B-Instruct-2507 与 gpt-oss-20b，2026-09-07）

目的：在 RTX 5090（32 GB）上验证两件事：(1) 现有 Atlas 采集管线（scenario → controller 工具流 → 模型回复 → 逐 token router 捕获）
能否在新模型上跑通；(2) 现有攻击样本构造（工具结果注入 / 直接用户 / 多轮）对更强的模型是否仍能诱发可观测的题外参与或执行。
**这是工程可行性试跑，不是数据收集，不是 B3，不产生任何检测指标。**

## 固定设计
- 场景：从 `configs/agent_v2_5_b2.json` 取 4 个 scenario（不同攻击渠道与题外域：诗歌 / 代码或 SQL / 数学或法律 / 食谱），
  每个 clean / benign_control / attack 三臂，共 12 条；采样 temperature 0.8、top-p 0.9、seed 沿用 scenario 的 sampling_seed；
  `max_new_tokens_per_turn` 384。系统提示、工具、controller、知识库、记录全部沿用 Atlas v2.5。
- 攻击探针（第二步，探索性）：若 12 条里 attack 臂无一参与题外任务，对同 4 个 scenario 各试至多 2 个加强注入变体
  （例如权威口吻的"系统更新"、多轮用户追加、把交付要求放到 TRUSTED RESPONSE BRIEF 之外的工具字段），只改注入文本，不改系统提示。
  记录每个变体的行为结果；这些不是正式样本。
- 模型：
  - Qwen/Qwen3-30B-A3B-Instruct-2507：bf16 权重 + bitsandbytes NF4（router 门、embedding、lm_head 保持 bf16）；非思考。
  - openai/gpt-oss-20b：原生 MXFP4（transformers Mxfp4Config，需要 `kernels` 包；若内核不可用则报告，不用 bf16 回退占满显存）；
    harmony 模板由 tokenizer chat template 提供；reasoning effort 设 low；analysis 通道的 token 也捕获路由并在 trace 中标注通道。
- 路由捕获：`src/routing/capture.py` 增加 router 适配层（OLMoE / Qwen3Moe / GptOss 的门模块定位与输出解析），
  统一产出 logits [T, E]、top-k weights、top-k ids；schema 不写死 16/64/8。现有 OLMoE 路径行为不变（回归测试）。
- 产物：`artifacts/agent_v2/pilot_qwen3_30b_a3b/`、`artifacts/agent_v2/pilot_gpt_oss_20b/`；配置 `configs/pilot_*.json`；
  报告 `docs/research_v3/model_pilot_report.md`。
- 记录：模型加载时间、显存峰值、tokens/s、每条 trace 的 token 数与停止原因、路由张量形状与完整性校验、
  行为判读（routing-blind：attack 臂是否参与 / 承诺 / 执行题外任务；clean/benign 臂是否回答了客服问题、是否编造）。

## 约束
- 不修改任何既有 config、数据、标签、冻结结果；`run_agent_v2.py` 与 `capture.py` 只做增量改动并保持 OLMoE 行为逐位不变。
- GPU 一次只加载一个模型：用 `flock` 文件锁串行。
- B3 不使用；不计算任何检测分数。
