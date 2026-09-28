# 原 G-dev trace 隐藏激活补采：五条轨迹验证报告

日期：2026-09-09（America/Los_Angeles）。性质：工程冒烟，不是检测器或机制研究结果。

## 结论

**本次五条原轨迹可以通过固定 token、逐步缓存回放补采隐藏激活，无需重新自由生成。**
原路由的完整 prefill 与 decode 均逐位复现；补采的三类隐藏激活在两次回放之间均逐位一致。
独立落盘审计 PASS。没有改变原 trace、原 token 轴或标注，也没有访问 G-conf。

必须区分：原始隐藏激活当时未保存。因此上述结果是“回放匹配旧路由，并稳定产生配对隐藏激活”，
**不是直接证明回放隐藏激活与原采集时未保存的隐藏激活逐位相同**。

## 范围与结果

| 原 trace | 类型 | 生成步骤 | 输出 token | 两次均匹配旧路由 | 两次隐藏激活一致 |
|---|---|---:|---:|---|---|
| g-dev-001--clean | 正常任务 | 2 | 89 | 逐位一致 | 逐位一致 |
| g-dev-001--attack | 直接用户注入 | 2 | 194 | 逐位一致 | 逐位一致 |
| g-dev-017--attack | 多轮用户注入；保留注入前 episode | 3 | 216 | 逐位一致 | 逐位一致 |
| g-dev-097--attack | 工具返回注入 | 2 | 148 | 逐位一致 | 逐位一致 |
| g-dev-305--clean | legitimate_refusal，非普通 clean | 1 | 113 | 逐位一致 | 逐位一致 |

五条 trace、六个 episode、十个生成步骤、760 个输出 token。五条分别完整回放两次，
没有重新采样或再次运行 Agent 工具。样本在模型前向前固定，没有按检测分数选取。

- 每遍校验全部 **10,047 个 prefill token** 和 **760 个 decode token**，24 层、32 个专家。
  合计每遍 **8,299,776 个 router logit 元素**、259,368 个 layer-token 的 top-k 集合。
- 与旧分片比较：router logits、top-k ID 顺序和 weights 全部逐位相同；
  全专家 softmax 概率相同；最大误差、均方误差、相对 L2 均为 **0**。
  没有需要借“并列专家”解释的差异。
- 隐藏激活保存于全部 760 个输出位置，以及十个 prefill 的最后位置：
  - `router_input_hidden`：24 层 post-attention RMSNorm 后、MLP/router 输入，`[24,T,2880]`；
  - `block_output_hidden`：24 层残差相加后的 block 输出、最终 norm 前，`[24,T,2880]`；
  - `final_norm_hidden`：最终 RMSNorm 输出，`[1,T,2880]`。
  均为原生 bf16；共 **108,662,400 个隐藏激活元素**在两次回放之间逐位一致。
  **没有保存整个 prompt 的全部隐藏激活**；prefill 的隐藏张量只保存最后位置。
- 780 个原始 JSON/manifest/分片文件的 sha256 前后不变；计划、采集代码、kernel 源码未变。
  CPU 独立审计再次核对了落盘 tensor 哈希、形状、原 token/position/episode/step 映射和旧路由。
  第二遍隐藏一致性依据 runner 的逐张量比较记录审计，CPU 审计没有第三次执行模型。

## 方法与运行条件

沿用原 `RouterTraceRecorder`，在私有 wrapper 中加只读隐藏 hooks。每个原 prefill
重新建立 KV cache，每个 decode 输入原分片的一个 token，保持原前向参数；
不重新渲染含日期的 chat template，不重新 tokenize，不把多步轨迹改成一次全序列前向。
decode token t 的隐藏/路由是读入 t 后的状态，不能说是输出 t 前的预警信号。

checkpoint：gpt-oss-20b `6cee5e81ee83917806bbde320786a8fb61efebee`，MXFP4/bf16/eager，
eval、batch=1。Torch 2.13.0+cu130、Transformers 5.16.1、Triton 3.7.1、kernels 0.16.1。
本地 kernel 固定 `c039a37b84eb546e3e38277e011903560347024b`，全程 offline。
原数据未逐条记录 kernel revision；本次记录本地 kernel 全部 Python 源码与 metadata 哈希，
用实测旧路由逐位一致来检验回放。未修改共享依赖或 Claude 工作树。

RTX 5090，共享 flock 内只驻留一个模型；合计 **129.23 秒**（含加载 10.25 秒及两次回放）。
PyTorch 峰值 allocated **13.53 GiB**、reserved **13.62 GiB**，不是整卡总显存。
落盘十个 tensor 文件合计 **208.71 MiB**（218,844,632 bytes）。
进程退出后 19:36:54 本地时间 nvidia-smi：无计算进程，整卡占用约 2,263 MiB（桌面基线）。
这只是短样本工程读数，不作为完整 G 批量运行耗时、显存或部署吞吐保证。

## 产物与下一步

- 计划：`docs/research_v4/codex_g_hidden_replay_smoke_plan.md`
  - sha256：`28cc8c1dbb48de3870f493dffcd0dcc0197643ccc1793ac70592db90e3ac8676`
- Runner：`scripts/research_v4/codex_g_hidden_replay_smoke.py`
- 独立 CPU 审计：`scripts/research_v4/codex_g_hidden_replay_smoke_audit.py`
- 单元测试：`tests/test_research_v4_codex_g_hidden_replay_smoke.py`，**7 passed**。
- 产物：`artifacts/agent_v2/codex_g/hidden_replay_smoke_v1/`
  - `preflight.json`：计划/代码/原输入/kernel 哈希、环境版本、原 HEAD；
  - 每条 trace 的 `prefill.safetensors`（最后位置）、`decode.safetensors`、`pass_1.json`、`pass_2.json`；
  - `result.json`：`EXACT_REPLAY_ON_SAMPLED_TRACES`，sha256
    `c368884dd5860648d9640b9ee7c44d7b2db1d6e454143ef55262a4241e357feb`；
  - `audit.json`：`PASS`。

建议以此方式补采开放数据的隐藏激活，为隐藏基线和路由检测提供同轨迹、同观测时点的比较。
批量补采前仍应清点所需训练/校准/开发池的完整输入，确认长轨迹资源预算，
并按基线需要明确是否增加 prompt 内部位置或额外前向。
**本次未自动扩展样本或开始基线训练，不是全 G 可恢复证明，不消耗 G-conf。**
