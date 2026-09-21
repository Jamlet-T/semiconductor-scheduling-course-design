# Simulation Contract：动态晶圆厂仿真契约

版本：`0.1.3`
状态：技术路线和本地数据语义冻结，机制分阶段验证中
适用里程碑：`M1 — Simulation Reliability Baseline`

本文档是仿真器、基准策略、优化器、实验和可视化共同遵守的行为契约。原始字段映射与证据见 [Data Contract](data-contract.md)。若实现与本文冲突，以本文和后续有证据的契约修订为准。只有本地 SMT2020 格式说明或可复现实验能够证明当前解释错误时，才修改已冻结语义；每次修改必须记录原因、证据、影响范围和版本。

状态标记：

- `FROZEN`：项目实现必须遵守；修改需要契约版本变更。
- `PROVISIONAL`：已有明确的首版决定，但必须通过源格式或对照实验复核。
- `OPEN`：证据不足，不允许在正式实验中静默猜测。

## 1. 固定研究边界

| 项目 | 契约 |
| --- | --- |
| 研究对象 | 动态、随机、重入、并行机、批处理的半导体前端派工系统 |
| 主算法 | 约束感知多指标派工与组批策略；M1 通过后用 CMA-ES 优化低维参数 |
| 基准 | FIFO、SPT、EDD、CR；Hot Lot 前缀规则必须单独命名，不能混入纯基准 |
| 精确求解 | 仅用于约束闭合的小规模确定性派生实例 |
| 不进入主线 | PPO、GNN、显式 AMHS、设施布局优化、三维数字孪生 |
| 信息系统 | Streamlit + Plotly 优先；二维页面只回放同一仿真事件 |

优化器接口可提前定义，但在 M1 全部通过前保持禁用：

```python
metrics = simulate(theta, scenario, seed)
objective = evaluate(metrics)
```

首版参数向量为：

```text
[priority, due, wait, process, remaining, setup, cqt, batch,
 batch_target_wafers, max_batch_wait_minutes]
```

## 2. 时间推进与同刻事件顺序

| 项目 | 状态 | 决定 |
| --- | --- | --- |
| 时间表示 | FROZEN | 连续仿真时间，内部统一为分钟，不使用固定步长 |
| 时间推进 | FROZEN | 离散事件日历，推进到下一事件时间戳 |
| 同刻处理 | FROZEN | 同一时刻先完成所有状态更新，再执行一次统一派工屏障 |
| 稳定排序 | FROZEN | 同类事件按 `event_seq` 排序；并发候选再按稳定实体 ID 排序 |
| 非法前视 | FROZEN | 策略只能读取当前可观测状态，不得读取未来故障或尚未抽样的加工时间 |

同一时间戳 `t` 的优先级：

1. `PROCESS_FINISH`、`BATCH_FINISH`、`SETUP_FINISH`、`TRANSPORT_ARRIVE`；
2. `REPAIR_FINISH`、`PM_FINISH`；
3. `FAILURE_START`；
4. `PM_START`；
5. `LOT_RELEASE`；
6. `CQT_DEADLINE`、批等待唤醒等监测事件；
7. `DISPATCH_BARRIER`。

含义：在 `t` 恰好加工完成的 lot 先完成；同刻开始的故障随后使设备不可用；同刻释放或到达的 lot 会进入本次派工候选；所有设备在同一状态快照上派工。多台设备选择同一 lot 时，中央动作提交器按稳定设备 ID 原子预留，后续冲突动作重新选择或空闲，禁止重复占用。

## 3. Lot、工序和设备

| 模块 | 状态 | 契约 |
| --- | --- | --- |
| Lot 投放 | FROZEN | `START` 转换为仿真零点后的释放时刻；重复订单按 `RDIST/REPEAT/RUNITS` 惰性生成；每个重复 lot 继承 `DUE-START` 的相对交期 |
| 首工序入队 | FROZEN | release 后直接进入首工序队列；首工序前不增加搬运 |
| 路线推进 | FROZEN | 工序由 `(route_id, step_id, visit_index)` 唯一标识；只有加工完成事件才能推进实际路线 |
| 工序完成 | FROZEN | 加工及约定的卸载活动完成后记为完成；随后 lot 进入运输或完成状态 |
| 设备资格 | FROZEN | 工序只能分配给其 `STNFAM` 对应设备组中的合格设备 |
| 组内机台 | FROZEN | 每台物理机拥有独立状态、当前 setup、故障/维护状态和占用区间 |
| 组内分配 | FROZEN | 空闲合格机台构成动作的一部分；等价候选按 machine ID 稳定打破平局 |

