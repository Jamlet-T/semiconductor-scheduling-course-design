"""最小 DES 使用的不可变场景定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TerminationMode = Literal["until_all_complete", "fixed_horizon"]


@dataclass(frozen=True, slots=True)
class MachineSpec:
    """一台可独立占用的物理设备。"""

    machine_id: str

    def __post_init__(self) -> None:
        if not self.machine_id:
            raise ValueError("machine_id 不能为空")


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """lot 路线中的一道确定性工序。"""

    step_id: int
    processing_time: float
    eligible_machines: tuple[str, ...]
    route_id: str = "micro"
    tool_group_id: str | None = None

    def __post_init__(self) -> None:
        if self.step_id < 1:
            raise ValueError("step_id 必须从 1 开始")
        if self.processing_time <= 0:
            raise ValueError("processing_time 必须为正")
        if not self.eligible_machines:
            raise ValueError("eligible_machines 不能为空")
        if len(self.eligible_machines) != len(set(self.eligible_machines)):
            raise ValueError("eligible_machines 不能重复")


@dataclass(frozen=True, slots=True)
class LotSpec:
    """动态投放 lot 及其完整路线。"""

    lot_id: str
    release_time: float
    operations: tuple[OperationSpec, ...]
    quantity_wafers: int = 25
    due_time: float | None = None
    priority: int = 0

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


@dataclass(frozen=True, slots=True)
class Scenario:
    """一次仿真所需的静态输入。"""

    scenario_id: str
    dataset_version: str
    machines: tuple[MachineSpec, ...]
    lots: tuple[LotSpec, ...]
    termination_mode: TerminationMode = "until_all_complete"
    horizon: float | None = None

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
