# SMT2020 Data Integration Gate

审计日期：2026-09-29
本轮版本核对：Simulation Contract `0.1.9`、Data Contract `0.1.8`；本轮最终 Git commit 与回归结果以验收记录为准
Simulation Contract：`0.1.9`
Data Contract：`0.1.8`
Policy Contract：`0.1.1`
Loader Contract：`0.1.7`

## 1. Executive conclusion

**SMT2020 Data Integration Gate = `not_passed_gaps`。**

当前已建立只读 manifest、正式 loader API、全量 TSV parser、产品/路线/设备资格/加工参数/Setup/Batch/CQT/Dedication/Failure/PM/Transport/Release/WIP 的静态领域映射、结构化 audit、raw/parsed count reconciliation，以及真实加工/搬运/release/batch/load-unload 的受限 validation slice、sampling 判定诊断 slice 和同机多 Calendar PM 的单机单工序 slice。两套 raw 数据跨文件引用均无 ERROR。`StepPercent` 已进入 immutable Scenario 和 operation-entry runtime。显式、manifest 绑定的 Batch v1 配置在两模型真实 initial WIP 上验证了 target/timeout 启动路径；load/unload slice 只验证 non-cascade、non-batch 两工序阶段链；Calendar PM slice 验证 raw `attach/pmcal/FOA` 到三条独立 runtime source 的受限链；同机多 Wafer PM 的独立计数、按 `pm_id` 排序 due、串行 owner 和 per-PM snapshot 仅由 synthetic runtime 验证。MINRUN 的真实组合、多 Wafer PM raw 组合与完整日历附件仍各自保持 blocker。

Gate 不能通过，因为真实模型仍启用了当前 runtime 尚不能完整表达的 load/unload/cascade、rework、setup MINRUN、多 calendar attachment。raw 不提供 `B_target/T_max`；仓库现在提供显式 [v1 决策配置](../configs/smt2020-batch-decision-v1.json)，以 E 级本地规则取 `B_target=raw BATCHMX`、`T_max=60 min`，不是 raw 真值或性能推荐。**在显式传入该配置的 Gate profile 下，剩余 4 类 blocker**；无配置的默认 audit 仍有 5 类，绝不隐式采用 v1。batch/sampling 诊断 slice 均省略真实设备的 load/unload，batch slice 还省略已附着的 calendar；新 Calendar PM slice 则省略 Failure、其他机、前后 route/rework 和初始历史。完整加载仍有意返回 `scenario=None`。

## 2. 实际模型和 manifest

| Model | Files | Manifest hash | Dataset version |
| --- | ---: | --- | --- |
| SMT2020_HVLM | 12 | `5f7e76a8c2791717585f7af586d3b052f99f2690ac850fb00c5ba0cbecbffc84` | `SMT2020_HVLM@sha256:5f7e76a8c2791717585f7af586d3b052f99f2690ac850fb00c5ba0cbecbffc84` |
| SMT2020_LVHM | 20 | `06625a12b5a8073ff393431e903e782255e3e20be0c97991ed96583e35a6219c` | `SMT2020_LVHM@sha256:06625a12b5a8073ff393431e903e782255e3e20be0c97991ed96583e35a6219c` |

绝对路径与加载时间不参与 hash。测试已验证不同目录、不同文件创建/枚举顺序产生相同 logical hash，内容改变则 hash 改变。此处 hash 按 Loader `0.1.7` 重新计算；manifest canonical payload 按既有契约包含 loader version，raw 文件字节没有改变，显式 Batch v1 配置的 manifest 绑定也已同步更新。

## 3. Reconciliation statistics

