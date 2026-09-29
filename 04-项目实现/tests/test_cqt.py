"""MC05 CQT 的金标准、边界和回归测试。"""

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
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    SetupTransition,
)
from fab_scheduler.simulation.cqt import CQTRuntime, CQTRuntimeError
from fab_scheduler.simulation.engine import Simulator
from fab_scheduler.simulation.events import EventType


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "micro_cases"
    / "cases.json"
)


def load_mc05() -> dict:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return next(
        case for case in payload["cases"]
        if case["id"] == "MC05_CROSS_STEP_CQT"
    )


def cqt_spec(
    *,
    constraint_id: str = "CQT-MAIN-1-3",
    source: int = 1,
    target: int = 3,
    limit: float = 8,
    route: str = "MC05",
) -> CQTSpec:
    return CQTSpec(
        constraint_id=constraint_id,
        route_id=route,
        source_step_id=source,
        target_step_id=target,
        max_duration_minutes=limit,
    )


def build_mc05_variant(variant_index: int) -> tuple[dict, Scenario]:
    case = load_mc05()
    variant = case["variants"][variant_index]
    blocker_finish = variant["expected_step_3_start"]
    main = LotSpec(
        lot_id="L1",
        release_time=0,
        operations=tuple(
            OperationSpec(
                step_id=item["step"],
                processing_time=item["process"],
                eligible_machines=(f"M{item['step']}",),
                route_id="MC05",
            )
            for item in case["operations"]
        ),
    )
    blocker = LotSpec(
        lot_id="B1",
        release_time=0,
        operations=(
            OperationSpec(
                step_id=1,
                processing_time=blocker_finish,
                eligible_machines=("M3",),
                route_id="BLOCKER",
            ),
        ),
    )
    scenario = Scenario(
        scenario_id=f"{case['id']}:V{variant_index + 1}",
        dataset_version="micro_cases@0.1.0",
        machines=tuple(MachineSpec(f"M{index}") for index in range(1, 4)),
        lots=(main, blocker),
        cqt_constraints=(cqt_spec(limit=case["constraint"]["limit"]),),
    )
    return variant, scenario


def fixed_horizon_scenario(horizon: float) -> Scenario:
    return Scenario(
        scenario_id=f"CQT_OPEN_H{horizon}",
        dataset_version="micro_cases@0.1.0",
        machines=(MachineSpec("M1"), MachineSpec("M2"), MachineSpec("M3")),
        lots=(
            LotSpec(
                lot_id="L1",
                release_time=0,
                operations=(
                    OperationSpec(1, 10, ("M1",), route_id="MC05"),
                    OperationSpec(2, 100, ("M2",), route_id="MC05"),
                    OperationSpec(3, 1, ("M3",), route_id="MC05"),
                ),
            ),
        ),
        cqt_constraints=(cqt_spec(limit=20),),
        termination_mode="fixed_horizon",
        horizon=horizon,
    )


class CQTDomainAndRuntimeTests(unittest.TestCase):
    def test_spec_requires_forward_cross_step_and_unique_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "target step"):
            cqt_spec(source=3, target=3)
        with self.assertRaisesRegex(ValueError, "不能重复"):
            Scenario(
                scenario_id="DUPLICATE_CQT",
                dataset_version="micro_cases@0.1.0",
                machines=(MachineSpec("M1"),),
                lots=(
                    LotSpec(
                        "L1",
                        0,
                        (
                            OperationSpec(1, 1, ("M1",), route_id="R"),
                            OperationSpec(2, 1, ("M1",), route_id="R"),
                        ),
                    ),
                ),
                cqt_constraints=(
                    cqt_spec(constraint_id="C", source=1, target=2, route="R"),
                    cqt_spec(constraint_id="C", source=1, target=2, route="R"),
                ),
            )

    def test_multiple_active_clocks_do_not_overwrite_and_query_slack_risk(self) -> None:
        runtime = CQTRuntime(
            (
                cqt_spec(constraint_id="C1", target=3, limit=10),
                cqt_spec(constraint_id="C2", target=4, limit=20),
            )
        )
        opened = runtime.open_for_source(
            lot_id="L1", route_id="MC05", step_id=1, opened_at=10
        )
        self.assertEqual([clock.constraint_id for clock in opened], ["C1", "C2"])
        self.assertEqual(len(runtime.active_clocks), 2)
        state = runtime.query(
            lot_id="L1", constraint_id="C1", at_time=25
        )
        self.assertEqual(state.slack, -5)
        self.assertEqual(state.risk, 1.5)
        self.assertTrue(state.overdue)

    def test_duplicate_open_and_missing_close_are_explicit_errors(self) -> None:
        runtime = CQTRuntime((cqt_spec(),))
        runtime.open_for_source(
            lot_id="L1", route_id="MC05", step_id=1, opened_at=10
        )
        with self.assertRaisesRegex(CQTRuntimeError, "重复开启"):
            runtime.open_for_source(
                lot_id="L1", route_id="MC05", step_id=1, opened_at=11
            )
        empty = CQTRuntime((cqt_spec(),))
        with self.assertRaisesRegex(CQTRuntimeError, "关闭不存在"):
            empty.close_for_target(
                lot_id="L1", route_id="MC05", step_id=3, closed_at=20
            )

    def test_exact_deadline_is_satisfied(self) -> None:
        runtime = CQTRuntime((cqt_spec(limit=8),))
        runtime.open_for_source(
            lot_id="L1", route_id="MC05", step_id=1, opened_at=10
        )
        record = runtime.close_for_target(
            lot_id="L1", route_id="MC05", step_id=3, closed_at=18
        )[0]
        self.assertEqual(record.actual_duration, 8)
        self.assertEqual(record.slack, 0)
        self.assertFalse(record.violation)
        self.assertEqual(record.excess_duration, 0)


