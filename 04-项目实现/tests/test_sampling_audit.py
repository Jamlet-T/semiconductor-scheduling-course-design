"""Sampling trace、随机账本和 provenance 的独立审计回归。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (  # noqa: E402
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
)
from fab_scheduler.evaluation.audit import audit_result_invariants  # noqa: E402
from fab_scheduler.simulation.engine import Simulator  # noqa: E402


class SamplingAuditTests(unittest.TestCase):
    @staticmethod
    def scenario(percent: float) -> Scenario:
        return Scenario(
            "SAMPLING_AUDIT",
            "sampling-audit@0.1",
            (MachineSpec("M1"),),
            (
                LotSpec(
                    "L1",
                    0,
                    (
                        OperationSpec(
                            1,
                            2,
                            ("M1",),
                            route_id="R",
                            sample_percent=percent,
                        ),
                    ),
                ),
            ),
        )

    @classmethod
    def result(cls, percent: float, seed: int):
        scenario = cls.scenario(percent)
        return scenario, Simulator(scenario, seed=seed, git_commit="sampling-audit").run()

    @staticmethod
    def decision(result):
        return next(record for record in result.trace if record.event_type == "SAMPLING_DECISION")

    def test_valid_p100_and_stochastic_results_pass_independent_audit(self) -> None:
        p100_scenario, p100 = self.result(100, 1)
        self.assertTrue(audit_result_invariants(p100, p100_scenario).passed)
        self.assertEqual(
            [item for item in p100.random_sample_ledger if item.stream_name == "sampling"],
            [],
        )

        stochastic_scenario, stochastic = self.result(50, 1)  # draw 69..., hence skip
        audit = audit_result_invariants(stochastic, stochastic_scenario)
        self.assertTrue(audit.passed, audit.violations)
        self.assertEqual(
            len([item for item in stochastic.random_sample_ledger if item.stream_name == "sampling"]),
            1,
        )
        self.assertEqual(
            len([record for record in stochastic.trace if record.event_type == "OPERATION_SKIPPED"]),
            1,
        )

    def test_tampered_draw_is_rejected(self) -> None:
        scenario, result = self.result(50, 4)  # draw 30..., hence performed
        old = self.decision(result)
        trace = tuple(
            replace(record, sampling_draw=old.sampling_draw + 1.0)
            if record.event_seq == old.event_seq
            else record
            for record in result.trace
        )
        audit = audit_result_invariants(replace(result, trace=trace), scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("draw" in violation for violation in audit.violations))

    def test_tampered_percent_is_rejected_against_scenario(self) -> None:
        scenario, result = self.result(50, 4)
        old = self.decision(result)
        trace = tuple(
            replace(record, sampling_percent=60)
            if record.event_seq == old.event_seq
            else record
            for record in result.trace
        )
        audit = audit_result_invariants(replace(result, trace=trace), scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(
            any("Scenario 配置不一致" in item for item in audit.violations)
        )

    def test_tampered_decision_is_rejected(self) -> None:
        scenario, result = self.result(50, 1)
        old = self.decision(result)
        trace = tuple(
            replace(record, sampling_performed=True)
            if record.event_seq == old.event_seq
            else record
            for record in result.trace
        )
        tampered = replace(result, trace=trace)
        audit = audit_result_invariants(tampered, scenario)
        self.assertFalse(audit.passed)

    def test_missing_decision_for_entry_is_rejected(self) -> None:
        # p=100 keeps the LOT_RELEASE and PROCESS_START/FINISH witnesses while
        # removing only the sampling decision, exercising the silent-ignore
        # failure mode directly.
        scenario, result = self.result(100, 1)
        tampered = replace(
            result,
            trace=tuple(
                record
                for record in result.trace
                if record.event_type != "SAMPLING_DECISION"
            ),
        )
        audit = audit_result_invariants(tampered, scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("entry" in violation for violation in audit.violations))

    def test_same_route_step_can_have_different_lot_sampling(self) -> None:
        scenario = Scenario(
            "SAMPLING_PER_LOT_AUDIT", "sampling-audit@0.1",
            (MachineSpec("M1"),),
            (
                LotSpec("A", 0, (OperationSpec(1, 1, ("M1",), route_id="R", sample_percent=100),)),
                LotSpec("B", 0, (OperationSpec(1, 1, ("M1",), route_id="R"),)),
            ),
        )
        result = Simulator(scenario, seed=1, git_commit="sampling-audit").run()
        audit = audit_result_invariants(result, scenario)
        self.assertTrue(audit.passed, audit.violations)

    def test_missing_intermediate_skip_is_rejected_by_route_witness(self) -> None:
        scenario = Scenario(
            "SAMPLING_ROUTE_AUDIT",
            "sampling-audit@0.1",
            (MachineSpec("M1"),),
            (
                LotSpec(
                    "L1", 0,
                    (
                        OperationSpec(1, 1, ("M1",), route_id="R"),
                        OperationSpec(2, 1, ("M1",), route_id="R", sample_percent=0.1),
                        OperationSpec(3, 1, ("M1",), route_id="R"),
                    ),
                ),
            ),
        )
        result = Simulator(scenario, seed=1, git_commit="sampling-audit").run()
        self.assertTrue(audit_result_invariants(result, scenario).passed)
        tampered = replace(
            result,
            trace=tuple(
                record for record in result.trace
                if record.event_type not in {"SAMPLING_DECISION", "OPERATION_SKIPPED"}
            ),
            random_sample_ledger=tuple(
                record for record in result.random_sample_ledger
                if record.stream_name != "sampling"
            ),
        )
        audit = audit_result_invariants(tampered, scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("route witness" in item for item in audit.violations))

    def test_decision_after_process_start_is_rejected(self) -> None:
        scenario, result = self.result(100, 1)
        old = self.decision(result)
        trace = tuple(
            replace(record, sim_time=old.sim_time + 1)
            if record.event_seq == old.event_seq
            else record
            for record in result.trace
        )
        audit = audit_result_invariants(replace(result, trace=trace), scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("晚于" in item for item in audit.violations))

    def test_missing_skip_is_rejected(self) -> None:
        scenario, result = self.result(50, 1)
        tampered = replace(
            result,
            trace=tuple(
                record
                for record in result.trace
                if record.event_type != "OPERATION_SKIPPED"
            ),
        )
        audit = audit_result_invariants(tampered, scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("OPERATION_SKIPPED" in violation for violation in audit.violations))

    def test_tampered_skip_payload_is_rejected(self) -> None:
        scenario, result = self.result(50, 1)
        trace = tuple(
            replace(record, sampling_percent=99, sampling_performed=True)
            if record.event_type == "OPERATION_SKIPPED"
            else record
            for record in result.trace
        )
        audit = audit_result_invariants(replace(result, trace=trace), scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("payload/order" in item for item in audit.violations))

    def test_tampered_sampling_provenance_is_rejected(self) -> None:
        scenario, result = self.result(50, 1)
        config = dict(result.provenance.simulation_config)
        runtime = dict(config["sampling_runtime"])
        boundary = dict(runtime["supported_boundary"])
        boundary["scope"] = "global"
        runtime["supported_boundary"] = boundary
        config["sampling_runtime"] = runtime
        tampered = replace(
            result,
            provenance=replace(result.provenance, simulation_config=config),
        )
        audit = audit_result_invariants(tampered, scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("sampling runtime boundary" in violation for violation in audit.violations))

    def test_tampered_sampling_ledger_is_rejected(self) -> None:
        scenario, result = self.result(50, 4)
        sampling = next(
            index
            for index, item in enumerate(result.random_sample_ledger)
            if item.stream_name == "sampling"
        )
        ledger = list(result.random_sample_ledger)
        ledger[sampling] = replace(ledger[sampling], value=ledger[sampling].value + 1.0)
        tampered = replace(result, random_sample_ledger=tuple(ledger))
        audit = audit_result_invariants(tampered, scenario)
        self.assertFalse(audit.passed)
        self.assertTrue(any("draw" in violation for violation in audit.violations))

    def test_matching_tampered_draw_and_ledger_is_rejected(self) -> None:
        scenario, result = self.result(50, 4)
        old = self.decision(result)
        trace = tuple(
            replace(record, sampling_draw=7.0)
            if record.event_seq == old.event_seq
            else record
            for record in result.trace
        )
        ledger = tuple(
            replace(record, value=7.0)
            if record.stream_name == "sampling"
            else record
            for record in result.random_sample_ledger
        )
        audit = audit_result_invariants(
            replace(result, trace=trace, random_sample_ledger=ledger), scenario
        )
        self.assertFalse(audit.passed)
        self.assertTrue(any("派生随机流" in item for item in audit.violations))


if __name__ == "__main__":
    unittest.main()
