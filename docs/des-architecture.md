# 可信轻量 DES 架构

适用版本：Simulation Contract `0.1.0`  
当前能力：Basic DES + 显式 Setup + 显式 Batch + 跨步 CQT，已验证 MC01～MC05

锁定能力：Dedication、Failure/PM、正式 SMT2020 loader

## 1. 模块边界

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 场景领域模型 | `domain/models.py` | `MachineSpec`、`LotSpec`、`OperationSpec`、`Scenario` 及加载前校验 |
| 事件定义 | `simulation/events.py` | `Event(time, priority, seq, ...)`、契约优先级、审计 trace |
| 事件内核 | `simulation/engine.py` | 日历推进、状态机、统一派工屏障、路线推进、终止和指标 |
| Setup resolver | `simulation/setup.py` | 按冻结优先级唯一解析换型时长；缺失时明确失败 |
| Batch formation | `simulation/batch.py` | compatibility 分组、wafer 容量、确定性成员选择与 timeout 决策 |
| CQT runtime | `simulation/cqt.py` | 独立约束索引、活动时钟、闭合记录、slack/risk 与期末暴露 |
| 随机流 | `simulation/random_streams.py` | 由 seed/stream/entity/occurrence 派生随机量 |
| provenance | `simulation/provenance.py` | Contract、数据、commit、seed、配置、策略和终止条件 |
| 派工接口 | `policies/base.py` | `DispatchPolicy.select(state, feasible_actions)` |
| FIFO | `policies/fifo.py` | 当前工序入队时刻优先，实体 ID 稳定打破平局 |
| 数据身份 | `data/identity.py` | 对数据文件路径及原始 SHA-256 生成 manifest hash |

## 2. 事件推进

```mermaid
flowchart LR
    A[Event Calendar] --> B[弹出最小 time priority seq]
    B --> C[更新 Lot / Machine 状态]
    C --> D{同刻还有更高优先级事件?}
    D -- 是 --> B
    D -- 否 --> E[DISPATCH_BARRIER]
    E --> F[生成可行动作]
    F --> G[策略选择]
    G --> H[中央原子提交]
    H --> I{普通工序或 Batch?}
    I -- 普通工序 --> J{需要 Setup?}
    J -- 是 --> L[SETTING_UP / SETUP_FINISH]
    L --> K[PROCESSING / PROCESS_FINISH]
    J -- 否 --> K
    I -- Batch --> M[BATCH_FORMED / BATCH_START]
    M --> N[BATCH_FINISH / 全体成员推进]
    N --> A
    K --> A
```

`Event` 的可比较字段只有：

```text
(time, priority, seq)
```

`entity_id`、`payload` 和枚举值不参与排序。`seq` 由单调计数器生成，因此不依赖 dict/set 的隐式顺序。

Basic DES 当前启用：

```text
PROCESS_FINISH / SETUP_FINISH / BATCH_FINISH priority=10
LOT_RELEASE priority=40
BATCH_TIMEOUT priority=50
DISPATCH_BARRIER priority=60
```

同一时刻先处理全部完成，再处理投放，最后在统一状态快照上派工。

## 3. 状态机

```text
Lot:
UNRELEASED → QUEUED → RESERVED → PROCESSING → QUEUED ... → COMPLETED

Machine:
IDLE → PROCESSING → IDLE
IDLE → SETTING_UP → PROCESSING → IDLE
IDLE → PROCESSING(active_batch_id) → IDLE
```

派工提交后，lot 与 machine 先被原子保留。内核集中调用 `SetupDurationResolver`：

```text
无 setup 要求或 current == required → 0
operation.setup_override_minutes
→ exact (current_setup, required_setup)
→ initial fallback ("", required_setup)
→ SetupResolutionError
```

正时长 setup 产生独立 `SETUP_START` trace 和 `SETUP_FINISH` 日历事件；完成后先更新 `machine.current_setup`，再对已保留 lot 发出 `PROCESS_START`。FIFO 只选择可行动作，不包含 setup 判断。

Batch 路径分为三层：

```text
Eligible: 当前 operation 可由 machine 加工
Compatible: crit_sameroutestep，即 route_id + step_id 相同
Selected: 按 queue_entered_at、lot_id 稳定排序，累计 wafer 不超过 B_max
```

`BatchFormation` 先检查 `B_min`，再检查 `B_target` 或真实最老等待是否达到 `T_max`。达到合法最小容量但尚未达到目标时，内核安排 `BATCH_TIMEOUT`；该事件仅请求重新评价，不直接启动。token 已失效的 stale timeout 记录为无副作用事件。Batch ID 使用仿真内单调序号 `BATCH-000001`。

一个 Batch 只产生一次物理 `BATCH_START/BATCH_FINISH` 和一个 `BatchInterval`。成员共享起止时刻，每个 lot 的 `PROCESS_START/PROCESS_FINISH` 通过同一 `batch_id` 关联。machine 使用普通 `PROCESSING` 与 `active_batch_id` 表示批占用，不增加重复的 BATCHING 状态。

CQT 在领域层使用 `CQTSpec(constraint_id, route_id, source_step_id, target_step_id, max_duration_minutes)` 独立建模。运行时以 `(lot_id, constraint_id, visit_index)` 索引 `ActiveCQTClock`：source 的真实 `PROCESS_FINISH` 后记录 `CQT_OPEN`，target 的真实 `PROCESS_START` 后记录 `CQT_CLOSE`；超限时追加 `CQT_VIOLATION`，但不改变 feasible actions。Setup、排队、中间加工以及后续搬运只要处于这两个物理时点之间，都会自然计入 duration。FIFO 不读取 CQT。