状态机至少包含：

```text
Lot: UNRELEASED → TRANSPORTING/QUEUED → RESERVED → SETUP/PROCESSING
     → TRANSPORTING/QUEUED → ... → COMPLETED

Machine activity: IDLE ↔ SETTING_UP ↔ PROCESSING
Machine availability: UP ↔ DOWN
```

activity 与 availability 正交保存；故障期间保留被中断活动及剩余时长，不通过组合枚举 `DOWN_DURING_*` 状态表达。

## 4. Batch

| 项目 | 状态 | 契约 |
| --- | --- | --- |
| 容量单位 | FROZEN | 本地 `BATCHMN/BATCHMX` 按 wafer 数解释；不得与 lot 数混用 |
| 兼容性 | FROZEN | `crit_sameroutestep` 解释为相同 `route_id + step_id` |
| 合法容量 | FROZEN | `B_min ≤ Σ lot.quantity ≤ B_max` |
| 启动条件 | FROZEN | 先达到合法最小容量，再依据目标装载或等待上限决定是否启动 |
| 欠载 | FROZEN | 小于 `B_min` 时，即使等待超时也不能启动；特殊放宽必须是单独场景并计数 |
| 批占用 | FROZEN | 同批 lot 同时开始、同时结束，共享一次批加工占用 |

批启动公式中的 `n` 统一表示 wafer 数：

```text
start = n >= B_min and (n >= B_target or oldest_eligible_wait >= T_max)
```

`T_max` 从队列首次形成合法最小批量时开始限制主动等待；不从第一个不足容量的 lot 到达时强制启动非法批。

## 5. Setup

| 项目 | 状态 | 契约 |
| --- | --- | --- |
| 状态归属 | FROZEN | setup 是 machine state，不是 lot 的独立加工工序 |
| 触发 | FROZEN | `WHEN=need` 时，目标 operation 的请求 setup 与 machine 当前 setup 不同才触发 |
| 时间来源 | FROZEN | route `STIME` → 精确有向转移 → 空 `CURSETUP` fallback；仍缺失则数据校验失败 |
| 资源占用 | FROZEN | setup 全程占用具体 machine，结束后才允许加工 |
| 重复计时 | FROZEN | 一次换型只能计一次，不能同时累加 route 与 transition 两套时间 |

## 6. CQT

CQT 在领域层独立建模，不藏在普通工序属性中：

```python
CQTConstraint(
    constraint_id,
    route_id,
    start_step,
    end_step,
    max_duration_minutes,
)
```

运行时使用 `(lot_id, constraint_id, visit_index)` 保存时钟。

| 项目 | 状态 | 契约 |
| --- | --- | --- |
| 关系 | FROZEN | `STEP_CQT` 是显式终点，可跨越多道工序 |
| 起点 | FROZEN | 起始 step 的 `PROCESS_FINISH` 开启时钟 |
| 终点 | FROZEN | 目标 step 的 `PROCESS_START` 关闭时钟 |
| 持续时间 | FROZEN | 包含中间加工、搬运、等待和 setup 的实际经过时间 |
| 违规量 | FROZEN | `max(0, target_start - source_finish - limit)` |
| 未闭合窗口 | FROZEN | 仿真期末仍开启的窗口单列数量、当前风险和已超时量，不能从分母删除 |

派工特征可使用：

```text
cqt_risk = elapsed_since_source_finish / max_duration
```

风险可以大于 1；不得裁剪后丢失已违规严重程度。

## 7. Dedication

| 项目 | 状态 | 契约 |
| --- | --- | --- |
| 关系 | FROZEN | `SVESTN=yes/FORSTEP` 表示当前 step 选择的具体 machine 要在指定未来 step 复用 |
| 建立时机 | FROZEN | 仅中央动作提交器成功提交 `DISPATCH`、lot 进入 `RESERVED` 后原子建立；候选生成、可行性检查和策略排序不得写入绑定 |
| 生命周期 | FROZEN | 绑定键为 `(lot, dedication_edge, visit)`，到达指定终点并完成后释放 |
| 可行性 | FROZEN | 绑定设备不可用时 lot 等待，不能自行改派到同组其他设备 |
| 资格交集 | FROZEN | target 动作必须同时满足普通 qualification 与具体 machine binding；两者冲突时显式报错，不得 fallback |
| 初始 WIP | FROZEN | 不伪造 t=0 前的历史绑定；按未绑定派工并计数 `initial_wip_missing_dedication`，正式报告做 cohort 敏感性分析 |

