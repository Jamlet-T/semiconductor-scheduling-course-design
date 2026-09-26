"""从结果日志独立重算基础指标并检查 M1 资源不变量。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from math import floor, isfinite
import random
from statistics import fmean
from typing import Any, Iterable, Mapping

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


_SAMPLING_STREAM = "sampling"
_SAMPLING_RUNTIME_SCHEMA_VERSION = "0.1.0"
_SAMPLING_RUNTIME_ID = "per_lot_sampling"


def _sampling_operation_key(record: TraceRecord) -> tuple[Any, ...]:
    """返回 sampling/operation 事件共用的稳定键。

    Sampling 的实体 identity 把 visit 放在 occurrence 中，而 trace 事件本身
    仍需要保留 visit 维度。这里集中构造键，避免审计逻辑因事件类型而漂移。
    """

    return (
        record.lot_id,
        record.route_id,
        record.step_id,
        record.visit_index,
    )


def _expected_sampling_entity_id(record: TraceRecord) -> str | None:
    if (
        record.lot_id is None
        or record.route_id is None
        or record.step_id is None
        or record.visit_index is None
    ):
        return None
    return (
        f"{record.lot_id}|{record.route_id}|{record.step_id}|"
        f"visit={record.visit_index}"
    )


def _audit_sampling_invariants(
    result: SimulationResult,
    scenario: Scenario,
    *,
    tolerance: float,
) -> list[str]:
    """独立核对 StepPercent sampling 的决定、事件和随机账本。

    该检查器只消费结果中的 trace、ledger 和 provenance，不调用 sampling
    runtime，因此可以发现 runtime 自己同时篡改决定和派生记录的情况。
    """

    violations: list[str] = []
    trace = tuple(result.trace)
    decisions = [
        record for record in trace if record.event_type == "SAMPLING_DECISION"
    ]
    skipped = [
        record for record in trace if record.event_type == "OPERATION_SKIPPED"
    ]
    sampling_ledger = [
        record
        for record in result.random_sample_ledger
        if record.stream_name == _SAMPLING_STREAM
    ]
    ledger_by_key: dict[tuple[str, str, int], list[Any]] = {}
    for item in sampling_ledger:
        ledger_by_key.setdefault(item.identity, []).append(item)
    lots_by_id = {lot.lot_id: lot for lot in scenario.lots}
    templates_by_id = {
        template.template_id: template for template in scenario.release_templates
    }
    for release in trace:
        if release.event_type != "LOT_RELEASE" or release.lot_id in lots_by_id:
            continue
        template = templates_by_id.get(release.release_template_id)
        if template is not None:
            lots_by_id[release.lot_id] = template

    operations_by_spec: dict[int, dict[tuple[str, int], Any]] = {}

    def scenario_operation(record: TraceRecord):
        lot_spec = lots_by_id.get(record.lot_id)
        if lot_spec is None:
            return None
        operations_identity = id(lot_spec.operations)
        lookup = operations_by_spec.get(operations_identity)
        if lookup is None:
            lookup = {
                (item.route_id, item.step_id): item
                for item in lot_spec.operations
            }
            operations_by_spec[operations_identity] = lookup
        return lookup.get((record.route_id, record.step_id))

    # A sampling decision must be present exactly once for each sampled
    # operation instance. Duplicates are otherwise easy to hide by matching
    # only on (lot, step).
    decisions_by_key: dict[tuple[Any, ...], list[TraceRecord]] = {}
    for record in decisions:
        decisions_by_key.setdefault(_sampling_operation_key(record), []).append(
            record
        )
    for key, records in decisions_by_key.items():
        if len(records) != 1:
            violations.append(f"sampling decision identity 重复：{key!r}")

    # Validate every decision and its ledger entry. p=100 is deliberately a
    # deterministic path and must not materialize a random sample.
    expected_ledger_keys: set[tuple[str, str, int]] = set()
    for record in decisions:
        key = _sampling_operation_key(record)
        percent = record.sampling_percent
        draw = record.sampling_draw
        performed = record.sampling_performed
        entity_id = record.sampling_entity_id
        expected_entity_id = _expected_sampling_entity_id(record)
        operation = scenario_operation(record)
        if operation is None or operation.sample_percent is None:
            violations.append(f"sampling decision 无对应 Scenario 配置：{key!r}")
        elif percent != operation.sample_percent:
            violations.append(
                f"sampling percent 与 Scenario 配置不一致：{key!r}"
            )

        try:
            percent_value = float(percent)
        except (TypeError, ValueError):
            percent_value = float("nan")
        if percent is None or isinstance(percent, bool) or not isfinite(percent_value):
            violations.append(f"sampling percent 非法：{percent!r}")
            continue
        percent = percent_value
        if not 0 < percent <= 100:
            violations.append(f"sampling percent 超出边界：{percent!r}")
        if entity_id != expected_entity_id:
            violations.append(
                f"sampling entity identity 不一致：{entity_id!r} != "
                f"{expected_entity_id!r}"
            )
        if performed not in (True, False):
            violations.append(f"sampling performed 非布尔值：{performed!r}")

        ledger_key = None
        if entity_id is not None and record.visit_index is not None:
            ledger_key = (_SAMPLING_STREAM, entity_id, record.visit_index)
            expected_ledger_keys.add(ledger_key)
        matching_ledger = ledger_by_key.get(ledger_key, ())
        if len(matching_ledger) > 1:
            violations.append(f"sampling ledger identity 重复：{ledger_key!r}")

        if percent == 100:
            if draw is not None:
                violations.append("sampling p=100 不应记录 draw")
            if performed is not True:
                violations.append("sampling p=100 必须 performed=True")
            if matching_ledger:
                violations.append("sampling p=100 不应产生 sampling ledger")
            continue

        try:
            draw_value = float(draw)
        except (TypeError, ValueError):
            draw_value = float("nan")
        if draw is None or isinstance(draw, bool) or not isfinite(draw_value):
            violations.append("sampling stochastic decision 缺少合法 draw")
        else:
            draw = draw_value
            if not 0 <= draw <= 100:
                violations.append(f"sampling draw 超出 [0,100]：{draw!r}")
            expected_performed = draw <= percent
            if performed is not expected_performed:
                violations.append(
                    f"sampling performed 与 draw<=percent 不一致："
                    f"{performed!r} != {expected_performed!r}"
                )
        if len(matching_ledger) != 1:
            violations.append(
                f"sampling stochastic decision ledger 数量不为 1：{ledger_key!r}"
            )
        elif (
            draw is not None
            and isfinite(matching_ledger[0].value)
            and abs(matching_ledger[0].value - draw) > tolerance
        ):
            violations.append("sampling draw 与 RandomSampleLedger value 不一致")

        if matching_ledger:
            ledger = matching_ledger[0]
            if not isfinite(ledger.value):
                violations.append("sampling ledger value 非有限数")
            if ledger.distribution != "uniform":
                violations.append("sampling ledger 分布不是 uniform")
            if tuple(ledger.parameters) != (0.0, 100.0):
                violations.append("sampling ledger 参数不是 uniform(0,100)")
            if ledger.entity_id != entity_id:
                violations.append("sampling ledger entity identity 不一致")
            if record.visit_index is not None and ledger.occurrence_index != record.visit_index:
                violations.append("sampling ledger occurrence 不等于 visit_index")
            if entity_id is not None and record.visit_index is not None:
                material = (
                    f"{result.seed}\0{_SAMPLING_STREAM}\0{entity_id}\0"
                    f"{record.visit_index}"
                ).encode()
                expected_seed = int.from_bytes(
                    hashlib.sha256(material).digest()[:16], "big"
                )
                if ledger.derived_seed != expected_seed:
                    violations.append("sampling ledger derived seed 不一致")
                expected_value = random.Random(expected_seed).uniform(0.0, 100.0)
                if isfinite(ledger.value) and abs(ledger.value - expected_value) > tolerance:
                    violations.append("sampling ledger value 与派生随机流不一致")

    # Any sampling ledger must be justified by a stochastic decision. This
    # catches both an extra random draw and a ledger entry moved to p=100.
    actual_ledger_keys = [item.identity for item in sampling_ledger]
    if len(actual_ledger_keys) != len(set(actual_ledger_keys)):
        violations.append("sampling RandomSampleLedger identity 重复")
    for identity in actual_ledger_keys:
        if identity not in expected_ledger_keys:
            violations.append(f"sampling ledger 缺少对应 decision：{identity!r}")

    # Entry events are an independent runtime witness.  A runtime that
    # silently ignores sample_percent can still produce a plausible process
    # trace, so every sampled LOT_RELEASE/ROUTE_ADVANCE identity must have
    # exactly one decision.  Multiple entry records for one identity (for
    # example transport-arrival and direct route advance variants) are
    # intentionally deduplicated here.
    entry_identities = {
        _sampling_operation_key(record)
        for record in trace
        if record.event_type in {"LOT_RELEASE", "ROUTE_ADVANCE"}
        and (
            (operation := scenario_operation(record)) is not None
            and operation.sample_percent is not None
        )
    }
    for identity in entry_identities:
        matching_decisions = decisions_by_key.get(identity, ())
        if len(matching_decisions) != 1:
            violations.append(
                f"sampling operation entry 缺少唯一 decision："
                f"{identity!r} ({len(matching_decisions)})"
            )

    # Route witnesses also expose omissions at an intermediate sampled step:
    # a direct step 1 -> step 3 advance proves that step 2 was visited, even
    # though a skipped step never emits its own ROUTE_ADVANCE.
    witness_events = {
        "LOT_RELEASE", "ROUTE_ADVANCE", "PROCESS_START",
        "SAMPLING_DECISION", "OPERATION_SKIPPED", "TRANSPORT_START",
        "TRANSPORT_MISSING", "LOT_COMPLETE",
    }
    trace_by_lot: dict[str, list[TraceRecord]] = {}
    for record in trace:
        if record.lot_id is not None and record.event_type in witness_events:
            trace_by_lot.setdefault(record.lot_id, []).append(record)
    indices_by_operations: dict[int, dict[tuple[str, int], int]] = {}
    for lot_id, records in trace_by_lot.items():
        if not any(record.event_type == "LOT_RELEASE" for record in records):
            continue
        lot_spec = lots_by_id.get(lot_id)
        if lot_spec is None:
            continue
        operations = lot_spec.operations
        index_by_key = indices_by_operations.get(id(operations))
        if index_by_key is None:
            index_by_key = {
                (operation.route_id, operation.step_id): index
                for index, operation in enumerate(operations)
            }
            indices_by_operations[id(operations)] = index_by_key
        reached = max(
            (
                index_by_key[(record.route_id, record.step_id)]
                for record in records
                if (record.route_id, record.step_id) in index_by_key
            ),
            default=0,
        )
        if any(record.event_type == "LOT_COMPLETE" for record in records):
            reached = len(operations) - 1
        initial = getattr(lot_spec, "initial_operation_index", 0)
        for operation in operations[initial : reached + 1]:
            if operation.sample_percent is None:
                continue
            key = (lot_id, operation.route_id, operation.step_id, 0)
            if key not in decisions_by_key:
                violations.append(
                    f"sampling route witness 缺少 decision：{key!r}"
                )

    downstream_by_key: dict[tuple[Any, ...], list[TraceRecord]] = {}
    for record in trace:
        if record.event_type in {
            "ROUTE_ADVANCE", "PROCESS_START", "TRANSPORT_START",
            "TRANSPORT_MISSING",
        }:
            downstream_by_key.setdefault(_sampling_operation_key(record), []).append(
                record
            )
    for decision in decisions:
        key = _sampling_operation_key(decision)
        for record in downstream_by_key.get(key, ()):
            if (
                decision.sim_time > record.sim_time + tolerance
                or decision.event_seq >= record.event_seq
            ):
                violations.append(
                    f"sampling decision 晚于 queue/transport/process：{key!r}"
                )

    # A failed decision has one and only one same-time skip. A successful
    # decision has no skip. Reverse matching catches orphan skip records too.
    false_decisions: set[tuple[tuple[Any, ...], float]] = set()
    true_decisions: set[tuple[tuple[Any, ...], float]] = set()
    skipped_by_key: dict[tuple[Any, ...], list[TraceRecord]] = {}
    for record in skipped:
        skipped_by_key.setdefault(_sampling_operation_key(record), []).append(record)
    for record in decisions:
        marker = (_sampling_operation_key(record), record.sim_time)
        if record.sampling_performed is False:
            false_decisions.add(marker)
        elif record.sampling_performed is True:
            true_decisions.add(marker)
    for marker in false_decisions:
        key, sim_time = marker
        matches = [
            record for record in skipped_by_key.get(key, ())
            if abs(record.sim_time - sim_time) <= tolerance
        ]
        if len(matches) != 1:
            violations.append(
                f"false sampling decision 对应 OPERATION_SKIPPED 数量错误："
                f"{key!r}@{sim_time} ({len(matches)})"
            )
        else:
            decision = next(
                record for record in decisions_by_key[key]
                if abs(record.sim_time - sim_time) <= tolerance
            )
            skipped_record = matches[0]
            if (
                skipped_record.sampling_percent != decision.sampling_percent
                or skipped_record.sampling_draw != decision.sampling_draw
                or skipped_record.sampling_performed is not False
                or skipped_record.sampling_entity_id != decision.sampling_entity_id
                or skipped_record.event_seq <= decision.event_seq
            ):
                violations.append(
                    f"OPERATION_SKIPPED payload/order 与 decision 不一致：{key!r}"
                )
    for marker in true_decisions:
        key, sim_time = marker
        if any(
            abs(record.sim_time - sim_time) <= tolerance
            for record in skipped_by_key.get(key, ())
        ):
            violations.append(f"true sampling decision 不得对应 skip：{key!r}@{sim_time}")
    for record in skipped:
        marker = (_sampling_operation_key(record), record.sim_time)
        if marker not in false_decisions:
            violations.append(
                f"OPERATION_SKIPPED 缺少同刻 false sampling decision："
                f"{_sampling_operation_key(record)!r}@{record.sim_time}"
            )

    # A skipped operation is a route transition, never a real process. The
    # operation key includes visit so a future rework visit remains auditable.
    skipped_keys = {_sampling_operation_key(record) for record in skipped}
    for record in trace:
        if record.event_type in {"PROCESS_START", "PROCESS_FINISH"} and (
            _sampling_operation_key(record) in skipped_keys
        ):
            violations.append(
                f"skipped operation 出现 {record.event_type}："
                f"{_sampling_operation_key(record)!r}"
            )
    for interval in result.processing_intervals:
        key = (interval.lot_id, interval.route_id, interval.step_id, 0)
        if key in skipped_keys:
            violations.append(f"skipped operation 出现 processing interval：{key!r}")

    # Provenance is checked only when sampling is in scope. Existing legacy
    # scenarios have no sampling operations and therefore no sampling runtime
    # block to validate.
    sampled_operations = [
        operation
        for source in (*scenario.lots, *scenario.release_templates)
        for operation in source.operations
        if getattr(operation, "sample_percent", None) is not None
    ]
    runtime = result.provenance.simulation_config.get("sampling_runtime")
    if sampled_operations or decisions or sampling_ledger:
        if not isinstance(runtime, Mapping):
            violations.append("provenance sampling runtime 缺失")
        else:
            if runtime.get("schema_version") != _SAMPLING_RUNTIME_SCHEMA_VERSION:
                violations.append("provenance sampling runtime schema 不一致")
            if runtime.get("id") != _SAMPLING_RUNTIME_ID:
                violations.append("provenance sampling runtime id 不一致")
            boundary = runtime.get("supported_boundary")
            if not isinstance(boundary, Mapping):
                violations.append("provenance sampling runtime boundary 缺失")
            else:
                expected_boundary = {
                    "decision_point": "operation_entry_before_dispatch_and_transport",
                    "scope": "per_lot",
                    "explicit_percent_range": "(0,100]",
                    "unconfigured": "always_perform_without_decision",
                    "percent_100": "perform_without_random_draw",
                    "rework_visits": "unsupported_visit_0_only",
                }
                for name, expected in expected_boundary.items():
                    actual = boundary.get(name)
                    if actual != expected:
                        violations.append(
                            f"provenance sampling runtime boundary {name} 不一致"
                        )
            if runtime.get("schema_version") == _SAMPLING_RUNTIME_SCHEMA_VERSION:
                random_streams = result.provenance.simulation_config.get(
                    "sampling_random_streams"
                )
                if not isinstance(random_streams, Mapping):
                    violations.append("provenance sampling random streams 缺失")
                else:
                    expected_streams = {
                        "decision": _SAMPLING_STREAM,
                        "identity": "lot_id+route_id+step_id+visit_index",
                        "draw": "uniform(0,100), draw<=sample_percent",
                    }
                    for name, expected in expected_streams.items():
                        if random_streams.get(name) != expected:
                            violations.append(
                                f"provenance sampling random streams {name} 不一致"
                            )
    return violations


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

    violations.extend(
        _audit_sampling_invariants(result, scenario, tolerance=tolerance)
    )

    return ResultInvariantAudit(tuple(violations))
