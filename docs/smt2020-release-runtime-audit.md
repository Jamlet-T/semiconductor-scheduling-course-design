# SMT2020 Release Runtime Audit

审计日期：2026-09-23

Simulation Contract：`0.1.4`；Loader Contract：`0.1.3`；Policy Contract：`0.1.1`；Data Contract：`0.1.3`

审计用途：为 `DI_UNSUPPORTED_RELEASE_TEMPLATES` 的关闭提供 raw 证据、语义冻结、受限运行时边界和可重复的回归证据。

## 1. 结论与边界

release template blocker 在以下**联合支持边界**内关闭：

```text
termination_condition = fixed_horizon
RDIST = constant
RUNITS = min
LOTSPERRPT = 1
```

这表示 loader/runtime 可以对满足上述条件的真实 SMT2020 release template 进行按 horizon 的惰性投放、稳定命名和 due 平移；不表示完整 HVLM/LVHM 已可执行，也不表示任意 release 配置均已支持。`RDIST` 非 constant、`LOTSPERRPT > 1`、非 fixed-horizon，或与尚未闭合机制组合的场景必须显式标记 unsupported/blocker，不得通过预展开、重复名称或静默降级获得“支持”结论。

Data Integration Gate 状态仍为 `not_passed_gaps`，剩余 6 类 blocker：load/unload/cascade、sampling、rework、setup MINRUN、batch decision config、multi-calendar attachment。`optimizer_enabled=false`，不得启动正式 HVLM/LVHM 策略实验或 CMA-ES。

## 2. Raw evidence

真实 raw 数据的 release template 汇总如下：

| Model | Future release templates | Configured future capacity | START | RDIST/RUNITS | LOTSPERRPT | PIECES | HOTLOT |
| --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| SMT2020_HVLM | 5 | 442000 | 全部为 0 | `constant/min` | 1 | 25 | `no` |
| SMT2020_LVHM | 21 | 2202000 | 全部为 0 | `constant/min` | 1 | 25 | `no` |

这些统计中的 template 数与 configured capacity 分开记录；不能把 5/21 个 template 直接解释成 lot 数，也不能把 capacity 当作已在内存中展开的实体数。`START` 全为 0 只说明本地 raw profile 的首个释放偏移，不授权对其他 START 形态作运行时支持声明。

raw release 记录还提供 `PART`、`ORDER`、`LOT` 前缀、`PRIOR`、`DUE`、`REPEAT`、`RPT#` 及其 source-row 位置。`HOTLOT=no` 是字段值证据，不允许用 lot 名称或前缀猜测 hot-lot 语义。

## 3. 冻结的 release domain 与 ID

静态模型、runtime template 与 dataset provenance 合起来必须保留以下字段及其来源：

```text
dataset model / order.txt source_row / namespaced template_id
PART / ORDER / LOT-prefix / PIECES / PRIOR / HOTLOT
START / RDIST / REPEAT / RUNITS / RPT# / LOTSPERRPT
relative_due_offset = DUE - START
```

`template_id` 必须包含 model/source-row namespace，不能仅由原始 `LOT` 名称构造。每个惰性生成的 release lot 使用唯一 canonical ID：

```text
REL::<template_id>::<lot_prefix>::r<repeat_index:06d>::m<member_index:04d>
```

其中：

- `repeat_index` 从 `0` 开始，且 `RPT#` 的重复数量包含 index 0，即 `0 <= repeat_index < RPT#`（数学记法为 `[0, RPT# - 1]`）；
- `member_index` 是重复点内成员序号；当前支持边界 `LOTSPERRPT=1`，因此真实 slice 使用 `m0000`；
- release 时刻为 `START + repeat_index × constant_interval`；
- due 时刻为该实体 release 加 `DUE-START`，每个重复 lot 独立平移；
- `PART`、`ORDER`、`PIECES`、`PRIOR`、`HOTLOT` 和 due offset 同时进入 immutable domain、release trace 和 provenance。

策略不得自动读取 `ORDER`、`HOTLOT` 或 `PRIOR`。若未来需要使用这些字段，必须由独立、版本化的 Policy Contract 显式授权，不能因字段已经进入 domain 就改变 baseline 策略可见性。

## 4. Horizon-lazy runtime

release generator 不在 loader 初始化时预展开全部重复实体，而是按 fixed horizon 生成 release event：

1. 读取 template 的 constant interval 与 `RPT#` 上界；
2. 从 repeat index 0 开始，生成不晚于 `H` 的下一实体；
3. 按 SMT2020 十进制时间值确定性组合 release 时刻，并在统一事件日历中处理 `release_time <= H` 的 `LOT_RELEASE`；
4. 对每个实体写入 namespaced ID、release、due、template/source-row provenance；
5. 对 `release_time > H` 的实体不进入当前 run，也不为其预先抽样或构造业务状态。

fixed horizon 的观察区间为闭区间 `[0,H]`。恰好位于 `H` 的 release 必须生效；超过 `H` 的 release 必须被截断。`until_all_complete` 仍可用于微型算例，但不属于本轮 SMT2020 release profile 的支持声明。

