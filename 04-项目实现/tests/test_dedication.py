"""MC06 Dedication 的金标准、硬约束和组合边界测试。"""

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
    MachineSpec,
    OperationSpec,
    Scenario,
    SetupTransition,
)
from fab_scheduler.policies.fifo import FIFOPolicy
from fab_scheduler.simulation.dedication import (
    DedicationRuntime,
    DedicationRuntimeError,
)
from fab_scheduler.simulation.engine import Simulator
from fab_scheduler.simulation.events import EventType


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "micro_cases"
    / "cases.json"
)


def load_mc06() -> dict:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return next(
        case for case in payload["cases"]
        if case["id"] == "MC06_MACHINE_DEDICATION"
    )


def dedication_spec(
    *,
    dedication_id: str = "DED-MC06-1-3",
    route: str = "MC06",
    source: int = 1,
    target: int = 3,
) -> DedicationSpec:
    return DedicationSpec(
        dedication_id=dedication_id,
        route_id=route,
        source_step_id=source,
        target_step_id=target,
    )


class AuditedFIFOPolicy:
    """记录 source 候选，并拒绝任何泄漏的 forbidden target 动作。"""

    name = "FIFO_DEDICATION_AUDIT"

    def __init__(self) -> None:
        self._fifo = FIFOPolicy()
        self.source_candidate_machines: set[str] = set()

    def select(self, state, feasible_actions):
        for action in feasible_actions:
            if action.lot_id == "L1" and action.step_id == 1:
                self.source_candidate_machines.add(action.machine_id)
            if (
                action.lot_id == "L1"
                and action.step_id == 3
                and action.machine_id != "M2"
            ):
                raise AssertionError(
                    "Dedication target 动作泄漏到非绑定 machine"
                )
        return self._fifo.select(state, feasible_actions)


def build_mc06(*, horizon: float | None = None) -> tuple[dict, Scenario]:
    case = load_mc06()
    scenario = Scenario(
        scenario_id=case["id"],
        dataset_version="micro_cases@0.1.0",
        machines=(MachineSpec("M1"), MachineSpec("M2"), MachineSpec("M3")),
        lots=(
            LotSpec(
                lot_id="A0",
                release_time=0,
                operations=(
                    OperationSpec(1, 2, ("M1",), route_id="AUX-M1"),
                ),
            ),
            LotSpec(
                lot_id="B0",
                release_time=5,
                operations=(
                    OperationSpec(1, 10, ("M2",), route_id="AUX-M2"),
                ),
            ),
            LotSpec(
                lot_id="L1",
                release_time=0,
                operations=(
                    OperationSpec(
                        1, 5, ("M1", "M2"), route_id="MC06", tool_group_id="G"
                    ),
                    OperationSpec(2, 5, ("M3",), route_id="MC06"),
                    OperationSpec(
                        3, 5, ("M1", "M2"), route_id="MC06", tool_group_id="G"
                    ),
                ),
            ),
        ),
        dedication_constraints=(dedication_spec(),),
        termination_mode=(
            "fixed_horizon" if horizon is not None else "until_all_complete"
        ),
        horizon=horizon,
    )
    return case, scenario


