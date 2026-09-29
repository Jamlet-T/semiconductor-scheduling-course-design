# SMT2020 Semantic Evidence Matrix

状态：M1 Closure Audit + SMT2020 Runtime Compatibility Gap Closure 证据基线

适用契约：Simulation Contract `0.1.9`；Data Contract `0.1.8`；Loader Contract `0.1.7`

审计日期：2026-09-28

本文只回答“某项语义的依据来自哪里”。它不把微型场景运行时验证等同于正式 SMT2020 数据接入验证。

## 1. 证据等级

| 等级 | 含义 |
| --- | --- |
| A | 原始 SMT2020 文件直接给出字段或记录 |
| B | 由原始字段及其引用关系、单位或结构推导 |
| C | 论文或官方格式文档支持 |
| D | PySCFabSim 或其他开源实现支持，仅作参考证据 |
| E | 本项目显式、版本化的本地建模假设 |
| F | 原始数据缺少历史状态，无法恢复真实值 |

等级不是可信度排名。A/B 说明数据来源，E 说明仿真边界；D 不能替代本地语义审计，F 不能用推测补齐。

## 2. 语义证据矩阵

| 语义 | 原始字段或对象 | 等级 | 当前冻结解释 | 审计结论 |
| --- | --- | --- | --- | --- |
| 动态投放 | `START`、`RDIST`、`REPEAT`、`RPT#`、`LOTSPERRPT` | A/B | release 时刻后进入当前工序队列；重复投放惰性生成，`RPT#` 包含 index 0，稳定 ID 使用 model/source-row namespace | 受限 runtime 支持已形成；仅 `fixed_horizon + constant RDIST + LOTSPERRPT=1` |
| 交期 | `DUE - START` | B | 以每个 lot 实际投放为基准换算绝对 due time | loader 推导与 release slice 已验证；每个重复 lot 独立平移 |
| 优先级 | 数据中的 priority/hot-lot 信息 | A/B | 保存为 lot 属性，不改变硬可行性；策略不得自动读取 | loader 静态映射已验证 |
| Release identity | `model/source row`、`LOT`、`PART`、`ORDER` | A/B | `REL::<template_id>::<lot_prefix>::r<repeat_index:06d>::m<member_index:04d>`；字段进入 immutable domain、trace、provenance | release audit 已冻结 ID/来源要求 |
| 设备资格 | `STNFAM`、`STN`、`STNQTY` | A/B | tool group 展开到稳定物理 machine ID；动作同时满足 qualification | loader 已展开并由真实 validation slice 使用具体物理机 |
| 加工时间 | `PDIST`、`PTIME`、`PTIME2`、`PTPER` | A | 按字段指定的分布和 per-piece/per-batch 规则计算；non-cascade 受限链在 committed `PROCESS_START` 只抽样一次 | constant/uniform/exponential 与三种 basis 已接入统一 runtime；cascade interval 另列 blocker |
| `uniform(m,w)` | 分布参数 | D/E | `Uniform[m-w/2, m+w/2]`，第二参数为全宽 | 原始文件不自描述；属于参考实现支持的本地规则 |
| Setup / MINRUN | route 的 `SETUP/WHEN/STIME`、setup/setupgrp 表、tool `SETUPGRP` | A/B/D/E/F | 有向换型；严格 resolver；MINRUN 本地按成功完成的 lot 数计，初始历史缺失按可审计下界处理 | MINRUN 合成场景 runtime 已实现；与参考实现派工时扣减有意不同；真实组合仍为 blocker |
| 初始 Setup | 无完整历史记录 | F/E | 默认空 setup，并在 provenance 中标识初始化规则 | 无法声称恢复真实历史状态 |
| Batch 容量 | `BATCHMN`、`BATCHMX` | A/B | 单位为 wafer；合法批次必须满足最小/最大 wafer 容量 | loader 静态映射与随机 per-batch runtime 已验证；显式 v1 配置下真实 WIP target/timeout slice 已验证 |
| Batch 加工与兼容 | `PTPER`、`BATCHCRITF`、`BATCHPER` | A/B/E | compatibility 与加工口径按字段组合生成 BatchSpec；`B_target=raw BATCHMX/T_max=60 min` 是版本化本地选择 | raw 不提供 `B_target/T_max`；无配置仍为 blocker，配置下仅为受限决策闭环，L/U/calendar 另阻塞 |
| CQT | `STEP`、`STEP_CQT`、`CQT`、`CQTUNITS` | A/B | source `PROCESS_FINISH` 到 target `PROCESS_START`，支持跨步 | runtime 与 loader 静态关系均验证 |
| Dedication | `SVESTN`、`FORSTEP` | A/B | 建立后约束具体物理 machine，而非 tool group | runtime 与 loader 静态关系均验证 |
| LOAD/PROCESS_CORE/UNLOAD | `LTIME/ULTIME`、`STNCAP`、`MachineSpec` | A/B/E | 阶段 runtime 仅限 non-cascade、non-batch；真实 loader slice 另外筛选无 Setup/sampling/rework/CQT/Dedication/interval：LOAD 与 UNLOAD 独立占用，core `PROCESS_START` 关闭 CQT，卸载后 `PROCESS_FINISH` 为 canonical completion；Failure/PM 按剩余时长恢复 | 合成 runtime（含 CQT+正 L/U）与真实 `r_3:18→19` 两模型 slice 已形成受限证据；其他合格机、日历和初始历史省略，cascade 仍 blocker |
| 初始 Dedication | 无历史 machine | F | 不猜测绑定；记录 unknown/audit 状态 | 无法恢复真实历史绑定 |
| 初始 CQT clock | 无 source finish 历史时间 | F | 初始 WIP 不伪造已开启时钟 | 无法恢复真实历史起点 |
| Failure/SDT 配置 | down calendar、attach、FOA、distribution | A/B | loader 应生成 machine-level failure configuration | exponential 已接入统一 sampler；Calendar PM 诊断 slice 明确省略 Failure，完整 Failure×Calendar PM runtime 仍为 blocker |
| Failure preemptive-resume | 无充分原始业务说明 | D/E | 暂停同一 machine 上同一活动并恢复剩余时长 | 本项目显式语义，不表述为数据集规定 |
| PM calendar | `PMCAL`、attach、FOA | A/B | 同一物理机的每条 calendar 均生成独立 `CalendarPMSpec`；runtime identity 为 `<PMCALNAME>@<machine_id>`，FOA/interval/duration 保留原始来源和转换值 | `Litho_BE_110` 三条 Calendar PM 的真实 selector/source-row/provenance 已形成受限诊断 slice；只证明 Calendar PM 子链，不证明 full-fab 组合 |
| Wafer PM | FOA/wafer trigger 相关记录 | A/B/E | 真实完成 wafer 才累计；达到阈值后下次 dispatch 前 PM | 全量仍有 `213` 条 wafer-PM attachment rows；multi-Wafer PM 与 Failure/Calendar PM 联动未由 Calendar PM 子链关闭，仍为 blocker |
| Wafer PM reset | 无明确长期业务说明 | E | PM 完成后 `reset_zero` | 本项目显式规则，不表述为原始数据规定 |
| 初始 wafer PM counter | 无设备历史计数 | F/E | 默认值必须由 scenario 显式给出并写入 provenance | 无法恢复真实历史计数 |
| Failure/PM overlap ownership | 无充分原始业务说明 | E | 单一 downtime owner；重叠 occurrence 按 Contract 抑制/失效 | 合成 runtime 规则已冻结；真实 Calendar PM slice 省略 Failure/Wafer PM，不宣称真实组合集成通过 |
| PM 抑制 stochastic failure 后重新起算 | 无充分原始业务说明 | E | PM 结束后按实体索引流重新安排下一 occurrence | 本项目显式规则 |
| Transport | route location 与 `fromto` | A/B/D/E | 当前主线使用外生延迟，不占运输资源；仅已配置 `Fab→Fab` 使用 `uniform(7.5,2.5)`，其余 pair 为零并显式审计 | runtime、CRN、CQT、fixed horizon 与真实 configured/missing-pair slice 已验证；L/U slice 仅使用 Fab→Fab |
| Sampling | `StepPercent` | A/B/C/D/E | `None` 始终执行；显式 100% 不抽随机；`0<p<100` 在 operation entry 以稳定流判断，失败写 skip trace | runtime/initial-WIP 诊断已验证；sampled CQT targets 4/18 全为 p100，stochastic endpoint=0，sampling blocker 已关闭；load/unload 另列 blocker |
| Rework | `RWKSTEP/REWORK/RWKTYPE` | A/B/C/D | raw 为 lot scope 并回到更早 step；不得在证据不足时假定每 visit 或仅一次触发 | 字段组与回跳引用已静态验证；参考实现是每 lot/source 最多一次，现 Data Contract 未冻结本地选择，runtime 未实现 |

