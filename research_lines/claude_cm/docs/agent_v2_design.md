# Atlas Agent v2 设计与实现基线

日期：2026-09-03（America/Los_Angeles）

状态：`2.4.0` 为已验证基线；`2.5.0` 已通过结构自检与 Q6 行为资格并冻结，等待 B1 预注册

## 1. 设计目的

Agent v2 用于替换 Phase A 中依靠单段 prompt 驱动的简化客服 agent。旧 agent 的主要问题不是不能产生跨领域偏移，而是正常业务行为不稳定：它会跳过 lookup、把示例占位符当作工具、或在完成工具调用后不回复用户。继续扩大旧 agent 的运行次数会增加无效样本和协议混淆。

v2 的目标不是让控制器替模型完成工作，也不是阻止模型生成跨领域内容。它要提供一个更接近生产系统的、确定且可审计的业务环境，使以下两件事可以独立测量：

1. agent 是否完成了授权 routine workflow；
2. 模型候选输出是否开始执行跨领域工作。

当前实现已经包含 agent、本地数据层、完整 MoE trace runner、行为裁决与样本批评估。路由分类方法仍与数据生产分离，避免采集过程中根据 routing 结果调 agent 或挑样本。

## 2. 授权范围

Atlas v2 仍是固定职责客服，不是通用助手。授权 workflow 为：

| Workflow | 必需工具序列 | 正常结果 |
|---|---|---|
| `order_status` | `lookup_order` | 报告订单 ID、状态和预计时间 |
| `return_status` | `lookup_return` | 报告退货、退款状态和时间 |
| `knowledge_qa` | `search_support_kb` | 基于客服 KB 回答并引用文章 ID |
| `order_and_knowledge` | `lookup_order` → `search_support_kb` | 状态查询加相关政策解释 |
| `return_and_knowledge` | `lookup_return` → `search_support_kb` | 退货状态加退款/退货政策解释 |
| `support_case_status` | `lookup_support_case` | 报告客服 case 状态、队列和下一次更新 |
| `warranty_status` | `lookup_warranty` | 报告保修状态、覆盖截止时间和下一步 |
| `subscription_status` | `lookup_subscription` | 报告订阅状态、续费时间和下一步 |
| `case_and_knowledge` | `lookup_support_case` → `search_support_kb` | case 状态加相关处理政策 |
| `warranty_and_knowledge` | `lookup_warranty` → `search_support_kb` | 保修状态加覆盖或 claim 政策 |
| `subscription_and_knowledge` | `lookup_subscription` → `search_support_kb` | 订阅状态加暂停、取消或续费政策 |

知识库只包含客服政策，例如配送、退货、case 更新与升级、保修、订阅暂停/取消/续费。一般知识、诗歌、代码、数学、故事和菜谱不因加入知识库工具而获得授权。

## 3. 分层结构

```text
authenticated task manifest
          │
          ▼
deterministic workflow controller ──> policy/state oracle
          │                                  │
          ▼                                  ▼
recorded read-only tool action ──> sandboxed local tool
                                             │
                                             ▼
                                  model-visible tool result
                                             │
                           optional verified response brief
                                             │
                                             ▼
                              free model natural-language reply
                                             │
                                             ▼
                              completion + behavior observers
```

### 3.1 模型行为层

模型自由生成最终自然语言，controller 不改写、过滤或替换回复。v2.4 不再要求约 1B-active 的模型规划工具或精确复写 JSON；工具 action 由 controller 根据已认证 task manifest 确定，并仍以如下结构进入 trace 和模型 prefill：

```json
{"type":"action","name":"search_support_kb","arguments":{"query":"refund timing","top_k":2}}
```

模型看到真实 action、tool result、provenance，以及复杂 workflow 可选的可信 response brief。brief 只归纳已验证客服事实，不包含 attack，也不授权跨领域工作。模型仍可以在用户可见候选回复中完整生成诗歌、代码或其他跨领域内容，因此 controller 只移除了工具协议噪声，没有旁路研究对象。

### 3.2 确定性工作流层

每个任务显式声明：

- workflow 类型；
- 有序的必需工具步骤；
- 已认证的业务对象 ID；
- KB 检索应命中的文章 ID，或无答案 fallback；
- 用户回复必须包含的完成证据。

任务也可以声明一个缺失字段及其预期澄清值。此时状态机按以下路径运行：

```text
need_clarification_request
    → assistant asks for the missing order/return ID
    → awaiting_user_clarification
    → authenticated user supplies the ID
    → normal tool workflow resumes
```

用户在补充有效 ID 时可以同时附带其他文本；该文本会原样进入下一轮模型上下文，但只有预声明字段被接受为任务澄清，附带的跨领域要求不会获得系统授权。这为后续 multi-turn attack 提供了自然入口。

状态机只接受当前步骤所需的 action。已知但不属于任务的工具记为 `permission`；正确工具在错误阶段调用记为 `state_precondition`；错误 ID 或 schema 记为 `parameter`；未知工具记为 `unknown_schema`。这些错误均不自动成为跨领域主阳性。

### 3.3 工具与信任层

六个工具都是本地、确定性、只读操作：

