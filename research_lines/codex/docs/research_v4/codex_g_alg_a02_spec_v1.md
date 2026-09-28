# A02：取消回合硬分库的完整检测消融 v1

2026-09-08。副对话算法线；依据A02-D诊断与A02正常参照支持方案。
本文件及实现/测试在本轮真实数据计算前哈希冻结。G-dev已反复开发，不是确认性预注册。

## 假设与唯一统计改动

正常模式已经出现在其他回合时，同回合硬分库可能产生不必要的异常尾部。
新U_pool/W_pool取消episode_index条件，仅保留message channel。其余沿用A01均值：
全部24层32坐标，实际top4的mask/4或实际入选内softmax，w8、stride1、M7完整V1端点。
以缓存实际IDs为准，不重排bf16并列。固定8次float32相加后开方，全层平均平方Hellinger。
先取每个scenario最小窗口距离，再取三个不同scenario的最小值平均；fit查询也排除自身整个scenario。
同场景各臂/回合不能分别占名额。并集使用拟合折全部filtered正常合格窗口，不按文本/误报抽样。
无日期规则、局部缩放、P、聚类、编码器或额外平滑。只实现均值精确核以节约计算，块64/2048，容差2e-6。

七格固定S/CW/TU/U_mean/W_mean/U_pool/W_pool，前五格重放已冻结A01的raw/z/p。
主比较W_pool−S，5%filtered正常预算下匹配S实测filtered FAR。
次级W_pool−W_mean、U_pool−U_mean、W_pool−U_pool、W_pool−CW/TU及U_pool−S。
不得用次级最高者替换失败主比较；U/W差异不解释为权重的因果作用。

## 校准、评价与停止

仍为fixture_rank_mod三折：评价k、拟合k+1、C1参照k+2。各scenario全部同折。
新两格正常标准化与C1重新拟合：共享calibrate_g/score_episode，全路径最大值，通道位置桶32，
fit仅filtered且排除整个query scenario；C1仅filtered正常。数值阈值不能沿用旧均值格。
正常408/filtered293，冻结全部可达k/(n+1)加0网格及工作点后才开本轮混臂评分/攻击缓存。

名义、filtered经验预算、filtered匹配及all匹配四种模式各报.10/.05/.01/.001。
all匹配已在本轮预设：目标取S在相应filtered预算工作点上的all FAR，七格各取不超此值最大alpha，
包括S本身；保留离散缺口。双分母同时报告，不凭filtered改善宣称整体胜出。
1%filtered预算固定为硬诊断；alpha=0关闭和不可达折保留正例分母，不重选预算。

完整784个G-dev episode、全部126个X正例；首报在[E_view,X+16]，pre-E记漏检。
静默仅注入后40；注入前88、合法拒绝24、攻击过拒53/全臂过拒59分别报告。
family与family×tier bootstrap2000、seed20260907，精确McNemar；正常差按scenario bootstrap。
增益门要求16家族、主差家族CI下界>0且McNemar p<.05；风险门用A01更正版，F4仍为硬范围门。
同批正常选点是数据依赖开发口径，区间条件于选点；不是未来流量低误报保证。
时效记录X前/截至X及各延迟；路由在处理已采样token时才可见，不宣称采样前预测。

## 执行及独立审计

独立输出alg_a02_pooled_reference_v1；不改旧冻结文件、机制线、Claude或共享harness，不做Git操作。
源码/测试/本文件及依赖哈希冻结，HEAD只作来源。M11已完整报告且审计PASS，作为本轮机制快照：
它支持完整路由包含额外上下文变化，也显示正常背景变化；不据此再改本轮单一消融。
只读报告与审计摘要并记录SHA，不重算机制或挑选M11子集。

CPU单线程、无GPU/模型/新生成；预检查+正常校准+恢复评分+独立审计总预算600秒、峰值2GiB。
正常预检查每折/通道全库，查询为该折字典序首个filtered正常的前64个合格look。
成本估计为1.5×(按各条件实测速率推算正常全量搜索+190284个全评价look×最慢条件速率)
+120秒I/O/校准/审计余量+已用预检查时间；超预算先报告，不采样/ANN/放宽限制。
真实执行亦逐查询/阶段检查剩余预算。总预算不含代码开发与合成测试；超限失败记录保留。

阶段：freeze→正常preflight→正常calibrate及阈值SHA→score只恢复库/校准→audit。
正常守卫拒绝攻击缓存、M7/A01/A02混臂评分产物。全程CodexGAccessGuard，非OS隔离。
G-conf任何数据/标签/封存清单不读；G-session/G-medium等不进入本轮。
只恢复评分以tripwire禁止新建库或拟合；408正常重放逐值一致，旧五格逐值一致。
按dataset-g-look-scores-1.0.0导出已经形成的raw look，不二次平滑。
审计重建全部正常库的float64坐标与键/端点，逐look检查三scenario及whole-scenario排除；
每折/通道/角色字典序首查询的首末look独立枚举全部donor距离，阈值2e-6，近并列不要求行号位级相同。
独立复核p、首报、全分母、比较/区间、阈值和源/输入/输出SHA。风险报表的审计状态单列补充，不覆写旧结果。
不自动启动下一补丁；如果此修订仍无稳健性能/时效/互补价值，先交主管review近邻族。
