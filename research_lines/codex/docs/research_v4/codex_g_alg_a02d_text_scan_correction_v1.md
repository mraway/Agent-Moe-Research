# A02-D 跨回合补充：文本覆盖计数更正

2026-09-08，已看跨回合诊断读数后登记。原近邻输出已找到正常日期窗口，但正则覆盖计数为零，
交叉检查发现 `date_segments` 对 `episode.tags` 直接做字符串比较；该字段为tuple，
不是ndarray，因此比较返回标量False，跳过了所有analysis段。

受影响范围仅 `cross_round_v1/result.json` 的 `normal_date_coverage` 文本计数/示例，标为
**invalidated / superseded**。银行数量字段及全部三种库的近邻距离/路由不受影响，
它们使用独立的bank张量、原始query概率和已是ndarray的look tags，不调用文本正则函数。
原规范/源码/source SHA/输出不覆写。

只在新增脚本中将tags转换为ndarray后执行同一分段与正则，增加tuple/list/ndarray一致性、
无日期、跨step不可拼接的测试。新源码/测试/本说明及原result/source文件先SHA冻结，
仅重算相同两个拟合bank的覆盖计数；新产物放
`artifacts/agent_v2/codex_g/alg_a02d_normal_tail_v1/cross_round_text_correction_v1/`。
同时校验全部casecards的近邻窗口ID及各层入选权重，独立重算坐标距离；
不更换query、bank、donor、raw/z/p、标签、阈值或正则，不增加新检测器。

权限仍限原正常数据及缓存、单线程600秒/2GiB、无GPU、不读攻击评分或G-conf。
这是实现修复，不是通过更换正则选择有利结果，也不把更正后的文本覆盖称为新独立证据。
