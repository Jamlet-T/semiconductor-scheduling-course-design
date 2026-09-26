"""FIFO 基准策略。"""

from __future__ import annotations

from collections.abc import Sequence

from fab_scheduler.policies.base import (
    DispatchAction,
    DispatchState,
)


class FIFOPolicy:
    """按当前工序入队时间排序，使用稳定实体 ID 打破平局。"""

    name = "FIFO"
    policy_id = "fifo"

    def select(
        self,
        state: DispatchState,
        feasible_actions: Sequence[DispatchAction],
    ) -> DispatchAction | None:
        del state
        if not feasible_actions:
            return None
        return min(
            feasible_actions,
            key=lambda action: (
                action.queue_entered_at,
                action.lot_id,
                action.machine_id,
                action.operation_index,
                action.action_id,
            ),
        )
