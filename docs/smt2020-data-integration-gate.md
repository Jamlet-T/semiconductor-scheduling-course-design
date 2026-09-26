# SMT2020 Data Integration Gate

审计日期：2026-09-25
本轮起点：sampling runtime 迭代前已通过的 release 闭环提交；最终 Git commit 以本轮验收记录为准
Simulation Contract：`0.1.5`
Policy Contract：`0.1.1`
Loader Contract：`0.1.4`

## 1. Executive conclusion

**SMT2020 Data Integration Gate = `not_passed_gaps`。**

当前已建立只读 manifest、正式 loader API、全量 TSV parser、产品/路线/设备资格/加工参数/Setup/Batch/CQT/Dedication/Failure/PM/Transport/Release/WIP 的静态领域映射、结构化 audit、raw/parsed count reconciliation，以及真实加工/搬运/release 的受限 validation slice和 sampling 判定诊断 slice。两套 raw 数据跨文件引用均无 ERROR。`StepPercent` 已进入 immutable Scenario 和 operation-entry runtime，并具备独立 skip trace、稳定随机身份、provenance 与审计检查器。raw 中 4/18 个 sampled CQT target 全部 p=100，stochastic endpoint 为 0，因此实际 sampling profile 已闭环；诊断 slice 仍不证明完整物理 duration。

Gate 不能通过，因为真实模型仍启用了当前 runtime 尚不能完整表达的 load/unload/cascade、rework、setup MINRUN、多 calendar attachment；同时 raw 不提供 `B_target/T_max`，正式场景需要显式版本化配置。全部显式 sampled 工序（221/955）所属 tool template 都带 `LOAD=1 min / UNLOAD=1 min`，sampling slice 没有执行这两段时长，因此 sampling blocker 已关闭但 `DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` 保留。完整加载仍有意返回 `scenario=None`；当前共 5 类 blocker。

## 2. 实际模型和 manifest

| Model | Files | Manifest hash | Dataset version |
| --- | ---: | --- | --- |
| SMT2020_HVLM | 12 | `8b5ad109d1052dff53c4354f4567a71b84c27b2badf159105194e46cde4a8cd7` | `SMT2020_HVLM@sha256:8b5ad109d1052dff53c4354f4567a71b84c27b2badf159105194e46cde4a8cd7` |
| SMT2020_LVHM | 20 | `80c8ccc08aa8f3ed5338cbb27af4db20c82deeca24c5fa060b57268ca3ae247c` | `SMT2020_LVHM@sha256:80c8ccc08aa8f3ed5338cbb27af4db20c82deeca24c5fa060b57268ca3ae247c` |

绝对路径与加载时间不参与 hash。测试已验证不同目录、不同文件创建/枚举顺序产生相同 logical hash，内容改变则 hash 改变。

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
| Cascading/interval operations | 379 | 1668 |
| Setup transitions / group members | 13 / 9 | 13 / 9 |
| Failure / PM calendars | 11 / 292 | 11 / 292 |
| Calendar attachments | 303 | 303 |
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
| Setup | route override、transition、group/MINRUN 已保存 | transition 已支持，MINRUN 未执行 | BLOCKER |
| Batch | wafer min/max 与 criterion 已保存 | 随机 per-batch runtime 已支持；B_target/T_max 正式配置缺失 | BLOCKER |
| CQT | source/target/unit 已闭合 | runtime 已验证 | PASS-static/runtime；initial history warning |
| Dedication | source/target/physical qualification 已闭合 | runtime 已验证 | PASS-static/runtime；initial history warning |
| Failure | calendar/attach/FOA 已解析 | exponential runtime 已支持；多 calendar/机不支持 | BLOCKER |
| PM | calendar/pieces/attach/FOA 已解析 | 多 calendar/机与历史 counter 缺口 | BLOCKER + warning |
| Transport | `Fab→Fab uniform(7.5,2.5)` 与 route location pairs 已解析 | 外生无容量 `TRANSPORTING/ARRIVE`、CRN、CQT、fixed horizon、missing-pair audit 已验证 | PASS-static/runtime；未配置 pair warning |
| Release | `START/RDIST/REPEAT/RUNITS/RPT#/LOTSPERRPT/DUE` 与 template namespace 已解析 | fixed-horizon 惰性投放、index-0 repeat、namespaced stable ID、due offset 平移已形成受限 slice；仅 constant RDIST + LOTSPERRPT=1 | PASS-limited；非支持组合显式保留 |
| Sampling | StepPercent 已解析；显式/随机/100%、initial WIP、rework/CQT/load-unload overlap 已 reconciliation；CQT targets 全为 p=100 | operation-entry Bernoulli、skip trace、CRN、initial WIP 与 p100-CQT integration 已验证；p<100 endpoint 显式拒绝 | PASS-runtime / diagnostic slice |
| Rework | 三字段成组、scope、百分比与严格回跳引用已验证；真实 scope 均为 `lot` | visit/route loop 未实现 | BLOCKER |
| Cascade | STNCAP/PartInterval/BatchInterval 已解析 | lot finish 与 machine release 双时刻未实现 | BLOCKER |
| Load/Unload | 每机 LTIME/ULTIME 已转分钟 | 尚未进入占用时间 | BLOCKER |

