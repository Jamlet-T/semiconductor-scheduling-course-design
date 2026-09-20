# Data Contract：SMT2020 字段到仿真语义

版本：`0.1.0`  
状态：本地模型语义已冻结；实现按 M1 分阶段验证  
数据范围：`datasets/SMT2020_HVLM`、`datasets/SMT2020_LVHM`

本文件回答三个连续问题：

```text
原始字段 → 内部数据结构 → 事件和状态如何变化
```

“字段存在”不代表机制已经实现。表中的 `FROZEN-SPEC` 表示本地模型行为已经定义，但仍需相应 micro case 通过后才能称为 `VERIFIED`。当前 Basic DES 的 MC01、MC02 与 Setup 的 MC03 已进入实现并验证；Batch、CQT、Dedication、Failure/PM 仍不得用于正式实验。

## 1. 证据层级与统一约定

证据优先级：

1. 本仓库原始 SMT2020 文件的字段、引用关系和取值；
2. `docs/simulation-contract.md` 中已经冻结的本地模型；
3. PySCFabSim 固定 commit `0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a` 的静态行为，作为参数解释和交叉验证证据；
4. 无来源的内容只能成为显式、版本化的本地模型假设，不能描述为真实 Fab 事实。

统一内部单位为分钟和 wafer。`sec/min/hr/day` 分别乘以 `1/60、1、60、1440`。日期使用数据中的 `%m/%d/%y %H:%M:%S` 解析，不读取本机时区。

分布统一表示为：

```python
DistributionSpec(kind, parameter_1, parameter_2, unit)
```

本地 `uniform(m, w)` 冻结为“均值 + 全宽”：

```text
Uniform[m - w/2, m + w/2]
```

因此 `uniform(7.5, 2.5) min = Uniform[6.25, 8.75] min`。这一解释与 PySCFabSim 固定 commit 的 `UniformDistribution` 一致。随机抽样必须使用 `(seed, stream, entity_key, occurrence)` 派生子流，不能共享一个顺序消费的全局 RNG。

## 2. 产品、路线与工序

| 原始文件/字段 | 内部结构 | 运行时语义 | 状态 |
| --- | --- | --- | --- |
| `part.PART` | `ProductSpec.product_id` | order/WIP 通过它找到产品 | FROZEN-SPEC |
| `part.ROUTEFILE` | `ProductSpec.route_source` | 指向实际 route 文件 | FROZEN-SPEC |
| `part.ROUTE` | `RouteSpec.route_id` | 与 route 文件每行 `ROUTE` 一致 | FROZEN-SPEC |
| `route.ROUTE` | `OperationSpec.route_id` | 参与 Batch 兼容键和 trace | FROZEN-SPEC |
| `route.STEP` | `OperationSpec.step_id` | 路线文件中的显式顺序；本地两套数据均严格为 `1..N` | VERIFIED-DATA |
| `route.DESC` | `OperationSpec.description` | 仅作可读标签，不参与资格判断 | FROZEN-SPEC |
| `route.STNFAM` | `OperationSpec.tool_family_id` | 生成该工序的合格物理机集合 | FROZEN-SPEC |
| `route.IGNORE` | `source_metadata` | 不能按字段名丢弃行；当前只保留用于审计 | FROZEN-SPEC |

工序实例使用 `(route_id, step_id, visit_index)` 唯一标识。只有 `PROCESS_FINISH` 才能完成当前工序并推进路线；抽样跳步和返工必须写独立 trace。

### 抽样与返工

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| `StepPercent` | `OperationSpec.sample_percent` | 空值按 100；每个 lot/step/visit 使用 `sampling` 子流做一次 Bernoulli，未命中则产生 `OPERATION_SKIPPED` 并推进 |
| `RWKSTEP` | `ReworkRule.return_step` | 返工命中后回到该路线 step |
| `REWORK` | `ReworkRule.percent` | 百分数，例如 `1.8` 表示 1.8%，使用 `rework` 子流 |
| `RWKTYPE=lot` | `ReworkRule.scope` | 以 lot 为判定单位 |

同一 `(lot, source_step, visit)` 只抽样一次。返工会创建新的 visit；不能用 Python 容器遍历顺序决定随机量。

## 3. 设备、设备组与资格

