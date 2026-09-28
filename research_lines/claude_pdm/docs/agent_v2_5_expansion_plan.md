# Atlas Agent v2.5 扩充计划

日期：2026-09-04（America/Los_Angeles）

状态：结构实现及 Q6 小规模模型行为资格均已通过；v2.5 已冻结，B1 已完成运行前预注册

## 1. 目的

下一批开发数据的目标规模是 240 条 trace，即 80 个 `clean / benign_control / attack` 匹配三元组。仅把原有 5 种 workflow 重复更多 seed，会把表面样本量放大，却不能充分排除“少量模板过于容易分类”的 false positive。因此先扩充 routine 工作面，再冻结采集设计。

v2.5 只扩展正常客服业务能力，不根据历史 routing 特征或分类结果设计工具、记录或 KB。它仍然是固定职责的客服 agent，跨领域生成仍不在授权范围内。

## 2. 结构扩充

### 2.1 工具

保留原有三个只读工具，并新增三个只读查询：

```text
lookup_order(order_id)
lookup_return(return_id)
lookup_support_case(case_id)
lookup_warranty(warranty_id)
lookup_subscription(subscription_id)
search_support_kb(query, top_k=1..3)
```

### 2.2 Routine workflow

原有 5 种 workflow 扩展为 11 种：

| 类别 | Workflow |
|---|---|
| 单记录查询 | `order_status`、`return_status`、`support_case_status`、`warranty_status`、`subscription_status` |
| 纯知识问答 | `knowledge_qa` |
| 记录加政策解释 | `order_and_knowledge`、`return_and_knowledge`、`case_and_knowledge`、`warranty_and_knowledge`、`subscription_and_knowledge` |

业务数据由 6 篇 KB、6 条记录扩展为 20 篇 KB、54 条记录：12 个订单、12 个退货、10 个 support case、10 个 warranty 和 10 个 subscription。记录内容覆盖不同状态、日期、后续动作和政策主题，而不是只替换 ID。

## 3. 实现约束

- 旧版三工具配置继续可加载，保证历史实验可复现；
- 新 workflow 只有在 agent 配置显式提供对应工具时才能构造运行时；
- 配置启用的记录查询必须有非空数据表；每条记录的内部 ID 必须与索引键一致；
- 所有工具仍为本地、确定性、只读；工具结果仍标记 `instructions_authoritative=false`；
- workflow 的工具顺序和认证 ID 继续由 task manifest 固定；
- 模型仍自由生成最终用户回复，controller 不替换或过滤它。

## 4. 分阶段准入

### Step 1：结构资格（已完成）

- 配置、数据与 JSON 可加载；
- 11 种 workflow 的确定性工具序列都能完成；
- 新增 workflow 的越序、错误目标和旧配置不可用检查生效；
- 全量 CPU 回归测试通过。

结构资格只证明 agent runtime 正确，不证明目标模型能够稳定使用新增上下文。

### Step 2：小规模模型行为资格（已完成）

使用不进入正式数据集的专用任务、payload 和 seed，覆盖 6 种新增 workflow。主要检查：

- routing trace 完整；
- clean arm 能完成新增 routine 工作；
- 三个 arm 的必需工具流程都完成；
- 组合 workflow 检索并引用预期 KB；
- 最终输出为自然语言，而非协议错误。

攻击是否 drift 只做描述性记录，不把特定 drift 数量作为 agent routine 能力的准入门槛，也不在资格运行后替换 seed。

Q6 共运行 18 条 trace，所有预注册门槛通过：工具流 18/18、clean 完成 5/6、clean KB 检索与引用 3/3，攻击经人工裁决为 2 drift / 4 resist。详细结果与限制见 `docs/agent_v2_5_qualification_q6_report.md`。

### Step 3：冻结 240 条 B1 设计（已完成）

只有 Step 2 通过后才构造 B1。B1 预期包含 80 个匹配三元组，覆盖至少 8 种 routine workflow、12 个 attack family、6 个目标领域，以及 direct-user、tool-output 和 multi-turn 三种入口。正式配置将另行预注册，资格任务、payload 和 seed 不复用。

训练/验证必须按任务模板、attack family 和语义近邻分组，不能把同一模板的 seed 变体拆到两侧。240 是 development signal batch，不被解释为最终确认；若 B1 给出稳定信号，再使用独立 B2 批次验证。

B1 最终冻结为 80 个匹配三元组，覆盖全部 11 种 workflow、16 个 attack family、8 个目标领域和三种入口。正式配置与门槛见 `docs/agent_v2_5_b1_plan.md`。

## 5. 当前结构自检结果

- 工具：6；
- routine workflow：11；
- 业务记录：54；
- KB 文章：20；
- 确定性 workflow 自检：11/11 完成；
- CPU 单元测试：81/81 通过。
