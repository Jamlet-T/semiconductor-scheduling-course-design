"""优化器与仿真内核之间的稳定公共调用边界。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from fab_scheduler.domain.models import Scenario
from fab_scheduler.policies.base import (
    DISPATCH_POLICY_CONTRACT_VERSION,
    DispatchPolicy,
)
from fab_scheduler.policies.cr import CRPolicy
from fab_scheduler.policies.edd import EDDPolicy
from fab_scheduler.policies.fifo import FIFOPolicy
from fab_scheduler.policies.spt import SPTPolicy
from fab_scheduler.simulation.engine import SimulationResult, Simulator


POLICY_CONFIG_VERSION = DISPATCH_POLICY_CONTRACT_VERSION
_POLICIES: dict[str, type[DispatchPolicy]] = {
    "fifo": FIFOPolicy,
    "spt": SPTPolicy,
    "edd": EDDPolicy,
    "cr": CRPolicy,
}


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    """当前 theta schema；baseline 暂不接受策略参数。"""

    policy_id: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        policy_id = self.policy_id.lower().strip()
        if policy_id not in _POLICIES:
            raise ValueError(f"未知 policy_id：{self.policy_id}")
        copied = dict(self.parameters)
        if copied:
            raise ValueError("当前 baseline policy 不接受 parameters")
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "parameters", MappingProxyType(copied))

    @classmethod
    def parse(cls, theta: PolicyConfig | Mapping[str, Any]) -> PolicyConfig:
        if isinstance(theta, cls):
            return theta
        if not isinstance(theta, Mapping):
            raise TypeError("theta 必须是 PolicyConfig 或 mapping")
        unknown = set(theta) - {"policy_id", "parameters"}
        if unknown:
            raise ValueError(f"theta 包含未知字段：{sorted(unknown)}")
        if "policy_id" not in theta:
            raise ValueError("theta 缺少 policy_id")
        parameters = theta.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise TypeError("theta.parameters 必须是 mapping")
        return cls(str(theta["policy_id"]), parameters)


def build_policy(config: PolicyConfig) -> DispatchPolicy:
    """只构造确定性 baseline policy，不执行任何参数搜索。"""

    return _POLICIES[config.policy_id]()


def simulate(
    theta: PolicyConfig | Mapping[str, Any],
    scenario: Scenario,
    seed: int,
    *,
    git_commit: str | None = None,
) -> SimulationResult:
    """验证 theta、构造 policy 并运行一次不可变 Scenario。"""

    if not isinstance(scenario, Scenario):
        raise TypeError("scenario 必须是 Scenario")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError("seed 必须是 int")
    config = PolicyConfig.parse(theta)
    policy = build_policy(config)
    return Simulator(
        scenario,
        policy=policy,
        policy_parameters=dict(config.parameters),
        seed=seed,
        git_commit=git_commit,
    ).run()


__all__ = [
    "POLICY_CONFIG_VERSION",
    "PolicyConfig",
    "build_policy",
    "simulate",
]