## 5. Initial-state audit

| Missing history | HVLM | LVHM | Handling |
| --- | ---: | ---: | --- |
| Machine initial setup | 1043 machines | 913 machines | 不猜测；empty setup 是本地规则 |
| Open CQT source time | 341 relationships | 433 relationships | 不开伪造 clock，单列 unknown |
| Dedication machine | 2435 relationships | 1965 relationships | 不猜 machine，单列 unknown |
| Wafer-PM counter | raw 未提供 | raw 未提供 | 0 仅为 E 级本地假设 |

## 6. BLOCKER register

| Code | HVLM affected | LVHM affected | Required fix |
| --- | ---: | ---: | --- |
| `DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` | 379 cascade ops | 1668 | load/unload and distinct completion/release timing |
| `DI_UNSUPPORTED_REWORK` | 14 | 52 | visit-indexed route loop |
| `DI_UNSUPPORTED_SETUP_MINRUN` | 9 members | 9 | minimum run hard constraint |
| `DI_MISSING_BATCH_DECISION_CONFIG` | 28 | 135 | explicit versioned B_target/T_max config |
| `DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT` | 303 attachments | 303 | multiple calendars per physical machine |

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

这些运行证明受限记录上的 parser→static model→Scenario schema→`simulate()`→manifest provenance 链条。sampling slice 只诊断 sampling decision/mapping；它排除 rework 并省略真实 tool load/unload duration，因此不是物理闭包，不比较策略、不生成正式 KPI 结论，也不证明 full HVLM/LVHM 可执行。p100 sampled CQT target 的无跳步集成由独立 micro/integration tests 验证。

## 8. PySCFabSim 可比项与差异

可比项包括产品/route 长度、tool family、release 参数、`uniform(m,w)` 参数解释和 selected FIFO smoke 的输入结构。当前 loader 的原始计数与仓库既有静态审计一致；`uniform` 的 mean/full-width 解释继续引用固定 PySCFabSim commit 作为 D 级证据。

本轮没有把 PySCFabSim 输出当作 ground truth，也没有做 full-fab FIFO trace 对齐。固定参考实现支持把 `StepPercent` 解释为“执行该工序的百分比”并在派工可见前判断；本项目进一步以 raw 证据确认 CQT target 全为 p=100，不需要发明 skip-CQT 语义。参考实现能运行仍不能替代组合契约。当前本地 runtime 尚不能表达完整 cascade/rework/multi-calendar/route-branching 组合，此时比较终态 KPI 会混入模型差异。

## 9. Modeling assumptions and unsupported features