CQT 不安排 deadline 日历事件，也不强制派工或抢占。活动时钟按需返回 `slack=limit-(t-opened_at)` 与 `risk=(t-opened_at)/limit`。fixed horizon 时，仍开放的时钟进入 `TerminalCQTSnapshot`，并将 `max(0, horizon-deadline)` 单列为 terminal exposure。

lot 完成一道工序时：

1. 记录 `PROCESS_FINISH` 和设备占用区间；
2. 设备回到 `IDLE`；
3. 若有下一工序，记录 `ROUTE_ADVANCE` 并进入其队列；
4. 否则记录 `LOT_COMPLETE`；
5. 安排同刻 `DISPATCH_BARRIER`。

策略只接收已经通过资格过滤的 `DispatchAction`。内核按 machine ID 稳定遍历，并在每次选择后立即原子预留 lot 和 machine，防止一个 lot 被多台设备重复占用。

## 4. 终止

- `until_all_complete`：所有场景 lot 完成；事件日历提前耗尽时抛出 `SimulationError`，不返回伪完成结果。
- `fixed_horizon`：处理所有 `time <= horizon` 的事件，在 horizon 截断并保留 terminal WIP。

MC01～MC05 使用 `until_all_complete` 或算例显式 fixed horizon；独立测试验证 horizon 截断正在进行的 setup、batch，以及开放 CQT 的安全/超期状态。

## 5. Trace 与结果

完整 trace 记录：

```text
run_id, event_seq, sim_time, priority, event_type,
lot_id, visit_index, route_id, step_id,
machine_id, tool_group_id, batch_id,
batch_member_lot_ids, batch_member_wafers,
batch_total_wafers, batch_start_reason,
cqt_constraint_id, cqt_source_step_id, cqt_target_step_id,
cqt_limit, cqt_opened_at, cqt_deadline, cqt_closed_at,
cqt_actual_duration, cqt_slack, cqt_violation, cqt_excess_duration,
state_before, state_after, cause_event_seq
```

`key_trace()` 保留人工核算所需的 `LOT_RELEASE / DISPATCH / SETUP_START / SETUP_FINISH / BATCH_TIMEOUT / BATCH_FORMED / BATCH_START / BATCH_FINISH / PROCESS_START / PROCESS_FINISH / CQT_OPEN / CQT_CLOSE / CQT_VIOLATION / ROUTE_ADVANCE / LOT_COMPLETE`。完整 trace 仍包含 `DISPATCH_BARRIER` 和 `BATCH_TIMEOUT_STALE`。

当前 KPI：

- completed/released lots；
- completion ratio；
- completed lot mean cycle time；
- throughput lots/min；
- terminal WIP；
- simulation end time。

CQT 结果另含 closed count、已闭合 violation count、total/max excess、open count、overdue open count 与 terminal exposure。已闭合违规和期末仍开放且超期的窗口分开统计，CQT 不改变生产 KPI 定义。

`MachineStatistics` 分开记录 `processing_time`、`setup_time` 和 `idle_time`。固定 horizon 截断正在进行的 setup 时，已占用部分只累计到 `setup_time`；不会进入 `processing_time`。Lot cycle time 仍为 `completion-release`，因此实际经历的 setup 会自然计入。

`BatchInterval` 记录 batch identity、machine、成员及 wafer 数、route/step、启动原因和物理起止。fixed horizon 截断活动 batch 时使用 `ActiveBatchSnapshot` 保留成员、开始和计划完成时刻，成员继续计入 terminal WIP。

每个结果始终包含 `simulation_contract_version`、`dataset_version`、`git_commit`、`seed`、配置摘要、策略和终止条件。

## 6. 当前验证边界

| 机制 | 状态 | 自动化证据 |
| --- | --- | --- |
| Event heap 的 time/priority/seq | VERIFIED | 同刻优先级与 seq 测试 |
| 动态 release | VERIFIED | MC02 |
| FIFO queue | VERIFIED | MC02 |
| route progression | VERIFIED | MC01 |
| machine 占用区间 | VERIFIED | MC01～MC05 |
| all-complete termination | VERIFIED | MC01～MC05 |
| fixed horizon | VERIFIED | 普通加工、Setup、Batch terminal WIP 测试 |
| 实体索引随机流 | VERIFIED | 调用顺序独立性测试 |
| provenance | VERIFIED | 必填字段测试 |
| Setup resolver 优先级与缺失错误 | VERIFIED | resolver 单元测试 |
| Setup 状态、事件、占用与 setup identity | VERIFIED | MC03 |
| MC03 确定性与 Setup provenance | VERIFIED | 重复运行与配置断言 |
| Batch wafer 容量与 compatibility | VERIFIED | MC04 与边界测试 |
| Batch timeout、stale token、同刻排序 | VERIFIED | MC04 timeout 测试 |
| Batch fixed horizon 与单次机器占用 | VERIFIED | active batch 快照与统计测试 |
| CQT 跨步开闭、精确期限与软约束 | VERIFIED | MC05 两个金标准变体 |
| 多 CQT 时钟、slack/risk 与非法重复开闭 | VERIFIED | CQT runtime 单元测试 |
| CQT fixed horizon 开放暴露 | VERIFIED | safe/overdue terminal 测试 |
| Setup/Batch 的实际 PROCESS 事件 CQT 钩子 | VERIFIED | 组合边界测试 |
| Dedication、Failure/PM | LOCKED | 不得进入运行路径 |

MC06 之后的机制必须继续沿用现有 Event、TraceRecord、DispatchPolicy 和 SimulationResult 边界，不能为兼容外部仿真器绕开契约。
