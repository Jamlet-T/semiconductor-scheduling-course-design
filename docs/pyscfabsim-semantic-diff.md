# PySCFabSim Semantic Diff

状态：静态源码审计完成，行为实验待补。  
对照基线：[Simulation Contract](simulation-contract.md)。

本报告不以目录名、字段名或 README 声明代替行为证据。每一项沿“数据读取 → 状态变量 → 事件触发 → 指标结果”检查。

## 1. 审计对象

| 项目 | 值 |
| --- | --- |
| 仓库 | `prosysscience/PySCFabSim-release` |
| 分支/Commit | `master` / `0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a` |
| License | MIT |
| 审计方式 | 固定 commit 静态调用链；未安装依赖、未运行仿真 |

状态含义：`一致`表示静态行为满足契约；`可适配`表示核心结构可用但要局部修改；`结构性冲突`表示会改变内核实体或事件语义；`缺失`表示核心链路没有该行为；`证据不足`表示必须补最小行为实验。

## 2. Semantic Diff

| 机制 | PySCFabSim 行为证据 | 结论 | 处理方案 |
| --- | --- | --- | --- |
| 数据读取 | `simulation/read.py:9-58` 读取制表符文本并转换数值 | 可适配 | 补 `tool.txt.1l`、原始行号和严格 schema 验证 |
| Lot release | `file_instance.py:39-63` 从订单重复参数构造 lot；`events.py:22-39` 将 lot 移入 active | 可适配 | 对照本地 `START/DUE` 绝对时间语义；禁止预展开超长 `RPT#` |
| Route progression | `classes.py:80-151` 保存 route；`instance.py:74-107` 推进步骤并处理抽样/返工 | 可适配 | 增加 `(route, step, visit)` 和可审计事件日志 |
| Tool group→具体 tool | `classes.py:23-38` 保存 family/group；两个 dispatch manager 按 family 关联可用设备 | 可适配 | 统一中央动作提交与稳定平局，验证 qualification 范围 |
| Batch min/max 单位 | `classes.py:105-112` 用全局 `pieces_per_lot` 将 wafer 数除成 lot 数 | 结构性冲突 | 内核统一保存 wafer 容量，逐 lot 求和；不得用最大 lot size 全局换算 |
| Batch 启动 | `greedy.py:22-49` 的 lot-for-machine 要求达到 `batch_max`，`batch_min`只参与排序；`greedy.py:65-73` 的另一方向最多取 `batch_max`，不检查 `batch_min` | 结构性冲突 | 自行实现统一合法候选与启动门槛；低于 `B_min` 一律禁止 |
| Batch compatibility | 设备优先模式按 `step_name` 分组 | 证据不足 | 对照 `crit_sameroutestep`，用 route+step 显式兼容键做行为测试 |
| Setup | `instance.py:158-195` 计算 setup 并加到完成时刻；`instance.py:147-156` 只创建 machine/lot done event | 可适配 | 本项目需要独立 setup 状态/事件和明确的转移优先级，不能只加总时间 |
| 跨步 CQT | `classes.py:116-118` 读 `STEP_CQT/CQT`；`instance.py:114-126` 登记与检查 | 结构性冲突 | 源码把相对 `cqt_time` 直接与绝对 `current_time` 比较，疑似时钟错位；改为独立约束和绝对 deadline |
| Dedication | `instance.py:137-140` 将指定 step 映射到 machine ID；dispatch manager 检查该绑定 | 可适配 | 补 visit 生命周期、初始 WIP 缺失绑定和独立测试 |
| Failure | `events.py:41-71` 触发停机；`instance.py:205-213` 移除设备完成事件、延后后重新入队 | 可适配 | 行为接近 preemptive-resume；补旧事件版本、lot状态和同刻屏障测试 |
| PM | `file_instance.py:64-99` 从日历和 attach 构造；`events.py:53-71` 重排下一事件 | 证据不足 | 静态代码含按日历/加工片数路径；仍需验证与在制加工冲突及空 `FOAUNITS` 语义 |
| Transport | `file_instance.py:22-36` 读 from/to；`instance.py:158-172` 将运输时间加到 lot 完成时间和统计，不占 machine | 可适配 | 主线同样不建运输资源；改成可见的 TRANSPORTING 状态和到达事件 |
| Dispatch hook | `instance.py:108-157` 派工并调用插件；事件中有释放、完成、故障等回调 | 可适配 | 包装统一 `DispatchPolicy`，候选生成与动作可行性移出策略 |
| 终止条件 | `instance.py:215-217` 以无待释放/active lot 判完成；`greedy.py:138-156` 另有 `run_to` 截止 | 可适配 | 正式实验固定 horizon；微型算例可 all-complete；期末必须快照 WIP |
| 随机流 | `randomizer.py:4-16` 使用单例 `random.Random`；所有分布共享同一顺序流 | 结构性冲突 | 按 stream/entity/occurrence 派生稳定子流，支持共同随机场景 |
| 完成结果指标 | `stats.py:8-50` 在 `done_lots` 上计算 throughput、ACT、tardiness、按时率 | 可适配 | 明确 lot/wafer单位、观察窗与 cohort；保留 completed block |
| Terminal WIP | `stats.py:16-28` 只遍历 `done_lots`，未汇总期末 active lots | 缺失 | 新增 completion ratio、terminal WIP、remaining work、exposure 和 mean WIP |

## 3. 关键源码链路

- 数据构造：[`simulation/file_instance.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/file_instance.py)
- 状态与派工：[`simulation/instance.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/instance.py)
- Lot、Step、Machine：[`simulation/classes.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/classes.py)
- 事件处理：[`simulation/events.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/events.py)
- 基准派工：[`simulation/greedy.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/greedy.py)
- 指标：[`simulation/stats.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/stats.py)
- 许可证：[`LICENSE`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/LICENSE)

## 4. 复用判定

当前不建议直接以 PySCFabSim 原内核作为本项目正式仿真器。理由是 Batch 最小容量在两种 dispatch 方向下语义不一致，CQT 时钟存在相对/绝对时间混用迹象，随机数只有全局顺序流，期末 WIP 指标缺失；这些都位于本项目核心实验口径。

建议采用“参考实现 + 行为对照”模式：

1. 参考其数据解析、事件队列、route progression、dedication、downtime/PM 和插件接口；
2. 自行实现轻量事件内核中的统一屏障、wafer-based batch、独立 CQT、随机子流与终态指标；
3. 用 FIFO/CR 的最小行为实验比较两者，差异逐项归因，不强求数值完全相同；
4. 如复用 MIT 代码片段，保留版权和许可证通知，并在报告中列明来源与修改。

静态审计尚不能确认 PM 冲突、首工序搬运和全部分布参数。M1 仍需行为实验或原始格式说明才能关闭这些 `OPEN` 项。
