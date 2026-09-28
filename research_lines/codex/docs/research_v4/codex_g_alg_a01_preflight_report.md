# A01：四格近邻实现与正常预检查

2026-09-08，副对话算法线。**覆盖和数值预检查通过；完整评价因预设资源门暂停。**
尚未拟合报警阈值、评分攻击或计算新的 FAR/recall；不能宣称算法优于 S。

## 1. 已交付内容

[A01 v1 规范](codex_g_alg_a01_spec_v1.md)固定四格：U/W × mean/ordered，
全24层、w8、完整 M7 端点、同通道/回合正常库、三个不同场景近邻。
主比较仍是 W_ordered 对 S，在 S 的5% filtered FAR预算所得实测 FAR上匹配。
本轮实现[纯数学模块](../../scripts/research_v4/codex_g_alg_a01_math.py)、
[正常预检查](../../scripts/research_v4/codex_g_alg_a01_preflight.py)及
[序列化修正入口](../../scripts/research_v4/codex_g_alg_a01_preflight_v1_1.py)。
共享校准/攻击评价接口与完整独立审计尚待完成，没有把 raw 距离当成在线检测结果。

18项原始合成测试、3项序列化回归测试全部通过。覆盖直接 float64 Hellinger 枚举、
U/W 定义、不同场景去重、整个自身场景排除、窗口边界、前缀不变性（数值容差）、
分块一致性、平局规则、状态保存恢复与封存路径拒绝。

## 2. 正常数据可以支持这个固定库定义

只读 M7 已冻结正常阶段资料和原有 G-dev 缓存。
408个正常 episode 的全部端点、通道和 ordinal 与共享网格逐位相同，未见跨 step 窗口。
293个 filtered 正常用于各自角色下的拟合/校准候选；三折没有场景跨角色。
18个条件库均满足排除自身**整个 scenario**后仍有至少3个 donor。

| 条件 | 三个拟合库的场景数 | 各库窗口数范围 |
|---|---|---:|
| analysis / 第1回合 | 42 / 43 / 40 | 1,491–1,530 |
| analysis / 第2回合 | 14 / 16 / 14 | 241–341 |
| commentary / 第1回合 | 42 / 43 / 40 | 2,126–2,296 |
| commentary / 第2回合 | 14 / 14 / 13 | 733–837 |
| final / 第1回合 | 42 / 43 / 40 | 15,121–16,801 |
| final / 第2回合 | 14 / 16 / 14 | 5,582–7,582 |

最小库13个场景，拟合流排除自身后最少12个。这里是**数量支持**，不证明各话题/体裁
都有相近的正常路由，不证明误报问题已经解决；攻击阶段的新条件仍须逐例检查，不能丢弃。
第二回合 analysis 某库的单场景占106/341个窗口（31.1%），显示候选机会并不均匀。
三场景去重能防同场景占满三个名额，但不消除长场景更多窗口带来的最近距离优势；本轮不因此改库。

## 3. 数值与资源读数

固定选取每个条件字典序首个合格评价正常 episode，前至多64窗，共691个 look，
每个同时计算四格。未使用五个历史误报的报警位置来选择这批 smoke。
对每条件首/末查询的全部四格、三个 donor 用 float64 逐坐标公式独立复核，
最大绝对误差 `5.1241e-7`，低于事前容差 `2e-6`。

- 成功归档的预检查：4.40秒，CPU单线程，峰值 RSS **1.167 GiB**。
- 按各条件实际工作量估计，正常 fit/cal/eval 的**距离计算**约 **7.68分钟**。
- 用最慢条件单位成本估计完整190,284 looks，再加正常工作量并乘2保守余量，
  本轮批量检索估计 **31.54分钟**，超过规范600秒门；未启动完整运行。
- 该估计重复包含部分正常评价且未实测完整校准/审计成本；不是精确时长承诺。
  测到的是批量离线缓存检索，不是生产逐 token 的p50/p95延迟。

建议保持算法不变，CPU仍单线程、内存仍2 GiB，把本轮计算预算放宽到40分钟后分阶段执行；
或者另冻一项纯工程优化再测成本。**尚未获批预算调整，不自动用GPU、ANN、抽样或压缩库。**

## 4. 首次失败及可追溯性

v1首次运行完成正常覆盖和18条件计算，但保存结果时 NumPy int64 无法JSON序列化。
原目录中的 `failure.json` 和空 `result.json` 原样保留；该次不作为完整成功实验。
修正详情见[v1.1输出修正](codex_g_alg_a01_preflight_fix_v1_1.md)：只转换输出标量、
先序列化再开文件、新建版本目录。原数学、数据选择、参数、源码与失败产物均未修改。
修正版完整重跑，未按速度择优报告某次运行。

本轮核验904个输入文件，其中586个正常路由缓存（293 episode × top-k/logits），
运行结束后又逐一验证输入和28个作用域源文件SHA；无变化。Python访问守卫无阻挡尝试，
不是OS级隔离。没有读取攻击路由、G-conf的任何数据/标签/封存清单，也没有加载模型。
未修改主线机制、Claude或共享源文件，也未提交、切换分支或重置；新增产物目录均受Git忽略规则保护。

成功归档目录：
`artifacts/agent_v2/codex_g/alg_a01_joint_neighbors_v1_1/`。

| 文件 | SHA256 |
|---|---|
| A01 v1 规范 | `d46b7df15ee2fe6a34d0b2454743ba38ad4392ece3417cc50a94d3e6ce48866f` |
| preflight_source_manifest.json | `8369d373c9e854e757865a06b4e493baab77f0fb570c10ec1567d44ce9686c3c` |
| preflight/support.json | `0d294d1843f8d96bff8ac8cdcdf3137866d93f8e082dbb7b9bb0dfbbeb0e43c0` |
| preflight/result.json | `789208dfe97fa437c79d4c18daf32df95f1b689cd76d6fb92648203a0dbb44da` |

可复跑命令（同名目录存在则拒绝，不能覆盖当前产物）：

```bash
PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_research_v4_codex_g_alg_a01*.py'
PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -B scripts/research_v4/codex_g_alg_a01_preflight_v1_1.py --stage freeze
PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -B scripts/research_v4/codex_g_alg_a01_preflight_v1_1.py --stage preflight --source-sha256 <新环境freeze输出的SHA>
```

## 5. 主线同步与接下来交付

已读机制M9完整报告，提交 `1376661bba398998f209908bd5c8183cffdb0228`，报告SHA
`4de82cf922655ff133c012cf75010f348a8ab63697fb7ea974380e14c27185a8`。
同token差异不能完全归于窗口前7个token，但仍是小覆盖的上下文相关现象；
本轮不改成中层检测器，不声称权重必胜，也不把 R_t 当作采样 y_t 之前的信号。

后续顺序：批准执行预算 → 补全共享校准/导出/评价与审计并冻结源码 →
正常评分及库/阈值哈希 → 只恢复的攻击评分 → 与S/CW/TU匹配FAR对比及独立审计。
通过数量支持与正确性预检查，仅表示这个候选值得进入完整算法实验，不是性能结论。