class DedicationRuntimeTests(unittest.TestCase):
    def test_unbound_source_enumeration_is_side_effect_free_until_commit(self) -> None:
        runtime = DedicationRuntime((dedication_spec(),))
        self.assertTrue(
            runtime.allows_machine(
                lot_id="L1", route_id="MC06", step_id=1, machine_id="M1"
            )
        )
        self.assertTrue(
            runtime.allows_machine(
                lot_id="L1", route_id="MC06", step_id=1, machine_id="M2"
            )
        )
        self.assertEqual(runtime.active_bindings, ())

        binding = runtime.establish_for_source(
            lot_id="L1",
            route_id="MC06",
            step_id=1,
            machine_id="M2",
            established_at=0,
        )[0]
        self.assertEqual(binding.machine_id, "M2")
        self.assertFalse(
            runtime.allows_machine(
                lot_id="L1", route_id="MC06", step_id=3, machine_id="M1"
            )
        )
        self.assertTrue(
            runtime.allows_machine(
                lot_id="L1", route_id="MC06", step_id=3, machine_id="M2"
            )
        )

    def test_multiple_independent_bindings_do_not_overwrite(self) -> None:
        runtime = DedicationRuntime(
            (
                dedication_spec(dedication_id="D1", source=1, target=3),
                dedication_spec(dedication_id="D2", source=2, target=4),
            )
        )
        runtime.establish_for_source(
            lot_id="L1", route_id="MC06", step_id=1,
            machine_id="M2", established_at=0,
        )
        runtime.establish_for_source(
            lot_id="L1", route_id="MC06", step_id=2,
            machine_id="M1", established_at=5,
        )
        self.assertEqual(
            [(item.dedication_id, item.machine_id) for item in runtime.active_bindings],
            [("D1", "M2"), ("D2", "M1")],
        )
        self.assertTrue(
            runtime.allows_machine(
                lot_id="L1", route_id="MC06", step_id=3, machine_id="M2"
            )
        )
        self.assertTrue(
            runtime.allows_machine(
                lot_id="L1", route_id="MC06", step_id=4, machine_id="M1"
            )
        )

    def test_qualification_conflict_is_explicit(self) -> None:
        runtime = DedicationRuntime((dedication_spec(),))
        runtime.establish_for_source(
            lot_id="L1", route_id="MC06", step_id=1,
            machine_id="M1", established_at=0,
        )
        with self.assertRaisesRegex(
            DedicationRuntimeError, "Dedication 与 qualification 冲突"
        ):
            runtime.validate_target_qualification(
                lot_id="L1",
                route_id="MC06",
                step_id=3,
                eligible_machines=("M2",),
            )


