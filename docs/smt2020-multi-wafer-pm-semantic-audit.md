# SMT2020 同机多 Wafer PM 原始语义审计

审计日期：2026-09-29。范围为 `datasets/SMT2020_HVLM`、`datasets/SMT2020_LVHM` 中的 `pmcal.txt`、`attach.txt`、`tool.txt.1l`、`part.txt`、`route_*.txt` 和 `WIP.txt`，并对照当前 Data Contract、Simulation Contract 与固定 PySCFabSim commit `0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a`。

本文是**只读 raw 画像和语义边界审计**，不修改契约、Data Integration Gate、代码或 `datasets/`。本文不把同机多 Wafer PM 宣称为已支持，也不关闭任何 blocker。

## 1. 结论摘要

两模型的 raw 都包含同一结构：

- 292 条 `pmcal` 记录：79 条 `mtbpm_by_cal`，213 条 `mtbpm_by_pieces`；
- 213 条按加工片数触发的 PM 均按 `WK/MN/QT` 三条附着到同一 `STNFAM`；
- 按项目现有 `STNQTY` 展开，HVLM 有 692 台、LVHM 有 606 台物理机各自带三条 wafer PM；
- 所有 wafer-PM 家族都通过 `STNGRP` 与一条 Failure attachment 相交；
- raw 中没有任何同一 `STNFAM/STN` 同时拥有 wafer PM 和 Calendar PM 的组合；
- wafer PM 的 `attach.FOAUNITS` 为空。项目 Data Contract 将空单位映射为加工 wafer 数，这是本地解释，不是 raw 的单位自描述；
- `WIP.txt` 没有 machine、当前 setup、剩余加工时间、PM counter、CQT 起点或 dedication machine 历史；初始 counter 不能从 raw 恢复。

因此，最小真实候选只能是“同一真实机台的三条 wafer PM + Failure + 一个无 batch/setup/sampling/rework 的单工序 WIP 映射诊断”。它不能作为完整路线、同机多 PM 并行、初始历史或 Gate 关闭证据。

## 2. 证据等级与术语边界

| 等级 | 含义 | 本文用途 |
| --- | --- | --- |
| A | raw 单字段直接观测 | 行号、字段值、空值、单位和记录数 |
| B | raw 跨表连接或 `STNQTY` 展开 | `PMCALNAME → attach.CALNAME → STNFAM → tool.STNQTY` |
| D | 固定 PySCFabSim 参考实现交叉证据 | 只能说明代码行为，不能升级为 SMT2020 真值 |
| E | 本项目显式本地规则 | 例如完成边界、counter reset、同刻 owner |
| F | raw 无法恢复的历史 | 初始 PM counter、历史 machine、CQT 起点等 |

本文把 `MTBPM`、`MTTR`、`MTTR2`、`FOA` 先按 raw 列名报告；只有明确标注为项目映射时，才使用内部的 wafer/minute 解释。

## 3. 两模型 raw 统计

### 3.1 PMCAL、MTTR 和 FOA

两模型的 `pmcal.txt` 行数和类型计数相同；具体参数数值在 wafer-PM 记录上有所不同。

| `PMCALTYPE` | 行数 | 家族数 | `MTBPMUNITS` | `MTBPM` 范围 | `MTTRDIST` / `MTTRUNITS` | raw `MTTR` 范围 | raw `MTTR2` 范围 |
| --- | ---: | ---: | --- | ---: | --- | ---: | ---: |
| `mtbpm_by_cal` | 79 | 34 | `day` | 7–91 | 全部 `uniform` / `hr` | 6.65–52.42 hr | 1.33–10.48 hr |
| `mtbpm_by_pieces` | 213 | 71 | `pieces` | 1000–156000 | 全部 `uniform` / `hr` | 8.36–68.37 hr | 1.67–13.67 hr |

`attach.txt` 中 PM 的首次触发字段如下：

| 类别 | `FOADIST` | `FOAUNITS` | HVLM `FOA` 范围 | LVHM `FOA` 范围 |
| --- | --- | --- | ---: | ---: |
| Calendar PM | 全部 `constant` | 全部 `day` | 6.5–102.8 | 6.4–100.1 |
| Wafer PM | 全部 `constant` | 全部为空 | 970–162240 | 900–154440 |

Wafer PM 的 `MTBPMUNITS=pieces` 是 raw 明示；但其 `attach.FOAUNITS` 为空，不能仅凭 raw 把 `FOA` 数值说成 wafer、piece、lot 或时间。当前 Data Contract §11 的“空单位按已加工 wafer 数”是 E 级项目映射，后续报告必须保留该边界。

