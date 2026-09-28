# P1 逐 token 路由采集报告

日期：2026-09-02
状态：**通过**
模型：`allenai/OLMoE-1B-7B-0125-Instruct`
固定 revision：`b89a7c4bc24fb9e55ce2543c9458ce0ca5c4650e`

## 验收结论

当前实现能够在不修改 Transformers 源码的情况下，对 OLMoE 的 16 个
`OlmoeTopKRouter` 注册 forward hook，并把 prefill 与逐 token decode 的路由结果对齐到
准确的 token ID、绝对 position、角色、conversation turn 和 agent step。

同一输入、同一模型和 greedy decoding 连续运行两次后：

- 生成 token 完全一致；
- 17 个 forward step 的结构完全一致（1 个 prefill + 16 个 decode）；
- top-k expert IDs 和 weights 完全一致；
- 所有 pre-softmax router logits 的最大绝对差为 0.0；
- 两份落盘 trace 均通过独立完整性验证。

因此逐 token 路由可复现性在当前单样本 smoke 条件下成立。扩大 prompt、任务和重启进程后的
重复性测试仍应在规模化数据采集前继续执行，不能把本次 smoke test 当作跨硬件结论。

## 实现

- `src/routing/capture.py`：hook 生命周期、forward 边界和 token 对齐；
- `src/routing/schema.py`：schema v3 的内存对象和形状校验；
- `src/routing/writer.py`：每个 forward 一个 safetensors 分片，并追加 JSONL manifest；
- `src/routing/validate.py`：独立检查 shard、manifest、top-k 和派生统计；
- `scripts/capture_olmoe_trace.py`：带 KV cache 的显式 greedy decode 与双次重复性测试；
- `scripts/validate_routing_trace.py`：落盘 trace 验证入口；
- `tests/test_routing_capture.py`：无需 GPU 的 hook/序列化单元测试。

## Schema v3

每个 trace 目录包含：

```text
trace.json                  模型、revision、解码配置、消息和完成状态
manifest.jsonl              每个 forward 的 token 对齐元数据和 tensor 路径
steps/000000_prefill.safetensors
steps/000001_decode.safetensors
...
```

每个 step shard 保存：

- `token_ids [token]`、`positions [token]`；
- `router_logits [layer, token, expert]`，bf16 pre-softmax 原始值；
- `top_k_ids [layer, token, k]`，int16；
- `top_k_weights [layer, token, k]`，bf16；
- `router_entropy [layer, token]`，fp32；
- `router_margin [layer, token]`，fp32 的 top-1/top-2 probability margin；
- `effective_experts [layer, token]`，fp32 的 `exp(entropy)`。

manifest 为每个 token 保存文本片段、role、conversation turn、agent step 和 tool-boundary
标志。prefill 的角色不是粗略标成一个 prompt：脚本通过逐消息扩展 chat template，并检查
每个中间 token 序列确实是最终 prompt 的前缀，再赋予 token-level 角色。本次 48-token
prefill 中有 20 个 system、22 个 user、6 个 assistant generation-prefix token。

显式 decode 在选出每个生成 token 后，将该 token 连同 KV cache 再输入模型，从而捕获它
自身的路由。为记录最后一个输出 token，脚本会做一次不再用于选取输出的末尾 forward；
该 forward 不改变已生成文本，但计入采集成本。

## 实测结果

最终验收目录：`artifacts/routing_smoke/20260902T104200Z`（本地 artifact，不进入版本控制）。

- 模型加载：7.406 秒；
- 每次 trace：48 prompt tokens + 16 generated tokens；
- 第一次 trace（含 CUDA/路径 warm-up 和写盘）：2.163 秒；
- 第二次 trace：0.572 秒；
- 峰值 CUDA allocated：13244.05 MiB；
- 峰值 CUDA reserved：14750.0 MiB；
- 每份 trace：17 个 safetensors step shard；
- 每份完整 logits 的逻辑大小：131072 bytes；
- 每份 top-k IDs + weights 的逻辑大小：32768 bytes，即完整 logits 的 25%；
- artifact 目录实际总大小（两份 trace + manifest/report）：约 448 KiB。

验证器从保存的 bf16 logits 在 CPU 上重新计算 softmax。由于 GPU/CPU softmax 与 bf16
weight 存储存在舍入差，最大 weight 误差为 0.001833，低于预设 0.003 容差。并列概率可能
导致 top-k 内部顺序不同，因此验证的是保存 expert 是否属于合法 top-k 集合，并按保存的
expert ID 检查对应 weight；双次真实 GPU trace 的原始 IDs/weights 仍是逐元素完全一致。

## 复现

采集两个重复 trace：

```bash
.venv/bin/python scripts/capture_olmoe_trace.py \
  --local-files-only \
  --max-new-tokens 16 \
  --repeats 2
```

验证单份 trace：

```bash
.venv/bin/python scripts/validate_routing_trace.py \
  artifacts/routing_smoke/<run-id>/repeat_00
```

运行 CPU 单元测试和依赖检查：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```
