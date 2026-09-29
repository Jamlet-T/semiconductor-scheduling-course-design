"""跨步骤 Critical Queue Time 的独立运行时。"""

from __future__ import annotations

from dataclasses import dataclass

from fab_scheduler.domain.models import CQTSpec


CQT_RUNTIME_SCHEMA_VERSION = "0.1.0"


class CQTRuntimeError(RuntimeError):
    """CQT 时钟出现重复开闭或状态不一致。"""


@dataclass(frozen=True, slots=True)
class ActiveCQTClock:
    constraint_id: str
    lot_id: str
    route_id: str
    source_step_id: int
    target_step_id: int
    visit_index: int
    opened_at: float
    deadline: float
    max_duration_minutes: float


@dataclass(frozen=True, slots=True)
class CQTClockState:
    constraint_id: str
    lot_id: str
    visit_index: int
    as_of: float
    elapsed: float
    slack: float
    risk: float
    overdue: bool
    exposure: float


@dataclass(frozen=True, slots=True)
class CQTRecord:
    constraint_id: str
    lot_id: str
    route_id: str
    source_step_id: int
    target_step_id: int
    visit_index: int
    opened_at: float
    closed_at: float
    actual_duration: float
    limit: float
    slack: float
    violation: bool
    excess_duration: float


@dataclass(frozen=True, slots=True)
class TerminalCQTSnapshot:
    constraint_id: str
    lot_id: str
    route_id: str
    source_step_id: int
    target_step_id: int
    visit_index: int
    opened_at: float
    deadline: float
    limit: float
    as_of: float
    elapsed: float
    slack: float
    risk: float
    overdue: bool
    exposure: float


@dataclass(frozen=True, slots=True)
class CQTMetrics:
    closed_cqt_count: int
    cqt_violation_count: int
    total_cqt_excess: float
    max_cqt_excess: float
    open_cqt_count: int
    overdue_open_cqt_count: int
    terminal_cqt_exposure: float