Failure attachment 在两模型均为 11 条，均为 `FOADIST=exponential`、`FOA=10080`、`FOAUNITS=min`；这与 wafer PM 的空 `FOAUNITS` 不是同一单位体系。

### 3.2 同机三条 wafer PM 的族级结构

213 条 wafer PM attachment 正好形成 71 个 `STNFAM` 家族，每个家族均有 `WK/MN/QT` 三条；不存在只附着一条或两条的 wafer-PM 家族。

| 模型 | wafer-PM 家族 | wafer-PM raw 行 | 按 `STNQTY` 展开的物理机 | 物理机×3 PM 边 |
| --- | ---: | ---: | ---: | ---: |
| HVLM | 71 | 213 | 692 | 2076 |
| LVHM | 71 | 213 | 606 | 1818 |

这些是基于 `tool.txt.1l` 的 `STNQTY` 进行的 B 级展开；raw 本身只给设备模板/数量，不给独立的物理机 ID。项目 loader 的稳定命名（如 `DE_BE_11#0001`）不能反写成 raw 事实。

### 3.3 代表族/机和逐行证据

以下三组覆盖 non-cascade、cascade、不同设备组，展示同一族内三条 wafer PM 如何连接到同一设备模板。每组的行号在 HVLM/LVHM 相同。

| 族/机 | 组 | HVLM `pmcal` / `attach.FOA` | LVHM `pmcal` / `attach.FOA` | `tool.STNQTY` HVLM/LVHM | `STNCAP` raw |
| --- | --- | --- | --- | ---: | --- |
| `DE_BE_11` | `Dry_Etch` | `2000/8700/26100 pieces` / `1880/8178/24534` | `2222/9667/29000 pieces` / `2244/9764/29290` | 10 / 9 | 空 |
| `Implant_128` | `Implant` | `8000/34700/104100` / `8000/34700/104100` | `7364/31909/95727` / `7438/32228/96684` | 10 / 11 | 2.0 |
| `WE_FE_85` | `Wet_Etch` | `7500/32500/97500` / `6675/28925/86775` | `7000/30500/91500` / `7560/32940/98820` | 2 / 2 | 2.0 |

逐行证据：

- `DE_BE_11`：`pmcal.txt:81-83`、`attach.txt:92-94`、`tool.txt.1l:2`；同组 Failure 为 `attach.txt:5` 的 `BREAK_Dry_Etch`。
- `Implant_128`：`pmcal.txt:183-185`、`attach.txt:194-196`、`tool.txt.1l:54`；同组 Failure 为 `attach.txt:6` 的 `BREAK_Implant`。
- `WE_FE_85`：`pmcal.txt:291-293`、`attach.txt:302-304`、`tool.txt.1l:107`；同组 Failure 为 `attach.txt:12` 的 `BREAK_Wet_Etch`。

`STNCAP=2.0` 只说明这些工具进入项目的 cascade 标记集合；`STNCAP` 为空的 `DE_BE_11` 也只是“raw 未见 2”，不是 raw 文本对完整 non-cascade 物理语义的自描述。

## 4. Failure 与 Calendar PM 的交集

### 4.1 Failure 交集

11 条 Failure attachment 均以 `RESTYPE=stngrp` 连接设备组。按 `tool.STNGRP` 展开后：

| 类别 | HVLM | LVHM | 结论 |
| --- | ---: | ---: | --- |
| wafer-PM 家族与 Failure group 相交 | 71/71 | 71/71 | 每个 wafer-PM 家族都有一条 group-level Failure 来源 |
| Calendar-PM 家族与 Failure group 相交 | 34/34 | 34/34 | 每个 Calendar-PM 家族也都有一条 group-level Failure 来源 |

这只证明静态 attachment 连接，不证明 Failure 与 wafer PM 的中断、抢占、恢复、同刻顺序已经在同机三 PM 组合上通过 runtime 验证。

### 4.2 Calendar PM 交集

34 个 `mtbpm_by_cal` 家族与 71 个 `mtbpm_by_pieces` 家族没有交集，且两模型中 `STN` 名称没有重复。因此：

- raw 没有同一 `STNFAM/STN` 同时附着 Calendar PM 和 wafer PM；
- 不能从 raw 选出“同一 physical machine = Failure + Calendar PM + 三条 wafer PM”；
- `Litho_BE_110` 可作为 Calendar PM 对照，但不是 wafer-PM 组合：`pmcal.txt:36-38`、`attach.txt:47-49`、`tool.txt.1l:60`，HVLM `STNQTY=28`、LVHM `STNQTY=23`，三条是 `WK/MN/QT` Calendar PM。

