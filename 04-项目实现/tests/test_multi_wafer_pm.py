"""同一 machine 上多条 wafer PM 的合成验收测试。

本文件只固定多 Wafer PM 的 E 级运行时边界：每条 PM 按 ``pm_id`` 维护
独立状态；同刻到期稳定排序；machine 仍只有一个 downtime owner。测试不
修改原始数据，也不把同机多 PM 扩展解释成 SMT2020 raw 已闭合。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (  # noqa: E402
    BatchSpec,
    CalendarPMSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
    TimeDistributionSpec,
    WaferPMSpec,
)
from fab_scheduler.evaluation.audit import audit_result_invariants  # noqa: E402
from fab_scheduler.simulation.engine import Simulator  # noqa: E402
from fab_scheduler.simulation.events import EVENT_PRIORITIES, EventType  # noqa: E402
from fab_scheduler.simulation.pm import PMRuntime  # noqa: E402
from fab_scheduler.simulation.random_streams import EntityRandomStreams  # noqa: E402


def _operation(duration: float = 1, *, machine: str = "M1") -> OperationSpec:
    return OperationSpec(1, duration, (machine,), route_id="MULTI-WAFER-PM")


def _wafer_pm(
    pm_id: str,
    *,
    threshold: int,
    duration: float | TimeDistributionSpec,
    initial: int = 0,
    machine: str = "M1",
) -> WaferPMSpec:
    duration_spec = (
        duration
        if isinstance(duration, TimeDistributionSpec)
        else TimeDistributionSpec("constant", duration)
    )
    return WaferPMSpec(pm_id, machine, threshold, duration_spec, initial)


def _failure(
    machine: str,
    *occurrences: tuple[float, float],
) -> MachineFailureSpec:
    return MachineFailureSpec(
        machine,
        "scripted",
        scripted_failures=tuple(
            ScriptedFailureSpec(start, duration)
            for start, duration in occurrences
        ),
    )


def _calendar_pm(
    pm_id: str,
    start: float,
    duration: float,
    *,
    machine: str = "M1",
) -> CalendarPMSpec:
    return CalendarPMSpec(
        pm_id,
        machine,
        "scripted",
        scripted_occurrences=(ScriptedPMSpec(start, duration),),
    )


def _scenario(
    *,
    wafer: tuple[WaferPMSpec, ...],
    lots: tuple[LotSpec, ...],
    failures: tuple[MachineFailureSpec, ...] = (),
    calendar: tuple[CalendarPMSpec, ...] = (),
    horizon: float | None = None,
    machines: tuple[MachineSpec, ...] = (MachineSpec("M1"),),
) -> Scenario:
    return Scenario(
        "SYNTH_MULTI_WAFER_PM",
        "synthetic@multi-wafer-pm-0.1.0",
        machines,
        lots,
        termination_mode="fixed_horizon" if horizon is not None else "until_all_complete",
        horizon=horizon,
        failure_specs=failures,
        calendar_pm_specs=calendar,
        wafer_pm_specs=wafer,
    )


def _wafer_records(result, event_type: str):
    return [
        record
        for record in result.trace
        if record.event_type == event_type
        and record.pm_trigger_type == "WAFER_PM"
    ]


class MultiWaferPMAcceptanceTests(unittest.TestCase):
    def test_mc08_machine_readable_multi_wafer_variant(self) -> None:
        """MC08 扩展变体必须由 runtime 执行，而非仅留在 JSON 中。"""
        fixture_path = Path(__file__).parent / "fixtures" / "micro_cases" / "cases.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        case = next(item for item in fixture["cases"] if item["id"] == "MC08_TERMINAL_EXPOSURE")
        variant = next(item for item in case["pm_variants"] if item["id"] == "MULTI_WAFER_SAME_MACHINE")
        scenario = _scenario(
            wafer=tuple(
                _wafer_pm(
                    item["pm_id"],
                    threshold=item["threshold_wafers"],
                    initial=item["initial_counter_wafers"],
                    duration=item["duration"],
                )
                for item in variant["wafer_pms"]
            ),
            lots=tuple(
                LotSpec(
                    item["id"],
                    item["release"],
                    (_operation(item["process"]),),
                    quantity_wafers=item["quantity_wafers"],
                )
                for item in variant["lots"]
            ),
            horizon=variant["horizon"],
        )
        result = Simulator(scenario, seed=42).run()
        audit = audit_result_invariants(result, scenario)
        self.assertTrue(audit.passed, audit.violations)
        expected = variant["expected"]
        self.assertEqual(
            [item.pm_id for item in _wafer_records(result, "PM_DUE")],
            expected["due_order"],
        )
        self.assertEqual(
            [
                {"pm_id": item.pm_id, "start": item.start, "finish": item.finish}
                for item in result.pm_intervals
            ],
            expected["owner_intervals"],
        )
        self.assertEqual(result.machine_statistics["M1"].wafer_counter, expected["machine_wafer_counter"])
        self.assertEqual(expected["authoritative_state"], "per_pm_snapshot")
        self.assertEqual(len(result.wafer_pm_states), len(variant["wafer_pms"]))
        self.assertEqual(
            result.provenance.simulation_config["wafer_pm_combination_rule"],
            {
                "state_key": "machine_id+pm_id",
                "simultaneous_due_order": "pm_id_ascending",
                "downtime_owner": "one_per_machine_sequential",
                "counter_reset": "zero_without_carry",
                "multi_spec_machine_counter": "none_use_per_pm_snapshots",
                "evidence_level": "E_local_modeling_rule",
            },
        )

    def test_until_all_complete_keeps_final_due_pending_at_lot_completion(self) -> None:
        """until_all_complete 不为尾部 lot 触发的 PM 偷加观察时间。"""
        result = Simulator(
            _scenario(
                wafer=(
                    _wafer_pm("PM-A", threshold=25, duration=2),
                    _wafer_pm("PM-B", threshold=25, duration=2),
                ),
                lots=(LotSpec("L1", 0, (_operation(),), quantity_wafers=25),),
            ),
            seed=100,
        ).run()

        self.assertEqual(result.completion_times, {"L1": 1})
        self.assertEqual(result.pm_count, 0)
        self.assertEqual(
            [(record.pm_id, record.sim_time) for record in _wafer_records(result, "PM_DUE")],
            [("PM-A", 1), ("PM-B", 1)],
        )
        self.assertEqual(
            {
                state.pm_id: (state.pending, state.active, state.occurrence_count)
                for state in result.wafer_pm_states
            },
            {
                "PM-A": (True, False, 0),
                "PM-B": (True, False, 0),
            },
        )

    def test_three_counters_are_independent_and_one_reset_does_not_reset_others(self) -> None:
        """三条 PM 的计数、到期次数和 reset_zero 生命周期彼此独立。"""
        result = Simulator(
            _scenario(
                wafer=(
                    _wafer_pm("PM-A", threshold=50, duration=1),
                    _wafer_pm("PM-B", threshold=75, duration=1),
                    _wafer_pm("PM-C", threshold=100, duration=1),
                ),
                lots=tuple(
                    LotSpec(
                        f"L{index}",
                        0,
                        (_operation(),),
                        quantity_wafers=25,
                    )
                    for index in range(1, 4)
                ),
                horizon=20,
            ),
            seed=101,
        ).run()

        due = _wafer_records(result, "PM_DUE")
        self.assertEqual(
            [(record.pm_id, record.wafer_counter_before, record.wafer_counter_after)
             for record in due],
            [("PM-A", 25, 50), ("PM-B", 50, 75)],
        )
        self.assertEqual(
            {
                state.pm_id: (
                    state.counter_wafers,
                    state.pending,
                    state.active,
                    state.occurrence_count,
                )
                for state in result.wafer_pm_states
            },
            {
                "PM-A": (25, False, False, 1),
                "PM-B": (0, False, False, 1),
                "PM-C": (75, False, False, 0),
            },
        )

    def test_successful_batch_counts_total_wafers_once_for_every_wafer_pm(self) -> None:
        """Batch 完成按 total_wafers 对每条 PM 只累计一次，不按成员 lot 重复。"""
        batch = BatchSpec(50, 50, 50, 0)
        # The batch declaration belongs to the operation, so reconstruct the
        # two member lots with the same physical batch contract.
        lots = tuple(
            LotSpec(
                f"L{index}",
                0,
                (OperationSpec(1, 2, ("M1",), route_id="MULTI-WAFER-PM", batch_spec=batch),),
                quantity_wafers=25,
            )
            for index in range(1, 3)
        )
        result = Simulator(
            _scenario(
                wafer=(
                    _wafer_pm("PM-A", threshold=50, duration=2),
                    _wafer_pm("PM-B", threshold=50, duration=2),
                ),
                lots=lots,
                horizon=20,
            ),
            seed=108,
        ).run()

        due = _wafer_records(result, "PM_DUE")
        self.assertEqual(
            [(record.pm_id, record.processed_wafers) for record in due],
            [("PM-A", 50), ("PM-B", 50)],
        )
        self.assertEqual(
            {state.pm_id: state.occurrence_count for state in result.wafer_pm_states},
            {"PM-A": 1, "PM-B": 1},
        )
        self.assertEqual(sum(record.event_type == "PM_DUE" for record in result.trace), 2)

    def test_non_cascade_load_process_unload_counts_all_pms_once_after_real_finish(self) -> None:
        """LOAD/CORE/UNLOAD 完成后才同时累计 PM，Failure 中断不重复累计。"""
        result = Simulator(
            _scenario(
                machines=(MachineSpec("M1", load_minutes=1, unload_minutes=1),),
                wafer=(
                    _wafer_pm("PM-A", threshold=25, duration=2),
                    _wafer_pm("PM-B", threshold=25, duration=2),
                ),
                calendar=(_calendar_pm("PM-CALENDAR", 0.5, 1),),
                lots=(
                    LotSpec(
                        "L1",
                        0,
                        (_operation(4),),
                        quantity_wafers=25,
                    ),
                ),
                failures=(_failure("M1", (4, 1)),),
                horizon=20,
            ),
            seed=109,
        ).run()

        process_finishes = [
            record for record in result.trace if record.event_type == "PROCESS_FINISH"
        ]
        self.assertEqual(len(process_finishes), 1)
        self.assertEqual(
            sum(record.event_type == "PROCESS_CORE_FINISH" for record in result.trace),
            1,
        )
        self.assertIn(
            "PROCESS_CORE_FINISH_STALE",
            {record.event_type for record in result.trace},
        )
        self.assertIn(
            "LOAD_FINISH_STALE",
            {record.event_type for record in result.trace},
        )
        canonical_finish = process_finishes[0].sim_time
        due = _wafer_records(result, "PM_DUE")
        self.assertEqual([record.sim_time for record in due], [canonical_finish, canonical_finish])
        self.assertEqual(
            [(record.pm_id, record.processed_wafers) for record in due],
            [("PM-A", 25), ("PM-B", 25)],
        )
        self.assertEqual(
            {state.pm_id: state.occurrence_count for state in result.wafer_pm_states},
            {"PM-A": 1, "PM-B": 1},
        )
        self.assertEqual(result.pm_count, 3)
        self.assertEqual(sum(record.event_type == "PM_DUE" for record in result.trace), 2)

    def test_simultaneous_due_is_sorted_by_pm_id_and_input_order_independent(self) -> None:
        """同刻三条 PM 按 pm_id 到期和取得 owner，不依赖 spec 输入顺序。"""
        specs = tuple(
            _wafer_pm(pm_id, threshold=25, duration=1)
            for pm_id in ("PM-C", "PM-A", "PM-B")
        )
        reverse_specs = tuple(reversed(specs))
        lots = (LotSpec("L1", 0, (_operation(),), quantity_wafers=25),)

        results = [
            Simulator(_scenario(wafer=items, lots=lots, horizon=20), seed=102).run()
            for items in (specs, reverse_specs)
        ]
        for result in results:
            self.assertIsNone(result.machine_statistics["M1"].wafer_counter)
            self.assertEqual(
                [record.pm_id for record in _wafer_records(result, "PM_DUE")],
                ["PM-A", "PM-B", "PM-C"],
            )
            self.assertEqual(
                [record.pm_id for record in _wafer_records(result, "PM_START")],
                ["PM-A", "PM-B", "PM-C"],
            )
            self.assertEqual(
                [
                    (interval.pm_id, interval.start, interval.finish)
                    for interval in result.pm_intervals
                    if interval.trigger_type == "WAFER_PM"
                ],
                [("PM-A", 1, 2), ("PM-B", 2, 3), ("PM-C", 3, 4)],
            )

        projected_trace = lambda result: [
            (
                record.event_type,
                record.sim_time,
                record.pm_id,
                record.pm_occurrence_index,
            )
            for record in result.trace
            if record.pm_trigger_type == "WAFER_PM"
        ]
        self.assertEqual(projected_trace(results[0]), projected_trace(results[1]))

    def test_same_machine_wafer_pm_intervals_have_one_sequential_owner(self) -> None:
        """同一 machine 不得并行执行两个 wafer PM。"""
        result = Simulator(
            _scenario(
                wafer=(
                    _wafer_pm("PM-A", threshold=25, duration=2),
                    _wafer_pm("PM-B", threshold=25, duration=2),
                ),
                lots=(LotSpec("L1", 0, (_operation(),), quantity_wafers=25),),
                horizon=20,
            ),
            seed=103,
        ).run()

        intervals = [
            item for item in result.pm_intervals if item.trigger_type == "WAFER_PM"
        ]
        self.assertEqual(
            [(item.pm_id, item.start, item.finish) for item in intervals],
            [("PM-A", 1, 3), ("PM-B", 3, 5)],
        )
        for previous, current in zip(intervals, intervals[1:]):
            self.assertLessEqual(previous.finish, current.start)
        self.assertEqual(result.pm_count, 2)

    def test_failure_wins_same_time_and_all_due_pms_remain_pending(self) -> None:
        """Failure 同刻取得 machine owner，多个 pending wafer PM 不能丢失。"""
        result = Simulator(
            _scenario(
                wafer=tuple(
                    _wafer_pm(pm_id, threshold=25, duration=2)
                    for pm_id in ("PM-A", "PM-B", "PM-C")
                ),
                lots=(LotSpec("L1", 0, (_operation(2),), quantity_wafers=25),),
                failures=(_failure("M1", (2, 10)),),
                horizon=4,
            ),
            seed=104,
        ).run()

        at_failure_time = [
            record.event_type
            for record in result.trace
            if record.sim_time == 2
            and record.event_type in {"FAILURE_START", "PM_START_DEFERRED"}
        ]
        self.assertEqual(at_failure_time[0], "FAILURE_START")
        self.assertLess(
            EVENT_PRIORITIES[EventType.FAILURE_START],
            EVENT_PRIORITIES[EventType.PM_START],
        )
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.pm_count, 0)
        self.assertEqual(
            {
                state.pm_id: (
                    state.counter_wafers,
                    state.pending,
                    state.active,
                    state.occurrence_count,
                )
                for state in result.wafer_pm_states
            },
            {
                "PM-A": (25, True, False, 0),
                "PM-B": (25, True, False, 0),
                "PM-C": (25, True, False, 0),
            },
        )

    def test_fixed_horizon_exposes_active_and_pending_state_per_pm(self) -> None:
        """fixed horizon 截断时保留每条 PM 的 active/pending 快照。"""
        result = Simulator(
            _scenario(
                wafer=(
                    _wafer_pm("PM-A", threshold=25, duration=5),
                    _wafer_pm("PM-B", threshold=25, duration=5),
                ),
                lots=(LotSpec("L1", 0, (_operation(),), quantity_wafers=25),),
                horizon=2,
            ),
            seed=105,
        ).run()

        self.assertEqual(result.metrics.end_time, 2)
        self.assertEqual(
            [state.pm_id for state in result.wafer_pm_states],
            ["PM-A", "PM-B"],
        )
        states = {state.pm_id: state for state in result.wafer_pm_states}
        self.assertEqual(
            (states["PM-A"].counter_wafers, states["PM-A"].pending,
             states["PM-A"].active, states["PM-A"].occurrence_count),
            (25, False, True, 0),
        )
        self.assertEqual(
            (states["PM-B"].counter_wafers, states["PM-B"].pending,
             states["PM-B"].active, states["PM-B"].occurrence_count),
            (25, True, False, 0),
        )
        self.assertEqual(result.pm_count, 1)
        self.assertIsNone(result.machine_statistics["M1"].wafer_counter)
        self.assertTrue(result.machine_statistics["M1"].wafer_pm_pending)

    def test_wafer_pm_crn_identity_uses_pm_id_and_occurrence_index(self) -> None:
        """增加另一 PM 或改变输入顺序，不得改写目标 PM 的 CRN 样本。"""
        target = _wafer_pm(
            "PM-TARGET",
            threshold=25,
            duration=TimeDistributionSpec("uniform", 5, 2),
        )
        extra = _wafer_pm(
            "PM-EXTRA",
            threshold=100,
            duration=TimeDistributionSpec("uniform", 3, 1),
        )

        def sample_target(
            runtime: PMRuntime,
            streams: EntityRandomStreams,
        ) -> tuple[float, float, set[tuple[str, str, int]]]:
            first = next(
                due
                for due in runtime.account_completed_wafers("M1", 25)
                if due.pm_id == "PM-TARGET"
            )
            runtime.start_wafer_pm("M1", "PM-TARGET", first.occurrence_index)
            runtime.finish_wafer_pm("M1", "PM-TARGET", first.occurrence_index)
            second = next(
                due
                for due in runtime.account_completed_wafers("M1", 25)
                if due.pm_id == "PM-TARGET"
            )
            identities = {
                item.identity
                for item in streams.ledger
                if item.entity_id == "PM-TARGET"
            }
            return first.duration, second.duration, identities

        isolated_streams = EntityRandomStreams(106)
        forward_streams = EntityRandomStreams(106)
        reverse_streams = EntityRandomStreams(106)
        isolated = sample_target(
            PMRuntime((), (target,), isolated_streams), isolated_streams
        )
        forward = sample_target(
            PMRuntime((), (target, extra), forward_streams), forward_streams
        )
        reverse = sample_target(
            PMRuntime((), (extra, target), reverse_streams), reverse_streams
        )

        self.assertEqual(isolated, forward)
        self.assertEqual(isolated, reverse)
        self.assertEqual(
            isolated[2],
            {
                ("pm_duration", "PM-TARGET", 0),
                ("pm_duration", "PM-TARGET", 1),
            },
        )

    def test_due_records_conserve_counter_and_processed_wafers(self) -> None:
        """每条 PM 的 due 记录都满足计数守恒，不重复累计 interrupted/stale finish。"""
        result = Simulator(
            _scenario(
                wafer=(
                    _wafer_pm("PM-A", threshold=25, duration=1),
                    _wafer_pm("PM-B", threshold=50, duration=1),
                ),
                lots=tuple(
                    LotSpec(
                        f"L{index}",
                        0,
                        (_operation(2),),
                        quantity_wafers=25,
                    )
                    for index in range(1, 4)
                ),
                horizon=30,
            ),
            seed=107,
        ).run()

        for record in _wafer_records(result, "PM_DUE"):
            self.assertEqual(
                record.wafer_counter_before + record.processed_wafers,
                record.wafer_counter_after,
            )
            self.assertGreaterEqual(
                record.wafer_counter_after,
                record.wafer_threshold,
            )
        self.assertEqual(
            {
                state.pm_id: state.occurrence_count
                for state in result.wafer_pm_states
            },
            {"PM-A": 3, "PM-B": 1},
        )


if __name__ == "__main__":
    unittest.main()
