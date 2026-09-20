"""MC01、MC02 的逐事件金标准测试。"""

from __future__ import annotations

import heapq
import json
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
)
from fab_scheduler.policies.fifo import FIFOPolicy
from fab_scheduler.simulation.engine import Simulator
from fab_scheduler.simulation.events import (
    EVENT_PRIORITIES,
    Event,
    EventType,
)
from fab_scheduler.simulation.random_streams import EntityRandomStreams


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "micro_cases"
    / "cases.json"
)


def load_case(case_id: str) -> dict:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return next(case for case in payload["cases"] if case["id"] == case_id)


def build_mc01() -> tuple[dict, Scenario]:
    case = load_case("MC01_ROUTE_ORDER")
    lot_data = case["lots"][0]
    operations = tuple(
        OperationSpec(
            step_id=operation["step"],
            processing_time=operation["process"],
            eligible_machines=(operation["machine"],),
            route_id="MC01",
        )
        for operation in lot_data["operations"]
    )
    machine_ids = sorted(
        {machine for operation in operations for machine in operation.eligible_machines}
    )
    scenario = Scenario(
        scenario_id=case["id"],
        dataset_version="micro_cases@0.1.0",
        machines=tuple(MachineSpec(machine_id) for machine_id in machine_ids),
        lots=(
            LotSpec(
                lot_id=lot_data["id"],
                release_time=lot_data["release"],
                quantity_wafers=lot_data["quantity_wafers"],
                operations=operations,
            ),
        ),
    )
    return case, scenario


def build_mc02() -> tuple[dict, Scenario]:
    case = load_case("MC02_DYNAMIC_RELEASE_FIFO")
    machine_id = case["machines"][0]["id"]
    lots = tuple(
        LotSpec(
            lot_id=lot["id"],
            release_time=lot["release"],
            quantity_wafers=lot["quantity_wafers"],
            operations=(
                OperationSpec(
                    step_id=1,
                    processing_time=lot["process"],
                    eligible_machines=(machine_id,),
                    route_id="MC02",
                ),
            ),
        )
        for lot in case["lots"]
    )
    scenario = Scenario(
        scenario_id=case["id"],
        dataset_version="micro_cases@0.1.0",
        machines=(MachineSpec(machine_id),),
        lots=lots,
    )
    return case, scenario


