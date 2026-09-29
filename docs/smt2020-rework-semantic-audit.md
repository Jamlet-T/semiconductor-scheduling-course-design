# SMT2020 返工语义审计

审计日期：2026-09-27
状态：**raw 关系已核对；返工 visit/lifecycle 尚未闭环；`DI_UNSUPPORTED_REWORK` 仍为 BLOCKER。**
原审计基线：Simulation Contract `0.1.6`、Loader Contract `0.1.4`、Policy Contract `0.1.1`、Data Contract `0.1.4`；2026-09-28 复核后当前版本为 Simulation `0.1.9`、Loader `0.1.7`、Policy `0.1.1`、Data `0.1.8`，未改变返工语义。

本文只审计 SMT2020 的 `RWKSTEP/REWORK/RWKTYPE` 与它和 sampling、dedication、CQT、initial WIP 的组合关系。它不修改任何契约版本、Gate exit criteria、测试数量或优化器状态；也不把参考实现的行为升级为 SMT2020 真值。

## 1. 结论

HVLM/LVHM 的返工字段已经可以从 raw 读入并做静态 reconciliation，但目前仍不能把真实数据装配成可执行的 visit-indexed route loop。原因不是“返工记录数量还没有统计”，而是以下可观测组合会改变事件、随机身份和约束生命周期：

- 两模型的返工记录分别为 14/52，均为 `source STEP − RWKSTEP = 2`、`RWKTYPE=lot`，且 source 与回跳覆盖的中间工序均声明 `StepPercent`；source sampling 中 p=100 为 6/22、p<100 为 8/30。
- 初始 WIP 已落在返工相关路径的 source、return 和中间位置：source 为 7/3、return 为 88/51、中间位置为 5/6。它们不能被当作仿真零点前“尚未发生”的普通 lot。
- Loader 已把三类位置写入 `initial_wip_rework_position_counts`，并发出 `DI_REWORK_INITIAL_HISTORY_UNKNOWN` warning；warning 只标出缺失历史，不代替历史恢复。
- return 等于既有 dedication target `FORSTEP` 的静态关系为 5/17；按显式闭区间诊断约定，与返工三步段相交的 dedication 路线区间为 13/42。后一个数字仅用于筛选组合测试，**不证明绑定生命周期或时间并发**（定义见第 3 节）。
- 未发现 CQT 边穿过回跳段；这减少了一类组合，但不决定 rework 的触发、抽样或 dedication 生命周期。

因此当前结论为：**raw 语义可审计 ≠ rework runtime 已支持**。`DI_UNSUPPORTED_REWORK` 继续为 BLOCKER；正式 HVLM/LVHM 实验和 CMA-ES 仍按现有 Gate 保持禁用。

## 2. 证据分层与来源边界

本审计使用以下证据类型；行为冻结仍遵守 raw、已冻结契约、参考实现和显式本地假设的优先级，来源类型不能自动裁决相互冲突的行为解释。

| 等级 | 本文含义 | 可以支持什么 | 不能支持什么 |
| --- | --- | --- | --- |
| **A** | raw 文件直接给出的字段/记录 | `RWKSTEP`、`REWORK`、`RWKTYPE`、`StepPercent`、`STNFAM`、initial-WIP 所在 operation 等事实 | 事件顺序、每次访问是否重抽、历史 machine 绑定 |
| **B** | 由 raw 字段及路线/资格/引用关系推导 | source→return 是同一路线的更早 step；资格集合交集；return/dedication/CQT 的静态关系 | “同一物理机”在历史运行中是否被复用 |
| **C** | 论文或官方资料 | 论文对返工意图的文字描述、sampling 字段的业务背景 | 本项目所需的 visit index、随机流、initial WIP 恢复规则 |
| **D** | 固定的 PySCFabSim 开源参考实现 | 可复现实参考行为：每 lot/source 最多一次返工判定、每次进入 sampled operation 重新判定、显式 dedication 才绑定物理机 | SMT2020 数据集真值、本项目必须采用的语义 |
| **E** | 本项目显式、版本化的建模/验收假设 | 如何设计 trace、最小 slice、候选假设对照和拒绝条件 | 将假设写成“raw 规定”或“论文已证明” |

