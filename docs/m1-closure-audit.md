# M1 Simulation Reliability Baseline Closure Audit

审计日期：2026-09-21

首次审计基线：`d5d70c5a7490273b31256c1a774530a2e8512b1e`

E06/E07/E10 补缺基线：`22ddbd998ee1f0a0e3247ce293693cea5ffa9ed6`

Simulation Contract：`0.1.3`

重新审计结论：**M1 已通过，状态为 `passed`**

优化器：`optimizer_enabled=false`

## 1. Executive conclusion

MC01～MC08 证明了：给定一个正确构造的 `Scenario`，当前轻量 DES 对 Basic DES、Setup、Batch、CQT、Dedication、Failure 和 PM 的核心运行时语义能够按 Contract 确定执行。闭环审计又补充验证了 exact-horizon、termination-aware throughput、Mean WIP、remaining nominal processing work、时间与 lot 守恒、状态互斥以及 provenance。

首次审计发现 E06、E07、E10 三项缺口。本轮在不改变 DES 物理语义的前提下补齐统一 DispatchAction/Policy 链、FIFO/SPT/EDD/CR、RandomSampleLedger/CRN audit 和 `simulate(theta, scenario, seed)`。重新执行原 E01～E10 后十项全部 PASS，因此 M1 可标记为 passed。审计没有修改原验收标准。

完整 SMT2020 loader 并未在原 M1 十项通过条件中被明确列为必要条件，因此本审计不事后把它加入 M1 判定；它被单独列为 **SMT2020 Data Integration Gate**。该 Gate 通过前，不得运行或宣称正式 HVLM/LVHM 实验，也不得启动 CMA-ES。

## 2. Original M1 Exit Criteria Matrix

本表逐字对应 `m1-simulation-reliability-baseline.md` 第 2 节，没有新增或删除条件。

| ID | 类型 | 原始验收条件 | Evidence | Status | Gap |
| --- | --- | --- | --- | --- | --- |
| M1-E01 | 必需 | Data Contract 完成；影响正式结果的 OPEN 已解决或明确排除 | `data-contract.md`、`semantic-evidence-matrix.md` | PASS | 正式 loader 缺口另进 Data Integration Gate，不冒充运行时已验证 |
| M1-E02 | 必需 | PySCFabSim semantic diff 完成，字段读取不能替代行为证据 | `pyscfabsim-semantic-diff.md` | PASS | 部分参考行为仍证据不足，已显式保留，不影响“审计文档已形成”的条件 |
| M1-E03 | 必需 | Simulation Contract 冻结并有版本 | `simulation-contract.md`，版本 `0.1.3` | PASS | — |
| M1-E04 | 必需 | 8 个手算 micro cases 的实际 trace 与期望一致 | `cases.json`；MC01～MC08 测试 | PASS | — |
| M1-E05 | 必需 | Batch、Setup、CQT、Dedication、Failure/PM 各有独立验证 | `test_setup.py`、`test_batch.py`、`test_cqt.py`、`test_dedication.py`、`test_failure.py`、`test_pm.py` | PASS | — |
| M1-E06 | 必需 | FIFO、SPT、EDD、CR 使用相同候选生成、可行性检查和动作提交器 | `policies/*.py`、`test_dispatch_policies.py` | PASS | 四策略接收相同 Engine action snapshots，只在 `select` 处分化 |
| M1-E07 | 必需 | 同场景/代码/契约/种子严格复现；跨策略使用实体索引共同随机场景 | `random_streams.py`、`crn_audit.py`、`test_common_random_numbers.py` | PASS | 共同 identity 样本完全一致；轨迹特有 occurrence 单独列出 |
| M1-E08 | 必需 | completed lots 与 terminal WIP 同时输出；独立重算与引擎汇总一致 | `evaluation/audit.py`、`test_m1_closure_audit.py` | PASS | 独立复核当前基础指标；完整 tardiness/wafer 指标仍属 Metric Contract 缺口 |
| M1-E09 | 必需 | 事件日志满足 `initial + released = completed + WIP + explicit_removed` | `audit_result_invariants()` 与守恒测试 | PASS | 当前没有 explicit removal，按 0 审计 |
| M1-E10 | 必需 | `simulate(theta, scenario, seed)` 存在，优化命令在 M1 通过前拒绝运行 | `fab_scheduler.api.simulate`、`test_simulation_api.py` | PASS | API 严格验证 theta；不搜索参数；优化器继续禁用 |

