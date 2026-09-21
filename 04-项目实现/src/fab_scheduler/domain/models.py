"""最小 DES 使用的不可变场景定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TerminationMode = Literal["until_all_complete", "fixed_horizon"]
BatchCompatibilityRule = Literal["crit_sameroutestep"]
BatchMemberSelectionRule = Literal["fifo_queue_time_lot_id"]


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

    def __post_init__(self) -> None:
        if not self.machine_id:
            raise ValueError("machine_id 不能为空")


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
    """lot 路线中的一道确定性工序。"""

    step_id: int
    processing_time: float
    eligible_machines: tuple[str, ...]
    route_id: str = "micro"
    tool_group_id: str | None = None
    required_setup: str | None = None
    setup_override_minutes: float | None = None
    batch_spec: BatchSpec | None = None

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
