"""受限 per-lot operation sampling runtime。"""

from __future__ import annotations

from dataclasses import dataclass

from fab_scheduler.domain.models import OperationSpec
from fab_scheduler.simulation.random_streams import EntityRandomStreams


SAMPLING_RUNTIME_SCHEMA_VERSION = "0.1.0"
SAMPLING_RUNTIME_ID = "per_lot_sampling"
SAMPLING_STREAM = "sampling"


@dataclass(frozen=True, slots=True)
class SamplingDecision:
    sampling_percent: float
    draw: float | None
    performed: bool
    entity_id: str


def sampling_entity_id(
    lot_id: str,
    route_id: str,
    step_id: int,
    visit_index: int,
) -> str:
    """稳定地标识一次 lot/route/step visit，不依赖策略或事件顺序。"""

    return f"{lot_id}|{route_id}|{step_id}|visit={visit_index}"


def decide_sampling(
    operation: OperationSpec,
    *,
    lot_id: str,
    visit_index: int,
    random_source: EntityRandomStreams,
) -> SamplingDecision | None:
    """对已进入 operation 的 lot 做一次采样判定。

    ``None`` 表示 raw operation，完全不产生 sampling decision 或随机样本。
    百分比 100 是确定执行，也不写随机 ledger，但仍写 decision trace。
    """

    percent = operation.sample_percent
    if percent is None:
        return None
    entity_id = sampling_entity_id(
        lot_id, operation.route_id, operation.step_id, visit_index
    )
    if percent == 100:
        return SamplingDecision(float(percent), None, True, entity_id)
    draw = random_source.uniform(
        SAMPLING_STREAM,
        entity_id,
        visit_index,
        0.0,
        100.0,
    )
    return SamplingDecision(float(percent), draw, draw <= percent, entity_id)
