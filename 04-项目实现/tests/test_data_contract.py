"""本地 SMT2020 数据与 Data Contract 的结构证据。"""

from __future__ import annotations

import csv
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.data.identity import identify_dataset


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATASETS_ROOT = REPOSITORY_ROOT / "datasets"
DATASET_NAMES = ("SMT2020_HVLM", "SMT2020_LVHM")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


class DataContractTests(unittest.TestCase):
    def test_transport_distribution_is_the_frozen_fab_to_fab_row(self) -> None:
        for name in DATASET_NAMES:
            with self.subTest(dataset=name):
                rows = read_tsv(DATASETS_ROOT / name / "fromto.txt")
                self.assertEqual(
                    rows,
                    [
                        {
                            "FROMLOC": "Fab",
                            "TOLOC": "Fab",
                            "DDIST": "uniform",
                            "DTIME": "7.5",
                            "DTIME2": "2.5",
                            "DUNITS": "min",
                        }
                    ],
                )

    def test_route_edges_reference_existing_forward_steps(self) -> None:
        for name in DATASET_NAMES:
            for route_path in sorted((DATASETS_ROOT / name).glob("route_*.txt")):
                with self.subTest(dataset=name, route=route_path.name):
                    rows = read_tsv(route_path)
                    step_ids = [int(float(row["STEP"])) for row in rows]
                    self.assertEqual(step_ids, list(range(1, len(rows) + 1)))
                    known_steps = set(step_ids)
                    for row in rows:
                        source = int(float(row["STEP"]))
                        if row["STEP_CQT"]:
                            target = int(float(row["STEP_CQT"]))
                            self.assertGreater(target, source)
                            self.assertIn(target, known_steps)
                        if row["SVESTN"].lower() == "yes":
                            target = int(float(row["FORSTEP"]))
                            self.assertGreater(target, source)
                            self.assertIn(target, known_steps)

    def test_batch_bounds_are_wafer_quantities_and_well_formed(self) -> None:
        observed_bounds = set()
        for name in DATASET_NAMES:
            for route_path in sorted((DATASETS_ROOT / name).glob("route_*.txt")):
                for row in read_tsv(route_path):
                    if not row["BATCHMX"]:
                        continue
                    minimum = int(float(row["BATCHMN"]))
                    maximum = int(float(row["BATCHMX"]))
                    self.assertEqual(row["PTPER"], "per_batch")
                    self.assertLessEqual(minimum, maximum)
                    self.assertEqual(minimum % 25, 0)
                    self.assertEqual(maximum % 25, 0)
                    observed_bounds.add((minimum, maximum))
        self.assertEqual(
            observed_bounds,
            {(75, 100), (100, 125), (125, 150)},
        )

    def test_dataset_identity_is_stable_and_complete(self) -> None:
        expected_counts = {"SMT2020_HVLM": 12, "SMT2020_LVHM": 20}
        for name in DATASET_NAMES:
            with self.subTest(dataset=name):
                first = identify_dataset(DATASETS_ROOT / name)
                second = identify_dataset(DATASETS_ROOT / name)
                self.assertEqual(first, second)
                self.assertEqual(first.file_count, expected_counts[name])
                self.assertEqual(len(first.sha256), 64)


if __name__ == "__main__":
    unittest.main()