## 5. Reference implementation evidence

PySCFabSim 参考实现可用于交叉核对 release 的三类基本关系：

- repeat count/index 的推进关系；
- constant interval 的时间平移；
- due 与 release 的相对偏移关系。

但下列实现细节不能直接照搬为本项目语义或工程方案：

- 启动时预展开全部重复 lot；
- 用原始名称生成重复实体而造成 duplicate name；
- 对超过 horizon 或容量范围的实体采用越界构造。

本项目以 raw source-row namespace、惰性事件生成和 fixed-horizon 闭区间为准，参考实现只提供 D 级交叉证据，不把其行为升级为 SMT2020 raw 规定。

## 6. Trace、provenance 与禁止组合

release trace 至少能重算：template identity、repeat/member index、release、due、`PART`、`ORDER`、`PIECES`、`PRIOR`、`HOTLOT`、source-row namespace 和 termination/horizon。provenance 必须同时记录 Simulation/Loader/Data/Policy Contract 版本、selector/config 和 raw dataset identity；缺失 source-row namespace 或版本化边界时，不得把结果标记为真实 release validation。

以下组合保持显式 unsupported：

| 组合 | 处理 |
| --- | --- |
| `RDIST != constant` | 不声明 release runtime 支持；返回 blocker/audit |
| `LOTSPERRPT > 1` | 不声明 member expansion 支持；返回 blocker/audit |
| `termination_condition != fixed_horizon` | 不归入 SMT2020 release profile |
| `HOTLOT=yes` 或依赖 priority/order 的策略规则 | 不由本 release slice 推断；需独立语义/策略证据 |
| 与 sampling/rework/cascade 等未闭合机制的组合 | 保持整体 unsupported，不拆分后宣称完整支持 |

## 7. 真实 slice 证据

在两个原始模型上以 FIFO、seed 42 构造 `release_validation_slice`；这里只验证 raw→template→runtime→trace/provenance，不作策略性能比较：

| Model/template | RPT# | Interval (min) | 实际 release times (min) | Due offset / metadata |
| --- | ---: | ---: | --- | --- |
| HVLM `Lot_3` | 200000 | 51.69 | 0, 51.69, 103.38 | 77527.783333；`part_3/O_Lot_3/priority=10` |
| HVLM `HotLot_3` | 20000 | 2016 | 0, 2016, 4032 | 47999.25；`part_3/O_HotLot_3/priority=20` |
| HVLM `SuperHotLot_3` | 2000 | 27397.61 | 0, 27397.61, 54795.22 | 47833.016667；`part_3/O_SuperHotLot_3/priority=30` |
| LVHM `Lot_1` | 200000 | 258.46 | 0, 258.46, 516.92 | 71681.766667；`part_1/O_Lot_1/priority=10` |
| LVHM `HotLot_1` | 20000 | 10080 | 0, 10080, 20160 | 48881.85；`part_1/O_HotLot_1/priority=20` |
| LVHM `SuperHotLot_3` | 2000 | 28258.37 | 0, 28258.37, 56516.74 | 55145.016667；`part_3/O_SuperHotLot_3/priority=30` |

每个 slice 保留原始 `RPT#`，但 horizon 内只物化 3 个 lot；每个 lot 的 `due-release` 保持不变，`PIECES=25`、`HOTLOT=false`、source row 与 canonical ID 均写入 trace。constant release 没有生成 release-stream RandomSampleLedger 记录。Loader `0.1.3` 的 manifest hash 为：

```text
HVLM 87ce2cf364f5ccd738a8349c95deaa9d26ea1e83aadf47a499ee897906f36d73
LVHM fe1ea708721ecabe3b8eaad27b96e2172c88c3049b97bd92d0ee0e14e0e1514e
```

manifest identity 包含 loader version，因此从 `0.1.2` 升到 `0.1.3` 后 logical hash 改变；`datasets/` 原始文件字节未修改。

## 8. 测试与回归

本轮整体验收结果：

```text
pytest: 182 passed
unittest discover: 182 tests, OK
M1 / Runtime Reliability / MC01-MC08 定向回归: 26 passed
compileall: passed
git diff --check: passed
datasets/: no tracked diff
```

新增测试覆盖大 cap 小 horizon 的惰性生成、整数与小数 interval 的 `H` 精确边界、cap 截断、稳定 ID、同刻模板输入顺序无关、due 平移、元数据保真、首工序无 transport、constant release 无 ledger 样本、Scenario 不可变、真实 normal/hot/super selector，以及独立审计器对 release identity/due/provenance 篡改的检出。

## 9. Gate 更新

本审计关闭 `DI_UNSUPPORTED_RELEASE_TEMPLATES`，但不关闭其他 Data Integration blocker。Gate 仍为 `not_passed_gaps`，剩余 6 类 blocker 及其代码级回归结果以 [SMT2020 Data Integration Gate](smt2020-data-integration-gate.md) 为准。release slice 只构成 raw→static model→受限 Scenario→runtime→trace/provenance 的兼容性证据，不能生成正式 HVLM/LVHM KPI 或策略优劣结论。
