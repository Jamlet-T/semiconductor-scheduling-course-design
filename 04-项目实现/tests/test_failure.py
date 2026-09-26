"""MC07 Failure 的金标准、边界、集成和确定性测试。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (
    BatchSpec,
    CQTSpec,
    DedicationSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    SetupTransition,
    TimeDistributionSpec,
)
from fab_scheduler.simulation.engine import Simulator
from fab_scheduler.simulation.events import EVENT_PRIORITIES, EventType
from fab_scheduler.simulation.failure import FailureSchedule
from fab_scheduler.simulation.random_streams import EntityRandomStreams


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "micro_cases" / "cases.json"


def operation(step: int, duration: float, machines=("M1",), **kwargs) -> OperationSpec:
    return OperationSpec(step, duration, machines, route_id=kwargs.pop("route_id", "R"), **kwargs)


def scripted(machine: str, *occurrences: tuple[float, float]) -> MachineFailureSpec:
    return MachineFailureSpec(machine, "scripted", tuple(ScriptedFailureSpec(*item) for item in occurrences))


def basic_scenario(*, failures, lots=None, machines=None, horizon=None) -> Scenario:
    return Scenario(
        "FAILURE_TEST",
        "micro_cases@0.1.0",
        tuple(machines or (MachineSpec("M1"),)),
        tuple(lots or (LotSpec("L1", 0, (operation(1, 10),)),)),
        termination_mode="fixed_horizon" if horizon is not None else "until_all_complete",
        horizon=horizon,
        failure_specs=tuple(failures),
    )


class FailureRuntimeTests(unittest.TestCase):
    def test_mc07_golden_trace_remaining_work_stale_finish_and_metrics(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        case = next(item for item in fixture["cases"] if item["id"].startswith("MC07"))
        scenario = basic_scenario(
            failures=(scripted("M1", (5, 3), (13, 3)),),
            lots=(
                LotSpec("L1", 0, (operation(1, 10),)),
                LotSpec("L2", 13, (operation(1, 2),)),
            ),
            horizon=13,
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.key_trace(), case["expected_trace"])
        self.assertEqual(result.completion_times, {"L1": 13})
        self.assertEqual([(i.start, i.finish) for i in result.processing_intervals], [(0, 5), (8, 13)])
        self.assertEqual(sum(r.event_type == "PROCESS_FINISH_STALE" for r in result.trace), 1)
        self.assertFalse(any(r.event_type == "DISPATCH" and r.lot_id == "L2" for r in result.trace))
        machine = result.machine_statistics["M1"]
        self.assertEqual((machine.processing_time, machine.downtime, machine.idle_time), (10, 3, 0))
        self.assertEqual((machine.availability, machine.failure_count), ("DOWN", 2))
        self.assertEqual(result.metrics.completed_lots, 1)
        self.assertEqual(result.metrics.terminal_wip_lots, 1)
        self.assertEqual(result.failure_count, 2)
        self.assertEqual(result.total_downtime, 3)
        self.assertEqual(result.provenance.simulation_contract_version, "0.1.5")
        self.assertEqual(result.provenance.simulation_config["failure_specs"][0]["model_type"], "scripted")

    def test_failure_while_idle_blocks_dispatch_then_repair_uses_normal_barrier(self) -> None:
        result = Simulator(basic_scenario(
            failures=(scripted("M1", (0, 3)),),
            lots=(LotSpec("L1", 1, (operation(1, 2),)),),
        )).run()
        starts = [r for r in result.trace if r.event_type == "PROCESS_START"]
        self.assertEqual([r.sim_time for r in starts], [3])
        self.assertEqual(result.completion_times, {"L1": 5})

    def test_setup_is_paused_and_resumed_without_restart(self) -> None:
        scenario = Scenario(
            "FAILURE_SETUP", "micro@x", (MachineSpec("M1", "A"),),
            (LotSpec("L1", 0, (operation(1, 2, required_setup="B"),)),),
            setup_transitions=(SetupTransition("A", "B", 10),),
            failure_specs=(scripted("M1", (4, 3)),),
        )
        result = Simulator(scenario).run()
        self.assertEqual([(i.start, i.finish) for i in result.setup_intervals], [(0, 4), (7, 13)])
        self.assertEqual(result.completion_times, {"L1": 15})
        self.assertEqual(result.machine_statistics["M1"].setup_time, 10)
        self.assertIn("SETUP_FINISH_STALE", {r.event_type for r in result.trace})

    def test_batch_is_atomically_paused_and_resumed(self) -> None:
        batch = BatchSpec(125, 150, 125, 0)
        lots = tuple(LotSpec(f"L{i}", 0, (operation(1, 20, batch_spec=batch),)) for i in range(1, 6))
        result = Simulator(basic_scenario(failures=(scripted("M1", (7, 4)),), lots=lots)).run()
        interval = result.batch_intervals[0]
        self.assertEqual((interval.start, interval.finish, interval.active_processing_time), (0, 24, 20))
        self.assertEqual([(i.start, i.finish) for i in result.downtime_intervals], [(7, 11)])
        self.assertEqual(set(result.completion_times.values()), {24})
        self.assertIn("BATCH_FINISH_STALE", {r.event_type for r in result.trace})

    def test_down_machine_cannot_receive_dedicated_lot_or_fallback(self) -> None:
        lots = (
            LotSpec("L1", 0, (
                operation(1, 1, ("M1", "M2")),
                operation(2, 1, ("M1", "M2")),
            )),
        )
        scenario = Scenario(
            "FAILURE_DEDICATION", "micro@x", (MachineSpec("M1"), MachineSpec("M2")), lots,
            dedication_constraints=(DedicationSpec("D1", "R", 1, 2),),
            failure_specs=(scripted("M1", (1, 4)),),
        )
        result = Simulator(scenario).run()
        target = [r for r in result.trace if r.event_type == "PROCESS_START" and r.step_id == 2]
        self.assertEqual([(r.sim_time, r.machine_id) for r in target], [(5, "M1")])

    def test_failure_delays_target_start_and_creates_cqt_violation(self) -> None:
        scenario = Scenario(
            "FAILURE_CQT", "micro@x", (MachineSpec("M1"), MachineSpec("M2")),
            (LotSpec("L1", 0, (operation(1, 2, ("M1",)), operation(2, 1, ("M2",)))),),
            cqt_constraints=(CQTSpec("C1", "R", 1, 2, 5),),
            failure_specs=(scripted("M2", (0, 10)),),
        )
        result = Simulator(scenario).run()
        record = result.cqt_records[0]
        self.assertEqual((record.opened_at, record.closed_at, record.actual_duration), (2, 10, 8))
        self.assertTrue(record.violation)

    def test_setup_failure_delay_is_included_in_open_cqt(self) -> None:
        scenario = Scenario(
            "FAILURE_SETUP_CQT", "micro@x", (MachineSpec("M1"), MachineSpec("M2", "A")),
            (LotSpec("L1", 0, (
                operation(1, 2, ("M1",)),
                operation(2, 1, ("M2",), required_setup="B"),
            )),),
            setup_transitions=(SetupTransition("A", "B", 4),),
            cqt_constraints=(CQTSpec("C1", "R", 1, 2, 5),),
            failure_specs=(scripted("M2", (4, 4)),),
        )
        result = Simulator(scenario).run()
        record = result.cqt_records[0]
        self.assertEqual((record.opened_at, record.closed_at, record.actual_duration), (2, 10, 8))
        self.assertTrue(record.violation)

    def test_fixed_horizon_preserves_interrupted_processing_and_repair(self) -> None:
        result = Simulator(basic_scenario(failures=(scripted("M1", (4, 10)),), horizon=8)).run()
        snapshot = result.machine_failure_snapshots[0]
        self.assertEqual((snapshot.availability, snapshot.remaining_repair_time), ("DOWN", 6))
        self.assertEqual((snapshot.interrupted_activity_kind, snapshot.remaining_activity_time), ("PROCESS", 6))
        self.assertEqual(result.completion_times, {})
        self.assertEqual(result.machine_statistics["M1"].processing_time, 4)
        self.assertEqual(result.machine_statistics["M1"].downtime, 4)

    def test_fixed_horizon_preserves_interrupted_setup_and_batch(self) -> None:
        setup_scenario = Scenario(
            "FAILURE_SETUP_HORIZON", "micro@x", (MachineSpec("M1", "A"),),
            (LotSpec("L1", 0, (operation(1, 1, required_setup="B"),)),),
            termination_mode="fixed_horizon", horizon=6,
            setup_transitions=(SetupTransition("A", "B", 10),),
            failure_specs=(scripted("M1", (3, 10)),),
        )
        setup_result = Simulator(setup_scenario).run()
        setup_snapshot = setup_result.machine_failure_snapshots[0]
        self.assertEqual((setup_snapshot.interrupted_activity_kind, setup_snapshot.remaining_activity_time), ("SETUP", 7))
        self.assertEqual(setup_result.machine_statistics["M1"].setup_time, 3)

        batch = BatchSpec(125, 150, 125, 0)
        lots = tuple(LotSpec(f"L{i}", 0, (operation(1, 20, batch_spec=batch),)) for i in range(1, 6))
        batch_result = Simulator(basic_scenario(failures=(scripted("M1", (4, 10)),), lots=lots, horizon=7)).run()
        batch_snapshot = batch_result.machine_failure_snapshots[0]
        self.assertEqual((batch_snapshot.interrupted_activity_kind, batch_snapshot.remaining_activity_time), ("BATCH", 16))
        self.assertEqual(batch_result.active_batches[0].remaining_processing, 16)
        self.assertEqual(batch_result.machine_statistics["M1"].processing_time, 4)

    def test_nested_failure_is_stale_and_has_no_second_repair(self) -> None:
        result = Simulator(basic_scenario(
            failures=(scripted("M1", (0, 5), (2, 2)),), horizon=5,
        )).run()
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(sum(r.event_type == "FAILURE_START_STALE" for r in result.trace), 1)
        self.assertEqual(sum(r.event_type == "REPAIR_COMPLETE" for r in result.trace), 1)

    def test_finish_failure_and_repair_release_exact_ties_follow_contract(self) -> None:
        self.assertLess(EVENT_PRIORITIES[EventType.PROCESS_FINISH], EVENT_PRIORITIES[EventType.FAILURE_START])
        self.assertLess(EVENT_PRIORITIES[EventType.REPAIR_FINISH], EVENT_PRIORITIES[EventType.LOT_RELEASE])
        result = Simulator(basic_scenario(
            failures=(scripted("M1", (2, 3), (10, 2)),),
            lots=(LotSpec("L1", 0, (operation(1, 2),)), LotSpec("L2", 5, (operation(1, 1),))),
            horizon=5,
        )).run()
        at_two = [r.event_type for r in result.trace if r.sim_time == 2]
        self.assertLess(at_two.index("PROCESS_FINISH"), at_two.index("FAILURE_START"))
        at_five = [r.event_type for r in result.trace if r.sim_time == 5]
        self.assertLess(at_five.index("REPAIR_COMPLETE"), at_five.index("LOT_RELEASE"))
        self.assertIn("PROCESS_START", at_five)

    def test_same_seed_reproduces_failure_trace_metrics_and_terminal_state(self) -> None:
        spec = MachineFailureSpec(
            "M1", "stochastic",
            failure_interval=TimeDistributionSpec("uniform", 4, 2),
            repair_duration=TimeDistributionSpec("uniform", 2, 1),
        )
        scenario = basic_scenario(failures=(spec,), horizon=8)
        first = Simulator(scenario, seed=9, git_commit="x").run()
        second = Simulator(scenario, seed=9, git_commit="x").run()
        self.assertEqual(first.trace_as_dicts(), second.trace_as_dicts())
        self.assertEqual(first.machine_statistics, second.machine_statistics)
        self.assertEqual(first.machine_failure_snapshots, second.machine_failure_snapshots)

    def test_random_stream_samples_are_call_order_and_other_machine_independent(self) -> None:
        target = MachineFailureSpec(
            "M1", "stochastic",
            failure_interval=TimeDistributionSpec("uniform", 7.5, 2.5),
            repair_duration=TimeDistributionSpec("uniform", 3, 1),
        )
        extra = MachineFailureSpec(
            "M0", "stochastic",
            failure_interval=TimeDistributionSpec("uniform", 2, 1),
            repair_duration=TimeDistributionSpec("uniform", 1, 0.5),
        )
        single = FailureSchedule((target,), EntityRandomStreams(42)).initial_occurrences()[0]
        combined = next(item for item in FailureSchedule((extra, target), EntityRandomStreams(42)).initial_occurrences() if item.machine_id == "M1")
        self.assertEqual(single, combined)
        self.assertGreaterEqual(single.failure_time, 6.25)
        self.assertLessEqual(single.failure_time, 8.75)

    def test_repair_and_batch_timeout_tie_sees_up_machine(self) -> None:
        batch = BatchSpec(125, 150, 150, 5)
        lots = tuple(LotSpec(f"L{i}", 0, (operation(1, 2, batch_spec=batch),)) for i in range(1, 6))
        result = Simulator(basic_scenario(failures=(scripted("M1", (1, 4)),), lots=lots)).run()
        at_five = [r.event_type for r in result.trace if r.sim_time == 5]
        self.assertLess(at_five.index("REPAIR_COMPLETE"), at_five.index("BATCH_TIMEOUT"))
        self.assertIn("BATCH_START", at_five)


class FailureDomainTests(unittest.TestCase):
    def test_uniform_uses_mean_and_full_width_and_rejects_nonpositive_lower_bound(self) -> None:
        self.assertEqual(TimeDistributionSpec("uniform", 7.5, 2.5).width_minutes, 2.5)
        with self.assertRaises(ValueError):
            TimeDistributionSpec("uniform", 1, 2)

    def test_failure_configuration_rejects_unknown_machine_and_invalid_duration(self) -> None:
        with self.assertRaises(ValueError):
            ScriptedFailureSpec(1, 0)
        with self.assertRaisesRegex(ValueError, "未知设备"):
            basic_scenario(failures=(scripted("M9", (1, 1)),))


if __name__ == "__main__":
    unittest.main()