这与现有多 Calendar 审计中的逐机分布一致：HVLM/LVHM 分别有 692/606 台 `(failure=1, calendar PM=0, wafer PM=3)` 物理机；raw 没有同时带 calendar PM 与 wafer PM 的物理机组合。

## 5. 最小真实单工序候选

### 5.1 选择

建议把 `part_4 → r_4:242 → DE_BE_11` 作为两模型共同的**单工序静态/诊断 slice**：

- `part.txt:3`：`part_4 → route_4.txt → r_4`；
- `route_4.txt:243`：`STEP=242`、`DESC=505_Dry_Etch`、`STNFAM=DE_BE_11`、`PTPER=per_lot`；
- `tool.txt.1l:2`：HVLM `STNQTY=10`、LVHM `STNQTY=9`，LOAD/UNLOAD 均为 1 min，`STNCAP` 为空；
- wafer PM：`pmcal.txt:81-83`、`attach.txt:92-94`；
- Failure：`attach.txt:5`，`BREAK_Dry_Etch → STNGRP=Dry_Etch`。

真实初始 WIP：

| 模型 | `WIP.txt` 行 | LOT | `PART/CURSTEP/PIECES` |
| --- | --- | --- | --- |
| HVLM | 1660、1661、1666、1697 | `Init_Lot_4_221/222/227/257` | `part_4 / 242 / 25` |
| LVHM | 1118、1128 | `Init_Lot_4_41/51` | `part_4 / 242 / 25` |

该候选保留真实 `pmcal`、`attach`、`tool`、`route` 和 WIP 行号，适合验证 raw → static model 的同机三 PM + Failure 连接。它不是完整 `route_4` 仿真，不应使用完整路线 KPI 解释。

### 5.2 当前工序的省略/依赖矩阵

| 机制 | 当前工序 raw 观测 | 候选处理 | 边界 |
| --- | --- | --- | --- |
| Cascade | `STNCAP` 未见 `2`，无 `PartInterval/BatchInterval` | 可作为受限 non-cascade 候选 | 不外推到其他 route 或完整 cascade 语义 |
| Batch | `PTPER=per_lot`，`BATCHMN/BATCHMX` 为空 | 省略 | 只对 step 242 成立 |
| Setup | `SETUP/WHEN/STIME` 为空 | 省略 | 不代表机台 setup 历史已知 |
| Sampling | `StepPercent` 为空 | 省略 | 当前工序不产生 sampling decision |
| Rework | `RWKSTEP/REWORK/RWKTYPE` 为空 | 省略 | 不代表完整 route 无 rework |
| LOAD/UNLOAD | tool 中均为 1 min | 若进入受限 runtime，保留独立 phase | 不得并入纯 processing |
| Failure | group-level `BREAK_Dry_Etch` 存在 | 可保留为静态 provenance | 组合行为仍需单独验证 |
| 三条 wafer PM | 同机 `WK/MN/QT` | raw 连接保留 | 当前 `PMRuntime` 不接受同机多条 wafer PM |
| Calendar PM | `DE_BE_11` 无 `mtbpm_by_cal` | 省略 | 不是 raw 同机组合 |

如果从 step 242 继续完整执行 `route_4`，后续仍存在 batch、setup、sampling、rework 和 cascade interval；因此本候选必须明确叫“单工序诊断 slice”，不能称为完整路线 slice。

## 6. 初始历史、同刻 due 与计数边界

`WIP.txt` 的字段只有 `LOT/PART/PRIOR/PIECES/START/CURSTEP/DUE/ORDER/HOTLOT/TRACE`。对上述 WIP 行，raw 没有：

- 进入当前 step 时所选的 physical machine；
- 当前 setup 和 setup run count；
- 当前加工阶段、已加工时长或剩余时长；
- 每条 wafer PM 的初始 counter、pending 或 active 状态；
- 已开启 CQT 的 source finish 时间；
- 历史 dedication machine 和 visit/rework 状态。

因此：

1. 不能根据 `CURSTEP=242` 推断这些 lot 当前正在某台机上加工；
2. 不能根据 `PIECES=25` 推断 PM counter 是 0、25 或任何其他值；
3. 不能根据 `FOA`、`MTBPM` 或 `DUE` 推断仿真零点时哪些 PM 已经 due；
4. 三条 wafer PM 在同一 machine 的初始 counter 是否分别存在、是否共享历史，也没有 raw 证据；
5. raw 没有给出同刻 `due` 事件的优先级、PM-PM owner 或 Failure/PM 同刻顺序。

当前项目的 `wafer PM counter=0`、完成后 reset-to-zero、完成边界绑定 `PROCESS_FINISH/BATCH_FINISH`、pending 在下一次派工前执行、Failure 同刻优先等均是 E 级本地契约规则；它们不能被描述成 raw 恢复的 Fab 历史。项目 loader 已将初始 counter 记录为 unknown，运行时若使用 0 只能标注为 fallback/假设。

