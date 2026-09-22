# SMT2020 Loader Contract

版本：`0.1.1`
Loader：`fab_scheduler.data.load_smt2020` / `0.1.1`
状态：静态数据链已实现；完整可执行 Scenario 尚有 blocker

## 1. 边界

```text
datasets/<model> 原始字节（只读）
→ DatasetManifest
→ TSV parser
→ SMT2020StaticModel + LoaderAudit
→ 可执行 Scenario（仅在 runtime 能完整表达语义时）
```

`load_smt2020(dataset_root, model_id, *, loader_config=None) -> LoadedScenario` 不运行策略、不修改原始文件。`LoadedScenario.scenario=None` 表示完整模型不能在不丢语义的情况下交给当前 DES；这不是成功场景的空值替代，而是明确的 Gate 状态。`mode=validation_slice` 只构造一个来自真实记录的单工序闭包，用于 loader/API/provenance 兼容性 smoke，不是正式模型。

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
| `StepPercent` | percent | sampling metadata | 原值保留；runtime 未实现时 BLOCKER | A |
| `RWKSTEP/REWORK/RWKTYPE` | percent | rework metadata | 原值保留；runtime 未实现时 BLOCKER | A |
| `order.START` | datetime | first release offset | 数据最早 START 为零点 | A+B |
| `RDIST/REPEAT/RUNITS/RPT#/LOTSPERRPT` | min/count | `ReleaseTemplateDefinition` | 不预展开 20 万级模板 | A |
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

该 slice 验证：

```text
raw parser → static model → Scenario schema → simulate() → provenance
```

它不验证完整加工组合、cascade 或性能结论。

## 7. Error and blocker policy

- `ERROR`：损坏、缺字段、悬空引用或非法范围；`load_smt2020` 抛出 `LoaderError`。
- `BLOCKER`：raw 语义已识别，但当前 executable Scenario/runtime 不能无损表达；audit 返回，完整 `scenario=None`。
- `WARNING`：允许的显式假设或不可恢复历史状态。
- `INFO`：统计或来源说明。

只有 `BLOCKER count == 0` 才允许 Data Integration Gate 通过。

