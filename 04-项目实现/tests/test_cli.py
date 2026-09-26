"""CLI 最小可用性测试。"""

import os
from pathlib import Path
import subprocess
import sys
import unittest


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            path
            for path in (str(SOURCE_ROOT), existing_pythonpath)
            if path
        )
        return subprocess.run(
            [sys.executable, "-m", "fab_scheduler", *args],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_help_is_available(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())

    def test_info_reports_m1_status_without_results(self) -> None:
        result = self.run_cli("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("项目阶段：M1 Simulation Reliability Baseline", result.stdout)
        self.assertIn("项目周期：14 周", result.stdout)
        self.assertIn(
            "仿真状态：可信轻量 DES 已验证 MC01-MC08；M1 已通过",
            result.stdout,
        )
        self.assertIn(
            "数据状态：SMT2020 Data Integration Gate 待验证",
            result.stdout,
        )
        self.assertIn(
            "优化器状态：CMA-ES 已选型，Data Integration Gate 通过前禁用",
            result.stdout,
        )
        self.assertNotIn("吞吐量", result.stdout)


if __name__ == "__main__":
    unittest.main()
