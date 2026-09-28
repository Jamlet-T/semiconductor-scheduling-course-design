"""真实 SMT2020 raw → static model → validation Scenario 数据链。"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest

from fab_scheduler.api import simulate
from fab_scheduler.data import (
    LoaderConfig,
    SMT2020_LOADER_CONTRACT_VERSION,
    build_dataset_manifest,
    LoaderError,
    load_smt2020,
    parse_distribution,
    to_runtime_distribution,
)
from fab_scheduler.evaluation.audit import audit_result_invariants


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATASETS_ROOT = REPOSITORY_ROOT / "datasets"
MODELS = ("SMT2020_HVLM", "SMT2020_LVHM")


def raw_hashes(model: str) -> dict[str, str]:
    root = DATASETS_ROOT / model
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class DatasetManifestTests(unittest.TestCase):
    def test_manifest_is_stable_and_does_not_use_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_root, second_root = Path(first), Path(second)
            (first_root / "b.txt").write_bytes(b"B")
            (first_root / "a.txt").write_bytes(b"A")
            (second_root / "a.txt").write_bytes(b"A")
            (second_root / "b.txt").write_bytes(b"B")
            left = build_dataset_manifest(first_root, model_name="MODEL", loader_version="test")
            right = build_dataset_manifest(second_root, model_name="MODEL", loader_version="test")
            self.assertEqual(left.manifest_hash, right.manifest_hash)
            self.assertEqual([item.relative_path for item in left.files], ["a.txt", "b.txt"])

    def test_content_change_changes_manifest_hash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "data.txt"
            path.write_bytes(b"before")
            before = build_dataset_manifest(root, model_name="MODEL", loader_version="test")
            path.write_bytes(b"after")
            after = build_dataset_manifest(root, model_name="MODEL", loader_version="test")
            self.assertNotEqual(before.manifest_hash, after.manifest_hash)


class SMT2020LoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before = {model: raw_hashes(model) for model in MODELS}
        cls.loaded = {model: load_smt2020(DATASETS_ROOT, model) for model in MODELS}

    @classmethod
    def tearDownClass(cls) -> None:
        for model in MODELS:
            if raw_hashes(model) != cls.before[model]:
                raise AssertionError(f"loader 修改了原始数据：{model}")

    def test_public_contract_and_detected_models(self) -> None:
        self.assertEqual(SMT2020_LOADER_CONTRACT_VERSION, "0.1.6")
        self.assertEqual(set(self.loaded), set(MODELS))
        for model, loaded in self.loaded.items():
            self.assertEqual(loaded.dataset_manifest.model_name, model)
            self.assertEqual(len(loaded.dataset_manifest.manifest_hash), 64)
            self.assertIn(loaded.dataset_manifest.manifest_hash, loaded.dataset_manifest.dataset_version)
            self.assertIsNone(loaded.scenario)
        with self.assertRaises(ValueError):
            LoaderConfig(mode="audit", validation_product_id="part_3")
        with self.assertRaises(ValueError):
            LoaderConfig(
                mode="validation_slice",
                validation_transport_pair=("Fab", "Fab"),
            )
        with self.assertRaises(ValueError):
            LoaderConfig(
                mode="transport_validation_slice",
                validation_transport_pair=("Fab",),  # type: ignore[arg-type]
            )

    def test_reconciled_entity_counts(self) -> None:
        expected = {
            "SMT2020_HVLM": (2, 2, 926, 105, 1043, 2255, 5),
            "SMT2020_LVHM": (10, 10, 4013, 105, 913, 2156, 21),
        }
        for model, values in expected.items():
            stats = self.loaded[model].statistics
            actual = (
                stats["products"], stats["routes"], stats["operations"],
                stats["tool_groups"], stats["physical_machines"],
                stats["initial_wip_lots"], stats["release_templates"],
            )
            self.assertEqual(actual, values)
            self.assertEqual(stats["raw_rows"]["route"], stats["parsed_rows"]["operations"])
            self.assertEqual(stats["raw_rows"]["wip"], stats["parsed_rows"]["initial_wip"])

    def test_route_order_qualification_and_references_are_closed(self) -> None:
        for loaded in self.loaded.values():
            for route in loaded.static_model.routes:
                self.assertEqual([op.step_id for op in route.operations], list(range(1, len(route.operations) + 1)))
                known = {op.step_id for op in route.operations}
                for op in route.operations:
                    self.assertTrue(op.eligible_machine_ids)
                    if op.cqt_target_step_id is not None:
                        self.assertIn(op.cqt_target_step_id, known)
                        self.assertGreater(op.cqt_target_step_id, op.step_id)
                    if op.dedication_target_step_id is not None:
                        self.assertIn(op.dedication_target_step_id, known)
                        self.assertGreater(op.dedication_target_step_id, op.step_id)
                    if op.rework_step_id is not None:
                        self.assertIn(op.rework_step_id, known)
                        self.assertLess(op.rework_step_id, op.step_id)
                        self.assertEqual(op.rework_scope, "lot")

    def test_real_rework_profile_and_initial_wip_mapping_are_preserved(self) -> None:
        """锁定真实 RWK 静态画像，不把尚未实现的 rework runtime 当作已闭环。"""
        expected_rework_counts = {
            "SMT2020_HVLM": 14,
            "SMT2020_LVHM": 52,
        }
        expected_rework_source_sampling_100 = {
            "SMT2020_HVLM": 6,
            "SMT2020_LVHM": 22,
        }
        expected_initial_wip = {
            "SMT2020_HVLM": ("Init_Lot_4_194", "part_4", "r_4", 256),
            "SMT2020_LVHM": ("Init_Lot_2_18", "part_2", "r_2", 461),
        }
        expected_rework_wip_positions = {
            "SMT2020_HVLM": {"return": 88, "middle": 5, "source": 7},
            "SMT2020_LVHM": {"return": 51, "middle": 6, "source": 3},
        }
        expected_dedication_diagnostics = {
            "SMT2020_HVLM": (5, 13),
            "SMT2020_LVHM": (17, 42),
        }

        for model in MODELS:
            with self.subTest(model=model):
                static = self.loaded[model].static_model
                operations = tuple(
                    operation
                    for route in static.routes
                    for operation in route.operations
                )
                rework_operations = tuple(
                    operation
                    for operation in operations
                    if operation.rework_step_id is not None
                )
                self.assertEqual(
                    len(rework_operations), expected_rework_counts[model]
                )
                self.assertEqual(
                    sum(source.sample_percent == 100.0 for source in rework_operations),
                    expected_rework_source_sampling_100[model],
                )
                for source in rework_operations:
                    assert source.rework_step_id is not None
                    self.assertEqual(
                        source.step_id - source.rework_step_id,
                        2,
                    )
                    self.assertEqual(source.rework_scope, "lot")
                    intermediate = next(
                        operation
                        for operation in operations
                        if operation.route_id == source.route_id
                        and operation.step_id == source.rework_step_id + 1
                    )
                    # raw profile 为 source 与中间工序都声明 StepPercent。
                    self.assertIsNotNone(source.sample_percent)
                    self.assertIsNotNone(intermediate.sample_percent)

                operation_by_route_step = {
                    (operation.route_id, operation.step_id): operation
                    for operation in operations
                }
                for source in rework_operations:
                    assert source.rework_step_id is not None
                    return_operation = operation_by_route_step[
                        (source.route_id, source.rework_step_id)
                    ]
                    self.assertEqual(
                        set(source.eligible_machine_ids)
                        & set(return_operation.eligible_machine_ids),
                        set(),
                    )
                    self.assertNotEqual(
                        source.tool_family_id, return_operation.tool_family_id
                    )
                    self.assertIsNone(return_operation.sample_percent)

                dedication_edges = tuple(
                    operation for operation in operations
                    if operation.dedication_target_step_id is not None
                )
                endpoint_count = sum(
                    any(
                        edge.route_id == source.route_id
                        and edge.dedication_target_step_id == source.rework_step_id
                        for edge in dedication_edges
                    )
                    for source in rework_operations
                )
                closed_span_intersection_count = sum(
                    any(
                        edge.route_id == source.route_id
                        and edge.step_id <= source.step_id
                        and edge.dedication_target_step_id >= source.rework_step_id
                        for edge in dedication_edges
                    )
                    for source in rework_operations
                )
                self.assertEqual(
                    (endpoint_count, closed_span_intersection_count),
                    expected_dedication_diagnostics[model],
                )
                cqt_edges = tuple(
                    operation for operation in operations
                    if operation.cqt_target_step_id is not None
                )
                self.assertEqual(
                    sum(
                        any(
                            edge.route_id == source.route_id
                            and edge.step_id <= source.step_id
                            and edge.cqt_target_step_id >= source.rework_step_id
                            for edge in cqt_edges
                        )
                        for source in rework_operations
                    ),
                    0,
                )

                products = {
                    product.product_id: product.route_id
                    for product in static.products
                }
                expected_lot, expected_product, expected_route, expected_step = (
                    expected_initial_wip[model]
                )
                wip = next(item for item in static.initial_wip if item.lot_id == expected_lot)
                self.assertEqual(
                    (wip.lot_id, wip.product_id, products[wip.product_id], wip.current_step_id),
                    (expected_lot, expected_product, expected_route, expected_step),
                )
                current_operation = next(
                    operation
                    for operation in operations
                    if operation.route_id == products[wip.product_id]
                    and operation.step_id == wip.current_step_id
                )
                self.assertEqual(current_operation.route_id, expected_route)
                self.assertIsNotNone(current_operation.rework_step_id)
                self.assertTrue(current_operation.eligible_machine_ids)
                self.assertEqual(
                    self.loaded[model].statistics["initial_wip_rework_position_counts"],
                    expected_rework_wip_positions[model],
                )
                history_warning = next(
                    item for item in self.loaded[model].loader_audit
                    if item.code == "DI_REWORK_INITIAL_HISTORY_UNKNOWN"
                )
                self.assertEqual(history_warning.severity, "WARNING")
                self.assertEqual(
                    {key: dict(history_warning.context)[key] for key in ("return", "middle", "source")},
                    {key: str(value) for key, value in expected_rework_wip_positions[model].items()},
                )

    def test_setup_batch_cqt_and_dedication_are_actually_mapped(self) -> None:
        expected = {
            "SMT2020_HVLM": (28, 66, 17, 18),
            "SMT2020_LVHM": (135, 264, 83, 73),
        }
        for model, values in expected.items():
            loaded = self.loaded[model]
            stats = loaded.statistics
            self.assertEqual((stats["batch_operations"], stats["cqt_constraints"], stats["cross_step_cqt_constraints"], stats["dedication_constraints"]), values)
            self.assertEqual(len(loaded.static_model.setup_transitions), 13)
            for route in loaded.static_model.routes:
                for op in route.operations:
                    if op.batch_min_wafers is not None:
                        self.assertEqual(op.processing_basis, "per_batch")
                        self.assertLessEqual(op.batch_min_wafers, op.batch_max_wafers)
        self.assertEqual(self.loaded["SMT2020_HVLM"].statistics["stochastic_sampling_operations"], 149)
        self.assertEqual(self.loaded["SMT2020_LVHM"].statistics["stochastic_sampling_operations"], 662)
        self.assertEqual(self.loaded["SMT2020_HVLM"].statistics["rework_scope_counts"], {"lot": 14})
        self.assertEqual(self.loaded["SMT2020_LVHM"].statistics["rework_scope_counts"], {"lot": 52})

    def test_real_implant_minrun_three_table_links_remain_explicit(self) -> None:
        expected = {"SMT2020_HVLM": (35, 24), "SMT2020_LVHM": (169, 25)}
        for model, (operation_count, machine_count) in expected.items():
            with self.subTest(model=model):
                static = self.loaded[model].static_model
                members = tuple(static.setup_group_members)
                self.assertEqual(len(members), 9)
                self.assertEqual({item.setup_group for item in members}, {"Implant_Gas"})
                self.assertEqual({item.minimum_run for item in members}, {7})
                configured_setups = {item.setup_id for item in members}
                self.assertEqual(len(configured_setups), 9)
                templates = tuple(
                    item for item in static.machine_templates
                    if item.setup_group == "Implant_Gas"
                )
                families = {item.tool_family_id for item in templates}
                self.assertEqual(families, {"Implant_128", "Implant_132", "Implant_91"})
                self.assertEqual(sum(item.quantity for item in templates), machine_count)
                operations = tuple(
                    operation
                    for route in static.routes
                    for operation in route.operations
                    if operation.tool_family_id in families
                )
                self.assertEqual(len(operations), operation_count)
                self.assertTrue(all(item.required_setup in configured_setups for item in operations))
                self.assertTrue(all(item.processing_basis == "per_piece" for item in operations))
                self.assertTrue(all(item.batch_min_wafers is None for item in operations))

    def test_transport_validation_slice_honors_product_selector(self) -> None:
        loaded = load_smt2020(
            DATASETS_ROOT,
            "SMT2020_HVLM",
            loader_config=LoaderConfig(
                mode="transport_validation_slice",
                validation_product_id="part_4",
            ),
        )
        self.assertIsNotNone(loaded.scenario)
        assert loaded.scenario is not None
        self.assertIn(":r_4:", loaded.scenario.scenario_id)
        self.assertIn(
            ("validation_product_id", "part_4"),
            loaded.scenario.dataset_provenance.loader_config,
        )

        with self.assertRaises(ValueError):
            load_smt2020(
                DATASETS_ROOT,
                "SMT2020_HVLM",
                loader_config=LoaderConfig(
                    mode="transport_validation_slice",
                    validation_product_id="not_a_product",
                ),
            )

    def test_real_missing_transport_pair_reaches_runtime_audit(self) -> None:
        for model in MODELS:
            loaded = load_smt2020(
                DATASETS_ROOT,
                model,
                loader_config=LoaderConfig(
                    mode="transport_validation_slice",
                    validation_transport_pair=("Delay", "Fab"),
                ),
            )
            self.assertIsNotNone(loaded.scenario)
            assert loaded.scenario is not None
            result = simulate({"policy_id": "fifo"}, loaded.scenario, 42)
            self.assertEqual(result.metrics.completed_lots, 1)
            self.assertEqual(result.transport_metrics.missing_pair_count, 1)
            self.assertEqual(
                result.transport_metrics.missing_pairs,
                (("Delay", "Fab", 1),),
            )
            self.assertFalse(
                [
                    item
                    for item in result.random_sample_ledger
                    if item.stream_name == "transport"
                ]
            )
            self.assertIn(
                ("validation_transport_pair", "Delay->Fab"),
                loaded.scenario.dataset_provenance.loader_config,
            )

    def test_route_semantic_invalid_fields_are_rejected(self) -> None:
        def load_variant(mutator):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder) / "datasets"
                shutil.copytree(DATASETS_ROOT / "SMT2020_HVLM", root / "SMT2020_HVLM")
                route_path = root / "SMT2020_HVLM" / "route_3.txt"
                lines = route_path.read_text(encoding="utf-8").splitlines()
                header = lines[0].split("\t")
                for index in range(1, len(lines)):
                    fields = lines[index].split("\t")
                    if fields[1] == "2":
                        mutator(dict(zip(header, fields)), fields, header)
                        lines[index] = "\t".join(fields)
                        break
                route_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return load_smt2020(root, "SMT2020_HVLM")

        invalid_variants = {
            "sampling_zero": lambda row, fields, header: fields.__setitem__(header.index("StepPercent"), "0"),
            "sampling_over": lambda row, fields, header: fields.__setitem__(header.index("StepPercent"), "100.1"),
            "incomplete_rework": lambda row, fields, header: fields.__setitem__(header.index("RWKSTEP"), "1"),
            "rework_over": lambda row, fields, header: [
                fields.__setitem__(header.index("RWKSTEP"), "1"),
                fields.__setitem__(header.index("REWORK"), "100.1"),
                fields.__setitem__(header.index("RWKTYPE"), "lot"),
            ],
            "rework_target_missing": lambda row, fields, header: [
                fields.__setitem__(header.index("RWKSTEP"), "9999"),
                fields.__setitem__(header.index("REWORK"), "1"),
                fields.__setitem__(header.index("RWKTYPE"), "lot"),
            ],
            "rework_target_forward": lambda row, fields, header: [
                fields.__setitem__(header.index("RWKSTEP"), "2"),
                fields.__setitem__(header.index("REWORK"), "1"),
                fields.__setitem__(header.index("RWKTYPE"), "lot"),
            ],
        }
        expected_codes = {
            "sampling_zero": "DI_INVALID_SAMPLING_PERCENT",
            "sampling_over": "DI_INVALID_SAMPLING_PERCENT",
            "incomplete_rework": "DI_INCOMPLETE_REWORK_FIELDS",
            "rework_over": "DI_INVALID_REWORK_PERCENT",
            "rework_target_missing": "DI_INVALID_REWORK_LINK",
            "rework_target_forward": "DI_INVALID_REWORK_LINK",
        }
        for name, mutator in invalid_variants.items():
            with self.subTest(name=name):
                with self.assertRaises(LoaderError) as raised:
                    load_variant(mutator)
                self.assertIn(expected_codes[name], {entry.code for entry in raised.exception.entries})

        loaded = load_variant(
            lambda row, fields, header: [
                fields.__setitem__(header.index("RWKSTEP"), "1"),
                fields.__setitem__(header.index("REWORK"), "1"),
                fields.__setitem__(header.index("RWKTYPE"), "wafer"),
            ]
        )
        self.assertIn(
            "DI_UNSUPPORTED_REWORK_SCOPE",
            {entry.code for entry in loaded.loader_audit if entry.severity == "BLOCKER"},
        )

    def test_failure_pm_and_transport_references_are_mapped(self) -> None:
        for loaded in self.loaded.values():
            model = loaded.static_model
            self.assertEqual(len(model.failure_calendars), 11)
            self.assertEqual(len(model.pm_calendars), 292)
            self.assertEqual(len(model.calendar_attachments), 303)
            wafer_pm = [item for item in model.pm_calendars if item.wafer_threshold is not None]
            calendar_pm = [item for item in model.pm_calendars if item.interval is not None]
            self.assertEqual((len(wafer_pm), len(calendar_pm)), (213, 79))
            self.assertTrue(all(item.interval is None for item in wafer_pm))
            self.assertTrue(all(item.first_occurrence_wafers is not None for item in model.calendar_attachments if item.first_occurrence is None))
            self.assertEqual(len(model.transport), 1)
            transport = model.transport[0]
            self.assertEqual((transport.from_location, transport.to_location), ("Fab", "Fab"))
            self.assertEqual((transport.duration.kind, transport.duration.parameter_1_minutes, transport.duration.parameter_2_minutes), ("uniform", 7.5, 2.5))
            evidence = {item.topic: item for item in loaded.semantic_evidence}
            self.assertEqual(evidence["transport-runtime"].level, "A/B/D/E")
            self.assertIn("显式审计", evidence["transport-runtime"].interpretation)

    def test_calendar_attachments_expand_to_physical_machine_audit_counts(self) -> None:
        expected = {
            "SMT2020_HVLM": {
                "expanded_edges": 4024,
                "physical_machines": 1043,
                "multi_calendar_pm_machines": 351,
                "max_calendar_pm_per_machine": 3,
                "distributions": {
                    "failure": {1: 1043},
                    "calendar_pm": {0: 692, 2: 148, 3: 203},
                    "wafer_pm": {0: 351, 3: 692},
                },
                "sample_machine": ("DE_BE_11#0001", {"failure": 1, "calendar_pm": 0, "wafer_pm": 3}),
            },
            "SMT2020_LVHM": {
                "expanded_edges": 3515,
                "physical_machines": 913,
                "multi_calendar_pm_machines": 307,
                "max_calendar_pm_per_machine": 3,
                "distributions": {
                    "failure": {1: 913},
                    "calendar_pm": {0: 606, 2: 137, 3: 170},
                    "wafer_pm": {0: 307, 3: 606},
                },
                "sample_machine": ("DE_BE_11#0001", {"failure": 1, "calendar_pm": 0, "wafer_pm": 3}),
            },
        }
        for model, values in expected.items():
            with self.subTest(model=model):
                loaded = self.loaded[model]
                stats = loaded.statistics
                self.assertEqual(stats["calendar_attachments"], 303)
                self.assertEqual(stats["attachment_expanded_machine_edges"], values["expanded_edges"])
                self.assertEqual(stats["attachment_physical_machine_count"], values["physical_machines"])
                self.assertEqual(stats["attachment_physical_machine_count_total"], values["physical_machines"])
                self.assertEqual(
                    stats["attachment_physical_machines_with_multi_calendar_pm"],
                    values["multi_calendar_pm_machines"],
                )
                self.assertEqual(
                    stats["attachment_max_calendar_pm_per_machine"],
                    values["max_calendar_pm_per_machine"],
                )
                self.assertEqual(stats["attachment_machine_count_distributions"], values["distributions"])
                joint_counts: dict[tuple[int, int, int], int] = {}
                for counts in stats["attachment_machine_counts_by_physical_machine"].values():
                    key = (counts["failure"], counts["calendar_pm"], counts["wafer_pm"])
                    joint_counts[key] = joint_counts.get(key, 0) + 1
                expected_joint = (
                    {(1, 0, 3): 692, (1, 2, 0): 148, (1, 3, 0): 203}
                    if model == "SMT2020_HVLM" else
                    {(1, 0, 3): 606, (1, 2, 0): 137, (1, 3, 0): 170}
                )
                self.assertEqual(joint_counts, expected_joint)
                sample_machine, sample_counts = values["sample_machine"]
                self.assertEqual(
                    stats["attachment_machine_counts_by_physical_machine"][sample_machine],
                    sample_counts,
                )
                blocker = next(
                    item
                    for item in loaded.loader_audit
                    if item.code == "DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT"
                )
                context = dict(blocker.context)
                self.assertEqual(context["raw_attachment_rows"], "303")
                self.assertEqual(
                    context["physical_machines_with_multi_calendar_pm"],
                    str(values["multi_calendar_pm_machines"]),
                )

    def test_unknown_attachment_type_or_target_type_is_not_silently_ignored(self) -> None:
        def load_attach_variant(field: str, value: str):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder) / "datasets"
                shutil.copytree(DATASETS_ROOT / "SMT2020_HVLM", root / "SMT2020_HVLM")
                attach_path = root / "SMT2020_HVLM" / "attach.txt"
                lines = attach_path.read_text(encoding="utf-8").splitlines()
                header = lines[0].split("\t")
                fields = lines[1].split("\t")
                fields[header.index(field)] = value
                lines[1] = "\t".join(fields)
                attach_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return load_smt2020(root, "SMT2020_HVLM")

        with self.assertRaises(LoaderError) as caltype_error:
            load_attach_variant("CALTYPE", "unknown")
        self.assertIn(
            "DI_UNSUPPORTED_ATTACHMENT_TYPE",
            {item.code for item in caltype_error.exception.entries},
        )
        with self.assertRaises(LoaderError) as restype_error:
            load_attach_variant("RESTYPE", "unknown")
        self.assertIn(
            "DI_UNSUPPORTED_ATTACHMENT_TARGET_TYPE",
            {item.code for item in restype_error.exception.entries},
        )
        with self.assertRaises(LoaderError) as calendar_type_error:
            load_attach_variant("CALTYPE", "pm")
        self.assertIn(
            "DI_ATTACHMENT_CALENDAR_TYPE_MISMATCH",
            {item.code for item in calendar_type_error.exception.entries},
        )

    def test_real_route_location_transitions_and_missing_pairs_are_audited(self) -> None:
        expected = {
            "SMT2020_HVLM": {
                "Fab->Fab": 857,
                "Fab->Delay": 33,
                "Delay->Fab": 33,
                "Delay->Delay": 1,
            },
            "SMT2020_LVHM": {
                "Fab->Fab": 3714,
                "Fab->Delay": 142,
                "Delay->Fab": 142,
                "Delay->Delay": 5,
            },
        }
        for model, counts in expected.items():
            loaded = self.loaded[model]
            self.assertEqual(loaded.statistics["route_location_transition_counts"], counts)
            self.assertEqual(
                loaded.statistics["unconfigured_transport_transition_counts"],
                {key: value for key, value in counts.items() if key != "Fab->Fab"},
            )
            self.assertEqual(loaded.statistics["ambiguous_location_transitions"], 0)
            warnings = {
                item.code: dict(item.context)
                for item in loaded.loader_audit
                if item.severity == "WARNING"
            }
            self.assertEqual(
                warnings["DI_TRANSPORT_ROUTE_PAIRS_UNCONFIGURED"]["transitions"],
                str(sum(value for key, value in counts.items() if key != "Fab->Fab")),
            )

    def test_all_runtime_gaps_are_explicit_blockers(self) -> None:
        required_codes = {
            "DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE",
            "DI_UNSUPPORTED_REWORK",
            "DI_UNSUPPORTED_SETUP_MINRUN",
            "DI_MISSING_BATCH_DECISION_CONFIG",
            "DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT",
        }
        for loaded in self.loaded.values():
            codes = {item.code for item in loaded.loader_audit if item.severity == "BLOCKER"}
            self.assertEqual(codes, required_codes)
            self.assertEqual(loaded.blocker_count, 5)
            self.assertIn(
                "DI_RELEASE_TEMPLATE_RUNTIME_SUPPORTED",
                {item.code for item in loaded.loader_audit if item.severity == "INFO"},
            )
            self.assertNotIn("DI_UNSUPPORTED_TRANSPORT_RUNTIME", codes)
            self.assertEqual(loaded.error_count, 0)
            audit = loaded.audit_to_dict()
            self.assertEqual(audit["blocker_count"], loaded.blocker_count)
            self.assertFalse(audit["scenario_constructed"])
            self.assertEqual(audit["dataset_manifest"]["manifest_hash"], loaded.dataset_manifest.manifest_hash)

    def test_unknown_initial_history_is_never_silently_recovered(self) -> None:
        expected = {"SMT2020_HVLM": (341, 2435), "SMT2020_LVHM": (433, 1965)}
        for model, counts in expected.items():
            loaded = self.loaded[model]
            self.assertEqual((loaded.statistics["unknown_initial_cqt_relationships"], loaded.statistics["unknown_initial_dedication_relationships"]), counts)
            warnings = {item.code for item in loaded.loader_audit if item.severity == "WARNING"}
            self.assertTrue({"DI_INITIAL_SETUP_UNKNOWN", "DI_INITIAL_CQT_UNKNOWN", "DI_INITIAL_DEDICATION_UNKNOWN", "DI_INITIAL_WAFER_PM_COUNTER_UNKNOWN"} <= warnings)

    def test_uniform_parser_uses_mean_and_full_width(self) -> None:
        spec = parse_distribution("uniform", "7.5", "2.5", "min")
        self.assertEqual((spec.parameter_1_minutes, spec.parameter_2_minutes), (7.5, 2.5))
        runtime = to_runtime_distribution(spec)
        self.assertEqual((runtime.kind, runtime.mean_minutes, runtime.width_minutes), ("uniform", 7.5, 2.5))

    def test_real_data_processing_basis_and_distribution_statistics_are_preserved(self) -> None:
        self.assertEqual(self.loaded["SMT2020_HVLM"].statistics["processing_basis_counts"], {"per_batch": 28, "per_lot": 490, "per_piece": 408})
        self.assertEqual(self.loaded["SMT2020_LVHM"].statistics["processing_basis_counts"], {"per_batch": 135, "per_lot": 2104, "per_piece": 1774})
        self.assertEqual(self.loaded["SMT2020_HVLM"].statistics["processing_distribution_kinds"], {"uniform": 926})
        self.assertEqual(self.loaded["SMT2020_LVHM"].statistics["processing_distribution_kinds"], {"uniform": 4013})

    def test_real_data_validation_slice_reaches_simulator_with_manifest_provenance(self) -> None:
        loaded = load_smt2020(DATASETS_ROOT, "SMT2020_HVLM", loader_config=LoaderConfig(mode="validation_slice"))
        self.assertIsNotNone(loaded.scenario)
        scenario = loaded.scenario
        assert scenario is not None
        before = asdict(scenario)
        result = simulate({"policy_id": "fifo"}, scenario, 42, git_commit="data-integration-smoke")
        self.assertEqual(result.metrics.completed_lots, 1)
        self.assertEqual(asdict(scenario), before)
        provenance = result.provenance.simulation_config["dataset_provenance"]
        self.assertEqual(provenance["manifest_hash"], loaded.dataset_manifest.manifest_hash)
        self.assertEqual(len(provenance["raw_files"]), len(loaded.dataset_manifest.files))
        self.assertEqual(result.policy_id, "fifo")
        self.assertEqual(len([item for item in result.random_sample_ledger if item.stream_name == "processing"]), 1)

    def test_release_template_fields_and_initial_wip_metadata_are_preserved(self) -> None:
        loaded = self.loaded["SMT2020_HVLM"]
        self.assertEqual(
            loaded.statistics["release_distribution_kinds"],
            {"constant": 5},
        )
        self.assertEqual(loaded.statistics["unsupported_release_templates"], 0)
        self.assertEqual(loaded.statistics["unsupported_release_unit_count"], 0)
        self.assertIn(
            "release-runtime",
            {item.topic for item in loaded.semantic_evidence},
        )
        template = loaded.static_model.release_templates[0]
        self.assertEqual(
            (template.lot_prefix, template.order_id, template.source_row,
             template.repeat_limit, template.lots_per_repeat, template.hot_lot),
            ("Lot_3", "O_Lot_3", 2, 200000, 1, False),
        )
        self.assertEqual(template.quantity_wafers, 25)
        first_wip = loaded.static_model.initial_wip[0]
        self.assertEqual(
            (first_wip.priority, first_wip.order_id, first_wip.hot_lot,
             first_wip.source_start_minutes, first_wip.source_trace, first_wip.trace),
            (10, "O_Init_WIP", None, 0.0, None, None),
        )

    def test_release_validation_slice_is_lazy_and_preserves_metadata(self) -> None:
        loaded = load_smt2020(
            DATASETS_ROOT,
            "SMT2020_HVLM",
            loader_config=LoaderConfig(mode="release_validation_slice"),
        )
        scenario = loaded.scenario
        assert scenario is not None
        self.assertEqual(scenario.lots, ())
        self.assertEqual(len(scenario.release_templates), 1)
        template = scenario.release_templates[0]
        self.assertEqual(
            template.template_id,
            "SMT2020_HVLM:release-template:0002",
        )
        self.assertEqual(template.repeat_limit, 200000)
        self.assertEqual(template.lots_per_repeat, 1)
        self.assertEqual(
            scenario.horizon,
            template.first_release_time + 2 * template.interval.mean_minutes,
        )
        result = simulate({"policy_id": "fifo"}, scenario, 42)
        releases = [item for item in result.trace if item.event_type == "LOT_RELEASE"]
        self.assertEqual(len(releases), 3)
        self.assertEqual(
            [item.sim_time for item in releases],
            [template.first_release_time + i * template.interval.mean_minutes for i in range(3)],
        )
        self.assertEqual(
            [item.lot_due_time - item.sim_time for item in releases],
            [template.relative_due_minutes] * 3,
        )
        self.assertEqual({item.product_id for item in releases}, {template.product_id})
        self.assertEqual({item.order_id for item in releases}, {template.order_id})
        self.assertEqual({item.lot_priority for item in releases}, {template.priority})
        self.assertEqual({item.lot_quantity_wafers for item in releases}, {template.quantity_wafers})
        self.assertEqual({item.hot_lot for item in releases}, {template.hot_lot})
        self.assertEqual({item.release_template_id for item in releases}, {template.template_id})
        self.assertEqual({item.source_row for item in releases}, {template.source_row})
        self.assertEqual(
            {item.stream_name for item in result.random_sample_ledger},
            {"processing"},
        )

    def test_release_selector_is_exact_and_covers_real_templates(self) -> None:
        expected = ("Lot_3", "HotLot_3", "SuperHotLot_3")
        for lot_prefix in expected:
            with self.subTest(model="SMT2020_HVLM", lot_prefix=lot_prefix):
                loaded = load_smt2020(
                    DATASETS_ROOT,
                    "SMT2020_HVLM",
                    loader_config=LoaderConfig(
                        mode="release_validation_slice",
                        validation_release_lot_prefix=lot_prefix,
                    ),
                )
                scenario = loaded.scenario
                assert scenario is not None
                self.assertEqual(scenario.release_templates[0].lot_prefix, lot_prefix)
                self.assertEqual(simulate({"policy_id": "fifo"}, scenario, 42).metrics.released_lots, 3)
        loaded = load_smt2020(
            DATASETS_ROOT,
            "SMT2020_LVHM",
            loader_config=LoaderConfig(
                mode="release_validation_slice",
                validation_release_lot_prefix="Lot_1",
            ),
        )
        assert loaded.scenario is not None
        self.assertEqual(loaded.scenario.release_templates[0].lot_prefix, "Lot_1")
        with self.assertRaises(ValueError):
            LoaderConfig(mode="audit", validation_release_lot_prefix="Lot_3")
        with self.assertRaises(ValueError):
            load_smt2020(
                DATASETS_ROOT,
                "SMT2020_HVLM",
                loader_config=LoaderConfig(
                    mode="release_validation_slice",
                    validation_release_lot_prefix="not-an-order-template",
                ),
            )

    def _real_sampling_selector(self, model: str, *, percent: float | None) -> tuple[str, int]:
        loaded = self.loaded[model]
        route_by_product = {item.product_id: item.route_id for item in loaded.static_model.products}
        operations = {
            (route.route_id, operation.step_id): operation
            for route in loaded.static_model.routes
            for operation in route.operations
        }
        cqt_endpoints = {
            (operation.route_id, step_id)
            for operation in operations.values()
            if operation.cqt_target_step_id is not None
            for step_id in (operation.step_id, operation.cqt_target_step_id)
        }
        dedication_endpoints = {
            (operation.route_id, step_id)
            for operation in operations.values()
            if operation.dedication_target_step_id is not None
            for step_id in (operation.step_id, operation.dedication_target_step_id)
        }
        cascading_families = {
            template.tool_family_id
            for template in loaded.static_model.machine_templates
            if template.cascading
        }
        candidates = []
        for wip in loaded.static_model.initial_wip:
            key = (route_by_product[wip.product_id], wip.current_step_id)
            operation = operations[key]
            if (
                (operation.sample_percent == percent if percent is not None else operation.sample_percent not in (None, 100.0))
                and operation.rework_step_id is None
                and key not in cqt_endpoints
                and key not in dedication_endpoints
                and operation.tool_family_id not in cascading_families
            ):
                candidates.append((key, wip.lot_id))
        self.assertTrue(candidates)
        return sorted(candidates, key=lambda item: (item[0], item[1]))[0][0]

    def test_sampling_audit_statistics_and_rework_overlap_are_explicit(self) -> None:
        expected = {"SMT2020_HVLM": (221, 72, 149, 14, 98), "SMT2020_LVHM": (955, 293, 662, 52, 75)}
        for model, (explicit, always, stochastic, overlap, initial_wip) in expected.items():
            loaded = self.loaded[model]
            self.assertEqual(
                (
                    loaded.statistics["sampling_field_operations"],
                    loaded.statistics["sampling_100_operations"],
                    loaded.statistics["sampling_stochastic_operations"],
                    loaded.statistics["sampling_rework_overlap_operations"],
                    loaded.statistics["initial_wip_at_sampling_count"],
                ),
                (explicit, always, stochastic, overlap, initial_wip),
            )
            self.assertIn(
                "DI_SAMPLING_RUNTIME_SUPPORTED",
                {item.code for item in loaded.loader_audit if item.severity == "INFO"},
            )
            self.assertIn(
                "DI_SAMPLING_REWORK_OVERLAP",
                {item.code for item in loaded.loader_audit if item.severity == "INFO"},
            )
        self.assertEqual(
            self.loaded["SMT2020_HVLM"].statistics[
                "sampling_cqt_endpoint_operations"
            ],
            4,
        )
        self.assertEqual(
            self.loaded["SMT2020_LVHM"].statistics[
                "sampling_cqt_endpoint_operations"
            ],
            18,
        )
        for loaded in self.loaded.values():
            self.assertEqual(
                loaded.statistics["sampling_profile_unsupported_operations"], 0
            )
            self.assertEqual(
                loaded.statistics["sampling_stochastic_cqt_endpoint_operations"], 0
            )
            operations = tuple(
                operation
                for route in loaded.static_model.routes
                for operation in route.operations
            )
            cqt_targets = {
                (operation.route_id, operation.cqt_target_step_id)
                for operation in operations
                if operation.cqt_target_step_id is not None
            }
            self.assertTrue(all(
                operation.sample_percent == 100.0
                for operation in operations
                if operation.sample_percent is not None
                and (operation.route_id, operation.step_id) in cqt_targets
            ))
            self.assertEqual(
                loaded.statistics["sampling_dedication_endpoint_operations"], 0
            )
            self.assertEqual(
                loaded.statistics["sampling_cascading_tool_operations"], 0
            )
            self.assertEqual(
                loaded.statistics["sampling_load_unload_operations"],
                loaded.statistics["sampling_field_operations"],
            )
            self.assertIn(
                "sampling-runtime",
                {item.topic for item in loaded.semantic_evidence},
            )

    def test_sampling_validation_slice_preserves_real_wip_and_sampling_runtime(self) -> None:
        for model in MODELS:
            stochastic_selector = self._real_sampling_selector(model, percent=None)
            stochastic = load_smt2020(
                DATASETS_ROOT,
                model,
                loader_config=LoaderConfig(
                    mode="sampling_validation_slice",
                    validation_sampling_operation=stochastic_selector,
                ),
            )
            assert stochastic.scenario is not None
            lot = stochastic.scenario.lots[0]
            operation = lot.operations[0]
            self.assertEqual((operation.route_id, operation.step_id), stochastic_selector)
            self.assertIsNotNone(operation.sample_percent)
            assert operation.sample_percent is not None
            self.assertLess(operation.sample_percent, 100.0)
            self.assertTrue(lot.is_initial_wip)
            raw_wip = next(
                item for item in self.loaded[model].static_model.initial_wip
                if item.lot_id == lot.lot_id
            )
            raw_product = next(
                item for item in self.loaded[model].static_model.products
                if item.product_id == raw_wip.product_id
            )
            raw_operation = next(
                item for route in self.loaded[model].static_model.routes
                for item in route.operations
                if (item.route_id, item.step_id) == stochastic_selector
            )
            self.assertEqual(
                (
                    lot.product_id, lot.order_id, lot.priority, lot.hot_lot,
                    lot.quantity_wafers, lot.due_time, lot.source_row,
                ),
                (
                    raw_wip.product_id, raw_wip.order_id, raw_wip.priority,
                    raw_wip.hot_lot, raw_wip.quantity_wafers,
                    raw_wip.due_minutes, raw_wip.source_row,
                ),
            )
            self.assertEqual(raw_product.route_id, stochastic_selector[0])
            self.assertEqual(operation.sample_percent, raw_operation.sample_percent)
            self.assertEqual(
                operation.processing_distribution,
                to_runtime_distribution(raw_operation.processing),
            )
            self.assertIn(
                "DI_SAMPLING_SLICE_OMITS_LOAD_UNLOAD",
                {item.code for item in stochastic.loader_audit},
            )
            slice_config = dict(stochastic.scenario.dataset_provenance.loader_config)
            self.assertGreater(
                float(slice_config["sampling_slice_omitted_load_minutes"]), 0
            )
            self.assertGreater(
                float(slice_config["sampling_slice_omitted_unload_minutes"]), 0
            )
            self.assertEqual(
                (stochastic.scenario.dataset_provenance.loader_contract_version,
                 dict(stochastic.scenario.dataset_provenance.loader_config)["validation_sampling_operation"]),
                ("0.1.6", f"{stochastic_selector[0]}:{stochastic_selector[1]}"),
            )
            result = simulate({"policy_id": "fifo"}, stochastic.scenario, 42)
            self.assertTrue(
                audit_result_invariants(result, stochastic.scenario).passed
            )
            self.assertEqual(
                [item.stream_name for item in result.random_sample_ledger if item.stream_name == "sampling"],
                ["sampling"],
            )

            always_selector = self._real_sampling_selector(model, percent=100.0)
            always = load_smt2020(
                DATASETS_ROOT,
                model,
                loader_config=LoaderConfig(
                    mode="sampling_validation_slice",
                    validation_sampling_operation=always_selector,
                ),
            )
            assert always.scenario is not None
            always_result = simulate({"policy_id": "fifo"}, always.scenario, 42)
            self.assertTrue(audit_result_invariants(always_result, always.scenario).passed)
            self.assertFalse(
                [item for item in always_result.random_sample_ledger if item.stream_name == "sampling"]
            )

    def test_real_percent_100_cqt_targets_have_diagnostic_sampling_slice(self) -> None:
        for model, selector in (
            ("SMT2020_HVLM", ("r_3", 430)),
            ("SMT2020_LVHM", ("r_1", 416)),
        ):
            loaded = load_smt2020(
                DATASETS_ROOT, model,
                loader_config=LoaderConfig(
                    mode="sampling_validation_slice",
                    validation_sampling_operation=selector,
                ),
            )
            assert loaded.scenario is not None
            operation = loaded.scenario.lots[0].operations[0]
            self.assertEqual(
                (operation.route_id, operation.step_id, operation.sample_percent),
                (*selector, 100.0),
            )
            result = simulate({"policy_id": "fifo"}, loaded.scenario, 42)
            self.assertTrue(audit_result_invariants(result, loaded.scenario).passed)
            self.assertEqual(
                [item.sampling_performed for item in result.trace
                 if item.event_type == "SAMPLING_DECISION"],
                [True],
            )
            self.assertFalse(
                [item for item in result.random_sample_ledger
                 if item.stream_name == "sampling"]
            )
            self.assertIn(
                "DI_SAMPLING_SLICE_OMITS_LOAD_UNLOAD",
                {item.code for item in loaded.loader_audit},
            )

if __name__ == "__main__":
    unittest.main()
