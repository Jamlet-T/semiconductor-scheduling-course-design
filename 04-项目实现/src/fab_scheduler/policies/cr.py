"""Critical Ratio baseline。"""

from __future__ import annotations

from collections.abc import Sequence

from fab_scheduler.policies.base import DispatchAction, DispatchContext


class CRPolicy:
    """按最小 `(due-now)/remaining_nominal_processing` 排序。"""

    name = "CR"
    policy_id = "cr"

    def select(
        self,
        state: DispatchContext,
        feasible_actions: Sequence[DispatchAction],
    ) -> DispatchAction | None:
        if not feasible_actions:
            return None
        return min(
            feasible_actions,
            key=lambda action: (
                action.critical_ratio(state.current_time),
                action.oldest_queue_time,
                action.action_id,
            ),
        )