### 2.1 建议条件与后续条件

- Data Contract、semantic diff、metric contract、审计检查器属于 M1 交付与审计证据。
- 完整 SMT2020 loader、真实 HVLM/LVHM 实验和 CMA-ES 属于 M1 之后的数据接入/优化阶段；不得用它们重写 M1 原始门槛。
- SPT、EDD、CR 与统一 `simulate(...)` 接口已按原 M1 baseline 补齐；它们是 deterministic baseline，不表示进入 M2。

## 3. Contract → Code → Test traceability

| Mechanism | Contract clause | Runtime implementation | Test evidence | Status |
| --- | --- | --- | --- | --- |
| Event ordering | Contract §2 | `simulation/events.py`、`engine.py` | exact-tie 与 priority table tests | PASS |
| Dynamic Release | Contract §3 | `engine.py` event calendar/queue | MC02、exact-horizon release | PASS（runtime） |
| FIFO/SPT/EDD/CR | Policy Contract `0.1.0` / M1-E06 | `policies/base.py`、四个 baseline policy | common-action、golden、hard-filter tests | PASS |
| Setup | Contract §5 | `setup.py`、machine activity | MC03、setup unit/integration tests | PASS（runtime） |
| Batch | Contract §4 | `batch.py`、BatchRuntime entity | MC04、timeout/stale/capacity tests | PASS（runtime） |
| CQT | Contract §6 | `cqt.py` | MC05、terminal exposure/integration tests | PASS（runtime） |
| Dedication | Contract §7 | `dedication.py`、feasibility filter | MC06、side-effect/identity/conflict tests | PASS（runtime） |
| Failure | Contract §8 | `failure.py`、downtime owner/activity token | MC07、stale finish/tie/random stream tests | PASS（runtime） |
| PM | Contract §8 | `pm.py`、shared interruption framework | MC08、calendar/wafer/overlap tests | PASS（runtime） |
| Fixed horizon | Contract §10 | `engine.py`：处理 `event.time <= H` | MC07/MC08 与 exact-horizon audit tests | PASS |
| Terminal WIP | Contract §10 | `engine.py` result aggregation | MC08 与 independent recomputation | PASS（lot 口径） |
| Random streams / CRN | Contract §11、Policy Contract §6 | `random_streams.py`、`crn_audit.py` | 跨四策略、不同 seed、run-order tests | PASS（已实现外生流） |
| Provenance | Contract §10/11 | `provenance.py`、Scenario serialization | MC04/MC06/MC07/MC08 与 completeness tests | PASS（手工 Scenario） |
| Public simulation API | M1-E10、Policy Contract §5 | `fab_scheduler.api.simulate` | theta/schema/immutability/result tests | PASS |
| Metrics | Contract §10 | `engine.py`、`evaluation/audit.py` | closure audit tests | PARTIAL；见 Metric Contract，非本次 M1 blocker |
| Transport | Contract §9 | 无 runtime | 无 | GAP |
| Sampling/rework | Data Contract §2 | 无 runtime | 无 | GAP |
| 随机 processing、load/unload、cascading | Data Contract §4 | 仅确定性 OperationSpec | 无正式数据测试 | GAP |

三类差异如下：

1. **Contract 有定义、代码未实现**：Transport、sampling/rework、随机 processing 及 load/unload/cascading、部分正式指标。这些均保留在 Data Integration/Evaluation Gate，不属于重新审计后的 M1 blocker。
2. **代码实现、Contract 未定义**：本次审计未发现会改变业务语义而未进 Contract 的主要路径；防御性的 stale audit 名称属于已冻结 token/owner 原则的实现细节。
3. **Contract 与代码都有、测试证据有限**：`REPAIR_FINISH_STALE`、`PM_FINISH_STALE` 的 owner/id 防御分支主要靠状态校验，缺少每一种非自然注入路径的直接端到端算例；不把它表述成完整形式验证。

## 4. Metric audit

统一口径见 `metric-contract.md`。主要结论如下。

### 4.1 Throughput

- `until_all_complete`：`completed_lots / simulation_end_time`。
- `fixed_horizon`：`completed_lots / horizon`，即使最后一个 lot 更早完成也不缩短分母。
- 因此 MC08 Calendar PM 子例 completion=25、H=30 时 throughput=1/30；Wafer PM 子例 completed=2、H=10 时 throughput=0.2。这是固定口径，不是 event loop 偶然结果。