数据身份沿用 Gate 的只读 manifest（Loader `0.1.7`）：HVLM 为 `SMT2020_HVLM@sha256:5f7e76a8c2791717585f7af586d3b052f99f2690ac850fb00c5ba0cbecbffc84`，LVHM 为 `SMT2020_LVHM@sha256:06625a12b5a8073ff393431e903e782255e3e20be0c97991ed96583e35a6219c`。下文的 A/B 计数仍指相同 raw 字节下的审计，不是新生成的数据集；manifest identity 随 loader version 更新。

## 3. Raw audit：已观察到的结构

### 3.1 返工规则和 sampling

| raw audit 项 | HVLM | LVHM | 证据 | 限定 |
| --- | ---: | ---: | --- | --- |
| rework records / operations | 14 | 52 | A | 记录存在不等于已执行 |
| `source STEP − RWKSTEP` | 全为 2 | 全为 2 | A | `RWKSTEP` 是回跳目标；source 是带返工字段的 operation |
| scope | 全为 `lot` | 全为 `lot` | A | 不是 wafer/piece 级判定 |
| source 与中间位置的 sampling 关系 | 均有 sampled 关系 | 均有 sampled 关系 | A/B | 这里只报告 raw 关系，不推定 visit 时点 |
| source p=100 | 6 | 22 | A | p100 不消费 sampling 随机数是当前本地 E 级 runtime 规则 |
| source p<100 | 8 | 30 | A | raw 只给百分数，不给重入时的随机身份/重抽规定 |

这里的“source 与中间 sampled”表示 raw 路线区间上的 sampling 关联已经存在；它不是说一次返工一定经过相同的 sampled 分支，也不是说 source 和中间 operation 在同一次 visit 中都一定命中。是否在回跳后的每一次 operation entry 重判，仍需由闭环语义确定。

### 3.2 Initial WIP、dedication 和 CQT

| 静态关系 | HVLM | LVHM | 证据 | 含义边界 |
| --- | ---: | ---: | --- | --- |
| initial WIP 位于 source | 7 | 3 | A/B | 仿真从 t=0 开始时可能已经处于返工 source |
| initial WIP 位于 return | 88 | 51 | A/B | 不能伪造 source 之前的 visit 或 machine 绑定 |
| initial WIP 位于中间位置 | 5 | 6 | A/B | 返工区间可能被截断在快照中间 |
| return 等于 dedication target `FORSTEP` | 5 | 17 | B | return 处可能需要已有 dedication 的 target 生命周期语义 |
| dedication 与返工段闭区间相交（诊断约定） | 13 | 42 | B+E | 端点开闭约定会改变计数；不是 runtime 生命周期证据 |
| CQT 边穿过回跳段 | 0 | 0 | B | 只排除该类静态 CQT 穿越，不排除普通 CQT/返工时钟组合 |

**overlap 定义。** 对每条返工规则取闭区间 `[RWKSTEP, source STEP]`，对同一路线每条 `SVESTN=yes`、`FORSTEP` 非空的 dedication 边取闭区间 `[dedication source STEP, FORSTEP]`。若至少一条边满足 `dedication source STEP ≤ rework source STEP` 且 `FORSTEP ≥ RWKSTEP`，这条返工规则计入 13/42。这是显式端点约定下的静态筛选；若采用严格内部相交，则分别为 11/35。target 命中 5/17 则要求同一路线某条 dedication 边的 `FORSTEP = RWKSTEP`。若改计 dedication source 或 target 任一端点命中，LVHM 会变为 18，不能把两种口径混用。这些数字不使用仿真时间戳，不能解读为绑定持续时间、时间并发或实际返工次数。

### 3.3 资格集合冲突的正确解读

