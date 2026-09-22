# SMT2020 Data Integration Gate

审计日期：2026-09-22
代码基线：`2fd1a57c3cd051af62dba8d2bdd44d6da75ef54d`；本轮变更在该基线上验收
Simulation Contract：`0.1.3`
Policy Contract：`0.1.1`
Loader Contract：`0.1.2`

## 1. Executive conclusion

**SMT2020 Data Integration Gate = `not_passed_gaps`。**

当前已建立只读 manifest、正式 loader API、全量 TSV parser、产品/路线/设备资格/加工参数/Setup/Batch/CQT/Dedication/Failure/PM/Transport/Release/WIP 的静态领域映射、结构化 audit、raw/parsed count reconciliation，以及真实加工/搬运 validation-slice smoke。两套 raw 数据跨文件引用均无 ERROR，M1 runtime 也未回归。本轮关闭 `DI_UNSUPPORTED_TRANSPORT_RUNTIME`，并补强 StepPercent/RWKTYPE/RWKSTEP 的静态完整性校验。

Gate 不能通过，因为真实模型仍启用了当前 runtime 尚不能完整表达的 load/unload/cascade、重复投放模板、sampling、rework、setup MINRUN、多 calendar attachment；同时 raw 不提供 `B_target/T_max`，正式场景需要显式版本化配置。已关闭的 processing distribution、PTPER、exponential failure 与 transport 不会降低其余 7 类 blocker 的严重性；完整加载因此仍有意返回 `scenario=None`，不能通过取均值或忽略字段伪造可执行 Scenario。

## 2. 实际模型和 manifest

| Model | Files | Manifest hash | Dataset version |
| --- | ---: | --- | --- |
| SMT2020_HVLM | 12 | `c41b55a0bb7862c606edde40d9809d5644b75a045f52f550322b6f57343ec236` | `SMT2020_HVLM@sha256:c41b...c236` |
| SMT2020_LVHM | 20 | `3d64881b91302324d073480fb6076db784445a008a1e2631345b8e75e18f2ba5` | `SMT2020_LVHM@sha256:3d64...2ba5` |

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
| StepPercent field / stochastic sampling operations | 221 / 149 | 955 / 662 |
| Rework operations | 14 | 52 |
| Cascading/interval operations | 379 | 1668 |
| Setup transitions / group members | 13 / 9 | 13 / 9 |
| Failure / PM calendars | 11 / 292 | 11 / 292 |
| Calendar attachments | 303 | 303 |
| Transport pairs | 1 | 1 |
| Route transitions `Fab→Fab` | 857 | 3714 |
| Route transitions `Fab→Delay / Delay→Fab / Delay→Delay` | 33 / 33 / 1 | 142 / 142 / 5 |

`RPT#` 不在加载时展开成数十万/数百万显式 Lot；当前 runtime 没有按 horizon 惰性 release template，故这项仍是 blocker。Lot 数应分别报告 initial WIP 明细数和 future template/capacity，不能把 template 数当 lot 数。

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
| Sampling | StepPercent 已解析且范围 `(0,100]` 已验证 | Bernoulli skip 未实现 | BLOCKER |
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
| `DI_UNSUPPORTED_RELEASE_TEMPLATES` | 5 | 21 | horizon-lazy release generator |
| `DI_UNSUPPORTED_SAMPLING` | 149 | 662 | stable sampling stream and skip trace；StepPercent=100 不产生随机跳步 |
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

这些运行证明受限记录上的 parser→static model→Scenario schema→`simulate()`→manifest provenance 链条，以及 configured/missing transport pair 的契约行为。slice 有意不装配 machine load/unload、calendar attachment、release template 和 initial WIP；未比较策略、未生成正式 KPI 结论、未验证 full HVLM/LVHM。

## 8. PySCFabSim 可比项与差异

可比项包括产品/route 长度、tool family、release 参数、`uniform(m,w)` 参数解释和 selected FIFO smoke 的输入结构。当前 loader 的原始计数与仓库既有静态审计一致；`uniform` 的 mean/full-width 解释继续引用固定 PySCFabSim commit 作为 D 级证据。

本轮没有把 PySCFabSim 输出当作 ground truth，也没有做 full-fab FIFO trace 对齐：当前本地 runtime 尚不能表达真实 release/cascade/route branching/calendar 组合，此时比较终态 KPI 会混入模型差异。主要差异是本 loader 对不支持字段产生 BLOCKER 并拒绝构造完整 Scenario；参考实现能够运行不代表其事件语义自动满足本项目 Contract。

## 9. Modeling assumptions and unsupported features

- `uniform(m,w)` 的均值/全宽解释：D/E，不是 raw 自描述；
- machine initial setup 为空、wafer-PM initial counter=0：E/F，必须随结果标识；
- initial dedication/CQT 历史：F，不恢复；
- validation slice 使用真实 uniform sampler：仅 smoke compatibility，不能进入正式结果；
- transport 被建模为外生无资源延迟：E；runtime 已执行，未建模 OHT/AMHS 容量；
- `fromto` 未配置 pair 按零时长执行并显式计数：已冻结本地规则；真实 route 中此类转移 HVLM 67、LVHM 289 次，不能解释为已知真实物流耗时；
- `B_target/T_max`：raw 不提供，未来必须由版本化 experiment/loader config 给出；
- load-unload/cascade、release templates、sampling/rework、setup MINRUN、batch decision config、multi-calendar attachment：当前明确 unsupported，全部列为 BLOCKER。

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
| DI-E10 | Transport/Sampling/Rework/Cascade | transport runtime + real configured/missing slices；其余 fields audited | GAP | transport PASS；sampling/rework/cascade 仍实际启用 |
| DI-E11 | Initial WIP unknown history auditable | four explicit warnings and counts | PASS | historical facts remain unrecoverable |
| DI-E12 | raw→parsed→Scenario references | zero ERROR; actual-data tests | PASS-static | full executable Scenario blocked |
| DI-E13 | Real-data provenance | manifest/raw hashes embedded in smoke result | PASS |
| DI-E14 | Real-data end-to-end smoke | processing + configured/missing transport FIFO slices | PASS-limited | compatibility only |
| DI-E15 | BLOCKER count=0 | 7 blocker codes/model | GAP | count > 0 |

最终状态：`not_passed_gaps`。Runtime Reliability 与 M1 继续为 passed；正式 HVLM/LVHM 实验和 CMA-ES 均不允许启动。下一阶段继续执行 **SMT2020 Runtime Compatibility Gap Closure**：优先审计并闭合 release template 或其他证据充分的独立机制；sampling/rework、cascade、MINRUN 和 multi-calendar 在语义证据不足时继续保留 blocker。
