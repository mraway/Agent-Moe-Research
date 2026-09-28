# 对《外部数据集与相关工作能否替代或补充数据集 G：评估 v0.1》的反驳核查

日期：2026-09-07。角色：对抗性怀疑者。方法：不信任侦察笔记，逐条重新打开来源
（WebFetch 原始文件 / arXiv HTML / HF API；GitHub REST API 在本机已被限流，改用 raw.githubusercontent.com、
GitHub 网页与本地克隆），并直接阅读运行时代码（tau2-bench 本地克隆 `672227c`、DoomArena 克隆 `b80902f`、
项目 venv 内 transformers 5.16.1 的 `modeling_gpt_oss.py`、公开 gpt-4.1 轨迹 JSON）。
默认判定为 **unverified**；只有我自己重新看到证据的才写 stands / falls。

结论先行：**5 条关键主张中 2 条倒（tau2-bench-verified 的采用建议；"用户模拟器能看到工具结果"），
1 条部分倒（"分块 prefill 是硬前提"），2 条成立（进程内驱动、gpt-oss-20b 分数）。**
另有 3 条次要数字错误（LLMail 类别计数、模拟器成本 ×3、AgentTrajectorySentinel 许可证）。

---

## 1. 五条最关键主张

| # | 主张（评估原文位置） | 我核查的来源 | 判定 | 校正值 |
|---|---|---|---|---|
| 1 | tau2 环境可在进程内驱动，无需 LiteLLM 代理循环，已冒烟验证（§1.2、§3.2） | `src/tau2/orchestrator/orchestrator.py:819-900` (`step`)、`src/tau2/agent/base_agent.py:52` (`HalfDuplexAgent`)、`src/tau2/environment/environment.py:130,370-420`、`smoke_inprocess.py` 与其输出 `smoke_deviant_sim.json`、`regrade_out/` 对 200 条公开 gpt-4.1 airline 轨迹的重评（我重算：200/200 reward 完全一致，均值 0.56） | **stands** | 无需修改。附带修正两点：(a) `Environment.set_state` 回放时**跳过所有非 mutating 工具**（`if not self._is_mutating_tool(...): continue`），只比较 WRITE 工具输出；故 tau2.md §8(b)"工具输出注入会触发 strict 回放异常"仅对 WRITE 工具成立，对 READ 工具（DoomArena 目录攻击的 `get_product_details` 模式）不成立。(b) `Tool.openai_schema`、`get_tool_types()`、`_is_mutating_tool` 均存在；但 **`tool_result_transform` 不是 tau2 的钩子**，是我们 `src/agent_v3/tools.py:71` 的；Path A 由 tau2 编排器执行工具，该钩子必须改挂在 `Environment.get_response` 上（integration §6 已如此写，评估 §3.2 措辞误导）。 |
| 2 | DoomArena 攻击配置可复用：3 航空脚本 + 1 零售工具输出注入 + 3 攻击者人设；Apache-2.0；钉 tau-bench v1；"网关移植到 tau2 只需 env.step + products/users dict，约 1 日"（§2.2） | 本地克隆 `doomarena/taubench/src/doomarena/taubench/{attack_gateway.py (308 行), adversarial_user.py, success_filters/*, system_prompt_config/*, scripts/*.yaml}`、`LICENSE`（Apache 2.0）、PyPI `doomarena-taubench` JSON（0.0.4，2025-04-19，仅依赖 `doomarena>=0.0.4`）、GitHub 页面（63 星，Apache-2.0，README 只提 tau-bench）、`pip download tau_bench`（PyPI 无此包） | **载荷/人设/成功谓词：stands；"约 1 日"：unverified；"retail 116 任务"：falls** | 网关不是"只需 env.step"：它依赖 `tau_bench.types.{EnvInfo,EnvResponse}`、`env.data[db_name][pk]` 的**裸 dict** DB、`env.task.instruction`、`action.kwargs`、`AttackGateway.calculate_reward`；tau2 无 `env.step`，DB 是 pydantic `RetailDB.products: Dict[str, Product]`，动作是 `ToolCall`。`RetailRefundSuccessFilter` 的判官是**硬编码 `https://api.openai.com/v1/chat/completions` + gpt-4o 的 requests 调用**，不走 litellm，不能直接指向本地模型（评估说"都经 litellm"错一半：攻击者经 litellm，退款判官不经）。默认攻击者 `openrouter/openai/gpt-4o`（yaml 第 22 行）。意图聚类：`retail_classification.json` 116 行，`task_id` 取值 **1–116**，tau-bench v1 retail 只有 115 个任务（索引 0–114），无法 1:1 对齐——评估写"retail 116 任务"是错的，映射关系 unverified。 |
| 3 | amazon-agi/tau2-bench-verified（arXiv 2512.07850）"修正 airline 50 任务"，MIT，"airline 用 verified 版任务"（§2.1、§3.1） | raw `pyproject.toml`、`LICENSE`、`README.md`、`FIXES.md` 于 amazon-agi/tau2-bench-verified；arXiv 2512.07850 摘要页；tau2-bench `CHANGELOG.md` [1.0.0] 节；我下载 verified 的 `airline/tasks.json`、`retail/tasks.json` 与本地 1.0.1 逐任务 diff | **falls** | (i) arXiv 2512.07850 是 **SABER**（"Small Actions, Big Errors"，Cuadron/Yu/Liu/Gupta），论文页许可 **CC BY-NC-SA 4.0**；仓库 LICENSE 是 MIT（版权行 "Sierra Research"，即原 tau2 的 LICENSE）。(ii) 仓库基于 **tau2 `0.2.1-dev`**（`requires-python >=3.10`、`litellm>=1.65.0`），不是 1.0.x；README 自述"只改数据集，代码与原版相同"。(iii) 修的是 **retail 与 airline 两个域**（FIXES.md 约 48 retail + 24 airline，telecom 无），不是"airline 50"。(iv) tau2-bench **1.0.0 已内置 27 airline + 26 retail 任务修复**（CHANGELOG 未署名 SABER，但任务号高度重叠：2,5,7,9,13,15,16,18,21,23,25,29,30,34,36,37,38,39,42,43,44,45）。(v) diff 结果：verified vs 1.0.1，airline 50/50 任务不同，其中 36 个仅差 `evaluation_criteria.reward_basis` 缺失（旧 schema），**14 个内容不同**（2,9,13,14,18,21,23,25,33,35,37,43,44,45）；retail 114/114 不同，**58 个内容不同**。校正：以 tau2-bench 1.0.1 为唯一任务源；verified 只作"另一套修法"逐任务比对后择优；其任务文件缺 `reward_basis`，直接塞进 1.0.1 运行时会改变判定基（airline 官方基是 DB+COMMUNICATE）。 |
| 4 | gpt-oss-20b 在 tau-bench v1 上 low/med/high：retail 35.0/47.3/54.8、airline 32.0/42.6/38.0（模型卡 Table 3）；无 tau2 官方数字（§3.5、§4.5） | https://arxiv.org/html/2508.10925v1 Table 3（"Evaluations across multiple benchmarks and reasoning levels"，单位 Accuracy %） | **stands** | 数字逐格一致；120b 为 49.4/62.0/67.8 与 42.6/48.6/49.2。注意：是 τ-bench **v1**（2024 任务集，含后来被修的 27+26 个任务），无用户模拟器/trial 说明，不能外推为 tau2 1.0.1 的通过率；评估"约一半以上会失败"是合理推断而非测量。BenchLM 的 tau2 60.2% 我未复核，维持 unverified。 |
| 5 | TaskTracker：MIT；文本需从索引再生成；预计算激活需表单（README "coming soon"，容器 409）；探针 pickle 仅 Phi/Mistral/Llama(/Mixtral/70B)（§1.1、§2.3） | raw `README.md`、raw `LICENSE`（MIT）于 microsoft/TaskTracker；`curl` 容器 `https://tasktrackeropensource.blob.core.windows.net/activations?restype=container&comp=list` | **stands**（一处状态码修正） | 容器列举返回 **HTTP 400 `PublicAccessNotPermitted`**（"Public access is not permitted on this storage account"），不是 409；结论不变：无 SAS 不可下载。README 本身无许可证行，MIT 来自 LICENSE 文件。探针模型清单与 README 一致。 |

