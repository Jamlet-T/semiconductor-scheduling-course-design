"""Transport runtime：外生、无容量、可审计搬运。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import unittest

from fab_scheduler.api import simulate
from fab_scheduler.data import LoaderConfig, load_smt2020
from fab_scheduler.domain import (
    CQTSpec,
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    TimeDistributionSpec,
    TransportSpec,
)
from fab_scheduler.evaluation.audit import audit_result_invariants


ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "datasets"


def transport_scenario(
    *,
    target_location: str = "Fab",
    transport: TimeDistributionSpec | None = None,
    horizon: float = 30,
    cqt_limit: float | None = None,
) -> Scenario:
    spec = transport or TimeDistributionSpec("constant", 3)
    machines = (
        MachineSpec("M1", location_id="Fab"),
        MachineSpec("M2", location_id=target_location),
    )
    lots = (
        LotSpec(
            "L1",
            0,
            (
                OperationSpec(1, 2, ("M1",), route_id="R"),
                OperationSpec(2, 2, ("M2",), route_id="R"),
            ),
        ),
    )
    return Scenario(
        "TRANSPORT",
        "micro@transport",
        machines,
        lots,
        termination_mode="fixed_horizon",
        horizon=horizon,
        transport_specs=(TransportSpec("Fab", "Fab", spec),),
        cqt_constraints=(
            (CQTSpec("C1", "R", 1, 2, cqt_limit),)
            if cqt_limit is not None
            else ()
        ),
    )


class TransportRuntimeTests(unittest.TestCase):
    def test_arrival_blocks_target_dispatch_and_same_time_priority_is_stable(self) -> None:
        scenario = transport_scenario(transport=TimeDistributionSpec("constant", 3))
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        events = [item for item in result.trace if item.lot_id == "L1"]
        arrival = next(item for item in events if item.event_type == "TRANSPORT_ARRIVE")
        target_dispatch = next(
            item for item in events
            if item.event_type == "DISPATCH" and item.step_id == 2
        )
        self.assertEqual(arrival.sim_time, 5)
        self.assertEqual(target_dispatch.sim_time, 5)
        self.assertLess(arrival.priority, target_dispatch.priority)
        self.assertEqual(result.transport_metrics.completed_count, 1)
        self.assertEqual(result.transport_metrics.active_count, 0)

    def test_process_finish_machine_can_dispatch_another_lot_during_transport(self) -> None:
        base = transport_scenario(transport=TimeDistributionSpec("constant", 3))
        lots = base.lots + (
            LotSpec("L2", 0, (OperationSpec(1, 1, ("M1",), route_id="R"),)),
        )
        scenario = Scenario(
            base.scenario_id,
            base.dataset_version,
            base.machines,
            lots,
            termination_mode="fixed_horizon",
            horizon=10,
            transport_specs=base.transport_specs,
        )
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        l2_start = next(
            item for item in result.trace
            if item.event_type == "PROCESS_START" and item.lot_id == "L2"
        )
        self.assertEqual(l2_start.sim_time, 2)

    def test_missing_pair_is_zero_and_audited_without_sampling(self) -> None:
        scenario = transport_scenario(target_location="Delay")
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        self.assertEqual(result.transport_metrics.missing_pair_count, 1)
        self.assertEqual(result.transport_metrics.total_minutes, 0)
        self.assertEqual(result.transport_metrics.missing_pairs, (("Fab", "Delay", 1),))
        self.assertFalse([item for item in result.random_sample_ledger if item.stream_name == "transport"])
        trace = [item for item in result.trace if item.event_type == "TRANSPORT_MISSING"]
        self.assertEqual(len(trace), 1)
        self.assertTrue(trace[0].transport_missing_pair)
        self.assertTrue(audit_result_invariants(result, scenario).passed)

    def test_transport_metric_corruption_is_detected_independently(self) -> None:
        scenario = transport_scenario(transport=TimeDistributionSpec("constant", 3))
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        corrupted = replace(
            result,
            transport_metrics=replace(
                result.transport_metrics,
                total_minutes=result.transport_metrics.total_minutes + 1,
            ),
        )
        audit = audit_result_invariants(corrupted, scenario)
        self.assertFalse(audit.passed)
        self.assertIn("transport total_minutes 不一致", audit.violations)

    def test_cqt_includes_transport_duration(self) -> None:
        scenario = transport_scenario(
            transport=TimeDistributionSpec("constant", 3),
            cqt_limit=2,
        )
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        self.assertEqual(len(result.cqt_records), 1)
        self.assertEqual(result.cqt_records[0].actual_duration, 3)
        self.assertTrue(result.cqt_records[0].violation)

    def test_mid_transport_fixed_horizon_exposes_active_snapshot(self) -> None:
        scenario = transport_scenario(
            transport=TimeDistributionSpec("constant", 4),
            horizon=3,
        )
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        self.assertEqual((result.metrics.completed_lots, result.metrics.terminal_wip_lots), (0, 1))
        self.assertFalse(result.transport_intervals)
        self.assertEqual(result.transport_metrics.started_count, 1)
        self.assertEqual(result.transport_metrics.configured_pair_count, 1)
        self.assertEqual(result.transport_metrics.completed_count, 0)
        self.assertEqual(result.transport_metrics.active_count, 1)
        self.assertEqual(result.active_transports[0].remaining_duration, 3)

    def test_arrival_exactly_at_horizon_is_completed_and_auditable(self) -> None:
        scenario = transport_scenario(
            transport=TimeDistributionSpec("constant", 3),
            horizon=5,
        )
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        self.assertEqual(len(result.transport_intervals), 1)
        self.assertFalse(result.active_transports)
        self.assertEqual(result.transport_intervals[0].finish, 5)
        self.assertTrue(audit_result_invariants(result, scenario).passed)

    def test_transport_runtime_and_identity_scheme_are_in_provenance(self) -> None:
        scenario = transport_scenario(
            transport=TimeDistributionSpec("uniform", 3, 1),
        )
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        config = result.provenance.simulation_config
        self.assertEqual(config["transport_runtime_schema_version"], "0.1.0")
        self.assertEqual(
            config["transport_model"],
            {
                "resource_capacity": "external_unbounded",
                "initial_leg": "none",
                "missing_pair": "zero_duration_audited",
            },
        )
        self.assertEqual(config["transport_random_streams"]["duration"], "transport")
        self.assertIn("visit_index", config["transport_random_streams"]["identity"])

    def test_transport_samples_are_common_random_numbers_across_all_policies(self) -> None:
        base = transport_scenario(
            transport=TimeDistributionSpec("uniform", 3, 1),
            horizon=30,
        )
        scenario = Scenario(
            base.scenario_id,
            base.dataset_version,
            base.machines,
            (
                LotSpec(
                    "L1",
                    0,
                    (
                        OperationSpec(1, 5, ("M1",), route_id="R"),
                        OperationSpec(2, 2, ("M2",), route_id="R"),
                    ),
                    due_time=20,
                ),
                LotSpec(
                    "L2",
                    0,
                    (
                        OperationSpec(1, 1, ("M1",), route_id="R"),
                        OperationSpec(2, 2, ("M2",), route_id="R"),
                    ),
                    due_time=5,
                ),
            ),
            termination_mode="fixed_horizon",
            horizon=30,
            transport_specs=base.transport_specs,
        )
        ledgers = []
        first_starts = []
        for policy_id in ("fifo", "spt", "edd", "cr"):
            result = simulate({"policy_id": policy_id}, scenario, 42)
            first_starts.append(
                next(
                    item.lot_id
                    for item in result.trace
                    if item.event_type == "PROCESS_START" and item.step_id == 1
                )
            )
            ledgers.append(
                {
                    item.identity: item.value
                    for item in result.random_sample_ledger
                    if item.stream_name == "transport"
                }
            )
        self.assertEqual(first_starts, ["L1", "L2", "L2", "L2"])
        self.assertTrue(ledgers[0])
        self.assertTrue(all(ledger == ledgers[0] for ledger in ledgers[1:]))

    def test_real_data_transport_validation_slice_has_manifest_provenance(self) -> None:
        before = {
            model: {
                path.relative_to(DATASETS / model).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (DATASETS / model).rglob("*") if path.is_file()
            }
            for model in ("SMT2020_HVLM", "SMT2020_LVHM")
        }
        for model in ("SMT2020_HVLM", "SMT2020_LVHM"):
            loaded = load_smt2020(
                DATASETS,
                model,
                loader_config=LoaderConfig(mode="transport_validation_slice"),
            )
            self.assertIsNotNone(loaded.scenario)
            assert loaded.scenario is not None
            self.assertIn(
                ("DI_TRANSPORT_RUNTIME_SUPPORTED", "INFO"),
                {(item.code, item.severity) for item in loaded.loader_audit},
            )
            result = simulate({"policy_id": "fifo"}, loaded.scenario, 42)
            self.assertEqual(result.metrics.completed_lots, 1)
            self.assertEqual(result.transport_metrics.completed_count, 1)
            self.assertEqual(
                result.provenance.simulation_config["dataset_provenance"]["manifest_hash"],
                loaded.dataset_manifest.manifest_hash,
            )
        after = {
            model: {
                path.relative_to(DATASETS / model).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (DATASETS / model).rglob("*") if path.is_file()
            }
            for model in ("SMT2020_HVLM", "SMT2020_LVHM")
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