class MicroCaseSimulationTests(unittest.TestCase):
    def test_mc01_route_progression_trace_and_metrics(self) -> None:
        case, scenario = build_mc01()
        result = Simulator(
            scenario,
            policy=FIFOPolicy(),
            seed=42,
            git_commit="test-commit",
        ).run()

        self.assertEqual(result.key_trace(), case["expected_trace"])
        expected_intervals = case["expected"]["intervals"]
        actual_intervals = [
            {
                "lot": interval.lot_id,
                "step": interval.step_id,
                "start": interval.start,
                "finish": interval.finish,
            }
            for interval in result.processing_intervals
        ]
        self.assertEqual(actual_intervals, expected_intervals)
        self.assertEqual(result.completion_times, case["expected"]["completion"])
        self.assertEqual(
            {
                record.visit_index
                for record in result.trace
                if record.lot_id is not None
            },
            {0},
        )
        self.assertEqual(result.metrics.completed_lots, 1)
        self.assertEqual(result.metrics.terminal_wip_lots, 0)
        self.assertEqual(result.metrics.mean_cycle_time_completed, 30)
        self.assertAlmostEqual(result.metrics.throughput_lots_per_minute, 1 / 30)

    def test_mc02_dynamic_release_fifo_trace_and_metrics(self) -> None:
        case, scenario = build_mc02()
        result = Simulator(
            scenario,
            policy=FIFOPolicy(),
            seed=42,
            git_commit="test-commit",
        ).run()

        self.assertEqual(result.key_trace(), case["expected_trace"])
        expected_intervals = case["expected"]["intervals"]
        actual_intervals = [
            {
                "lot": interval.lot_id,
                "machine": interval.machine_id,
                "start": interval.start,
                "finish": interval.finish,
            }
            for interval in result.processing_intervals
        ]
        self.assertEqual(actual_intervals, expected_intervals)
        self.assertEqual(result.completion_times, {"L1": 10, "L2": 20})
        self.assertEqual(result.metrics.completed_lots, 2)
        self.assertEqual(result.metrics.released_lots, 2)
        self.assertEqual(result.metrics.completion_ratio, 1)
        self.assertEqual(result.metrics.mean_cycle_time_completed, 12.5)
        self.assertAlmostEqual(result.metrics.throughput_lots_per_minute, 0.1)

    def test_same_seed_and_input_reproduce_exact_trace(self) -> None:
        _, scenario = build_mc02()
        first = Simulator(scenario, seed=7, git_commit="test-commit").run()
        second = Simulator(scenario, seed=7, git_commit="test-commit").run()
        self.assertEqual(first.trace_as_dicts(), second.trace_as_dicts())
        self.assertEqual(first.metrics, second.metrics)
        self.assertEqual(first.processing_intervals, second.processing_intervals)

    def test_result_always_contains_required_provenance(self) -> None:
        _, scenario = build_mc01()
        result = Simulator(
            scenario,
            seed=42,
            git_commit="test-commit",
        ).run()
        provenance = result.provenance.to_dict()
        self.assertEqual(provenance["simulation_contract_version"], "0.1.0")
        self.assertEqual(provenance["dataset_version"], "micro_cases@0.1.0")
        self.assertEqual(provenance["git_commit"], "test-commit")
        self.assertEqual(provenance["seed"], 42)
        self.assertEqual(provenance["dispatch_policy"], "FIFO")
        self.assertEqual(provenance["termination_condition"], "until_all_complete")
        self.assertIsNone(provenance["horizon"])
        self.assertIn("simulation_config", provenance)
        self.assertEqual(
            provenance["simulation_config"]["scenario_id"],
            "MC01_ROUTE_ORDER",
        )
        self.assertEqual(
            provenance["simulation_config"]["lots"][0]["lot_id"],
            "L1",
        )

    def test_fixed_horizon_stops_with_terminal_wip(self) -> None:
        scenario = Scenario(
            scenario_id="FIXED_HORIZON",
            dataset_version="micro_cases@0.1.0",
            machines=(MachineSpec("M1"),),
            lots=(
                LotSpec(
                    lot_id="L1",
                    release_time=0,
                    operations=(
                        OperationSpec(
                            step_id=1,
                            processing_time=10,
                            eligible_machines=("M1",),
                        ),
                    ),
                ),
            ),
            termination_mode="fixed_horizon",
            horizon=5,
        )
        result = Simulator(
            scenario,
            seed=42,
            git_commit="test-commit",
        ).run()
        self.assertEqual(result.metrics.end_time, 5)
        self.assertEqual(result.metrics.released_lots, 1)
        self.assertEqual(result.metrics.completed_lots, 0)
        self.assertEqual(result.metrics.terminal_wip_lots, 1)
        self.assertNotIn("PROCESS_FINISH", {
            row["event"] for row in result.key_trace()
        })

    def test_event_heap_uses_contract_priority_then_sequence(self) -> None:
        events = [
            Event(
                time=5,
                priority=int(EVENT_PRIORITIES[EventType.LOT_RELEASE]),
                seq=0,
                event_type=EventType.LOT_RELEASE,
                entity_id="L1",
                payload={},
            ),
            Event(
                time=5,
                priority=int(EVENT_PRIORITIES[EventType.PROCESS_FINISH]),
                seq=2,
                event_type=EventType.PROCESS_FINISH,
                entity_id="M1",
                payload={},
            ),
            Event(
                time=5,
                priority=int(EVENT_PRIORITIES[EventType.PROCESS_FINISH]),
                seq=1,
                event_type=EventType.PROCESS_FINISH,
                entity_id="M2",
                payload={},
            ),
        ]
        heapq.heapify(events)
        first = heapq.heappop(events)
        second = heapq.heappop(events)
        third = heapq.heappop(events)
        self.assertEqual(first.priority, int(EVENT_PRIORITIES[EventType.PROCESS_FINISH]))
        self.assertEqual(first.seq, 1)
        self.assertEqual(second.seq, 2)
        self.assertEqual(third.event_type, EventType.LOT_RELEASE)

    def test_entity_random_streams_do_not_depend_on_call_order(self) -> None:
        streams = EntityRandomStreams(seed=42)
        first_a = streams.uniform("process", "L1:step1", 0, 1, 2)
        first_b = streams.uniform("process", "L2:step1", 0, 1, 2)
        second_b = streams.uniform("process", "L2:step1", 0, 1, 2)
        second_a = streams.uniform("process", "L1:step1", 0, 1, 2)
        self.assertEqual(first_a, second_a)
        self.assertEqual(first_b, second_b)
        self.assertNotEqual(first_a, first_b)


if __name__ == "__main__":
    unittest.main()
