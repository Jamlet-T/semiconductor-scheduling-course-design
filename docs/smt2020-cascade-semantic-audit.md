# SMT2020 Load / Unload / Cascade 语义审计

审计日期：2026-09-27。范围为当前仓库的 HVLM/LVHM 原始表、Data Contract §3–4、固定 PySCFabSim commit `0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a` 与现有 DES；这是静态证据审计，**不是 runtime closure**。

## 决策

`DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` 保持 **OPEN / BLOCKER**，Data Integration Gate 保持 `not_passed_gaps`。当前 loader 保存 `LTIME/ULTIME/STNCAP/PartInterval/BatchInterval`，但受限 Scenario 有意省略 load/unload，DES 也没有独立的 lot 完成和 machine 释放时钟。不能把能解析字段或运行单工序 sampling slice 等同于完整物理兼容。

下一步先实现并验收真实 **non-cascade load/unload** 的受限子链；即使该子链通过，也只可标记 `PASS-limited`，不能关闭整个 blocker。级联所需的尾段所有权、故障/PM 中断、同刻事件次序及指标审计另行冻结和实现。

## 原始数据与现有公式

| 观测项 | HVLM | LVHM |
| --- | ---: | ---: |
| route 工序 | 926 | 4013 |
| `PartInterval` 工序 | 284 | 1267 |
| `BatchInterval` 工序 | 95 | 401 |
| `STNCAP=2.0` tool template | 45 | 45 |

两模型中，`PartInterval` 工序全部为 `PTPER=per_piece`，`BatchInterval` 工序全部为 `PTPER=per_lot`；所有带 interval 的工序都映射到 `STNCAP=2.0` 工具，反向也没有发现“级联工具上的无 interval 工序”。级联 tool template 的 `LTIME=ULTIME=1 min`。真实初始 WIP 已有 PartInterval、BatchInterval 工序；不能以“将来才会遇到”排除它们。

Data Contract §4 在**不计 load/unload 的加工阶段**给出：

```text
PartInterval p, n pieces: lot_finish = sampled_base + (n-1)*p
                           machine_release = n*p
BatchInterval b:           lot_finish = sampled_processing
                           machine_release = b
```

对 raw `uniform(PTIME, PTIME2)` 使用现有全宽解释，`sampled_base` 下界是 `PTIME - PTIME2/2`。两模型全部 284/1267 个 PartInterval 工序的 `base_low - p > 0`，最小值为 `0.135 min`；全部 95/401 个 BatchInterval 工序的 `base_low - b > 0`，最小值为 `2.50695 min`。这只验证现有**加工阶段公式与 raw 数值相容**，不验证完整机器占用。

例如 [HVLM `route_3.txt:343`](../datasets/SMT2020_HVLM/route_3.txt) 的 `PTIME=0.6`、`PTIME2=0.03`、`PartInterval=0.45 min`，25 片 lot 的加工阶段 `lot_finish_low=11.385 min`，`machine_release=11.25 min`，仅相差 `0.135 min`；对应 `WE_FE_84` 级联设备另有 1+1 分钟 load/unload。若**单边**把 1 分钟 unload 加到 machine release 而不改变 lot finish，两个事件的顺序会反转。类似地，[HVLM `route_3.txt:106`](../datasets/SMT2020_HVLM/route_3.txt) 的 BatchInterval 下界差为 `2.50695 min`；即使只给 machine release 加上 2 分钟，仍有释放后而 lot 未完成的尾段。这些是检验规则敏感性的反事实计算，**不是采纳的 runtime 规则，也不表示实际装卸应单边计入**。

还存在真实 `Setup + PartInterval` 组合（HVLM 39、LVHM 191 条 route 工序），故仅验证“无 setup 的级联”也不足以关闭完整 blocker。当前 raw 中未发现 `per_batch + interval` 或 `Setup + BatchInterval`；这不证明未来数据的此类组合可安全执行。

## 参考实现能说明什么

固定 [PySCFabSim `instance.py` 的派工与时间计算](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/instance.py#L891-L1022) 会为同一 dispatch 安排 `MachineDoneEvent` 和 `LotDoneEvent`：lot 时长含 load/unload（及有下一步时的 transport），而 `machine.cascading=True` 时 machine 时长采用 cascade interval，**不再加 load/unload**。这与原始字段共同提供交叉证据，但参考实现的事件时序不能自动升格为本项目的物理真值。

尤其是该实现的 [`free_up_machines()`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/instance.py#L778-L790) 会清空 `machine.events`；随后 [`handle_breakdown()`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/instance.py#L1036-L1052) 仅平移该列表中仍在全局队列的事件。据此推断：若 machine 先释放而旧 lot 的 `LotDoneEvent` 尚未到来，后续由该处理器驱动的故障/日历停机不再平移旧 lot 的尾段完成事件。这个行为是**固定代码行为**，不是已验证的 SMT2020 物理规则；现有项目的 MC07/MC08 也不能被其覆盖。

## 实施边界与验收条件

先选择真实 `STNCAP!=2`、无 Part/BatchInterval 的 per-lot 工序，保留该 tool 的 load/unload、加工分布与 manifest/provenance，构造单工序、单机、fixed-horizon validation slice。此子链必须先定义并验证 `LOAD → PROCESS → UNLOAD → lot finish / machine release` 的事件、machine 时间守恒、fixed-horizon 截断和加工样本只在真实 commit 后生成一次；不得把 load/unload 静默并入“纯加工时间”指标。Failure/PM、Batch、Setup、CQT、Dedication、Rework 组合未验收前必须显式限制，不得借子链宣称完整兼容。

完整级联还需证明：前一个 lot 尾段与下一 dispatch 的所有权和容量、Failure/PM 对前景与尾段的作用、wafer-triggered PM 计数时点、CQT source 与 Dedication 释放时点、同刻事件顺序、fixed-horizon terminal WIP，以及独立的 lot/machine conservation audit。证据不足时继续保留 blocker；不修改 raw `datasets/`，不启用正式策略实验或 optimizer。