## 8. 故障和 PM

| 项目 | 状态 | 契约 |
| --- | --- | --- |
| 故障到达 | FROZEN | `mttf_by_cal` 按日历时间触发；attach 的 FOA 可定义首次发生 |
| 故障时钟 | FROZEN | 首次随机故障间隔从仿真 `t=0` 计；维修完成后才抽取并从维修完成时刻累计下一次 calendar-time 间隔；DOWN 状态的嵌套故障无效 |
| 抢占 | FROZEN | 故障立即中断 setup、普通加工和整批物理加工；Batch identity 与成员保持不变 |
| 修复后行为 | FROZEN | 修复后从剩余时间继续，不完整重启、不隐式报废 |
| 恢复优先权 | FROZEN | 有被中断活动时先恢复原 machine 上的同一 lot/setup/batch，不重新参与派工；无被中断活动时才进入统一派工屏障 |
| PM 触发 | FROZEN | `mtbpm_by_cal` 按日历触发；FOA 空单位时按累计加工 wafer 触发 |
| 日历 PM | FROZEN | 到点时中断 setup、普通加工或整个 batch，结束后按剩余时长继续；周期 occurrence 由“上一计划开始时刻 + interval”生成，不因停机重叠而漂移 |
| wafer PM 计数 | FROZEN | 仅在真实 `PROCESS_FINISH/BATCH_FINISH` 后累计完成 wafer；普通 lot 加 `quantity_wafers`，Batch 只按 `total_wafers` 加一次 |
| wafer PM 启动 | FROZEN | 达到/越过阈值后置为 pending，在下一次派工前执行；中断、恢复和 stale finish 均不得重复累计 |
| wafer PM reset | FROZEN | PM 完成后计数归零，超过阈值的余量不结转；这是 Contract 0.1.3 的显式本地规则 |
| 停机所有权 | FROZEN | 每台 machine 同时最多一个 active downtime owner；Failure、Calendar PM、Wafer PM 的触发、统计和 provenance 分开 |
| Failure/PM 重叠 | FROZEN | 已 DOWN 时到达的 Failure 或 Calendar PM occurrence 无效；PM 期间被抑制的 stochastic failure 在 PM 完成后从该时刻采样下一间隔；pending wafer PM 保留，并在当前 downtime 结束、恢复派工前执行 |
| 同刻 Failure/PM | FROZEN | `FAILURE_START` 优先于 `PM_START`；Failure 取得所有权，随后同刻 PM occurrence 记为 stale |
| 旧完成事件 | FROZEN | 活动被中断后，旧完成事件必须用版本号/取消标记失效，禁止重复完工 |

故障间隔和维修时长使用相互独立的实体索引流 `(seed, stream, machine_id, occurrence_index)`；PM interval/duration 使用独立的 `(seed, stream, pm_id, occurrence_index)`。scripted 与 stochastic/periodic 配置只在事件生成方式上不同；事件到达后共用同一暂停、停机所有权和恢复路径。微型算例可显式声明 `preemptive-resume` 以验证内核能力；这不自动代表完整 SMT2020 的正式语义。

## 9. 搬运

| 项目 | 状态 | 契约 |
| --- | --- | --- |
| 时间占用 | FROZEN | lot 在搬运完成前不能进入目标设备候选队列 |
| 运输资源 | FROZEN | 主线不建车辆或轨道容量，搬运为外生随机延迟 |
| 参数 | FROZEN | `uniform(m,w)` 为均值和全宽，因此 `uniform(7.5,2.5)=U[6.25,8.75] min` |
| 适用转移 | FROZEN | 首工序前不搬运；后续工序按 location pair 查表，有行才抽样，无行记 0 并累计缺失 pair |
| 指标 | FROZEN | 搬运计入 cycle time，并计入跨越该区间的 CQT |