### 4.2 Exact horizon

事件循环执行 `event.time <= horizon`，跳过 `event.time > horizon`。在 `t == H` 的 `PROCESS_FINISH`、`LOT_COMPLETE`、`FAILURE_START`、`PM_START`、`LOT_RELEASE` 均生效。瞬时发生的 downtime occurrence 计入 count 和 terminal state，但其 `[H,H]` 区间对窗口内 downtime 积分贡献为 0。

### 4.3 Mean WIP

$$
\overline{WIP}=\frac{1}{T}\int_0^T WIP(t)\,dt
$$

未 release lot 不计入；lot 在 QUEUED、RESERVED、SETTING_UP、PROCESSING、suspended processing 或 active batch 中均计入；LOT_COMPLETE 时移除。`t == H` 瞬时状态变化对积分面积为 0。手算用例验证 `(2×10+1×20)/30=4/3`。

### 4.4 Remaining Work

当前结果实际定义为 **remaining nominal processing work**：当前 operation 的剩余 active processing 加后续 operations 的名义 processing duration。它不包含 future setup、transport、batch waiting、expected failure/PM downtime。被中断的 10 分钟加工已执行 5 分钟时按剩余 5 分钟计算，不回退到 10。

### 4.5 Tardiness and exposure

- completed tardiness：`max(0, completion_time - due_time)`，当前尚未形成完整汇总，标 GAP。
- terminal lateness exposure：对 H 时未完成 lot 计算 `max(0, H - due_time)`，当前已实现。
- 二者不合并；completion ratio 与 terminal WIP 同时保留，防止压住困难 lot 获得虚假优势。

### 4.6 Machine time conservation

在当前单 downtime owner 语义下，每台 machine 都满足：

```text
processing_time + setup_time + idle_up_time
+ failure_downtime + pm_downtime = observation_end_time
```

fixed horizon 以 H 为 observation end；until-all-complete 以 simulation end 为 observation end。审计器允许浮点容差并逐机报错。测试已覆盖 processing、setup、idle、failure、PM 混合场景。

## 5. State and invariant audit

- **Lot-state exclusivity：PASS。** released lot 在 `QUEUED / RESERVED / PROCESSING / COMPLETED` 中恰属一个主要状态；suspended lot 保持 PROCESSING ownership，不回 queue。
- **Batch exclusivity：PASS。** active batch ID 唯一，成员不可进入另一 active batch；审计器核对 member wafer sum、B_min/B_max。
- **Machine exclusivity：PASS。** 一台 machine 至多一个 active physical activity；downtime 只暂停 activity，不构成并发 processing。
- **Downtime ownership：PASS。** machine 同时最多一个 FAILURE/CALENDAR_PM/WAFER_PM owner；finish 事件只有 owner/id 匹配才可恢复。
- **Dedication lifecycle：PASS。** `(lot_id, dedication_id)` binding 唯一；枚举无副作用，commit 才建立绑定。
- **资源守恒：PASS。** 当前系统无 explicit removal，因此审计式为 `initial + released = completed + terminal_wip`。

这些结论是场景与不变量测试证据，不是对所有任意 Python 对象状态的形式化证明。

## 6. Stale-event inventory

| Stale event | 形成原因 | Detection | Required no-effect behavior | Evidence |
| --- | --- | --- | --- | --- |
| `PROCESS_FINISH` | Failure/PM 中断后旧 finish 留在 heap | activity token 不匹配 | 不完成 lot、不推进 route、不累计 wafer/CQT | failure/PM tests |
| `SETUP_FINISH` | Setup 被 Failure/PM 中断 | activity token | 不更新 setup、不启动 processing | setup interruption tests |
| `BATCH_FINISH` | Batch 被 Failure/PM 中断 | activity token + batch ID | 不完成成员、不重复累计 wafer | batch interruption tests |
| `BATCH_TIMEOUT` | 已提前达到 target 并启动 | wait token + 当前队列复核 | 不形成第二个 batch | MC04 stale-timeout test |
| `FAILURE_START` | machine 已由 downtime owner 占用或 occurrence 无效 | availability/owner/occurrence check | 不嵌套 downtime、不重复中断 | failure/PM overlap tests |
| `PM_START` | machine 已 DOWN 或 occurrence 已失效/被抑制 | owner/PM identity check | 不抢夺恢复权、不创建双 owner | PM overlap tests |
| `REPAIR_FINISH` | owner 或 occurrence 不再匹配 | downtime cause + occurrence ID | 不把仍受 PM 控制的 machine 置 UP | 防御分支 + overlap tests |
| `PM_FINISH` | PM owner/ID 不匹配 | downtime cause + PM ID | 不把仍受 Failure 控制的 machine 置 UP | 防御分支 + overlap tests |

