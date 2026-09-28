# G-dev 原轨迹隐藏激活补采：工程冒烟计划

日期：2026-09-09。授权：组长本轮要求“拿几条 trace 验证一下重采”。
性质：工程可复现性验证，不是检测算法评价、机制检验或新的独立样本。

## 固定范围

只读以下五条已开放 G-dev 原轨迹，不依据检测分数或行为标签选样：

| 原 trace | 采集目录 | 覆盖 | 生成步骤 | 输出 token |
|---|---|---|---:|---:|
| g-dev-001--clean | core_72_cells | 常规任务 | 2 | 89 |
| g-dev-001--attack | core_72_cells | direct_user | 2 | 194 |
| g-dev-017--attack | core_72_cells | multi_turn_user，保留两个 episode | 3 | 216 |
| g-dev-097--attack | resume_3_3 | tool_output | 2 | 148 |
| g-dev-305--clean | resume_1_1 | legitimate_refusal，非普通 clean | 1 | 113 |

合计 5 traces、6 episodes、10 个生成步骤、760 个输出 token。
每条完整回放两次，在同一次模型驻留中执行。不打开标签；不访问 G-conf，
不调用 Agent/controller/工具，不调用 generate 或任何采样器，不修改场景工厂。

## 前向与采集

- 固定原 checkpoint `6cee5e81ee83917806bbde320786a8fb61efebee`，原生 MXFP4，
  bf16、eager attention、eval、batch=1；仅用本地模型与 kernel 缓存，不升级依赖。
- 直接使用原分片 token IDs，不重新渲染含日期的模板，不重新 tokenize 文本。
  每个原 prefill 都重新建立 KV cache；decode 每次输入原来的一个 token，
  原调用参数 `use_cache=True, logits_to_keep=1, return_dict=True` 保持不变。
  不把整条多步/多轮轨迹拼成一次前向；不以一次全序列计算替代原逐 token decode。
- 原共享 RouterTraceRecorder 原样复用；通过私有 hooks 额外采集：
  (1) 24 层 MLP/router 输入（post-attention RMSNorm 后）；
  (2) 24 层 decoder block 输出（残差相加后、最终 norm 前）；
  (3) 模型最终 RMSNorm 输出。hook 只复制，不替换或修改任何激活。
- 保存所有生成 token 的上述隐藏张量及同次路由，另保存每个 prefill 的最后一个
  token 的隐藏/路由。完整 prefill 路由逐位置对照旧分片，但不重复落盘全部 prompt 激活。
  保留 episode、agent_step、routing step、position 与 token ID 映射。
- 时序含义：decode token t 的隐藏/路由是在读入该 token 后取得，不得声称是 token t
  输出前的信号。固定原自然轨迹回放，不构造新的行为反事实。

## 预先固定的验收与边界

1. 结构：原 JSON/manifest/全部选定分片哈希前后不变；完整 token/position/步边界匹配；
   每次前向全部 24 层 hook 到齐，张量有限，形状/层定义明确。
2. 旧记录一致性：prefill 与 decode 分别比较全 router logits、top-k IDs/weights；
   报逐元素相等率、逐位相等、最大/平均绝对误差、相对 L2；另报全专家概率误差、
   top-k 集合一致率，以及仅由精确并列解释的集合差异。
3. 两次回放一致性：比较保存位置的全部隐藏激活和路由，使用同样数值指标。
   不将“回放重复”替代“与旧记录一致”。
4. 只有完整通过结构、旧路由逐位复现、补采隐藏激活两遍逐位复现，才标记
   `EXACT_REPLAY_ON_SAMPLED_TRACES`；任何数值不一致标记 `NUMERICAL_DIFFERENCE_REQUIRES_REVIEW`，
   如实量化，不事后选择容差宣布通过。结构错误直接失败。
5. 即使路由逐位一致，也不能证明未保存的旧隐藏激活逐位相同；两次回放只验证当前环境。
   不外推全 G 可恢复、不报告 recall/precision、不形成模型机制或泛化结论。

运行前记录本计划/私有代码/共享采集代码/运行时模型实现的 sha256、当前 HEAD、版本、
checkpoint、GPU 状态；结束后核查原文件哈希与 GPU 释放。整个模型进程必须处于
`flock -w 36000 /home/wzh/Agent-Moe-Research/artifacts/agent_v2/gpu.lock <cmd>` 内，
一次只驻留一个模型。CUDA allocator 采用原 resume driver 的 expandable_segments 设置。
CPU 诊断线程数固定为 1，不改变 GPU dtype/backend。

模型前向之前的环境核对补充：本地 kernel cache 存在 `61d502c8…` 与 `c039a37b…`
两个 snapshot，但没有离线解析 `version=1` 所需的 refs。两份 metadata 所列的 Python
计算文件相同，`_ops.py` 仅注册 unique_id 不同。用 kernels 官方 `LOCAL_KERNELS`
入口固定到已有 `c039a37b84eb546e3e38277e011903560347024b` snapshot，
设置 HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE，不修改任何安装包或共享 cache。
原 trace 未逐条记录 kernel revision，不能声称其已被逐条还原；是否匹配由旧路由实测判断。
runner 记录本地 kernel 全部 Python 文件与 metadata 的 sha256。

产物仅写新目录 `artifacts/agent_v2/codex_g/hidden_replay_smoke_v1/`；拒绝覆盖已有目录。
计划 hash 由 runner 启动参数核验。新代码和报告仅在 main 的 codex 命名空间中。
