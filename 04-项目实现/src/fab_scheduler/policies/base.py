"""派工策略的稳定接口。"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, isfinite
from typing import Literal, Protocol, Sequence


DISPATCH_POLICY_CONTRACT_VERSION = "0.1.1"


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """策略可观察的当前状态。"""

    current_time: float


# 保留已验证测试和第三方策略的旧导入名；二者是同一个只读类型。
DispatchState = DispatchContext


@dataclass(frozen=True, slots=True)
class DispatchAction:
    """Engine 生成并通过全部硬约束过滤的不可变派工动作。"""

    action_id: str
    lot_id: str
    machine_id: str
    action_type: Literal["ordinary", "batch"]
    member_lot_ids: tuple[str, ...]
    operation_index: int
    route_id: str
    step_id: int
    queue_entered_at: float
    release_time: float
    physical_processing_time: float
    member_due_times: tuple[float | None, ...]
    member_remaining_nominal_processing_times: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.action_id or not self.lot_id or not self.machine_id:
            raise ValueError("DispatchAction identity 不能为空")
        if not self.member_lot_ids or self.lot_id != self.member_lot_ids[0]:
            raise ValueError("lot_id 必须是 member_lot_ids 的稳定代表成员")
        if self.action_type == "ordinary" and len(self.member_lot_ids) != 1:
            raise ValueError("ordinary action 必须只有一个 member")
        if self.action_type not in {"ordinary", "batch"}:
            raise ValueError(f"未知 action_type：{self.action_type}")
        if not isfinite(self.physical_processing_time) or self.physical_processing_time <= 0:
            raise ValueError("physical_processing_time 必须为有限正数")
        if len(self.member_due_times) != len(self.member_lot_ids):
            raise ValueError("member_due_times 长度与成员不一致")
        if len(self.member_remaining_nominal_processing_times) != len(
            self.member_lot_ids
        ):
            raise ValueError("remaining processing 长度与成员不一致")
        if any(
            not isfinite(value) or value <= 0
            for value in self.member_remaining_nominal_processing_times
        ):
            raise ValueError("remaining nominal processing 必须为有限正数")

    @property
    def oldest_queue_time(self) -> float:
        return self.queue_entered_at

    @property
    def representative_due_date(self) -> float:
        due_dates = [value for value in self.member_due_times if value is not None]
        return min(due_dates) if due_dates else inf

    def critical_ratio(self, current_time: float) -> float:
        """返回 action 中最紧迫 member 的 CR；缺少 due date 时为 +inf。"""

        ratios = [
            (due - current_time) / remaining
            for due, remaining in zip(
                self.member_due_times,
                self.member_remaining_nominal_processing_times,
                strict=True,
            )
            if due is not None
        ]
        return min(ratios) if ratios else inf

    def canonical_snapshot(self) -> tuple[object, ...]:
        """供 E06 审计比较 policy 所见 action 集，不暴露 mutable runtime。"""

        return (
            self.action_id,
            self.machine_id,
            self.action_type,
            self.member_lot_ids,
            self.route_id,
            self.step_id,
            self.operation_index,
            self.queue_entered_at,
            self.physical_processing_time,
        )


class DispatchPolicy(Protocol):
    """所有基准与改进策略必须实现的接口。"""

    name: str
    policy_id: str

    def select(
        self,
        state: DispatchContext,
        feasible_actions: Sequence[DispatchAction],
    ) -> DispatchAction | None:
        """从可行动作中选择一个动作；无动作时返回 None。"""