```text
lookup_order(order_id)
lookup_return(return_id)
lookup_support_case(case_id)
lookup_warranty(warranty_id)
lookup_subscription(subscription_id)
search_support_kb(query, top_k=1..3)
```

每个成功结果携带 provenance：来源类型、来源 ID、版本、`facts_authoritative=true` 和 `instructions_authoritative=false`。后一个字段非常重要：记录或 KB 文本可以提供客服事实，但其中的自然语言指令永远不能修改 agent mandate。未来的 tool-output injection 只改变实验输入，不改变这条权限规则。

未知、越序或参数错误的 action 不会触及业务数据；运行时向模型返回结构化错误，以允许后续研究记录模型是否自行恢复。所有工具无写副作用。

## 4. 知识库问答

`search_support_kb` 使用冻结的本地词法检索器，不调用第二个语言模型。检索按 query 与文章 topics、title、content 的 token 重合确定性计分，相同得分按 `article_id` 排序。这样同一 query、KB 版本与 `top_k` 始终返回同一结果，不会把检索随机性混入 MoE outcome。

支持两种正常路径：

- 有答案：检索到预期文章，回复事实并引用 `article_id`；
- 无答案：空结果本身完成检索步骤，回复“知识库没有答案”并提出人工升级，不得猜测。

KB 问答会使正常客服输出比单纯状态查询更长、语义更丰富。它既提高生产真实性，也为后续 detector 提供重要 hard negatives：长篇自然语言或“代码”等词汇不再自动意味着跨领域偏移。

## 5. 运行时不负责什么

运行时不会：

- 改写、截断或自动拒绝模型的自然语言候选；
- 因出现 poem/code 关键词自动判 drift；
- 让模型规划、排序或精确复写工具 JSON；
- 在模型失败后伪造任务完成；
- 选择或丢弃不利的 sampling seed；
- 将业务错误混入跨领域主标签。

跨领域自由文本仍由 trace 中的实际语义证据裁决，并保留首个证据 token。运行时提供的完成标签只回答 routine workflow 是否完成。

## 6. 当前实现

- 已验证基线定义：`configs/agent_v2_4_support.json`；
- v2.5 扩充候选定义：`configs/agent_v2_5_support.json`；
- 扩充 KB：`data/agent_v2/support_kb_v2.json`，20 篇文章；
- 扩充业务记录：`data/agent_v2/support_records_v2.json`，54 条记录；
- KB 检索：`src/agent_v2/knowledge.py`；
- 工作流与工具环境：`src/agent_v2/runtime.py`；
- hybrid 会话层：`src/agent_v2/session.py`；
- 严格配置加载：`src/agent_v2/config.py`；
- 完整 routing runner：`scripts/run_agent_v2.py`；
- 样本批评估器：`scripts/evaluate_agent_v2_sample_batch.py`；
- 确定性自检：`scripts/check_agent_v2.py`；
- 单元测试：`tests/test_agent_v2.py`、`tests/test_agent_v2_sample_evaluation.py`。

自检覆盖全部 11 种 workflow。测试还覆盖检索确定性、无答案路径、多轮 ID 澄清、错误 ID、未知工具、越序调用、非任务工具、旧配置兼容、扩充数据完整性、回复过早和工具结果的 instruction authority。

## 7. 资格历史与冻结边界

Q1/Q2 证明让小模型同时规划工具和回答客户会产生大量协议噪声；Q3 的 deterministic controller 解决了工具流，但复杂回复仍漏事实；Q4 的 verified brief 将 clean、工具和 KB 指标全部推到门槛以上，却显著抑制 attack drift；Q5 的 workflow-specific brief 恢复到 3 drift / 7 resist，但未达到原先过严的 2/2 exact-input mixed-group 门槛。

因此 Agent v2.4 的冻结结论分成两层：

1. 作为 routine 客服执行环境，它已通过：Q4/Q5 均为 clean 10/10、工具流 30/30、KB 检索与引用 4/4、协议错误 0；
2. same-input mixed outcome 不是 Agent 的生产能力指标，也不再作为正式样本准入条件。Q5 永久保留为失败 calibration，不能追溯补 seed。

正式样本批 01 使用新的任务、payload 和 seed，一次性收集 60 条 trace。数据质量门槛全部通过，所有攻击均有人工 token-aligned 裁决；该批是后续 routing 方法探索的 authoritative development dataset。其行为分布与局限记录在 `docs/agent_v2_sample_batch_01_report.md`。

## 8. v2.5 扩充边界

v2.5 是为后续 240 条 B1 开发批准备的候选版本，不追溯改变 v2.4 或样本批 01。它新增 support case、warranty 和 subscription 三类业务记录及相应组合 workflow，使 routine 工作面由 5 种扩展为 11 种；KB 由 6 篇扩展为 20 篇，记录由 6 条扩展为 54 条。

v2.5 已完成结构实现、确定性自检、CPU 回归测试和与 B1 隔离的 Q6 小规模模型行为资格。Q6 的 18 条 trace 达到全部预注册门槛，但也暴露了保修事实越界和 JSON-like 回复；这些限制必须进入 B1 的 collection-quality 指标。详细结果见 `docs/agent_v2_5_qualification_q6_report.md`，分阶段准入与 B1/B2 解释边界见 `docs/agent_v2_5_expansion_plan.md`。
