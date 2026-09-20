"""事件日历与可审计 trace 结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import Any


class EventPriority(IntEnum):
    """Simulation Contract 0.1.0 的同刻事件优先级。"""

    PROCESS_FINISH = 10
    REPAIR_FINISH = 20
    FAILURE_START = 30
    LOT_RELEASE = 40
    MONITOR = 50
    DISPATCH_BARRIER = 60


class EventType(str, Enum):
    PROCESS_FINISH = "PROCESS_FINISH"
    LOT_RELEASE = "LOT_RELEASE"
    DISPATCH_BARRIER = "DISPATCH_BARRIER"


EVENT_PRIORITIES = {
    EventType.PROCESS_FINISH: EventPriority.PROCESS_FINISH,
    EventType.LOT_RELEASE: EventPriority.LOT_RELEASE,
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
    visit_index: int | None = None
    route_id: str | None = None
    step_id: int | None = None
    machine_id: str | None = None
    tool_group_id: str | None = None
    batch_id: str | None = None
    state_before: str | None = None
    state_after: str | None = None
    cause_event_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
