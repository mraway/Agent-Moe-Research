# Proposal 1：Finite-Horizon Trajectory Scan（FHTS）实验报告

日期：2026-09-05（America/Los_Angeles）

状态：**Development No-go；在预注册校准支持门停止，未产生性能指标**

预注册提交：`58edc3e41d65b25256ef2fa4143a8ec15fe63b33`

## 主管摘要

FHTS-v1本轮无法完成有效性评价。原因不是已观察到高误报或低召回，而是预注册要求每个leave-one-scenario-out（LOSO）fold至少有100条、长度不短于64 token的正常校准轨迹；实际160条clean/benign-control中只有98条满足64-token观察窗要求。排除held-out scenario后，每个fold只剩96至98条，80/80个fold均不满足校准支持门。

脚本按预注册停止，没有降低最低样本数、缩短窗口、把短轨迹当负例或读取攻击标签校准阈值。因此没有合法的threshold、prediction、FAR、recall、AUROC或latency结果。FHTS-v1的正式判定是`development_no_go_pre_metric`。

这个结果揭示的是**当前数据与冻结校准规则不相容**，不是FHTS机制已被性能实验否定。任何把最低校准数从100事后降至96、对短输出补值或缩短64-token horizon后得到的性能，都必须作为新的proposal重新预注册，不能回填本轮。

## 1. 被验证的算法

算法只读取冻结的单token routing novelty序列`token_endpoint_z`，不使用文本、人工onset、任务、domain、workflow、attack channel、绝对token位置或B3。

对每个候选起点`e`，在token `e+63`生成后计算：

- `A_e = mean(z[e:e+16])`；
- `L_e = mean(z[e+32:e+64])`；
- recovery head：`R_e = A_e - L_e`；
- persistence head：`P_e = min(A_e, L_e)`。

early与late窗口之间的16-token gap不计分。每个候选只在其完整64-token horizon已经观察后作决定，因而是因果、onset-free的扫描。每条轨迹的两个threshold只允许由其他scenario中clean与benign-control的full-path head maxima校准。每个head使用`alpha=0.05`有限样本上侧order statistic；两head合并的名义routine FAR目标为0.10。

首次超过任一threshold时冻结状态：只触发recovery为`recovered`，只触发persistence为`sustained_execution_risk`，同时触发为`ambiguous`。短于64 token的轨迹没有一次完整look，必须作为`censored/no opportunity`报告。

## 2. 执行与完整性

精确执行命令为：

```bash
.venv/bin/python scripts/analyze_agent_v2_fhts.py --execute-routing-analysis
```

执行严格使用预注册提交中的plan、script和tests，没有修改算法、窗口、阈值、cohort或Go门。执行经过以下门后到达阈值校准：

| 项目 | 结果 |
|---|---:|
| replay traces | 240/240 |
| 独立FHTS routing cache | 240/240 |
| 完成`token_endpoint_z`评分 | 240/240 |
| exact-prefix replay | 通过 |
| 冻结正常流形 | 26条clean traces / 208 anchors |
| behavior标签 | 40 execution / 5 bounded-resisted / 35 silent-ignore |
| B3读取 | 0 |

关键冻结输入哈希：

| 输入 | SHA-256 |
|---|---|
| FHTS plan | `6db8aece5fb578c1ee70f88d57e8daea2a36afae58b3ef43fc85aac20c19dfa1` |
| replay sample index | `5399b0b32769c8ef98722080da9ef1f159f586fba6d1d907656afcc8e34eac10` |
| exact-prefix audit | `3405298d0cc5d29a07899f32dc113bb84d08334de51bab6ce1dd442053a41359` |
| engagement summary | `50b890ee4e30ff81855db5547cef53636669a5c4207c6930a969844736fae5e3` |
| behavior-only collection report | `b730343735831d962a3df4d484fd6a6ac21b652c18b5fc569d99dd7b4135b0a3` |
| routing-blind engagement labels | `8ad05186bbe7e508f3019a8c91aa71df8e369922bcdd4562b367bd7f298839d7` |

## 3. 观察机会与删失

