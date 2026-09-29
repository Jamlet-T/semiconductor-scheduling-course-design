"""外生、无容量 transport 的静态解析与运行时采样。"""

from __future__ import annotations

from dataclasses import dataclass

from fab_scheduler.domain.models import TimeDistributionSpec, TransportSpec
from fab_scheduler.simulation.distributions import sample_distribution
from fab_scheduler.simulation.random_streams import EntityRandomStreams


TRANSPORT_RUNTIME_SCHEMA_VERSION = "0.1.0"
TRANSPORT_STREAM = "transport"


@dataclass(frozen=True, slots=True)
class TransportResolution:
    from_location: str
    to_location: str
    duration: TimeDistributionSpec | None

    @property
    def missing_pair(self) -> bool:
        return self.duration is None


class TransportResolver:
    """按 location pair 查表；未命中不抽样且显式返回 missing。"""

    def __init__(self, specs: tuple[TransportSpec, ...]) -> None:
        self._specs = {
            (spec.from_location, spec.to_location): spec.duration
            for spec in specs
        }

    @property
    def enabled(self) -> bool:
        return bool(self._specs)

    def resolve(self, from_location: str, to_location: str) -> TransportResolution:
        return TransportResolution(
            from_location,
            to_location,
            self._specs.get((from_location, to_location)),
        )

    def sample(
        self,
        resolution: TransportResolution,
        *,
        lot_id: str,
        route_id: str,
        from_step_id: int,
        to_step_id: int,
        visit_index: int,
        random_source: EntityRandomStreams,
    ) -> tuple[float, str]:
        if resolution.duration is None:
            raise ValueError("missing transport pair 不应抽样")
        entity_id = (
            f"{lot_id}|{route_id}|from_step={from_step_id}|to_step={to_step_id}|"
            f"{resolution.from_location}|{resolution.to_location}|visit={visit_index}"
        )
        value = sample_distribution(
            resolution.duration,
            random_source=random_source,
            stream_name=TRANSPORT_STREAM,
            entity_id=entity_id,
            occurrence_index=0,
        )
        return value, entity_id