所有 stale 分支都禁止推进 route、关闭错误 CQT、解除 dedication 或重复更新 wafer counter。正式 trace 保留相应 stale audit 时，仍不得产生业务副作用。

## 7. Event Priority audit

唯一正式表与 `simulation/events.py` 一致：

| Priority | Event class |
| ---: | --- |
| 10 | `PROCESS_FINISH`、`SETUP_FINISH`、`BATCH_FINISH`、未来 `TRANSPORT_ARRIVE` |
| 20 | `REPAIR_FINISH`、`PM_FINISH` |
| 30 | `FAILURE_START` |
| 35 | `PM_START` |
| 40 | `LOT_RELEASE` |
| 50 | `BATCH_TIMEOUT` / monitoring |
| 60 | `DISPATCH_BARRIER` |

已验证同刻情形包括：finish/failure/release、repair/release、repair/batch-timeout、failure/PM、PM-finish/release、PM-finish/batch-timeout、completion/PM、release/batch-timeout，以及上述事件位于 exact horizon 的边界。

## 8. Randomness and reproducibility audit

当前已实现 stream identity：

```text
seed + stream_name + machine_id + occurrence_index
```

已启用流包括 failure occurrence、repair duration、PM interval 和 PM duration；scripted schedules 与 stochastic schedules 共用同一运行时中断/恢复处理。SHA-256 派生使目标 machine 样本与调用顺序、machine 遍历顺序和其他实体添加解耦。trace 开关不采样随机数。

RandomSampleLedger 记录真正请求的 `stream_name + entity_id + occurrence_index + distribution parameters + sample`。FIFO/SPT/EDD/CR 在相同 Scenario/seed 下对共同 identity 使用完全相同样本；不同 seed 至少一个样本变化；策略运行顺序不影响账本。只在一条轨迹中出现的 identity 标为 trajectory-specific，不视为失败。

release、processing、transport、sampling/rework 与 batch processing 的正式分布流尚未接入，继续属于 Data Integration Gate；这不否定已启用 Failure/Repair/PM 外生流的 M1-E07 验收证据。

## 9. Provenance audit

结果保存：Simulation Contract `0.1.3`、Policy Contract `0.1.0`、dataset identity/version、git commit、seed、policy ID/parameters、termination mode、horizon、完整 Scenario 和 RandomSampleLedger。Scenario 中可恢复 machine、lot、operation、Batch、CQT、Dedication、Failure 和 PM 配置，以及随机流 scheme/version。

结论：对**手工构造的 Scenario**，保存的 provenance 足以重建影响已实现运行时行为的参数。GAP 是 `dataset_version` 目前由调用方提供，尚未与正式 loader 输入清单及原始文件 hash manifest 自动绑定；缺失历史状态也不能由 provenance 反推。

## 10. Contract version compatibility

所有 fixture、结果与当前配置统一声明 Simulation Contract `0.1.3`；MC01～MC08 在当前版本下全部重跑。0.1.1～0.1.3 是对 Dedication、Failure、PM 的向后兼容 patch：没有改变 MC01～MC05 的既有语义。策略选择语义单独冻结在 Policy Contract `0.1.0`，没有修改物理仿真语义，因此 Simulation Contract 不升级。审计中修复了一个实现偏差：Mean Cycle Time 现在按 Data Contract 排除缺少真实 release history 的 initial WIP cohort。该修复使代码符合既有 Contract，不改变业务语义。

## 11. SMT2020 Loader Gap Register