| Item | HVLM | LVHM |
| --- | ---: | ---: |
| Products / routes | 2 / 2 | 10 / 10 |
| Raw route rows = parsed operations | 926 | 4013 |
| Tool table rows | 106 | 106 |
| Production tool groups | 105 | 105 |
| Physical machines | 1043 | 913 |
| Virtual `Delay_32` resources | 400 | 400 |
| Initial WIP lots | 2255 | 2156 |
| Future release templates | 5 | 21 |
| Configured future lot capacity | 442000 | 2202000 |
| Batch operations | 28 | 135 |
| CQT constraints / cross-step | 66 / 17 | 264 / 83 |
| Dedication constraints | 18 | 73 |
| StepPercent field / stochastic / 100% operations | 221 / 149 / 72 | 955 / 662 / 293 |
| Initial WIP currently at explicit sampling step | 98 | 75 |
| Sampling operations overlapping rework | 14 | 52 |
| Sampled CQT target / stochastic sampled CQT target | 4 / 0 | 18 / 0 |
| Explicit sampling operations using load/unload tool templates | 221 | 955 |
| Rework operations | 14 | 52 |
| Initial WIP at rework return / middle / source | 88 / 5 / 7 | 51 / 6 / 3 |
| Cascading/interval operations | 379 | 1668 |
| Setup transitions / group members | 13 / 9 | 13 / 9 |
| Failure / PM calendars | 11 / 292 | 11 / 292 |
| Calendar attachments | 303 | 303 |
| 多 calendar PM 的生产物理机（逐机展开） | 351 | 307 |
| Transport pairs | 1 | 1 |
| Route transitions `Fab→Fab` | 857 | 3714 |
| Route transitions `Fab→Delay / Delay→Fab / Delay→Delay` | 33 / 33 / 1 | 142 / 142 / 5 |

`RPT#` 不在加载时展开成数十万/数百万显式 Lot；release validation slice 已按 horizon 惰性生成，`RPT#` 包含 repeat index 0，canonical ID 使用 model/source-row namespace。raw 统计中的 template 数与 configured future capacity 仍须分开报告，不能把 template 数当 lot 数；非 constant RDIST、`LOTSPERRPT>1` 或非 fixed-horizon 仍不在支持声明内。

## 4. Mechanism audit

| Mechanism | Raw→static mapping | Runtime closure | Result |
| --- | --- | --- | --- |
| Product/Route/Operation | 完成，sequence/reference 已验证 | deterministic micro routes 已支持 | PASS-static |
| Qualification | STNFAM→STNQTY→稳定 machine ID | Engine 已使用具体 machine | PASS |
| Processing | uniform/PTPER/interval 全部保存 | uniform、per_lot/per_piece/per_batch runtime 已支持；interval/cascade 未实现 | PASS-partial / Cascade BLOCKER |
| Setup | route override、transition、group/MINRUN 已保存 | transition 已支持；MINRUN 有合成硬约束，但真实 Implant 工序全部带 PartInterval/L/U，组合物理语义未闭环 | BLOCKER |
| Batch | wafer min/max 与 criterion 已保存 | 显式 v1 配置 + 真实 initial-WIP target/timeout batch slice + 单次 per-batch 抽样已验证；slice 省略 L/U/calendar | PASS-limited；完整物理由其他 blocker 阻塞 |
| CQT | source/target/unit 已闭合 | runtime 已验证 | PASS-static/runtime；initial history warning |
| Dedication | source/target/physical qualification 已闭合 | runtime 已验证 | PASS-static/runtime；initial history warning |
| Failure | calendar/attach/FOA 已解析 | exponential runtime 已支持；真实多 Calendar PM slice 明确省略 Failure，完整组合未验证 | BLOCKER |
| PM | calendar/pieces/attach/FOA 已解析 | 同机多 Calendar PM 合成 runtime 与两模型单机单工序 slice PASS-limited；同机多 Wafer PM、完整附件/历史组合未闭环 | BLOCKER + warning |
| Transport | `Fab→Fab uniform(7.5,2.5)` 与 route location pairs 已解析 | 外生无容量 `TRANSPORTING/ARRIVE`、CRN、CQT、fixed horizon、missing-pair audit 已验证 | PASS-static/runtime；未配置 pair warning |
| Release | `START/RDIST/REPEAT/RUNITS/RPT#/LOTSPERRPT/DUE` 与 template namespace 已解析 | fixed-horizon 惰性投放、index-0 repeat、namespaced stable ID、due offset 平移已形成受限 slice；仅 constant RDIST + LOTSPERRPT=1 | PASS-limited；非支持组合显式保留 |
| Sampling | StepPercent 已解析；显式/随机/100%、initial WIP、rework/CQT/load-unload overlap 已 reconciliation；CQT targets 全为 p=100 | operation-entry Bernoulli、skip trace、CRN、initial WIP 与 p100-CQT integration 已验证；p<100 endpoint 显式拒绝 | PASS-runtime / diagnostic slice |
| Rework | 三字段成组、scope、百分比与严格回跳引用已验证；真实 scope 均为 `lot`；两模型 non-cascade 三步候选与其 sampling/L/U/multi-calendar 交集已锁定 | visit/route loop、重入随机身份和历史状态未冻结/实现 | BLOCKER |
| Cascade | STNCAP/PartInterval/BatchInterval 已解析 | lot finish 与 machine release 双时刻未实现 | BLOCKER |
| Load/Unload | 每机 LTIME/ULTIME 已转分钟；真实 `r_3:18→19` 选择与省略项进入 provenance | non-cascade、non-batch 受限 slice 已支持独立 LOAD/UNLOAD、core completion、阶段截断与合成 Failure/PM resume；cascade/真实组合仍未闭环 | PASS-limited / `DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` 仍 BLOCKER |

