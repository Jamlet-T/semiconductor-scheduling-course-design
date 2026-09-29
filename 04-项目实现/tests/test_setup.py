"""Setup duration resolver 的冻结语义测试。"""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (
    CalendarPMSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
    SetupMinimumRun,
    SetupTransition,
)
from fab_scheduler.simulation.engine import SimulationError, Simulator
from fab_scheduler.simulation.setup import (
    SetupDurationResolver,
    SetupResolutionError,
    SetupMinimumRunResolver,
)


def operation(
    required_setup: str | None,
    *,
    override: float | None = None,
) -> OperationSpec:
    return OperationSpec(
        step_id=1,
        processing_time=10,
        eligible_machines=("M1",),
        route_id="SETUP_TEST",
        required_setup=required_setup,
        setup_override_minutes=override,
    )


class SetupDurationResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = SetupDurationResolver(
            (
                SetupTransition("A", "B", 5),
                SetupTransition("", "B", 7),
                SetupTransition("", "C", 9),
            )
        )

    def test_no_requirement_needs_no_setup(self) -> None:
        self.assertEqual(
            self.resolver.resolve(
                current_setup="A",
                operation=operation(None),
            ),
            0,
        )

    def test_same_setup_needs_no_setup(self) -> None:
        self.assertEqual(
            self.resolver.resolve(
                current_setup="A",
                operation=operation("A"),
            ),
            0,
        )

    def test_operation_override_has_highest_duration_priority(self) -> None:
        self.assertEqual(
            self.resolver.resolve(
                current_setup="A",
                operation=operation("B", override=3),
            ),
            3,
        )

    def test_exact_directed_transition_precedes_initial_fallback(self) -> None:
        self.assertEqual(
            self.resolver.resolve(
                current_setup="A",
                operation=operation("B"),
            ),
            5,
        )

    def test_unknown_initial_setup_uses_empty_current_fallback(self) -> None:
        self.assertEqual(
            self.resolver.resolve(
                current_setup="",
                operation=operation("C"),
            ),
            9,
        )

    def test_missing_required_transition_raises_instead_of_zero_fallback(self) -> None:
        with self.assertRaisesRegex(
            SetupResolutionError,
            "无法解析 setup 时长",
        ):
            self.resolver.resolve(
                current_setup="A",
                operation=operation("D"),
            )

    def test_minrun_does_not_classify_operation_without_setup_requirement(self) -> None:
        resolver = SetupMinimumRunResolver((SetupMinimumRun("G", "A", 2),))
        self.assertTrue(
            resolver.allows_change(
                setup_group="G",
                current_setup="A",
                completed_lots=0,
                operation=operation(None),
            )
        )

    def test_minrun_configuration_rejects_invalid_or_duplicate_rules(self) -> None:
        with self.assertRaises(ValueError):
            SetupMinimumRun("G", "A", 0)
        with self.assertRaises(ValueError):
            MachineSpec("M1", setup_group="G", initial_setup_run_count=-1)
        with self.assertRaises(ValueError):
            MachineSpec("M1", setup_group="G", initial_setup_run_count=1)
        with self.assertRaises(ValueError):
            Scenario(
                scenario_id="DUPLICATE_MINRUN",
                dataset_version="micro_cases@0.1.0",
                machines=(MachineSpec("M1", setup_group="G"),),
                lots=(),
                setup_minimum_runs=(SetupMinimumRun("G", "A", 2), SetupMinimumRun("G", "A", 3)),
            )
        with self.assertRaises(ValueError):
            Scenario(
                scenario_id="UNKNOWN_MINRUN_GROUP",
                dataset_version="micro_cases@0.1.0",
                machines=(MachineSpec("M1", setup_group="G"),),
                lots=(),
                setup_minimum_runs=(SetupMinimumRun("TYPO", "A", 2),),
            )

    def test_fixed_horizon_keeps_partial_setup_separate_from_processing(self) -> None:
        scenario = Scenario(
            scenario_id="PARTIAL_SETUP",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1", initial_setup="A"),),
            lots=(
                LotSpec(
                    lot_id="L1",
                    release_time=0,
                    operations=(operation("B"),),
                ),
            ),
            setup_transitions=(SetupTransition("A", "B", 10),),
            termination_mode="fixed_horizon",
            horizon=5,
        )
        result = Simulator(
            scenario,
            seed=42,
            git_commit="test-commit",
        ).run()

        machine = result.machine_statistics["M1"]
        self.assertEqual(machine.setup_time, 5)
        self.assertEqual(machine.processing_time, 0)
        self.assertEqual(machine.idle_time, 0)
        self.assertEqual(machine.final_state, "SETTING_UP")
        self.assertEqual(machine.final_setup, "A")
        self.assertEqual(result.metrics.terminal_wip_lots, 1)
        self.assertEqual(len(result.setup_intervals), 0)
        self.assertNotIn(
            "PROCESS_START",
            {row["event"] for row in result.key_trace()},
        )

    def test_minrun_blocks_setup_change_until_same_setup_lots_finish(self) -> None:
        def op(lot_route: str, required_setup: str) -> OperationSpec:
            return OperationSpec(
                step_id=1,
                processing_time=1,
                eligible_machines=("M1",),
                route_id=lot_route,
                required_setup=required_setup,
            )

        scenario = Scenario(
            scenario_id="MINRUN_GATE",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1", initial_setup="A", setup_group="G"),),
            lots=(
                LotSpec("A1", 0, (op("A_ROUTE", "A"),)),
                LotSpec("A2", 0, (op("A_ROUTE", "A"),)),
                LotSpec("B1", 0, (op("B_ROUTE", "B"),)),
            ),
            setup_transitions=(SetupTransition("A", "B", 1),),
            setup_minimum_runs=(
                SetupMinimumRun("G", "A", 2),
                SetupMinimumRun("G", "B", 1),
            ),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.completion_times, {"A1": 1, "A2": 2, "B1": 4})
        runtime = result.provenance.simulation_config["setup_minrun_runtime"]
        self.assertEqual(runtime["initial_setup_count_unknown"], ["M1"])
        self.assertEqual(runtime["initial_setup_count_unknown_final"], [])
        setup_events = [
            (row["event"], row["time"])
            for row in result.key_trace()
            if row["event"] in {"SETUP_START", "SETUP_FINISH"}
        ]
        self.assertEqual(setup_events, [("SETUP_START", 2), ("SETUP_FINISH", 3)])

    def test_unknown_initial_count_blocks_change_and_remains_auditable(self) -> None:
        scenario = Scenario(
            scenario_id="MINRUN_UNKNOWN_INITIAL",
            dataset_version="micro_cases@0.1.0",
            machines=(
                MachineSpec("M1", initial_setup="A", setup_group="G"),
            ),
            lots=(LotSpec("B1", 0, (operation("B"),)),),
            setup_transitions=(SetupTransition("A", "B", 1),),
            setup_minimum_runs=(SetupMinimumRun("G", "A", 2),),
            termination_mode="fixed_horizon",
            horizon=2,
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.completion_times, {})
        self.assertNotIn(
            "SETUP_START",
            {row["event"] for row in result.key_trace()},
        )
        runtime = result.provenance.simulation_config["setup_minrun_runtime"]
        self.assertEqual(runtime["initial_setup_count_unknown"], ["M1"])
        self.assertEqual(runtime["initial_setup_count_unknown_final"], ["M1"])
        self.assertEqual(runtime["machines"]["M1"]["count_known"], False)
        self.assertEqual(runtime["machines"]["M1"]["completed_lots"], 0)

    def test_fixed_horizon_terminal_count_only_includes_completed_lots(self) -> None:
        def short_operation(required_setup: str) -> OperationSpec:
            return OperationSpec(
                step_id=1,
                processing_time=1,
                eligible_machines=("M1",),
                route_id="A_ROUTE",
                required_setup=required_setup,
            )

        scenario = Scenario(
            scenario_id="MINRUN_FIXED_HORIZON",
            dataset_version="micro_cases@0.1.0",
            machines=(
                MachineSpec(
                    "M1",
                    initial_setup="A",
                    setup_group="G",
                    initial_setup_run_count=0,
                ),
            ),
            lots=(
                LotSpec("A1", 0, (short_operation("A"),)),
                LotSpec("A2", 0, (short_operation("A"),)),
            ),
            setup_minimum_runs=(SetupMinimumRun("G", "A", 2),),
            termination_mode="fixed_horizon",
            horizon=1.5,
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.completion_times, {"A1": 1})
        runtime = result.provenance.simulation_config["setup_minrun_runtime"]
        self.assertEqual(runtime["machines"]["M1"]["completed_lots"], 1)

    def test_same_setup_seven_runs_and_no_setup_operation_do_not_switch_or_count(self) -> None:
        def short_operation(required_setup: str | None) -> OperationSpec:
            return OperationSpec(
                step_id=1,
                processing_time=1,
                eligible_machines=("M1",),
                route_id="MINRUN_ROUTE",
                required_setup=required_setup,
            )

        scenario = Scenario(
            scenario_id="MINRUN_SEVEN_AND_NONE",
            dataset_version="micro_cases@0.1.0",
            machines=(
                MachineSpec(
                    "M1",
                    initial_setup="A",
                    setup_group="G",
                    initial_setup_run_count=0,
                ),
            ),
            lots=tuple(
                [
                    LotSpec(f"A{index}", 0, (short_operation("A"),))
                    for index in range(1, 8)
                ]
                + [LotSpec("A0_NONE", 0, (short_operation(None),))]
                + [LotSpec("B1", 0, (short_operation("B"),))]
            ),
            setup_transitions=(SetupTransition("A", "B", 1),),
            setup_minimum_runs=(
                SetupMinimumRun("G", "A", 7),
                SetupMinimumRun("G", "B", 1),
            ),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(len(result.completion_times), 9)
        self.assertEqual(
            [row["time"] for row in result.key_trace() if row["event"] == "SETUP_START"],
            [8],
        )
        self.assertEqual(result.completion_times["B1"], 10)
        self.assertEqual(result.completion_times["A0_NONE"], 1)

    def test_zero_duration_setup_override_cannot_hide_setup_switch(self) -> None:
        scenario = Scenario(
            scenario_id="MINRUN_ZERO_SETUP",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1", initial_setup="A"),),
            lots=(LotSpec("B1", 0, (operation("B"),)),),
            setup_transitions=(SetupTransition("A", "B", 1),),
        )

        simulator = Simulator(scenario, seed=42, git_commit="test-commit")
        with patch.object(simulator._setup_resolver, "resolve", return_value=0.0):
            with self.assertRaisesRegex(SimulationError, "零时长隐式切换"):
                simulator.run()

    def test_failure_or_pm_resume_does_not_count_a_lot_twice(self) -> None:
        def op(lot_route: str, required_setup: str) -> OperationSpec:
            return OperationSpec(
                step_id=1,
                processing_time=1,
                eligible_machines=("M1",),
                route_id=lot_route,
                required_setup=required_setup,
            )

        for failure_specs, calendar_pm_specs in (
            (
                (MachineFailureSpec("M1", "scripted", (ScriptedFailureSpec(1.5, 1),)),),
                (),
            ),
            (
                (),
                (CalendarPMSpec("PM1", "M1", "scripted", (ScriptedPMSpec(1.5, 1),)),),
            ),
        ):
            with self.subTest(downtime="failure" if failure_specs else "pm"):
                scenario = Scenario(
                    scenario_id="MINRUN_RESUME",
                    dataset_version="micro_cases@0.1.0",
                    machines=(
                        MachineSpec(
                            "M1",
                            initial_setup="A",
                            setup_group="G",
                            initial_setup_run_count=1,
                        ),
                    ),
                    lots=(
                        LotSpec("B1", 0, (op("B_ROUTE", "B"),)),
                        LotSpec("A_C", 3.5, (op("C_ROUTE", "C"),)),
                        LotSpec("Z_B", 0, (op("B_ROUTE", "B"),)),
                    ),
                    setup_transitions=(
                        SetupTransition("A", "B", 1),
                        SetupTransition("B", "C", 1),
                    ),
                    setup_minimum_runs=(
                        SetupMinimumRun("G", "A", 1),
                        SetupMinimumRun("G", "B", 2),
                        SetupMinimumRun("G", "C", 1),
                    ),
                    failure_specs=failure_specs,
                    calendar_pm_specs=calendar_pm_specs,
                )
                result = Simulator(
                    scenario,
                    seed=42,
                    git_commit="test-commit",
                ).run()
                self.assertEqual(
                    result.completion_times,
                    {"B1": 3.0, "Z_B": 4.0, "A_C": 6.0},
                )
                self.assertEqual(
                    [
                        (row["event"], row["time"])
                        for row in result.key_trace()
                        if row["event"] == "SETUP_START"
                    ],
                    [("SETUP_START", 0), ("SETUP_START", 4.0)],
                )

    def test_minrun_setup_interruption_starts_new_run_only_after_resume(self) -> None:
        def op(route: str, setup: str) -> OperationSpec:
            return OperationSpec(
                step_id=1,
                processing_time=1,
                eligible_machines=("M1",),
                route_id=route,
                required_setup=setup,
            )

        for failure_specs, calendar_pm_specs in (
            ((MachineFailureSpec("M1", "scripted", (ScriptedFailureSpec(0.5, 1),)),), ()),
            ((), (CalendarPMSpec("PM1", "M1", "scripted", (ScriptedPMSpec(0.5, 1),)),)),
        ):
            with self.subTest(downtime="failure" if failure_specs else "pm"):
                scenario = Scenario(
                    scenario_id="MINRUN_SETUP_RESUME",
                    dataset_version="micro_cases@0.1.0",
                    machines=(MachineSpec("M1", initial_setup="A", setup_group="G", initial_setup_run_count=1),),
                    lots=(
                        LotSpec("B1", 0, (op("B_ROUTE", "B"),)),
                        LotSpec("B2", 0, (op("B_ROUTE", "B"),)),
                        LotSpec("C1", 2.5, (op("C_ROUTE", "C"),)),
                    ),
                    setup_transitions=(SetupTransition("A", "B", 1), SetupTransition("B", "C", 1)),
                    setup_minimum_runs=(SetupMinimumRun("G", "A", 1), SetupMinimumRun("G", "B", 2)),
                    failure_specs=failure_specs,
                    calendar_pm_specs=calendar_pm_specs,
                )
                result = Simulator(scenario, seed=42, git_commit="test-commit").run()
                self.assertEqual(result.completion_times, {"B1": 3, "B2": 4, "C1": 6})
                trace = result.key_trace()
                self.assertEqual(
                    [row["time"] for row in trace if row["event"] == "SETUP_FINISH"],
                    [2, 5],
                )
                self.assertEqual(
                    [row["time"] for row in trace if row["event"] == "SETUP_START"],
                    [0, 4],
                )


if __name__ == "__main__":
    unittest.main()
