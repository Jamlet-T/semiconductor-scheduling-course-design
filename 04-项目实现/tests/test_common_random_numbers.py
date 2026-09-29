"""M1-E07：跨策略实体索引 Common Random Numbers。"""

from __future__ import annotations

import unittest

from fab_scheduler.api import simulate
from fab_scheduler.domain.models import (
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    TimeDistributionSpec,
)
from fab_scheduler.evaluation.crn_audit import audit_common_random_numbers


def stochastic_scenario() -> Scenario:
    return Scenario(
        "CRN_POLICIES",
        "micro@crn-0.1.0",
        (MachineSpec("M1"),),
        (
            LotSpec("L1", 0, (OperationSpec(1, 10, ("M1",), route_id="R"),), due_time=100),
            LotSpec("L2", 0, (OperationSpec(1, 2, ("M1",), route_id="R"),), due_time=20),
        ),
        termination_mode="fixed_horizon",
        horizon=20,
        failure_specs=(
            MachineFailureSpec(
                "M1",
                "stochastic",
                failure_interval=TimeDistributionSpec("uniform", 5, 2),
                repair_duration=TimeDistributionSpec("uniform", 2, 1),
            ),
        ),
    )


class CommonRandomNumbersTests(unittest.TestCase):
    def test_all_four_policies_share_common_identity_samples(self) -> None:
        scenario = stochastic_scenario()
        results = {
            policy_id: simulate({"policy_id": policy_id}, scenario, 42)
            for policy_id in ("fifo", "spt", "edd", "cr")
        }
        fifo_first = next(
            row["lot"]
            for row in results["fifo"].key_trace()
            if row["event"] == "PROCESS_START"
        )
        spt_first = next(
            row["lot"]
            for row in results["spt"].key_trace()
            if row["event"] == "PROCESS_START"
        )
        self.assertEqual((fifo_first, spt_first), ("L1", "L2"))
        for policy_id in ("spt", "edd", "cr"):
            audit = audit_common_random_numbers(results["fifo"], results[policy_id])
            self.assertTrue(audit.passed, audit.mismatched_identities)
            self.assertGreaterEqual(len(audit.common_identities), 2)

    def test_different_seed_changes_at_least_one_sample(self) -> None:
        scenario = stochastic_scenario()
        left = simulate({"policy_id": "fifo"}, scenario, 42)
        right = simulate({"policy_id": "fifo"}, scenario, 43)
        left_values = {record.identity: record.value for record in left.random_sample_ledger}
        right_values = {record.identity: record.value for record in right.random_sample_ledger}
        common = left_values.keys() & right_values.keys()
        self.assertTrue(any(left_values[key] != right_values[key] for key in common))

    def test_ledger_does_not_change_repeated_run_or_policy_order(self) -> None:
        scenario = stochastic_scenario()
        first_fifo = simulate({"policy_id": "fifo"}, scenario, 7)
        simulate({"policy_id": "spt"}, scenario, 7)
        second_fifo = simulate({"policy_id": "fifo"}, scenario, 7)
        self.assertEqual(
            first_fifo.random_sample_ledger,
            second_fifo.random_sample_ledger,
        )
        self.assertEqual(first_fifo.trace_as_dicts(), second_fifo.trace_as_dicts())

    def test_audit_labels_trajectory_specific_occurrences_without_failure(self) -> None:
        scenario = stochastic_scenario()
        short_horizon = Scenario(
            scenario.scenario_id,
            scenario.dataset_version,
            scenario.machines,
            scenario.lots,
            termination_mode="fixed_horizon",
            horizon=6,
            failure_specs=scenario.failure_specs,
        )
        long_result = simulate({"policy_id": "fifo"}, scenario, 42)
        short_result = simulate({"policy_id": "spt"}, short_horizon, 42)
        audit = audit_common_random_numbers(long_result, short_result)
        self.assertTrue(audit.passed)
        self.assertTrue(audit.left_only_identities or audit.right_only_identities)


if __name__ == "__main__":
    unittest.main()