## 5. Initial-state audit

| Missing history | HVLM | LVHM | Handling |
| --- | ---: | ---: | --- |
| Machine initial setup | 1043 machines | 913 machines | 不猜测；empty setup 是本地规则 |
| MINRUN historical completed-lot count | raw 未提供 | raw 未提供 | 初始 active setup 的计数保持 unknown；本地运行时仅使用从仿真起点实际完成的保守下界 |
| Open CQT source time | 341 relationships | 433 relationships | 不开伪造 clock，单列 unknown |
| Dedication machine | 2435 relationships | 1965 relationships | 不猜 machine，单列 unknown |
| Wafer-PM counter | raw 未提供 | raw 未提供 | 0 仅为 E 级本地假设 |

## 6. BLOCKER register

| Code | HVLM affected | LVHM affected | Required fix |
| --- | ---: | ---: | --- |
| `DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` | 379 cascade ops | 1668 | load/unload and distinct completion/release timing；详见 [级联语义审计](smt2020-cascade-semantic-audit.md) |
| `DI_UNSUPPORTED_REWORK` | 14 | 52 | visit-indexed route loop；详见 [返工语义审计](smt2020-rework-semantic-audit.md) |
| `DI_UNSUPPORTED_SETUP_MINRUN` | 9 members | 9 | 真实 Implant setup 与 PartInterval/L/U 不可分离；详见 [语义审计](smt2020-setup-minrun-audit.md) |
| `DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT` | 303 raw attachment rows；351 台多 calendar PM 生产机；692 台每机 3 条 wafer PM | 303 raw attachment rows；307 台多 calendar PM 生产机；606 台每机 3 条 wafer PM | Calendar PM 单机子链与同机多 Wafer PM synthetic 规则已分别通过受限测试；raw 同机多 Wafer PM 与 Failure/全路线/初始历史的完整附件组合仍未闭环；详见 [逐机审计](smt2020-multi-calendar-attachment-audit.md) |

此表按显式 v1 batch 配置验收。默认 `LoaderConfig(mode="audit")` 不加载项目配置，仍返回 `DI_MISSING_BATCH_DECISION_CONFIG` BLOCKER；传入 model/manifest 匹配的 `BatchDecisionConfig` 才产生 `DI_BATCH_DECISION_CONFIG_SUPPLIED` INFO，且完整 `scenario` 仍为 `None`。配置不匹配为 ERROR，不能回退。

## 7. Validation smoke

只运行 FIFO、seed 42 的受限真实记录闭包；这些 smoke 不构成正式策略实验。

加工 smoke：

```text
model: SMT2020_HVLM
selector: route r_3, raw step 18
tool family: DE_FE_1
machine: DE_FE_1#0001
raw processing distribution: uniform(mean=135.234, width=6.7617) min
smoke processing: committed action 后按 uniform(mean=135.234, width=6.7617) 抽样
scenario: SMT2020_HVLM:validation-slice:r_3:18
horizon: 136.234 min
result: 1 released, 1 completed
```

搬运 configured-pair smoke：

```text
HVLM: r_3 step 18→19, Fab→Fab
LVHM: r_1 step 18→19, Fab→Fab
raw transport: uniform(mean=7.5, width=2.5) min
runtime: TRANSPORT_START → TRANSPORT_ARRIVE → target dispatch
seed 42 realized duration: HVLM 6.334190412625589 min; LVHM 7.277308394426477 min
result: each 1 released, 1 completed, 1 sampled transport
```

搬运 missing-pair smoke：

```text
HVLM: r_3 step 43→44, Delay→Fab
LVHM: r_1 step 41→42, Delay→Fab
fromto match: none
runtime: zero duration, no transport random sample, missing_pair_count=1
result: each 1 released, 1 completed
```

