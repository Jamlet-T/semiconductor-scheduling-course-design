"""冻结技术路线与 M1 微型算例清单的一致性检查。"""

import json
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_CONFIG = REPOSITORY_ROOT / "configs" / "project.json"
MICRO_CASES = (
    Path(__file__).resolve().parent / "fixtures" / "micro_cases" / "cases.json"
)


class ContractConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
        cls.micro_cases = json.loads(MICRO_CASES.read_text(encoding="utf-8"))

    def test_m1_gate_keeps_optimizer_disabled(self) -> None:
        route = self.project["technical_route"]
        self.assertEqual(self.project["phase"], "m1_simulation_reliability_baseline")
        self.assertTrue(self.project["simulation_implemented"])
        self.assertEqual(route["status"], "frozen")
        self.assertEqual(route["current_milestone"], "M1")
        self.assertEqual(route["optimizer"], "CMA-ES")
        self.assertFalse(route["optimizer_enabled"])

    def test_micro_cases_follow_the_frozen_contract_version(self) -> None:
        self.assertEqual(
            self.micro_cases["status"],
            "mc01_mc08_verified_m1_passed",
        )
        self.assertEqual(self.project["m1_status"], "passed")
        self.assertEqual(
            self.project["m1_closure_audit"]["failed_exit_criteria"],
            [],
        )
        self.assertEqual(
            self.project["smt2020_data_integration_gate"]["status"],
            "pending",
        )
        self.assertEqual(
            self.micro_cases["contract_version"],
            self.project["technical_route"]["contract_version"],
        )
        self.assertEqual(
            self.micro_cases["event_priority_contract_version"],
            self.project["technical_route"]["contract_version"],
        )

    def test_all_eight_gold_cases_are_present_once(self) -> None:
        expected_ids = {
            "MC01_ROUTE_ORDER",
            "MC02_DYNAMIC_RELEASE_FIFO",
            "MC03_SEQUENCE_SETUP",
            "MC04_BATCH_CAPACITY",
            "MC05_CROSS_STEP_CQT",
            "MC06_MACHINE_DEDICATION",
            "MC07_FAILURE_AND_EVENT_BARRIER",
            "MC08_TERMINAL_EXPOSURE",
        }
        case_ids = [case["id"] for case in self.micro_cases["cases"]]
        self.assertEqual(len(case_ids), 8)
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertEqual(set(case_ids), expected_ids)

    def test_mc01_through_mc08_and_m1_are_verified(self) -> None:
        statuses = {
            case["id"]: case["implementation_status"]
            for case in self.micro_cases["cases"]
        }
        self.assertEqual(statuses["MC01_ROUTE_ORDER"], "verified")
        self.assertEqual(statuses["MC02_DYNAMIC_RELEASE_FIFO"], "verified")
        self.assertEqual(statuses["MC03_SEQUENCE_SETUP"], "verified")
        self.assertEqual(statuses["MC04_BATCH_CAPACITY"], "verified")
        self.assertEqual(statuses["MC05_CROSS_STEP_CQT"], "verified")
        self.assertEqual(statuses["MC06_MACHINE_DEDICATION"], "verified")
        self.assertEqual(statuses["MC07_FAILURE_AND_EVENT_BARRIER"], "verified")
        self.assertEqual(statuses["MC08_TERMINAL_EXPOSURE"], "verified")
        self.assertFalse(self.project["technical_route"]["optimizer_enabled"])


if __name__ == "__main__":
    unittest.main()
