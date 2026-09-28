# A01 preflight v1.1：仅修复报表序列化

2026-09-08。继承 [A01 v1 规范](codex_g_alg_a01_spec_v1.md) 的所有算法、数据选择、
工作点和资源规则。本次不改数学模块，不改 v1 已冻结的源码和产物。

v1 首次运行的 source manifest SHA256 为
`6a09bbdd9680cffc456aef11721cc6be899876b8af2aaab5127d5309773c41c7`。
408 个正常 episode 的端点和三折库支持检查通过；18 个固定 smoke 条件完成，
但 `normal_stage_looks` 是 NumPy int64，JSON 输出最后失败，留下 failure.json 和空 result.json。
该次不能作为完整已审计实验，原输出保留，不覆盖、不删空文件，也没有计算 FAR/recall。

修复严格限于：

- JSON 输出将 NumPy 标量显式转为 Python 标量，并在开文件前完成序列化，避免错误创建空文件；
- 新入口复用原 runner 和数学实现，仅为当前 Python 进程替换输出根、序列化函数和源清单扩展；
- 新输出根 `artifacts/agent_v2/codex_g/alg_a01_joint_neighbors_v1_1/`，仍拒绝覆盖；
- 源清单额外冻结本说明、v1.1 入口和 v1.1 回归测试；原依赖清单与逐文件检查不放宽。

v1 的18项测试继续保留；新增序列化、异常不创建文件、拒绝覆盖测试。
重新执行同样的正常-only preflight，重复测时仅为修复输出后归档，不以快慢选择某次结果。
仍按保守成本估算决定是否触发 600 秒/2 GiB 资源暂停门，不据已看正常距离改变算法。
