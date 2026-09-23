"""从结果日志独立重算基础指标并检查 M1 资源不变量。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor
from statistics import fmean
from typing import Iterable

from fab_scheduler.domain.models import Scenario
from fab_scheduler.simulation.engine import SimulationResult
from fab_scheduler.simulation.events import TraceRecord
from fab_scheduler.simulation.release import release_lot_id, release_time
from fab_scheduler.simulation.release import (
    RELEASE_RUNTIME_ID,
    RELEASE_RUNTIME_SCHEMA_VERSION,
)
from fab_scheduler.simulation.provenance import SIMULATION_CONTRACT_VERSION


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
    if result.provenance.simulation_contract_version != SIMULATION_CONTRACT_VERSION:
        violations.append("provenance Simulation Contract version 不一致")
    if result.provenance.termination_condition != scenario.termination_mode:
        violations.append("provenance termination condition 不一致")
    if result.provenance.horizon != scenario.horizon:
        violations.append("provenance horizon 不一致")
    scenario_config = asdict(scenario)
    for field, expected in scenario_config.items():
        if (
            field not in result.provenance.simulation_config
            or result.provenance.simulation_config[field] != expected
        ):
            violations.append(f"provenance Scenario.{field} 不一致")

    if scenario.release_templates:
        expected_release_runtime = {
            "schema_version": RELEASE_RUNTIME_SCHEMA_VERSION,
            "id": RELEASE_RUNTIME_ID,
            "randomness": "none_for_constant_interval_profile",
            "supported_boundary": {
                "termination_mode": "fixed_horizon",
                "interval_kind": "constant",
                "interval_unit": "normalized_minutes",
                "lots_per_repeat": 1,
                "lazy_occurrence_materialization": True,
                "horizon_is_closed": True,
            },
        }
        if (
            result.provenance.simulation_config.get("release_runtime")
            != expected_release_runtime
        ):
            violations.append("provenance release runtime boundary 不一致")

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

    templates = {
        template.template_id: template
        for template in scenario.release_templates
    }
    release_records_by_template: dict[str, list[TraceRecord]] = {
        template_id: [] for template_id in templates
    }
    for record in result.trace:
        if record.event_type != "LOT_RELEASE" or record.release_template_id is None:
            continue
        template = templates.get(record.release_template_id)
        if template is None:
            violations.append(
                f"release trace 引用了未知 template：{record.release_template_id}"
            )
            continue
        release_records_by_template[template.template_id].append(record)
        repeat_index = record.release_repeat_index
        member_index = record.release_member_index
        if repeat_index is None or member_index is None:
            violations.append(f"{record.lot_id} 缺少 release repeat/member identity")
            continue
        expected_id = release_lot_id(
            template.template_id,
            template.lot_prefix,
            repeat_index,
            member_index,
        )
        if record.lot_id != expected_id:
            violations.append(
                f"release lot ID 不一致：{record.lot_id} != {expected_id}"
            )
        expected_time = release_time(template, repeat_index)
        if abs(record.sim_time - expected_time) > tolerance:
            violations.append(
                f"{record.lot_id} release time 不一致："
                f"{record.sim_time} != {expected_time}"
            )
        expected_due = (
            expected_time + template.relative_due_minutes
            if template.relative_due_minutes is not None
            else None
        )
        if expected_due is None:
            if record.lot_due_time is not None:
                violations.append(f"{record.lot_id} 不应有 due time")
        elif (
            record.lot_due_time is None
            or abs(record.lot_due_time - expected_due) > tolerance
        ):
            violations.append(f"{record.lot_id} due offset 不一致")
        metadata_pairs = {
            "product_id": (record.product_id, template.product_id),
            "order_id": (record.order_id, template.order_id),
            "hot_lot": (record.hot_lot, template.hot_lot),
            "source_row": (record.source_row, template.source_row),
            "priority": (record.lot_priority, template.priority),
            "quantity_wafers": (
                record.lot_quantity_wafers,
                template.quantity_wafers,
            ),
        }
        for field, (actual, expected) in metadata_pairs.items():
            if actual != expected:
                violations.append(
                    f"{record.lot_id} release {field} 不一致："
                    f"{actual!r} != {expected!r}"
                )

    if templates:
        horizon = scenario.horizon
        if horizon is None:
            violations.append("release template scenario 缺少 fixed horizon")
        else:
            for template_id, template in templates.items():
                if horizon + tolerance < template.first_release_time:
                    expected_count = 0
                else:
                    expected_count = min(
                        template.repeat_limit,
                        floor(
                            (
                                horizon
                                - template.first_release_time
                                + tolerance
                            )
                            / template.interval.mean_minutes
                        )
                        + 1,
                    )
                records = release_records_by_template[template_id]
                actual_indices = sorted(
                    record.release_repeat_index
                    for record in records
                    if record.release_repeat_index is not None
                )
                if actual_indices != list(range(expected_count)):
                    violations.append(
                        f"{template_id} release occurrence 不完整："
                        f"actual={actual_indices}, expected_count={expected_count}"
                    )

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
        for source in scenario.lots + scenario.release_templates
        for operation in source.operations
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

    transport_missing: dict[tuple[str, str], int] = {}
    for interval in result.transport_intervals:
        if interval.finish < interval.start:
            violations.append(f"{interval.lot_id} transport finish 早于 start")
        if abs((interval.finish - interval.start) - interval.duration) > tolerance:
            violations.append(f"{interval.lot_id} transport duration 不一致")
        if interval.finish > result.metrics.end_time + tolerance:
            violations.append(f"{interval.lot_id} transport 超出仿真终点")
        if interval.missing_pair:
            if abs(interval.duration) > tolerance:
                violations.append(f"{interval.lot_id} missing transport 非零时长")
            key = (interval.from_location, interval.to_location)
            transport_missing[key] = transport_missing.get(key, 0) + 1

    active_transport_lots = [item.lot_id for item in result.active_transports]
    if len(active_transport_lots) != len(set(active_transport_lots)):
        violations.append("active transport lot_id 重复")
    for snapshot in result.active_transports:
        expected_remaining = max(
            0.0,
            snapshot.scheduled_finish - result.metrics.end_time,
        )
        if snapshot.start > result.metrics.end_time + tolerance:
            violations.append(f"{snapshot.lot_id} active transport 尚未开始")
        if snapshot.scheduled_finish <= result.metrics.end_time - tolerance:
            violations.append(f"{snapshot.lot_id} active transport 已应完成")
        if abs(snapshot.remaining_duration - expected_remaining) > tolerance:
            violations.append(
                f"{snapshot.lot_id} active transport remaining 不一致"
            )

    transport_started = len(result.transport_intervals) + len(result.active_transports)
    expected_transport_metrics = {
        "started_count": transport_started,
        "completed_count": len(result.transport_intervals),
        "active_count": len(result.active_transports),
        "missing_pair_count": sum(transport_missing.values()),
    }
    for name, expected in expected_transport_metrics.items():
        reported = getattr(result.transport_metrics, name)
        if reported != expected:
            violations.append(
                f"transport {name} 不一致：reported={reported}, records={expected}"
            )
    expected_transport_minutes = sum(
        interval.duration for interval in result.transport_intervals
    )
    if abs(
        result.transport_metrics.total_minutes - expected_transport_minutes
    ) > tolerance:
        violations.append("transport total_minutes 不一致")
    expected_missing_pairs = tuple(
        (from_location, to_location, count)
        for (from_location, to_location), count in sorted(transport_missing.items())
    )
    if result.transport_metrics.missing_pairs != expected_missing_pairs:
        violations.append("transport missing_pairs 不一致")
    transport_samples = sum(
        record.stream_name == "transport"
        for record in result.random_sample_ledger
    )
    transport_distribution_by_pair = {
        (item.from_location, item.to_location): item.duration.kind
        for item in scenario.transport_specs
    }
    stochastic_transport_starts = sum(
        transport_distribution_by_pair.get(
            (item.from_location, item.to_location)
        ) not in {None, "constant"}
        for item in (*result.transport_intervals, *result.active_transports)
    )
    if transport_samples != stochastic_transport_starts:
        violations.append(
            "transport 随机记录与 RandomSampleLedger 不一致"
        )

    trace_transport_started = sum(
        record.event_type in {"TRANSPORT_START", "TRANSPORT_MISSING"}
        for record in result.trace
    )
    trace_transport_completed = sum(
        record.event_type in {"TRANSPORT_ARRIVE", "TRANSPORT_MISSING"}
        for record in result.trace
    )
    if trace_transport_started != result.transport_metrics.started_count:
        violations.append("transport started_count 与 trace 不一致")
    if trace_transport_completed != result.transport_metrics.completed_count:
        violations.append("transport completed_count 与 trace 不一致")

    return ResultInvariantAudit(tuple(violations))
