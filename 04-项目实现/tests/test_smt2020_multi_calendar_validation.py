"""真实 SMT2020 multi-calendar validation slice 的集成回归。

本文件只验证真实 raw -> static -> Scenario 的受限诊断链，以及固定种子下
Calendar PM 的事件/抽样审计；不把该 slice 当作正式 HVLM/LVHM 策略实验，
也不尝试关闭 ``DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT`` blocker。
"""

from __future__ import annotations

from hashlib import sha256
from math import isclose
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.api import simulate  # noqa: E402
from fab_scheduler.data import LoaderConfig, load_smt2020  # noqa: E402
from fab_scheduler.evaluation.audit import audit_result_invariants  # noqa: E402
from fab_scheduler.evaluation.crn_audit import audit_common_random_numbers  # noqa: E402


DATASETS_ROOT = PROJECT_ROOT / "datasets"
SLICE_CONFIG = LoaderConfig(mode="multi_calendar_validation_slice")
MACHINE_ID = "Litho_BE_110#0001"
CALENDAR_IDS = (
    "Litho_BE_110_WK",
    "Litho_BE_110_MN",
    "Litho_BE_110_QT",
)
EXPECTED = {
    "SMT2020_HVLM": {
        "route_id": "r_3",
        "step_id": 491,
        "lot_id": "Init_Lot_3_134",
        "product_id": "part_3",
        "route_file": "route_3.txt",
        "route_row": 492,
        "wip_row": 140,
        "tool_row": 60,
        "wip_fields": ("Init_Lot_3_134", "part_3", "491"),
        "overlap_pair": "QT/MN",
        "foa": (9936.0, 42336.0, 128448.0),
    },
    "SMT2020_LVHM": {
        "route_id": "r_2",
        "step_id": 459,
        "lot_id": "Init_Lot_2_22",
        "product_id": "part_2",
        "route_file": "route_2.txt",
        "route_row": 460,
        "wip_row": 508,
        "tool_row": 60,
        "wip_fields": ("Init_Lot_2_22", "part_2", "459"),
        "overlap_pair": "WK/QT",
        "foa": (10080.0, 43200.0, 131040.0),
    },
}


def _load(model_id: str):
    return load_smt2020(DATASETS_ROOT, model_id, loader_config=SLICE_CONFIG)


def _dataset_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(DATASETS_ROOT)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(DATASETS_ROOT.rglob("*"))
        if path.is_file()
    }


