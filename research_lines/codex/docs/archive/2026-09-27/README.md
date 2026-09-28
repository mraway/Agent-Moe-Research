# 项目结项与源码归档

> 此文记录的是本地归档范围，不是 GitHub 上传清单。GitHub 采用更严格的代码/文档净化快照，不携带本文提到的旧 Git 历史、数据、PDF、检测器权重或本地 JSON 审计资产。实际发布范围见[归档首页](../../../../../README.md)与根目录 HANDOFF_MANIFEST.json。

日期：2026-09-27。主管决定：团队后续验证后认为项目不值得继续投入，停止推进，
保留代码与研究文档，并在远端归档核验成功后释放本机大文件占用。

这是一项资源与研究方向决策，不是新增的统计检验，也不等于证明所有 MoE 监测方案无效。
本轮仅做归档、完整性核验和清理，不重新分析数据，不把尚未证实的假设改写成研究结论。

## 1. 从哪里开始阅读

| 内容 | 入口 | 阅读边界 |
|---|---|---|
| 研究问题、历史探索与交接 | [研究交接](../../moe_agent_task_shift_research_handoff_v2.md)、[生产目标](../../production_moe_routing_shift_research_brief.md) | 历史计划，不代表项目继续推进 |
| gpt-oss / Agent v3 研究定位 | [研究纲领](../../research_v4/gpt_oss_research_program.md)、[G 入门包](../../research_v4/dataset_g_onboarding_for_codex.md) | 旧模型结论仅作假设；注意每轮口径和后续修订 |
| Codex 机制探索 | [分工说明](../../research_v4/codex_g_mechanism_workstream.md)、[M8](../../research_v4/codex_g_m8_report.md)、[M15](../../research_v4/codex_g_mech_m15_report.md) | M1–M8 与 M9–M15 各有冻结规约、实现、审计及报告；以每份报告的限制为准 |
| Codex 算法探索 | [实验计划](../../research_v4/codex_g_algorithm_experiment_plan.md)、[A03-R](../../research_v4/codex_g_alg_a03r_report.md) | 开发探索与正式确认不能混用；主门失败不得由次级读数替换 |
| 隐藏激活重采验证 | [计划](../../research_v4/codex_g_hidden_replay_smoke_plan.md)、[报告](../../research_v4/codex_g_hidden_replay_smoke_report.md) | 小样本工程验证，不是隐藏激活检测基线的性能实验 |
| 早期 OLMoE / Agent v2.x | 根目录 README 的历史部分、`docs/research_v2/`、`docs/research_v3/` | 为新模型生成假设，不直接迁移为 gpt-oss 证据 |

主要实现：`src/agent_v2/`、`src/agent_v3/` 为 agent 与数据工厂；`src/routing/` 为路由采集；
`src/research_v2/` 与 `scripts/research_v4/` 包含统计量、harness、机制/算法实验和审计；
`tests/` 为对应测试。原始实验批次并未随源码发布，依赖这些批次的测试或分析不能直接在空数据环境复跑。

## 2. 并行分支不得合并成一条虚构的实验历史

归档整理前的五个分支如下。完整提交哈希另见 [branch_inventory.json](branch_inventory.json)。

| 分支 | 整理前 tip | 内容/处理 |
|---|---|---|
| `main` | `ba40ce87e374` | Codex 主线；归档提交另纳入此前未提交的代码、文档、配置与小型标注 |
| `claude/algorithm-research-proposals-427363` | `0c90f5be0b21` | Claude 后续工具链与报告；保留原分支，不修改其 worktree |
| `worktree-wf_6ba1b359-6f0-2` | `47191352ccb8` | 保留历史分支 |
| `worktree-wf_6ba1b359-6f0-3` | `372cc5e2b6a8` | 保留历史分支 |
| `worktree-wf_6ba1b359-6f0-4` | `5fabd9725f92` | 保留历史分支 |

整理前 main 与 Claude 分支分别有 51 / 23 个独有提交。不能用 fast-forward 同步，
本次不 merge、不 rebase、不 reset，也不强推覆盖远端。Claude 分支上的
`docs/research_v4/g_conf_confirmatory_report.md`、`detector_prereg_v3_3.md` 和 G-conf-2
相关文档，应在其原分支查阅，不冒充 main 同版本的结论。

四个 Claude worktree 的只读状态检查均无未提交的已跟踪修改；唯一未跟踪项是指向共享
`artifacts/` 的符号链接。归档不沿该链接打包实验数据。后续如状态变化，必须重新盘点。

## 3. 保存什么，不保存什么

