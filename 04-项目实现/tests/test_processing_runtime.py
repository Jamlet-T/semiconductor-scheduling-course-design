"""真实加工随机量、名义口径与中断恢复的机制测试。"""

from __future__ import annotations

import unittest

from fab_scheduler.domain import (
    BatchSpec,
    CalendarPMSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
    TimeDistributionSpec,
)
from fab_scheduler.policies import FIFOPolicy, SPTPolicy
from fab_scheduler.simulation import Simulator
from fab_scheduler.simulation.distributions import sample_distribution
from fab_scheduler.simulation.processing import ProcessingDurationResolver
from fab_scheduler.simulation.random_streams import EntityRandomStreams


def operation(
    duration: float,
    *,
    distribution: TimeDistributionSpec | None = None,
    basis: str = "per_lot",
    batch: BatchSpec | None = None,
) -> OperationSpec:
    return OperationSpec(
        1,
        duration,
        ("M1",),
        route_id="R",
        processing_distribution=distribution,
        processing_basis=basis,
        batch_spec=batch,
    )


def scenario(lots: tuple[LotSpec, ...], **kwargs: object) -> Scenario:
    return Scenario("PROCESSING", "micro@processing", (MachineSpec("M1"),), lots, **kwargs)


class DistributionRuntimeTests(unittest.TestCase):
    def test_constant_uniform_and_exponential_share_stable_ledger_identities(self) -> None:
        streams = EntityRandomStreams(9)
        self.assertEqual(
            sample_distribution(TimeDistributionSpec("constant", 3), random_source=streams, stream_name="x", entity_id="a", occurrence_index=0),
            3,
        )
        uniform = sample_distribution(TimeDistributionSpec("uniform", 7.5, 2.5), random_source=streams, stream_name="u", entity_id="a", occurrence_index=0)
        self.assertGreaterEqual(uniform, 6.25)
        self.assertLessEqual(uniform, 8.75)
        exponential = sample_distribution(TimeDistributionSpec("exponential", 10), random_source=streams, stream_name="e", entity_id="a", occurrence_index=0)
        self.assertGreater(exponential, 0)
        self.assertEqual(
            exponential,
            sample_distribution(TimeDistributionSpec("exponential", 10), random_source=streams, stream_name="e", entity_id="a", occurrence_index=0),
        )
        self.assertEqual({item.distribution for item in streams.ledger}, {"uniform", "exponential"})

    def test_processing_basis_uses_nominal_for_policy_and_realization_after_commit(self) -> None:
        resolver = ProcessingDurationResolver()
        piece = operation(2, distribution=TimeDistributionSpec("constant", 2), basis="per_piece")
        self.assertEqual(resolver.nominal(piece, quantity_wafers=25), 50)
        realized = resolver.realize_lot(operation=piece, lot_id="L", quantity_wafers=25, visit_index=0, random_source=EntityRandomStreams(1))
        self.assertEqual((realized.sampled_core_minutes, realized.realized_minutes), (2, 50))

    def test_candidate_enumeration_does_not_sample_and_commit_samples_once(self) -> None:
        op = operation(10, distribution=TimeDistributionSpec("uniform", 10, 2))
        result = Simulator(scenario((LotSpec("L1", 0, (op,)),))).run()
        ledger = [item for item in result.random_sample_ledger if item.stream_name == "processing"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0].entity_id, "L1|R|1|visit=0")
        self.assertEqual(len([item for item in result.trace if item.event_type == "DISPATCH"]), 1)

    def test_stochastic_processing_common_identity_survives_policy_order_change(self) -> None:
        long = LotSpec("L1", 0, (operation(10, distribution=TimeDistributionSpec("uniform", 10, 2)),))
        short = LotSpec("L2", 0, (operation(2, distribution=TimeDistributionSpec("uniform", 2, 1)),))
        inputs = scenario((long, short))
        fifo = Simulator(inputs, policy=FIFOPolicy(), seed=17).run()
        spt = Simulator(inputs, policy=SPTPolicy(), seed=17).run()
        left = {item.identity: item.value for item in fifo.random_sample_ledger if item.stream_name == "processing"}
        right = {item.identity: item.value for item in spt.random_sample_ledger if item.stream_name == "processing"}
        self.assertEqual(left, right)
        self.assertNotEqual(
            [record.lot_id for record in fifo.processing_intervals],
            [record.lot_id for record in spt.processing_intervals],
        )

    def test_failure_resume_keeps_one_realized_processing_sample(self) -> None:
        op = operation(12, distribution=TimeDistributionSpec("uniform", 12, 2))
        result = Simulator(
            scenario(
                (LotSpec("L1", 0, (op,)),),
                failure_specs=(MachineFailureSpec("M1", "scripted", scripted_failures=(ScriptedFailureSpec(5, 3),)),),
            )
        ).run()
        samples = [item for item in result.random_sample_ledger if item.stream_name == "processing"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(result.completion_times["L1"], samples[0].value + 3)
        self.assertEqual(sum(interval.finish - interval.start for interval in result.processing_intervals), samples[0].value)

    def test_batch_samples_one_physical_duration(self) -> None:
        batch = BatchSpec(125, 150, 125, 0)
        op = operation(20, distribution=TimeDistributionSpec("uniform", 20, 2), basis="per_batch", batch=batch)
        lots = tuple(LotSpec(f"L{i}", 0, (op,)) for i in range(1, 6))
        result = Simulator(scenario(lots), seed=12).run()
        ledger = [item for item in result.random_sample_ledger if item.stream_name == "batch_processing"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(len(result.batch_intervals), 1)

    def test_pm_resume_keeps_one_realized_processing_sample(self) -> None:
        op = operation(12, distribution=TimeDistributionSpec("uniform", 12, 2))
        result = Simulator(
            scenario(
                (LotSpec("L1", 0, (op,)),),
                calendar_pm_specs=(
                    CalendarPMSpec(
                        "PM-M1", "M1", "scripted",
                        scripted_occurrences=(ScriptedPMSpec(5, 3),),
                    ),
                ),
            )
        ).run()
        samples = [item for item in result.random_sample_ledger if item.stream_name == "processing"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(result.completion_times["L1"], samples[0].value + 3)
        self.assertEqual(sum(interval.finish - interval.start for interval in result.processing_intervals), samples[0].value)

    def test_exponential_failure_uses_shared_sampler_and_crn_identity(self) -> None:
        failure = MachineFailureSpec(
            "M1", "stochastic",
            failure_interval=TimeDistributionSpec("exponential", 100),
            repair_duration=TimeDistributionSpec("exponential", 2),
        )
        inputs = scenario((LotSpec("L1", 0, (operation(1),)),), termination_mode="fixed_horizon", horizon=10, failure_specs=(failure,))
        left = Simulator(inputs, policy=FIFOPolicy(), seed=5).run()
        right = Simulator(inputs, policy=SPTPolicy(), seed=5).run()
        left_values = {item.identity: item.value for item in left.random_sample_ledger}
        right_values = {item.identity: item.value for item in right.random_sample_ledger}
        common = set(left_values) & set(right_values)
        self.assertTrue(common)
        self.assertEqual({key: left_values[key] for key in common}, {key: right_values[key] for key in common})
        self.assertTrue(all(item.distribution == "exponential" for item in left.random_sample_ledger))


if __name__ == "__main__":
    unittest.main()
