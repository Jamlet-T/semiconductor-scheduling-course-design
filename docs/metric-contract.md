# Metric Contract：M1 指标口径与实现审计

适用版本：Simulation Contract `0.1.3`

审计范围：可信轻量 DES；不代表正式 SMT2020 数据链已接入

## 1. 终止时刻与观察窗

| 模式 | 分母/终点 |
| --- | --- |
| `until_all_complete` | `end_time` 为全部场景 lot 完成时的仿真时刻；throughput 分母为该 `end_time` |
| `fixed_horizon` | `end_time=H`；throughput、Mean WIP 和期末暴露分母/评价点均为 `H` |

fixed horizon 处理所有 `event.time <= H` 的事件，只拒绝 `event.time > H`。因此发生在 `H` 的完成、投放、Failure 和 PM 均改变事件计数与 terminal state；从 `H` 才开始的停机对 `[0,H]` 时间积分贡献为 0。

## 2. 生产与期末指标

| 指标 | 冻结定义 | 当前实现 | 审计状态 |
| --- | --- | --- | --- |
| Throughput lots | `completed_lots / end_time` | 已实现；分母随终止模式确定 | PASS |
| Throughput wafers | `completed_wafers / end_time` | 未实现 | GAP |
| Mean Cycle Time | 新投放且已完成 cohort 的 `mean(completion-release)` | 已实现；初始 WIP 已从完整 cycle-time cohort 排除 | PASS |
| Cycle-time coverage | 新投放 cohort 中 `completed/released` | 已实现 | PASS |
| Completion Ratio | `completed/released`，要求明确 cohort | 当前为全部已投放 lot，尚未分别输出新投放与初始 WIP cohort | GAP |
| Terminal WIP lots | `H` 时已投放但未完成 lot 数 | 已实现 | PASS |
| Terminal WIP wafers | `H` 时已投放但未完成 wafer 数 | 未实现 | GAP |
| Remaining Work | terminal WIP 的剩余 nominal processing time | 已实现 | PASS |
| Mean WIP | `(1/T)∫[0,T] WIP(t)dt` | 已实现 | PASS |

Mean WIP 的状态边界：

- unreleased lot 不计入；
- `LOT_RELEASE` 后到 `LOT_COMPLETE` 前均计入，包括 QUEUED、RESERVED、SETTING_UP、PROCESSING、故障/PM 中断和 Batch 成员；
- `LOT_COMPLETE` 时移出；
- `t=H` 的瞬时变化没有正长度面积，因此不改变积分；
- 每个 Batch member 按一个 lot 分别计入。

Remaining Work 只表示：

```text
当前 operation 尚未执行的 nominal processing time
+ 后续 operation 的全部 nominal processing time
```

它不包含 future setup、transport、batch waiting、未来 downtime 或队列等待，因此不得称为“预计剩余 cycle time”。被中断的 `10` 分钟加工若已实际执行 `5` 分钟，只计剩余 `5` 分钟。

## 3. 交期与暴露

| 指标 | 定义 | 当前实现 | 状态 |
| --- | --- | --- | --- |
| Completed tardiness | `max(0, completion-due)` | 未实现汇总 | GAP |
| Mean/total/weighted tardiness | 对 completed cohort 分别汇总 | 未实现 | GAP |
| On-time rate | 对可判定 cohort 统计 | 未实现 | GAP |
| Terminal lateness exposure | 对 terminal WIP 求 `max(0,H-due)` | 已实现总量 | PASS |

Completed tardiness 与 terminal exposure 必须分别报告。Exposure 不能替代 tardiness，也不能把未完成 lot 当作 completed violation。

## 4. CQT 指标

已关闭 CQT 记录输出：

- closed count；
- closed violation count；
- total/max closed excess；
- open count；
- overdue open count；
- terminal open exposure。

闭合违规和期末仍 open 的逾期窗口分开统计，不重复合并为同一个 violation count。

## 5. 设备时间与停机

每台 machine 在观察窗内使用：

```text
processing_time
+ setup_time
+ idle_time          # UP 且没有物理活动
+ failure_downtime
+ pm_downtime
= end_time
```

Failure 与 PM 使用单一 downtime owner，因此两个 downtime integral 不重叠。发生在 `H` 的 Failure/PM occurrence 计入 occurrence count 和 terminal DOWN snapshot，但其窗口内 downtime contribution 为 0。

当前输出：

| 指标 | 状态 |
| --- | --- |
| Processing Time | PASS |
| Setup Time | PASS |
| Idle-up Time（字段名仍为 `idle_time`） | PASS，文档必须解释口径 |
| Failure Downtime | PASS |
| PM Downtime | PASS |
| Total Downtime | PASS，可由两类停机相加 |
| Utilization ratio | GAP，尚未输出统一比率 |
| Availability ratio | GAP，尚未输出统一比率 |

## 6. 独立重算

`fab_scheduler.evaluation.audit` 从 trace 独立重算 released/completed、completion ratio、mean cycle time、throughput、terminal WIP 和 Mean WIP，并检查：

- lot 数量守恒；
- machine 时间守恒；
- Batch wafer 容量与成员 wafer 合计；
- CQT duration/excess/violation；
- Dedication record identity 与生命周期；
- Transport interval、active snapshot、metrics、trace 与 RandomSampleLedger 的一致性。

该检查器覆盖当前轻量 DES 和受限 Transport 输出，但尚未覆盖 sampling/rework、正式 full-fab loader 结果、wafer throughput、tardiness 和利用率比率。