class CQTIntegrationTests(unittest.TestCase):
    def test_mc05_within_limit_golden_trace_and_metrics(self) -> None:
        variant, scenario = build_mc05_variant(0)
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.key_trace(), variant["expected_trace"])
        record = result.cqt_records[0]
        self.assertEqual(record.opened_at, 10)
        self.assertEqual(record.closed_at, 17)
        self.assertEqual(record.actual_duration, variant["elapsed"])
        self.assertEqual(record.limit, 8)
        self.assertEqual(record.slack, 1)
        self.assertFalse(record.violation)
        self.assertEqual(result.completion_times, {"B1": 17, "L1": 27})
        self.assertEqual(result.cqt_metrics.closed_cqt_count, 1)
        self.assertEqual(result.cqt_metrics.cqt_violation_count, 0)
        self.assertEqual(result.cqt_metrics.open_cqt_count, 0)
        self.assertEqual(result.metrics.completed_lots, 2)
        self.assertEqual(result.metrics.mean_cycle_time_completed, 22)
        self.assertEqual(
            result.provenance.simulation_config["cqt_constraints"][0],
            {
                "constraint_id": "CQT-MAIN-1-3",
                "route_id": "MC05",
                "source_step_id": 1,
                "target_step_id": 3,
                "max_duration_minutes": 8,
            },
        )
        self.assertEqual(
            result.provenance.simulation_config["cqt_runtime_schema_version"],
            "0.1.0",
        )

    def test_mc05_violation_is_recorded_but_does_not_block_target(self) -> None:
        variant, scenario = build_mc05_variant(1)
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.key_trace(), variant["expected_trace"])
        record = result.cqt_records[0]
        self.assertEqual(record.opened_at, 10)
        self.assertEqual(record.closed_at, 19)
        self.assertEqual(record.actual_duration, variant["elapsed"])
        self.assertEqual(record.slack, -1)
        self.assertTrue(record.violation)
        self.assertEqual(record.excess_duration, 1)
        self.assertEqual(result.completion_times["L1"], 29)
        self.assertEqual(result.cqt_metrics.cqt_violation_count, 1)
        self.assertEqual(result.cqt_metrics.total_cqt_excess, 1)
        self.assertEqual(result.cqt_metrics.max_cqt_excess, 1)
        self.assertIn(
            "PROCESS_START",
            {
                row["event"] for row in result.key_trace()
                if row.get("lot") == "L1" and row.get("step") == 3
            },
        )

    def test_cross_step_duration_includes_middle_processing_and_queueing(self) -> None:
        _, scenario = build_mc05_variant(0)
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        middle = next(
            interval for interval in result.processing_intervals
            if interval.lot_id == "L1" and interval.step_id == 2
        )
        record = result.cqt_records[0]
        self.assertEqual((middle.start, middle.finish), (10, 15))
        self.assertEqual(record.actual_duration, 5 + 2)

    def test_fixed_horizon_preserves_open_safe_clock(self) -> None:
        result = Simulator(
            fixed_horizon_scenario(20), seed=42, git_commit="test-commit"
        ).run()
        self.assertEqual(result.cqt_records, ())
        self.assertEqual(len(result.open_cqt_clocks), 1)
        snapshot = result.open_cqt_clocks[0]
        self.assertEqual(snapshot.opened_at, 10)
        self.assertEqual(snapshot.deadline, 30)
        self.assertEqual(snapshot.elapsed, 10)
        self.assertEqual(snapshot.slack, 10)
        self.assertFalse(snapshot.overdue)
        self.assertEqual(snapshot.exposure, 0)
        self.assertEqual(result.cqt_metrics.open_cqt_count, 1)
        self.assertEqual(result.cqt_metrics.overdue_open_cqt_count, 0)
        self.assertEqual(result.cqt_metrics.terminal_cqt_exposure, 0)

    def test_fixed_horizon_preserves_open_overdue_clock_and_exposure(self) -> None:
        result = Simulator(
            fixed_horizon_scenario(40), seed=42, git_commit="test-commit"
        ).run()
        snapshot = result.open_cqt_clocks[0]
        self.assertEqual(snapshot.deadline, 30)
        self.assertEqual(snapshot.elapsed, 30)
        self.assertEqual(snapshot.slack, -10)
        self.assertTrue(snapshot.overdue)
        self.assertEqual(snapshot.exposure, 10)
        self.assertEqual(result.cqt_metrics.cqt_violation_count, 0)
        self.assertEqual(result.cqt_metrics.overdue_open_cqt_count, 1)
        self.assertEqual(result.cqt_metrics.terminal_cqt_exposure, 10)

    def test_setup_between_source_finish_and_target_start_counts_in_cqt(self) -> None:
        scenario = Scenario(
            scenario_id="CQT_SETUP_DELAY",
            dataset_version="micro_cases@0.1.0",
            machines=(
                MachineSpec("M1"),
                MachineSpec("M2", initial_setup="A"),
            ),
            lots=(
                LotSpec(
                    "L1",
                    0,
                    (
                        OperationSpec(1, 10, ("M1",), route_id="R"),
                        OperationSpec(
                            2,
                            1,
                            ("M2",),
                            route_id="R",
                            required_setup="B",
                        ),
                    ),
                ),
            ),
            setup_transitions=(SetupTransition("A", "B", 5),),
            cqt_constraints=(
                cqt_spec(source=1, target=2, limit=6, route="R"),
            ),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        record = result.cqt_records[0]
        self.assertEqual(record.opened_at, 10)
        self.assertEqual(record.closed_at, 15)
        self.assertEqual(record.actual_duration, 5)
        self.assertEqual(result.setup_intervals[0].start, 10)
        self.assertEqual(result.setup_intervals[0].finish, 15)

    def test_batch_member_process_events_open_and_close_cqt(self) -> None:
        batch = BatchSpec(125, 150, 125, 0)
        lots = tuple(
            LotSpec(
                f"L{index}",
                0,
                (
                    OperationSpec(
                        1, 5, ("M1",), route_id="R", batch_spec=batch
                    ),
                    OperationSpec(2, 1, ("M2",), route_id="R"),
                ),
            )
            for index in range(1, 6)
        )
        scenario = Scenario(
            scenario_id="CQT_BATCH_HOOKS",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1"), MachineSpec("M2")),
            lots=lots,
            cqt_constraints=(
                cqt_spec(source=1, target=2, limit=20, route="R"),
            ),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        self.assertEqual(len(result.cqt_records), 5)
        self.assertEqual(
            {record.opened_at for record in result.cqt_records}, {5}
        )
        self.assertEqual(
            [record.closed_at for record in result.cqt_records],
            [5, 6, 7, 8, 9],
        )

    def test_no_cqt_deadline_calendar_event_is_introduced(self) -> None:
        self.assertNotIn("CQT_DEADLINE", EventType.__members__)
        _, scenario = build_mc05_variant(1)
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()
        self.assertNotIn(
            "CQT_DEADLINE", {record.event_type for record in result.trace}
        )

    def test_same_seed_reproduces_cqt_trace_records_and_metrics(self) -> None:
        _, scenario = build_mc05_variant(1)
        first = Simulator(scenario, seed=7, git_commit="test-commit").run()
        second = Simulator(scenario, seed=7, git_commit="test-commit").run()
        self.assertEqual(first.trace_as_dicts(), second.trace_as_dicts())
        self.assertEqual(first.cqt_records, second.cqt_records)
        self.assertEqual(first.open_cqt_clocks, second.open_cqt_clocks)
        self.assertEqual(first.cqt_metrics, second.cqt_metrics)
        self.assertEqual(first.metrics, second.metrics)


if __name__ == "__main__":
    unittest.main()