正式结论限定为给定 SMT2020 搬运假设下的调度效果。搬运缩放实验属于敏感性分析，不能称为真实 AMHS 验证。

## 10. 仿真结束和指标

正式对比采用固定观察终点 `H`；不会为了等所有 lot 完成而让不同策略拥有不同仿真时长。微型测试允许显式使用 `until_all_complete`。

| 指标 | 状态 | 精确定义 |
| --- | --- | --- |
| Throughput | FROZEN | 观察窗内完成的 lot 数和 wafer 数分别除以观察时长 |
| Cycle time | FROZEN | 对新投放且已完成的评价 cohort 计算 `completion-release`；同时报告完成覆盖率 |
| Tardiness | FROZEN | 已完成 lot 为 `max(0, completion-due)`；均值、总量和优先级加权值分别报告 |
| On-time rate | FROZEN | 只对截至评价点可判定的 cohort 报告；截断未完成 lot 不能算准时 |
| Completion ratio | FROZEN | `N_completed / N_released`，分子分母使用同一评价 cohort |
| Terminal WIP | FROZEN | `H` 时已释放但未完成的 lot/wafer 数 |
| Mean WIP | FROZEN | WIP 阶梯曲线在观察窗内的时间积分除以观察时长 |
| Remaining work | FROZEN | 期末 WIP 尚未执行的期望纯加工时间之和；不包含未知未来等待 |
| Lateness exposure | FROZEN | 对期末 WIP 计算 `max(0, H-due)`，同时报告优先级加权总量 |
| CQT | FROZEN | 闭合违规、超时量、仍开启窗口及期末已超时量分别报告 |
| Utilization | FROZEN | processing、setup、down、PM、idle 时间分别报告；分母口径写入结果 |

初始 WIP 没有完整释放历史，不与新投放 lot 的完整 cycle time 混合；只报告仿真内剩余逗留时间和期末状态。

## 11. 随机数和可复现性

随机流至少分为：

```text
release, process, transport, failure, repair,
pm_duration, sampling, rework, batch_process
```

| 项目 | 状态 | 契约 |
| --- | --- | --- |
| 根种子 | FROZEN | 每次 run 记录 `scenario_seed` |
| 子流 | FROZEN | 使用稳定摘要从 `(scenario_seed, stream, entity_key, occurrence)` 派生，不使用 Python `hash()` |
| 公共随机数 | FROZEN | 策略对照共享相同根种子和实体索引随机量 |
| 调用顺序 | FROZEN | 策略改变事件数量时，不得仅依赖一个顺序消费的全局 RNG |
| 记录 | FROZEN | run 产物保存数据 hash、契约版本、代码版本、配置、种子及随机流方案 |

批次组成变化会改变 batch-level 随机实体，必须在实验说明中披露，不能宣称所有批加工随机量都能一一配对。

## 12. 最小事件日志

每条事件至少包含：

```text
run_id, event_seq, sim_time, priority, event_type,
lot_id, visit_index, route_id, step_id,
machine_id, tool_group_id, batch_id,
state_before, state_after, cause_event_seq
```

随机事件增加 `stream_name`、`entity_key`、`occurrence` 和抽样结果。指标必须能够从事件日志或受审计的状态快照独立重算。

## 13. 修改规则

1. `FROZEN` 项只能因本地数据格式证据、参考模型行为证据或可复现反例而修改。
2. `PROVISIONAL/OPEN` 项解决后，补充来源、验证方法和受影响测试。
3. 任何修改先更新契约和微型算例，再改仿真器。
4. 语义修改后，旧 run 不得与新契约 run 混合比较。

### 0.1.2 修订说明

MC07 实现前补齐了两项会改变事件行为的语义：Batch 按一次物理加工整体执行 preemptive-resume；随机故障首次从 `t=0`、后续从上次维修完成时刻按 calendar time 安排。其余事件优先级和既有机制未改变。

### 0.1.3 修订说明

MC08 实现前补齐了四项会改变 PM 长期行为的语义：完成时按真实 wafer 数累计；wafer PM 完成后计数归零且余量不结转；每台设备采用单一 active downtime owner；Failure 与 PM 同刻时 Failure 优先，active downtime 期间的日历型 occurrence 无效，而 wafer PM pending 保留到恢复派工前执行。MC01～MC07 的生产、Setup、Batch、CQT、Dedication 与 Failure 金标准语义未改变。