保存：所有分支的 Git 历史；main 的未提交研究代码、文档、测试、配置和小型标注；
既有小型合成场景/配置；既有冻结检测器参数（合计远小于 1 MiB，不是 LLM 权重）；
文档中的审计、失败结果及限制。`reports/` 保存原先未跟踪的三份 PDF，
`legacy_packet_validation.json` 保存原根目录的聚合 packet 检查结果（历史输出，不是本轮重算）。

不上传：LLM 权重及下载缓存、原始 agent 轨迹、逐 token 路由/隐藏激活、完整生成数据集、
实验中间缓存、虚拟环境、助手会话/认证文件和嵌套 worktree 目录。
本轮只对 G-conf / G-conf-2 做目录与尺寸盘点，不打开其原始轨迹、路由或标签内容。

清理后的恢复能力必须区分：

- 源码、文档与历史：远端成功备份后可恢复；本地另留 Git bundle 作为额外恢复副本。
- 模型：通常可以按记录的版本重新下载，仍取决于上游可用性和许可证。
- 实验原始数据：删除后本仓库没有完整副本；代码、种子和配置不保证重建完全相同的结果。
- 虚拟环境：保存关闭时的 [依赖快照](requirements-closeout.txt)，不保证未来平台仍可安装所有轮子。

未对项目新增开源许可证，也不改变现有远端仓库的可见性。代码可读不自动代表获得所有第三方资产的再分发许可。

## 4. 环境与尺寸快照

关闭时本机：Python 3.12.3；Linux `6.18.33.2-microsoft-standard-WSL2`，x86_64。
依赖快照来自现有 `.venv` 的 `pip freeze --all`，不覆盖历史实验的 `requirements-lock.txt`。
各实验实际模型版本、配置、核验哈希仍以各自冻结文件与报告为准。

下表是归档前 `du -x -B1` 的实际分配量，不是 GitHub 上传量，也不是保证能立即归还 Windows 的空间。

| 项目 | 分配字节数 | 约合 GiB |
|---|---:|---:|
| 整个检出（含数据/模型/环境） | 137,943,085,056 | 128.47 |
| `artifacts/hf_cache/` | 90,702,278,656 | 84.47 |
| `artifacts/agent_v2/` + `phase_a/` + `routing_smoke/` | 41,366,720,512 | 38.53 |
| `.venv/` | 5,767,151,616 | 5.37 |
| 其余（含代码、文档、Git、四个小型 worktree 和 PDF） | 106,934,272 | 0.10 |

大项可释放约 128.37 GiB；保留少量已跟踪的聚合审计文件后，实际值略小。
WSL 内部删除与 Windows 的 `ext4.vhdx` 缩小是两件事。若 C 盘空间未相应增加，
需要另行安排停止 WSL 后压缩虚拟磁盘；本轮不会自动关闭其它 WSL 会话，更不能删除 VHD 或注销发行版。

## 5. 上传与清理的硬门

1. 汇总源码/文档及各分支，检查意外大文件与常见凭据特征；生成文件 SHA-256 清单。
   这种扫描降低误上传风险，不是完整安全审计。
2. 生成并验证包含全部分支的本地 Git bundle。bundle 不能代替远端备份。
3. 确认目标 GitHub 仓库和权限，非强制推送归档提交及全部保留分支。
   若远端已有分叉或同名不同内容，停下协调，不覆盖。
4. 从远端重新获取到独立检出/裸库，核验分支 tip、对象完整性及源码文件清单。
   仅“push 命令返回成功”不算完成核验。
5. 确认没有仍在写入这些产物的实验进程，再对明确列出的模型/产物/环境目录先 dry-run 后清理。
   保护已跟踪文件；不删除检出根、Git 历史或 Claude worktree。
6. 记录删除目标、前后磁盘读数、不可恢复的原始数据范围，以及 WSL 是否仍需离线压缩。

本地整理的可机读记录：[源码 SHA-256 清单](source_manifest.json)、
[尺寸与常见凭据特征扫描](archive_audit.json)。清单不包含自身及派生审计记录，避免循环哈希；
扫描只覆盖拟归档源码及整理前 Git 对象，不读取被排除的原始实验数据。

归档时 `python -m compileall -q src scripts tests` 通过；未重新运行研究实验或宣称全套测试通过。
`git diff --cached --check` 在五份原有未跟踪脚本/测试中报告文件末尾空行，
归档保留原字节，未为消除格式提示而改变冻结源码及其历史哈希。

**本说明记录流程与保留边界，不是远端上传或数据删除已经完成的证明。**
如果 GitHub 认证/访问受阻，保留全部原始目录，待远端核验通过后才执行第 5 步。