| 原始文件/字段 | 内部结构 | 运行时语义 | 状态 |
| --- | --- | --- | --- |
| `tool.STNFAM` | `ToolFamily.tool_family_id` | route 工序资格连接键 | FROZEN-SPEC |
| `tool.STN` | `MachineTemplate.source_station_id` | 设备模板标识 | FROZEN-SPEC |
| `tool.STNQTY` | `MachineTemplate.quantity` | 实例化为确定 ID：`{STN}#0001...`；每台机独立占用 | FROZEN-SPEC |
| `tool.STNGRP` | `Machine.group_id` | downtime/统计分组，不替代 `STNFAM` 资格 | FROZEN-SPEC |
| `tool.STNFAMLOC` | `Machine.location_id` | 搬运表 from/to 的位置键 | FROZEN-SPEC |
| `LTIME/LTUNITS` | `Machine.load_minutes` | 每次加工的 lot 占用时间组成 | FROZEN-SPEC |
| `ULTIME/ULTUNITS` | `Machine.unload_minutes` | 每次加工的 lot 占用时间组成 | FROZEN-SPEC |
| `STNCAP=2` | `Machine.cascading` | 标记级联设备；不解释为可同时容纳两个普通 lot | FROZEN-SPEC |
| `RULE/FWLRANK/WAKERESRANK` | `NativeRuleMetadata` | 只用于 NativeLike 参考；纯 FIFO/SPT/EDD/CR 不继承这些复合规则 | FROZEN-SPEC |

资格集合严格为：

```text
eligible_machines(operation)
= 所有由 tool.STNFAM == operation.STNFAM 的行实例化出的物理机
```

若工序的 `STNFAM` 无对应设备、`STNQTY` 不是正整数或实例 ID 冲突，加载阶段直接失败。

## 4. 加工时间与级联

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| `PDIST` | `ProcessingTime.distribution.kind` | 当前原始数据均为 `uniform` |
| `PTIME/PTIME2/PTUNITS` | 分布参数 | 按“均值 + 全宽”转换为分钟 |
| `PTPER=per_lot` | `ProcessingBasis.PER_LOT` | 每个 lot 抽样一次 |
| `PTPER=per_piece` | `ProcessingBasis.PER_PIECE` | 无 `PartInterval` 时分布参数乘 wafer 数 |
| `PTPER=per_batch` | `ProcessingBasis.PER_BATCH` | 整批共享一次抽样；容量由 wafer 总数判断 |
| `PartInterval` | `CascadingSpec.part_interval` | lot 最后一片离开时刻为基础抽样加 `(pieces-1)*interval`；设备可用时刻为 `pieces*interval`，分别产生日志 |
| `BatchInterval` | `CascadingSpec.batch_interval` | lot 完工仍使用加工分布；设备可用间隔使用该固定值 |

load/unload 是否计入设备释放时刻按 `STNCAP` 的级联标记处理，并分别记录 lot 完工与 machine 可用事件。Basic DES 的 MC01、MC02只使用确定性 `processing_time`，尚未实现上述随机分布和级联。

## 5. 动态投放、交期和优先级

| 原始文件/字段 | 内部结构 | 运行时语义 | 状态 |
| --- | --- | --- | --- |
| `order.LOT` | `ReleaseTemplate.name_prefix` | 重复 lot 使用稳定序号生成唯一 ID | FROZEN-SPEC |
| `PART` | `ReleaseTemplate.product_id` | 找到产品路线 | FROZEN-SPEC |
| `PIECES` | `Lot.quantity_wafers` | wafer 数；不得与 lot 数混用 | FROZEN-SPEC |
| `START` | `first_release_datetime` | 相对全局仿真零点得到首个 release | FROZEN-SPEC |
| `RDIST` | `release_interval.kind` | 当前数据为 `constant` | VERIFIED-DATA |
| `REPEAT/RUNITS` | `release_interval` | 第 i 个重复 lot 在 `first_release + i*interval` 投放 | FROZEN-SPEC |
| `RPT#` | `repeat_limit` | 按 horizon 惰性生成，不预展开全部 200000 个 lot | FROZEN-SPEC |
| `LOTSPERRPT` | `lots_per_repeat` | 每个重复点生成的 lot 数 | FROZEN-SPEC |
| `DUE` | `relative_due_offset` | 先计算 `DUE-START`，每个重复 lot 的 due 为自身 release 加该偏移 | FROZEN-SPEC |
| `PRIOR` | `Lot.priority` | 数值越大优先级越高；纯 FIFO 不读取它 | FROZEN-SPEC |
| `HOTLOT` | `Lot.hotlot_flag` | 原值保留；本地数据均为 `no`，不得根据 lot 名称猜测 | VERIFIED-DATA |
| `ORDER` | `Lot.order_id` | 追踪和聚合字段 | FROZEN-SPEC |

release 后直接进入首工序等待队列，不添加首工序前搬运。队列入队时刻是 FIFO 的第一排序键，`lot_id` 是稳定 tie-breaker。

