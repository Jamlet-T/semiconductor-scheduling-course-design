# SMT2020 Setup MINRUN 语义审计

状态：**原始配置已核对；本地合成场景运行时于 Simulation Contract `0.1.6` 引入并回归，当前 Contract 为 `0.1.8`；`DI_UNSUPPORTED_SETUP_MINRUN` 暂不关闭。**

## 原始字段链

HVLM/LVHM 的 `setupgrp.txt` 均有 9 条成员，唯一组为 `Implant_Gas`，`MINRUN` 均为 7；成员是 `SU128_1/2/3`、`SU132_1/2/3`、`SU91_1/2/3`。`tool.txt.1l` 中 Implant_128/132/91 三个模板挂到该组，分别展开为 24/25 台物理机。两模型在这些设备族上分别有 35/169 道 route 工序，全部 `per_piece`、非 batch、`WHEN=need`，且每道要求的 setup 都能在这 9 条组成员中找到。这证明了 raw 字段和三表引用，但不证明“run”的计数时点或机台初始历史。

进一步逐工序核对发现，这 35/169 道真实 Implant setup 工序**全部带非空 `PartInterval`**，并使用有 1 min load/unload 的级联设备；不存在能同时保留真实 MINRUN 且避开 cascade/load-unload 的 Implant 工序。真实 initial WIP 可为 HVLM 三个 Implant family、LVHM 的 Implant_128/91 找到同 setup 7 lot + 异 setup 1 lot 的诊断组合，但 LVHM Implant_132 的同 setup WIP 最多 4 lot。此类受限 slice 若省略 interval 和装卸，只能证明 setup 决策映射，不能作为真实物理闭环或单独关闭 `DI_UNSUPPORTED_SETUP_MINRUN` 的证据。

## 参考实现与本地语义边界

固定 [PySCFabSim `file_instance.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/file_instance.py) 读取 `SETUP/MINRUN`；其 [`instance.py`](https://github.com/prosysscience/PySCFabSim-release/blob/0dbff6a55c30978aa7d61d4cbd42cbf550c48e9a/simulation/instance.py) 在派工路径按 `len(lots)` 扣减，并在 `new_setup` 属于 minimum-run 表时重设 counter，即使没有发生实际换型也可能重设。该实现只提供 D 级交叉证据，不是 SMT2020 的物理真值。

本项目的 Data Contract 已冻结“换到该 setup 后，未满足最小运行次数不得再次换型”。若用“成功 `PROCESS_FINISH` 的 lot 数”作为计数单位，这属于明确的 E 级本地解释，与参考实现的派工时扣减有意不同。Failure/PM 中断后只恢复剩余时间，旧完成事件失效，不能让一次 lot 重复计数。没有 setup 要求的动作不构成 setup 切换，不应消耗 run count；当前真实 Implant profile 没有这种组合。机台初始 setup 与已完成 run 数不在 raw 快照中，必须明确写入 Scenario 假设或维持 unknown，不能将缺失值静默解释为 0。

## 关闭条件

当前已覆盖候选与提交两层硬约束、实际 `SETUP_FINISH` 后重置、有效 `PROCESS_FINISH` 按 lot 计数、Failure/PM resume、fixed horizon 与初始历史 provenance。独立检查器从已提交的 dispatch、setup 与有效加工完成 trace 重算 MINRUN，并能检出提前换型、伪造 setup 完成或终态计数；trace 不记录未提交候选，因此不能单凭它证明候选过滤完备。真实 route/tool/setupgrp 三表联查测试已通过，验证 9 条配置覆盖 HVLM 35 / LVHM 169 道 Implant 工序。然而这仍是静态联查，不是包含真实 load/unload、随机 per-piece duration、initial WIP 与多日历中断的组合 slice。局部合成测试通过只证明本地运行时能力，不等于 raw→Scenario→runtime 正式闭环。Gate 保持 `not_passed_gaps`，不启动正式 HVLM/LVHM 策略比较。
