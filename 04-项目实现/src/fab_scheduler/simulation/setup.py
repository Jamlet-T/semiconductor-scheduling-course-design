"""严格遵守 Data Contract 的 Setup Duration Resolver。"""

from __future__ import annotations

from collections.abc import Iterable

from fab_scheduler.domain.models import OperationSpec, SetupMinimumRun, SetupTransition


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


class SetupMinimumRunResolver:
    """按 machine setup group 判断是否允许切换 setup。

    调用方传入的 ``completed_lots`` 是当前 setup 的运行时下界；初始历史
    计数未知时由 runtime 显式以 ``count_known=False`` 管理，而不是把它
    当成已知历史。未配置的 setup 不施加 MINRUN 约束。只有声明了具体
    ``required_setup`` 且确实要切换 setup 的 action 才会被拦截。
    """

    def __init__(self, minimum_runs: Iterable[SetupMinimumRun]) -> None:
        self._minimum_runs = {
            (item.setup_group, item.setup): item.minimum_run
            for item in minimum_runs
        }

    def allows_change(
        self,
        *,
        setup_group: str | None,
        current_setup: str,
        completed_lots: int,
        operation: OperationSpec,
    ) -> bool:
        required_setup = operation.required_setup
        if required_setup is None or required_setup == current_setup:
            return True
        if setup_group is None or not current_setup:
            return True
        minimum_run = self._minimum_runs.get((setup_group, current_setup))
        if minimum_run is None:
            return True
        return completed_lots >= minimum_run