- `uniform(m,w)` 的均值/全宽解释：D/E，不是 raw 自描述；
- machine initial setup 为空、wafer-PM initial counter=0：E/F，必须随结果标识；
- initial dedication/CQT 历史：F，不恢复；
- validation slice 使用真实 uniform sampler：仅 smoke compatibility，不能进入正式结果；
- transport 被建模为外生无资源延迟：E；runtime 已执行，未建模 OHT/AMHS 容量；
- `fromto` 未配置 pair 按零时长执行并显式计数：已冻结本地规则；真实 route 中此类转移 HVLM 67、LVHM 289 次，不能解释为已知真实物流耗时；
- `B_target/T_max`：raw 不提供，未来必须由版本化 experiment/loader config 给出；
- sampling：None/100%/随机百分比、operation-entry skip、initial WIP、CRN 与 provenance 已验证；raw sampled CQT targets 4/18 全为 p100，stochastic endpoint 为 0；真实 sampling blocker 已关闭；
- p<100 CQT/Dedication endpoint：当前 raw 未出现，Scenario 显式拒绝；若未来出现必须重新进入 blocker，而非 silent fallback；
- load-unload/cascade、rework、setup MINRUN、batch decision config、multi-calendar attachment：仍列为 5 类 BLOCKER。
- release template：`DI_UNSUPPORTED_RELEASE_TEMPLATES` 已关闭，但仅对 `fixed_horizon + constant RDIST + LOTSPERRPT=1` 声明支持；非 constant、`LOTSPERRPT>1`、非 fixed-horizon 或其他未验证组合仍显式 unsupported。

没有 C 级论文/官方资料被单独用于闭合本轮关键语义。

## 10. DI Exit Criteria

| ID | Criterion | Evidence | Status | Gap |
| --- | --- | --- | --- | --- |
| DI-E01 | Manifest/hash | `manifest.py` + stability tests | PASS | — |
| DI-E02 | Product/Route/Operation/Tool/Lot mapping | static model + reconciliation | PASS-static | future lot 仍为 template |
| DI-E03 | Qualification/Processing mapping | distribution sampler、PTPER resolver、真实 validation slice | PASS-partial | load/unload/cascade 由 DI-E10 阻塞 |
| DI-E04 | Setup mapping | transition/group/MINRUN parsed | GAP | MINRUN runtime |
| DI-E05 | Batch mapping | wafer bounds/criterion + per-batch single physical sample | GAP | B_target/T_max decision config |
| DI-E06 | CQT mapping | 330 constraints、跨步统计、refs closed | PASS | initial history warning |
| DI-E07 | Dedication mapping | 91 constraints、refs closed | PASS | initial history warning |
| DI-E08 | Failure mapping | exponential sampler + calendar/attachments parsed | GAP | multi-calendar runtime |
| DI-E09 | PM mapping | calendar/pieces/FOA parsed | GAP | multi-calendar + initial counter |
| DI-E10 | Transport/Release/Sampling/Rework/Cascade | transport/release/sampling runtime + sampling 判定诊断 slice；其余 fields audited | GAP | sampling PASS；真实 load/unload、rework/cascade 仍实际启用 |
| DI-E11 | Initial WIP unknown history auditable | four explicit warnings and counts | PASS | historical facts remain unrecoverable |
| DI-E12 | raw→parsed→Scenario references | zero ERROR; actual-data tests | PASS-static | full executable Scenario blocked |
| DI-E13 | Real-data provenance | manifest/raw hashes embedded in smoke result | PASS |
| DI-E14 | Real-data end-to-end smoke | processing + configured/missing transport + restricted release slices + sampling decision diagnostic | PASS-limited | sampling slice 不是 physical-duration closure |
| DI-E15 | BLOCKER count=0 | 5 blocker codes/model；sampling blocker 已关闭 | GAP | count > 0 |

最终状态：`not_passed_gaps`。本地全量回归 `211 passed`，Runtime Reliability、M1 与 MC01～MC08 保持 `passed/verified`；正式 HVLM/LVHM 实验和 CMA-ES 均不允许启动，`optimizer_enabled=false`。`DI_UNSUPPORTED_SAMPLING` 已关闭；当前剩余 5 类 blocker：load/unload/cascade、rework、setup MINRUN、batch decision config、multi-calendar attachment。下一阶段继续执行 **SMT2020 Runtime Compatibility Gap Closure**。
