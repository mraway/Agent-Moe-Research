# Codex G 冒烟访问事故与暂停记录

日期：2026-09-08 UTC。状态：**原冒烟作废；检测研究暂停，待主管裁定**。

更新：主管随后明确表示“你没看数据就没有关系”，并在机制/算法研究方向讨论后指示“下面我们开始研究”。据此解除研究暂停；原事故及隔离产物继续保留，不使用该输出、不取消G-conf守卫。下文的暂停措辞保留为当时记录。

## 事实

1. 主检出按 `merge --ff-only` 从 `9e6ad56` 更新到 `12c83b4`，共114个提交；无 merge commit、rebase 或 reset，原未跟踪文件保留。未修改 Claude 工作树/分支。
2. 运行了指定的 `scripts/research_v4/codex_smoke_g.py`，额外传入 `--json artifacts/agent_v2/codex_g/onboarding_smoke.json` 以保存审计产物。它退出码为0，但不能据此认定封存纪律通过。
3. 该版本 `LABEL_SUBSETS` 包含 `g_conf`；`labels()` 对其调用 `io_g.read_labels(...)` 和 `_sha256(...)`。`_refuse_if_sealed()` 只保护 `dataset_g/g_conf/`，不保护同级的 `dataset_g/annotations/g_conf/`。因此脚本已在进程内读取 G-conf 文本标签并计算汇总。
4. Codex 在执行前的工具调用中已取得这段源码，却未及时阻止运行，这是执行审查失误，不归因于用户授权。发现后，没有向模型上下文展示该进程的 stdout，没有读取生成 JSON 的内容，也没有使用其标签或汇总设计统计量/选择参数。
5. 本次没有打开 G-conf 的 `trace.json`、路由分片或封存清单内容；配置元数据读取与目录/stat 检查发生在原脚本中。**不得把第4项当作“G-conf 标签从未被读取”的证明**。
6. 尚未运行任何本线检测器、拟合或开发评价；没有模型加载、新数据生成或 G-conf 两阶段解封。G-conf 路由仍保持封存。

## 隔离与修复范围

原产物已移到 `artifacts/agent_v2/codex_g/invalidated_smoke/onboarding_smoke.json`，只作事故证据，**不得用于研究、复现读数或作冒烟通过证明**。没有删除原数据或改动封存内容。

增加独立的 `codex_guarded_smoke_g.py`：只读取 G-fit/G-cal/G-dev 标签；对 G-conf 轨迹目录、标注目录及本次隔离产物安装进程内文件打开守卫，检查规范路径与符号链接目标。它复用原冒烟代码但不改其文件，也不改共享 loader / harness。守卫只作为额外防线，不替代流程纪律或提供 OS 隔离保证。

研究计划可作为不依赖此次输出的设计草案，但在主管裁定之前，不启动统计量实现、拟合、检测评价或 G-conf 相关操作。安全守卫的合成测试与严格访问冒烟属于事故处置，不是算法实验。

## 待裁定

原冒烟结果作废。请主管确认：在披露这次脚本级标签读取、保留事故记录、禁止使用其产物后，是否允许本线按已提交计划继续 G-dev 开发。本文不自行宣告此次读取对未来联合确认的影响为零。

## 处置验证

- 计划及初始事故记录先提交在 `3956c2d`；计划文件 sha256 为 `4be18631f74f01a03e933fd1e124a42601ce21fb135b8fe98859d1eb9182a0e3`。随后才编写独立守卫，没有实现候选检测器。
- `test_research_v4_codex_access_guard.py`：7项合成测试通过，涵盖路由/标注两目录、符号链接、父路径跳转、隔离产物与真实 Python open hook。测试没有访问真实 G-conf 文件。
- 严格冒烟只加载 G-fit/G-cal/G-dev 标签；G-dev 恰为784行，attack 352 / benign_control 192 / clean 192 / benign_lexical 24 / legitimate_refusal 24。
- G-dev 样本 `top_k_ids=(24,89,4)`，`router_logits=(24,89,32)`；脚本断言计数与形状通过，退出码0。G-conf 仍只对目录/存在性/权限作检查；配置元数据读取依入门包允许范围进行。
- 新报告：`artifacts/agent_v2/codex_g/onboarding_guarded_smoke.json`，sha256 `4451ede943249ec9e0918d251176fe4c57f2084e8698e17391ead7d82d471082`。本次守卫审计了2,397次 Python 文件打开，封存路径打开尝试为0；该计数不声称覆盖任意原生文件读取。
- 原 `codex_smoke_g.py` 未修改，sha256 仍为 `2629940fa87c5834e20112e120be1c5f63d77345d46ea5f291548cbe1f0b8ad1`；共用 loader、harness、预注册和入门包均未修改。
- 以上仅证明新包装器的本次访问冒烟通过，**不覆盖、消除或追认首次标签读取事故**。检测实验继续暂停。

## 顺带发现的文档一致性问题

入门包 §5.1 的 `alpha_eff` 文本公式分母写为 `n_cal`；其逐折读数和 `trm3_g.attainable_rank` 实际使用 `n_cal+1`。本线将调用共用实现、不另写公式；原入门包和共享代码均未修改，建议 Claude 线核对该笔误。