class SMT2020MultiCalendarValidationTests(unittest.TestCase):
    def test_raw_rows_reach_static_and_scenario_with_three_pm_mappings(self) -> None:
        """attach/pmcal/tool/route/WIP 真实行逐层映射，不以均值替代语义。"""
        for model_id, expected in EXPECTED.items():
            with self.subTest(model=model_id):
                loaded = _load(model_id)
                scenario = loaded.scenario
                self.assertIsNotNone(scenario)
                assert scenario is not None

                # Pin the raw records selected by the loader, including their
                # source rows.  The source bytes remain outside the scenario.
                dataset_dir = DATASETS_ROOT / model_id
                pmcal_lines = (dataset_dir / "pmcal.txt").read_text(encoding="utf-8").splitlines()
                attach_lines = (dataset_dir / "attach.txt").read_text(encoding="utf-8").splitlines()
                tool_lines = (dataset_dir / "tool.txt.1l").read_text(encoding="utf-8").splitlines()
                route_lines = (dataset_dir / expected["route_file"]).read_text(encoding="utf-8").splitlines()
                wip_lines = (dataset_dir / "WIP.txt").read_text(encoding="utf-8").splitlines()
                for row, calendar_id in zip((36, 37, 38), CALENDAR_IDS, strict=True):
                    self.assertTrue(pmcal_lines[row - 1].startswith(calendar_id + "\tmtbpm_by_cal\t"))
                for row, calendar_id in zip((47, 48, 49), CALENDAR_IDS, strict=True):
                    self.assertTrue(attach_lines[row - 1].startswith(calendar_id + "\tpm\tstnfam\tLitho_BE_110\t"))
                self.assertTrue(tool_lines[expected["tool_row"] - 1].startswith("Litho_BE_110\t"))
                self.assertTrue(route_lines[expected["route_row"] - 1].startswith(
                    f"{expected['route_id']}\t{expected['step_id']}\t"
                ))
                wip_fields = wip_lines[expected["wip_row"] - 1].split("\t")
                self.assertEqual(
                    (wip_fields[0], wip_fields[1], wip_fields[5]),
                    expected["wip_fields"],
                )

                static = loaded.static_model
                template = next(item for item in static.machine_templates if item.tool_family_id == "Litho_BE_110")
                self.assertEqual(template.source_row, expected["tool_row"])
                self.assertEqual(template.location_id, "Fab")
                self.assertEqual((template.load_minutes, template.unload_minutes), (1.0, 1.0))
                self.assertFalse(template.cascading)
                self.assertIn(MACHINE_ID, template.resource_instance_ids)

                route = next(item for item in static.routes if item.route_id == expected["route_id"])
                operation = next(item for item in route.operations if item.step_id == expected["step_id"])
                self.assertEqual(operation.source_row, expected["route_row"])
                self.assertEqual(operation.tool_family_id, "Litho_BE_110")
                self.assertEqual(operation.processing_basis, "per_piece")
                self.assertEqual(operation.processing.kind, "uniform")
                self.assertIn(MACHINE_ID, operation.eligible_machine_ids)

                wip = next(item for item in static.initial_wip if item.lot_id == expected["lot_id"])
                self.assertEqual(wip.source_row, expected["wip_row"])
                self.assertEqual((wip.product_id, wip.current_step_id), (expected["product_id"], expected["step_id"]))

                self.assertEqual(len(scenario.calendar_pm_specs), 3)
                self.assertEqual([item.machine_id for item in scenario.calendar_pm_specs], [MACHINE_ID] * 3)
                self.assertEqual([item.pm_id for item in scenario.calendar_pm_specs], [f"{item}@{MACHINE_ID}" for item in CALENDAR_IDS])
                self.assertEqual([item.first_start_time for item in scenario.calendar_pm_specs], list(expected["foa"]))
                self.assertEqual([item.interval.mean_minutes for item in scenario.calendar_pm_specs], [10080.0, 43200.0, 131040.0])
                self.assertEqual([item.duration.kind for item in scenario.calendar_pm_specs], ["uniform"] * 3)
                self.assertEqual([item.duration.mean_minutes for item in scenario.calendar_pm_specs], [399.0, 797.4, 1595.4])
                for actual, expected_width in zip(
                    [item.duration.width_minutes for item in scenario.calendar_pm_specs],
                    [79.8, 159.6, 319.2],
                    strict=True,
                ):
                    self.assertAlmostEqual(actual, expected_width)

                lot = scenario.lots[0]
                runtime_operation = lot.operations[0]
                self.assertEqual((lot.lot_id, lot.quantity_wafers), (expected["lot_id"], 25))
                self.assertEqual((runtime_operation.route_id, runtime_operation.step_id), (expected["route_id"], expected["step_id"]))
                self.assertEqual(runtime_operation.processing_basis, "per_piece")
                self.assertEqual(runtime_operation.processing_distribution.kind, "uniform")
                self.assertEqual(runtime_operation.processing_distribution.mean_minutes, operation.processing.parameter_1_minutes)
                self.assertEqual((scenario.machines[0].machine_id, scenario.machines[0].load_minutes, scenario.machines[0].unload_minutes), (MACHINE_ID, 1.0, 1.0))

    def test_warning_and_provenance_keep_multi_calendar_slice_boundary_explicit(self) -> None:
        for model_id in EXPECTED:
            with self.subTest(model=model_id):
                loaded = _load(model_id)
                scenario = loaded.scenario
                assert scenario is not None
                codes = {item.code for item in loaded.loader_audit}
                self.assertTrue({
                    "DI_MULTI_CALENDAR_SLICE_OMITS_FAILURE",
                    "DI_MULTI_CALENDAR_SLICE_OMITS_OTHER_MACHINES",
                    "DI_MULTI_CALENDAR_SLICE_OMITS_ROUTE_HISTORY",
                    "DI_MULTI_CALENDAR_SLICE_INITIAL_HISTORY_UNKNOWN",
                    "DI_MULTI_CALENDAR_SLICE_NOT_FULL_FAB",
                } <= codes)
                provenance = dict(scenario.dataset_provenance.loader_config)
                self.assertEqual(provenance["multi_calendar_slice_omitted_failure_calendar_ids"], "BREAK_Litho")
                self.assertIn("Litho_BE_110#0002", provenance["multi_calendar_slice_omitted_machine_ids"])
                self.assertTrue(provenance["multi_calendar_slice_omitted_prior_route_steps"])
                self.assertTrue(provenance["multi_calendar_slice_omitted_future_route_steps"])
                self.assertTrue(provenance["multi_calendar_slice_omitted_rework_links"])
                for key in (
                    "multi_calendar_slice_initial_setup",
                    "multi_calendar_slice_initial_cqt_history",
                    "multi_calendar_slice_initial_dedication_history",
                    "multi_calendar_slice_initial_wafer_pm_counter",
                ):
                    self.assertEqual(provenance[key], "unknown")
                self.assertIn("does_not_close_DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT", provenance["multi_calendar_slice_scope_boundary"])

    def test_fifo_fixed_seed_has_invariants_and_trace_interval_for_each_pm(self) -> None:
        for model_id in EXPECTED:
            with self.subTest(model=model_id):
                scenario = _load(model_id).scenario
                assert scenario is not None
                result = simulate({"policy_id": "fifo"}, scenario, 42)
                audit = audit_result_invariants(result, scenario)
                self.assertTrue(audit.passed, audit.violations)
                self.assertEqual(result.metrics.end_time, scenario.horizon)
                self.assertEqual(result.metrics.completed_lots, 1)
                self.assertEqual(len(result.processing_intervals), 1)
                self.assertEqual(len(result.load_intervals), 1)
                self.assertEqual(len(result.unload_intervals), 1)
                sample = next(item for item in result.random_sample_ledger if item.stream_name == "processing")
                self.assertAlmostEqual(
                    result.processing_intervals[0].finish - result.processing_intervals[0].start,
                    sample.value * scenario.lots[0].quantity_wafers,
                )
                for pm in scenario.calendar_pm_specs:
                    starts = [item for item in result.trace if item.event_type == "PM_START" and item.pm_id == pm.pm_id]
                    intervals = [item for item in result.pm_intervals if item.pm_id == pm.pm_id]
                    self.assertTrue(starts, pm.pm_id)
                    self.assertTrue(intervals, pm.pm_id)

    def test_real_overlap_clocks_have_one_owner_and_one_stale_occurrence(self) -> None:
        hvlm = simulate({"policy_id": "fifo"}, _load("SMT2020_HVLM").scenario, 42)
        hvlm_overlap = [
            item for item in hvlm.trace
            if isclose(item.sim_time / 1440.0, 89.2, abs_tol=1e-9)
            or isclose(item.sim_time / 1440.0, 89.4, abs_tol=1e-9)
        ]
        self.assertEqual(
            [(item.event_type, item.sim_time / 1440.0, item.pm_id) for item in hvlm_overlap if item.event_type.startswith("PM_START")],
            [
                ("PM_START", 89.2, f"Litho_BE_110_QT@{MACHINE_ID}"),
                ("PM_START_STALE", 89.4, f"Litho_BE_110_MN@{MACHINE_ID}"),
            ],
        )
        self.assertEqual(len([item for item in hvlm.pm_intervals if item.start / 1440.0 >= 89.2]), 1)

        lvhm = simulate({"policy_id": "fifo"}, _load("SMT2020_LVHM").scenario, 42)
        lvhm_same_time = [
            item for item in lvhm.trace
            if item.event_type.startswith("PM_START") and isclose(item.sim_time / 1440.0, 91.0, abs_tol=1e-9)
        ]
        self.assertEqual(
            [(item.event_type, item.pm_id, item.state_after) for item in lvhm_same_time],
            [
                ("PM_START", f"Litho_BE_110_QT@{MACHINE_ID}", "AVAILABILITY:DOWN"),
                ("PM_START_STALE", f"Litho_BE_110_WK@{MACHINE_ID}", "NO_EFFECT"),
            ],
        )

    def test_fifo_spt_share_pm_identity_sample_ledger(self) -> None:
        for model_id in EXPECTED:
            with self.subTest(model=model_id):
                scenario = _load(model_id).scenario
                assert scenario is not None
                fifo = simulate({"policy_id": "fifo"}, scenario, 42)
                spt = simulate({"policy_id": "spt"}, scenario, 42)
                crn = audit_common_random_numbers(fifo, spt)
                self.assertTrue(crn.passed, crn.mismatched_identities)
                fifo_pm = {
                    item.identity: item.value
                    for item in fifo.random_sample_ledger
                    if item.stream_name.startswith("pm_")
                }
                spt_pm = {
                    item.identity: item.value
                    for item in spt.random_sample_ledger
                    if item.stream_name.startswith("pm_")
                }
                self.assertTrue(fifo_pm)
                self.assertEqual(fifo_pm, spt_pm)
                self.assertTrue(set(fifo_pm) <= set(crn.common_identities))

    def test_default_audit_keeps_scenario_none_and_multi_calendar_blocker(self) -> None:
        for model_id in EXPECTED:
            with self.subTest(model=model_id):
                loaded = load_smt2020(DATASETS_ROOT, model_id)
                self.assertIsNone(loaded.scenario)
                self.assertTrue(any(
                    item.code == "DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT"
                    and item.severity == "BLOCKER"
                    for item in loaded.loader_audit
                ))

    def test_loader_and_simulation_do_not_modify_dataset_bytes(self) -> None:
        before = _dataset_hashes()
        for model_id in EXPECTED:
            loaded = _load(model_id)
            assert loaded.scenario is not None
            simulate({"policy_id": "fifo"}, loaded.scenario, 42)
            load_smt2020(DATASETS_ROOT, model_id)
        self.assertEqual(before, _dataset_hashes())


if __name__ == "__main__":
    unittest.main()
