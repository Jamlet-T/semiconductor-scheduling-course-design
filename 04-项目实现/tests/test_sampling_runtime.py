"""受限 per-lot sampling runtime 的集成契约测试。"""

from __future__ import annotations

import unittest

from fab_scheduler.domain import (
    BatchSpec,
    CQTSpec,
    DedicationSpec,
    LotSpec,
    MachineSpec,
    OperationSpec,
    ReleaseTemplateSpec,
    Scenario,
    TimeDistributionSpec,
    TransportSpec,
    WaferPMSpec,
)
from fab_scheduler.policies import FIFOPolicy, SPTPolicy
from fab_scheduler.evaluation.audit import audit_result_invariants
from fab_scheduler.simulation import Simulator


def op(
    step: int,
    duration: float = 1.0,
    *,
    machine: str = "M1",
    sample: float | None = None,
    **kwargs: object,
) -> OperationSpec:
    return OperationSpec(
        step,
        duration,
        (machine,),
        route_id="R",
        sample_percent=sample,
        **kwargs,
    )


def make_scenario(
    operations: tuple[OperationSpec, ...],
    *,
    lot_id: str = "L1",
    initial_wip: bool = False,
    machines: tuple[MachineSpec, ...] = (MachineSpec("M1"),),
    **kwargs: object,
) -> Scenario:
    return Scenario(
        "SAMPLING_TEST",
        "micro@sampling-test",
        machines,
        (LotSpec(lot_id, 0, operations, is_initial_wip=initial_wip),),
        **kwargs,
    )


