"""MC07 故障发生时刻生成；暂停、维修和恢复由 engine 统一执行。"""

from __future__ import annotations

from dataclasses import dataclass

from fab_scheduler.domain.models import MachineFailureSpec, TimeDistributionSpec
from fab_scheduler.simulation.random_streams import EntityRandomStreams
from fab_scheduler.simulation.distributions import sample_distribution


FAILURE_RUNTIME_SCHEMA_VERSION = "0.1.0"
FAILURE_INTERVAL_STREAM = "failure"
REPAIR_DURATION_STREAM = "repair"


@dataclass(frozen=True, slots=True)
class FailureOccurrence:
    machine_id: str
    occurrence_index: int
    failure_time: float
    repair_duration: float
    model_type: str


class FailureSchedule:
    """生成 scripted 或按实体索引随机流采样的故障 occurrence。"""

    def __init__(self, specs: tuple[MachineFailureSpec, ...], streams: EntityRandomStreams) -> None:
        self._specs = {spec.machine_id: spec for spec in specs}
        self._streams = streams

    def initial_occurrences(self) -> tuple[FailureOccurrence, ...]:
        occurrences: list[FailureOccurrence] = []
        for machine_id in sorted(self._specs):
            spec = self._specs[machine_id]
            if spec.model_type == "scripted":
                for index, item in enumerate(spec.scripted_failures):
                    occurrences.append(FailureOccurrence(machine_id, index, item.failure_time, item.repair_duration, "scripted"))
            else:
                occurrences.append(self._sample_stochastic(machine_id, 0, 0.0))
        return tuple(sorted(occurrences, key=lambda item: (item.failure_time, item.machine_id, item.occurrence_index)))

    def next_after_repair(self, *, machine_id: str, occurrence_index: int, repaired_at: float) -> FailureOccurrence | None:
        spec = self._specs[machine_id]
        if spec.model_type != "stochastic":
            return None
        return self._sample_stochastic(machine_id, occurrence_index + 1, repaired_at)

    def _sample_stochastic(self, machine_id: str, occurrence_index: int, basis_time: float) -> FailureOccurrence:
        spec = self._specs[machine_id]
        assert spec.failure_interval is not None and spec.repair_duration is not None
        interval = sample_distribution(spec.failure_interval, random_source=self._streams, stream_name=FAILURE_INTERVAL_STREAM, entity_id=machine_id, occurrence_index=occurrence_index)
        repair = sample_distribution(spec.repair_duration, random_source=self._streams, stream_name=REPAIR_DURATION_STREAM, entity_id=machine_id, occurrence_index=occurrence_index)
        return FailureOccurrence(machine_id, occurrence_index, basis_time + interval, repair, "stochastic")
