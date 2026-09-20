"""严格遵守 Data Contract 的 Setup Duration Resolver。"""

from __future__ import annotations

from collections.abc import Iterable

from fab_scheduler.domain.models import OperationSpec, SetupTransition


class SetupResolutionError(ValueError):
    """需要换型但无法严格解析时长。"""


class SetupDurationResolver:
    """集中实现 setup 时长优先级，禁止调用点自行 fallback。"""

    def __init__(self, transitions: Iterable[SetupTransition]) -> None:
        self._durations = {
            (transition.from_setup, transition.to_setup): transition.duration
            for transition in transitions
        }

    def resolve(
        self,
        *,
        current_setup: str,
        operation: OperationSpec,
    ) -> float:
        required_setup = operation.required_setup
        if required_setup is None or current_setup == required_setup:
            return 0.0
        if operation.setup_override_minutes is not None:
            return operation.setup_override_minutes
        exact = self._durations.get((current_setup, required_setup))
        if exact is not None:
            return exact
        initial_fallback = self._durations.get(("", required_setup))
        if initial_fallback is not None:
            return initial_fallback
        raise SetupResolutionError(
            "无法解析 setup 时长："
            f"current={current_setup!r}, required={required_setup!r}, "
            f"route={operation.route_id!r}, step={operation.step_id}"
        )
