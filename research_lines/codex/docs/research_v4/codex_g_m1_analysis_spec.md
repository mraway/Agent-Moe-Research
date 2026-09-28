# Codex G / M1：表示—阶段诊断规范

状态：实施前固定的开发分析规范；不是G-conf预注册。日期2026-09-08 UTC。母计划为 `docs/codex_g_plan.md` v2。M1不拟合攻击标签、不选择检测器工作点，也不报告在线检出结论。

## 1. 输入与冻结

唯一真实数据池：G-dev（784 episode，五路臂恢复），标签 `artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl`；配置 `configs/dataset_g/g_dev.json`。不扫描其他批次、旧结果或封存内容。使用 `CodexGAccessGuard` 并对每个原生 safetensors 读取显式检查规范路径。代码和方案哈希先于读取路由记录；原始张量/标签不加入git。

公共几何固定 V1/message、w=8、全24层、端点前352 looks；事件锚点从共享 `view_anchors` 获取，注入在场用共享 `injection_present`。完整配置的fixture/scenario元数据进入 `fold_assignment(key='fixture_rank_mod')`，再用 `fold_of_episodes` 映射episode。拟合折k+1、正常参照折k+2、评价折k；同一scenario不跨折。

## 2. 表示与数据契约

U在缓存选中的每个专家上赋1/4；W散射实际 `top_k_weights` 并按每层每token重新归一化；P为全32路 `softmax(router_logits.float())`。不重算专家选择，不把P当实际混合系数。

逐decode分片同时读取ids、token ids、门控权重、全logits，并与loader的episode张量逐位核对。记录原始W的simplex误差、重建 `softmax(gather(logits,stored_ids))` 与实际W的最大偏差、token数/分片数。非法形状、非有限值、负权重、总权重<=0、id越界或id重复即停止。出现门控权重重建最大误差>0.01或simplex误差>0.01时停止数据契约检查，不进入机制结论。

## 3. 固定读出与正常百分位

1. 拟合折正常episode仅取 `filter_pass is True`。对各表示和通道，按该通道保留token等权估计逐层参考分布q；若无该通道正常token，回退至拟合折全部保留token并显式记录。
2. 每个合格窗口计算该表示的均值y；逐层计算 `JS(y,q) = 0.5 KL(y||m)+0.5 KL(q||m)`，m=(y+q)/2，采用自然对数，0log0=0。24层均值为主要raw分数；3个固定8层带只作诊断。
3. 对每个留出episode的look，用正常参照折中同通道、同 `ordinal//32`、同 `episode_index` 的look分数计算midrank百分位 `(count< + 0.5 count=)/n`。百分位越大表示越偏离该参照；无平滑、无报警阈值。
4. 稀疏回退固定顺序：精确三键 → 同通道/同位置桶 → 同通道/同episode_index → 同通道 → 全部参照look。每级需至少30个look、10个不同episode；最后一级仍不足则报缺失，不自行放宽门槛。每个区间同时报告严格精确三键的覆盖率与其子集读数。
5. 该百分位是参照窗口等权、目标episode内窗口平均、目标episode之间等权的描述性量；**不是保形p值、总体AUROC或检测召回**。不同表示分别使用自己的正常分数分布，匹配键和有效集合相同。报告正常留出episode本身的百分位作校准诊断。

## 4. 事件窗口、对照及分母

- 正例候选只取攻击臂、注入在场、共享E锚点有效的episode。E前为 `[max(0,E-16),E-1]`，E为`[E,E+16]`；有X时另报 `[E+17,X-1]`、`[X,X+16]`、`[X+17,X+64]`。空区间、无合格端点、H截断均逐项计数。
- 每个区间只使用先按H截断后的合格端点。X+16越过H_end单列，不能从其短区间声称X时效性；E前/后比较另报同一episode成对且事件通道不变的子集。
- 五路臂、轨迹类、quality过滤状态、注入渠道、family、tier、8层带分别描述。`legitimate_refusal`不是正常参照/误报分母；静默攻击必须排除 `multi_turn_user` ep0，并同时报告被排除数。
- 对全路径窗口百分位报告全部正常输入和过滤后正常两个集合（它们不是FAR）；静默攻击、engaged-only、execution等分别列出，不将未注入回合混入静默对照。episode位置匹配是正常参照的一部分，不等于已经排除所有长度/语义混淆。

## 5. 集合固定时的变化及数值信息损失

对逐token逐层记录 `JS(W,U)` 和 `1-H(W_selected)/log(4)`，用于描述把门控权重均匀化的数值损失。它们非零不等于包含行为信息。

相邻生成token仅在同通道、同Agent step内比较。记录top-k集合保持不变的层比例；在集合相同的层上记录 `JS(W_t,W_t-1)` 与 `JS(P_t,P_t-1)`；同时记录全层集合更替比例。先按实际有效层/token对聚合，再在事件区间按episode汇总；没有相同集合样本时报告缺失，不填0。

集合不变条件是事后表征子集，不是干预；选择该子集可能改变样本构成。每阶段报告其支持量、每层覆盖及不加该条件的基准，不据此单独宣称权重的条件互信息或因果作用。

## 6. 区间、选择限制与产物

固定比较W−U、P−U，按相同有效episode的事件百分位做配对差；按 `attack_family_id` 重抽2,000次（seed=20260908），95% percentile区间，并报family×tier稳健列。报告实际家族数及逐家族N。所有区间均为开发描述性、不做显著性筛选，不据此选择某层/事件窗为新主格。若以后进入正式二元命中评价，再使用共享家族bootstrap + 精确McNemar合取规则。

产物目录 `artifacts/agent_v2/codex_g/m1_representation_stages_v1/`：`run_manifest.json`、输入/代码哈希、`episode_metrics.jsonl`、逐look raw分数/百分位（不含原文）、分母与分层汇总、数据契约核查、耗时/RSS。可保留本线top-k缓存；不得改写共享缓存。目录已存在则拒绝覆盖，程序故障修复记录在独立日志。

运行中不查看G-conf、不复核其封存哈希、不启动GPU。没有相同正常参照键的子集不补样、不修改冻结场景工厂。主要问题仍是“不同路由信息对哪些可见行为阶段有可重复的关联”，不是先选一个赢的算法。
