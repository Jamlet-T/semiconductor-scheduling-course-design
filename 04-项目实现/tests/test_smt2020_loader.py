"""真实 SMT2020 raw → static model → validation Scenario 数据链。"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import tempfile
import unittest

from fab_scheduler.api import simulate
from fab_scheduler.data import (
    LoaderConfig,
    SMT2020_LOADER_CONTRACT_VERSION,
    build_dataset_manifest,
    load_smt2020,
    parse_distribution,
)


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
        self.assertEqual(SMT2020_LOADER_CONTRACT_VERSION, "0.1.0")
        self.assertEqual(set(self.loaded), set(MODELS))
        for model, loaded in self.loaded.items():
            self.assertEqual(loaded.dataset_manifest.model_name, model)
            self.assertEqual(len(loaded.dataset_manifest.manifest_hash), 64)
            self.assertIn(loaded.dataset_manifest.manifest_hash, loaded.dataset_manifest.dataset_version)
            self.assertIsNone(loaded.scenario)
        with self.assertRaises(ValueError):
            LoaderConfig(mode="audit", validation_product_id="part_3")

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

    def test_all_runtime_gaps_are_explicit_blockers(self) -> None:
        required_codes = {
            "DI_UNSUPPORTED_PROCESSING_DISTRIBUTION", "DI_UNSUPPORTED_RELEASE_TEMPLATES",
            "DI_UNSUPPORTED_TRANSPORT_RUNTIME", "DI_UNSUPPORTED_SAMPLING", "DI_UNSUPPORTED_REWORK",
            "DI_UNSUPPORTED_SETUP_MINRUN", "DI_UNSUPPORTED_EXPONENTIAL_FAILURE",
        }
        for loaded in self.loaded.values():
            codes = {item.code for item in loaded.loader_audit if item.severity == "BLOCKER"}
            self.assertTrue(required_codes <= codes)
            self.assertGreater(loaded.blocker_count, 0)
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


if __name__ == "__main__":
    unittest.main()
