"""MC08 Preventive Maintenance 的金标准、边界、组合和确定性测试。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (
    BatchSpec,
    CalendarPMSpec,
    CQTSpec,
    DedicationSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
    SetupTransition,
    TimeDistributionSpec,
    WaferPMSpec,
)
from fab_scheduler.simulation.engine import Simulator
from fab_scheduler.simulation.events import EVENT_PRIORITIES, EventType
from fab_scheduler.simulation.pm import PMRuntime
from fab_scheduler.simulation.random_streams import EntityRandomStreams


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "micro_cases" / "cases.json"


def operation(
    step: int,
    duration: float,
    machines: tuple[str, ...] = ("M1",),
    **kwargs,
) -> OperationSpec:
    return OperationSpec(
        step,
        duration,
        machines,
        route_id=kwargs.pop("route_id", "R"),
        **kwargs,
    )


def calendar_pm(
    machine: str,
    *occurrences: tuple[float, float],
    pm_id: str | None = None,
) -> CalendarPMSpec:
    return CalendarPMSpec(
        pm_id or f"PM-CAL-{machine}",
        machine,
        "scripted",
        tuple(ScriptedPMSpec(*item) for item in occurrences),
    )


def wafer_pm(
    machine: str,
    *,
    threshold: int,
    duration: float,
    initial: int = 0,
    pm_id: str | None = None,
) -> WaferPMSpec:
    return WaferPMSpec(
        pm_id or f"PM-WAFER-{machine}",
        machine,
        threshold,
        TimeDistributionSpec("constant", duration),
        initial,
    )


def failure(machine: str, *occurrences: tuple[float, float]) -> MachineFailureSpec:
    return MachineFailureSpec(
        machine,
        "scripted",
        tuple(ScriptedFailureSpec(*item) for item in occurrences),
    )


def scenario(
    *,
    lots: tuple[LotSpec, ...],
    machines: tuple[MachineSpec, ...] = (MachineSpec("M1"),),
    calendar: tuple[CalendarPMSpec, ...] = (),
    wafer: tuple[WaferPMSpec, ...] = (),
    failures: tuple[MachineFailureSpec, ...] = (),
    horizon: float | None = None,
    **kwargs,
) -> Scenario:
    return Scenario(
        "PM_TEST",
        "micro_cases@0.1.0",
        machines,
        lots,
        termination_mode="fixed_horizon" if horizon is not None else "until_all_complete",
        horizon=horizon,
        calendar_pm_specs=calendar,
        wafer_pm_specs=wafer,
        failure_specs=failures,
        **kwargs,
    )


class PMGoldenAndMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.case = next(item for item in fixture["cases"] if item["id"] == "MC08_TERMINAL_EXPOSURE")

    def test_mc08_calendar_pm_golden_trace_and_manual_timing(self) -> None:
        variant = next(item for item in self.case["pm_variants"] if item["id"] == "CALENDAR_PREEMPT_RESUME")
        result = Simulator(
            scenario(
                lots=(LotSpec("L1", 0, (operation(1, 20),)),),
                calendar=(calendar_pm("M1", (8, 5)),),
                horizon=30,
            ),
            seed=42,
            git_commit="test-commit",
        ).run()
        self.assertEqual(result.key_trace(), variant["expected_trace"])
        self.assertEqual(result.completion_times, {"L1": 25})
        self.assertEqual([(i.start, i.finish) for i in result.processing_intervals], [(0, 8), (13, 25)])
        self.assertEqual([(i.start, i.finish) for i in result.pm_intervals], [(8, 13)])
        self.assertEqual(result.machine_statistics["M1"].processing_time, 20)
        self.assertEqual(result.machine_statistics["M1"].pm_downtime, 5)
        self.assertEqual(result.pm_count, 1)
        self.assertEqual(result.total_downtime, 5)
        self.assertEqual(result.total_failure_downtime, 0)
        self.assertEqual(result.total_pm_downtime, 5)
        self.assertEqual(sum(r.event_type == "PROCESS_FINISH_STALE" for r in result.trace), 1)

    def test_mc08_wafer_pm_golden_trace_counter_and_next_dispatch(self) -> None:
        variant = next(item for item in self.case["pm_variants"] if item["id"] == "WAFER_THRESHOLD")
        result = Simulator(
            scenario(
                lots=(
                    LotSpec("L1", 0, (operation(1, 2),), quantity_wafers=25),
                    LotSpec("L2", 0, (operation(1, 2),), quantity_wafers=25),
                ),
                wafer=(wafer_pm("M1", threshold=125, duration=3, initial=100),),
                horizon=10,
            ),
            seed=42,
            git_commit="test-commit",
        ).run()
        self.assertEqual(result.key_trace(), variant["expected_trace"])
        self.assertEqual(result.completion_times, {"L1": 2, "L2": 7})
        self.assertEqual(result.wafer_pm_states[0].counter_wafers, 25)
        self.assertEqual(result.wafer_pm_states[0].occurrence_count, 1)
        self.assertEqual(result.pm_count, 1)
        self.assertEqual(result.total_pm_downtime, 3)
        config = result.provenance.simulation_config
        self.assertEqual(config["wafer_pm_specs"][0]["threshold_wafers"], 125)
        self.assertEqual(config["wafer_pm_specs"][0]["reset_rule"], "reset_zero")

    def test_mc08_terminal_exposure_and_wip_integral_are_preserved(self) -> None:
        result = Simulator(
            scenario(
                machines=(MachineSpec("M1"), MachineSpec("M2")),
                lots=(
                    LotSpec("L1", 0, (operation(1, 10, ("M1",)),), due_time=15),
                    LotSpec("L2", 0, (operation(1, 100, ("M2",)),), due_time=20),
                ),
                horizon=30,
            )
        ).run()
        expected = self.case["expected"]
        self.assertEqual(result.metrics.completed_lots, expected["completed_lots"])
        self.assertEqual(result.metrics.completion_ratio, expected["completion_ratio"])
        self.assertEqual(result.metrics.mean_cycle_time_completed, expected["mean_cycle_time_completed"])
        self.assertEqual(result.metrics.throughput_lots_per_minute, expected["throughput_lots_per_minute"])
        self.assertEqual(result.metrics.terminal_wip_lots, expected["terminal_wip_lots"])
        self.assertEqual(
            result.metrics.terminal_exposure_to_dict(),
            {
                "cycle_time_coverage": expected["cycle_time_coverage"],
                "remaining_work_minutes": expected["remaining_work_minutes"],
                "lateness_exposure_minutes": expected["lateness_exposure_minutes"],
                "mean_wip": expected["mean_wip"],
            },
        )


class PMRuntimeBoundaryTests(unittest.TestCase):
    def test_calendar_pm_while_idle_blocks_then_dispatches_after_finish(self) -> None:
        result = Simulator(scenario(
            lots=(LotSpec("L1", 1, (operation(1, 2),)),),
            calendar=(calendar_pm("M1", (0, 3)),),
        )).run()
        start = next(r for r in result.trace if r.event_type == "PROCESS_START")
        self.assertEqual(start.sim_time, 3)
        self.assertEqual(result.completion_times, {"L1": 5})

    def test_calendar_pm_preemptively_resumes_setup(self) -> None:
        result = Simulator(scenario(
            machines=(MachineSpec("M1", "A"),),
            lots=(LotSpec("L1", 0, (operation(1, 2, required_setup="B"),)),),
            calendar=(calendar_pm("M1", (4, 3)),),
            setup_transitions=(SetupTransition("A", "B", 10),),
        )).run()
        self.assertEqual([(i.start, i.finish) for i in result.setup_intervals], [(0, 4), (7, 13)])
        self.assertEqual(result.completion_times, {"L1": 15})
        self.assertIn("SETUP_FINISH_STALE", {r.event_type for r in result.trace})

    def test_calendar_pm_preemptively_resumes_batch_atomically(self) -> None:
        batch = BatchSpec(125, 150, 125, 0)
        lots = tuple(
            LotSpec(f"L{i}", 0, (operation(1, 20, batch_spec=batch),))
            for i in range(1, 6)
        )
        result = Simulator(scenario(
            lots=lots,
            calendar=(calendar_pm("M1", (7, 4)),),
        )).run()
        interval = result.batch_intervals[0]
        self.assertEqual((interval.start, interval.finish, interval.active_processing_time), (0, 24, 20))
        self.assertEqual(set(result.completion_times.values()), {24})
        self.assertIn("BATCH_FINISH_STALE", {r.event_type for r in result.trace})

    def test_wafer_threshold_exact_not_reached_and_crossed_reset_zero(self) -> None:
        exact = Simulator(scenario(
            lots=(
                LotSpec("L1", 0, (operation(1, 1),), quantity_wafers=25),
                LotSpec("L2", 0, (operation(1, 1),), quantity_wafers=25),
            ),
            wafer=(wafer_pm("M1", threshold=125, duration=2, initial=100),),
            horizon=5,
        )).run()
        self.assertEqual(exact.pm_count, 1)
        self.assertEqual(exact.wafer_pm_states[0].counter_wafers, 25)
        self.assertEqual(
            [r.sim_time for r in exact.trace if r.event_type == "PROCESS_START"],
            [0, 3],
        )

        below = Simulator(scenario(
            lots=(LotSpec("L1", 0, (operation(1, 1),), quantity_wafers=25),),
            wafer=(wafer_pm("M1", threshold=125, duration=2, initial=99),),
            horizon=3,
        )).run()
        self.assertEqual(below.pm_count, 0)
        self.assertEqual(below.wafer_pm_states[0].counter_wafers, 124)

        crossed = Simulator(scenario(
            lots=(LotSpec("L1", 0, (operation(1, 1),), quantity_wafers=25),),
            wafer=(wafer_pm("M1", threshold=100, duration=2, initial=90),),
            horizon=3,
        )).run()
        self.assertEqual(crossed.pm_count, 1)
        self.assertEqual(crossed.wafer_pm_states[0].counter_wafers, 0)
        due = next(r for r in crossed.trace if r.event_type == "PM_DUE")
        self.assertEqual((due.wafer_counter_before, due.wafer_counter_after), (90, 115))

    def test_batch_counts_total_wafers_once_and_triggers_one_pm(self) -> None:
        batch = BatchSpec(125, 150, 125, 0)
        lots = tuple(
            LotSpec(f"L{i}", 0, (operation(1, 2, batch_spec=batch),), quantity_wafers=25)
            for i in range(1, 6)
        )
        result = Simulator(scenario(
            lots=lots,
            wafer=(wafer_pm("M1", threshold=125, duration=2),),
            horizon=5,
        )).run()
        self.assertEqual(sum(r.event_type == "PM_DUE" for r in result.trace), 1)
        due = next(r for r in result.trace if r.event_type == "PM_DUE")
        self.assertEqual(due.processed_wafers, 125)
        self.assertEqual(result.pm_count, 1)

    def test_interrupted_processing_counts_wafers_only_at_real_finish(self) -> None:
        result = Simulator(scenario(
            lots=(LotSpec("L1", 0, (operation(1, 4),), quantity_wafers=25),),
            calendar=(calendar_pm("M1", (1, 2)),),
            wafer=(wafer_pm("M1", threshold=25, duration=1),),
            horizon=8,
        )).run()
        self.assertEqual(result.completion_times, {"L1": 6})
        self.assertEqual(sum(r.event_type == "PM_DUE" for r in result.trace), 1)
        self.assertEqual(result.pm_count, 2)
        self.assertEqual(result.wafer_pm_states[0].occurrence_count, 1)

    def test_pm_preserves_dedication_and_can_cause_cqt_violation(self) -> None:
        dedicated = Simulator(scenario(
            machines=(MachineSpec("M1"), MachineSpec("M2")),
            lots=(LotSpec("L1", 0, (
                operation(1, 1, ("M1", "M2")),
                operation(2, 1, ("M1", "M2")),
            )),),
            calendar=(calendar_pm("M1", (1, 4)),),
            dedication_constraints=(DedicationSpec("D1", "R", 1, 2),),
        )).run()
        target = next(
            r for r in dedicated.trace
            if r.event_type == "PROCESS_START" and r.step_id == 2
        )
        self.assertEqual((target.machine_id, target.sim_time), ("M1", 5))

        cqt = Simulator(scenario(
            machines=(MachineSpec("M1"), MachineSpec("M2")),
            lots=(LotSpec("L1", 0, (
                operation(1, 1, ("M1",)),
                operation(2, 1, ("M2",)),
            )),),
            calendar=(calendar_pm("M2", (0, 5)),),
            cqt_constraints=(CQTSpec("C1", "R", 1, 2, 2),),
        )).run()
        record = cqt.cqt_records[0]
        self.assertEqual((record.opened_at, record.closed_at, record.actual_duration), (1, 5, 4))
        self.assertTrue(record.violation)

    def test_failure_pm_overlap_has_single_downtime_owner(self) -> None:
        failure_during_pm = Simulator(scenario(
            lots=(LotSpec("L1", 6, (operation(1, 1),)),),
            calendar=(calendar_pm("M1", (0, 5)),),
            failures=(failure("M1", (2, 2)),),
            horizon=8,
        )).run()
        self.assertEqual(failure_during_pm.pm_count, 1)
        self.assertEqual(failure_during_pm.failure_count, 0)
        self.assertEqual(sum(r.event_type == "FAILURE_START_STALE" for r in failure_during_pm.trace), 1)

        pm_during_failure = Simulator(scenario(
            lots=(LotSpec("L1", 6, (operation(1, 1),)),),
            calendar=(calendar_pm("M1", (2, 2)),),
            failures=(failure("M1", (0, 5)),),
            horizon=8,
        )).run()
        self.assertEqual(pm_during_failure.failure_count, 1)
        self.assertEqual(pm_during_failure.pm_count, 0)
        self.assertEqual(sum(r.event_type == "PM_START_STALE" for r in pm_during_failure.trace), 1)

    def test_stochastic_failure_suppressed_by_pm_restarts_after_pm_finish(self) -> None:
        stochastic = MachineFailureSpec(
            "M1",
            "stochastic",
            failure_interval=TimeDistributionSpec("constant", 2),
            repair_duration=TimeDistributionSpec("constant", 1),
        )
        result = Simulator(scenario(
            lots=(LotSpec("L1", 9, (operation(1, 1),)),),
            calendar=(calendar_pm("M1", (0, 5)),),
            failures=(stochastic,),
            horizon=8,
        )).run()
        self.assertEqual(sum(r.event_type == "FAILURE_START_STALE" for r in result.trace), 1)
        effective = [r for r in result.trace if r.event_type == "FAILURE_START"]
        self.assertEqual([(r.sim_time, r.failure_occurrence_index) for r in effective], [(7, 1)])
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.machine_statistics["M1"].failure_downtime, 1)

    def test_wafer_pm_due_during_same_time_failure_is_deferred_until_repair(self) -> None:
        result = Simulator(scenario(
            lots=(
                LotSpec("L1", 0, (operation(1, 2),), quantity_wafers=25),
                LotSpec("L2", 0, (operation(1, 1),), quantity_wafers=25),
            ),
            wafer=(wafer_pm("M1", threshold=25, duration=2),),
            failures=(failure("M1", (2, 3)),),
            horizon=9,
        )).run()
        self.assertEqual(sum(r.event_type == "PM_START_DEFERRED" for r in result.trace), 1)
        pm_start = next(r for r in result.trace if r.event_type == "PM_START")
        self.assertEqual(pm_start.sim_time, 5)
        l2_start = next(r for r in result.trace if r.event_type == "PROCESS_START" and r.lot_id == "L2")
        self.assertEqual(l2_start.sim_time, 7)

    def test_exact_time_priority_failure_wins_pm_and_pm_finish_precedes_release_timeout(self) -> None:
        self.assertLess(EVENT_PRIORITIES[EventType.FAILURE_START], EVENT_PRIORITIES[EventType.PM_START])
        self.assertLess(EVENT_PRIORITIES[EventType.PM_FINISH], EVENT_PRIORITIES[EventType.LOT_RELEASE])
        same_start = Simulator(scenario(
            lots=(LotSpec("L1", 0, (operation(1, 10),)),),
            calendar=(calendar_pm("M1", (2, 2)),),
            failures=(failure("M1", (2, 3)),),
            horizon=6,
        )).run()
        at_two = [r.event_type for r in same_start.trace if r.sim_time == 2]
        self.assertLess(at_two.index("FAILURE_START"), at_two.index("PM_START_STALE"))

        completion = Simulator(scenario(
            lots=(LotSpec("L1", 0, (operation(1, 2),)),),
            calendar=(calendar_pm("M1", (2, 1)),),
            horizon=3,
        )).run()
        at_completion = [r.event_type for r in completion.trace if r.sim_time == 2]
        self.assertLess(at_completion.index("PROCESS_FINISH"), at_completion.index("PM_START"))

        release = Simulator(scenario(
            lots=(LotSpec("L1", 3, (operation(1, 1),)),),
            calendar=(calendar_pm("M1", (0, 3)),),
        )).run()
        at_three = [r.event_type for r in release.trace if r.sim_time == 3]
        self.assertLess(at_three.index("PM_FINISH"), at_three.index("LOT_RELEASE"))
        self.assertIn("PROCESS_START", at_three)

        batch = BatchSpec(125, 150, 150, 5)
        lots = tuple(LotSpec(f"L{i}", 0, (operation(1, 1, batch_spec=batch),)) for i in range(1, 6))
        timeout = Simulator(scenario(
            lots=lots,
            calendar=(calendar_pm("M1", (1, 4)),),
        )).run()
        at_five = [r.event_type for r in timeout.trace if r.sim_time == 5]
        self.assertLess(at_five.index("PM_FINISH"), at_five.index("BATCH_TIMEOUT"))
        self.assertIn("BATCH_START", at_five)

    def test_fixed_horizon_preserves_active_pm_and_interrupted_activity(self) -> None:
        result = Simulator(scenario(
            lots=(LotSpec("L1", 0, (operation(1, 10),)),),
            calendar=(calendar_pm("M1", (2, 10)),),
            horizon=5,
        )).run()
        snapshot = result.machine_failure_snapshots[0]
        self.assertEqual(snapshot.availability, "DOWN")
        self.assertEqual(snapshot.downtime_cause, "CALENDAR_PM")
        self.assertEqual(snapshot.remaining_downtime_time, 7)
        self.assertEqual((snapshot.interrupted_activity_kind, snapshot.remaining_activity_time), ("PROCESS", 8))
        stats = result.machine_statistics["M1"]
        self.assertEqual((stats.processing_time, stats.pm_downtime, stats.failure_downtime), (2, 3, 0))
        self.assertEqual(result.completion_times, {})

    def test_fixed_horizon_preserves_wafer_pm_pending_behind_failure(self) -> None:
        result = Simulator(scenario(
            lots=(LotSpec("L1", 0, (operation(1, 2),), quantity_wafers=25),),
            wafer=(wafer_pm("M1", threshold=25, duration=2),),
            failures=(failure("M1", (2, 10)),),
            horizon=5,
        )).run()
        snapshot = result.machine_failure_snapshots[0]
        self.assertEqual(snapshot.downtime_cause, "FAILURE")
        self.assertTrue(snapshot.wafer_pm_pending)
        self.assertTrue(result.wafer_pm_states[0].pending)
        self.assertFalse(result.wafer_pm_states[0].active)
        self.assertEqual(result.pm_count, 0)

    def test_same_seed_reproduces_periodic_pm_and_other_stream_calls_do_not_change_it(self) -> None:
        target = CalendarPMSpec(
            "PM1", "M1", "periodic",
            first_start_time=2,
            interval=TimeDistributionSpec("uniform", 5, 2),
            duration=TimeDistributionSpec("uniform", 2, 1),
        )
        extra = CalendarPMSpec(
            "PM0", "M0", "periodic",
            first_start_time=1,
            interval=TimeDistributionSpec("uniform", 3, 1),
            duration=TimeDistributionSpec("uniform", 1, 0.5),
        )
        single_runtime = PMRuntime((target,), (), EntityRandomStreams(42))
        first = single_runtime.initial_calendar_occurrences()[0]
        second = single_runtime.next_calendar_occurrence(first)
        combined_runtime = PMRuntime((extra, target), (), EntityRandomStreams(42))
        combined_first = next(x for x in combined_runtime.initial_calendar_occurrences() if x.machine_id == "M1")
        combined_second = combined_runtime.next_calendar_occurrence(combined_first)
        self.assertEqual((first, second), (combined_first, combined_second))

        periodic_scenario = scenario(
            machines=(MachineSpec("M1"),),
            lots=(LotSpec("L1", 0, (operation(1, 20),)),),
            calendar=(target,),
            horizon=12,
        )
        a = Simulator(periodic_scenario, seed=9, git_commit="x").run()
        b = Simulator(periodic_scenario, seed=9, git_commit="x").run()
        self.assertEqual(a.trace_as_dicts(), b.trace_as_dicts())
        self.assertEqual(a.pm_intervals, b.pm_intervals)
        self.assertEqual(a.machine_statistics, b.machine_statistics)

    def test_periodic_calendar_continues_after_stale_overlap_without_drift(self) -> None:
        periodic = CalendarPMSpec(
            "PM1",
            "M1",
            "periodic",
            first_start_time=2,
            interval=TimeDistributionSpec("constant", 5),
            duration=TimeDistributionSpec("constant", 1),
        )
        result = Simulator(scenario(
            lots=(LotSpec("L1", 9, (operation(1, 1),)),),
            calendar=(periodic,),
            failures=(failure("M1", (0, 4)),),
            horizon=8,
        )).run()
        stale = next(r for r in result.trace if r.event_type == "PM_START_STALE")
        active = next(r for r in result.trace if r.event_type == "PM_START")
        self.assertEqual(stale.sim_time, 2)
        self.assertEqual(active.sim_time, 7)
        self.assertEqual(result.pm_count, 1)

    def test_invalid_pm_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ScriptedPMSpec(0, 0)
        with self.assertRaises(ValueError):
            wafer_pm("M1", threshold=100, duration=1, initial=100)
        with self.assertRaisesRegex(ValueError, "未知设备"):
            scenario(
                lots=(LotSpec("L1", 0, (operation(1, 1),)),),
                calendar=(calendar_pm("M9", (1, 1)),),
            )


if __name__ == "__main__":
    unittest.main()
