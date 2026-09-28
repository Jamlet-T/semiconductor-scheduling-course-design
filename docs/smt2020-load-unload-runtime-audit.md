# SMT2020 Non-cascade LOAD/UNLOAD Runtime 审计

审计日期：2026-09-27
适用契约：Simulation Contract `0.1.7`；Data Contract `0.1.6`；Loader Contract `0.1.6`

本文记录本轮实现对 non-cascade、non-batch 受限子链的语义冻结和证据边界。它是 `smt2020-cascade-semantic-audit.md` 的 runtime 补充，不是完整 SMT2020 Data Integration Gate 通过证明；`DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` 仍为 OPEN/BLOCKER。

## 1. 受限 profile

真实 SMT2020 `load_unload_validation_slice` 的选择条件同时要求以下条件；这些是该真实 slice 的最小可审计闭包，不是通用 Scenario 对其他机制的拒绝规则：

```text
machine.cascading = false
processing_basis = per_lot
无 Batch / Setup / StepPercent / Rework / CQT / Dedication
无 PartInterval / BatchInterval
```

通用阶段 runtime 的硬边界是 non-cascade、非 Batch/`per_batch` 且无 Part/BatchInterval；合成 Scenario 可继续组合已有 CQT、Dedication、sampling 或 Setup 语义（其中 CQT+正 L/U 的 core-start 关闭规则已有 micro case 证据）。

runtime 对每个 committed dispatch 保持单一 machine 占用，阶段顺序固定为：

```text
DISPATCH/RESERVED
  → LOAD_START → LOAD_FINISH
  → PROCESS_START（core 开始；不另发 `PROCESS_CORE_START` trace） → PROCESS_CORE_FINISH
  → UNLOAD_START → UNLOAD_FINISH
  → PROCESS_FINISH → LOT_COMPLETE 或下一段 transport
```

`LOAD`/`UNLOAD` 是独立 phase interval；`processing_intervals` 只记录 core。machine 在 `UNLOAD_FINISH` 前不会重新进入可派工状态，第二个 lot 不能穿过前一个 lot 的尾段占用。

## 2. 已冻结的事件和指标口径

| 项目 | 当前受限语义 | 证据边界 |
| --- | --- | --- |
| CQT 终点 | target `PROCESS_START`，即 core 开始；LOAD 不关闭 CQT | 受限 micro case；不外推 cascade |
| canonical completion | 卸载完成后发 `PROCESS_FINISH`；无卸载时可同刻发出 | 受限 runtime trace |
| processing sample | committed core start 后按 processing stable identity 抽样一次 | random ledger 与 resume 测试 |
| Failure/PM | LOAD、PROCESS_CORE、UNLOAD 均可抢占；保存 remaining duration，恢复不重抽样 | 合成 failure/calendar PM stage cases |
| stale finish | activity token 使旧 `LOAD/PROCESS_CORE/UNLOAD_FINISH` 无效 | runtime stale-event path |
| fixed horizon | 事件时间 `<=H` 才生效；活动阶段在 `H` 截断写入独立 interval，未到 `PROCESS_FINISH` 的 lot 留在 terminal WIP | fixed-horizon phase snapshot cases |
| 时间守恒 | machine statistics 分别报告 load、processing、unload、setup、down/PM、idle；装卸不计入 processing | independent invariant audit |

`PROCESS_FINISH` 继续是业务完成边界：路线推进、有效 MINRUN lot 计数、CQT source 开钟、Dedication 释放、wafer-PM 完成计数和 lot completion 均不得提前到 core finish 或 unload start。MINRUN 的真实 raw 组合仍不在本 profile 内。

## 3. 真实 SMT2020 两模型 slice

Loader mode 为 `load_unload_validation_slice`，固定选择：

| Model | Initial WIP | Product/route/steps | Selected machines | Raw phase values |
| --- | --- | --- | --- | --- |
| `SMT2020_HVLM` | `Init_Lot_3_1361` | `part_3` / `r_3` / `18→19` | `DE_FE_1#0001`, `DE_FE_86#0001` | each `LOAD=1 min`, `UNLOAD=1 min` |
| `SMT2020_LVHM` | `Init_Lot_3_290` | `part_3` / `r_3` / `18→19` | `DE_FE_1#0001`, `DE_FE_86#0001` | each `LOAD=1 min`, `UNLOAD=1 min` |

两道工序为真实 `per_lot`、`uniform` processing，机器位置为 `Fab`，工序间使用已有 `Fab→Fab` 外生 transport。slice provenance 保存 raw source row、WIP metadata、selector、machine qualification counts、source load/unload values 和 transport pair。

## 4. 有意省略与不得宣称的内容

该 slice 不是完整产线 Scenario，以下内容有意不装配或不伪造：

- 其他合格物理机；因此不验证 machine competition、资格机并发或策略性能；
- 所选物理机的 Failure、calendar PM、wafer PM attachments；合成 runtime 的阶段抢占证据不能替代真实日历组合；
- initial setup、MINRUN completed-lot count、wafer-PM counter、历史 CQT source time、历史 Dedication machine；unknown 状态和 runtime fallback 写入 provenance/audit；
- Batch、Setup、StepPercent、Rework、CQT/Dedication endpoint、PartInterval、BatchInterval 和 cascade；
- 正式 HVLM/LVHM KPI、策略排名、优化器/CMA-ES 结论。

省略项不是 raw 数据不存在，而是受限 slice 的闭包选择；报告必须同时给出 selector、manifest、seed、配置和运行产物，不能把 slice 名称简写成“完整 SMT2020 仿真”。

## 5. Gate 结论

受限 runtime 证据将 Load/Unload 机制标记为 `PASS-limited`，但不改变 Data Integration Gate：

```text
DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE = OPEN / BLOCKER
Gate = not_passed_gaps
```

要关闭 blocker，还需对 cascade 尾段所有权、lot finish/machine release 双时点、Part/BatchInterval、真实 Setup/MINRUN、Failure/PM 与多 calendar 组合、rework、同刻事件次序及独立 conservation audit 取得可复现证据。在此之前不得启动正式 HVLM/LVHM 策略实验或 CMA-ES。
