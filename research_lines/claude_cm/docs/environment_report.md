# 本地实验环境报告

检查时间：2026-09-02
里程碑：P0 环境与可复现性
状态：**通过**

## 当前结论

WSL2 的内存配置和 CUDA 设备映射均正常，RTX 5090 已完成 PyTorch bf16 运算和固定
revision OLMoE 推理实测。P0 不再有环境阻塞，可以进入 P1 逐 token 路由采集。

### 宿主与 GPU

- WSL：2.7.12.0
- Kernel：6.18.33.2-microsoft-standard-WSL2
- WSL RAM：23.46 GiB
- WSL swap：16.0 GiB
- GPU：NVIDIA GeForce RTX 5090
- 显存：32607 MiB
- Windows NVIDIA driver：591.86
- PyTorch 检测到的 compute capability：12.0（sm_120）
- 工作区文件系统：Linux ext4，检查时约 934 GiB 可用

`.wslconfig` 中的 `memory=24GB` 和 `swap=16GB` 已在 `wsl --shutdown` 后生效。

### Python 环境

隔离环境位于项目根目录 `.venv`，主要版本为：

- Python 3.12.3
- PyTorch 2.13.0+cu130
- Transformers 5.16.1
- Accelerate 1.14.0
- NumPy 2.5.2

`pip check` 报告 `No broken requirements found`。直接依赖记录在 `requirements.txt`，
完整传递依赖快照记录在 `requirements-lock.txt`。PyTorch 官方的 CUDA 13 Linux wheel
包含 Blackwell/sm_120 支持；本项目首轮实验不需要系统级 `nvcc`。

## 固定模型

- 模型：`allenai/OLMoE-1B-7B-0125-Instruct`
- revision：`b89a7c4bc24fb9e55ce2543c9458ce0ca5c4650e`
- 权重格式：3 个 safetensors 分片
- 权重精确大小：13,838,721,960 bytes
- 本地缓存：`artifacts/hf_cache`（不进入版本控制）
- 模型结构：16 层、每层 64 个专家、每 token 选择 top-8

## 实测结果

完整报告由 `scripts/smoke_test_olmoe.py` 生成到
`artifacts/p0_olmoe_smoke.json`。本次结果：

- CUDA bf16 矩阵乘：通过
- 固定缓存的模型加载时间：7.387 秒
- 测试 prompt：48 tokens
- 生成：32 tokens
- 两次 greedy generation：token 完全一致
- router logits：16 个 tensor，全部为 `[48, 64]`
- 峰值 CUDA allocated：13709.25 MiB
- 峰值 CUDA reserved：14752.0 MiB
- 峰值进程 RSS：14049.8 MiB
- 两次生成耗时：0.930 秒、0.783 秒

模型生成内容符合客服场景，建议用户先登录账户并在订单列表中查询订单状态。

## 复现命令

首次创建环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
```

验证 WSL、CUDA 和 bf16：

```bash
.venv/bin/python scripts/check_environment.py
```

只下载并验证模型配置与 tokenizer：

```bash
.venv/bin/python scripts/smoke_test_olmoe.py \
  --metadata-only \
  --report artifacts/p0_metadata.json
```

首次下载权重并运行完整测试：

```bash
.venv/bin/python scripts/smoke_test_olmoe.py \
  --report artifacts/p0_olmoe_smoke.json
```

缓存存在后做严格离线复验：

```bash
.venv/bin/python scripts/smoke_test_olmoe.py \
  --local-files-only \
  --report artifacts/p0_olmoe_smoke.json
```

## 沙箱说明

Codex 普通受控命令会隐藏 `/dev/dxg`，此时 `nvidia-smi` 会显示
`GPU access blocked by the operating system`。这不是 WSL 或驱动故障。GPU 检查和推理需
获准在沙箱外执行；项目脚本、配置、报告和模型缓存仍全部位于工作区。

不要在 WSL 中安装 NVIDIA Linux display driver。WSL 使用 Windows 驱动映射的
`libcuda.so`。只有编译自定义 CUDA 扩展时才可能需要单独安装 toolkit；当前官方
PyTorch wheel 已自带运行时依赖。

## 下一步

进入 P1：为 16 个 `OlmoeTopKRouter` 注册 forward hook，分别捕获 prefill 与逐 token
decode 的 pre-softmax logits、top-k expert IDs 和 weights，并为 token 对齐与 greedy
重复性建立自动化测试。