## 7. 参考实现与本项目契约的差异

固定 PySCFabSim commit 的静态审计（见 `docs/pyscfabsim-semantic-diff.md`，以及该 commit 的 `simulation/file_instance.py`、`simulation/instance.py`）显示：

- `file_instance.py` 将空 `FOAUNITS` 的附件放入按片触发路径，并为每台 machine 保存多条维护阈值、首次计数和时长分布；这是 D 级代码解释，不是 raw 自带单位；
- `instance.py` 在 **dispatch 时**按附件数组顺序，对每条计数器扣除派工 lot 的片数；若达到阈值，抽取该条 PM 时长并加入 machine 占用时间，然后把该计数器重置为周期阈值；
- 这一计数时点和“时长加入占用”的实现，与本项目已冻结的“真实加工完成后累计、独立 downtime owner”不同。参考实现可证明其自身怎样处理多条计数器，不能证明 SMT2020 的物理真值，也不能直接替换本项目契约；
- raw 和参考实现均不足以恢复初始历史，或裁决本项目独立事件模型下的同刻 PM-PM/Failure owner 顺序。

当前项目契约与 raw/参考实现之间有以下必须保留的差异：

| 事项 | 参考实现/ raw 证据 | 本项目契约 | 审计结论 |
| --- | --- | --- | --- |
| 同机多 wafer PM | raw 每台相关物理机有 `WK/MN/QT` 三条；参考实现为 machine 保存多条计数器 | 当前 `Scenario/PMRuntime` 拒绝同一 machine 多条 wafer PM | 结构性未闭环，不能静默折叠或任选一条 |
| Wafer PM 计数 | raw 只给 `MTBPMUNITS=pieces`，WIP 不给初始 counter | 在真实 `PROCESS_FINISH/BATCH_FINISH` 后按 wafer 累计 | 这是 E 级运行规则，非 raw 历史 |
| FOA 空单位 | raw wafer-PM `FOAUNITS` 全为空 | Contract 将空单位解释为加工 wafer 数 | 需保留 A/E 边界，不能称为 raw 明示 |
| PM reset/余量 | raw 无 reset 或 carry 字段 | 完成后归零，超阈值余量不结转 | 仅本地冻结规则 |
| 同刻/重叠 PM | raw 无 PM-PM 优先级；参考实现按附件数组顺序把到期 wafer PM 时长相加 | 本地单一 active owner；多 wafer PM 的 pending 顺序尚未冻结，Calendar PM 的 `event_seq`/stale 规则不能外推 | 不能归因于 raw 或参考实现 |
| 周期续期 | raw 不定义 PM 冲突后的续期行为 | 本地 Calendar PM 按计划 occurrence 起点续期；wafer PM 在当前支持范围内完成后 reset-zero | 不得把 Calendar PM 规则外推到多 wafer PM |
| 初始 WIP | raw 无 machine/counter/CQT/dedication 历史 | unknown；0 只能是显式 fallback | F 级不可恢复历史 |

这类差异不等于参考实现“错误”或 raw “冲突已裁决”。参考实现只提供 D 级交叉证据；本项目必须优先保留 raw 事实、显式 E 级选择和 F 级未知。

## 8. 不关闭 blocker 的验收边界

本审计最多支持以下受限结论：

- 两模型 raw 都存在 71 个 wafer-PM 家族、每台相关物理机三条 `WK/MN/QT` 附件；
- 通过 `STNGRP` 可以静态证明这些家族与 Failure 相交；
- 可以从 `part/route/WIP/tool/pmcal/attach` 形成 `DE_BE_11` 单工序候选；
- 可以明确记录三条 wafer PM、Failure、Calendar PM、初始历史和同刻语义的省略项。

本审计不能支持以下结论：

- 同机多 Wafer PM 已在 `Scenario/PMRuntime` 中可执行；
- wafer PM counter 初始值为 0，或由 WIP 的 `PIECES` 推出；
- raw 给出了同刻 due 的 owner/优先级；
- raw 给出了 Calendar PM 与 wafer PM 同机组合；
- 单工序 slice 通过即可关闭 cascade、multi-calendar、rework、setup MINRUN 或完整附件 Gate；
- 可以启动正式 HVLM/LVHM 全路线实验、比较 KPI 或启用 CMA-ES。

`Data Integration Gate` 仍保持 `not_passed_gaps`；同机多 wafer PM、完整 Failure/PM/路线组合和初始历史仍需独立的 loader golden test、Scenario 约束和 runtime trace 证据。
