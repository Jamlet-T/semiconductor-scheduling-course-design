"""真实 SMT2020 级联字段的静态诊断；不证明物理释放或 DES 兼容。"""

from __future__ import annotations

from pathlib import Path
import unittest

from fab_scheduler.data import load_smt2020


DATASETS = Path(__file__).resolve().parents[2] / "datasets"
EXPECTED = {
    "SMT2020_HVLM": (284, 95, 39),
    "SMT2020_LVHM": (1267, 401, 191),
}


class CascadeStaticProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.models = {
            name: load_smt2020(DATASETS, name).static_model for name in EXPECTED
        }

    def test_interval_basis_and_tool_mapping(self) -> None:
        for name, model in self.models.items():
            with self.subTest(model=name):
                tools = {item.tool_family_id: item for item in model.machine_templates}
                operations = [op for route in model.routes for op in route.operations]
                part = [op for op in operations if op.part_interval_minutes is not None]
                batch = [op for op in operations if op.batch_interval_minutes is not None]
                self.assertEqual((len(part), len(batch)), EXPECTED[name][:2])
                self.assertEqual(sum(tool.cascading for tool in tools.values()), 45)
                self.assertFalse(set(part) & set(batch))
                self.assertTrue(all(op.processing_basis == "per_piece" for op in part))
                self.assertTrue(all(op.processing_basis == "per_lot" for op in batch))
                self.assertEqual(
                    sum(op.required_setup is not None for op in part), EXPECTED[name][2]
                )
                for op in operations:
                    tool = tools[op.tool_family_id]
                    has_interval = (
                        op.part_interval_minutes is not None
                        or op.batch_interval_minutes is not None
                    )
                    self.assertEqual(tool.cascading, has_interval)
                    if has_interval:
                        self.assertEqual((tool.load_minutes, tool.unload_minutes), (1.0, 1.0))

    def test_uniform_lower_bound_margins_are_static_only(self) -> None:
        # 对照 Data Contract §4 的加工阶段公式；不把 load/unload 加法当作物理规则。
        for name, model in self.models.items():
            with self.subTest(model=name):
                operations = [op for route in model.routes for op in route.operations]
                margins = {"part": [], "batch": []}
                for op in operations:
                    if op.part_interval_minutes is None and op.batch_interval_minutes is None:
                        continue
                    self.assertEqual(op.processing.kind, "uniform")
                    low = (
                        op.processing.parameter_1_minutes
                        - op.processing.parameter_2_minutes / 2
                    )
                    if op.part_interval_minutes is not None:
                        margins["part"].append(low - op.part_interval_minutes)
                    else:
                        margins["batch"].append(low - op.batch_interval_minutes)
                self.assertGreater(min(margins["part"]), 0)
                self.assertGreater(min(margins["batch"]), 0)
                self.assertAlmostEqual(min(margins["part"]), 0.135)
                self.assertAlmostEqual(min(margins["batch"]), 2.50695)

    def test_real_rows_anchor_sensitive_release_order(self) -> None:
        route = next(route for route in self.models["SMT2020_HVLM"].routes if route.route_id == "r_3")
        part = route.operations[341]  # route_3.txt:343, STEP=342
        self.assertEqual(part.step_id, 342)
        self.assertEqual(part.tool_family_id, "WE_FE_84")
        self.assertEqual(part.processing_basis, "per_piece")
        self.assertEqual(part.processing.parameter_1_minutes, 0.6)
        self.assertEqual(part.processing.parameter_2_minutes, 0.03)
        self.assertEqual(part.part_interval_minutes, 0.45)
        low = 0.6 - 0.03 / 2
        self.assertAlmostEqual(low + 24 * 0.45, 11.385)
        self.assertAlmostEqual(25 * 0.45, 11.25)
        # 这 0.135 min 差值不能决定真实 unload 后的 machine release 顺序。


if __name__ == "__main__":
    unittest.main()
