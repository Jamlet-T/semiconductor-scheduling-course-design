# SMT2020 Sampling Runtime Audit

审计日期：2026-09-26
Simulation Contract：`0.1.5`；Loader Contract：`0.1.4`；Policy Contract：`0.1.1`；Data Contract：`0.1.3`

## 1. 结论

本轮建立了 `StepPercent` 的 raw→Scenario→decision runtime→trace/ledger→audit 证据链，并关闭 `DI_UNSUPPORTED_SAMPLING`。真实 initial-WIP slice 仍只是判定/映射诊断，不是完整物理 duration 闭环。

已验证边界为：

```text
per_lot + unique Fab location
+ no batch/setup/cascade
+ p=100 may be a CQT endpoint; 0<p<100 endpoint is rejected
+ no rework, visit_index=0
```

真实 SMT2020 中 sampled CQT target 为 HVLM 4 条、LVHM 18 条，逐条均为显式 `p=100`，stochastic sampled CQT target 为 `0/0`。p100 没有 skip 分支，可按普通 target 在 `PROCESS_START` 关闭 CQT；本轮补充集成测试验证该路径。未来若出现 `0<p<100` endpoint，Scenario 显式拒绝，不猜测 skip-CQT 语义。此外全部 221/955 条显式 sampled 工序所属 tool template 都配置 `LOAD=1 min / UNLOAD=1 min`，而 sampling slice 没有执行这两段物理时长；这属于独立的 load/unload blocker。

Data Integration Gate 保持 `not_passed_gaps`，每个模型还剩 5 类 blocker；`optimizer_enabled=false`，不得启动正式 HVLM/LVHM 策略实验或 CMA-ES。

## 2. Raw evidence

| Statistic | HVLM | LVHM |
| --- | ---: | ---: |
| route rows | 926 | 4013 |
| explicit `StepPercent` | 221 | 955 |
| stochastic `0<p<100` | 149 | 662 |
| explicit `p=100` | 72 | 293 |
| initial WIP currently at an explicit sampling step | 98 | 75 |
| sampling operation overlapping rework | 14 | 52 |
| sampled CQT target / stochastic sampled CQT target | 4 / 0 | 18 / 0 |
| explicit sampling operations using `LOAD=1 min / UNLOAD=1 min` tool templates | 221 | 955 |

显式 `StepPercent` operation 均为 `per_lot`、Fab metrology 类工序；raw 直接给出百分数，但不自描述概率方向、判定时点、随机流 identity 或与 CQT/rework 的组合行为。

