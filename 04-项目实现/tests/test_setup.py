"""Setup duration resolver 的冻结语义测试。"""

from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    SetupTransition,
)
from fab_scheduler.simulation.engine import Simulator
from fab_scheduler.simulation.setup import (
    SetupDurationResolver,
    SetupResolutionError,
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


if __name__ == "__main__":
    unittest.main()