惰性投放 smoke（FIFO、seed 42；只验证 release，不作性能比较）：

```text
HVLM Lot_3: interval=51.69, releases=[0, 51.69, 103.38]
HVLM HotLot_3: interval=2016, releases=[0, 2016, 4032]
HVLM SuperHotLot_3: interval=27397.61, releases=[0, 27397.61, 54795.22]
LVHM Lot_1: interval=258.46, releases=[0, 258.46, 516.92]
LVHM HotLot_1: interval=10080, releases=[0, 10080, 20160]
LVHM SuperHotLot_3: interval=28258.37, releases=[0, 28258.37, 56516.74]
result: each 3 released; configured RPT# retained; no horizon-external lot materialized
audit: due-release offset invariant; PART/ORDER/PRIOR/PIECES/HOTLOT/source-row preserved
randomness: constant release creates no release-stream ledger sample
```

Sampling smoke（FIFO、seed 42；真实 initial WIP 单工序闭包）：

```text
models: SMT2020_HVLM / SMT2020_LVHM
selector: provenance 中保存 validation_sampling_operation=(route_id, step_id)
profile: per_lot + Fab；无 batch/setup/cascade/rework；p<100 endpoint 被拒绝，p100 CQT endpoint 可执行；不执行 tool LOAD/UNLOAD duration
stochastic case: 0 < StepPercent < 100；每次 run 恰有一条 sampling decision，随机判定恰有一条 sampling ledger
100% case: SAMPLING_DECISION(performed=true)，无 sampling ledger
initial state: 使用真实 initial WIP lot/source row，在 t=0 判定当前 operation
audit: decision/skip/ledger/provenance 与“跳步不加工、不累计 PM wafer”不变量
scope: sampling decision/mapping diagnostic only, not physical-duration closure
warning/provenance: DI_SAMPLING_SLICE_OMITS_LOAD_UNLOAD + omitted load/unload minutes
```

Batch smoke（FIFO、seed 42；显式 v1 配置、真实 initial WIP）：

```text
HVLM r_3:1: raw BATCHMN/BATCHMX=125/150 wafers；15 个真实 WIP；首批 150 wafers 于 t=0 TARGET_REACHED
LVHM r_1:331: raw BATCHMN/BATCHMX=75/100 wafers；3 个真实 25-wafer WIP；t=60 TIMEOUT_REACHED，启动 75-wafer batch
config: B_target=raw BATCHMX，T_max=60 min，model/manifest/source artifact 绑定；canonical config 与 source 文件 SHA-256 进入 provenance
runtime: 每个物理 batch 仅一次 processing sample；FIFO/SPT 对相同 batch identity 的 CRN 一致
audit: batch capacity、trace、provenance 与 machine/lot 守恒检查通过
omissions: 只选一台合格机；每机 LOAD/UNLOAD 各 1 min、已附着的 Failure/PM calendar 未进入 slice；独立 WARNING 和 provenance 保留
```

Load/unload smoke（FIFO、seed 42；真实两模型受限 slice）：

```text
models: SMT2020_HVLM / SMT2020_LVHM
selector: r_3:18→19；HVLM Init_Lot_3_1361；LVHM Init_Lot_3_290；part_3
profile: per_lot + Fab；non-cascade；无 setup/batch/sampling/rework/CQT/dedication/PartInterval/BatchInterval
selected machines: DE_FE_1#0001 → DE_FE_86#0001；每道 raw LOAD=1 min / UNLOAD=1 min
event chain: LOAD_START/FINISH → PROCESS_START → PROCESS_CORE_FINISH → UNLOAD_START/FINISH → PROCESS_FINISH
audit: PROCESS_START 在 core 开始关闭 CQT；processing 只抽样一次；load/process/unload 区间分开；fixed-horizon 可截断仍活动阶段
omissions: 其他合格机、Failure/calendar PM/wafer PM attachment、initial setup/PM counter/CQT/dedication history；均写入 WARNING/provenance
scope: parser→Scenario→runtime 受限兼容性；不作正式 KPI/策略结论，不关闭 cascade blocker
```

同机多 Calendar PM smoke（FIFO、seed 42；两模型真实单机单工序诊断 slice）：