## 3. 证据来源结论

- **来自原始数据（A/B）**：投放、交期、设备资格、加工参数、Setup、Batch、CQT、Dedication、Failure/PM 配置以及 transport 的字段或引用关系。
- **来自论文/官方资料（C）**：SMT2020 论文将 metrology inspection frequency 描述为 sampling rate，并给出多类典型频率；它支持字段用途，不定义本项目 CQT/rework 组合事件顺序。
- **仅来自参考实现（D）**：`uniform(m,w)` 的“均值 + 全宽”、Failure 抢占，以及 StepPercent 的执行概率/派工可见前判定作为交叉参考。参考实现只提供佐证。
- **本项目显式建模假设（E）**：初始 setup 回退、preemptive-resume、wafer PM `reset_zero`、Failure/PM 单 owner、PM 抑制 failure 后重新起算、外生且无资源的运输模型，以及 sampling identity/trace 结构。
- **无法恢复的历史状态（F）**：初始 WIP 的真实 setup、dedication machine、已开启 CQT 起点、wafer PM counter，以及其他未保存在数据快照中的 machine history。

## 4. 报告使用规则

后续报告必须把 E 级规则写成“本项目建模假设”，把 F 级状态写成“数据不可恢复”。不得把 D/E/F 级结论表述成“SMT2020 数据集规定”或“真实 Fab 已验证”。