## 2. 其余被核查的事实主张

| 主张 | 来源 | 判定 | 校正值 |
|---|---|---|---|
| "tau2 把工具结果也展示给用户模拟器，工具输出注入可能先劫持模拟器，须在 `HalfDuplexUser` 子类里屏蔽"（§3.6、§4.2；integration §6 据此把模拟器输入估为 1.5–2.5k token 含工具结果） | `src/tau2/user/user_simulator_base.py:41-47` (`is_valid_user_history_message`) 与 `UserState.flip_roles`（对 assistant 工具调用 **raise ValueError**，只接受 `requestor=="user"` 的 ToolMessage）；`orchestrator.py:872-880`（ENV 的回复 `self.to_role = self.from_role`，即回到发起方 AGENT，从不到 USER） | **falls** | airline/retail 的用户模拟器**只看到 agent 的文本回复**，看不到 agent 的工具调用与工具结果（telecom 的用户侧工具除外）。"屏蔽工具结果"这项工作不需要做；§4.2 的第二条风险不存在；模拟器 token 估计应下调（见成本行）。 |
| 用户模拟器成本："Sonnet 5 $12–18 / 1000 会话（启用缓存），无缓存 ×3；Opus 5 $30–45"（§1.3、§3.6） | tau2.md 从公开 gpt-4.1 轨迹实测：用户模拟器每会话 5.6k prompt + 242 completion token（与 tau2 记录的 user_cost $0.013 在 gpt-4.1 $2/$8 价下自洽）；claude-api skill 价目：Sonnet 5 $2/$10、Opus 5 $5/$25 per MTok，缓存读 ≈0.1×、写 1.25× | **falls（×3 部分）** | 无缓存 Sonnet 5 ≈ 1000 × (5.6k×$2 + 242×$10)/1M ≈ **$13.6**（Anthropic 分词可能多 1.0–1.35×，即 $14–19）；Opus 5 ≈ $34。integration §6 的"15–20k 输入/会话"是实测值的 ~3 倍，因此"无缓存 ×3"是错的；启用缓存只会更低。评估给的区间数值碰巧对，推导错。 |
| "分块 prefill 是硬前提：eager attention 在 8k 时单张量 8 GB，5090 装不下"（§3.2 工作项，1.5 日） | 项目 venv `transformers 5.16.1` `modeling_gpt_oss.py:386-391`：`_supports_flash_attn = True`、`_supports_sdpa = False`、**`_supports_flex_attn = True`**；`integrations/flex_attention.py:270,315-353` 用 `s_aux` 实现 attention sinks（CUDA only）；`configs/pilot_gpt_oss_20b_mxfp4.json` 注释称"eager 是唯一无依赖选项"；torch 2.13.0；`kernels 0.16.1` 已装 | **partially falls** | 8.6 GB 的算术成立（64 头 × 8192² × 2 B，另加 sinks 拼接与 softmax 副本）；但 **`attn_implementation="flex_attention"` 是 torch 自带、无额外依赖、且 transformers 已为 gpt-oss 实现 sinks** 的选项，不物化 T×T 张量。分块 prefill 可能根本不需要；应先做 1 小时探针（flex vs eager 的 top-k 路由一致性、峰值显存），再决定是否投 1.5 日。pilot 配置注释"eager 是唯一无依赖选项"是错的。 |
| 提示长度："retail 中位 6–7k、p90 10k token" | 我重算公开 gpt-4.1 retail 轨迹 456 sims：每会话最大 prompt 中位 **6,950**，p90 **9,160**，最大 10,575 | **stands** | — |
| 算力："每会话 ~1,500 生成 token，~50 GPU 小时 / 1000 会话" | gpt-4.1 retail 每会话生成 token 中位 **772**、p90 1,141（不含 analysis）；P0 实测 16.8 tok/s（含 prefill 与落盘） | **unverified** | gpt-oss low 会多出 analysis token，1,500 是猜测；按 16.8 tok/s，1,500 token ≈ 90 s + prefill，50 h 是可信上界而非测量值。探针后再定。 |
| LLMail-Inject：461,640 提交 / 208,095 唯一 / 成功 3,018 + 306；MIT 不门控；"phase-2 api_triggered 3,165 条按 social-engineering 13,950 / direct 13,169 / obfuscation 4,482 分类"（§2.2） | HF API（`gated: false`, `license: mit`, 2025-05-16）；arXiv 2506.09956 HTML（"3,018 (0.8%)"、"306 (0.3%)"、"208,095 unique prompts = 169,598 + 38,497"）；本地 `llmail_labelled_phase2.json` 重算 | 计数 **stands**；分类句 **falls** | phase-2 标注文件 37,303 条 = `judge` 34,138 + `api_triggered` 3,165；**类别只存在于 judge 条目**（social engineering 13,950 / direct 13,169 / obfuscation 4,482 / N/A 2,500 / 混合 37），api_triggered 条目的 `judge_category` 为 None。另："~3.3k 成功自适应载荷"是成功**提交行数**，唯一性未验（3 MB 样本中 19 条成功行全唯一，倾向接近唯一）。论文的 phase-1/2 唯一数（169,598 / 38,497）与标注文件（160,741 / 37,303）不一致，原因未查。 |
| AgentDojo 0.1.35（2025-10-27）、MIT、硬依赖 anthropic/cohere/google-genai/openai；97 用户任务 / 35 注入任务 / 949 用例（v1.2.2），v1 为 97/27/629 | PyPI JSON；在 `venv_agentdojo` 内重新加载 suites 计数 | **stands** | — |
| ChatInject：gpt-oss-120b AgentDojo 纯文本载体 ASR 0.3% → 伪模板 51.4%（多轮 55.5%）；InjecAgent 0.0 → 14.2 | arXiv 2509.22830 HTML Table 1 | **stands** | gpt-oss-20b 仅用于 §5.1 的模板相似度估计，未被攻击评测——评估已如此写。 |
| FraudBench 仓库 `github.com/leanmcp/fraudbench` 404 | GitHub 网页 404；arXiv 2608.18136 摘要页无代码 URL | **stands** | — |
| tau2-bench 1.0.1、MIT、py>=3.12,<3.14、litellm>=1.80.15,<1.82.7；任务数 airline 50 / retail 114 / telecom base 114 (full 2,285, small 20) / banking_knowledge 97 / mock 10；retail 官方基 DB+NL_ASSERTION 112 + DB 2（40 个非空 nl_assertions）；airline DB+COMMUNICATE 50/50 | raw `pyproject.toml`；本地 `data/tau2/domains/*/tasks.json`、`split_tasks.json` 重算 | **stands** | telecom full 集 reward_basis：ENV_ASSERTION 2,253 + ENV+ACTION 32；banking：DB 88 + ACTION 9。tau2.md 说的"telecom ACTION 20/114（base）"我未按 base 子集重算，unverified。PyPI `tau2` 为无关包一说本次未重新打开 PyPI，维持侦察结论。 |
| 许可证：AReaL apache-2.0（1,148/563/271 任务，33,531 SFT）；fuvty apache-2.0（280）；Bitext cdla-sharing-1.0；ABCD MIT；NCUser MIT（ICLR 2026，arXiv 2509.23124，集成 tau-bench v1）；Nemotron-Personas-USA cc-by-4.0；RealUserSim odc-by；ASSEBench apache-2.0；IPI-exposure-signal Apache-2.0；Agent-SafetyBench MIT；SHADE-Arena MIT；Zhang 2025 CC BY 4.0 | HF API / raw LICENSE / GitHub 页面 / arXiv 摘要页 | **stands** | Agent-SafetyBench 的"2,000 用例 / 349 环境"、ASSEBench "2,293 条"、SHADE-Arena "公开 5 对"我未见到数字，unverified。 |
| AgentTrajectorySentinel："Apache（Gemini 子集禁训）"（§2.3、§4.4） | HF API：`license_name: "mixed-see-licensing"`，指向 `LICENSING.md`；3,581 集 / 33 语料 | **falls（部分）** | 不是单一 Apache；为混合许可，需读 LICENSING.md 后再决定能否进 G-ext 或数据卡。 |
| DoomArena "2025-09 起停更、last push 2025-09-12" | GitHub API 被限流；本地克隆 HEAD `b80902f` 2025-09-10；网页 63 星 | **unverified（日期）** | 停更结论可信，精确日期未复核。 |