```text
selected machine: Litho_BE_110#0001；raw Fab、non-cascade、LOAD/UNLOAD=1/1 min
calendar sources: Litho_BE_110_WK/MN/QT；pmcal.txt:36–38、attach.txt:47–49
identity: <raw calendar ID>@Litho_BE_110#0001；FOA、period、uniform duration 逐条保留
HVLM: part_3/r_3:491、Init_Lot_3_134；第 89.2 天 QT 开始，第 89.4 天 MN 因重叠 stale
LVHM: part_2/r_2:459、Init_Lot_2_22；第 91 天 QT/WK 同刻，仅 QT 取得 downtime owner
runtime: 两模型各 3 条 CalendarPMSpec、所选真实 WIP 各完成 1 个；PM 区间分别 15/16 段
audit: machine/lot invariant、固定种子复现、PM identity CRN 与 raw source-row provenance 通过
omissions: BREAK_Litho Failure、其他资格机、前后 route/rework、初始 setup/CQT/dedication/wafer-PM counter
scope: 一机一工序 Calendar PM 子链；不作正式 KPI/策略比较，不关闭 full multi-calendar blocker
```

这些运行证明受限记录上的 parser→static model→Scenario schema→`simulate()`→manifest provenance 链条。sampling slice 只诊断 sampling decision/mapping，batch slice 只诊断组批判定/加工抽样；它们省略真实 tool load/unload duration，batch 还只选一台合格机并省略 calendar。Calendar PM slice 保留真实装卸但省略 Failure、其他机、前后 route 和历史状态。这些都不是完整物理闭包，不比较策略、不生成正式 KPI 结论，也不证明 full HVLM/LVHM 可执行。p100 sampled CQT target 的无跳步集成由独立 micro/integration tests 验证。

## 8. PySCFabSim 可比项与差异

可比项包括产品/route 长度、tool family、release 参数、`uniform(m,w)` 参数解释和 selected FIFO smoke 的输入结构。当前 loader 的原始计数与仓库既有静态审计一致；`uniform` 的 mean/full-width 解释继续引用固定 PySCFabSim commit 作为 D 级证据。

本轮没有把 PySCFabSim 输出当作 ground truth，也没有做 full-fab FIFO trace 对齐。固定参考实现支持把 `StepPercent` 解释为“执行该工序的百分比”并在派工可见前判断；本项目进一步以 raw 证据确认 CQT target 全为 p=100，不需要发明 skip-CQT 语义。参考实现能运行仍不能替代组合契约。当前本地 runtime 尚不能表达完整 cascade/rework/multi-calendar/route-branching 组合，此时比较终态 KPI 会混入模型差异。

## 9. Modeling assumptions and unsupported features

- `uniform(m,w)` 的均值/全宽解释：D/E，不是 raw 自描述；
- machine initial setup 为空、synthetic wafer-PM initial counter=0：E/F，必须随结果标识；raw initial wafer-PM counter 为 `unknown`；
- 同机多 Wafer PM 的独立 counter、`pm_id` 排序、串行 owner、Failure 同刻 deferred/pending 与 per-PM snapshot：E 级 synthetic runtime 规则；raw initial counter 历史为 `unknown`，不据此关闭完整附件 blocker；
- initial dedication/CQT 历史：F，不恢复；
- validation slice 使用真实 uniform sampler：仅 smoke compatibility，不能进入正式结果；同机多 Calendar PM 的 stable source ordering/stale/no-drift 是显式 E 级本地规则，raw 不提供 PM-PM 优先级；
- transport 被建模为外生无资源延迟：E；runtime 已执行，未建模 OHT/AMHS 容量；
- `fromto` 未配置 pair 按零时长执行并显式计数：已冻结本地规则；真实 route 中此类转移 HVLM 67、LVHM 289 次，不能解释为已知真实物流耗时；
- `B_target/T_max`：raw 不提供；v1 显式配置使用 E 级 `raw BATCHMX/60 min`，只用于当前 Data Integration 运行参数闭环，不是性能推荐；正式基线实验需先审查参数敏感性；
- sampling：None/100%/随机百分比、operation-entry skip、initial WIP、CRN 与 provenance 已验证；raw sampled CQT targets 4/18 全为 p100，stochastic endpoint 为 0；真实 sampling blocker 已关闭；
- p<100 CQT/Dedication endpoint：当前 raw 未出现，Scenario 显式拒绝；若未来出现必须重新进入 blocker，而非 silent fallback；
- 在显式 v1 batch 配置下，load-unload/cascade、rework、setup MINRUN、multi-calendar attachment 仍列为 4 类 BLOCKER；默认无配置 audit 则另有 batch decision config，共 5 类。
- 初始 WIP 返工历史：loader 以 `DI_REWORK_INITIAL_HISTORY_UNKNOWN` 记录 return/middle/source 位置计数；不伪造 visit、既往判定或原机台绑定。
- release template：`DI_UNSUPPORTED_RELEASE_TEMPLATES` 已关闭，但仅对 `fixed_horizon + constant RDIST + LOTSPERRPT=1` 声明支持；非 constant、`LOTSPERRPT>1`、非 fixed-horizon 或其他未验证组合仍显式 unsupported。

