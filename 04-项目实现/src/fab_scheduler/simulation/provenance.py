"""仿真结果的来源记录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess
from typing import Any


SIMULATION_CONTRACT_VERSION = "0.1.3"


def discover_git_commit() -> str:
    """读取当前 checkout commit；非 Git 安装环境返回 unknown。"""

    repository_root = Path(__file__).resolve().parents[4]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


@dataclass(frozen=True, slots=True)
class RunProvenance:
    simulation_contract_version: str
    dataset_version: str
    git_commit: str
    seed: int
    simulation_config: dict[str, Any]
    dispatch_policy: str
    termination_condition: str
    horizon: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
