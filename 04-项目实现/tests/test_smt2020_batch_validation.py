"""真实 SMT2020 raw -> batch validation slice -> simulate 的受限验收。

这里验证的是受限 per-batch validation slice，不把省略 LOAD/UNLOAD 的 slice
表述为完整物理闭环，也不把它当作正式 HVLM/LVHM 性能实验。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.api import simulate
from fab_scheduler.data.smt2020_loader import (
    BatchDecisionConfig,
    LoaderConfig,
    LoaderError,
    load_batch_decision_config,
    load_smt2020,
)
from fab_scheduler.evaluation.audit import audit_result_invariants
from fab_scheduler.evaluation.crn_audit import audit_common_random_numbers


DATASETS_ROOT = PROJECT_ROOT / "datasets"
BATCH_CONFIG_PATH = PROJECT_ROOT / "configs" / "smt2020-batch-decision-v1.json"


def _batch_config(model_id: str) -> BatchDecisionConfig:
    return load_batch_decision_config(BATCH_CONFIG_PATH, model_id)


def _load_batch(
    model_id: str,
    selector: tuple[str, int] | None,
    *,
    decision_config: BatchDecisionConfig | None = None,
):
    return load_smt2020(
        DATASETS_ROOT,
        model_id,
        loader_config=LoaderConfig(
            mode="batch_validation_slice",
            validation_batch_operation=selector,
            batch_decision_config=(
                _batch_config(model_id)
                if decision_config is None
                else decision_config
            ),
        ),
    )


def _loader_config_items(scenario) -> dict[str, str]:
    return dict(scenario.dataset_provenance.loader_config)


class SMT2020BatchValidationTests(unittest.TestCase):
    def test_hvlm_r3_step1_reaches_real_batch_target(self) -> None:
        config = _batch_config("SMT2020_HVLM")
        loaded = _load_batch("SMT2020_HVLM", ("r_3", 1), decision_config=config)
        self.assertIsNotNone(loaded.scenario)
        scenario = loaded.scenario
        assert scenario is not None

        operation = scenario.lots[0].operations[0]
        self.assertEqual(
            (operation.batch_spec.minimum_wafers, operation.batch_spec.maximum_wafers),
            (125, 150),
        )
        self.assertEqual(operation.batch_spec.target_wafers, 150)
        self.assertTrue(loaded.scenario.scenario_id.endswith("r_3:1"))

        result = simulate({"policy_id": "fifo"}, scenario, 42)
        self.assertGreaterEqual(len(result.batch_intervals), 1)
        first = result.batch_intervals[0]
        self.assertEqual(first.start, 0.0)
        self.assertEqual(first.start_reason, "TARGET_REACHED")
        self.assertEqual(first.total_wafers, operation.batch_spec.target_wafers)
        self.assertTrue(
            all(item.start_reason == "TARGET_REACHED" for item in result.batch_intervals)
        )
        self.assertTrue(audit_result_invariants(result, scenario).passed)

    def test_lvhm_r1_step331_three_wip_timeout_at_tmax(self) -> None:
        config = _batch_config("SMT2020_LVHM")
        loaded = _load_batch("SMT2020_LVHM", ("r_1", 331), decision_config=config)
        self.assertIsNotNone(loaded.scenario)
        scenario = loaded.scenario
        assert scenario is not None

        self.assertEqual(len(scenario.lots), 3)
        self.assertEqual([lot.quantity_wafers for lot in scenario.lots], [25, 25, 25])
        self.assertEqual(sum(lot.quantity_wafers for lot in scenario.lots), 75)
        operation = scenario.lots[0].operations[0]
        self.assertEqual(
            (operation.batch_spec.minimum_wafers, operation.batch_spec.maximum_wafers),
            (75, 100),
        )
        self.assertEqual(operation.batch_spec.target_wafers, 100)
        self.assertEqual(operation.batch_spec.max_wait_minutes, 60.0)

        result = simulate({"policy_id": "fifo"}, scenario, 42)
        self.assertEqual(len(result.batch_intervals), 1)
        interval = result.batch_intervals[0]
        self.assertEqual(interval.start, 60.0)
        self.assertEqual(interval.start_reason, "TIMEOUT_REACHED")
        self.assertEqual(interval.total_wafers, 75)
        self.assertTrue(any(item.event_type == "BATCH_TIMEOUT" for item in result.trace))
        self.assertTrue(audit_result_invariants(result, scenario).passed)

    def test_batch_processing_is_sampled_once_and_common_random_numbers_match(self) -> None:
        for model_id, selector in (
            ("SMT2020_HVLM", ("r_3", 1)),
            ("SMT2020_LVHM", ("r_1", 331)),
        ):
            with self.subTest(model=model_id):
                scenario = _load_batch(model_id, selector).scenario
                assert scenario is not None
                fifo = simulate({"policy_id": "fifo"}, scenario, 42)
                spt = simulate({"policy_id": "spt"}, scenario, 42)

                samples = [
                    row
                    for row in fifo.random_sample_ledger
                    if row.stream_name == "batch_processing"
                ]
                self.assertEqual(len(samples), len(fifo.batch_intervals))
                self.assertTrue(all(row.occurrence_index == 0 for row in samples))
                for sample, interval in zip(samples, fifo.batch_intervals, strict=True):
                    self.assertAlmostEqual(sample.value, interval.active_processing_time)
                crn = audit_common_random_numbers(fifo, spt)
                self.assertTrue(crn.passed, crn.mismatched_identities)
                self.assertEqual(len(crn.common_identities), len(samples))

    def test_batch_provenance_contains_config_hash_and_load_unload_omission(self) -> None:
        config = _batch_config("SMT2020_LVHM")
        loaded = _load_batch("SMT2020_LVHM", ("r_1", 331), decision_config=config)
        scenario = loaded.scenario
        assert scenario is not None
        items = _loader_config_items(scenario)

        self.assertEqual(items["batch_decision_config_hash"], config.canonical_hash)
        self.assertEqual(items["batch_decision_config_source_sha256"], config.source_sha256)
        self.assertEqual(items["batch_decision_config_source_schema_version"], "1.0.0")
        self.assertEqual(items["batch_decision_config_manifest_hash"], config.manifest_hash)
        self.assertGreater(float(items["batch_slice_omitted_load_minutes"]), 0.0)
        self.assertGreater(float(items["batch_slice_omitted_unload_minutes"]), 0.0)
        self.assertTrue(items["batch_slice_omitted_calendar_ids"])
        self.assertEqual(items["batch_slice_selected_machine_id"], scenario.machines[0].machine_id)
        self.assertGreater(int(items["batch_slice_raw_eligible_machine_count"]), 1)
        self.assertEqual(items["batch_slice_raw_batch_criterion"], "crit_sameroutestep")
        self.assertEqual(items["batch_slice_raw_batch_unit"], "piece")
        self.assertEqual(scenario.lots[0].operations[0].batch_spec.compatibility_rule, "crit_sameroutestep")
        self.assertTrue(
            any(
                item.severity == "WARNING"
                and item.code == "DI_BATCH_SLICE_OMITS_LOAD_UNLOAD"
                for item in loaded.loader_audit
            )
        )
        self.assertTrue(
            any(
                item.severity == "INFO"
                and "BATCH_DECISION_CONFIG" in item.code
                for item in loaded.loader_audit
            )
        )
        self.assertTrue(any(
            item.severity == "WARNING"
            and item.code == "DI_BATCH_SLICE_OMITS_CALENDAR_ATTACHMENTS"
            for item in loaded.loader_audit
        ))
        self.assertTrue(any(
            item.severity == "WARNING"
            and item.code == "DI_BATCH_SLICE_SINGLE_MACHINE"
            for item in loaded.loader_audit
        ))
        json.dumps(loaded.audit_to_dict(), ensure_ascii=False)

    def test_configured_audit_removes_only_batch_config_blocker(self) -> None:
        for model_id in ("SMT2020_HVLM", "SMT2020_LVHM"):
            with self.subTest(model=model_id):
                default = load_smt2020(DATASETS_ROOT, model_id)
                configured = load_smt2020(
                    DATASETS_ROOT,
                    model_id,
                    loader_config=LoaderConfig(
                        mode="audit",
                        batch_decision_config=_batch_config(model_id),
                    ),
                )
                self.assertEqual(default.blocker_count, 5)
                self.assertEqual(configured.blocker_count, 4)
                self.assertIsNone(configured.scenario)
                self.assertEqual(configured.error_count, 0)
                self.assertIn(
                    "DI_BATCH_DECISION_CONFIG_SUPPLIED",
                    {item.code for item in configured.loader_audit},
                )
                json.dumps(configured.audit_to_dict(), ensure_ascii=False)

    def test_missing_batch_config_is_blocker_without_scenario(self) -> None:
        loaded = load_smt2020(
            DATASETS_ROOT,
            "SMT2020_HVLM",
            loader_config=LoaderConfig(
                mode="batch_validation_slice",
                validation_batch_operation=("r_3", 1),
            ),
        )
        self.assertIsNone(loaded.scenario)
        self.assertTrue(
            any(item.severity == "BLOCKER" and "BATCH" in item.code for item in loaded.loader_audit)
        )

    def test_inline_config_cannot_close_gate_blocker(self) -> None:
        valid = _batch_config("SMT2020_HVLM")
        inline = BatchDecisionConfig(
            valid.config_id, valid.version, valid.model_id,
            valid.manifest_hash, valid.target_rule, valid.max_wait_minutes,
        )
        loaded = load_smt2020(
            DATASETS_ROOT,
            "SMT2020_HVLM",
            loader_config=LoaderConfig(mode="audit", batch_decision_config=inline),
        )
        self.assertEqual(loaded.blocker_count, 5)
        self.assertIsNone(loaded.scenario)
        self.assertIn(
            "DI_MISSING_BATCH_DECISION_CONFIG",
            {item.code for item in loaded.loader_audit},
        )

    def test_source_artifact_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch.json"
            payload = json.loads(BATCH_CONFIG_PATH.read_text(encoding="utf-8"))
            path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_batch_decision_config(path, "SMT2020_HVLM")
            payload["max_wait_minutes"] = 61.0
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(LoaderError) as error:
                _load_batch("SMT2020_HVLM", ("r_3", 1), decision_config=config)
            self.assertIn(
                "DI_BATCH_DECISION_CONFIG_SOURCE_MISMATCH",
                {item.code for item in error.exception.entries},
            )

    def test_batch_config_model_and_manifest_mismatch_are_errors(self) -> None:
        valid = _batch_config("SMT2020_HVLM")
        wrong_model = replace(valid, model_id="SMT2020_LVHM")
        with self.assertRaises(LoaderError) as model_error:
            _load_batch("SMT2020_HVLM", ("r_3", 1), decision_config=wrong_model)
        self.assertTrue(any(item.severity == "ERROR" for item in model_error.exception.entries))

        wrong_hash = replace(valid, manifest_hash="0" * 64)
        with self.assertRaises(LoaderError) as hash_error:
            _load_batch("SMT2020_HVLM", ("r_3", 1), decision_config=wrong_hash)
        self.assertTrue(any(item.severity == "ERROR" for item in hash_error.exception.entries))

    def test_invalid_selector_and_batch_config_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LoaderConfig(
                mode="batch_validation_slice",
                validation_batch_operation=("r_3", 0),
            )
        with self.assertRaises(ValueError):
            _load_batch("SMT2020_HVLM", ("not_a_route", 1))
        with self.assertRaises(ValueError):
            BatchDecisionConfig(
                "BAD",
                "1.0.0",
                "SMT2020_HVLM",
                _batch_config("SMT2020_HVLM").manifest_hash,
                "unsupported_rule",  # type: ignore[arg-type]
                60.0,
            )
        with self.assertRaises(ValueError):
            LoaderConfig(
                mode="validation_slice",
                batch_decision_config=_batch_config("SMT2020_HVLM"),
            )


if __name__ == "__main__":
    unittest.main()