## 5. Loader 实测更新

Loader Contract `0.1.7` 已在真实 HVLM/LVHM 上完成静态映射与受限 runtime validation slice。以下结论由实际 parser、runtime 和引用证据支持；其中 multi-calendar 只代表诊断 slice，不代表真实 full-fab 集成测试通过：

- Product/Route/Operation、STNFAM→具体 machine、Setup transition/group、Batch wafer bounds、CQT、Dedication、Failure/PM calendar/attach、Transport、ReleaseTemplate 和 Initial WIP 已进入 `SMT2020StaticModel`；
- raw route row 与 parsed operation 逐行 reconciliation，跨文件引用没有 ERROR；
- `uniform(m,w)` 仍为 D/E，loader 使用它不会把证据升级成 A；
- initial setup、dedication、CQT 和 wafer-PM counter 仍为 F/E，loader 只输出 warning/count，不恢复虚构历史；
- processing distribution/PTPER、exponential failure 与外生 transport 已形成 raw→Scenario→runtime→test 闭环；transport 的未配置 pair 在静态 reconciliation 和运行结果中均显式审计；
- release template 已在受限 profile 关闭 blocker：HVLM 5 个 template/442000 capacity，LVHM 21 个 template/2202000 capacity，raw `START` 全为 0、`RDIST=constant/min`、`LOTSPERRPT=1`、`PIECES=25`、`HOTLOT=no`；非 constant、`LOTSPERRPT>1`、非 fixed-horizon 和未验证组合不宣称支持。
- sampling 基础判定已形成 raw→Scenario→decision/trace/ledger/provenance→audit 诊断链：HVLM/LVHM 显式 221/955、随机 149/662、100% 72/293、initial-WIP 98/75、rework overlap 14/52；
- 全部显式 sampled 工序（221/955）都使用带 `LOAD=1 min / UNLOAD=1 min` 的 tool template，诊断 slice 未执行这两段时长；因此它不是物理 duration 闭环；
- raw sampled CQT target 为 HVLM 4、LVHM 18，逐条均为 p100，stochastic endpoint 为 0；p100 不存在 skip 分支，sampling blocker 关闭；未来 p<100 endpoint 仍显式 unsupported；
- batch v1 config 显式绑定两模型 manifest，并将 E 级 `raw BATCHMX/60 min` 与 canonical hash 写入 provenance；HVLM target、LVHM timeout 两条真实 initial-WIP slice 通过 per-batch CRN 和结果不变量审计，load/unload/calendar 省略仍有 warning；
- non-cascade、non-batch 的真实 L/U slice 固定为两模型 `r_3:18→19`、指定 initial WIP 和每道首台合格物理机，保留 1 min LOAD/UNLOAD 与 Fab→Fab transport；其他合格机、Failure/PM attachment 和初始历史显式写入 provenance 并省略；
- multi-calendar 诊断 slice 固定为 HVLM `part_3/r_3/491/Init_Lot_3_134`（route row 492、WIP row 140，QT/MN overlap）和 LVHM `part_2/r_2/459/Init_Lot_2_22`（route row 460、WIP row 508，WK/QT overlap），均选 `Litho_BE_110#0001`；两模型各保留 `Litho_BE_110_WK/MN/QT`，复合 `pm_id`、FOA、interval、duration、`pmcal.txt:36–38`、`attach.txt:47–49` 与 `tool.txt.1l:60` machine source row 均进入 provenance；
- multi-calendar slice 明确为单机/单工序/单 WIP Calendar PM 子链：省略 `BREAK_Litho` Failure、其他资格机、后续 route/rework 和初始 setup/CQT/dedication/wafer-PM counter。它不加载 Wafer PM，也不将全量 `213` 条 wafer-PM attachment rows 或 HVLM/LVHM `351/307` 台多 Calendar PM 物理机 blocker 误报为已解决；
- static mapping 不等于完整 runtime closure。受限 L/U 子链通过不等于 cascade 语义关闭；显式 v1 batch 配置下 rework、load/unload/cascade、setup MINRUN 与 multi-calendar 仍构成 Data Integration 的 4 类 blocker；无配置默认 audit 另有 batch 参数 blocker。