class CQTRuntime:
    """管理所有活动 CQT 时钟；不改变 DES 可行域或事件推进。"""

    def __init__(self, specs: tuple[CQTSpec, ...]) -> None:
        self._specs = tuple(
            sorted(specs, key=lambda item: item.constraint_id)
        )
        self._by_source: dict[tuple[str, int], tuple[CQTSpec, ...]] = {}
        self._by_target: dict[tuple[str, int], tuple[CQTSpec, ...]] = {}
        for spec in self._specs:
            source_key = (spec.route_id, spec.source_step_id)
            target_key = (spec.route_id, spec.target_step_id)
            self._by_source[source_key] = (
                *self._by_source.get(source_key, ()),
                spec,
            )
            self._by_target[target_key] = (
                *self._by_target.get(target_key, ()),
                spec,
            )
        self._active: dict[
            tuple[str, str, int], ActiveCQTClock
        ] = {}
        self._closed: list[CQTRecord] = []

    @property
    def records(self) -> tuple[CQTRecord, ...]:
        return tuple(self._closed)

    @property
    def active_clocks(self) -> tuple[ActiveCQTClock, ...]:
        return tuple(
            self._active[key]
            for key in sorted(self._active)
        )

    def open_for_source(
        self,
        *,
        lot_id: str,
        route_id: str,
        step_id: int,
        opened_at: float,
        visit_index: int = 0,
    ) -> tuple[ActiveCQTClock, ...]:
        opened: list[ActiveCQTClock] = []
        for spec in self._by_source.get((route_id, step_id), ()):
            key = (lot_id, spec.constraint_id, visit_index)
            if key in self._active:
                raise CQTRuntimeError(f"重复开启 CQT clock：{key}")
            clock = ActiveCQTClock(
                constraint_id=spec.constraint_id,
                lot_id=lot_id,
                route_id=route_id,
                source_step_id=spec.source_step_id,
                target_step_id=spec.target_step_id,
                visit_index=visit_index,
                opened_at=opened_at,
                deadline=opened_at + spec.max_duration_minutes,
                max_duration_minutes=spec.max_duration_minutes,
            )
            self._active[key] = clock
            opened.append(clock)
        return tuple(opened)

    def close_for_target(
        self,
        *,
        lot_id: str,
        route_id: str,
        step_id: int,
        closed_at: float,
        visit_index: int = 0,
    ) -> tuple[CQTRecord, ...]:
        closed: list[CQTRecord] = []
        for spec in self._by_target.get((route_id, step_id), ()):
            key = (lot_id, spec.constraint_id, visit_index)
            clock = self._active.pop(key, None)
            if clock is None:
                raise CQTRuntimeError(f"关闭不存在的 CQT clock：{key}")
            actual = closed_at - clock.opened_at
            if actual < 0:
                raise CQTRuntimeError(f"CQT clock 时间倒退：{key}")
            slack = spec.max_duration_minutes - actual
            excess = max(0.0, -slack)
            record = CQTRecord(
                constraint_id=spec.constraint_id,
                lot_id=lot_id,
                route_id=route_id,
                source_step_id=spec.source_step_id,
                target_step_id=spec.target_step_id,
                visit_index=visit_index,
                opened_at=clock.opened_at,
                closed_at=closed_at,
                actual_duration=actual,
                limit=spec.max_duration_minutes,
                slack=slack,
                violation=actual > spec.max_duration_minutes,
                excess_duration=excess,
            )
            self._closed.append(record)
            closed.append(record)
        return tuple(closed)

    def query(
        self,
        *,
        lot_id: str,
        constraint_id: str,
        at_time: float,
        visit_index: int = 0,
    ) -> CQTClockState:
        key = (lot_id, constraint_id, visit_index)
        clock = self._active.get(key)
        if clock is None:
            raise CQTRuntimeError(f"CQT clock 不处于 active：{key}")
        elapsed = at_time - clock.opened_at
        if elapsed < 0:
            raise CQTRuntimeError(f"CQT 查询时间早于开启时间：{key}")
        slack = clock.max_duration_minutes - elapsed
        exposure = max(0.0, -slack)
        return CQTClockState(
            constraint_id=constraint_id,
            lot_id=lot_id,
            visit_index=visit_index,
            as_of=at_time,
            elapsed=elapsed,
            slack=slack,
            risk=elapsed / clock.max_duration_minutes,
            overdue=at_time > clock.deadline,
            exposure=exposure,
        )

    def terminal_snapshots(
        self,
        *,
        at_time: float,
    ) -> tuple[TerminalCQTSnapshot, ...]:
        snapshots: list[TerminalCQTSnapshot] = []
        for clock in self.active_clocks:
            state = self.query(
                lot_id=clock.lot_id,
                constraint_id=clock.constraint_id,
                visit_index=clock.visit_index,
                at_time=at_time,
            )
            snapshots.append(
                TerminalCQTSnapshot(
                    constraint_id=clock.constraint_id,
                    lot_id=clock.lot_id,
                    route_id=clock.route_id,
                    source_step_id=clock.source_step_id,
                    target_step_id=clock.target_step_id,
                    visit_index=clock.visit_index,
                    opened_at=clock.opened_at,
                    deadline=clock.deadline,
                    limit=clock.max_duration_minutes,
                    as_of=at_time,
                    elapsed=state.elapsed,
                    slack=state.slack,
                    risk=state.risk,
                    overdue=state.overdue,
                    exposure=state.exposure,
                )
            )
        return tuple(snapshots)

    def metrics(self, *, at_time: float) -> CQTMetrics:
        terminal = self.terminal_snapshots(at_time=at_time)
        violations = [record for record in self._closed if record.violation]
        return CQTMetrics(
            closed_cqt_count=len(self._closed),
            cqt_violation_count=len(violations),
            total_cqt_excess=sum(
                record.excess_duration for record in violations
            ),
            max_cqt_excess=max(
                (record.excess_duration for record in violations),
                default=0.0,
            ),
            open_cqt_count=len(terminal),
            overdue_open_cqt_count=sum(
                snapshot.overdue for snapshot in terminal
            ),
            terminal_cqt_exposure=sum(
                snapshot.exposure for snapshot in terminal
            ),
        )