## 6. 初始 WIP

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| `WIP.LOT/PART/PIECES/PRIOR/DUE/ORDER` | `InitialWipLot` | 与动态 lot 对应字段相同 |
| `CURSTEP` | `operation_index` | t=0 时在该 step 前等待；原始文件没有机台和剩余加工信息，因此不得初始化成加工中 |
| `START` | `source_start_datetime` | 只作来源和剩余逗留分析，不与新投放 lot 的完整 cycle time 混算 |
| `TRACE` | `source_metadata` | 当前不影响事件逻辑 |

原始 WIP 不保存历史 machine 或 CQT 起点时间。数据审计发现：

| 缺失历史 | HVLM | LVHM |
| --- | ---: | ---: |
| 落在已跨 dedication 起点、未到终点关系中的记录数 | 2435 | 1965 |
| 落在已开启但缺起点时间的 CQT 窗口 lot 数 | 341 | 433 |

本地模型冻结以下处理：

- t=0 之前产生的 dedication 不继承未知绑定；到达目标 step 时按普通资格派工，同时累计 `initial_wip_missing_dedication`；
- 只有仿真内观察到 source finish 的 CQT 窗口进入 CQT 分母；历史窗口累计 `initial_wip_unknown_cqt`；
- 正式报告必须单列这些计数，并对“排除初始 WIP 的评价 cohort”做敏感性分析。

这关闭了仿真器的行为分支，但没有补造不存在的历史事实。

## 7. Batch

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| `PTPER=per_batch` | `OperationSpec.is_batch` | 启用组批 |
| `BATCHMN/BATCHMX` | `BatchCapacity(min_wafers,max_wafers)` | 单位为 wafer |
| `tool.BATCHCRITF=crit_sameroutestep` | `BatchCompatibility.SAME_ROUTE_STEP` | 只有相同 `route_id + step_id` 可同批 |
| `tool.BATCHPER=piece` | `BatchCapacity.unit=WAFER` | 容量按 lot wafer 数求和 |

本地数据的 batch 边界只有 `(75,100)、(100,125)、(125,150)`，order/WIP 的标准 lot 为 25 wafers。这与 wafer 容量解释一致。启动规则继续遵守 Simulation Contract：

```text
n_wafers >= B_min
and (n_wafers >= B_target or feasible_wait >= T_max)
```

低于 `B_min` 不能因超时启动。实现前状态为 `FROZEN-SPEC / NOT-IMPLEMENTED`。

## 8. Setup

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| `route.SETUP` | `OperationSpec.required_setup` | 空值表示无 setup 要求 |
| `route.WHEN=need` | `SetupTrigger.ON_CHANGE` | 仅当前 setup 不同才触发 |
| `route.STIME/STUNITS` | `OperationSpec.setup_override_minutes` | 非空时优先作为该工序的 setup 时长 |
| `setup.CURSETUP/NEWSETUP` | `SetupTransition(from,to)` | 有向转移，不自动对称 |
| `setup` 中空 `CURSETUP` | 初始/通用转移 | 当前 setup 无精确转移时的 fallback |
| `setupgrp.SETUP/MINRUN` | `SetupMinimumRun` | 换到该 setup 后，达到最小 run 数前禁止再次换型 |
| `tool.SETUPGRP` | `Machine.setup_group` | 连接机台和 setup 组 |

时长解析顺序冻结为：

```text
route.STIME
→ setup[(current_setup, required_setup)]
→ setup[("", required_setup)]
→ 数据契约错误
```

机台初始 setup 为空字符串。换型是独立 machine state/event，不能把时间静默加进加工事件。MC03 已验证有向转移、显式 `SETTING_UP` 状态、`SETUP_START/SETUP_FINISH` trace、设备占用和 setup/processing 分离统计；状态为 `VERIFIED-MC03`。正式 SMT2020 loader 的 setup group 映射仍未实现。

## 9. CQT

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| 当前行 `STEP` | `CQTConstraint.start_step` | source operation |
| `STEP_CQT` | `CQTConstraint.end_step` | 显式目标 step，可跨步 |
| `CQT/CQTUNITS` | `max_duration_minutes` | 最大实际经过时间 |

起点冻结为 source step 的 `PROCESS_FINISH`，终点冻结为 target step 的 `PROCESS_START`。中间加工、等待、搬运和 setup 均计时。两套数据中的所有 CQT target 都存在且严格位于 source 之后。实现前状态为 `FROZEN-SPEC / NOT-IMPLEMENTED`。

## 10. Dedication

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| `SVESTN=yes` | `DedicationEdge.enabled` | 当前 step 选择的具体物理机产生绑定 |
| `FORSTEP` | `DedicationEdge.target_step` | 目标 step 必须复用该物理机 |