FHTS需要至少64个output tokens。自然EOS造成的短输出保持原样，不做padding或负例填充。

| Cohort | 总数 | ≥64 tokens、可扫描 | <64 tokens、无机会 |
|---|---:|---:|---:|
| clean | 80 | 51 | 29 |
| benign-control | 80 | 47 | 33 |
| routine合计 | 160 | 98 | 62 |
| attack合计 | 80 | 67 | 13 |
| └ bounded-engagement resisted | 5 | 5 | 0 |
| └ cross-domain execution | 40 | 39 | 1 |
| └ silent ignore | 35 | 23 | 12 |

按arm的output token数量范围为：clean 19–384（中位数74），benign-control 17–384（中位数78），attack 20–384（中位数194）。所有无机会样本均为自然EOS；所有length-stop样本均至少有64 tokens。

## 4. 停止原因

预注册校准支持门要求：每个held-out scenario的两个head均至少使用100条eligible routine traces校准。

全数据在LOSO排除前只有98条eligible routine controls，因此这个门不可能满足。逐fold的实际校准量为：

| 每fold校准controls | fold数量 |
|---:|---:|
| 96 | 35 |
| 97 | 28 |
| 98 | 17 |

首个fold的冻结异常为：

```text
ValueError: FHTS LOSO fold b2-f0-001-order_and_knowledge-free-verse
has only 96 eligible calibration controls
```

因为80/80 folds均低于100，分析在产生threshold和prediction之前停止。主`result.json`没有被创建；这防止了无效结果被误当成完成实验。

## 5. 全部预注册Go门

| Gate | 门槛 | 结果 | 状态 |
|---|---:|---:|---|
| LOSO校准支持 | 80/80 folds各≥100 controls | 0/80；每fold 96–98 | **失败** |
| routine any-head FAR | ≤0.10 | 未估计 | 因前置门停止 |
| execution correct-state final recall | ≥0.50 | 未估计 | 因前置门停止 |
| recovery correct-state final recall | ≥0.60 | 未估计 | 因前置门停止 |
| engaged pre-onset any-alarm rate | ≤0.10 | 未估计 | 因前置门停止 |
| engaged ambiguous-first-state rate | ≤0.20 | 未估计 | 因前置门停止 |

综合状态：`development_go=false`。此外，虽然39条execution具有观察机会，recovery仍只有5条，低于未来机制确认所需的12条。因此即使算法门能够运行，本批也不能形成机制确认。

## 6. 能与不能得出的结论

可以得出：

- 64-token FHTS与当前短routine输出分布组合后，只有98个正常校准样本；
- 预注册的100-sample LOSO支持要求不可满足，FHTS-v1在本数据上构成程序化No-go；
- 停止规则有效阻止了事后降低门槛来追求一个可发表的性能数字。

不能得出：

- FHTS的FAR高或低；
- FHTS能够或不能识别execution；
- recovery与persistence双head优于或劣于其他proposal；
- 本轮结果否定此前oracle-onset下观察到的recovery轨迹机制。

## 7. 局限与后续处理原则

本轮的直接局限是设计前没有先做与routing无关的output-length可行性核查。100条校准门本意是避免小样本tail threshold不稳定，但现有正常输出长度使该门在数学上不可达。另一方面，直接放宽至96也不能补救确认性地位：本批已被反复用于方法理解，且bounded-resisted只有5条。

若继续研究同一思想，应作为新proposal处理，并在查看其routing性能前冻结以下任一选择：采集更多自然长输出的正常校准轨迹，或使用能够在短回复上作决定的不同horizon。不能在FHTS-v1名下事后改变最低校准数或窗口长度。

## 8. 产物

- 失败审计artifact：`artifacts/agent_v2/fhts_horizon384/failure.json`
- artifact SHA-256：`dc5de12cbd68898ad3847465207f58bec09db1ee0a4d0c608e2ef67cad9fe27a`
- 主管报告：`docs/agent_v2_fhts_report.md`

失败artifact只保存完整性、删失、校准支持和gate状态，不保存或二次计算冻结停止点之后的routing性能指标。
