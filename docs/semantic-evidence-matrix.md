# SMT2020 Semantic Evidence Matrix

状态：M1 Closure Audit + SMT2020 Data Integration Gate 首轮证据基线

适用契约：Simulation Contract `0.1.3`

审计日期：2026-09-21

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
| 动态投放 | `START`、`RDIST`、`REPEAT` | A/B | release 时刻后进入当前工序队列；重复投放需由 loader 保留稳定实体编号 | 已映射 ReleaseTemplate；runtime 惰性生成仍为 blocker |
| 交期 | `DUE - START` | B | 以 lot 投放为基准换算内部绝对 due time | loader 推导已验证 |
| 优先级 | 数据中的 priority/hot-lot 信息 | A/B | 保存为 lot 属性，不改变硬可行性 | loader 静态映射已验证 |
| 设备资格 | `STNFAM`、`STN`、`STNQTY` | A/B | tool group 展开到稳定物理 machine ID；动作同时满足 qualification | 结构关系可推导；物理机展开链待接入 |
| 加工时间 | `PDIST`、`PTIME`、`PTIME2`、`PTPER` | A | 按字段指定的分布和 per-piece/per-batch 规则计算 | 微型运行时仅验证确定性时长；正式分布链未接入 |
| `uniform(m,w)` | 分布参数 | D/E | `Uniform[m-w/2, m+w/2]`，第二参数为全宽 | 原始文件不自描述；属于参考实现支持的本地规则 |
| Setup | route 的 `SETUP/WHEN/STIME`、setup 表、tool `SETUPGRP` | A/B | 有向换型；严格 resolver；Setup 独立占用 machine | loader 已映射；MINRUN runtime blocker |
| 初始 Setup | 无完整历史记录 | F/E | 默认空 setup，并在 provenance 中标识初始化规则 | 无法声称恢复真实历史状态 |
| Batch 容量 | `BATCHMN`、`BATCHMX` | A/B | 单位为 wafer；合法批次必须满足最小/最大 wafer 容量 | loader 静态映射已验证；随机时长与决策配置 blocker |
| Batch 加工与兼容 | `PTPER`、`BATCHCRITF`、`BATCHPER` | A/B | compatibility 与加工口径按字段组合生成 BatchSpec | 运行时接口可表达；真实字段组合尚未 loader 验证 |
| CQT | `STEP`、`STEP_CQT`、`CQT`、`CQTUNITS` | A/B | source `PROCESS_FINISH` 到 target `PROCESS_START`，支持跨步 | runtime 与 loader 静态关系均验证 |
| Dedication | `SVESTN`、`FORSTEP` | A/B | 建立后约束具体物理 machine，而非 tool group | runtime 与 loader 静态关系均验证 |
| 初始 Dedication | 无历史 machine | F | 不猜测绑定；记录 unknown/audit 状态 | 无法恢复真实历史绑定 |
| 初始 CQT clock | 无 source finish 历史时间 | F | 初始 WIP 不伪造已开启时钟 | 无法恢复真实历史起点 |
| Failure/SDT 配置 | down calendar、attach、FOA、distribution | A/B | loader 应生成 machine-level failure configuration | calendar/attach 已映射；exponential 与 multi-calendar runtime blocker |
| Failure preemptive-resume | 无充分原始业务说明 | D/E | 暂停同一 machine 上同一活动并恢复剩余时长 | 本项目显式语义，不表述为数据集规定 |
| PM calendar | `PMCAL`、attach、FOA | A/B | calendar PM 生成明确 PM occurrence | calendar/pieces/FOA 已映射；multi-calendar runtime blocker |
| Wafer PM | FOA/wafer trigger 相关记录 | A/B/E | 真实完成 wafer 才累计；达到阈值后下次 dispatch 前 PM | 字段存在，触发细节含本地规则 |
| Wafer PM reset | 无明确长期业务说明 | E | PM 完成后 `reset_zero` | 本项目显式规则，不表述为原始数据规定 |
| 初始 wafer PM counter | 无设备历史计数 | F/E | 默认值必须由 scenario 显式给出并写入 provenance | 无法恢复真实历史计数 |
| Failure/PM overlap ownership | 无充分原始业务说明 | E | 单一 downtime owner；重叠 occurrence 按 Contract 抑制/失效 | 本项目显式规则 |
| PM 抑制 stochastic failure 后重新起算 | 无充分原始业务说明 | E | PM 结束后按实体索引流重新安排下一 occurrence | 本项目显式规则 |
| Transport | route/transport-time 相关记录 | A/E | 当前主线使用外生延迟，不占运输资源；相邻设施统一 `uniform(7.5,2.5)` | 数据字段与本地简化边界并存；运行时尚未实现 |
| Sampling/rework | `SAMPLE`、返工/回跳相关字段 | A/B | 按 route occurrence 生成明确分支，不允许隐式修改 route | Contract/Data Contract 有定义，运行时未实现 |

## 3. 证据来源结论

- **来自原始数据（A/B）**：投放、交期、设备资格、加工参数、Setup、Batch、CQT、Dedication、Failure/PM 配置以及 transport 的字段或引用关系。
- **来自论文/官方资料（C）**：当前核心运行语义没有仅靠 C 级证据闭合的条目；本次审计不以二手论文替代本地文件和契约。
- **仅来自参考实现（D）**：`uniform(m,w)` 的“均值 + 全宽”解释，以及 Failure 抢占行为的交叉参考。参考实现只提供佐证。
- **本项目显式建模假设（E）**：初始 setup 回退、preemptive-resume、wafer PM `reset_zero`、Failure/PM 单 owner、PM 抑制 failure 后重新起算、外生且无资源的运输模型。
- **无法恢复的历史状态（F）**：初始 WIP 的真实 setup、dedication machine、已开启 CQT 起点、wafer PM counter，以及其他未保存在数据快照中的 machine history。

## 4. 报告使用规则

后续报告必须把 E 级规则写成“本项目建模假设”，把 F 级状态写成“数据不可恢复”。不得把 D/E/F 级结论表述成“SMT2020 数据集规定”或“真实 Fab 已验证”。

## 5. Loader 实测更新

Loader Contract `0.1.0` 已在真实 HVLM/LVHM 上完成静态映射。以下结论由实际 parser 和引用测试支持：

- Product/Route/Operation、STNFAM→具体 machine、Setup transition/group、Batch wafer bounds、CQT、Dedication、Failure/PM calendar/attach、Transport、ReleaseTemplate 和 Initial WIP 已进入 `SMT2020StaticModel`；
- raw route row 与 parsed operation 逐行 reconciliation，跨文件引用没有 ERROR；
- `uniform(m,w)` 仍为 D/E，loader 使用它不会把证据升级成 A；
- initial setup、dedication、CQT 和 wafer-PM counter 仍为 F/E，loader 只输出 warning/count，不恢复虚构历史；
- static mapping 不等于 runtime closure。随机 processing、release template、transport、sampling/rework、cascade、setup MINRUN、exponential failure 和 multi-calendar 仍为 Data Integration BLOCKER。

完整判定见 `smt2020-data-integration-gate.md`。
