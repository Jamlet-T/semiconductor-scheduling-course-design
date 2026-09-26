# SMT2020 Transport Runtime Audit

审计日期：2026-09-22

代码基线：`2fd1a57c3cd051af62dba8d2bdd44d6da75ef54d`；本轮变更在该基线上验收

Simulation Contract：`0.1.3`；Policy Contract：`0.1.1`；Loader Contract：`0.1.2`

状态：截至 2026-09-22 transport 闭环提交的历史审计快照；当前版本与 blocker register 以 `smt2020-data-integration-gate.md` 为准。

## 1. 结论

`DI_UNSUPPORTED_TRANSPORT_RUNTIME` 已关闭。关闭范围是 Data Contract 已冻结的**外生、无容量 transport**，不是 AMHS/OHT 仿真。raw evidence、location-pair 映射、Scenario、事件运行时、随机账本、真实记录 slice 和回归测试已形成闭环。

Data Integration Gate 仍为 `not_passed_gaps`。剩余 7 类 blocker 与本轮 transport 闭环相互独立；`optimizer_enabled=false`，未启动正式 HVLM/LVHM 策略实验。

## 2. Raw 与映射证据

两套数据的 `fromto.txt` 都只配置：

```text
Fab → Fab, uniform(mean=7.5, width=2.5), min
```

loader 依据 operation 的具体物理机资格解析唯一 location，并对相邻 route step 逐一 reconciliation：

| Location pair | HVLM | LVHM | `fromto` configured |
| --- | ---: | ---: | --- |
| `Fab→Fab` | 857 | 3714 | yes |
| `Fab→Delay` | 33 | 142 | no |
| `Delay→Fab` | 33 | 142 | no |
| `Delay→Delay` | 1 | 5 | no |

未配置 pair 不被伪装成已知搬运时间。loader 输出 `DI_TRANSPORT_ROUTE_PAIRS_UNCONFIGURED` WARNING 和分 pair 计数；runtime 每次执行时输出 missing-pair trace/metrics。

## 3. 冻结运行时语义

- 新投放 lot 和 initial WIP 在首工序前不添加 transport。
- 前序工序真实完成后，以实际 source machine location 和下一工序资格集合的唯一 target location 查表。
- 匹配 pair 时，在 commit 后使用 `transport` 子流抽样一次，lot 进入 `TRANSPORTING`；`TRANSPORT_ARRIVE` 后才进入 target queue。
- 缺失 pair 时，时长为 0、不抽样，同时写入 `TRANSPORT_MISSING`、`missing_pair_count` 和 pair 计数。
- source machine 在加工完成时释放，不被搬运占用；transport 不创建容量资源。
- transport 时间计入 cycle time 和跨越该区间的 CQT。
- fixed horizon 截断时保存 active transport snapshot；恰好在 horizon 的 arrival 仍被处理。
- 随机 identity 为 `lot_id + route_id + from/to step + from/to location + visit_index`，底层仍使用确定性派生 seed 和 `RandomSampleLedger`。

当前 rework 尚未实现，因此 `visit_index=0` 只对无回跳的已支持路径成立。关闭 rework blocker 时必须把真实 visit index 同步接入 transport identity 并补 CRN 回归；本轮没有用未来语义占位冒充已实现。

## 4. 真实记录验证

configured pair：

| Model | Slice | Seed 42 realized transport | Result |
| --- | --- | ---: | --- |
| HVLM | `r_3:18→19`, `Fab→Fab` | 6.334190412625589 min | completed；1 ledger sample |
| LVHM | `r_1:18→19`, `Fab→Fab` | 7.277308394426477 min | completed；1 ledger sample |

missing pair：

| Model | Slice | Runtime result |
| --- | --- | --- |
| HVLM | `r_3:43→44`, `Delay→Fab` | zero duration；missing=1；无 transport sample |
| LVHM | `r_1:41→42`, `Delay→Fab` | zero duration；missing=1；无 transport sample |

这些 slice 保留真实 route、工序加工分布、物理机 ID、location、manifest/hash 和 loader selector provenance。它们有意不装配 selected machine 的 load/unload、calendar attachment、release template 或 initial WIP，因此只验证 transport 子系统兼容链条，不代表该记录的全部物理语义或完整 fab KPI。

## 5. 测试与不变量

自动化覆盖：arrival 阻塞 target dispatch、同刻优先级、source machine 释放、configured/missing pair、CQT、fixed-horizon active/exact-arrival、FIFO/SPT/EDD/CR transport CRN、provenance identity、真实 HVLM/LVHM configured/missing slice、raw route pair reconciliation，以及 datasets 前后 hash 一致。

本地验收结果：`unittest discover` 与 `pytest -p no:cacheprovider` 均为 `164 passed`；`compileall` 和 `git diff --check` 通过。CI 状态以本轮提交推送后的 GitHub checks 为准。

本轮没有改变 FIFO/SPT/EDD/CR 的选择逻辑；策略仍只在 feasible actions 中排序，也看不到 transport realization。Simulation Contract 不升级，Loader Contract 因新增 selector、静态校验和 transport 映射闭环升级到 `0.1.2`。

## 6. 明确不包含

- 不建模 OHT、AGV、轨道、车辆容量或拥堵；
- 不从示意坐标推导搬运时间；
- 不把未配置 pair 的零时长解释为真实 fab 事实；
- 不解决 release template、sampling/rework、load/unload/cascade、setup MINRUN、batch decision config 或 multi-calendar；
- 不授权正式策略实验或优化器训练。