| Mechanism | Raw fields | Internal model | Loader status | Evidence gap | Required before real experiment? |
| --- | --- | --- | --- | --- | --- |
| Product/route/release/WIP | product/route、`START/RDIST/REPEAT`、WIP | `Scenario/LotSpec/OperationSpec` | 未实现 | 实体展开、稳定 ID、initial cohort | 是 |
| Qualification/tool instances | `STNFAM/STN/STNQTY` | `MachineSpec.eligible_machines` | 未实现 | tool group 到物理 machine 映射 | 是 |
| Processing | `PDIST/PTIME/PTIME2/PTPER`、load/unload/cascading | operation duration | 未实现 | 分布、单位、per-piece/per-batch 链 | 是 |
| Setup | tool `SETUPGRP`、setup group、`STIME` override | `SetupRule/SetupDurationResolver` | 未实现 | physical machine 与 override precedence | 是 |
| Batch | `BATCHMN/BATCHMX/PTPER/BATCHCRITF/BATCHPER` | `BatchSpec` | 未实现 | wafer 单位、compatibility、process duration | 是 |
| CQT | `STEP/STEP_CQT/CQT/CQTUNITS` | `CQTSpec` | 未实现 | source-target pairing、单位转换 | 是 |
| Dedication | `SVESTN/FORSTEP` | `DedicationSpec` | 未实现 | establishing/target occurrence、machine identity | 是 |
| Failure/SDT | down calendar、attach、FOA、distribution | `FailureSpec` | 未实现 | calendar attachment、failure-clock basis、参数转换 | 是 |
| PM | `PMCAL`、attach、FOA | Calendar/Wafer PM specs | 未实现 | calendar attach、wafer threshold、initial counter | 是 |
| Transport | transport-time/route records | 尚无 runtime model | 未实现 | 外生 delay 接口与 uniform stream | 是 |
| Sampling/rework | `SAMPLE` 与 route branch 信息 | 尚无 runtime model | 未实现 | occurrence/branch semantics | 取决于正式场景是否启用；启用时必需 |
| Initial state | setup、dedication、CQT clock、wafer-PM counter | scenario initial/audit fields | 部分 fallback | 历史值在原始快照中不可恢复 | 是，必须显式 cohort/assumption |
| Dataset identity | 原始文件 | `data/identity.py` | 仅 hash/identity 基础 | 尚未绑定 loader manifest | 是 |

## 12. Semantic evidence and modeling assumptions

完整表见 `semantic-evidence-matrix.md`。汇总结论：

- A/B 原始数据或结构推导：release、due、qualification、processing 参数、Setup、Batch、CQT、Dedication、Failure/PM 配置、transport 字段。
- C 论文/官方资料：本次没有用 C 级证据单独闭合核心运行语义。
- D 参考实现：`uniform(m,w)` 的均值/全宽解释和部分 preemptive 行为佐证。
- E 本项目假设：preemptive-resume、initial setup fallback、wafer PM reset-zero、single downtime owner、PM 抑制 failure 后重新起算、外生无资源 transport。
- F 不可恢复历史：initial setup、dedication machine、active CQT start、wafer-PM counter。

## 13. PASS/GAP decision

### 13.1 Runtime Reliability

**PASS WITHIN IMPLEMENTED SCOPE**：对正确构造的 Scenario，MC01～MC08、四种 baseline、公共 API、CRN 与新增审计不变量在 Simulation Contract `0.1.3` 和 Policy Contract `0.1.0` 下通过。该结论不覆盖尚未实现的 transport、sampling/rework、正式 processing distributions 和完整指标。

### 13.2 SMT2020 Dataset Integration

**NOT READY**：当前没有经验证的 raw SMT2020 → internal Scenario loader。运行时机制通过不能证明 Setup/Batch/CQT/Dedication/Failure/PM 的真实字段已经正确进入 Scenario。

### 13.3 M1 decision

**M1 = `passed`**。E01～E10 全部 PASS。审计没有为了通过而修改验收标准；原 baseline 文件保持不变。

## 14. Next gate

下一阶段唯一建议是 **SMT2020 Data Integration Gate**：

1. 建立正式 raw-field → internal Scenario loader 与 manifest/hash provenance；
2. 对每类字段做小切片的 loader golden test 和数据质量审计；
3. 验证 Setup/Batch/CQT/Dedication/Failure/PM 的真实字段链；
4. 完成 Data Integration Gate 后再讨论正式策略实验。

在 Data Integration Gate 通过前：不允许正式 HVLM/LVHM 实验，不允许 CMA-ES，`optimizer_enabled` 继续为 `false`。
