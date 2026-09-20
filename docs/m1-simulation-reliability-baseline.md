# M1：Simulation Reliability Baseline

M1 是进入 CMA-ES 优化前的强制门槛。完成标准是“仿真世界可解释、可手算、可复现、可审计”，不是某个基准策略能够结束运行。

## 1. 交付物

- `docs/simulation-contract.md`：带版本和状态的仿真契约。
- `docs/pyscfabsim-semantic-diff.md`：沿调用链形成的 PySCFabSim 语义差异报告。
- `docs/data-contract.md`：本地字段、内部结构、运行时语义、来源证据和显式假设。
- `tests/fixtures/micro_cases/`：确定性微型算例。
- 统一的 `DispatchPolicy.select(state, feasible_actions)` 接口。
- FIFO、SPT、EDD、CR 的实现及统一平局规则。
- 可独立重算资源占用、批容量、CQT、绑定和指标的日志检查器。

## 2. 通过条件

以下条件必须全部满足：

1. 本地数据字段语义表完成，所有影响正式结果的 `OPEN` 项已解决或被明确排除出正式模型。
2. PySCFabSim semantic diff 完成，不能用“读取了字段”代替事件行为证据。
3. 仿真契约冻结并记录版本。
4. 下列 8 个手算算例全部通过；实际事件序列与期望序列一致。
5. Batch、Setup、CQT、Dedication、Failure/PM 各有独立验证。
6. FIFO、SPT、EDD、CR 使用相同候选生成、可行性检查和动作提交器。
7. 相同场景、代码、契约和种子严格复现；跨策略使用实体索引的共同随机场景。
8. 指标同时包含 completed lots 和 terminal WIP；独立重算与引擎汇总一致。
9. 事件日志通过资源守恒：`initial + released = completed + WIP + explicit_removed`。
10. `simulate(theta, scenario, seed)` 接口存在，但优化命令在 M1 标志通过前拒绝运行。

## 3. 金标准微型算例

所有算例内部单位为分钟；除非特别声明，加工时间确定、搬运为 0、设备无故障、lot 数量为 25 wafers。

### MC01：工艺顺序

```text
L1: release=0
O1 → M1, 10
O2 → M2, 20
```

期望：O1=`[0,10]`，O2=`[10,30]`，`completion(L1)=30`。O2 不能早于 O1 完成。

### MC02：动态投放与 FIFO

```text
M1
L1: release=0, process=10
L2: release=5, process=10
```

期望：L1=`[0,10]`，L2=`[10,20]`。L2 在 t=5 前不能进入候选队列。

### MC03：有向 Setup

```text
M1 initial_recipe=A
L1: recipe=A, process=10, release=0
L2: recipe=B, process=10, release=0
setup A→B=5
```

期望：L1=`[0,10]`，setup=`[10,15]`，L2=`[15,25]`；setup 占用 M1 且只计一次。

### MC04：Batch 单位、合法容量与等待

```text
B_min=125 wafers, B_max=150 wafers, B_target=150 wafers
lot_size=25 wafers, max_wait_after_feasible=5
batch_process=20
```

三个子例：

- 4 lots=100 wafers：始终不能启动，即使等待超时。
- 5 lots：在形成125 wafers后开始等待，第5分钟仍未达到150时启动5-lot batch。
- 6 lots 同刻可用：立即启动满批。

期望：任何事件和日志都不能把 wafer 容量误写为 lot 容量；同批成员共起共止。

### MC05：跨步 CQT

```text
L1: O1=10, O2=5, O3=10
CQT: O1 finish → O3 start, limit=8
```

- O2 完成后额外等待2分钟：O3在17开始，elapsed=7，violation=0。
- O2 完成后额外等待4分钟：O3在19开始，elapsed=9，violation=1。

期望：CQT 不依赖 O1 与 O3 是否相邻；中间加工和等待均计时。

### MC06：具体设备 Dedication

设备组 G 有 M1、M2。L1 在绑定起点由 M2 加工，未来指定 step 到达时 M1 空闲、M2 忙碌。

期望：L1 等待 M2，不得因 M1 空闲而改派；指定终点完成后对应绑定释放。

### MC07：故障、恢复与同刻屏障

场景显式指定 `preemptive-resume`：

```text
L1: M1 process=10, start=0
failure(M1)=5, repair_duration=3
```

期望：L1 在 t=5 剩余5分钟，t=8继续，t=13完成，没有旧完成事件导致的 t=10 重复完工。

在 t=13 同时安排 `PROCESS_FINISH(L1)`、`FAILURE_START(M1)`、`LOT_RELEASE(L2)`：期望 L1 先完成，故障随后生效，L2 被释放但不能在维修前派到 M1。PM 冲突另用相同结构验证其最终契约。

### MC08：期末暴露和 WIP 积分

固定终点 `H=30`，两台独立设备：

```text
L1: release=0, due=15, process=10 on M1
L2: release=0, due=20, process=100 on M2
```

期望：

```text
completed_lots=1
throughput_lots=1/30
completion_ratio=1/2
mean_cycle_time_completed=10
cycle_time_coverage=1/2
terminal_wip_lots=1
remaining_work=70
lateness_exposure=10
mean_wip=(2*10 + 1*20)/30 = 4/3
```

L2 不能因为未完成而从结果中消失。

## 4. PySCFabSim 审计判据

每项机制沿以下链路检查：

```text
源数据读取 → 领域/状态变量 → 事件或状态转移 → 日志/指标影响
```

结论只能使用：`一致`、`可适配`、`结构性冲突`、`缺失`、`证据不足`。如果代码只读取字段但没有进入事件或状态转移，结论是“缺失”或“证据不足”。

复用内核的最低条件：事件日历、lot release、路线推进、machine state、processing event、queue、dispatch hook 和 batch framework 与契约一致或局部可适配。若 batch 实体、跨步 CQT、具体设备绑定、故障抢占或 qualification 存在深层耦合冲突，则自行实现轻量 DES。

## 5. M1 之后

只有 M1 状态设为 `passed` 后，才进入：

```text
M2 — CMA-ES Policy Optimization
```

M2 首先优化低维可解释参数；PPO、GNN、AMHS 不因 M1 完成而自动进入范围。