class SamplingRuntimeTests(unittest.TestCase):
    def test_percent_100_records_decision_without_sampling_ledger(self) -> None:
        result = Simulator(make_scenario((op(1, sample=100),)), seed=1).run()
        decisions = [item for item in result.trace if item.event_type == "SAMPLING_DECISION"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].sampling_percent, 100)
        self.assertIsNone(decisions[0].sampling_draw)
        self.assertTrue(decisions[0].sampling_performed)
        self.assertFalse(any(item.stream_name == "sampling" for item in result.random_sample_ledger))

    def test_percent_less_than_100_can_perform_and_skip(self) -> None:
        performed = Simulator(make_scenario((op(1, sample=80),)), seed=1).run()
        skipped = Simulator(make_scenario((op(1, sample=0.1),)), seed=1).run()
        self.assertTrue(next(item for item in performed.trace if item.event_type == "SAMPLING_DECISION").sampling_performed)
        self.assertEqual(performed.metrics.completed_lots, 1)
        decision = next(item for item in skipped.trace if item.event_type == "SAMPLING_DECISION")
        self.assertFalse(decision.sampling_performed)
        self.assertIn("OPERATION_SKIPPED", {item.event_type for item in skipped.trace})
        self.assertEqual(skipped.metrics.completed_lots, 1)
        self.assertEqual(len([item for item in skipped.random_sample_ledger if item.stream_name == "sampling"]), 1)

    def test_continuous_skip_terminal_skip_and_release_queue_time(self) -> None:
        result = Simulator(
            make_scenario((op(1, sample=0.1), op(2, sample=0.1))),
            seed=1,
        ).run()
        self.assertEqual(
            [item.step_id for item in result.trace if item.event_type == "OPERATION_SKIPPED"],
            [1, 2],
        )
        self.assertEqual(result.completion_times["L1"], 0)
        self.assertEqual(result.metrics.end_time, 0)

    def test_initial_wip_is_sampled_after_release(self) -> None:
        result = Simulator(
            make_scenario((op(1, sample=100),), initial_wip=True),
            seed=7,
        ).run()
        release = next(item for item in result.trace if item.event_type == "LOT_RELEASE")
        decision = next(item for item in result.trace if item.event_type == "SAMPLING_DECISION")
        self.assertEqual(release.sim_time, 0)
        self.assertEqual(decision.sim_time, 0)
        self.assertEqual(result.completion_times["L1"], 1)

    def test_skipped_intermediate_step_gets_one_direct_transport(self) -> None:
        machines = (MachineSpec("M1", location_id="A"), MachineSpec("M2", location_id="B"))
        operations = (
            op(1, machine="M1"),
            op(2, machine="M1", sample=0.1),
            op(3, machine="M2", sample=100),
        )
        result = Simulator(
            make_scenario(
                operations,
                machines=machines,
                transport_specs=(TransportSpec("A", "B", TimeDistributionSpec("constant", 5)),),
            ),
            seed=1,
        ).run()
        self.assertEqual(len(result.transport_intervals), 1)
        self.assertEqual((result.transport_intervals[0].from_step_id, result.transport_intervals[0].to_step_id), (1, 3))
        self.assertNotIn(2, [item.to_step_id for item in result.transport_intervals])

    def test_sampling_common_random_numbers_survive_policy_order(self) -> None:
        operations_a = (op(1, 10, sample=50), op(2, 1, sample=50))
        operations_b = (op(1, 1, sample=50), op(2, 1, sample=50))
        scenario = Scenario(
            "SAMPLING_CRN",
            "micro@sampling-test",
            (MachineSpec("M1"),),
            (
                LotSpec("A", 0, operations_a),
                LotSpec("B", 0, operations_b),
            ),
        )
        fifo = Simulator(scenario, policy=FIFOPolicy(), seed=42).run()
        spt = Simulator(scenario, policy=SPTPolicy(), seed=42).run()
        left = {item.identity: item.value for item in fifo.random_sample_ledger if item.stream_name == "sampling"}
        right = {item.identity: item.value for item in spt.random_sample_ledger if item.stream_name == "sampling"}
        self.assertEqual(left, right)

    def test_fixed_horizon_keeps_sampling_and_terminal_wip_semantics(self) -> None:
        result = Simulator(
            make_scenario((op(1, 10, sample=100),), termination_mode="fixed_horizon", horizon=5),
            seed=1,
        ).run()
        self.assertEqual(result.metrics.end_time, 5)
        self.assertEqual(result.metrics.completed_lots, 0)
        self.assertEqual(len([item for item in result.trace if item.event_type == "SAMPLING_DECISION"]), 1)

    def test_sampling_rejects_unsupported_profiles_and_constraint_endpoints(self) -> None:
        with self.assertRaises(ValueError):
            op(1, sample=50, processing_basis="per_piece")
        with self.assertRaises(ValueError):
            op(1, sample=50, required_setup="S1")
        with self.assertRaises(ValueError):
            op(1, sample=50, part_interval_minutes=1)
        with self.assertRaises(ValueError):
            op(1, sample=50, batch_interval_minutes=1)
        with self.assertRaises(ValueError):
            make_scenario((op(1, sample=50), op(2)), cqt_constraints=(CQTSpec("C", "R", 1, 2, 10),))
        with self.assertRaises(ValueError):
            make_scenario((op(1, sample=50), op(2)), dedication_constraints=(DedicationSpec("D", "R", 1, 2),))
        with self.assertRaises(ValueError):
            op(1, sample=0)

    def test_percent_100_cqt_target_closes_clock_at_process_start(self) -> None:
        scenario = make_scenario(
            (op(1), op(2, sample=100)),
            cqt_constraints=(CQTSpec("C", "R", 1, 2, 10),),
        )
        result = Simulator(scenario, seed=3).run()
        self.assertEqual(len(result.cqt_records), 1)
        self.assertEqual(result.cqt_records[0].target_step_id, 2)
        self.assertFalse(result.open_cqt_clocks)
        self.assertEqual(
            [item.sampling_performed for item in result.trace
             if item.event_type == "SAMPLING_DECISION"],
            [True],
        )
        self.assertFalse(
            [item for item in result.random_sample_ledger
             if item.stream_name == "sampling"]
        )

    def test_percent_100_cqt_target_matches_unsampled_physics(self) -> None:
        constraints = (CQTSpec("C", "R", 1, 2, 10),)
        always = Simulator(
            make_scenario((op(1), op(2, sample=100)), cqt_constraints=constraints),
            seed=3,
        ).run()
        unconfigured = Simulator(
            make_scenario((op(1), op(2)), cqt_constraints=constraints),
            seed=3,
        ).run()
        self.assertEqual(always.processing_intervals, unconfigured.processing_intervals)
        self.assertEqual(always.cqt_records, unconfigured.cqt_records)
        self.assertEqual(always.metrics, unconfigured.metrics)
        self.assertEqual(always.pm_count, unconfigured.pm_count)
        self.assertEqual(
            [item.event_type for item in always.trace
             if item.event_type.startswith("SAMPLING")],
            ["SAMPLING_DECISION"],
        )

    def test_percent_100_cqt_source_matches_unsampled_clock(self) -> None:
        constraints = (CQTSpec("C", "R", 1, 2, 10),)
        always = Simulator(
            make_scenario((op(1, sample=100), op(2)), cqt_constraints=constraints),
            seed=3,
        ).run()
        unconfigured = Simulator(
            make_scenario((op(1), op(2)), cqt_constraints=constraints),
            seed=3,
        ).run()
        self.assertEqual(always.processing_intervals, unconfigured.processing_intervals)
        self.assertEqual(always.cqt_records, unconfigured.cqt_records)
        self.assertEqual(always.metrics, unconfigured.metrics)
        self.assertEqual(
            [item.event_type for item in always.trace
             if item.event_type.startswith("SAMPLING")],
            ["SAMPLING_DECISION"],
        )

    def test_skipped_operation_does_not_increment_wafer_pm_counter(self) -> None:
        pm = WaferPMSpec(
            "PM1", "M1", 50, TimeDistributionSpec("constant", 2)
        )
        result = Simulator(
            make_scenario(
                (op(1, sample=0.1), op(2)),
                wafer_pm_specs=(pm,),
            ),
            seed=1,
        ).run()
        self.assertEqual(
            [item.step_id for item in result.trace
             if item.event_type == "OPERATION_SKIPPED"],
            [1],
        )
        self.assertEqual(result.pm_count, 0)
        self.assertEqual(result.wafer_pm_states[0].counter_wafers, 25)

    def test_lazy_release_template_samples_each_materialized_lot(self) -> None:
        release = ReleaseTemplateSpec(
            template_id="T1", source_row=2, lot_prefix="PFX",
            product_id="P", order_id="O",
            operations=(op(1, sample=50),),
            first_release_time=0,
            interval=TimeDistributionSpec("constant", 2),
            repeat_limit=2, lots_per_repeat=1,
            relative_due_minutes=5, priority=0, hot_lot=False,
            quantity_wafers=25,
        )
        scenario = Scenario(
            "SAMPLING_RELEASE", "micro@sampling-release",
            (MachineSpec("M1"),), (),
            termination_mode="fixed_horizon", horizon=3,
            release_templates=(release,),
        )
        result = Simulator(scenario, seed=42).run()
        decisions = [
            item for item in result.trace
            if item.event_type == "SAMPLING_DECISION"
        ]
        samples = [
            item for item in result.random_sample_ledger
            if item.stream_name == "sampling"
        ]
        self.assertEqual([item.sim_time for item in decisions], [0, 2])
        self.assertEqual(len({item.lot_id for item in decisions}), 2)
        self.assertEqual(len({item.identity for item in samples}), 2)
        self.assertTrue(audit_result_invariants(result, scenario).passed)


if __name__ == "__main__":
    unittest.main()