SMT2020 论文把 metrology inspection frequency 描述为 sampling rate，并报告 lithography/thin-film/defectivity 的典型频率范围，支持“该字段控制是否执行 metrology”的用途解释，但不定义本项目所需的事件顺序与组合语义：[SMT2020 paper](https://doi.org/10.1109/TSM.2020.3001933)。

固定参考实现 commit `0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a` 将空值视为始终执行，以 `uniform(0,100) <= StepPercent` 决定是否执行，并在 operation 对派工可见前判定；该行为只作为 D 级交叉证据，不作为 ground truth：[reference source](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/classes.py)。

## 3. 冻结的受限运行语义

### 3.1 三种输入状态

| `OperationSpec.sample_percent` | Decision trace | Random ledger | Result |
| --- | --- | --- | --- |
| `None` | 无 | 无 | 始终执行；表示 raw 未配置 |
| `100` | `SAMPLING_DECISION(performed=true)` | 无 | 显式 100%，不浪费随机量 |
| `0<p<100` | `SAMPLING_DECISION` | 恰好一次 `sampling` sample | `draw <= p` 时执行 |

`p` 必须是 `(0,100]` 内有限数；不允许把 0、越界值、布尔值或 NaN 静默纠正。

### 3.2 判定时点与跳步

判定发生在 operation 对派工可见以及首段/下一段 transport 之前。内部 lot 可已标记为 `QUEUED`，但 sampling 完成前不会生成 feasible action。若未命中：

1. 同刻写 `OPERATION_SKIPPED`；
2. 不创建 feasible action；
3. 不占用 machine，不执行 setup/batch，不抽 processing duration；
4. 不产生 `PROCESS_START/PROCESS_FINISH`，也不累计 wafer-triggered PM 完成量；
5. 继续扫描下一 operation；连续跳步允许；
6. 只向最终实际执行的 target 发起一次 transport；若余下全部跳过，则同刻完成 lot。

release lot 在首工序前、initial WIP 在 `t=0` 的当前工序前都走同一入口。策略只看到 sampling 已判定且真实入队的 feasible actions；SPT/CR 等策略不能读取未来 draw。

### 3.3 CRN identity

```text
stream = sampling
entity_key = lot_id + route_id + step_id + visit_index
occurrence = visit_index
draw = uniform(0,100)
performed = draw <= sample_percent
```

实体索引流不依赖事件或容器遍历顺序，因此相同 seed 与 sampling identity 在不同策略下得到相同底层样本。当前受限 profile 没有 rework，故 `visit_index=0`；未来关闭 rework blocker 时必须重新验证 visit lifecycle，不能沿用 0 冒充多次访问。

## 4. Loader 与真实 validation slice

Loader Contract `0.1.4` 提供 sampling 判定/映射诊断 slice：

- 把 raw `StepPercent` 映射到 immutable `OperationSpec.sample_percent`；
- reconciliation 分开报告显式、随机、100%、initial-WIP、rework overlap、CQT endpoint 与真正 unsupported endpoint 数；当前两模型 `sampling_profile_unsupported_operations=0`；
- `mode="sampling_validation_slice"` 从真实 initial WIP 稳定选择一个当前 sampled step；
- `validation_sampling_operation=(route_id, step_id)` 可精确选择，且写入 provenance；
- slice 保留真实 lot/product/order/source row、wafer 数、priority、due 与 processing distribution，但不把 raw tool 的 load/unload duration 装配进 Scenario；loader 写 `DI_SAMPLING_SLICE_OMITS_LOAD_UNLOAD` warning，并在 selector provenance 保存省略的 load/unload 分钟数；
- 不满足受限 profile或找不到对应 initial WIP 时显式报错，不做字段删除或替代。

两模型分别覆盖 `0<p<100` 与 `p=100` 的真实记录。随机 case 必须产生一条 sampling ledger；100% case 必须有 decision trace 而没有 sampling ledger。该 slice 是 sampling decision/mapping diagnostic，不是 physical-duration closure 或 full-fab 场景，也不产生策略性能结论。全部 sampled 工序共有的 load/unload 由 `DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` 继续阻塞。

Loader `0.1.4` 的最终 manifest identity：

| Model | Manifest hash | Dataset version |
| --- | --- | --- |
| SMT2020_HVLM | `8b5ad109d1052dff53c4354f4567a71b84c27b2badf159105194e46cde4a8cd7` | `SMT2020_HVLM@sha256:8b5ad109d1052dff53c4354f4567a71b84c27b2badf159105194e46cde4a8cd7` |
| SMT2020_LVHM | `80c8ccc08aa8f3ed5338cbb27af4db20c82deeca24c5fa060b57268ca3ae247c` | `SMT2020_LVHM@sha256:80c8ccc08aa8f3ed5338cbb27af4db20c82deeca24c5fa060b57268ca3ae247c` |

以上 identity 已按 Loader `0.1.4` 的最终代码只读重算；没有沿用 Loader `0.1.3` 的 logical hash。manifest 版本变化不表示 `datasets/` 原始字节发生变化。

## 5. Trace、provenance 与独立审计

`SAMPLING_DECISION` 保存 lot/route/step/visit、百分数、draw（100% 时为空）、performed 和 sampling entity ID。`OPERATION_SKIPPED` 与对应 false decision 同时刻、同 identity 关联。

结果 provenance 固定记录：

```text
sampling_runtime.schema_version = 0.1.0
sampling_runtime.id = per_lot_sampling
decision_point = operation_entry_before_dispatch_and_transport
scope = per_lot
explicit_percent_range = (0,100]
unconfigured = always_perform_without_decision
percent_100 = perform_without_random_draw
rework_visits = unsupported_visit_0_only
```

独立 audit 至少检查：decision/skip 一一对应、随机账本数值与 entity identity 一致、100% 不消费随机量、skipped operation 没有 process interval/finish、结果 provenance 与受限边界一致。篡改 decision、skip、ledger 或 provenance 必须被识别为 violation。

本轮本地全量回归为 `211 passed`，包含 MC01～MC08、M1、Runtime Reliability 既有测试以及新增 Sampling/真实数据切片测试；`git diff -- datasets` 为空。

## 6. 已关闭项与保留边界

### 6.1 p100 CQT target 已支持

raw 实测 HVLM 4、LVHM 18 个 sampled operation 同时是 CQT target，且全部 `sample_percent=100`。它们写确定性 `SAMPLING_DECISION(performed=true)`，不消费 sampling ledger，随后正常进入派工，并在真实 `PROCESS_START` 关闭 CQT。由于没有 skip 分支，这不是对含糊 skip-CQT 语义的假设。

对于未来 `0<p<100` 的 CQT endpoint 或任何 sampled Dedication endpoint，Scenario 仍显式拒绝。不得通过“skip 自动关闭 CQT”、把 skip 当作 `PROCESS_START` 或删除字段等 silent fallback 获得支持结论。

### 6.2 Rework overlap 仍为独立 blocker

HVLM/LVHM 分别有 14/52 个 sampling operation 同时声明 rework。固定参考实现表现为每个 lot/source step 最多一次 rework 判定，而旧 Data Contract 曾暗示每 visit 重抽；二者冲突，且 dedication/transport visit lifecycle 尚未闭环。因此本轮不实现 rework，不用 sampling runtime 推断 rework 语义。

### 6.3 Load/unload duration 仍为独立 blocker

HVLM/LVHM 的全部 221/955 个显式 sampled operation 都落在配置 `LOAD=1 min / UNLOAD=1 min` 的 tool template 上。当前 sampling slice 只带 processing distribution，不执行 load/unload，故其完成时间不是 raw operation 的完整物理 duration。loader/audit/provenance 必须显式说明该省略；不得用 sampling smoke 关闭 `DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` 或宣称 metrology 工序端到端兼容。

### 6.4 Dedication 与其他组合

领域层拒绝任何 sampled Dedication endpoint。当前 raw 未触发该项不等于语义已证明；它属于未来数据的防护边界。Batch、Setup、cascade 组合也未由 sampling slice 验证。

## 7. Gate decision

本轮可声明：

```text
SMT2020 sampling runtime = VERIFIED FOR CURRENT RAW PROFILE
sampling validation slice = DECISION/MAPPING DIAGNOSTIC, NOT PHYSICAL DURATION
DI_UNSUPPORTED_SAMPLING = CLOSED
DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE = OPEN / BLOCKER
SMT2020 Data Integration Gate = not_passed_gaps
M1 = passed
Runtime Reliability = passed within implemented scope
MC01-MC08 = verified
optimizer_enabled = false
```

下一步不应继续把 sampling 当作当前 raw blocker；应处理剩余的 load/unload/cascade、rework、setup MINRUN、batch decision config 或 multi-calendar。若未来数据出现 stochastic CQT/Dedication endpoint，再以显式 unsupported/blocker 重新进入证据审计。