class DedicationIntegrationTests(unittest.TestCase):
    def test_mc06_golden_trace_hard_filter_lifecycle_kpi_and_provenance(self) -> None:
        case, scenario = build_mc06()
        policy = AuditedFIFOPolicy()
        result = Simulator(
            scenario, policy=policy, seed=42, git_commit="test-commit"
        ).run()

        self.assertEqual(result.key_trace(), case["expected_trace"])
        self.assertEqual(policy.source_candidate_machines, {"M1", "M2"})
        self.assertEqual(len(result.dedication_records), 1)
        record = result.dedication_records[0]
        self.assertEqual(record.machine_id, "M2")
        self.assertEqual(record.established_at, 0)
        self.assertEqual(record.released_at, 20)
        self.assertEqual(result.active_dedication_bindings, ())
        self.assertEqual(result.completion_times, {"A0": 2, "B0": 15, "L1": 20})
        self.assertEqual(result.metrics.completed_lots, 3)
        self.assertAlmostEqual(result.metrics.mean_cycle_time_completed, 32 / 3)
        self.assertEqual(result.metrics.throughput_lots_per_minute, 0.15)
        self.assertEqual(result.dedication_metrics.dedication_binding_count, 1)
        self.assertEqual(result.dedication_metrics.released_dedication_count, 1)

        target = next(
            interval for interval in result.processing_intervals
            if interval.lot_id == "L1" and interval.step_id == 3
        )
        self.assertEqual((target.machine_id, target.start, target.finish), ("M2", 15, 20))
        self.assertFalse(
            any(
                interval.lot_id == "L1"
                and interval.step_id == 3
                and interval.machine_id == "M1"
                for interval in result.processing_intervals
            )
        )
        config = result.provenance.simulation_config
        self.assertEqual(
            config["dedication_constraints"][0],
            {
                "dedication_id": "DED-MC06-1-3",
                "route_id": "MC06",
                "source_step_id": 1,
                "target_step_id": 3,
            },
        )
        self.assertEqual(config["dedication_runtime_schema_version"], "0.1.0")

    def test_bound_machine_busy_never_falls_back_to_idle_peer(self) -> None:
        _, scenario = build_mc06()
        result = Simulator(
            scenario,
            policy=AuditedFIFOPolicy(),
            seed=42,
            git_commit="test-commit",
        ).run()
        route_advance = next(
            row for row in result.key_trace()
            if row["event"] == "ROUTE_ADVANCE"
            and row.get("lot") == "L1"
            and row.get("step") == 3
        )
        target_dispatch = next(
            row for row in result.key_trace()
            if row["event"] == "DISPATCH"
            and row.get("lot") == "L1"
            and row.get("step") == 3
        )
        self.assertEqual(route_advance["time"], 10)
        self.assertEqual(target_dispatch, {
            "time": 15,
            "event": "DISPATCH",
            "lot": "L1",
            "machine": "M2",
            "step": 3,
        })

    def test_engine_raises_on_bound_machine_qualification_conflict(self) -> None:
        scenario = Scenario(
            scenario_id="DEDICATION_QUALIFICATION_CONFLICT",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1"), MachineSpec("M2"), MachineSpec("M3")),
            lots=(
                LotSpec(
                    "L1", 0,
                    (
                        OperationSpec(1, 1, ("M1",), route_id="R"),
                        OperationSpec(2, 1, ("M3",), route_id="R"),
                        OperationSpec(3, 1, ("M2",), route_id="R"),
                    ),
                ),
            ),
            dedication_constraints=(dedication_spec(route="R"),),
        )
        with self.assertRaisesRegex(
            DedicationRuntimeError, "Dedication 与 qualification 冲突"
        ):
            Simulator(scenario, seed=42, git_commit="test-commit").run()

    def test_initial_wip_unknown_history_is_audited_not_guessed(self) -> None:
        scenario = Scenario(
            scenario_id="INITIAL_WIP_UNKNOWN_DEDICATION",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1"), MachineSpec("M2"), MachineSpec("M3")),
            lots=(
                LotSpec(
                    "W1", 0,
                    (
                        OperationSpec(1, 1, ("M1", "M2"), route_id="R"),
                        OperationSpec(2, 1, ("M3",), route_id="R"),
                        OperationSpec(3, 1, ("M1", "M2"), route_id="R"),
                    ),
                    is_initial_wip=True,
                    initial_operation_index=2,
                ),
            ),
            dedication_constraints=(dedication_spec(route="R"),),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        self.assertEqual(result.dedication_metrics.initial_wip_missing_dedication, 1)
        self.assertEqual(len(result.initial_wip_dedication_audits), 1)
        self.assertEqual(result.initial_wip_dedication_audits[0].lot_id, "W1")
        self.assertEqual(result.dedication_records, ())
        self.assertEqual(result.active_dedication_bindings, ())
        self.assertIn(
            "DEDICATION_HISTORY_UNKNOWN",
            {row["event"] for row in result.key_trace()},
        )

    def test_dedicated_machine_still_runs_required_setup(self) -> None:
        scenario = Scenario(
            scenario_id="DEDICATION_SETUP",
            dataset_version="micro_cases@0.1.0",
            machines=(
                MachineSpec("M1", initial_setup="A"),
                MachineSpec("M2", initial_setup="A"),
                MachineSpec("M3"),
            ),
            lots=(
                LotSpec(
                    "A0", 0,
                    (OperationSpec(1, 1, ("M1",), route_id="AUX"),),
                ),
                LotSpec(
                    "L1", 0,
                    (
                        OperationSpec(1, 1, ("M1", "M2"), route_id="R"),
                        OperationSpec(2, 1, ("M3",), route_id="R"),
                        OperationSpec(
                            3, 1, ("M1", "M2"), route_id="R", required_setup="B"
                        ),
                    ),
                ),
            ),
            setup_transitions=(SetupTransition("A", "B", 3),),
            dedication_constraints=(dedication_spec(route="R"),),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        self.assertEqual(result.dedication_records[0].machine_id, "M2")
        setup = result.setup_intervals[0]
        target = next(
            interval for interval in result.processing_intervals
            if interval.lot_id == "L1" and interval.step_id == 3
        )
        self.assertEqual((setup.machine_id, setup.start, setup.finish), ("M2", 2, 5))
        self.assertEqual((target.machine_id, target.start), ("M2", 5))

    def test_dedication_wait_can_produce_cqt_violation(self) -> None:
        scenario = Scenario(
            scenario_id="DEDICATION_CQT",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1"), MachineSpec("M2"), MachineSpec("M3")),
            lots=(
                LotSpec(
                    "B1", 1,
                    (OperationSpec(1, 9, ("M1",), route_id="BLOCKER"),),
                ),
                LotSpec(
                    "L1", 0,
                    (
                        OperationSpec(1, 1, ("M1", "M2"), route_id="R"),
                        OperationSpec(2, 4, ("M3",), route_id="R"),
                        OperationSpec(3, 1, ("M1", "M2"), route_id="R"),
                    ),
                ),
            ),
            dedication_constraints=(dedication_spec(route="R"),),
            cqt_constraints=(CQTSpec("CQT-R-2-3", "R", 2, 3, 2),),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        record = result.cqt_records[0]
        self.assertEqual(result.dedication_records[0].machine_id, "M1")
        self.assertEqual((record.opened_at, record.closed_at), (5, 10))
        self.assertTrue(record.violation)
        self.assertEqual(record.excess_duration, 3)
        target = next(
            interval for interval in result.processing_intervals
            if interval.lot_id == "L1" and interval.step_id == 3
        )
        self.assertEqual(target.machine_id, "M1")

    def test_batch_formation_sees_only_dedication_feasible_lots(self) -> None:
        batch = BatchSpec(125, 150, 125, 100)
        lots = tuple(
            LotSpec(
                f"L{index}", 0,
                (
                    OperationSpec(1, 1, ("M2",), route_id="R"),
                    OperationSpec(
                        2, 2, ("M1", "M2"), route_id="R", batch_spec=batch
                    ),
                ),
            )
            for index in range(1, 6)
        )
        scenario = Scenario(
            scenario_id="DEDICATION_BATCH_FILTER",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1"), MachineSpec("M2")),
            lots=lots,
            dedication_constraints=(
                dedication_spec(route="R", source=1, target=2),
            ),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        self.assertEqual(len(result.batch_intervals), 1)
        self.assertEqual(result.batch_intervals[0].machine_id, "M2")
        self.assertEqual(result.batch_intervals[0].total_wafers, 125)

    def test_fixed_horizon_preserves_active_binding(self) -> None:
        _, scenario = build_mc06(horizon=12)
        result = Simulator(
            scenario,
            policy=AuditedFIFOPolicy(),
            seed=42,
            git_commit="test-commit",
        ).run()
        self.assertEqual(result.metrics.terminal_wip_lots, 2)
        self.assertEqual(len(result.active_dedication_bindings), 1)
        binding = result.active_dedication_bindings[0]
        self.assertEqual((binding.lot_id, binding.machine_id), ("L1", "M2"))
        self.assertEqual(binding.established_at, 0)
        self.assertEqual(result.dedication_records, ())

    def test_same_seed_reproduces_binding_trace_kpi_and_terminal_snapshot(self) -> None:
        _, scenario = build_mc06(horizon=12)
        first = Simulator(
            scenario, policy=AuditedFIFOPolicy(), seed=7, git_commit="test-commit"
        ).run()
        second = Simulator(
            scenario, policy=AuditedFIFOPolicy(), seed=7, git_commit="test-commit"
        ).run()
        self.assertEqual(first.trace_as_dicts(), second.trace_as_dicts())
        self.assertEqual(
            first.active_dedication_bindings,
            second.active_dedication_bindings,
        )
        self.assertEqual(first.dedication_metrics, second.dedication_metrics)
        self.assertEqual(first.metrics, second.metrics)

    def test_dedication_adds_no_event_calendar_type(self) -> None:
        self.assertNotIn("DEDICATION_CHECK", EventType.__members__)
        self.assertNotIn("DEDICATION_DEADLINE", EventType.__members__)


if __name__ == "__main__":
    unittest.main()
