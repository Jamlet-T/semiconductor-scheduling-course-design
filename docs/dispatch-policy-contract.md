# Dispatch Policy Contract

版本：`0.1.1`

适用范围：M1 deterministic baseline policies。本文定义策略如何比较 Engine 已判定可行的动作，不修改 Simulation Contract `0.1.4` 的物理事件语义。

## 1. 职责边界

统一动作链为：

```text
Engine.generate feasible DispatchAction
→ DispatchPolicy.select(context, actions)
→ Engine central commit
→ Setup / Batch / Dedication / Failure / PM runtime
```

Engine 独占 qualification、Dedication、Batch legality、machine availability、Failure/PM、route precedence、RESERVED 状态与 Setup resolver。Policy 只读，不修改 Simulator，不建立 binding，不安排事件，也不请求随机数。

## 2. DispatchAction

每个不可变 action 至少包含：

- `action_id`：稳定 canonical identity；
- `machine_id`；
- `action_type`：`ordinary` 或 `batch`；
- `member_lot_ids`：按 BatchFormation 已冻结顺序排列；
- `route_id / step_id / operation_index`；
- `queue_entered_at`：最老成员的入队时刻；
- `physical_processing_time`：派工时可见的**名义**一次物理加工时长；
- member due dates；
- member remaining nominal processing times。

普通 action 只有一个 member。Batch action 的成员由 Engine/BatchFormation 在 policy 之前确定，policy 不得重新组批。canonical ID 不使用 Python object ID、容器偶然顺序或随机 UUID。

## 3. 统一接口与平局规则

四种策略实现同一接口：

```python
select(
    context: DispatchContext,
    feasible_actions: Sequence[DispatchAction],
) -> DispatchAction | None
```

除 FIFO 保留已经验证的既有稳定键外，SPT/EDD/CR 的统一平局规则为：

```text
primary policy key
→ oldest_queue_time
→ canonical action_id
```

FIFO 保留：

```text
queue_entered_at
→ representative lot_id
→ machine_id
→ operation_index
→ action_id
```

最后增加 `action_id` 不改变既有 FIFO 结果。

## 4. Baseline definitions

### FIFO

按当前工序 `queue_entered_at` 最早者优先。Batch 使用 BatchFormation 选出的第一个稳定成员作为代表，因此与 MC04 既有语义一致。

### SPT

按 `physical_processing_time` 最短者优先。该字段在本版本表示分布期望值按 `PTPER` 换算后的名义时长；普通 action 使用当前 operation 的名义加工时长；Batch 使用一次物理 batch 的名义加工时长，不乘成员数。真实随机 realization 只能在 central commit 后抽样，不能被 SPT 前视。Setup、transport、future downtime 和等待不计入 SPT。

### EDD

普通 action 使用 lot due date。Batch action 使用所有有 due date 成员中的最小值。全部成员均无 due date 时为 `+∞`。

### CR

对每个 member：

$$
CR_j(t)=\frac{d_j-t}{RPT_j}
$$

`RPT` 与 Metric Contract 一致，只含当前 operation 未执行的 nominal processing 与后续 nominal processing，不含 Setup、transport、等待和 expected downtime。缺少 due date 时为 `+∞`；负值合法；分母必须为有限正数，否则 Action invariant 失败。Batch action 使用 member CR 的最小值。

随机加工不会改变 `RPT` 的 nominal 口径：未提交候选和未来 operation 均不得抽样，已提交 activity 的 realized duration 仅用于 DES 时间推进与活动快照。

## 5. Public simulation API

公共入口：

```python
simulate(theta, scenario, seed, *, git_commit=None) -> SimulationResult
```

Policy Contract `0.1.1` 的 theta schema：

```json
{
  "policy_id": "fifo | spt | edd | cr",
  "parameters": {}
}
```

当前 baseline 不接受非空 parameters，不执行搜索，也不隐式选择 seed。Scenario 是不可变 specification；每次调用新建独立 runtime。结果统一在 provenance 中保存 policy ID、parameters、seed、Contract、scenario、termination 和随机流配置。

## 6. Common Random Numbers

CRN 的验收定义是：

```text
same seed + stream_name + entity_id + occurrence_index
→ same distribution parameters and sample
```

RandomSampleLedger 只记录运行时真正请求的随机样本，不预采样未来变量。跨策略共有 identity 的记录必须完全一致；只在一条系统轨迹中出现的 identity 标为 trajectory-specific occurrence，不视为 CRN failure。CRN 不要求不同策略具有相同 dispatch、KPI、随机事件实际发生时刻或完整 trace。