### Calendar PM 诊断 slice 的证据边界

三条 raw Calendar PM 均为 `PMCALTYPE=mtbpm_by_cal`，并通过 `stnfam/Litho_BE_110` attachment 连接到同一台 `Litho_BE_110#0001`。runtime 使用复合 identity `Litho_BE_110_{WK|MN|QT}@Litho_BE_110#0001`；raw source rows 为 `pmcal.txt:36/37/38` 与 `attach.txt:47/48/49`。HVLM 的 FOA 为 `6.9/29.4/89.2 day`，LVHM 为 `7/30/91 day`；三条 interval 均为 `7/30/91 day`，duration 均为 `uniform(6.65/13.29/26.59 hr, 1.33/2.66/5.32 hr)`。这些值转换为分钟后写入 `calendar_mapping`，而不是另造固定时刻 occurrence。

诊断 selector、source-row、复合 `pm_id`、FOA/interval/duration、overlap/horizon、omitted IDs 与 manifest/raw hashes 均写入 `DatasetProvenanceSpec.loader_config`。warning 代码为 `DI_MULTI_CALENDAR_SLICE_OMITS_FAILURE`、`DI_MULTI_CALENDAR_SLICE_OMITS_OTHER_MACHINES`、`DI_MULTI_CALENDAR_SLICE_OMITS_ROUTE_HISTORY`、`DI_MULTI_CALENDAR_SLICE_INITIAL_HISTORY_UNKNOWN` 和 `DI_MULTI_CALENDAR_SLICE_NOT_FULL_FAB`；此外全量 `DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT` 仍为 BLOCKER，Gate 仍是 `not_passed_gaps`。因此这里的 A/B 证据支持 raw 映射，E/F 支持省略与不可恢复历史边界，但不能升级为“SMT2020 full-fab 已集成”。

受限运行验收记录为 `test_smt2020_multi_calendar_validation.py: 7 passed`，本轮 full regression `293 passed`；验收覆盖两模型 raw→Scenario→`simulate()`、真实 overlap 时钟、audit/CRN/provenance。该结果是诊断 slice 的运行证据，不是 full-fab 集成、正式 KPI 或 HVLM/LVHM 策略实验结果。

完整判定见 `smt2020-data-integration-gate.md`。
