# SMT2020 Loader Contract

版本：`0.1.4`
Loader：`fab_scheduler.data.load_smt2020` / `0.1.4`
状态：静态数据链与加工/搬运/release 受限 validation slice、sampling 判定诊断 slice 已实现；完整可执行 Scenario 尚有 blocker

## 1. 边界

```text
datasets/<model> 原始字节（只读）
→ DatasetManifest
→ TSV parser
→ SMT2020StaticModel + LoaderAudit
→ 可执行 Scenario（仅在 runtime 能完整表达语义时）
```

`load_smt2020(dataset_root, model_id, *, loader_config=None) -> LoadedScenario` 不运行策略、不修改原始文件。`LoadedScenario.scenario=None` 表示完整模型不能在不丢语义的情况下交给当前 DES；这不是成功场景的空值替代，而是明确的 Gate 状态。`mode=validation_slice`、`mode=transport_validation_slice`、`mode=release_validation_slice` 和 `mode=sampling_validation_slice` 只构造来自真实记录的受限闭包，用于 loader/API/provenance/runtime 兼容性 smoke，不是正式模型。

## 2. Manifest identity

Manifest 文件项为：

```text
relative_path + size_bytes + SHA-256
```

按 POSIX 相对路径排序后，与 `dataset_family/model_name/parser_schema_version/loader_version` 一起编码为 canonical JSON，再取 SHA-256。绝对路径和加载时间不参与 identity；`loaded_at_utc` 只作观测信息。数据版本为：

```text
<model_name>@sha256:<manifest_hash>
```

## 3. Raw → static domain 映射

| Raw field | Unit | Static domain | Conversion / validation | Evidence |
| --- | --- | --- | --- | --- |
| `part.PART/PARTFAM/ROUTEFILE/ROUTE` | — | `ProductDefinition` | route 文件与 route ID 必须存在 | A |
| `route.STEP` | sequence | `OperationDefinition.step_id` | 严格按原始 sequence，验证 `1..N`；不按文件名或 DESC 排序 | A |
| `route.STNFAM` | — | `eligible_machine_ids` | 与 `tool.STNFAM` 连接，按 `STNQTY` 展开 `{STN}#0001...` | A+B |
| `tool.STNGRP/STNFAMLOC` | — | machine group/location | downtime attachment 与 transport location | A |
| `PDIST/PTIME/PTIME2/PTUNITS` | min | `DistributionDefinition → TimeDistributionSpec` | 单位转分钟；uniform 保存 mean/full-width；commit 后统一 sampler 抽样 | A+D |
| `PTPER` | lot/piece/batch | `processing_basis` | 原值保留，不用均值伪装 runtime | A |
| `LTIME/ULTIME` | time | machine template | 单位转分钟 | A |
| `STNCAP/PartInterval/BatchInterval` | time | cascading metadata | 单位转分钟；当前 runtime blocker | A |
| `BATCHMN/BATCHMX` | wafer | operation batch bounds | 必须 `min<=max` 且 `PTPER=per_batch` | A+B |
| `BATCHCRITF/BATCHPER` | — | machine template | 当前真实值 `crit_sameroutestep/piece` | A |
| `route.SETUP/STIME` | min | setup requirement/override | WHEN 原值审计；override 转分钟 | A |
| `setup.CURSETUP/NEWSETUP/STIME` | min | `SetupTransitionDefinition` | 有向，不自动对称 | A |
| `setupgrp.SETUP/MINRUN` | run count | `SetupGroupMemberDefinition` | 空 SETUPGRP 向下继承上一显式组 | A+B |
| `STEP/STEP_CQT/CQT/CQTUNITS` | hr→min | CQT link in operation | target 必须存在且在 source 后，可跨步 | A+B |
| `SVESTN/FORSTEP` | — | dedication link in operation | target 必须存在且在 source 后 | A+B |
| `StepPercent` | percent | `OperationSpec.sample_percent` | 空值保留为 `None`；显式值校验 `(0,100]`；受限 profile 可运行，超出边界保持 BLOCKER | A+D |
| `RWKSTEP/REWORK/RWKTYPE` | percent | rework metadata | 三字段成组；比例 `(0,100]`；target 必须是同 route 更早 step；非 `lot` scope 显式 BLOCKER | A+B |
| `order.START` | datetime | first release offset | 数据最早 START 为零点 | A+B |
| `RDIST/REPEAT/RUNITS/RPT#/LOTSPERRPT` | min/count | `ReleaseTemplateDefinition` | 惰性生成；`RPT#` 包含 index 0；canonical ID 使用 model/source-row namespace | A+B |
| `DUE` | datetime | relative due allowance | `DUE-START`，每个重复 lot 后续以自身 release 平移 | A+B |
| `PRIOR/HOTLOT/PIECES` | class/bool/wafer | release/WIP fields | HOTLOT 只按字段，不按 lot 名猜测 | A |
| `WIP.CURSTEP` | step | `InitialWipDefinition` | t=0 在该 step 前等待；必须属于产品 route | A+B |
| `downcal` | time distribution | failure calendar | calendar、repair 分布均保存 | A |
| `pmcal` | calendar/pieces | PM calendar | calendar interval 转分钟；pieces 保留 wafer/piece 阈值单位 | A |
| `attach` | — | calendar attachment | calendar 与 stnfam/stngrp target 必须存在 | A+B |
| `fromto` | min | `TransportDefinition` | 当前两模型均为 `Fab→Fab uniform(7.5,2.5)` | A+D |

