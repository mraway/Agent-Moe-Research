# 换模型试跑：router 适配层与加载路径（工程说明，2026-09-06）

给两个 pilot agent（Qwen3-30B-A3B / gpt-oss-20b）看的工程说明。只讲“怎么接、有什么坑”，不含任何检测指标。
环境：transformers 5.16.1，torch 2.13.0+cu130，triton 3.7.1，bitsandbytes 0.50.2，kernels 0.16.1，RTX 5090（sm 12.0，32 GB）。

## 1. Router 适配层（`src/routing/capture.py`）

```python
class RouterAdapter:
    name: str                      # 注册名
    model_types: tuple[str, ...]   # 自动匹配的 config.model_type
    capture_module: str            # 实际挂 hook 的子模块（文档用）

    def attach_points(model) -> list[tuple[int, nn.Module]]   # (decoder 层号, 被 hook 的模块)
    def router_of(module) -> nn.Module                        # 带 num_experts/top_k/weight 的门
    def weight_semantics(router) -> str                       # top-k 权重语义
    def parse(*, layer_index, module, inputs, output, expected_rows) -> RouterLayerCapture
    def describe(model, points) -> dict                       # 写进 trace 的 router 元数据
```

`RouterLayerCapture` = `logits [T, E]`（全专家、softmax 之前）、`top_k_weights [T, k]`、`top_k_ids [T, k]`。
注册表：`register_router_adapter / available_router_adapters / resolve_router_adapter / describe_model_routers`。
`RouterTraceRecorder(model, router_adapter=None)`：`None` 时按 `config.model_type` 自动选，认不出就退回 `olmoe`（与加适配层之前完全一致）。
`recorder.router_metadata` 与 `recorder.moe_layer_indices` 供上层写盘。

三个内置适配器：

| adapter | 挂载点 | E / k 来源 | logits 来源 | top-k 权重语义 |
|---|---|---|---|---|
| `olmoe`（默认） | `layers[i].mlp.gate`（要求每层都有，否则报错，消息与旧版逐字相同） | 门模块属性 | 门返回的第 0 项 | `norm_topk_prob=False` → 全专家 softmax 概率（OLMoE-1B-7B-0125 即此） |
| `qwen3_moe` | `layers[i].mlp.gate`，**稠密层自动跳过**（`mlp_only_layers` / `decoder_sparse_step`） | 门模块属性 | 门返回的第 0 项；若被隐藏则用 `F.linear(hook 输入, gate.weight)` 重算 | `norm_topk_prob=True` → 归一化后的 top-k 概率 |
| `gpt_oss` | `layers[i].mlp`（**不是** `mlp.router`，原因见 3.2） | `mlp.router` 属性 | 输出里形状为 `[T, E]` 的浮点张量（MXFP4 路径会返回）；否则用 `F.linear(hook 输入, router.weight, router.bias)` 重算 | 只对被选中的 k 个 logit 做 softmax（**不是**全专家概率） |

写进 `trace.json["router"]` 的元数据（不再假设 16/64/8）：
`router_adapter, model_type, num_experts, top_k, num_moe_layers, num_hidden_layers, moe_layer_indices,
capture_module, router_hidden_dim, router_has_bias, router_logits_semantics, top_k_weight_semantics, shared_expert_count`。

`src/routing/validate.py` 相应增量：按 `trace.json["router"]["top_k_weight_semantics"]` 反推期望的 top-k 权重
（缺省 = 旧行为 = 全专家 softmax 概率），所以旧 trace 校验结果一字不变，Qwen3 的归一化权重与 gpt-oss 的 top-k softmax 也能通过。

**没有共享专家、没有专家偏置路由**：Qwen3-MoE 和 gpt-oss 都是纯 top-k，`shared_expert_count = 0`，专家 id 空间不需要偏移。
gpt-oss 的 attention sink 在 `self_attn.sinks`，与路由无关，不进 trace。

## 2. 模型配置新增字段（`scripts/run_agent_v2.py`，全部可选，缺省行为不变）

```json
"quantization": {"method": "bnb_nf4", "double_quant": true, "modules_to_not_convert": ["lm_head", "mlp.gate", "embed_tokens"]}
"quantization": {"method": "mxfp4"}
"quantization": {"method": null}          // 或整块省略 → 与原来逐位相同
"chat_template_kwargs": {"reasoning_effort": "low"}
"generation_channels": true               // 或 {"final": "<|channel|>final", ...}
"router_adapter": "qwen3_moe"             // 省略则自动
"stop_token_ids": [200002]                // 可选补充停止符
```

- `bnb_nf4` → `BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=bfloat16)`，
  `modules_to_not_convert` 走 `llm_int8_skip_modules`。
- `mxfp4` → `Mxfp4Config(dequantize=False)`。
- `generation_channels` 记录每个 agent step 里各 harmony 通道标记**完成的那个生成 token 下标**
  （正文从下标+1 开始，`-1` 表示没出现），写入 `trace.json["generation_channels"]` 和该步的 `model_generation` 事件；
  **所有生成 token（含 analysis 通道）照常记录路由**，解码过程完全不变。

## 3. 已确认的 transformers 实现限制