没有 C 级论文/官方资料被单独用于闭合本轮关键语义。

## 10. DI Exit Criteria

| ID | Criterion | Evidence | Status | Gap |
| --- | --- | --- | --- | --- |
| DI-E01 | Manifest/hash | `manifest.py` + stability tests | PASS | — |
| DI-E02 | Product/Route/Operation/Tool/Lot mapping | static model + reconciliation | PASS-static | future lot 仍为 template |
| DI-E03 | Qualification/Processing mapping | distribution sampler、PTPER resolver、真实 validation slice | PASS-partial | load/unload/cascade 由 DI-E10 阻塞 |
| DI-E04 | Setup mapping | transition/group/MINRUN 静态映射 + 合成场景硬约束、trace audit | GAP | 真实 Implant 的 MINRUN 与 PartInterval/L/U 组合尚未运行验证；初始 setup/run 历史不可恢复 |
| DI-E05 | Batch mapping | wafer bounds/criterion + 显式 v1 配置 + 真实 WIP target/timeout slice + per-batch 单次 physical sample | PASS-limited | 完整 L/U/calendar 组合由 DI-E10/DI-E08/09 阻塞 |
| DI-E06 | CQT mapping | 330 constraints、跨步统计、refs closed | PASS | initial history warning |
| DI-E07 | Dedication mapping | 91 constraints、refs closed | PASS | initial history warning |
| DI-E08 | Failure mapping | exponential sampler + calendar/attachments parsed | GAP | 真实多 Calendar PM slice 省略 Failure，完整组合未验证 |
| DI-E09 | PM mapping | calendar/pieces/FOA parsed + 同机多 Calendar PM 合成与真实受限 slice + 同机多 Wafer PM synthetic counter/owner slice | GAP | raw 多 Wafer PM、完整附件/历史组合与初始 counter |
| DI-E10 | Transport/Release/Sampling/Rework/Cascade | transport/release/sampling runtime + sampling 判定诊断 slice；non-cascade L/U 两工序 slice PASS-limited | GAP | 完整 cascade 双时点、rework 仍未闭环 |
| DI-E11 | Initial WIP unknown history auditable | four explicit warnings and counts | PASS | historical facts remain unrecoverable |
| DI-E12 | raw→parsed→Scenario references | zero ERROR; actual-data tests | PASS-static | full executable Scenario blocked |
| DI-E13 | Real-data provenance | manifest/raw hashes embedded in smoke result | PASS |
| DI-E14 | Real-data end-to-end smoke | processing + configured/missing transport + restricted release/batch/L/U/Calendar PM slices + sampling decision diagnostic | PASS-limited | Calendar PM slice 省略 Failure/其他机/路线历史；其余 slice 亦不构成完整 physical-duration closure |
| DI-E15 | BLOCKER count=0 | 显式 v1 batch 配置下 4 blocker codes/model；默认无配置为 5 | GAP | count > 0 |

最终状态：`not_passed_gaps`。本轮本地全量回归 `294 passed`（前次快照 `283 passed`）；同机多 Wafer PM 仅合成运行时通过，真实多 Calendar PM 受限集成测试与 Runtime Reliability、M1、MC01～MC08 的既有边界不因本次规则而改变。正式 HVLM/LVHM 实验和 CMA-ES 均不允许启动，`optimizer_enabled=false`。显式 v1 batch 配置下剩余 4 类 blocker：load/unload/cascade、rework、setup MINRUN、multi-calendar attachment；默认无配置 audit 另保留 batch 决策配置 blocker。下一阶段继续执行 **SMT2020 Runtime Compatibility Gap Closure**。
