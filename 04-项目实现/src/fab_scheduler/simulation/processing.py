"""名义加工口径与提交后实现加工时长的唯一解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from fab_scheduler.domain.models import OperationSpec
from fab_scheduler.simulation.distributions import sample_distribution
from fab_scheduler.simulation.random_streams import EntityRandomStreams


PROCESSING_RUNTIME_SCHEMA_VERSION = "0.1.0"
PROCESSING_STREAM = "processing"
BATCH_PROCESSING_STREAM = "batch_processing"


@dataclass(frozen=True, slots=True)
class RealizedProcessingDuration:
    nominal_minutes: float
    sampled_core_minutes: float
    realized_minutes: float
    basis: str
    stream_name: str
    entity_id: str


class ProcessingDurationResolver:
    """候选阶段只读 nominal；仅已提交物理活动才调用 sample。"""

    @staticmethod
    def nominal(operation: OperationSpec, *, quantity_wafers: int) -> float:
        if quantity_wafers <= 0:
            raise ValueError("quantity_wafers 必须为正")
        base = operation.processing_time
        if operation.processing_basis == "per_piece":
            return base * quantity_wafers
        return base

    def realize_lot(
        self,
        *,
        operation: OperationSpec,
        lot_id: str,
        quantity_wafers: int,
        visit_index: int,
        random_source: EntityRandomStreams,
    ) -> RealizedProcessingDuration:
        identity = f"{lot_id}|{operation.route_id}|{operation.step_id}|visit={visit_index}"
        return self._realize(
            operation=operation,
            quantity_wafers=quantity_wafers,
            stream_name=PROCESSING_STREAM,
            entity_id=identity,
            random_source=random_source,
        )

    def realize_batch(
        self,
        *,
        operation: OperationSpec,
        ordered_member_ids: tuple[str, ...],
        total_wafers: int,
        random_source: EntityRandomStreams,
    ) -> RealizedProcessingDuration:
        identity = (
            f"{operation.route_id}|{operation.step_id}|"
            f"members={','.join(ordered_member_ids)}"
        )
        return self._realize(
            operation=operation,
            quantity_wafers=total_wafers,
            stream_name=BATCH_PROCESSING_STREAM,
            entity_id=identity,
            random_source=random_source,
        )

    def _realize(
        self,
        *,
        operation: OperationSpec,
        quantity_wafers: int,
        stream_name: str,
        entity_id: str,
        random_source: EntityRandomStreams,
    ) -> RealizedProcessingDuration:
        nominal = self.nominal(operation, quantity_wafers=quantity_wafers)
        if operation.processing_distribution is None:
            sampled_core = operation.processing_time
        else:
            sampled_core = sample_distribution(
                operation.processing_distribution,
                random_source=random_source,
                stream_name=stream_name,
                entity_id=entity_id,
                occurrence_index=0,
            )
        realized = sampled_core * quantity_wafers if operation.processing_basis == "per_piece" else sampled_core
        if not isfinite(realized) or realized <= 0:
            raise ValueError("realized processing duration 必须为有限正数")
        return RealizedProcessingDuration(nominal, sampled_core, realized, operation.processing_basis, stream_name, entity_id)