## 3. 对工作量表（§3.2，~12.5 工程日）的挑战

- **可删**：模拟器屏蔽工具结果（不存在该路径）。
- **可能可删（先探针）**：分块 prefill + prefill 分片只存 top-k（1.5 日）。flex_attention 路径若数值等价，只剩"prefill 分片存储策略"这一小项。注意 flex 路径要求 CUDA（5090 满足），且 `kernels` 包已装但 FA3 内核未下载，不必依赖它。
- **被低估**：DoomArena 网关移植"约 1 日"。实际要重写：DB 篡改（pydantic 模型而非 dict）、动作过滤（`ToolCall` 而非 `action.kwargs`）、`EnvResponse/EnvInfo` 语义、退款判官（硬编码 OpenAI HTTP，需换成 litellm 或本地判官）、tau_bench 依赖剥离（tau_bench 不在 PyPI，需 git 安装才能 import `doomarena.taubench`，否则只能复制文本载荷）。复制**载荷字符串与成功谓词**确实是半天的事；"网关移植"不是。
- **未变**：HalfDuplexAgent 适配器（接口已核实：`get_init_state`、`generate_next_message`、`is_stop`、`set_seed`；工具结果以 `MultiToolMessage` 回到 agent）；判定胶水（airline 全确定性；retail 需 NL 判官 40 任务）。
- **新增**：若采用 verified 修法，需一次 airline 14 + retail 58 任务的逐条比对（约 0.5 日），否则直接放弃 verified。

