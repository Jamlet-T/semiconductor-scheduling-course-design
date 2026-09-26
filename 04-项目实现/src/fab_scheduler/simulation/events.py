"""事件日历与可审计 trace 结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import Any


class EventPriority(IntEnum):
    """Simulation Contract 0.1.5 的同刻事件优先级。"""

    PROCESS_FINISH = 10
    REPAIR_FINISH = 20
    FAILURE_START = 30
    PM_START = 35
    LOT_RELEASE = 40
    MONITOR = 50
    DISPATCH_BARRIER = 60


class EventType(str, Enum):
    PM_FINISH = "PM_FINISH"
    PM_START = "PM_START"
    REPAIR_FINISH = "REPAIR_FINISH"
    FAILURE_START = "FAILURE_START"
    PROCESS_FINISH = "PROCESS_FINISH"
    BATCH_FINISH = "BATCH_FINISH"
    SETUP_FINISH = "SETUP_FINISH"
    TRANSPORT_ARRIVE = "TRANSPORT_ARRIVE"
    LOT_RELEASE = "LOT_RELEASE"
    BATCH_TIMEOUT = "BATCH_TIMEOUT"
    DISPATCH_BARRIER = "DISPATCH_BARRIER"


EVENT_PRIORITIES = {
    EventType.PM_FINISH: EventPriority.REPAIR_FINISH,
    EventType.PM_START: EventPriority.PM_START,
    EventType.REPAIR_FINISH: EventPriority.REPAIR_FINISH,
    EventType.FAILURE_START: EventPriority.FAILURE_START,
    EventType.PROCESS_FINISH: EventPriority.PROCESS_FINISH,
    EventType.BATCH_FINISH: EventPriority.PROCESS_FINISH,
    EventType.SETUP_FINISH: EventPriority.PROCESS_FINISH,
    EventType.TRANSPORT_ARRIVE: EventPriority.PROCESS_FINISH,
    EventType.LOT_RELEASE: EventPriority.LOT_RELEASE,
    EventType.BATCH_TIMEOUT: EventPriority.MONITOR,
    EventType.DISPATCH_BARRIER: EventPriority.DISPATCH_BARRIER,
}


@dataclass(order=True, frozen=True, slots=True)
class Event:
    """堆排序键严格为 time、priority、seq。"""

    time: float
    priority: int
    seq: int
    event_type: EventType = field(compare=False)
    entity_id: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False)


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """仿真事件和派生状态转移的统一审计记录。"""

    run_id: str
    event_seq: int
    sim_time: float
    priority: int
    event_type: str
    lot_id: str | None = None
    product_id: str | None = None
    order_id: str | None = None
    hot_lot: bool | None = None
    source_row: str | int | None = None
    lot_due_time: float | None = None
    lot_priority: int | None = None
    lot_quantity_wafers: int | None = None
    release_template_id: str | None = None
    release_repeat_index: int | None = None
    release_member_index: int | None = None
    visit_index: int | None = None
    route_id: str | None = None
    step_id: int | None = None
    machine_id: str | None = None
    tool_group_id: str | None = None
    batch_id: str | None = None
    batch_member_lot_ids: tuple[str, ...] | None = None
    batch_member_wafers: tuple[int, ...] | None = None
    batch_total_wafers: int | None = None
    batch_start_reason: str | None = None
    cqt_constraint_id: str | None = None
    cqt_source_step_id: int | None = None
    cqt_target_step_id: int | None = None
    cqt_limit: float | None = None
    cqt_opened_at: float | None = None
    cqt_deadline: float | None = None
    cqt_closed_at: float | None = None
    cqt_actual_duration: float | None = None
    cqt_slack: float | None = None
    cqt_violation: bool | None = None
    cqt_excess_duration: float | None = None
    dedication_id: str | None = None
    dedication_source_step_id: int | None = None
    dedication_target_step_id: int | None = None
    dedication_bound_machine_id: str | None = None
    dedication_established_at: float | None = None
    dedication_released_at: float | None = None
    dedication_audit_reason: str | None = None
    failure_occurrence_index: int | None = None
    failure_model_type: str | None = None
    interrupted_activity_kind: str | None = None
    remaining_duration: float | None = None
    repair_duration: float | None = None
    activity_token: int | None = None
    downtime_cause: str | None = None
    pm_id: str | None = None
    pm_trigger_type: str | None = None
    pm_occurrence_index: int | None = None
    pm_duration: float | None = None
    transport_from_location: str | None = None
    transport_to_location: str | None = None
    transport_duration: float | None = None
    transport_missing_pair: bool | None = None
    wafer_counter_before: int | None = None
    wafer_counter_after: int | None = None
    wafer_threshold: int | None = None
    processed_wafers: int | None = None
    sampling_percent: float | None = None
    sampling_draw: float | None = None
    sampling_performed: bool | None = None
    sampling_entity_id: str | None = None
    state_before: str | None = None
    state_after: str | None = None
    cause_event_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
