"""MC08 Calendar/Wafer PM 排程、计数与确定性随机流。"""

from __future__ import annotations

from dataclasses import dataclass

from fab_scheduler.domain.models import (
    CalendarPMSpec,
    TimeDistributionSpec,
    WaferPMSpec,
)
from fab_scheduler.simulation.random_streams import EntityRandomStreams
from fab_scheduler.simulation.distributions import sample_distribution


PM_RUNTIME_SCHEMA_VERSION = "0.1.0"
PM_INTERVAL_STREAM = "pm_interval"
PM_DURATION_STREAM = "pm_duration"


@dataclass(frozen=True, slots=True)
class PMOccurrence:
    pm_id: str
    machine_id: str
    occurrence_index: int
    start_time: float
    duration: float
    trigger_type: str
    model_type: str


@dataclass(frozen=True, slots=True)
class WaferPMDue:
    pm_id: str
    machine_id: str
    occurrence_index: int
    counter_before: int
    processed_wafers: int
    counter_after: int
    threshold_wafers: int
    duration: float


@dataclass(frozen=True, slots=True)
class WaferPMStateSnapshot:
    pm_id: str
    machine_id: str
    counter_wafers: int
    threshold_wafers: int
    pending: bool
    active: bool
    occurrence_count: int
    reset_rule: str


@dataclass(slots=True)
class _WaferState:
    spec: WaferPMSpec
    counter_wafers: int
    pending: WaferPMDue | None = None
    active: WaferPMDue | None = None
    occurrence_count: int = 0


class PMRuntime:
    """不操作 machine；只生成 PM occurrence 并维护 wafer counter。"""

    def __init__(
        self,
        calendar_specs: tuple[CalendarPMSpec, ...],
        wafer_specs: tuple[WaferPMSpec, ...],
        streams: EntityRandomStreams,
    ) -> None:
        self._calendar = {spec.machine_id: spec for spec in calendar_specs}
        self._wafer = {
            spec.machine_id: _WaferState(spec, spec.initial_counter_wafers)
            for spec in wafer_specs
        }
        self._streams = streams

    def initial_calendar_occurrences(self) -> tuple[PMOccurrence, ...]:
        result: list[PMOccurrence] = []
        for machine_id in sorted(self._calendar):
            spec = self._calendar[machine_id]
            if spec.model_type == "scripted":
                for index, item in enumerate(spec.scripted_occurrences):
                    result.append(PMOccurrence(spec.pm_id, machine_id, index, item.start_time, item.duration, "CALENDAR_PM", "scripted"))
            else:
                assert spec.first_start_time is not None and spec.duration is not None
                result.append(PMOccurrence(spec.pm_id, machine_id, 0, spec.first_start_time, self._sample(spec.duration, PM_DURATION_STREAM, spec.pm_id, 0), "CALENDAR_PM", "periodic"))
        return tuple(sorted(result, key=lambda item: (item.start_time, item.machine_id, item.pm_id, item.occurrence_index)))

    def next_calendar_occurrence(self, occurrence: PMOccurrence) -> PMOccurrence | None:
        spec = self._calendar[occurrence.machine_id]
        if spec.model_type != "periodic":
            return None
        assert spec.interval is not None and spec.duration is not None
        index = occurrence.occurrence_index + 1
        interval = self._sample(spec.interval, PM_INTERVAL_STREAM, spec.pm_id, index)
        duration = self._sample(spec.duration, PM_DURATION_STREAM, spec.pm_id, index)
        return PMOccurrence(spec.pm_id, occurrence.machine_id, index, occurrence.start_time + interval, duration, "CALENDAR_PM", "periodic")

    def account_completed_wafers(self, machine_id: str, wafers: int) -> WaferPMDue | None:
        state = self._wafer.get(machine_id)
        if state is None:
            return None
        if wafers <= 0:
            raise ValueError("completed wafers 必须为正")
        before = state.counter_wafers
        state.counter_wafers += wafers
        if state.pending is not None or state.active is not None:
            raise RuntimeError("wafer PM pending/active 时不应完成新的物理加工")
        if state.counter_wafers < state.spec.threshold_wafers:
            return None
        index = state.occurrence_count
        due = WaferPMDue(
            pm_id=state.spec.pm_id,
            machine_id=machine_id,
            occurrence_index=index,
            counter_before=before,
            processed_wafers=wafers,
            counter_after=state.counter_wafers,
            threshold_wafers=state.spec.threshold_wafers,
            duration=self._sample(state.spec.duration, PM_DURATION_STREAM, state.spec.pm_id, index),
        )
        state.pending = due
        return due

    def pending_for_machine(self, machine_id: str) -> WaferPMDue | None:
        state = self._wafer.get(machine_id)
        return None if state is None else state.pending

    def start_wafer_pm(self, machine_id: str, occurrence_index: int) -> WaferPMDue:
        state = self._wafer[machine_id]
        due = state.pending
        if due is None or due.occurrence_index != occurrence_index:
            raise RuntimeError("wafer PM start 与 pending state 不一致")
        state.pending = None
        state.active = due
        return due

    def finish_wafer_pm(self, machine_id: str, occurrence_index: int) -> WaferPMDue:
        state = self._wafer[machine_id]
        due = state.active
        if due is None or due.occurrence_index != occurrence_index:
            raise RuntimeError("wafer PM finish 与 active state 不一致")
        state.active = None
        state.counter_wafers = 0
        state.occurrence_count += 1
        return due

    @property
    def snapshots(self) -> tuple[WaferPMStateSnapshot, ...]:
        return tuple(
            WaferPMStateSnapshot(
                pm_id=state.spec.pm_id,
                machine_id=machine_id,
                counter_wafers=state.counter_wafers,
                threshold_wafers=state.spec.threshold_wafers,
                pending=state.pending is not None,
                active=state.active is not None,
                occurrence_count=state.occurrence_count,
                reset_rule=state.spec.reset_rule,
            )
            for machine_id, state in sorted(self._wafer.items())
        )

    def _sample(self, distribution: TimeDistributionSpec, stream: str, pm_id: str, occurrence: int) -> float:
        return sample_distribution(distribution, random_source=self._streams, stream_name=stream, entity_id=pm_id, occurrence_index=occurrence)