## 4. 必须改的评估正文位置

1. §2.1 tau2-bench-verified 行与 §3.1"airline 用 verified 版任务"：改为"以 1.0.1 为准；verified 为 0.2.1-dev 分支、修 retail+airline、与 1.0.0 修法重叠且 schema 旧"。
2. §3.6 第三点与 §4.2 第二点：删除"模拟器可被工具输出注入劫持"。
3. §3.2 工作项"分块 prefill（硬前提）"：改为"探针决定：flex_attention 优先"。
4. §2.2 LLMail 行：类别计数改挂在 judge 标注的 34,138 条上。
5. §2.2 DoomArena 行："retail 116 任务"改为"116 行、task_id 1–116，与 v1 的 115 任务对齐关系未定"；"网关移植约 1 日"改为"载荷复制 0.5 日；网关重写 unverified，≥2 日"。
6. §3.6 成本："无缓存 ×3"删除；给出无缓存 ≈ $14–19 / 1000（Sonnet 5）。
7. §2.3 / §4.4 AgentTrajectorySentinel：Apache → mixed（见 LICENSING.md）。

## 5. 本次打开的来源

tau2-bench raw pyproject/CHANGELOG https://raw.githubusercontent.com/sierra-research/tau2-bench/main/{pyproject.toml,CHANGELOG.md}；
tau2-bench-verified https://github.com/amazon-agi/tau2-bench-verified 及 raw {LICENSE, README.md, FIXES.md, pyproject.toml, data/tau2/domains/{airline,retail}/tasks.json}；
SABER https://arxiv.org/abs/2512.07850；gpt-oss 模型卡 https://arxiv.org/html/2508.10925v1；
TaskTracker raw README/LICENSE https://raw.githubusercontent.com/microsoft/TaskTracker/main/{README.md,LICENSE} 与 blob 容器；
LLMail-Inject https://huggingface.co/api/datasets/microsoft/llmail-inject-challenge、https://arxiv.org/abs/2506.09956、https://arxiv.org/html/2506.09956；
DoomArena https://github.com/ServiceNow/DoomArena、https://pypi.org/pypi/doomarena-taubench/json；AgentDojo https://pypi.org/pypi/agentdojo/json；
ChatInject https://arxiv.org/html/2509.22830；FraudBench https://github.com/leanmcp/fraudbench（404）、https://arxiv.org/abs/2608.18136；Zhang https://arxiv.org/abs/2512.24415；
HF API：inclusionAI/AReaL-tau2-data、fuvty/tau-bench-synthetic、bitext/Bitext-retail-ecommerce-llm-chatbot-training-dataset、Atarogic/ASSEBench、nvidia/Nemotron-Personas-USA、Salesforce/RealUserSim、sunnydubey1111/agent-trajectory-sentinel；
raw LICENSE：asappresearch/abcd、jianshuod/IPI-exposure-signal；GitHub 页面：holi-lab/NCUser、thu-coai/Agent-SafetyBench、jkutaso/SHADE-Arena。
本地：`scratchpad/ext_datasets/{tau2-bench, doomarena_src, venv_agentdojo, traj_gpt41_*.json, regrade_out, llmail_labelled_phase2.json, verified/}`；
`/home/wzh/Agent-Moe-Research/.venv/lib/python3.12/site-packages/transformers/{models/gpt_oss/modeling_gpt_oss.py, integrations/flex_attention.py}`；`configs/pilot_gpt_oss_20b_mxfp4.json`。
