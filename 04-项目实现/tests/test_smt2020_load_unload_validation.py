"""真实 SMT2020 LOAD/UNLOAD 两工序诊断 slice。

这些测试只确认 loader 的数据选择、物理机字段和 provenance；slice 仍保留
全量数据集的 blocker，不能当作 HVLM/LVHM 正式性能实验。
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.data import LoaderConfig, load_smt2020
from fab_scheduler.api import simulate
from fab_scheduler.evaluation.audit import audit_result_invariants
from fab_scheduler.evaluation.crn_audit import audit_common_random_numbers


DATASETS_ROOT = PROJECT_ROOT / "datasets"


class SMT2020LoadUnloadValidationTests(unittest.TestCase):
    def _load(self, model_id: str):
        return load_smt2020(
            DATASETS_ROOT,
            model_id,
            loader_config=LoaderConfig(mode="load_unload_validation_slice"),
        )

    def test_real_hvlm_and_lvhm_slice_selects_requested_initial_wip(self) -> None:
        expected_lots = {
            "SMT2020_HVLM": "Init_Lot_3_1361",
            "SMT2020_LVHM": "Init_Lot_3_290",
        }
        for model_id, expected_lot_id in expected_lots.items():
            with self.subTest(model=model_id):
                loaded = self._load(model_id)
                self.assertIsNotNone(loaded.scenario)
                scenario = loaded.scenario
                assert scenario is not None
                self.assertEqual(len(scenario.lots), 1)
                lot = scenario.lots[0]
                self.assertEqual(lot.lot_id, expected_lot_id)
                self.assertEqual(
                    (lot.product_id, lot.order_id, lot.quantity_wafers),
                    ("part_3", "O_Init_WIP", 25),
                )
                self.assertTrue(lot.is_initial_wip)
                self.assertEqual(lot.initial_operation_index, 0)
                self.assertEqual(lot.source_row, loaded.static_model.initial_wip[
                    next(
                        index
                        for index, item in enumerate(loaded.static_model.initial_wip)
                        if item.lot_id == expected_lot_id
                    )
                ].source_row)

                self.assertEqual([op.step_id for op in lot.operations], [18, 19])
                self.assertEqual(
                    [op.tool_group_id for op in lot.operations],
                    ["DE_FE_1", "DE_FE_86"],
                )
                self.assertTrue(
                    all(
                        op.processing_basis == "per_lot"
                        and op.processing_distribution is not None
                        and op.processing_distribution.kind == "uniform"
                        for op in lot.operations
                    )
                )

    def test_slice_preserves_one_minute_load_unload_and_fab_transport(self) -> None:
        for model_id in ("SMT2020_HVLM", "SMT2020_LVHM"):
            with self.subTest(model=model_id):
                loaded = self._load(model_id)
                scenario = loaded.scenario
                assert scenario is not None
                self.assertEqual(
                    [machine.machine_id for machine in scenario.machines],
                    ["DE_FE_1#0001", "DE_FE_86#0001"],
                )
                self.assertEqual(
                    [
                        (machine.load_minutes, machine.unload_minutes,
                         machine.cascading, machine.location_id)
                        for machine in scenario.machines
                    ],
                    [(1.0, 1.0, False, "Fab"), (1.0, 1.0, False, "Fab")],
                )
                self.assertEqual(len(scenario.transport_specs), 1)
                transport = scenario.transport_specs[0]
                self.assertEqual(
                    (transport.from_location, transport.to_location),
                    ("Fab", "Fab"),
                )
                self.assertEqual(
                    (transport.duration.kind, transport.duration.mean_minutes,
                     transport.duration.width_minutes),
                    ("uniform", 7.5, 2.5),
                )

    def test_slice_provenance_records_selection_and_omissions(self) -> None:
        loaded = self._load("SMT2020_HVLM")
        scenario = loaded.scenario
        assert scenario is not None
        items = dict(scenario.dataset_provenance.loader_config)
        self.assertEqual(items["mode"], "load_unload_validation_slice")
        self.assertEqual(items["load_unload_slice_route"], "r_3")
        self.assertEqual(items["load_unload_slice_steps"], "18->19")
        self.assertEqual(items["load_unload_slice_lot_id"], "Init_Lot_3_1361")
        self.assertEqual(
            items["load_unload_slice_selected_machine_ids"],
            "DE_FE_1#0001,DE_FE_86#0001",
        )
        self.assertIn("DE_FE_1#0002", items["load_unload_slice_omitted_machine_ids"])
        self.assertIn("DE_FE_86#0002", items["load_unload_slice_omitted_machine_ids"])
        self.assertEqual(
            items["load_unload_slice_raw_load_minutes"],
            "DE_FE_1:1.0;DE_FE_86:1.0",
        )
        self.assertEqual(
            items["load_unload_slice_raw_unload_minutes"],
            "DE_FE_1:1.0;DE_FE_86:1.0",
        )
        self.assertEqual(items["load_unload_slice_transport_pairs"], "Fab->Fab")
        self.assertIn("processing_basis=per_lot", items["load_unload_slice_profile_constraints"])
        self.assertIn("sampling=None", items["load_unload_slice_profile_constraints"])
        self.assertTrue(
            any(
                entry.code == "DI_LOAD_UNLOAD_SLICE_OMITS_CALENDAR_ATTACHMENTS"
                for entry in loaded.loader_audit
            )
        )
        self.assertTrue(
            any(
                entry.code == "DI_LOAD_UNLOAD_SLICE_INITIAL_HISTORY_UNKNOWN"
                for entry in loaded.loader_audit
            )
        )

    def test_audit_mode_remains_scenario_none_and_load_unload_blocker_open(self) -> None:
        for model_id in ("SMT2020_HVLM", "SMT2020_LVHM"):
            with self.subTest(model=model_id):
                loaded = load_smt2020(DATASETS_ROOT, model_id)
                self.assertIsNone(loaded.scenario)
                self.assertTrue(
                    any(
                        entry.code == "DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE"
                        and entry.severity == "BLOCKER"
                        for entry in loaded.loader_audit
                    )
                )

    def test_real_two_step_slice_runs_with_distinct_physical_phases(self) -> None:
        for model_id in ("SMT2020_HVLM", "SMT2020_LVHM"):
            with self.subTest(model=model_id):
                scenario = self._load(model_id).scenario
                assert scenario is not None
                fifo = simulate({"policy_id": "fifo"}, scenario, 42)
                spt = simulate({"policy_id": "spt"}, scenario, 42)
                self.assertEqual(fifo.metrics.completed_lots, 1)
                self.assertEqual(len(fifo.processing_intervals), 2)
                self.assertEqual(len(fifo.load_intervals), 2)
                self.assertEqual(len(fifo.unload_intervals), 2)
                self.assertEqual(len(fifo.transport_intervals), 1)
                self.assertEqual(
                    [item.event_type for item in fifo.trace].count("PROCESS_FINISH"),
                    2,
                )
                self.assertEqual(
                    [item.event_type for item in fifo.trace].count("PROCESS_START"),
                    2,
                )
                physical_duration = sum(
                    item.finish - item.start
                    for intervals in (
                        fifo.processing_intervals,
                        fifo.load_intervals,
                        fifo.unload_intervals,
                        fifo.transport_intervals,
                    )
                    for item in intervals
                )
                lot_id = scenario.lots[0].lot_id
                self.assertAlmostEqual(fifo.completion_times[lot_id], physical_duration)
                self.assertTrue(audit_result_invariants(fifo, scenario).passed)
                self.assertTrue(audit_common_random_numbers(fifo, spt).passed)
                runtime = fifo.provenance.simulation_config["load_unload_runtime"]
                self.assertEqual(runtime["canonical_completion"], "PROCESS_FINISH")
                self.assertEqual(runtime["supported_scope"], "noncascade_nonbatch")


if __name__ == "__main__":
    unittest.main()
