"""派工策略的稳定接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class DispatchState:
    """策略可观察的当前状态。"""

    current_time: float


@dataclass(frozen=True, slots=True)
class DispatchAction:
    """一个已通过可行性过滤的 lot-machine 动作。"""

    lot_id: str
    machine_id: str
    operation_index: int
    step_id: int
    queue_entered_at: float
    release_time: float


class DispatchPolicy(Protocol):
    """所有基准与改进策略必须实现的接口。"""

    name: str

    def select(
        self,
        state: DispatchState,
        feasible_actions: Sequence[DispatchAction],
    ) -> DispatchAction | None:
        """从可行动作中选择一个动作；无动作时返回 None。"""

