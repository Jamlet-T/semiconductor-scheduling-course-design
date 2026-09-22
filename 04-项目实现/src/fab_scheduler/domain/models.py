"""最小 DES 使用的不可变场景定义。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal


TerminationMode = Literal["until_all_complete", "fixed_horizon"]
BatchCompatibilityRule = Literal["crit_sameroutestep"]
BatchMemberSelectionRule = Literal["fifo_queue_time_lot_id"]
FailureModelType = Literal["scripted", "stochastic"]
CalendarPMModelType = Literal["scripted", "periodic"]
TimeDistributionKind = Literal["constant", "uniform", "exponential"]
ProcessingBasis = Literal["per_lot", "per_piece", "per_batch"]


@dataclass(frozen=True, slots=True)
class SourceFileProvenance:
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DatasetProvenanceSpec:
    """由 loader 自动附加到 Scenario 的原始数据身份。"""

    dataset_family: str
    model_name: str
    manifest_hash: str
    manifest_schema_version: str
    loader_version: str
    loader_contract_version: str
    loader_config: tuple[tuple[str, str], ...]
    raw_files: tuple[SourceFileProvenance, ...]

    def __post_init__(self) -> None:
        if not self.dataset_family or not self.model_name:
            raise ValueError("dataset provenance identity 不能为空")
        if len(self.manifest_hash) != 64:
            raise ValueError("manifest_hash 必须是 SHA-256")
        if not self.raw_files:
            raise ValueError("dataset provenance 必须包含 raw_files")


@dataclass(frozen=True, slots=True)
class TimeDistributionSpec:
    """分钟制时长分布；uniform 的第二参数为全宽，exponential 参数为均值。"""

    kind: TimeDistributionKind
    mean_minutes: float
    width_minutes: float = 0.0

    def __post_init__(self) -> None:
        if not isfinite(self.mean_minutes) or self.mean_minutes <= 0:
            raise ValueError("distribution mean_minutes 必须为有限正数")
        if not isfinite(self.width_minutes) or self.width_minutes < 0:
            raise ValueError("distribution width_minutes 必须为有限非负数")
        if self.kind == "constant" and self.width_minutes != 0:
            raise ValueError("constant distribution 的 width_minutes 必须为 0")
        if self.kind == "uniform":
            if self.mean_minutes - self.width_minutes / 2 <= 0:
                raise ValueError("uniform distribution 下界必须为正")
        elif self.kind == "exponential":
            if self.width_minutes != 0:
                raise ValueError("exponential distribution 的 width_minutes 必须为 0")
        elif self.kind != "constant":
            raise ValueError(f"不支持的 distribution kind：{self.kind}")


@dataclass(frozen=True, slots=True)
class ScriptedFailureSpec:
    """用于金标准的确定性绝对故障时刻和维修时长。"""

    failure_time: float
    repair_duration: float

    def __post_init__(self) -> None:
        if not isfinite(self.failure_time) or self.failure_time < 0:
            raise ValueError("scripted failure_time 必须为有限非负数")
        if not isfinite(self.repair_duration) or self.repair_duration <= 0:
            raise ValueError("scripted repair_duration 必须为有限正数")


@dataclass(frozen=True, slots=True)
class MachineFailureSpec:
    """一台物理机的 scripted 或 stochastic 故障配置。"""

    machine_id: str
    model_type: FailureModelType
    scripted_failures: tuple[ScriptedFailureSpec, ...] = ()
    failure_interval: TimeDistributionSpec | None = None
    repair_duration: TimeDistributionSpec | None = None
    clock_basis: Literal["calendar"] = "calendar"

    def __post_init__(self) -> None:
        if not self.machine_id:
            raise ValueError("failure machine_id 不能为空")
        if self.clock_basis != "calendar":
            raise ValueError("当前 failure clock_basis 仅支持 calendar")
        if self.model_type == "scripted":
            if not self.scripted_failures:
                raise ValueError("scripted failure 至少需要一个 occurrence")
            if self.failure_interval is not None or self.repair_duration is not None:
                raise ValueError("scripted failure 不接受随机分布")
            times = [item.failure_time for item in self.scripted_failures]
            if times != sorted(times) or len(times) != len(set(times)):
                raise ValueError("scripted failure_time 必须严格递增")
        elif self.model_type == "stochastic":
            if self.scripted_failures:
                raise ValueError("stochastic failure 不接受 scripted occurrence")
            if self.failure_interval is None or self.repair_duration is None:
                raise ValueError("stochastic failure 必须提供 interval 和 repair")
        else:
            raise ValueError(f"不支持的 failure model_type：{self.model_type}")


@dataclass(frozen=True, slots=True)
class ScriptedPMSpec:
    """确定性日历 PM occurrence。"""

    start_time: float
    duration: float

    def __post_init__(self) -> None:
        if not isfinite(self.start_time) or self.start_time < 0:
            raise ValueError("scripted PM start_time 必须为有限非负数")
        if not isfinite(self.duration) or self.duration <= 0:
            raise ValueError("scripted PM duration 必须为有限正数")


@dataclass(frozen=True, slots=True)
class CalendarPMSpec:
    """按绝对时刻或 calendar interval 触发的预防维护。"""

    pm_id: str
    machine_id: str
    model_type: CalendarPMModelType
    scripted_occurrences: tuple[ScriptedPMSpec, ...] = ()
    first_start_time: float | None = None
    interval: TimeDistributionSpec | None = None
    duration: TimeDistributionSpec | None = None

    def __post_init__(self) -> None:
        if not self.pm_id or not self.machine_id:
            raise ValueError("calendar PM identity 不能为空")
        if self.model_type == "scripted":
            if not self.scripted_occurrences:
                raise ValueError("scripted calendar PM 至少需要一个 occurrence")
            if any(value is not None for value in (self.first_start_time, self.interval, self.duration)):
                raise ValueError("scripted calendar PM 不接受周期字段")
            starts = [item.start_time for item in self.scripted_occurrences]
            if starts != sorted(starts) or len(starts) != len(set(starts)):
                raise ValueError("scripted PM start_time 必须严格递增")
        elif self.model_type == "periodic":
            if self.scripted_occurrences:
                raise ValueError("periodic calendar PM 不接受 scripted occurrence")
            if self.first_start_time is None or not isfinite(self.first_start_time) or self.first_start_time < 0:
                raise ValueError("periodic calendar PM 需要有限非负 first_start_time")
            if self.interval is None or self.duration is None:
                raise ValueError("periodic calendar PM 需要 interval 和 duration")
        else:
            raise ValueError(f"不支持的 calendar PM model_type：{self.model_type}")


@dataclass(frozen=True, slots=True)
class WaferPMSpec:
    """物理加工完成后按累计 wafer 触发的预防维护。"""

    pm_id: str
    machine_id: str
    threshold_wafers: int
    duration: TimeDistributionSpec
    initial_counter_wafers: int = 0
    reset_rule: Literal["reset_zero"] = "reset_zero"

    def __post_init__(self) -> None:
        if not self.pm_id or not self.machine_id:
            raise ValueError("wafer PM identity 不能为空")
        if self.threshold_wafers <= 0:
            raise ValueError("wafer PM threshold_wafers 必须为正")
        if not 0 <= self.initial_counter_wafers < self.threshold_wafers:
            raise ValueError("wafer PM initial counter 必须位于 [0, threshold)")
        if self.reset_rule != "reset_zero":
            raise ValueError("当前 wafer PM 仅支持 reset_zero")


@dataclass(frozen=True, slots=True)
class CQTSpec:
    """一条跨步骤 Critical Queue Time 约束。"""

    constraint_id: str
    route_id: str
    source_step_id: int
    target_step_id: int
    max_duration_minutes: float

    def __post_init__(self) -> None:
        if not self.constraint_id:
            raise ValueError("CQT constraint_id 不能为空")
        if not self.route_id:
            raise ValueError("CQT route_id 不能为空")
        if self.source_step_id < 1 or self.target_step_id < 1:
            raise ValueError("CQT step_id 必须从 1 开始")
        if self.target_step_id <= self.source_step_id:
            raise ValueError("CQT target step 必须晚于 source step")
        if self.max_duration_minutes <= 0:
            raise ValueError("CQT max_duration_minutes 必须为正")


@dataclass(frozen=True, slots=True)
class DedicationSpec:
    """从建立工序绑定到未来强制工序的物理机约束。"""

    dedication_id: str
    route_id: str
    source_step_id: int
    target_step_id: int

    def __post_init__(self) -> None:
        if not self.dedication_id:
            raise ValueError("Dedication dedication_id 不能为空")
        if not self.route_id:
            raise ValueError("Dedication route_id 不能为空")
        if self.source_step_id < 1 or self.target_step_id < 1:
            raise ValueError("Dedication step_id 必须从 1 开始")
        if self.target_step_id <= self.source_step_id:
            raise ValueError("Dedication target step 必须晚于 source step")


@dataclass(frozen=True, slots=True)
class MachineSpec:
    """一台可独立占用的物理设备。"""

    machine_id: str
    initial_setup: str = ""
    load_minutes: float = 0.0
    unload_minutes: float = 0.0
    cascading: bool = False

    def __post_init__(self) -> None:
        if not self.machine_id:
            raise ValueError("machine_id 不能为空")
        if self.load_minutes < 0 or self.unload_minutes < 0:
            raise ValueError("load/unload time 不能为负")


@dataclass(frozen=True, slots=True)
class BatchSpec:
    """一道 per-batch 工序的 wafer 容量与确定性组批参数。"""

    minimum_wafers: int
    maximum_wafers: int
    target_wafers: int
    max_wait_minutes: float
    compatibility_rule: BatchCompatibilityRule = "crit_sameroutestep"
    member_selection_rule: BatchMemberSelectionRule = (
        "fifo_queue_time_lot_id"
    )

    def __post_init__(self) -> None:
        if self.minimum_wafers <= 0:
            raise ValueError("batch minimum_wafers 必须为正")
        if self.maximum_wafers < self.minimum_wafers:
            raise ValueError("batch maximum_wafers 不能小于 minimum_wafers")
        if not (
            self.minimum_wafers
            <= self.target_wafers
            <= self.maximum_wafers
        ):
            raise ValueError(
                "batch target_wafers 必须位于 minimum 与 maximum 之间"
            )
        if self.max_wait_minutes < 0:
            raise ValueError("batch max_wait_minutes 不能为负")
        if self.compatibility_rule != "crit_sameroutestep":
            raise ValueError("当前仅支持 crit_sameroutestep")
        if self.member_selection_rule != "fifo_queue_time_lot_id":
            raise ValueError("当前仅支持 fifo_queue_time_lot_id")


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """lot 路线中的一道工序；processing_time 始终是派工可见的名义分钟数。"""

    step_id: int
    processing_time: float
    eligible_machines: tuple[str, ...]
    route_id: str = "micro"
    tool_group_id: str | None = None
    required_setup: str | None = None
    setup_override_minutes: float | None = None
    batch_spec: BatchSpec | None = None
    processing_distribution: TimeDistributionSpec | None = None
    processing_basis: ProcessingBasis = "per_lot"
    part_interval_minutes: float | None = None
    batch_interval_minutes: float | None = None

    def __post_init__(self) -> None:
        if self.step_id < 1:
            raise ValueError("step_id 必须从 1 开始")
        if self.processing_time <= 0:
            raise ValueError("processing_time 必须为正")
        if not self.eligible_machines:
            raise ValueError("eligible_machines 不能为空")
        if len(self.eligible_machines) != len(set(self.eligible_machines)):
            raise ValueError("eligible_machines 不能重复")
        if self.required_setup == "":
            raise ValueError("无 setup 要求应使用 None，而不是空字符串")
        if self.setup_override_minutes is not None:
            if self.required_setup is None:
                raise ValueError(
                    "setup_override_minutes 必须对应 required_setup"
                )
            if self.setup_override_minutes <= 0:
                raise ValueError("setup_override_minutes 必须为正")
        if self.processing_basis not in {"per_lot", "per_piece", "per_batch"}:
            raise ValueError(f"不支持的 processing_basis：{self.processing_basis}")
        if self.processing_basis == "per_batch" and self.batch_spec is None:
            raise ValueError("per_batch operation 必须提供 batch_spec")
        if self.part_interval_minutes is not None and self.part_interval_minutes <= 0:
            raise ValueError("part_interval_minutes 必须为正")
        if self.batch_interval_minutes is not None and self.batch_interval_minutes <= 0:
            raise ValueError("batch_interval_minutes 必须为正")


@dataclass(frozen=True, slots=True)
class SetupTransition:
    """有向 setup 转移时长。空 from_setup 表示冻结的初始 fallback。"""

    from_setup: str
    to_setup: str
    duration: float

    def __post_init__(self) -> None:
        if not self.to_setup:
            raise ValueError("to_setup 不能为空")
        if self.duration <= 0:
            raise ValueError("setup duration 必须为正")


@dataclass(frozen=True, slots=True)
class LotSpec:
    """动态投放 lot 及其完整路线。"""

    lot_id: str
    release_time: float
    operations: tuple[OperationSpec, ...]
    quantity_wafers: int = 25
    due_time: float | None = None
    priority: int = 0
    is_initial_wip: bool = False
    initial_operation_index: int = 0

    def __post_init__(self) -> None:
        if not self.lot_id:
            raise ValueError("lot_id 不能为空")
        if self.release_time < 0:
            raise ValueError("release_time 不能为负")
        if not self.operations:
            raise ValueError("operations 不能为空")
        if self.quantity_wafers <= 0:
            raise ValueError("quantity_wafers 必须为正")
        step_ids = [operation.step_id for operation in self.operations]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("同一路线的 step_id 不能重复")
        if not 0 <= self.initial_operation_index < len(self.operations):
            raise ValueError("initial_operation_index 超出路线范围")
        if not self.is_initial_wip and self.initial_operation_index != 0:
            raise ValueError("非初始 WIP 必须从路线首工序开始")
        if self.is_initial_wip and self.release_time != 0:
            raise ValueError("初始 WIP 必须在 t=0 进入系统")


@dataclass(frozen=True, slots=True)
class Scenario:
    """一次仿真所需的静态输入。"""

    scenario_id: str
    dataset_version: str
    machines: tuple[MachineSpec, ...]
    lots: tuple[LotSpec, ...]
    termination_mode: TerminationMode = "until_all_complete"
    horizon: float | None = None
    setup_transitions: tuple[SetupTransition, ...] = ()
    cqt_constraints: tuple[CQTSpec, ...] = ()
    dedication_constraints: tuple[DedicationSpec, ...] = ()
    failure_specs: tuple[MachineFailureSpec, ...] = ()
    calendar_pm_specs: tuple[CalendarPMSpec, ...] = ()
    wafer_pm_specs: tuple[WaferPMSpec, ...] = ()
    dataset_provenance: DatasetProvenanceSpec | None = None

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id 不能为空")
        if not self.dataset_version:
            raise ValueError("dataset_version 不能为空")
        machine_ids = [machine.machine_id for machine in self.machines]
        lot_ids = [lot.lot_id for lot in self.lots]
        if len(machine_ids) != len(set(machine_ids)):
            raise ValueError("machine_id 不能重复")
        if len(lot_ids) != len(set(lot_ids)):
            raise ValueError("lot_id 不能重复")
        known_machines = set(machine_ids)
        for lot in self.lots:
            for operation in lot.operations:
                unknown = set(operation.eligible_machines) - known_machines
                if unknown:
                    raise ValueError(
                        f"{lot.lot_id}/step {operation.step_id} 引用了未知设备 {sorted(unknown)}"
                    )
        if self.termination_mode == "fixed_horizon":
            if self.horizon is None or self.horizon < 0:
                raise ValueError("fixed_horizon 必须提供非负 horizon")
        elif self.horizon is not None:
            raise ValueError("until_all_complete 不应设置 horizon")
        transition_keys = [
            (transition.from_setup, transition.to_setup)
            for transition in self.setup_transitions
        ]
        if len(transition_keys) != len(set(transition_keys)):
            raise ValueError("setup transition 不能重复")
        constraint_ids = [
            constraint.constraint_id
            for constraint in self.cqt_constraints
        ]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("CQT constraint_id 不能重复")
        route_steps: dict[str, set[int]] = {}
        for lot in self.lots:
            for operation in lot.operations:
                route_steps.setdefault(operation.route_id, set()).add(
                    operation.step_id
                )
        for constraint in self.cqt_constraints:
            known_steps = route_steps.get(constraint.route_id)
            if known_steps is None:
                raise ValueError(
                    f"CQT {constraint.constraint_id} 引用了未知 route "
                    f"{constraint.route_id}"
                )
            missing = {
                constraint.source_step_id,
                constraint.target_step_id,
            } - known_steps
            if missing:
                raise ValueError(
                    f"CQT {constraint.constraint_id} 引用了 route 中不存在的 "
                    f"step {sorted(missing)}"
                )
        dedication_ids = [
            constraint.dedication_id
            for constraint in self.dedication_constraints
        ]
        if len(dedication_ids) != len(set(dedication_ids)):
            raise ValueError("Dedication dedication_id 不能重复")
        for constraint in self.dedication_constraints:
            known_steps = route_steps.get(constraint.route_id)
            if known_steps is None:
                raise ValueError(
                    f"Dedication {constraint.dedication_id} 引用了未知 route "
                    f"{constraint.route_id}"
                )
            missing = {
                constraint.source_step_id,
                constraint.target_step_id,
            } - known_steps
            if missing:
                raise ValueError(
                    f"Dedication {constraint.dedication_id} 引用了 route 中不存在的 "
                    f"step {sorted(missing)}"
                )
        failure_machines = [spec.machine_id for spec in self.failure_specs]
        if len(failure_machines) != len(set(failure_machines)):
            raise ValueError("每台 machine 最多只能有一条 failure spec")
        unknown_failure_machines = set(failure_machines) - known_machines
        if unknown_failure_machines:
            raise ValueError(
                "failure spec 引用了未知设备 "
                f"{sorted(unknown_failure_machines)}"
            )
        pm_ids = [spec.pm_id for spec in self.calendar_pm_specs + self.wafer_pm_specs]
        if len(pm_ids) != len(set(pm_ids)):
            raise ValueError("PM pm_id 不能重复")
        calendar_pm_machines = [spec.machine_id for spec in self.calendar_pm_specs]
        wafer_pm_machines = [spec.machine_id for spec in self.wafer_pm_specs]
        if len(calendar_pm_machines) != len(set(calendar_pm_machines)):
            raise ValueError("每台 machine 最多一条 calendar PM spec")
        if len(wafer_pm_machines) != len(set(wafer_pm_machines)):
            raise ValueError("每台 machine 最多一条 wafer PM spec")
        unknown_pm_machines = set(calendar_pm_machines + wafer_pm_machines) - known_machines
        if unknown_pm_machines:
            raise ValueError(f"PM spec 引用了未知设备 {sorted(unknown_pm_machines)}")
