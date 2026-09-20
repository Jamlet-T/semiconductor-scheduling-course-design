"""CLI 最小可用性测试。"""

import subprocess
import sys
import unittest


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "fab_scheduler", *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_help_is_available(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())

    def test_info_reports_scaffold_status_without_results(self) -> None:
        result = self.run_cli("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("项目阶段：scaffold", result.stdout)
        self.assertIn("项目周期：14 周", result.stdout)
        self.assertIn("仿真状态：尚未实现", result.stdout)
        self.assertNotIn("吞吐量", result.stdout)


if __name__ == "__main__":
    unittest.main()
