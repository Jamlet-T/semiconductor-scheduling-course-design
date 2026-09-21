"""M1-E10：统一 simulate(theta, scenario, seed) 公共 API。"""

from __future__ import annotations

from dataclasses import asdict
import unittest

from fab_scheduler.api import POLICY_CONFIG_VERSION, PolicyConfig, simulate
from fab_scheduler.domain.models import LotSpec, MachineSpec, OperationSpec, Scenario


def scenario() -> Scenario:
    return Scenario(
        "SIMULATE_API",
        "micro@api-0.1.0",
        (MachineSpec("M1"),),
        (
            LotSpec("L1", 0, (OperationSpec(1, 10, ("M1",), route_id="R"),), due_time=100),
            LotSpec("L2", 0, (OperationSpec(1, 2, ("M1",), route_id="R"),), due_time=20),
        ),
    )


class SimulationAPITests(unittest.TestCase):
    def test_theta_schema_is_strict_and_optimizer_free(self) -> None:
        self.assertEqual(POLICY_CONFIG_VERSION, "0.1.0")
        self.assertEqual(PolicyConfig("FIFO").policy_id, "fifo")
        with self.assertRaises(ValueError):
            PolicyConfig("weighted_dispatch")
        with self.assertRaises(ValueError):
            PolicyConfig("spt", {"weight": 1})
        with self.assertRaises(ValueError):
            simulate({"policy_id": "fifo", "optimizer": "cma-es"}, scenario(), 1)
        with self.assertRaises(TypeError):
            simulate({"policy_id": "fifo", "parameters": []}, scenario(), 1)

    def test_result_schema_and_policy_provenance_are_uniform(self) -> None:
        for policy_id in ("fifo", "spt", "edd", "cr"):
            result = simulate(
                {"policy_id": policy_id, "parameters": {}},
                scenario(),
                42,
                git_commit="api-test",
            )
            self.assertEqual(result.policy_id, policy_id)
            self.assertEqual(result.policy_parameters, {})
            self.assertEqual(result.seed, 42)
            self.assertEqual(result.contract_version, "0.1.3")
            self.assertEqual(result.policy_contract_version, "0.1.0")
            self.assertEqual(result.scenario_identity, "SIMULATE_API")
            self.assertEqual(result.termination, "until_all_complete")
            self.assertEqual(result.provenance.git_commit, "api-test")
            self.assertIsInstance(result.trace, tuple)

    def test_scenario_is_immutable_across_runs_and_order_independent(self) -> None:
        shared = scenario()
        before = asdict(shared)
        forward = {
            policy_id: simulate({"policy_id": policy_id}, shared, 42)
            for policy_id in ("fifo", "spt", "edd", "cr")
        }
        reverse = {
            policy_id: simulate({"policy_id": policy_id}, shared, 42)
            for policy_id in reversed(("fifo", "spt", "edd", "cr"))
        }
        self.assertEqual(asdict(shared), before)
        for policy_id in forward:
            self.assertEqual(
                forward[policy_id].trace_as_dicts(),
                reverse[policy_id].trace_as_dicts(),
            )
            self.assertEqual(forward[policy_id].metrics, reverse[policy_id].metrics)


if __name__ == "__main__":
    unittest.main()