证据级别：A=raw 字段直接证据，B=raw 跨表推导，D=参考实现解释，E=本项目假设，F=原始历史不可恢复。

## 4. Distribution parser

统一 parser 接受 `constant/uniform/exponential` 并保留类型和转换后的参数。`TimeDistributionSpec` 与唯一 `sample_distribution(...)` runtime 统一使用分钟；processing、failure 和 PM 均复用该 sampler。`uniform(m,w)` 的第二参数仍是全宽；`exponential(m)` 以 raw `MTTF/MTTR` 的分钟均值转换为 rate `1/m`。该 exponential 参数解释来自既有 Data Contract/参考实现证据，不能表述为 raw 字段自描述。

`uniform(m,w)` 按 `U[m-w/2,m+w/2]`，证据等级为 D（PySCFabSim 固定参考实现），不是 raw 文件自描述。

## 5. Initial WIP

Loader 不猜测 initial setup、历史 dedication machine、已开启 CQT 的 source finish time和 wafer-PM counter。它分别产生 `DI_INITIAL_*_UNKNOWN` audit。当前数据可静态识别的未知历史关系为：HVLM CQT 341、dedication 2435；LVHM CQT 433、dedication 1965。默认 0 counter 和空 setup 仍只能是 E 级本地规则。

## 6. Validation slice

`LoaderConfig(mode="validation_slice")` 按 `(route_id, step_id)` 稳定选择一个真实、单工序、per-lot、无 setup/batch/CQT/dedication/sampling/rework/cascade 的记录，保留其真实 `TimeDistributionSpec` 并由 runtime 在 committed action 后抽样构造一个 lot/一台 machine 的 fixed-horizon Scenario。结果 provenance 自动嵌入全部 raw file hashes、manifest、loader/contract 版本和 selector config。

`LoaderConfig(mode="transport_validation_slice")` 稳定选择同一路线中连续、per-lot、无 setup/batch/CQT/dedication/sampling/rework/cascade route-level 字段的两道真实工序。默认选择 `Fab→Fab`，也可用 `validation_transport_pair=(from_location, to_location)` 定向验证真实未配置 pair。Scenario 保留两道工序的真实加工分布、真实 machine location 与 `fromto` 分布；搬运随机样本仅在前序工序真实完成后生成。当前 raw route 的 location 转移统计及未配置 pair 会写入 statistics/audit，不能因 `fromto` 表只有一行而被忽略。slice 不装配 machine load/unload、calendar attachment、release template 或 initial WIP；这些机制仍按各自 blocker/warning 处理。

`LoaderConfig(mode="release_validation_slice")` 稳定选择一个真实 release template，并保留其 `PART/ORDER/PIECES/PRIOR/HOTLOT/START/RDIST/REPEAT/RUNITS/RPT#/LOTSPERRPT/DUE` 原始证据和 source-row namespace。可用 `validation_release_lot_prefix=<raw LOT prefix>` 定向选择模板；selector 值必须进入 provenance，未匹配时返回明确的 validation error。该 slice 只在以下联合边界下构造 fixed-horizon Scenario：`RDIST=constant`、`RUNITS=min`、`LOTSPERRPT=1`；repeat index 从 `0` 开始，合法范围为 `[0, RPT# - 1]`，lot ID 使用：

```text
REL::<template_id>::<lot_prefix>::r<repeat_index:06d>::m<member_index:04d>
```