### 3.1 Qwen3-MoE 的专家权重不会被 bitsandbytes 量化（阻塞项）
`Qwen3MoeExperts` 把 `gate_up_proj / down_proj` 存成 3 维 `nn.Parameter`，而 `replace_with_bnb_linear` 只替换
`nn.Linear` / `Conv1D`。在 transformers 5.16.1 上，NF4 只会量化 attention 的 q/k/v/o 与 `lm_head`，
约 28 B 的专家参数仍是 bf16（≈57 GB）→ 32 GB 显存放不下。已在小模型上确认：整个 Qwen3MoE 里的
`nn.Linear` 只有 `self_attn.{q,k,v,o}_proj` 和 `lm_head`。
Pilot agent 先做一次**空载试装**（`device_map="auto"` + `max_memory` 限制）确认，再决定：
预量化 4-bit checkpoint（注意 5.x 的 3 维融合权重与旧式 per-expert 命名的转换风险）、换更小的 MoE、或直接报告不可行。
不要用 CPU/磁盘 offload 硬跑（本机只有 23 GB 内存）。

### 3.2 gpt-oss 的 MXFP4 会替换 `GptOssMLP.forward`，router 子模块不再被调用
`transformers/integrations/mxfp4.py::mlp_forward` 直接内联
`F.linear(hidden, self.router.weight, self.router.bias)`，从不调用 `self.router.forward`。
因此挂在 `mlp.router` 上的 forward hook 在 MXFP4 下**永远不会触发**。
适配器改挂 `layers[i].mlp`：MXFP4 路径的返回值第二项就是完整 `[T, E]` logits；
eager 路径则用 router 的权重/偏置对 hook 输入重算（与两条 forward 的算式逐字一致，已在测试中与
`model(..., output_router_logits=True)` 和 `mlp.router(h)` 的直接调用对齐）。

### 3.3 gpt-oss 不支持 sdpa
`GptOssPreTrainedModel._supports_sdpa = False`（attention sink），`attn_implementation="sdpa"` 在加载时直接抛
`ValueError`。配置里用 `eager`；flash_attention_3 走 kernels hub，未安装，本轮不装。

### 3.4 融合 MoE kernel 会绕过 router
`GptOssMLP` 带 `@use_kernel_forward_from_hub("MegaBlocksMoeMLP")`。`from_pretrained` 默认 `use_kernels=False`，
所以默认安全；一旦 `use_kernels=True`，融合 kernel 会整体替换 forward。适配器在 `attach_points` 里直接拒绝
（`use_kernels=False` 的报错），别在 pilot 里打开它。另外 MXFP4 量化器在 CUDA 上遇到 `use_kernels=True` 会
**回退到 dequantize**（bf16 展开，约 40 GB），同样要避免。

### 3.5 MXFP4 的回退条件
需要 `kernels`（已装 0.16.1）、`triton >= 3.4`（已装 3.7.1）、compute capability >= 7.5（5090 是 12.0）。
任一不满足时量化器会 `warning_once` 并把 `dequantize` 置为 True 而**不报错**——加载日志里看到该警告就应中止，
不要让 bf16 专家把显存占满。

### 3.6 停止符
`generate_routed_turn` 只按 `tokenizer.eos_token_id` 停。harmony 用 `<|return|>` 收尾、用 `<|end|>` 切通道，
若实测发现不停，用 `stop_token_ids` 补上（默认空列表 = 行为不变）。

## 4. 试跑配置

- 模型：`configs/pilot_qwen3_30b_a3b_nf4.json`、`configs/pilot_gpt_oss_20b_mxfp4.json`
  （`revision` 现为 `main`，**跑之前必须把解析出的 commit hash 写死进去**）。
- 批次：`scripts/research_v3/pilot_build_batch.py --model {qwen3_30b_a3b,gpt_oss_20b} --mode {batch,probe}`
  → `configs/pilot_batch_<model>.json`（4 scenario × 3 臂 = 12 条，384 token，seed 沿用）
  与 `configs/pilot_probe_<model>.json`（同 4 个 scenario × 2 个加强注入变体 = 8 条，**只跑 `--arms attack`**）。
- 选中的 4 个 scenario 与理由、2 个探针变体的措辞，见 `pilot_build_batch.py` 的模块 docstring 与配置里的
  `scenario_selection` / `probe_variants` 字段。
- 产物目录：`artifacts/agent_v2/pilot_qwen3_30b_a3b/`、`artifacts/agent_v2/pilot_gpt_oss_20b/`（探针加 `_probe`）。
- 加载模型的每条命令都要套 `flock`；一次只驻留一个模型。

## 5. 回归

`tests/test_pilot_capture_adapters.py` 里 `_reference_olmoe_capture` 是**加适配层之前那段 hook 的逐字副本**，
测试断言新旧两条路径在同一个合成门上产出 6 个张量完全相同（`torch.equal`，dtype 也比）。
另有 Qwen3-MoE / gpt-oss 小模型（CPU、transformers config 类构造）的形状、与模块自身 top-k 选择一致性、
MXFP4 forward 替换后仍能捕获、写盘后 `validate_trace` 通过等测试。全套 569 个测试通过。
