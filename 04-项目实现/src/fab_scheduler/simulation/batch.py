"""Contract 0.1.2 的确定性 Batch formation。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fab_scheduler.domain.models import BatchSpec


BatchStartReason = Literal["TARGET_REACHED", "TIMEOUT_REACHED"]


class BatchFormationError(ValueError):
    """Batch 配置或候选集合违反冻结契约。"""


@dataclass(frozen=True, slots=True)
class BatchCandidate:
    lot_id: str
    quantity_wafers: int
    queue_entered_at: float
    route_id: str
    step_id: int
    operation_index: int
    processing_time: float
    batch_spec: BatchSpec


@dataclass(frozen=True, slots=True)
class BatchDecision:
    compatibility_key: tuple[str, int]
    selected_members: tuple[BatchCandidate, ...]
    compatible_wafers: int
    selected_wafers: int
    start_reason: BatchStartReason | None
    timeout_at: float | None

    @property
    def can_start(self) -> bool:
        return self.start_reason is not None


class BatchFormation:
    """按 queue time、lot ID 稳定组批，不包含任何派工策略逻辑。"""

    def evaluate(
        self,
        *,
        current_time: float,
        candidates: tuple[BatchCandidate, ...],
    ) -> BatchDecision:
        if not candidates:
            raise BatchFormationError("Batch formation 不能为空")
        ordered = tuple(
            sorted(
                candidates,
                key=lambda item: (item.queue_entered_at, item.lot_id),
            )
        )
        first = ordered[0]
        key = (first.route_id, first.step_id)
        spec = first.batch_spec
        processing_time = first.processing_time
        for candidate in ordered:
            if (candidate.route_id, candidate.step_id) != key:
                raise BatchFormationError(
                    "crit_sameroutestep 候选包含不同 route/step"
                )
            if candidate.batch_spec != spec:
                raise BatchFormationError(
                    "相同 route/step 的 BatchSpec 不一致"
                )
            if candidate.processing_time != processing_time:
                raise BatchFormationError(
                    "同一物理 batch 的 processing_time 必须一致"
                )
            if candidate.quantity_wafers > spec.maximum_wafers:
                raise BatchFormationError(
                    f"lot {candidate.lot_id} wafer 数超过 B_max"
                )
            if candidate.queue_entered_at > current_time:
                raise BatchFormationError("候选 lot 尚未进入队列")

        selected: list[BatchCandidate] = []
        selected_wafers = 0
        for candidate in ordered:
            next_total = selected_wafers + candidate.quantity_wafers
            if next_total > spec.maximum_wafers:
                break
            selected.append(candidate)
            selected_wafers = next_total

        compatible_wafers = sum(
            candidate.quantity_wafers for candidate in ordered
        )
        if selected_wafers < spec.minimum_wafers:
            return BatchDecision(
                compatibility_key=key,
                selected_members=tuple(selected),
                compatible_wafers=compatible_wafers,
                selected_wafers=selected_wafers,
                start_reason=None,
                timeout_at=None,
            )
        if selected_wafers >= spec.target_wafers:
            return BatchDecision(
                compatibility_key=key,
                selected_members=tuple(selected),
                compatible_wafers=compatible_wafers,
                selected_wafers=selected_wafers,
                start_reason="TARGET_REACHED",
                timeout_at=None,
            )

        timeout_at = first.queue_entered_at + spec.max_wait_minutes
        if current_time >= timeout_at:
            return BatchDecision(
                compatibility_key=key,
                selected_members=tuple(selected),
                compatible_wafers=compatible_wafers,
                selected_wafers=selected_wafers,
                start_reason="TIMEOUT_REACHED",
                timeout_at=timeout_at,
            )
        return BatchDecision(
            compatibility_key=key,
            selected_members=tuple(selected),
            compatible_wafers=compatible_wafers,
            selected_wafers=selected_wafers,
            start_reason=None,
            timeout_at=timeout_at,
        )