release 为惰性事件生成，`DUE-START` 随每个 release 平移；`ORDER/HOTLOT/PRIOR/PIECES/PART` 与 due offset 进入 immutable domain、trace 和 provenance，但策略不会自动读取 `ORDER/HOTLOT/PRIOR`。该 slice 不代表非 constant RDIST、`LOTSPERRPT>1`、非 fixed-horizon 或其他未验证组合可运行。

`LoaderConfig(mode="sampling_validation_slice")` 从真实 initial WIP 中稳定选择一个当前 step 带显式 `StepPercent` 的单工序诊断切片；也可用 `validation_sampling_operation=(route_id, step_id)` 精确选择，selector 必须进入 provenance。该切片只接受 `per_lot`、唯一 `Fab` location、无 Batch/Setup/cascade，并排除 rework；随机 `0<p<100` operation 不得是 CQT endpoint，任何 sampled operation 不得是 Dedication endpoint；显式 p=100 因无 skip 分支可作为 CQT endpoint。它保留真实 WIP 的 lot/product/order/source row、wafer 数、priority 与 due，并分别验证 `0<p<100` 的单次 sampling ledger 和 `p=100` 的无随机账本边界。初始 WIP 在 `t=0` 执行 operation-entry 判定。

当前 raw 共有 HVLM/LVHM 显式 StepPercent `221/955` 条，其中随机百分比 `149/662`、100% `72/293`；位于 initial WIP 当前 step 的 lot 数为 `98/75`，与 rework 重叠的 operation 为 `14/52`。sampled CQT target 为 `4/18`，逐条均为 p=100，stochastic sampled CQT target 为 `0/0`；因此实际 raw sampling profile 全部可由上述 runtime 表达，loader 将 `DI_UNSUPPORTED_SAMPLING` 替换为支持性 INFO。两模型全部显式 sampled 工序所属 tool template 都带 `LOAD=1 min / UNLOAD=1 min`，而 sampling slice 未执行 load/unload。loader 为此产生 `DI_SAMPLING_SLICE_OMITS_LOAD_UNLOAD` warning，并把省略分钟数写入 selector provenance；该 slice 不能关闭 `DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE`。

该 slice 验证：

```text
raw parser → static model → Scenario schema → simulate() → provenance
```

这些 slice 不验证完整加工组合、cascade 或性能结论。Loader Contract `0.1.4` 的 transport slice 仅闭合外生无容量搬运，release slice 仅闭合其受限 profile；sampling slice 诊断实际 raw 的 `visit_index=0` 判定路径，并有意不表达真实 load/unload duration。真实 sampling blocker 已关闭；rework、load/unload/cascade、setup MINRUN、batch 决策配置和 multi-calendar 等 5 类 blocker 不因此降低。

## 7. Error and blocker policy

- `ERROR`：损坏、缺字段、悬空引用或非法范围；`load_smt2020` 抛出 `LoaderError`。
- `BLOCKER`：raw 语义已识别，但当前 executable Scenario/runtime 不能无损表达；audit 返回，完整 `scenario=None`。
- `WARNING`：允许的显式假设或不可恢复历史状态。
- `INFO`：统计或来源说明。

只有 `BLOCKER count == 0` 才允许 Data Integration Gate 通过。

## 8. 版本记录

- `0.1.4`：增加 `sampling_validation_slice` 和精确 selector；将 `StepPercent` 映射到 immutable `OperationSpec`，冻结 None/100%/随机百分比、operation-entry skip、initial-WIP `t=0`、稳定 sampling identity 与 provenance；核实真实 sampled CQT target 全为 p=100 后关闭 sampling blocker。真实 sampled 工序的 load/unload 未执行，slice 仍只作判定/映射诊断，load/unload blocker 保留。
- `0.1.3`：增加 `release_validation_slice`；冻结惰性 release、包含 index 0 的 `RPT#`、model/source-row namespaced stable ID、due offset 平移和 fixed-horizon 闭区间边界；仅对 `constant RDIST + LOTSPERRPT=1` 声明 release 支持，其他组合显式保留为 unsupported/blocker。
- `0.1.2`：增加 transport validation slice 和 location-pair reconciliation；将已验证的 transport runtime 从 blocker 改为 INFO；增加 StepPercent 范围、RWK 字段组、返工目标与 scope 的严格静态校验。
- `0.1.1`：接入 processing distribution/PTPER 与 exponential failure runtime。

