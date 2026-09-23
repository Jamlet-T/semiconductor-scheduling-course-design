"""证据受限 Release Template runtime 的边界测试。"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (  # noqa: E402
    CQTSpec,
    DedicationSpec,
    LotSpec,
    MachineSpec,
    OperationSpec,
    ReleaseTemplateSpec,
    Scenario,
    TimeDistributionSpec,
    TransportSpec,
)
from fab_scheduler.evaluation.audit import audit_result_invariants  # noqa: E402
from fab_scheduler.simulation.engine import Simulator  # noqa: E402


def operation(*, step_id: int = 1, machine: str = "M1", route: str = "R") -> OperationSpec:
    return OperationSpec(step_id, 1.0, (machine,), route_id=route)


def template(
    template_id: str = "T1",
    *,
    first: float = 0.0,
    interval: TimeDistributionSpec | None = None,
    repeat_limit: int = 100,
    lots_per_repeat: int = 1,
    due: float | None = 5.0,
    operations: tuple[OperationSpec, ...] | None = None,
) -> ReleaseTemplateSpec:
    return ReleaseTemplateSpec(
        template_id=template_id,
        source_row=7,
        lot_prefix="PFX",
        product_id="PROD",
        order_id="ORD",
        operations=operations or (operation(),),
        first_release_time=first,
        interval=interval or TimeDistributionSpec("constant", 2.0),
        repeat_limit=repeat_limit,
        lots_per_repeat=lots_per_repeat,
        relative_due_minutes=due,
        priority=3,
        hot_lot=True,
        quantity_wafers=25,
    )


def scenario(
    templates: tuple[ReleaseTemplateSpec, ...],
    *,
    horizon: float = 5.0,
    **kwargs: object,
) -> Scenario:
    return Scenario(
        scenario_id="RELEASE-TEST",
        dataset_version="test@1",
        machines=(MachineSpec("M1", location_id="L1"),),
        lots=(),
        termination_mode="fixed_horizon",
        horizon=horizon,
        release_templates=templates,
        **kwargs,
    )


class ReleaseRuntimeTests(unittest.TestCase):
    def test_lazy_cap_and_closed_horizon(self) -> None:
        result = Simulator(scenario((template(repeat_limit=1000),))).run()
        releases = [item for item in result.trace if item.event_type == "LOT_RELEASE"]
        self.assertEqual([item.sim_time for item in releases], [0.0, 2.0, 4.0])
        self.assertEqual(result.metrics.released_lots, 3)
        self.assertEqual(result.provenance.simulation_contract_version, "0.1.4")
        self.assertEqual(
            result.provenance.simulation_config["release_runtime"]["id"],
            "fixed_horizon_constant_interval_lots_per_repeat_1",
        )

    def test_exact_horizon_release_is_processed(self) -> None:
        result = Simulator(scenario((template(first=5.0, repeat_limit=1),))).run()
        self.assertEqual(result.metrics.released_lots, 1)
        self.assertEqual(result.trace[0].sim_time, 5.0)

    def test_decimal_interval_release_at_exact_horizon_is_processed(self) -> None:
        result = Simulator(
            scenario(
                (
                    template(
                        interval=TimeDistributionSpec("constant", 0.1),
                        repeat_limit=10,
                    ),
                ),
                horizon=0.3,
            )
        ).run()
        releases = [item for item in result.trace if item.event_type == "LOT_RELEASE"]
        self.assertEqual([item.sim_time for item in releases], [0.0, 0.1, 0.2, 0.3])
        self.assertTrue(
            audit_result_invariants(
                result,
                scenario(
                    (
                        template(
                            interval=TimeDistributionSpec("constant", 0.1),
                            repeat_limit=10,
                        ),
                    ),
                    horizon=0.3,
                ),
            ).passed
        )

    def test_first_release_after_horizon_is_not_materialized(self) -> None:
        result = Simulator(scenario((template(first=6.0, repeat_limit=10),))).run()
        self.assertEqual(result.metrics.released_lots, 0)
        self.assertFalse(result.trace)

    def test_release_cap(self) -> None:
        result = Simulator(scenario((template(repeat_limit=2),), horizon=20)).run()
        self.assertEqual(result.metrics.released_lots, 2)

    def test_due_time_is_relative_to_each_release(self) -> None:
        result = Simulator(scenario((template(repeat_limit=2),), horizon=3)).run()
        releases = [item for item in result.trace if item.event_type == "LOT_RELEASE"]
        self.assertEqual(
            [item.lot_due_time - item.sim_time for item in releases],
            [5.0, 5.0],
        )
        self.assertEqual({item.lot_priority for item in releases}, {3})
        self.assertEqual({item.lot_quantity_wafers for item in releases}, {25})
        complete = [item for item in result.trace if item.event_type == "LOT_COMPLETE"]
        self.assertEqual([item.sim_time for item in complete], [1.0, 3.0])

    def test_id_and_trace_metadata(self) -> None:
        result = Simulator(scenario((template(repeat_limit=1),))).run()
        release = next(item for item in result.trace if item.event_type == "LOT_RELEASE")
        self.assertEqual(release.lot_id, "REL::T1::PFX::r000000::m0000")
        self.assertEqual(release.product_id, "PROD")
        self.assertEqual(release.order_id, "ORD")
        self.assertEqual(release.source_row, 7)
        self.assertEqual(release.release_template_id, "T1")
        self.assertEqual(release.release_repeat_index, 0)
        self.assertEqual(release.release_member_index, 0)
        self.assertTrue(release.hot_lot)

    def test_template_tuple_order_does_not_change_result(self) -> None:
        first = (template("B", first=0), template("A", first=0))
        second = tuple(reversed(first))
        a = Simulator(scenario(first)).run()
        b = Simulator(scenario(second)).run()
        self.assertEqual(a.trace_as_dicts(), b.trace_as_dicts())

    def test_unsupported_boundaries_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Scenario("x", "v", (MachineSpec("M1"),), (), release_templates=(template(),))
        with self.assertRaises(ValueError):
            template(interval=TimeDistributionSpec("uniform", 2.0, 1.0))
        with self.assertRaises(ValueError):
            template(lots_per_repeat=2)
        with self.assertRaises(ValueError):
            template(lots_per_repeat=True)

    def test_scenario_checks_template_route_and_namespace(self) -> None:
        op = operation(machine="UNKNOWN")
        with self.assertRaises(ValueError):
            scenario((template(operations=(op,)),))
        with self.assertRaises(ValueError):
            Scenario(
                "x", "v", (MachineSpec("M1"),),
                (LotSpec("REL::T1::explicit", 0, (operation(),)),),
                termination_mode="fixed_horizon", horizon=1,
                release_templates=(template(repeat_limit=1),),
            )

    def test_template_constraints_and_transport_are_validated(self) -> None:
        op1 = operation(step_id=1)
        op2 = operation(step_id=2)
        with self.assertRaises(ValueError):
            scenario(
                (template(operations=(op1, op2)),),
                cqt_constraints=(CQTSpec("C", "R", 1, 3, 1),),
            )
        with self.assertRaises(ValueError):
            scenario(
                (template(operations=(op1, op2)),),
                dedication_constraints=(DedicationSpec("D", "R", 1, 3),),
            )
        with self.assertRaises(ValueError):
            Scenario(
                "x", "v",
                (MachineSpec("M1", location_id="L1"), MachineSpec("M2", location_id="L2")),
                (),
                termination_mode="fixed_horizon", horizon=1,
                release_templates=(template(operations=(op1, OperationSpec(2, 1, ("M1", "M2"), route_id="R"))),),
                transport_specs=(TransportSpec("L1", "L2", TimeDistributionSpec("constant", 1)),),
            )

    def test_first_operation_has_no_transport_or_release_ledger(self) -> None:
        result = Simulator(scenario((template(repeat_limit=1),))).run()
        self.assertEqual(result.transport_intervals, ())
        self.assertEqual(result.random_sample_ledger, ())

    def test_scenario_is_unchanged_by_run(self) -> None:
        current = scenario((template(repeat_limit=3),))
        before = asdict(current)
        Simulator(current).run()
        self.assertEqual(asdict(current), before)

    def test_independent_audit_checks_release_identity_and_due(self) -> None:
        current = scenario((template(repeat_limit=3),))
        result = Simulator(current).run()
        self.assertTrue(audit_result_invariants(result, current).passed)

        trace = tuple(
            replace(record, lot_due_time=(record.lot_due_time or 0) + 1)
            if record.event_type == "LOT_RELEASE"
            else record
            for record in result.trace
        )
        corrupted = replace(result, trace=trace)
        audit = audit_result_invariants(corrupted, current)
        self.assertFalse(audit.passed)
        self.assertTrue(any("due offset" in item for item in audit.violations))

    def test_independent_audit_rejects_release_provenance_tampering(self) -> None:
        current = scenario((template(repeat_limit=3),))
        result = Simulator(current).run()
        config = dict(result.provenance.simulation_config)
        config["release_runtime"] = {
            **config["release_runtime"],
            "id": "tampered",
        }
        corrupted = replace(
            result,
            provenance=replace(result.provenance, simulation_config=config),
        )
        audit = audit_result_invariants(corrupted, current)
        self.assertFalse(audit.passed)
        self.assertTrue(
            any("release runtime boundary" in item for item in audit.violations)
        )


if __name__ == "__main__":
    unittest.main()
