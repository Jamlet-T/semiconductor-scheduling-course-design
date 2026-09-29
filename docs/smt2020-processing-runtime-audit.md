# SMT2020 Processing Runtime Closure Audit

审计日期：2026-09-22
基线：`47417c5110409c2e63c258535429f0a68f2b7818`
Simulation Contract：`0.1.3`；Policy Contract：`0.1.1`；Loader Contract：`0.1.1`

## 结论

本轮关闭 `DI_UNSUPPORTED_PROCESSING_DISTRIBUTION`、`DI_UNSUPPORTED_PROCESSING_BASIS` 与 `DI_UNSUPPORTED_EXPONENTIAL_FAILURE`。`DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE` 保持 **OPEN / BLOCKER**：原始字段已完整审计，但现有 raw 表和固定参考实现没有给出足以冻结 cascade 双时点、故障/PM tail 行为的可复现实验依据。没有用假设把该 blocker 降级。

## Raw evidence

| 观察项 | HVLM | LVHM | 证据等级 |
| --- | ---: | ---: | --- |
| route operations | 926 | 4013 | A |
| `PDIST=uniform` | 926 | 4013 | A |
| `PTPER=per_lot` | 490 | 2104 | A |
| `PTPER=per_piece` | 408 | 1774 | A |
| `PTPER=per_batch` | 28 | 135 | A |
| 带 Part/BatchInterval 的 operation | 379 | 1668 | A |
| `STNCAP=2.0` 的 tool template | 45 | 45 | A |
| `LTIME=ULTIME=1 min` 的普通/cascade template | 60 / 45 | 60 / 45 | A |
| downcal exponential calendar | 11 | 11 | A+B |

`uniform(m,w)=U[m-w/2,m+w/2]` 仍是 D 级参考实现/本地冻结解释。`exponential(m)` 的 `m` 按 MTTF/MTTR 的分钟均值解释，并转换为 `rate=1/m`；该解释同样不应写成 raw 文件自描述。

## Runtime mapping

```text
DistributionDefinition (minute)
→ TimeDistributionSpec
→ sample_distribution(spec, stream, entity, occurrence)
→ ProcessingDurationResolver
→ committed physical activity
```

- `constant(v)` 返回 `v`；`uniform(m,w)` 使用 full-width；`exponential(m)` 使用均值 `m`。
- `per_lot`：一 lot 一次 sample；`per_piece`：一次 base sample 乘 lot wafer quantity；`per_batch`：按稳定成员序列一次 sample。
- SPT 和 CR 继续使用 nominal processing，不在候选生成或策略排序时抽样。
- processing identity 为 `lot_id|route_id|step_id|visit=0`；batch identity 为 route、step 和有序成员 ID。Failure/repair 使用既有 `failure/repair + machine_id + occurrence` identity。
- Failure/PM suspend-resume 只保存剩余实现时长，旧完成事件依旧用 activity token 失效，不会重新 sample。

## Test evidence

`test_processing_runtime.py` 覆盖 constant/uniform/exponential、per-piece、commit-only sampling、FIFO/SPT CRN、Failure suspend-resume、per-batch 单物理 sample 以及 exponential failure CRN。真实 loader tests 对两模型的 PDIST/PTPER counts、real-data uniform validation slice 和 manifest provenance 做断言。

## Retained blocker

load/unload 与 cascade 尚未进入 DES。特别是 raw `PartInterval`/`BatchInterval` 需要明确区分 lot logical completion、machine release、CQT source、wafer PM accumulation 和故障/PM 对 cascade tail 的影响。当前资料不足以可靠冻结这些行为；因此没有新增 `LOAD_*`、`UNLOAD_*` 或 cascade event，也没有改变 MC07 preemptive-resume 语义。

Data Integration Gate 仍为 `not_passed_gaps`；M1 仍为 `passed`；`optimizer_enabled=false`；本轮没有启动 HVLM/LVHM 策略性能比较或 CMA-ES。
