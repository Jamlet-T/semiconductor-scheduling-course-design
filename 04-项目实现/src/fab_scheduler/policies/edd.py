"""Earliest Due Date baseline。"""

from __future__ import annotations

from collections.abc import Sequence

from fab_scheduler.policies.base import DispatchAction, DispatchContext


class EDDPolicy:
    """按最早代表交期排序；无交期 action 的代表交期为 +inf。"""

    name = "EDD"
    policy_id = "edd"

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
                action.representative_due_date,
                action.oldest_queue_time,
                action.action_id,
            ),
        )
