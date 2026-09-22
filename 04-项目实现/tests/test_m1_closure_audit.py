"""M1 Closure Audit：边界、指标、守恒和 provenance 审计测试。"""

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
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
    SetupTransition,
)
from fab_scheduler.evaluation.audit import (
    audit_result_invariants,
    recompute_trace_metrics,
)
from fab_scheduler.simulation.engine import (
    LotStatus,
    MachineAvailability,
    MachineStatus,
    Simulator,
)
from fab_scheduler.simulation.events import EVENT_PRIORITIES, EventPriority, EventType


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "micro_cases" / "cases.json"


def op(
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


class M1ClosureMetricAndBoundaryTests(unittest.TestCase):
    def test_fixed_horizon_executes_finish_release_failure_and_pm_at_h(self) -> None:
        horizon = 10
        scenario = Scenario(
            "EXACT_HORIZON",
            "audit@0.1.3",
            tuple(MachineSpec(f"M{i}") for i in range(1, 5)),
            (
                LotSpec("L1", 0, (op(1, 10, ("M1",)),)),
                LotSpec("L2", horizon, (op(1, 1, ("M2",)),)),
            ),
            termination_mode="fixed_horizon",
            horizon=horizon,
            failure_specs=(
                MachineFailureSpec(
                    "M3",
                    "scripted",
                    (ScriptedFailureSpec(horizon, 3),),
                ),
            ),
            calendar_pm_specs=(
                CalendarPMSpec(
                    "PM-M4",
                    "M4",
                    "scripted",
                    (ScriptedPMSpec(horizon, 3),),
                ),
            ),
        )
        result = Simulator(scenario, seed=42, git_commit="audit").run()
        at_h = [record.event_type for record in result.trace if record.sim_time == horizon]
        for event_type in (
            "PROCESS_FINISH",
            "LOT_COMPLETE",
            "FAILURE_START",
            "PM_START",
            "LOT_RELEASE",
        ):
            self.assertIn(event_type, at_h)
        self.assertEqual(result.metrics.completed_lots, 1)
        self.assertEqual(result.metrics.released_lots, 2)
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.pm_count, 1)
        self.assertEqual(result.machine_statistics["M3"].failure_downtime, 0)
        self.assertEqual(result.machine_statistics["M4"].pm_downtime, 0)
        snapshots = {
            item.machine_id: item for item in result.machine_failure_snapshots
        }
        self.assertEqual(snapshots["M3"].remaining_downtime_time, 3)
        self.assertEqual(snapshots["M4"].remaining_downtime_time, 3)

    def test_throughput_denominator_follows_termination_mode(self) -> None:
        base = dict(
            scenario_id="THROUGHPUT_AUDIT",
            dataset_version="audit@0.1.3",
            machines=(MachineSpec("M1"),),
            lots=(LotSpec("L1", 0, (op(1, 5),)),),
        )
        all_complete = Simulator(Scenario(**base)).run()
        fixed = Simulator(
            Scenario(
                **base,
                termination_mode="fixed_horizon",
                horizon=10,
            )
        ).run()
        self.assertEqual(all_complete.metrics.end_time, 5)
        self.assertEqual(all_complete.metrics.throughput_lots_per_minute, 1 / 5)
        self.assertEqual(fixed.metrics.end_time, 10)
        self.assertEqual(fixed.metrics.throughput_lots_per_minute, 1 / 10)

    def test_mean_wip_is_time_integral_and_excludes_unreleased_lots(self) -> None:
        scenario = Scenario(
            "MEAN_WIP_AUDIT",
            "audit@0.1.3",
            (MachineSpec("M1"), MachineSpec("M2")),
            (
                LotSpec("L1", 0, (op(1, 10, ("M1",)),)),
                LotSpec("L2", 0, (op(1, 100, ("M2",)),)),
                LotSpec("L3", 40, (op(1, 1, ("M1",)),)),
            ),
            termination_mode="fixed_horizon",
            horizon=30,
        )
        result = Simulator(scenario).run()
        self.assertEqual(result.metrics.mean_wip, 4 / 3)
        recalculated = recompute_trace_metrics(
            result.trace,
            end_time=30,
        )
        self.assertEqual(recalculated.mean_wip, 4 / 3)

    def test_remaining_work_uses_unfinished_nominal_processing_only(self) -> None:
        scenario = Scenario(
            "REMAINING_WORK_AUDIT",
            "audit@0.1.3",
            (MachineSpec("M1"),),
            (LotSpec("L1", 0, (op(1, 10), op(2, 4))),),
            termination_mode="fixed_horizon",
            horizon=7,
            failure_specs=(
                MachineFailureSpec(
                    "M1",
                    "scripted",
                    (ScriptedFailureSpec(5, 10),),
                ),
            ),
        )
        result = Simulator(scenario).run()
        self.assertEqual(result.metrics.remaining_work_minutes, 9)

    def test_initial_wip_is_excluded_from_full_cycle_time_cohort(self) -> None:
        scenario = Scenario(
            "INITIAL_WIP_COHORT",
            "audit@0.1.3",
            (MachineSpec("M1"), MachineSpec("M2")),
            (
                LotSpec(
                    "W1",
                    0,
                    (op(1, 2, ("M1",)),),
                    is_initial_wip=True,
                ),
                LotSpec("L1", 0, (op(1, 10, ("M2",)),)),
            ),
        )
        result = Simulator(scenario).run()
        self.assertEqual(result.metrics.mean_cycle_time_completed, 10)
        self.assertEqual(result.metrics.cycle_time_coverage, 1)

    def test_machine_time_conservation_for_fixed_and_all_complete(self) -> None:
        fixed = Scenario(
            "TIME_CONSERVATION_FIXED",
            "audit@0.1.3",
            (MachineSpec("M1", "A"), MachineSpec("M2")),
            (
                LotSpec(
                    "L1",
                    0,
                    (op(1, 6, ("M1",), required_setup="B"),),
                ),
            ),
            termination_mode="fixed_horizon",
            horizon=12,
            setup_transitions=(SetupTransition("A", "B", 2),),
            failure_specs=(
                MachineFailureSpec(
                    "M1",
                    "scripted",
                    (ScriptedFailureSpec(6, 2),),
                ),
            ),
            calendar_pm_specs=(
                CalendarPMSpec(
                    "PM1",
                    "M1",
                    "scripted",
                    (ScriptedPMSpec(1, 2),),
                ),
            ),
        )
        fixed_result = Simulator(fixed).run()
        for stats in fixed_result.machine_statistics.values():
            self.assertAlmostEqual(
                stats.processing_time
                + stats.setup_time
                + stats.idle_time
                + stats.failure_downtime
                + stats.pm_downtime,
                12,
            )

        all_complete = Scenario(
            "TIME_CONSERVATION_COMPLETE",
            "audit@0.1.3",
            (MachineSpec("M1"), MachineSpec("M2")),
            (LotSpec("L1", 0, (op(1, 10, ("M1",)), op(2, 20, ("M2",)))),),
        )
        complete_result = Simulator(all_complete).run()
        for stats in complete_result.machine_statistics.values():
            self.assertAlmostEqual(
                stats.processing_time
                + stats.setup_time
                + stats.idle_time
                + stats.failure_downtime
                + stats.pm_downtime,
                complete_result.metrics.end_time,
            )

    def test_result_auditor_recomputes_metrics_capacity_cqt_and_conservation(self) -> None:
        batch = BatchSpec(125, 150, 125, 0)
        scenario = Scenario(
            "RESULT_AUDITOR",
            "audit@0.1.3",
            (MachineSpec("M1"),),
            tuple(
                LotSpec(
                    f"L{i}",
                    0,
                    (op(1, 2, batch_spec=batch),),
                    quantity_wafers=25,
                )
                for i in range(1, 6)
            ),
            termination_mode="fixed_horizon",
            horizon=5,
        )
        result = Simulator(scenario).run()
        audit = audit_result_invariants(result, scenario)
        self.assertTrue(audit.passed, audit.violations)

    def test_lot_states_and_machine_ownership_are_exclusive(self) -> None:
        scenario = Scenario(
            "STATE_EXCLUSIVITY",
            "audit@0.1.3",
            (MachineSpec("M1", "A"), MachineSpec("M2")),
            (
                LotSpec("L1", 0, (op(1, 20, ("M1",)),)),
                LotSpec("L2", 0, (op(1, 1, ("M2",), required_setup="B"),)),
                LotSpec("L3", 20, (op(1, 1, ("M2",)),)),
            ),
            termination_mode="fixed_horizon",
            horizon=5,
            setup_transitions=(SetupTransition("", "B", 10),),
        )
        simulator = Simulator(scenario)
        simulator.run()
        owning_machines: dict[str, int] = {}
        for machine in simulator._machines.values():
            if machine.lot_id is not None:
                owning_machines[machine.lot_id] = (
                    owning_machines.get(machine.lot_id, 0) + 1
                )
            self.assertFalse(
                machine.availability is MachineAvailability.DOWN
                and machine.downtime_cause is None
            )
            self.assertFalse(
                machine.status is MachineStatus.IDLE
                and machine.interrupted_activity is not None
            )
        self.assertTrue(all(count == 1 for count in owning_machines.values()))
        for lot in simulator._lots.values():
            self.assertIsInstance(lot.status, LotStatus)
            if lot.status in {LotStatus.RESERVED, LotStatus.PROCESSING}:
                self.assertEqual(owning_machines.get(lot.spec.lot_id), 1)
            elif lot.status in {
                LotStatus.UNRELEASED,
                LotStatus.QUEUED,
                LotStatus.COMPLETED,
            }:
                self.assertNotIn(lot.spec.lot_id, owning_machines)

    def test_event_priority_table_matches_contract_values(self) -> None:
        self.assertEqual(EVENT_PRIORITIES[EventType.PROCESS_FINISH], 10)
        self.assertEqual(EVENT_PRIORITIES[EventType.SETUP_FINISH], 10)
        self.assertEqual(EVENT_PRIORITIES[EventType.BATCH_FINISH], 10)
        self.assertEqual(EVENT_PRIORITIES[EventType.REPAIR_FINISH], 20)
        self.assertEqual(EVENT_PRIORITIES[EventType.PM_FINISH], 20)
        self.assertEqual(EVENT_PRIORITIES[EventType.FAILURE_START], 30)
        self.assertEqual(EVENT_PRIORITIES[EventType.PM_START], 35)
        self.assertEqual(EVENT_PRIORITIES[EventType.LOT_RELEASE], 40)
        self.assertEqual(EVENT_PRIORITIES[EventType.BATCH_TIMEOUT], 50)
        self.assertEqual(EVENT_PRIORITIES[EventType.DISPATCH_BARRIER], 60)
        self.assertEqual(EventPriority.MONITOR, 50)

    def test_all_fixtures_and_results_use_contract_0_1_3(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["contract_version"], "0.1.3")
        self.assertEqual(fixture["event_priority_contract_version"], "0.1.3")
        scenario = Scenario(
            "VERSION_AUDIT",
            "audit@0.1.3",
            (MachineSpec("M1"),),
            (LotSpec("L1", 0, (op(1, 1),)),),
        )
        result = Simulator(scenario, seed=42, git_commit="audit").run()
        self.assertEqual(result.provenance.simulation_contract_version, "0.1.3")
        self.assertEqual(
            result.provenance.dispatch_policy_contract_version,
            "0.1.1",
        )

    def test_provenance_contains_behavior_affecting_scenario_fields(self) -> None:
        batch = BatchSpec(125, 150, 125, 5)
        scenario = Scenario(
            "PROVENANCE_AUDIT",
            "audit-dataset@sha256:test",
            (MachineSpec("M1", "A"),),
            (
                LotSpec(
                    "L1",
                    0,
                    (op(1, 2, batch_spec=batch, required_setup="A"),),
                ),
            ),
            termination_mode="fixed_horizon",
            horizon=10,
            failure_specs=(
                MachineFailureSpec(
                    "M1",
                    "scripted",
                    (ScriptedFailureSpec(6, 1),),
                ),
            ),
            calendar_pm_specs=(
                CalendarPMSpec(
                    "PM1",
                    "M1",
                    "scripted",
                    (ScriptedPMSpec(8, 1),),
                ),
            ),
        )
        result = Simulator(scenario, seed=7, git_commit="audit-commit").run()
        provenance = result.provenance
        self.assertEqual(provenance.simulation_contract_version, "0.1.3")
        self.assertEqual(provenance.dataset_version, "audit-dataset@sha256:test")
        self.assertEqual(provenance.seed, 7)
        self.assertEqual(provenance.git_commit, "audit-commit")
        self.assertEqual(provenance.dispatch_policy, "FIFO")
        self.assertEqual(provenance.termination_condition, "fixed_horizon")
        self.assertEqual(provenance.horizon, 10)
        config = provenance.simulation_config
        for key in (
            "machines",
            "lots",
            "failure_specs",
            "calendar_pm_specs",
            "wafer_pm_specs",
            "cqt_constraints",
            "dedication_constraints",
            "random_stream_scheme",
            "failure_random_streams",
            "pm_random_streams",
        ):
            self.assertIn(key, config)


if __name__ == "__main__":
    unittest.main()
