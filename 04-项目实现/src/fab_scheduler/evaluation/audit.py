"""从结果日志独立重算基础指标并检查 M1 资源不变量。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Iterable

from fab_scheduler.domain.models import Scenario
from fab_scheduler.simulation.engine import SimulationResult
from fab_scheduler.simulation.events import TraceRecord


@dataclass(frozen=True, slots=True)
class TraceMetricRecalculation:
    released_lots: int
    completed_lots: int
    completion_ratio: float
    mean_cycle_time_completed: float | None
    throughput_lots_per_minute: float
    terminal_wip_lots: int
    mean_wip: float


@dataclass(frozen=True, slots=True)
class ResultInvariantAudit:
    violations: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def recompute_trace_metrics(
    trace: Iterable[TraceRecord],
    *,
    end_time: float,
    initial_wip_lot_ids: frozenset[str] = frozenset(),
) -> TraceMetricRecalculation:
    """只依据 trace 与终止时刻重算基础 lot 指标。"""

    releases: dict[str, float] = {}
    completions: dict[str, float] = {}
    wip_changes: list[tuple[float, int, int]] = []
    for record in trace:
        lot_id = record.lot_id
        if record.event_type == "LOT_RELEASE" and lot_id is not None:
            if lot_id in releases:
                raise ValueError(f"重复 LOT_RELEASE：{lot_id}")
            releases[lot_id] = record.sim_time
            wip_changes.append((record.sim_time, 1, record.event_seq))
        elif record.event_type == "LOT_COMPLETE" and lot_id is not None:
            if lot_id in completions:
                raise ValueError(f"重复 LOT_COMPLETE：{lot_id}")
            if lot_id not in releases:
                raise ValueError(f"LOT_COMPLETE 缺少 release：{lot_id}")
            completions[lot_id] = record.sim_time
            wip_changes.append((record.sim_time, -1, record.event_seq))

    completed_cycle_times = [
        completed_at - releases[lot_id]
        for lot_id, completed_at in completions.items()
        if lot_id not in initial_wip_lot_ids
    ]
    released = len(releases)
    completed = len(completions)
    area = 0.0
    level = 0
    previous_time = 0.0
    for time, delta, _ in sorted(wip_changes):
        area += level * (time - previous_time)
        level += delta
        previous_time = time
    area += level * (end_time - previous_time)

    return TraceMetricRecalculation(
        released_lots=released,
        completed_lots=completed,
        completion_ratio=completed / released if released else 0.0,
        mean_cycle_time_completed=(
            fmean(completed_cycle_times) if completed_cycle_times else None
        ),
        throughput_lots_per_minute=(
            completed / end_time if end_time > 0 else 0.0
        ),
        terminal_wip_lots=released - completed,
        mean_wip=area / end_time if end_time > 0 else 0.0,
    )


def audit_result_invariants(
    result: SimulationResult,
    scenario: Scenario,
    *,
    tolerance: float = 1e-9,
) -> ResultInvariantAudit:
    """检查指标重算、lot 守恒、设备时间和机制记录的一致性。"""

    violations: list[str] = []
    initial_wip = frozenset(
        lot.lot_id for lot in scenario.lots if lot.is_initial_wip
    )
    recalculated = recompute_trace_metrics(
        result.trace,
        end_time=result.metrics.end_time,
        initial_wip_lot_ids=initial_wip,
    )
    comparisons = {
        "released_lots": (
            float(result.metrics.released_lots),
            float(recalculated.released_lots),
        ),
        "completed_lots": (
            float(result.metrics.completed_lots),
            float(recalculated.completed_lots),
        ),
        "completion_ratio": (
            result.metrics.completion_ratio,
            recalculated.completion_ratio,
        ),
        "throughput_lots_per_minute": (
            result.metrics.throughput_lots_per_minute,
            recalculated.throughput_lots_per_minute,
        ),
        "terminal_wip_lots": (
            float(result.metrics.terminal_wip_lots),
            float(recalculated.terminal_wip_lots),
        ),
        "mean_wip": (result.metrics.mean_wip, recalculated.mean_wip),
    }
    if (
        result.metrics.mean_cycle_time_completed is None
        or recalculated.mean_cycle_time_completed is None
    ):
        if (
            result.metrics.mean_cycle_time_completed
            is not recalculated.mean_cycle_time_completed
        ):
            violations.append("mean_cycle_time_completed 不一致")
    elif abs(
        result.metrics.mean_cycle_time_completed
        - recalculated.mean_cycle_time_completed
    ) > tolerance:
        violations.append("mean_cycle_time_completed 不一致")
    for name, (reported, expected) in comparisons.items():
        if abs(reported - expected) > tolerance:
            violations.append(f"{name} 不一致：reported={reported}, trace={expected}")

    if (
        result.metrics.released_lots
        != result.metrics.completed_lots + result.metrics.terminal_wip_lots
    ):
        violations.append("lot 守恒不成立")

    for machine_id, stats in result.machine_statistics.items():
        accounted = (
            stats.processing_time
            + stats.setup_time
            + stats.idle_time
            + stats.failure_downtime
            + stats.pm_downtime
        )
        if abs(accounted - result.metrics.end_time) > tolerance:
            violations.append(
                f"{machine_id} 时间不守恒：{accounted} != {result.metrics.end_time}"
            )

    batch_specs = {
        (operation.route_id, operation.step_id): operation.batch_spec
        for lot in scenario.lots
        for operation in lot.operations
        if operation.batch_spec is not None
    }
    for interval in result.batch_intervals:
        if sum(interval.member_wafers) != interval.total_wafers:
            violations.append(f"{interval.batch_id} wafer 合计不一致")
        spec = batch_specs.get((interval.route_id, interval.step_id))
        if spec is None:
            violations.append(f"{interval.batch_id} 缺少 BatchSpec")
        elif not (
            spec.minimum_wafers
            <= interval.total_wafers
            <= spec.maximum_wafers
        ):
            violations.append(f"{interval.batch_id} 超出 wafer 容量")

    for record in result.cqt_records:
        actual = record.closed_at - record.opened_at
        excess = max(0.0, actual - record.limit)
        if abs(actual - record.actual_duration) > tolerance:
            violations.append(f"{record.constraint_id} CQT duration 不一致")
        if abs(excess - record.excess_duration) > tolerance:
            violations.append(f"{record.constraint_id} CQT excess 不一致")
        if record.violation != (actual > record.limit):
            violations.append(f"{record.constraint_id} CQT violation 不一致")

    dedication_keys = [
        (record.lot_id, record.dedication_id, record.visit_index)
        for record in result.dedication_records
    ]
    if len(dedication_keys) != len(set(dedication_keys)):
        violations.append("Dedication released record key 重复")
    for record in result.dedication_records:
        if record.released_at < record.established_at:
            violations.append(f"{record.dedication_id} 释放早于建立")

    return ResultInvariantAudit(tuple(violations))
