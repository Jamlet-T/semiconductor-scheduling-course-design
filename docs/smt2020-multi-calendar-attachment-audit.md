# SMT2020 多日历附件静态展开审计

状态：**静态映射、多 Calendar PM 与同机多 Wafer PM synthetic runtime 已核对；真实 HVLM/LVHM 一机一工序诊断 slice 为 PASS-limited；Data Integration Gate 保持 `not_passed_gaps`。**

## 原始证据与影响范围

两套模型的 `attach.txt` 各有 303 条附件行：11 条 `down`、79 条按日历触发的 PM、213 条按加工片数触发的 PM。`RESTYPE=stngrp/stnfam` 必须先结合 `tool.txt.1l` 的 `STNGRP/STNFAM/STNQTY` 展开到具体物理机；303 是附件行数，**不是**受影响机台数。`pmcal.txt` 提供各 PM 的间隔或片数阈值及维修时长。

| 模型 | 生产物理机 | 附件展开边数 | 每机 2 条 calendar PM | 每机 3 条 calendar PM | 每机 3 条 wafer PM |
| --- | ---: | ---: | ---: | ---: | ---: |
| HVLM | 1043 | 4024 | 148 | 203 | 692 |
| LVHM | 913 | 3515 | 137 | 170 | 606 |

两模型所有生产机各有 1 条 failure attachment；有多条 calendar PM 的物理机分别为 351/307 台。原始可追溯示例：`attach.txt` 中 `DefMet_BE_33_MN/QT` 两行、`Litho_BE_110_WK/MN/QT` 三行，分别连接同名 `pmcal.txt` 条目和 `tool.txt.1l` 的具体设备模板。以上数字由 loader 的逐机静态展开统计与真实数据测试核对，不代表这些事件已在 DES 中正确并行运行。

逐机联合分布进一步确认：HVLM/LVHM 分别有 692/606 台为 `(failure=1, calendar PM=0, wafer PM=3)`，148/137 台为 `(1,2,0)`，203/170 台为 `(1,3,0)`；当前 raw **没有**同机同时附着 calendar PM 与 wafer PM 的组合。calendar PM 的真实间隔为 7/30/91 day，单机的两条或三条 PM 仍可能在运行中同刻或重叠；静态互斥不能据此推断冲突处理规则。

## 运行时组合状态：本地规则已冻结，真实组合未闭环

当前 `Scenario`/`PMRuntime` 已按独立 `pm_id` 保留同机多条 Calendar PM，不再将同类 calendar 折叠为单值。合成 runtime 对同刻/重叠的规则为：初始 occurrences 按 `(start_time,machine_id,pm_id,occurrence_index)` 稳定排序安排；后续 periodic occurrence 在前一事件到达时生成，同刻按已分配的 `event_seq` 处理。先执行者取得唯一 active downtime owner，后来同刻或重叠的 Calendar PM 记录 `PM_START_STALE`，不延期、不回补；periodic 下一 occurrence 仍从该 occurrence 的计划起点加 interval 生成，因此不发生漂移。该规则已通过本地合成测试与真实 HVLM/LVHM 受限 slice 达到 PASS-limited，但它是项目本地确定性规则，raw attachment 不提供 PM-PM 优先级；不能据此删除 `DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT`。

实现仍必须区分“多个 PM 来源”和“单一 active downtime owner”：Calendar PM 以 `pm_id` 保存并使用独立的 `(seed, stream, pm_id, occurrence_index)` 随机身份；同机多 Wafer PM 的 synthetic runtime 按 `(machine_id, pm_id)` 独立维护 counter、pending、active 和 occurrence，同一完成事件产生的 due 按 `pm_id` 排序，pending 按同序串行取得 owner；machine 级 counter 标量在多 spec 时为 `None`，per-PM snapshot 为权威状态。Failure 同刻优先时，Calendar PM stale，wafer PM deferred/pending。raw initial-WIP counter 历史为 `unknown`，因此 raw 中每机 3 条 wafer PM 与 Failure/完整路线的组合尚未闭合。上述 synthetic 规则不等于真实 SMT2020 物理语义已验收。

### 真实冲突候选：Litho_BE_110

`Litho_BE_110` 是两套 raw 中都可追溯的同机多 Calendar PM 候选：`attach.txt` 同时附着 `WK/MN/QT` 三条 PM，且 `tool.txt.1l` 给出同一 `STNFAM` 的物理机模板（HVLM `STNQTY=28`，LVHM `STNQTY=23`）。对应原始间隔如下：

| 模型 | `attach.txt` 的 FOA 首次时刻 | `pmcal.txt` 的 nominal interval | PM 日历 |
| --- | --- | --- | --- |
| HVLM | `6.9/29.4/89.2 day` | `7/30/91 day` | `Litho_BE_110_WK/MN/QT` |
| LVHM | `7/30/91 day` | `7/30/91 day` | `Litho_BE_110_WK/MN/QT` |

上表 `attach` 的数值是 FOA 首次发生时刻，**不是**后续周期。真实受限 slice 选择 `Litho_BE_110#0001` 和一个真实 initial WIP 当前工序，将三条 PM 映射为 `{calendar_id}@{physical_machine_id}`；`pmcal.txt` 的间隔、时长和 `attach.txt` 的 FOA/source row 进入 provenance。固定种子 42 的 trace 验证 HVLM `QT` 在第 89.2 天开始、`MN` 在第 89.4 天因重叠 stale；LVHM 第 91 天 `QT` 与 `WK` 同刻，仅 `QT` 取得 owner。两模型均通过 machine/lot invariant 和 PM CRN 审计；真实测试见 `04-项目实现/tests/test_smt2020_multi_calendar_validation.py`。该切片省略 Failure、其他资格机、前后 route/rework 和初始历史，不能作为完整 HVLM/LVHM 兼容性证据；raw 没有给出 PM-PM 优先级、stale/延期行为或周期续期规则。

本轮形成 `raw → static model → 一机一工序 Scenario → DES` 的受限闭环，并以独立 synthetic Scenario 验证同机多 Wafer PM 的计数、排序、串行 owner 和快照边界；不构造完整 Scenario，不启动正式实验，`optimizer_enabled=false`，`datasets/` 不修改。raw 同机多 wafer PM 以及与 Failure/完整路线/初始历史组合未闭环，`DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT` 继续为 BLOCKER。
