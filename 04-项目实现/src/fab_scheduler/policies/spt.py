"""Shortest Processing Time baseline。"""

from __future__ import annotations

from collections.abc import Sequence

from fab_scheduler.policies.base import DispatchAction, DispatchContext


class SPTPolicy:
    """按一次物理加工时长排序；Setup 和未来停机不进入主键。"""

    name = "SPT"
    policy_id = "spt"

    def select(
        self,
        state: DispatchContext,
        feasible_actions: Sequence[DispatchAction],
    ) -> DispatchAction | None:
        del state
        if not feasible_actions:
            return None
        return min(
            feasible_actions,
            key=lambda action: (
                action.physical_processing_time,
                action.oldest_queue_time,
                action.action_id,
            ),
        )
