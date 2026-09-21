"""跨策略 Common Random Numbers 账本比较。"""

from __future__ import annotations

from dataclasses import dataclass

from fab_scheduler.simulation.engine import SimulationResult
from fab_scheduler.simulation.random_streams import RandomSampleRecord


RandomIdentity = tuple[str, str, int]


@dataclass(frozen=True, slots=True)
class CRNAuditResult:
    common_identities: tuple[RandomIdentity, ...]
    mismatched_identities: tuple[RandomIdentity, ...]
    left_only_identities: tuple[RandomIdentity, ...]
    right_only_identities: tuple[RandomIdentity, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatched_identities


def _by_identity(
    ledger: tuple[RandomSampleRecord, ...],
) -> dict[RandomIdentity, RandomSampleRecord]:
    indexed = {record.identity: record for record in ledger}
    if len(indexed) != len(ledger):
        raise ValueError("RandomSampleLedger identity 重复")
    return indexed


def audit_common_random_numbers(
    left: SimulationResult,
    right: SimulationResult,
) -> CRNAuditResult:
    """共同 identity 必须具有相同样本；单边 identity 是轨迹特有 occurrence。"""

    left_samples = _by_identity(left.random_sample_ledger)
    right_samples = _by_identity(right.random_sample_ledger)
    common = tuple(sorted(left_samples.keys() & right_samples.keys()))
    mismatches = tuple(
        identity
        for identity in common
        if left_samples[identity] != right_samples[identity]
    )
    return CRNAuditResult(
        common_identities=common,
        mismatched_identities=mismatches,
        left_only_identities=tuple(
            sorted(left_samples.keys() - right_samples.keys())
        ),
        right_only_identities=tuple(
            sorted(right_samples.keys() - left_samples.keys())
        ),
    )