绑定键为 `(lot_id, edge_id, visit_index)`，目标 step 完成后释放。绑定设备忙或停机时只能等待。两套数据中的 target 均存在且严格位于 source 之后。初始 WIP 缺失历史绑定按第 6 节处理。实现前状态为 `FROZEN-SPEC / NOT-IMPLEMENTED`。

## 11. Failure、PM 与 SDT

| 原始字段 | 内部结构 | 运行时语义 |
| --- | --- | --- |
| `downcal.DOWNCALNAME` | `DowntimeCalendar.id` | 被 attach 引用 |
| `MTTFDIST/MTTF/MTTFUNITS` | failure interval | 首次及后续故障间隔 |
| `MTTRDIST/MTTR/MTTRUNITS` | repair duration | 修复时长 |
| `pmcal.PMCALNAME` | `PMCalendar.id` | 被 attach 引用 |
| `PMCALTYPE=mtbpm_by_cal` | calendar PM | 按日历间隔触发 |
| `MTBPM/MTBPMUNITS` | PM interval | 后续间隔 |
| `MTTRDIST/MTTR/MTTR2/MTTRUNITS` | PM duration | PM 时长分布 |
| `attach.CALTYPE` | `down` 或 `pm` | 选择日历类型 |
| `RESTYPE/RESNAME` | attachment target | `stnfam` 或 `stngrp` 解析到具体物理机 |
| `FOADIST/FOA/FOAUNITS` | first occurrence | 非空单位按时间；空单位按已加工 wafer 数 |

本地模型冻结为：

- 故障在发生时立即中断当前 setup/process，修复后从剩余时间继续；
- 日历 PM 到点时采用同样的 preemptive-resume；
- 按 wafer 触发的 PM 在造成计数越界的加工完成后、机台再次派工前执行；
- 所有被中断活动的旧完成事件必须失效；
- failure、repair、pm_duration 使用独立实体索引随机流。

这些选择与 PySCFabSim 延后在制完成事件的参考行为相容，但仍需 MC07 和独立 PM case 验证。当前状态为 `FROZEN-SPEC / NOT-IMPLEMENTED`。

## 12. Transport

两个数据集均只有：

```text
Fab → Fab, uniform(7.5, 2.5), min
```

内部建模为外生、无容量运输：

- 新 release 和初始 WIP 进入首个/当前 step 前不添加运输；
- operation 完成且存在下一工序时，按当前机台 location 到下一工序 location 查表；
- 有匹配行时抽样并进入 `TRANSPORTING`，到 `TRANSPORT_ARRIVE` 后才可派工；
- 无匹配 from/to 行时运输为 0，但必须累计 `missing_transport_pair`，不得静默宣称有真实物流；
- transport 计入 cycle time 和跨越该区间的 CQT；
- 不创建 OHT/AGV/轨道资源。

当前状态为 `FROZEN-SPEC / NOT-IMPLEMENTED`。MC01、MC02 fixture 显式采用 0 搬运。

## 13. Provenance 与数据版本

每次 `SimulationResult` 必须包含：

```json
{
  "simulation_contract_version": "0.1.0",
  "dataset_version": "name@sha256:manifest_hash",
  "git_commit": "...",
  "seed": 42,
  "simulation_config": {},
  "dispatch_policy": "FIFO",
  "termination_condition": "until_all_complete",
  "horizon": null
}
```

数据集版本由相对文件名和每个文件的原始 SHA-256 再生成 manifest hash；计算过程只读，不重写 `datasets/`。

## 14. 已关闭项与剩余证据缺口

原 Simulation Contract 中以下项已在本地模型层面关闭：首工序搬运、release/repeat/due、Batch 兼容键、Setup 时长优先级、CQT 起止事件、Dedication 关系、故障抢占与修复、PM 冲突、uniform 第二参数、运输适用转移。

仍存在但不会被静默猜测的源数据证据缺口：

1. 初始 WIP 的历史 dedication 机台和已开启 CQT 起点时间不存在；采用第 6 节显式 cohort 规则。
2. 原始文件没有每台机初始 setup；本地模型固定为空 setup。
3. `uniform(m,w)` 的参数解释来自开源参考实现而非原始文件自描述；本地模型已固定为均值和全宽。
4. `TRACE`、多数 `IGNORE` 内容的业务展示含义暂缓，不影响事件逻辑。

这些缺口不允许通过 UI 或报告措辞伪装成已知事实。HVLM/LVHM 正式实验仍要等待 8 个 micro case 全部通过。