raw audit 观察到 source 与 return 的 `STNFAM` 不同，且由 `STNFAM` 展开的合格物理机集合交集为 0（B）。这只说明：**同一台物理机不会因为同时属于 source 和 return 的普通 qualification 而自动成为两者的共同资格机**。

它不能否定论文中另一种可能含义：对每个被重做的工序，分别复用该工序上一次访问时实际使用的物理机。此种“per-step 同机重做”不要求 source/return 的普通资格集合有交集，仍是尚未冻结的语义歧义。实现不能用“交集为 0”静默选择换机，也不能用它直接宣称论文的 same machines 不成立；两种解释必须在最小 slice 中显式区分并记录。

## 4. 论文、参考实现与本地规则的对照

### 4.1 C 级论文证据

原论文第 526 页 §C 对返工的描述可意译为“重做返工检测工序之前的工艺段，沿用配方和机器”。该文字支持“返工重做前段，并关注 recipe/machine 连续性”的业务意图，但不定义：

- `REWORK` 判定是在 source 完成、return 完成还是其他事件发生；
- 是否每个 lot/source 只能判定一次，还是每个 visit 重判；
- source/中间 sampled operation 在重入时是否重新抽样；
- initial WIP 已位于 source、return 或中间时，历史 visit/dedication 如何恢复；
- “same machines”是 source/return 共享一台机，还是每个被重做工序分别复用其上一次物理机。

