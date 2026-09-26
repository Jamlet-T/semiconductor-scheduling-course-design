"""MC04 Batch 的金标准、边界和回归测试。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (
    BatchSpec,
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
)
from fab_scheduler.simulation.engine import Simulator
from fab_scheduler.simulation.events import EVENT_PRIORITIES, EventType


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "micro_cases"
    / "cases.json"
)


def load_mc04() -> dict:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return next(
        case for case in payload["cases"]
        if case["id"] == "MC04_BATCH_CAPACITY"
    )


def make_batch_spec(
    *,
    minimum: int = 125,
    maximum: int = 150,
    target: int = 150,
    max_wait: float = 5,
) -> BatchSpec:
    return BatchSpec(
        minimum_wafers=minimum,
        maximum_wafers=maximum,
        target_wafers=target,
        max_wait_minutes=max_wait,
    )


def make_batch_scenario(
    *,
    scenario_id: str,
    arrivals: list[float],
    routes: list[str] | None = None,
    quantities: list[int] | None = None,
    batch_spec: BatchSpec | None = None,
    processing_time: float = 20,
    horizon: float | None = None,
) -> Scenario:
    routes = routes or ["MC04"] * len(arrivals)
    quantities = quantities or [25] * len(arrivals)
    if not (len(arrivals) == len(routes) == len(quantities)):
        raise ValueError("arrivals/routes/quantities 长度必须一致")
    spec = batch_spec or make_batch_spec()
    lots = tuple(
        LotSpec(
            lot_id=f"L{index}",
            release_time=release,
            quantity_wafers=quantity,
            operations=(
                OperationSpec(
                    step_id=1,
                    processing_time=processing_time,
                    eligible_machines=("M1",),
                    route_id=route_id,
                    batch_spec=spec,
                ),
            ),
        )
        for index, (release, route_id, quantity) in enumerate(
            zip(arrivals, routes, quantities, strict=True),
            start=1,
        )
    )
    return Scenario(
        scenario_id=scenario_id,
        dataset_version="micro_cases@0.1.0",
        machines=(MachineSpec("M1"),),
        lots=lots,
        termination_mode=(
            "fixed_horizon" if horizon is not None else "until_all_complete"
        ),
        horizon=horizon,
    )


def build_mc04_variant(variant_id: str) -> tuple[dict, Scenario]:
    case = load_mc04()
    variant = next(
        item for item in case["variants"] if item["id"] == variant_id
    )
    batch = case["batch"]
    scenario = make_batch_scenario(
        scenario_id=f"{case['id']}:{variant_id}",
        arrivals=variant["arrivals"],
        quantities=[variant["lot_size_wafers"]] * len(variant["arrivals"]),
        batch_spec=BatchSpec(
            minimum_wafers=batch["minimum_wafers"],
            maximum_wafers=batch["maximum_wafers"],
            target_wafers=batch["target_wafers"],
            max_wait_minutes=batch["max_wait_minutes"],
            compatibility_rule=batch["compatibility"],
            member_selection_rule=batch["member_selection"],
        ),
        processing_time=batch["process"],
        horizon=variant.get("horizon"),
    )
    return variant, scenario


def interval_as_dict(interval) -> dict:
    return {
        "batch": interval.batch_id,
        "machine": interval.machine_id,
        "members": list(interval.member_lot_ids),
        "member_wafers": list(interval.member_wafers),
        "total_wafers": interval.total_wafers,
        "start": interval.start,
        "finish": interval.finish,
        "start_reason": interval.start_reason,
    }


class BatchRuntimeTests(unittest.TestCase):
    def test_mc04_golden_trace_interval_kpi_and_provenance(self) -> None:
        variant, scenario = build_mc04_variant("FIVE_LOTS_WAIT")
        result = Simulator(
            scenario,
            seed=42,
            git_commit="test-commit",
        ).run()

        self.assertEqual(result.key_trace(), variant["expected_trace"])
        self.assertEqual(len(result.batch_intervals), 1)
        self.assertEqual(
            interval_as_dict(result.batch_intervals[0]),
            variant["expected"]["batch_interval"],
        )
        self.assertEqual(
            result.completion_times,
            variant["expected"]["completion"],
        )
        self.assertEqual(
            result.metrics.completed_lots,
            variant["expected"]["completed_lots"],
        )
        self.assertEqual(
            result.metrics.mean_cycle_time_completed,
            variant["expected"]["mean_cycle_time_completed"],
        )
        self.assertEqual(
            result.metrics.throughput_lots_per_minute,
            variant["expected"]["throughput_lots_per_minute"],
        )
        self.assertEqual(
            result.metrics.terminal_wip_lots,
            variant["expected"]["terminal_wip_lots"],
        )
        self.assertEqual(
            result.metrics.end_time,
            variant["expected"]["end_time"],
        )
        self.assertEqual(result.machine_statistics["M1"].processing_time, 20)
        self.assertEqual(len(result.processing_intervals), 0)

        config = result.provenance.simulation_config
        stored = config["lots"][0]["operations"][0]["batch_spec"]
        self.assertEqual(stored["minimum_wafers"], 125)
        self.assertEqual(stored["maximum_wafers"], 150)
        self.assertEqual(stored["target_wafers"], 150)
        self.assertEqual(stored["max_wait_minutes"], 5)
        self.assertEqual(stored["compatibility_rule"], "crit_sameroutestep")
        self.assertEqual(
            stored["member_selection_rule"],
            "fifo_queue_time_lot_id",
        )

    def test_below_minimum_never_starts_even_after_timeout(self) -> None:
        variant, scenario = build_mc04_variant("FOUR_LOTS")
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.key_trace(), variant["expected_trace"])
        self.assertEqual(result.batch_intervals, ())
        self.assertEqual(result.active_batches, ())
        self.assertEqual(result.metrics.completed_lots, 0)
        self.assertEqual(result.metrics.terminal_wip_lots, 4)
        self.assertNotIn(
            "BATCH_TIMEOUT",
            {record.event_type for record in result.trace},
        )

    def test_exact_minimum_waits_then_timeout_starts_legal_batch(self) -> None:
        _, scenario = build_mc04_variant("FIVE_LOTS_WAIT")
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        interval = result.batch_intervals[0]
        self.assertEqual(interval.start, 5)
        self.assertEqual(interval.total_wafers, 125)
        self.assertEqual(interval.start_reason, "TIMEOUT_REACHED")
        self.assertFalse(
            any(
                row["event"] == "BATCH_START" and row["time"] == 0
                for row in result.key_trace()
            )
        )

    def test_target_reached_starts_immediately(self) -> None:
        _, scenario = build_mc04_variant("SIX_LOTS_FULL")
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(len(result.batch_intervals), 1)
        interval = result.batch_intervals[0]
        self.assertEqual(interval.start, 0)
        self.assertEqual(interval.finish, 20)
        self.assertEqual(interval.total_wafers, 150)
        self.assertEqual(interval.start_reason, "TARGET_REACHED")
        self.assertEqual(result.metrics.completed_lots, 6)

    def test_max_capacity_selects_first_six_and_leaves_seventh_queued(self) -> None:
        scenario = make_batch_scenario(
            scenario_id="BATCH_MAX_CAPACITY",
            arrivals=[0] * 7,
            horizon=1,
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(len(result.active_batches), 1)
        active = result.active_batches[0]
        self.assertEqual(
            active.member_lot_ids,
            ("L1", "L2", "L3", "L4", "L5", "L6"),
        )
        self.assertEqual(active.total_wafers, 150)
        self.assertEqual(result.metrics.terminal_wip_lots, 7)
        self.assertNotIn("L7", active.member_lot_ids)

    def test_incompatible_routes_cannot_be_mixed_to_reach_minimum(self) -> None:
        scenario = make_batch_scenario(
            scenario_id="BATCH_INCOMPATIBLE",
            arrivals=[0] * 6,
            routes=["R1"] * 3 + ["R2"] * 3,
            horizon=10,
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.batch_intervals, ())
        self.assertEqual(result.active_batches, ())
        self.assertEqual(result.metrics.terminal_wip_lots, 6)

    def test_timeout_wakes_des_without_any_other_event(self) -> None:
        _, scenario = build_mc04_variant("FIVE_LOTS_WAIT")
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        events_at_five = [
            record.event_type
            for record in result.trace
            if record.sim_time == 5
        ]
        self.assertIn("BATCH_TIMEOUT", events_at_five)
        self.assertIn("BATCH_START", events_at_five)

    def test_stale_timeout_after_early_target_has_no_side_effect(self) -> None:
        scenario = make_batch_scenario(
            scenario_id="BATCH_STALE_TIMEOUT",
            arrivals=[0, 0, 0, 0, 0, 5],
            batch_spec=make_batch_spec(max_wait=20),
            processing_time=10,
            horizon=25,
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(len(result.batch_intervals), 1)
        self.assertEqual(result.batch_intervals[0].start, 5)
        self.assertEqual(
            result.batch_intervals[0].start_reason,
            "TARGET_REACHED",
        )
        self.assertEqual(
            sum(
                record.event_type == "BATCH_START"
                for record in result.trace
            ),
            1,
        )
        self.assertEqual(
            sum(
                record.event_type == "BATCH_TIMEOUT_STALE"
                for record in result.trace
            ),
            1,
        )

    def test_release_at_timeout_is_seen_before_batch_decision(self) -> None:
        scenario = make_batch_scenario(
            scenario_id="BATCH_RELEASE_AT_TIMEOUT",
            arrivals=[0, 0, 0, 0, 0, 20],
            batch_spec=make_batch_spec(max_wait=20),
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        at_twenty = [
            record for record in result.trace if record.sim_time == 20
        ]
        release = next(
            record for record in at_twenty
            if record.event_type == "LOT_RELEASE"
        )
        timeout = next(
            record for record in at_twenty
            if record.event_type == "BATCH_TIMEOUT"
        )
        start = next(
            record for record in at_twenty
            if record.event_type == "BATCH_START"
        )
        self.assertLess(release.priority, timeout.priority)
        self.assertLess(timeout.priority, start.priority)
        self.assertEqual(start.batch_total_wafers, 150)
        self.assertEqual(start.batch_start_reason, "TARGET_REACHED")

    def test_fixed_horizon_preserves_active_batch_and_terminal_wip(self) -> None:
        scenario = make_batch_scenario(
            scenario_id="BATCH_FIXED_HORIZON",
            arrivals=[10] * 6,
            horizon=20,
        )
        result = Simulator(scenario, seed=42, git_commit="test-commit").run()

        self.assertEqual(result.batch_intervals, ())
        self.assertEqual(len(result.active_batches), 1)
        active = result.active_batches[0]
        self.assertEqual(active.start, 10)
        self.assertEqual(active.scheduled_finish, 30)
        self.assertEqual(result.metrics.completed_lots, 0)
        self.assertEqual(result.metrics.terminal_wip_lots, 6)
        self.assertEqual(result.machine_statistics["M1"].processing_time, 10)
        self.assertEqual(
            result.machine_statistics["M1"].active_batch_id,
            "BATCH-000001",
        )
        self.assertNotIn(
            "BATCH_FINISH",
            {record.event_type for record in result.trace},
        )

    def test_batch_membership_identity_trace_and_kpi_are_deterministic(
        self,
    ) -> None:
        _, scenario = build_mc04_variant("FIVE_LOTS_WAIT")
        first = Simulator(scenario, seed=7, git_commit="test-commit").run()
        second = Simulator(scenario, seed=7, git_commit="test-commit").run()

        self.assertEqual(first.trace_as_dicts(), second.trace_as_dicts())
        self.assertEqual(first.batch_intervals, second.batch_intervals)
        self.assertEqual(first.metrics, second.metrics)
        self.assertEqual(first.machine_statistics, second.machine_statistics)

    def test_batch_event_priorities_follow_contract(self) -> None:
        self.assertEqual(
            EVENT_PRIORITIES[EventType.BATCH_FINISH],
            EVENT_PRIORITIES[EventType.PROCESS_FINISH],
        )
        self.assertLess(
            EVENT_PRIORITIES[EventType.LOT_RELEASE],
            EVENT_PRIORITIES[EventType.BATCH_TIMEOUT],
        )
        self.assertLess(
            EVENT_PRIORITIES[EventType.BATCH_TIMEOUT],
            EVENT_PRIORITIES[EventType.DISPATCH_BARRIER],
        )


if __name__ == "__main__":
    unittest.main()
