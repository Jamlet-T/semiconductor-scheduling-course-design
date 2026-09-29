"""真实 SMT2020 rework 候选的静态回归与反误判测试。

本文件验证 raw -> static model 的字段、初始 WIP 位置和 attachment 展开；另以
multi-calendar validation slice 做 return 单工序诊断回归。它不构造 route loop，
不把候选段解释为 rework runtime 或 Data Integration closure。audit/default 模式
仍必须保留 blocker。
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
from pathlib import Path
import sys
import unittest


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASETS_ROOT = PROJECT_ROOT / "datasets"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.api import simulate  # noqa: E402
from fab_scheduler.data import LoaderConfig, load_smt2020  # noqa: E402


MODELS = ("SMT2020_HVLM", "SMT2020_LVHM")

# Raw line numbers are 1-based physical file lines, including the header.
# route rows: HVLM route_3.txt:492-494; LVHM route_2.txt:460-462.
# WIP rows: HVLM WIP.txt:41/140; LVHM WIP.txt:504/508.
CANDIDATES = {
    "SMT2020_HVLM": {
        "route_id": "r_3",
        "route_file": "route_3.txt",
        "product_id": "part_3",
        "steps": (491, 492, 493),
        "route_rows": {
            491: {
                "line": 492,
                "DESC": "565_Litho",
                "STNFAM": "Litho_BE_110",
                "PTPER": "per_piece",
                "StepPercent": "",
                "RWKSTEP": "",
                "REWORK": "",
                "RWKTYPE": "",
            },
            492: {
                "line": 493,
                "DESC": "566_Litho_Met",
                "STNFAM": "LithoMet_BE_18",
                "PTPER": "per_lot",
                "StepPercent": "19",
                "RWKSTEP": "",
                "REWORK": "",
                "RWKTYPE": "",
            },
            493: {
                "line": 494,
                "DESC": "567_Litho_Met",
                "STNFAM": "Litho_REG_BE_63",
                "PTPER": "per_lot",
                "StepPercent": "44",
                "RWKSTEP": "491",
                "REWORK": "1",
                "RWKTYPE": "lot",
            },
        },
        "wip_rows": {
            "source": {
                "line": 41,
                "lot": "Init_HotLot_3_3",
                "step": 493,
            },
            "return": {
                "line": 140,
                "lot": "Init_Lot_3_134",
                "step": 491,
            },
        },
        "calendar_pm_by_family": {
            "Litho_BE_110": 3,
            "LithoMet_BE_18": 2,
            "Litho_REG_BE_63": 2,
        },
    },
    "SMT2020_LVHM": {
        "route_id": "r_2",
        "route_file": "route_2.txt",
        "product_id": "part_2",
        "steps": (459, 460, 461),
        "route_rows": {
            459: {
                "line": 460,
                "DESC": "590_Litho",
                "STNFAM": "Litho_BE_110",
                "PTPER": "per_piece",
                "StepPercent": "",
                "RWKSTEP": "",
                "REWORK": "",
                "RWKTYPE": "",
            },
            460: {
                "line": 461,
                "DESC": "591_Litho_Met",
                "STNFAM": "LithoMet_BE_18",
                "PTPER": "per_lot",
                "StepPercent": "15",
                "RWKSTEP": "",
                "REWORK": "",
                "RWKTYPE": "",
            },
            461: {
                "line": 462,
                "DESC": "592_Litho_Met",
                "STNFAM": "Litho_REG_BE_63",
                "PTPER": "per_lot",
                "StepPercent": "42",
                "RWKSTEP": "459",
                "REWORK": "1.4",
                "RWKTYPE": "lot",
            },
        },
        "wip_rows": {
            "source": {
                "line": 504,
                "lot": "Init_Lot_2_18",
                "step": 461,
            },
            "return": {
                "line": 508,
                "lot": "Init_Lot_2_22",
                "step": 459,
            },
        },
        "calendar_pm_by_family": {
            "Litho_BE_110": 3,
            "LithoMet_BE_18": 2,
            "Litho_REG_BE_63": 2,
        },
    },
}


def _file_hashes(model: str) -> dict[str, str]:
    root = DATASETS_ROOT / model
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _rows_with_lines(path: Path) -> list[tuple[int, dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [
            (line_number, {key: (value or "").strip() for key, value in row.items()})
            for line_number, row in enumerate(reader, start=2)
        ]


def _expanded_attachment_counts(static_model: object, family_id: str) -> dict[str, tuple[int, int, int]]:
    """按 raw attachment 的 stnfam/stngrp 目标展开到物理机。

    返回每台机器的 (failure, calendar_pm, wafer_pm)，不连接 runtime calendar。
    """

    machine_templates = static_model.machine_templates  # type: ignore[attr-defined]
    templates_by_family = {item.tool_family_id: item for item in machine_templates}
    family_template = templates_by_family[family_id]
    family_machine_ids = set(family_template.resource_instance_ids)
    machines_by_group: dict[str, set[str]] = defaultdict(set)
    for template in machine_templates:
        machines_by_group[template.group_id].update(template.resource_instance_ids)

    pm_calendars = {item.calendar_id: item for item in static_model.pm_calendars}  # type: ignore[attr-defined]
    counts = {machine_id: [0, 0, 0] for machine_id in family_machine_ids}
    for attachment in static_model.calendar_attachments:  # type: ignore[attr-defined]
        if attachment.resource_type == "stnfam":
            targets = family_machine_ids if attachment.resource_name == family_id else set()
        elif attachment.resource_type == "stngrp":
            targets = family_machine_ids & machines_by_group.get(attachment.resource_name, set())
        else:
            targets = set()

        if attachment.calendar_kind == "down":
            bucket = 0
        elif attachment.calendar_kind == "pm":
            calendar = pm_calendars[attachment.calendar_id]
            bucket = 1 if calendar.interval is not None else 2
        else:
            continue
        for machine_id in targets:
            counts[machine_id][bucket] += 1
    return {machine_id: tuple(values) for machine_id, values in counts.items()}


class SMT2020ReworkCandidateStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before_hashes = {model: _file_hashes(model) for model in MODELS}
        cls.loaded = {
            model: load_smt2020(DATASETS_ROOT, model) for model in MODELS
        }

    @classmethod
    def tearDownClass(cls) -> None:
        for model in MODELS:
            if _file_hashes(model) != cls.before_hashes[model]:
                raise AssertionError(f"static candidate test 修改了原始数据：{model}")

    def test_raw_route_and_initial_wip_rows_anchor_candidate(self) -> None:
        """先核 raw 行，再核 parser，避免把相邻工序或 WIP 位置误读。"""

        for model in MODELS:
            expected = CANDIDATES[model]
            with self.subTest(model=model):
                route_rows = _rows_with_lines(
                    DATASETS_ROOT / model / expected["route_file"]
                )
                by_step = {int(row["STEP"]): (line, row) for line, row in route_rows}
                for step, fields in expected["route_rows"].items():
                    line, row = by_step[step]
                    self.assertEqual(line, fields["line"])
                    for key in (
                        "DESC", "STNFAM", "PTPER", "StepPercent",
                        "RWKSTEP", "REWORK", "RWKTYPE",
                    ):
                        self.assertEqual(row[key], fields[key], f"{model}:{line}:{key}")

                wip_rows = _rows_with_lines(DATASETS_ROOT / model / "WIP.txt")
                by_lot = {row["LOT"]: (line, row) for line, row in wip_rows}
                for position, fields in expected["wip_rows"].items():
                    line, row = by_lot[fields["lot"]]
                    self.assertEqual(line, fields["line"])
                    self.assertEqual(row["PART"], expected["product_id"])
                    self.assertEqual(int(row["CURSTEP"]), fields["step"])
                    self.assertEqual(row["ORDER"], "O_Init_WIP")

    def test_parser_preserves_source_middle_return_and_static_constraints(self) -> None:
        """锁定 source/middle/return 画像，但不推导 route loop。"""

        for model in MODELS:
            expected = CANDIDATES[model]
            with self.subTest(model=model):
                loaded = self.loaded[model]
                route = next(
                    route for route in loaded.static_model.routes
                    if route.route_id == expected["route_id"]
                )
                operations = {
                    operation.step_id: operation for operation in route.operations
                }
                # The route contains all operations; this test freezes only the
                # selected three-step candidate, not a truncated route object.
                self.assertTrue(set(expected["steps"]).issubset(operations))

                source_step = expected["steps"][-1]
                return_step = expected["steps"][0]
                middle_step = expected["steps"][1]
                source = operations[source_step]
                middle = operations[middle_step]
                returned = operations[return_step]

                self.assertEqual((source.rework_step_id, source.rework_scope), (return_step, "lot"))
                self.assertEqual(source.rework_percent, 1.0 if model.endswith("HVLM") else 1.4)
                self.assertIsNone(returned.rework_step_id)
                self.assertIsNone(middle.rework_step_id)
                self.assertEqual(
                    (source.sample_percent, middle.sample_percent),
                    (44.0, 19.0) if model.endswith("HVLM") else (42.0, 15.0),
                )

                # Candidate segment is static non-cascade/non-batch/no setup/interval/CQT.
                for operation in (returned, middle, source):
                    self.assertIsNone(operation.batch_min_wafers)
                    self.assertIsNone(operation.batch_max_wafers)
                    self.assertIsNone(operation.required_setup)
                    self.assertIsNone(operation.setup_override_minutes)
                    self.assertIsNone(operation.batch_interval_minutes)
                    self.assertIsNone(operation.part_interval_minutes)
                    self.assertIsNone(operation.cqt_target_step_id)
                    self.assertIsNone(operation.cqt_limit_minutes)
                    self.assertTrue(operation.eligible_machine_ids)

                products = {
                    product.product_id: product.route_id
                    for product in loaded.static_model.products
                }
                wip_by_lot = {
                    item.lot_id: item for item in loaded.static_model.initial_wip
                }
                for position, fields in expected["wip_rows"].items():
                    wip = wip_by_lot[fields["lot"]]
                    self.assertEqual(
                        (wip.product_id, products[wip.product_id], wip.current_step_id),
                        (expected["product_id"], expected["route_id"], fields["step"]),
                    )
                    self.assertEqual(wip.source_row, fields["line"])

    def test_candidate_qualified_machines_have_raw_load_unload_and_attachment_intersection(self) -> None:
        """核对所有真实合格机的 raw L/U 与 attachment 交集，仍只作静态证据。"""

        for model in MODELS:
            expected = CANDIDATES[model]
            with self.subTest(model=model):
                static = self.loaded[model].static_model
                route = next(route for route in static.routes if route.route_id == expected["route_id"])
                operations = {
                    operation.step_id: operation for operation in route.operations
                }
                family_ids = {
                    operations[step].tool_family_id for step in expected["steps"]
                }
                templates = {
                    item.tool_family_id: item for item in static.machine_templates
                }
                machine_to_template = {
                    machine_id: template
                    for template in static.machine_templates
                    for machine_id in template.resource_instance_ids
                }
                for family_id in family_ids:
                    template = templates[family_id]
                    counts = _expanded_attachment_counts(static, family_id)
                    qualified_ids = set(template.resource_instance_ids)
                    self.assertEqual(set(counts), qualified_ids)
                    self.assertEqual(
                        Counter(counts.values()),
                        Counter({
                            (1, expected["calendar_pm_by_family"][family_id], 0):
                            len(qualified_ids)
                        }),
                    )
                    for machine_id in qualified_ids:
                        machine = machine_to_template[machine_id]
                        self.assertEqual(
                            (machine.location_id, machine.cascading,
                             machine.load_minutes, machine.unload_minutes),
                            ("Fab", False, 1.0, 1.0),
                        )

    def test_default_and_explicit_audit_keep_rework_blocker_and_no_scenario(self) -> None:
        """反误判：静态候选存在不等于 audit/default 已支持 rework。"""

        for model in MODELS:
            for config in (None, LoaderConfig(mode="audit")):
                with self.subTest(model=model, config="default" if config is None else "audit"):
                    loaded = load_smt2020(
                        DATASETS_ROOT, model, loader_config=config
                    )
                    self.assertIsNone(loaded.scenario)
                    self.assertTrue(
                        any(
                            entry.code == "DI_UNSUPPORTED_REWORK"
                            and entry.severity == "BLOCKER"
                            for entry in loaded.loader_audit
                        )
                    )
                    self.assertTrue(
                        any(
                            entry.code == "DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE"
                            and entry.severity == "BLOCKER"
                            for entry in loaded.loader_audit
                        )
                    )

    def test_return_step_cross_checks_multi_calendar_per_piece_load_unload_slice(self) -> None:
        """两模型 return raw step 与受限 per-piece + L/U 诊断链保持一致。"""

        slice_config = LoaderConfig(mode="multi_calendar_validation_slice")
        expected_warning_codes = {
            "DI_MULTI_CALENDAR_SLICE_OMITS_FAILURE",
            "DI_MULTI_CALENDAR_SLICE_OMITS_OTHER_MACHINES",
            "DI_MULTI_CALENDAR_SLICE_OMITS_ROUTE_HISTORY",
            "DI_MULTI_CALENDAR_SLICE_INITIAL_HISTORY_UNKNOWN",
            "DI_MULTI_CALENDAR_SLICE_NOT_FULL_FAB",
        }
        for model in MODELS:
            with self.subTest(model=model):
                expected = CANDIDATES[model]
                loaded = load_smt2020(
                    DATASETS_ROOT, model, loader_config=slice_config
                )
                scenario = loaded.scenario
                self.assertIsNotNone(scenario)
                assert scenario is not None

                return_step = expected["steps"][0]
                route = next(
                    item for item in loaded.static_model.routes
                    if item.route_id == expected["route_id"]
                )
                operation = next(
                    item for item in route.operations if item.step_id == return_step
                )
                self.assertEqual(
                    (operation.step_id, operation.source_row, operation.processing_basis),
                    (
                        return_step,
                        expected["route_rows"][return_step]["line"],
                        "per_piece",
                    ),
                )
                self.assertEqual(
                    scenario.lots[0].lot_id,
                    expected["wip_rows"]["return"]["lot"],
                )
                self.assertEqual(scenario.lots[0].quantity_wafers, 25)
                self.assertEqual(
                    (scenario.lots[0].operations[0].step_id,
                     scenario.lots[0].operations[0].processing_basis),
                    (return_step, "per_piece"),
                )
                self.assertEqual(
                    (scenario.machines[0].load_minutes, scenario.machines[0].unload_minutes),
                    (1.0, 1.0),
                )

                result = simulate({"policy_id": "fifo"}, scenario, 42)
                processing_samples = [
                    item for item in result.random_sample_ledger
                    if item.stream_name == "processing"
                ]
                self.assertEqual(len(processing_samples), 1)
                self.assertEqual(len(result.processing_intervals), 1)
                self.assertAlmostEqual(
                    result.processing_intervals[0].finish
                    - result.processing_intervals[0].start,
                    processing_samples[0].value * scenario.lots[0].quantity_wafers,
                )
                self.assertEqual(
                    [item.finish - item.start for item in result.load_intervals],
                    [1.0],
                )
                self.assertEqual(
                    [item.finish - item.start for item in result.unload_intervals],
                    [1.0],
                )

                provenance = dict(scenario.dataset_provenance.loader_config)
                self.assertEqual(
                    (provenance["multi_calendar_slice_raw_load_minutes"],
                     provenance["multi_calendar_slice_raw_unload_minutes"]),
                    ("1.0", "1.0"),
                )
                self.assertTrue(
                    expected_warning_codes <= {
                        item.code for item in loaded.loader_audit
                    }
                )
                self.assertTrue(provenance["multi_calendar_slice_omitted_machine_ids"])
                self.assertTrue(provenance["multi_calendar_slice_omitted_rework_links"])
                self.assertFalse(
                    any(item.event_type.startswith("REWORK") for item in result.trace)
                )
                self.assertEqual(
                    provenance["multi_calendar_slice_omitted_failure_calendar_ids"],
                    "BREAK_Litho",
                )
                self.assertIn(
                    "does_not_close_DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT",
                    provenance["multi_calendar_slice_scope_boundary"],
                )


if __name__ == "__main__":
    unittest.main()