论文链接：[SMT2020 原论文](https://ieeexplore.ieee.org/document/9115710)。因此 C 级文字不能单独关闭 Gate blocker。

### 4.2 D 级固定参考实现

固定参考为 PySCFabSim `0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a`：[源码固定链接](https://github.com/prosysscience/PySCFabSim-release/tree/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a)。本次只把静态源码审计得到的以下行为列为 D 级交叉证据：

1. 每个 lot/source 最多一次 rework 判定；
2. sampling 在每次进入 operation 时重新判定；
3. 只有显式 dedication 才绑定具体物理机。

这些行为帮助提出可复现实验假设，但不是 raw 的隐藏字段，也不是论文含义的自动裁决。尤其第 1 项与当前本地 `visit_index` 设计、第 2 项与返工后的 sampling 重入、第 3 项与“same machines”之间都需要本项目自己的对照测试。

### 4.3 E 级本地边界

当前项目已冻结的本地边界是：工序 identity 含 `(route_id, step_id, visit_index)`；sampling identity 也预留 `visit_index`；dedication 绑定键含 visit；但真实 rework route loop 尚未实现，当前 sampling slice 明确为无 rework、`visit_index=0`。这些是 E 级实现边界，不是对 SMT2020 历史行为的恢复。

## 5. 为什么仍是 BLOCKER

`DI_UNSUPPORTED_REWORK` 不能因 raw 关系已经清楚或存在参考实现而关闭，具体原因如下：

1. **循环推进尚未进入 runtime。** 当前执行路径不产生返工 visit、回跳后的 route scan、loop guard 或 rework trace；因此无法保证 source→return→后续步骤的终态和 remaining work 正确。
2. **sampling 组合会改变随机账本。** source p<100 在 HVLM/LVHM 分别占 8/14、30/52。若回跳重入时不重抽、错误沿用首次 draw，或错误按 source/lot 而非 operation visit 建 identity，都会改变 CRN 和完成路径；D 级参考的“每次进入重抽”仍需本地 E 级验收。
3. **initial WIP 不是单一入口。** source、return、中间三类初始位置同时存在；raw 没有 t=0 前的 source 完成时间、visit 计数或历史 dedication machine。把它们全部重置为 visit 0 会制造不可审计的历史；把它们全部按完整新 lot 重跑也会重复物理工序。
4. **dedication 生命周期可能被回跳重新进入。** return 等于 dedication target 的 5/17 关系要求明确绑定按哪一个 visit 建立/释放。source/return 资格集合交集为 0 只能排除普通 qualification 的共同机器，不能排除 per-step 同机重做，因此不能用交集结果替代语义决定。
5. **存在需追踪的静态约束关系。** 闭区间诊断筛出 13/42 条可能与 dedication 关系相交的返工规则；实际回跳后绑定是保留、重新建立还是释放仍未定义。CQT 虽无边穿越回跳段，仍不能替代 loop/dedication/sampling 约束。

现有 sampling validation slice 有意排除 rework，且只证明 `visit_index=0` 的 decision/mapping；它不能作为返工物理闭包或正式 KPI 证据。当前显式 v1 Batch 配置下 Gate 仍有四类 blocker（默认无配置为五类），`not_passed_gaps` 和 `optimizer_enabled=false` 均保持原状。

## 6. 下一步最小可验收 slice（提案，未执行）

### 2026-09-28 真实非级联候选复核

原始数据中确有避开 cascade、Batch、Setup、Part/BatchInterval 和 CQT 的返工三步段，不应把这些其他 blocker 误当成所有返工的必经依赖：

| 模型与 raw route 行 | 回跳段 | sampling | initial WIP 对照 | 仍未闭合的关键点 |
| --- | --- | --- | --- | --- |
| HVLM `route_3.txt:492-494` | `491→493`，source `REWORK=1%`、`RWKTYPE=lot` | middle step 492 为 `19%`；source step 493 为 `44%` | `WIP.txt:41` 位于 source；`:140` 位于 return | 每次重入是否重抽、历史 visit 与同机语义未知 |
| LVHM `route_2.txt:460-462` | `459→461`，source `REWORK=1.4%`、`RWKTYPE=lot` | middle step 460 为 `15%`；source step 461 为 `42%` | `WIP.txt:504` 位于 source；`:508` 位于 return | 同上 |

上述三步段的 tool template 均为 non-cascade、每次 LOAD/UNLOAD 各 1 min；它们仅是可追溯的**静态候选**。逐台展开真实附件后，`Litho_BE_110` 的每台合格机有 1 条 failure、3 条 calendar PM，`LithoMet_BE_18` 与 `Litho_REG_BE_63` 各有 1 条 failure、2 条 calendar PM，均无 wafer PM；因此保留真实维护配置的物理闭环还依赖 Multi-calendar，不能用省略日历的诊断 slice 冒充完整兼容。每个候选段都包含真实 sampling 重入；source 与 return 的普通 qualification 机台交集在两模型 14/52 条返工规则中均为零，这不裁决论文所说的 per-step “same machines”。initial WIP 无法提供仿真零点前的 visit、抽样与机器历史。因此即使现有 non-cascade L/U 已可执行，也不能从这些候选直接推出可信 rework route loop 或关闭 `DI_UNSUPPORTED_REWORK`。后续若选择本地 E 级 `lot/source once` 等规则，必须先版本化冻结并与按 visit 重判等替代解释做 trace 对照。

2026-09-29 独立 raw 复核还确认：14/52 条返工三步段本身均无 Setup、Batch、Part/BatchInterval、CQT 或 `STNCAP=2`，所以 Cascade 双时点不是返工回跳的必然前置条件；但上述两个严格候选的 return 工序为 `per_piece`，而现有 `load_unload_validation_slice` 只接受 `per_lot` 并排除 sampling/rework。故它们仍不能直接交给该 slice builder 执行。要得到受限真实返工闭环，必须分别补齐 visit-indexed route loop、重入 sampling/CRN，以及多 Calendar PM/Failure 的保留或逐项显式省略；initial WIP 的 visit/机台历史继续标为 unknown。这一依赖判断不更改现有 Gate blocker。

进一步交叉核对发现：两模型的 return 行恰是既有 `multi_calendar_validation_slice` 的当前工序（HVLM `r_3:491`、LVHM `r_2:459`）。该受限 Scenario 已以真实 25-wafer WIP、`per_piece` 随机加工和各 1 分钟的 LOAD/UNLOAD 执行，并由 `test_smt2020_rework_candidate.py` 与 `test_smt2020_multi_calendar_validation.py` 联合锁定 raw 行、加工账本、阶段区间和省略项。因此 `per_piece`×装卸**组合验证已有单工序证据**，不需为它另造切片；仍未验收的是从 return 经 sampled middle/source 到回跳的 visit loop、Failure 附件及初始历史。这一单工序结果不能标为 rework runtime 通过。

`04-项目实现/tests/test_smt2020_rework_candidate.py` 对上述 raw 行、parser、initial WIP、所有合格机的装卸与附件交集以及默认/audit 模式仍保留 blocker 做 4 项回归；它是静态证据，不执行返工仿真。

目标是先验收语义链，不比较策略性能，不启动 full-fab 实验。每个模型各选择一条真实 route/source row，组成同一个 provenance 可追踪的 slice，至少覆盖：

1. 一个 source p=100 记录和一个 source p<100 记录（若单一路线不能同时覆盖，则保留两个 source row，但共享同一审计 schema）；
2. 一个 initial WIP 位于 source、一个位于 return、一个位于中间位置的记录；
3. 一个 return 命中 dedication endpoint 的关系；
4. 一个 13/42 dedication 区间相交的代表关系；若真实 route 选择器无法稳定命中，先用 raw-derived route graph 生成最小 synthetic wrapper，并在 provenance 明确标注 E 级 wrapper；
5. 一条无 CQT 穿越回跳段的对照路径，用于证明 CQT=0 是审计结果而非删除字段。

该 slice 必须输出可逐事件核对的 trace/provenance，至少包含：

```text
REWORK_DECISION(lot_id, source_step, return_step, visit_index, draw, performed)
SAMPLING_DECISION/OPERATION_SKIPPED(lot_id, route_id, step_id, visit_index, entity_id)
DEDICATION_BIND/DEDICATION_RELEASE(lot_id, dedication_id, visit_index, machine_id)
PROCESS_START/PROCESS_FINISH(lot_id, route_id, step_id, visit_index, machine_id)
```

最小验收条件为：

- raw `RWKTYPE=lot` 不被拆成 wafer 级判断；每个 `visit_index` 的 identity、rework draw 和 sampling draw 均可回溯到 source row；p100 不消费 sampling 随机量，p<100 的重入行为必须显式记录；
- return 不满足普通 qualification 时不得因“same machines”文字而绕过资格检查；若采用 per-step 同机重做假设，必须以显式历史 machine 绑定输入并记录为 E 级假设；source/return qualification 交集为 0 不得被解释成对该假设的否定；
- initial WIP 三类位置均不伪造 t=0 前的 source finish、visit 或 dedication machine；unknown history 单列 provenance；
- dedication target 的建立/释放、sampling 的重入和 route loop 的终止均按 visit 形成不变量；dedication 区间相交不造成绑定泄漏，不重复完成同一个 operation visit；
- 至少对两种未冻结假设做 trace 对照：D 级“每 lot/source 最多一次”与“按 visit 重判”；结果只用于识别需要冻结的本地规则，不以更接近参考实现作为真值证明。

只有当上述 slice 的 A/B 映射、C 级解释边界、D 级对照和 E 级冻结规则全部写入结果 provenance，并补齐真实 route loop、dedication、sampling、transport 的 visit 交互后，才可重新评估 `DI_UNSUPPORTED_REWORK`。在此之前，不得改写 Gate exit criteria、契约版本或优化器开关来绕过 blocker。

## 7. 当前状态摘要

```text
raw rework reconciliation                 = VERIFIED-STATIC (A/B)
paper intent                              = CONTEXT ONLY (C)
PySCFabSim fixed commit                   = CROSS-REFERENCE ONLY (D)
visit-indexed rework runtime             = NOT IMPLEMENTED
DI_UNSUPPORTED_REWORK                     = OPEN / BLOCKER
SMT2020 Data Integration Gate             = not_passed_gaps
optimizer_enabled                         = false
```
