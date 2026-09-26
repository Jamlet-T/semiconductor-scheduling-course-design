# SMT2020 Semantic Evidence Matrix

状态：M1 Closure Audit + SMT2020 Runtime Compatibility Gap Closure 证据基线

适用契约：Simulation Contract `0.1.5`

审计日期：2026-09-25

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
| 加工时间 | `PDIST`、`PTIME`、`PTIME2`、`PTPER` | A | 按字段指定的分布和 per-piece/per-batch 规则计算 | constant/uniform/exponential 与三种 basis 已接入统一 runtime；cascade interval 另列 blocker |
| `uniform(m,w)` | 分布参数 | D/E | `Uniform[m-w/2, m+w/2]`，第二参数为全宽 | 原始文件不自描述；属于参考实现支持的本地规则 |
| Setup | route 的 `SETUP/WHEN/STIME`、setup 表、tool `SETUPGRP` | A/B | 有向换型；严格 resolver；Setup 独立占用 machine | loader 已映射；MINRUN runtime blocker |
| 初始 Setup | 无完整历史记录 | F/E | 默认空 setup，并在 provenance 中标识初始化规则 | 无法声称恢复真实历史状态 |
| Batch 容量 | `BATCHMN`、`BATCHMX` | A/B | 单位为 wafer；合法批次必须满足最小/最大 wafer 容量 | loader 静态映射与随机 per-batch runtime 已验证；决策配置仍为 blocker |
| Batch 加工与兼容 | `PTPER`、`BATCHCRITF`、`BATCHPER` | A/B | compatibility 与加工口径按字段组合生成 BatchSpec | 真实字段组合已静态验证；raw 不提供 `B_target/T_max` |
| CQT | `STEP`、`STEP_CQT`、`CQT`、`CQTUNITS` | A/B | source `PROCESS_FINISH` 到 target `PROCESS_START`，支持跨步 | runtime 与 loader 静态关系均验证 |
| Dedication | `SVESTN`、`FORSTEP` | A/B | 建立后约束具体物理 machine，而非 tool group | runtime 与 loader 静态关系均验证 |
| 初始 Dedication | 无历史 machine | F | 不猜测绑定；记录 unknown/audit 状态 | 无法恢复真实历史绑定 |
| 初始 CQT clock | 无 source finish 历史时间 | F | 初始 WIP 不伪造已开启时钟 | 无法恢复真实历史起点 |
| Failure/SDT 配置 | down calendar、attach、FOA、distribution | A/B | loader 应生成 machine-level failure configuration | exponential 已接入统一 sampler；multi-calendar runtime 仍为 blocker |
| Failure preemptive-resume | 无充分原始业务说明 | D/E | 暂停同一 machine 上同一活动并恢复剩余时长 | 本项目显式语义，不表述为数据集规定 |
| PM calendar | `PMCAL`、attach、FOA | A/B | calendar PM 生成明确 PM occurrence | calendar/pieces/FOA 已映射；multi-calendar runtime blocker |
| Wafer PM | FOA/wafer trigger 相关记录 | A/B/E | 真实完成 wafer 才累计；达到阈值后下次 dispatch 前 PM | 字段存在，触发细节含本地规则 |
| Wafer PM reset | 无明确长期业务说明 | E | PM 完成后 `reset_zero` | 本项目显式规则，不表述为原始数据规定 |
| 初始 wafer PM counter | 无设备历史计数 | F/E | 默认值必须由 scenario 显式给出并写入 provenance | 无法恢复真实历史计数 |
| Failure/PM overlap ownership | 无充分原始业务说明 | E | 单一 downtime owner；重叠 occurrence 按 Contract 抑制/失效 | 本项目显式规则 |
| PM 抑制 stochastic failure 后重新起算 | 无充分原始业务说明 | E | PM 结束后按实体索引流重新安排下一 occurrence | 本项目显式规则 |
| Transport | route location 与 `fromto` | A/B/D/E | 当前主线使用外生延迟，不占运输资源；仅已配置 `Fab→Fab` 使用 `uniform(7.5,2.5)`，其余 pair 为零并显式审计 | runtime、CRN、CQT、fixed horizon 与真实 configured/missing-pair slice 已验证 |
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

Loader Contract `0.1.4` 已在真实 HVLM/LVHM 上完成静态映射与受限 runtime validation slice。以下结论由实际 parser、runtime 和引用证据支持：

- Product/Route/Operation、STNFAM→具体 machine、Setup transition/group、Batch wafer bounds、CQT、Dedication、Failure/PM calendar/attach、Transport、ReleaseTemplate 和 Initial WIP 已进入 `SMT2020StaticModel`；
- raw route row 与 parsed operation 逐行 reconciliation，跨文件引用没有 ERROR；
- `uniform(m,w)` 仍为 D/E，loader 使用它不会把证据升级成 A；
- initial setup、dedication、CQT 和 wafer-PM counter 仍为 F/E，loader 只输出 warning/count，不恢复虚构历史；
- processing distribution/PTPER、exponential failure 与外生 transport 已形成 raw→Scenario→runtime→test 闭环；transport 的未配置 pair 在静态 reconciliation 和运行结果中均显式审计；
- release template 已在受限 profile 关闭 blocker：HVLM 5 个 template/442000 capacity，LVHM 21 个 template/2202000 capacity，raw `START` 全为 0、`RDIST=constant/min`、`LOTSPERRPT=1`、`PIECES=25`、`HOTLOT=no`；非 constant、`LOTSPERRPT>1`、非 fixed-horizon 和未验证组合不宣称支持。
- sampling 基础判定已形成 raw→Scenario→decision/trace/ledger/provenance→audit 诊断链：HVLM/LVHM 显式 221/955、随机 149/662、100% 72/293、initial-WIP 98/75、rework overlap 14/52；
- 全部显式 sampled 工序（221/955）都使用带 `LOAD=1 min / UNLOAD=1 min` 的 tool template，诊断 slice 未执行这两段时长；因此它不是物理 duration 闭环；
- raw sampled CQT target 为 HVLM 4、LVHM 18，逐条均为 p100，stochastic endpoint 为 0；p100 不存在 skip 分支，sampling blocker 关闭；未来 p<100 endpoint 仍显式 unsupported；
- static mapping 不等于完整 runtime closure。rework、load/unload/cascade、setup MINRUN、batch `B_target/T_max` 与 multi-calendar 仍构成 Data Integration 的 5 类 blocker。

完整判定见 `smt2020-data-integration-gate.md`。
