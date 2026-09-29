"""Setup MINRUN 的 trace/provenance 独立审计回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import unittest


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (  # noqa: E402
    BatchSpec,
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    SetupMinimumRun,
    SetupTransition,
)
from fab_scheduler.evaluation.audit import audit_result_invariants  # noqa: E402
from fab_scheduler.simulation.engine import Simulator  # noqa: E402


class SetupMinrunAuditTests(unittest.TestCase):
    @staticmethod
    def scenario(*, initial_count: int | None = 0, horizon: float | None = None) -> Scenario:
        def operation(route: str, setup: str) -> OperationSpec:
            return OperationSpec(
                1,
                1,
                ("M1",),
                route_id=route,
                required_setup=setup,
            )

        return Scenario(
            "MINRUN_TRACE_AUDIT",
            "minrun-audit@0.1",
            (
                MachineSpec(
                    "M1",
                    initial_setup="A",
                    setup_group="G",
                    initial_setup_run_count=initial_count,
                ),
            ),
            (
                LotSpec("A1", 0, (operation("A", "A"),)),
                LotSpec("A2", 0, (operation("A", "A"),)),
                LotSpec("B1", 0, (operation("B", "B"),)),
            ),
            termination_mode="fixed_horizon" if horizon is not None else "until_all_complete",
            horizon=horizon,
            setup_transitions=(SetupTransition("A", "B", 1),),
            setup_minimum_runs=(
                SetupMinimumRun("G", "A", 2),
                SetupMinimumRun("G", "B", 1),
            ),
        )

    def run_result(self, scenario: Scenario):
        return Simulator(scenario, seed=7, git_commit="minrun-audit").run()

    def test_valid_trace_and_provenance_pass_independent_audit(self) -> None:
        scenario = self.scenario()
        result = self.run_result(scenario)

        audit = audit_result_invariants(result, scenario)
        self.assertTrue(audit.passed, audit.violations)
        self.assertEqual(
            result.provenance.simulation_config["setup_minrun_runtime"]["machines"]["M1"]["completed_lots"],
            1,
        )

    def test_trace_tamper_that_removes_a_successful_finish_exposes_early_switch(self) -> None:
        scenario = self.scenario()
        result = self.run_result(scenario)
        trace = tuple(
            replace(record, event_type="PROCESS_FINISH_STALE")
            if record.event_type == "PROCESS_FINISH" and record.lot_id == "A2"
            else record
            for record in result.trace
        )

        audit = audit_result_invariants(replace(result, trace=trace), scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(
            any("违反 setup MINRUN" in violation for violation in audit.violations),
            audit.violations,
        )

    def test_trace_tamper_that_hides_setup_finish_cannot_pass_as_processing(self) -> None:
        scenario = self.scenario()
        result = self.run_result(scenario)
        trace = tuple(
            replace(record, event_type="SETUP_FINISH_STALE")
            if record.event_type == "SETUP_FINISH"
            else record
            for record in result.trace
        )

        audit = audit_result_invariants(replace(result, trace=trace), scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(
            any("PROCESS_START setup 尚未完成" in violation for violation in audit.violations),
            audit.violations,
        )

    def test_provenance_terminal_count_tamper_is_rejected(self) -> None:
        scenario = self.scenario()
        result = self.run_result(scenario)
        config = deepcopy(result.provenance.simulation_config)
        config["setup_minrun_runtime"]["machines"]["M1"]["completed_lots"] = 99
        provenance = replace(result.provenance, simulation_config=config)

        audit = audit_result_invariants(replace(result, provenance=provenance), scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(
            any("completed_lots 不一致" in violation for violation in audit.violations),
            audit.violations,
        )

    def test_unknown_initial_count_is_audited_as_conservative_lower_bound(self) -> None:
        scenario = self.scenario(initial_count=None, horizon=2)
        result = self.run_result(scenario)

        self.assertTrue(
            audit_result_invariants(result, scenario).passed,
            audit_result_invariants(result, scenario).violations,
        )
        runtime = result.provenance.simulation_config["setup_minrun_runtime"]
        self.assertEqual(runtime["initial_setup_count_unknown"], ["M1"])
        self.assertEqual(runtime["initial_setup_count_unknown_final"], ["M1"])

    def test_batch_members_each_count_once_for_same_setup(self) -> None:
        batch = BatchSpec(2, 2, 2, 0)
        batch_operation = OperationSpec(
            1, 1, ("M1",), route_id="A", required_setup="A",
            batch_spec=batch, processing_basis="per_batch",
        )
        scenario = Scenario(
            "MINRUN_BATCH_AUDIT", "minrun-audit@0.1",
            (MachineSpec("M1", initial_setup="A", setup_group="G", initial_setup_run_count=0),),
            (
                LotSpec("A1", 0, (batch_operation,), quantity_wafers=1),
                LotSpec("A2", 0, (batch_operation,), quantity_wafers=1),
                LotSpec("B1", 0, (OperationSpec(1, 1, ("M1",), route_id="B", required_setup="B"),)),
            ),
            setup_transitions=(SetupTransition("A", "B", 1),),
            setup_minimum_runs=(SetupMinimumRun("G", "A", 2),),
        )
        result = self.run_result(scenario)
        self.assertTrue(audit_result_invariants(result, scenario).passed)
        self.assertEqual(result.completion_times, {"A1": 1, "A2": 1, "B1": 3})


if __name__ == "__main__":
    unittest.main()
