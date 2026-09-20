"""可信轻量 DES 内核的第一阶段实现。

当前仅覆盖 MC01、MC02：动态投放、等待队列、FIFO 派工、确定性加工、
路线推进和终止。Batch、Setup、CQT、Dedication、Failure/PM 尚未解锁。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import heapq
from statistics import fmean
from typing import Any

from fab_scheduler.domain.models import LotSpec, Scenario
from fab_scheduler.policies.base import (
    DispatchAction,
    DispatchPolicy,
    DispatchState,
)
from fab_scheduler.policies.fifo import FIFOPolicy
from fab_scheduler.simulation.events import (
    EVENT_PRIORITIES,
    Event,
    EventType,
    TraceRecord,
)
from fab_scheduler.simulation.provenance import (
    SIMULATION_CONTRACT_VERSION,
    RunProvenance,
    discover_git_commit,
)
from fab_scheduler.simulation.random_streams import EntityRandomStreams


class LotStatus(str, Enum):
    UNRELEASED = "UNRELEASED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


class MachineStatus(str, Enum):
    IDLE = "IDLE"
    PROCESSING = "PROCESSING"


@dataclass(slots=True)
class _LotRuntime:
    spec: LotSpec
    status: LotStatus = LotStatus.UNRELEASED
    operation_index: int = 0
    queue_entered_at: float | None = None
    completion_time: float | None = None
    current_machine_id: str | None = None


@dataclass(slots=True)
class _MachineRuntime:
    machine_id: str
    status: MachineStatus = MachineStatus.IDLE
    lot_id: str | None = None
    operation_index: int | None = None
    processing_started_at: float | None = None


@dataclass(frozen=True, slots=True)
class ProcessingInterval:
    lot_id: str
    machine_id: str
    route_id: str
    step_id: int
    start: float
    finish: float


@dataclass(frozen=True, slots=True)
class SimulationMetrics:
    completed_lots: int
    released_lots: int
    completion_ratio: float
    mean_cycle_time_completed: float | None
    throughput_lots_per_minute: float
    terminal_wip_lots: int
    end_time: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    provenance: RunProvenance
    metrics: SimulationMetrics
    trace: tuple[TraceRecord, ...]
    processing_intervals: tuple[ProcessingInterval, ...]
    completion_times: dict[str, float]

    def trace_as_dicts(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.trace]

    def key_trace(self) -> list[dict[str, Any]]:
        """返回适合人工逐事件核算的业务 trace。"""

        visible = {
            "LOT_RELEASE",
            "DISPATCH",
            "PROCESS_START",
            "PROCESS_FINISH",
            "ROUTE_ADVANCE",
            "LOT_COMPLETE",
        }
        rows = []
        for record in self.trace:
            if record.event_type not in visible:
                continue
            rows.append(
                {
                    key: value
                    for key, value in {
                        "time": record.sim_time,
                        "event": record.event_type,
                        "lot": record.lot_id,
                        "machine": record.machine_id,
                        "step": record.step_id,
                    }.items()
                    if value is not None
                }
            )
        return rows


class SimulationError(RuntimeError):
    """仿真输入或事件状态违反契约。"""


class Simulator:
    """事件驱动、确定性且带完整 trace 的最小仿真器。"""

    def __init__(
        self,
        scenario: Scenario,
        *,
        policy: DispatchPolicy | None = None,
        seed: int = 0,
        git_commit: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.policy = policy or FIFOPolicy()
        self.seed = seed
        self.random_streams = EntityRandomStreams(seed)
        self.current_time = 0.0
        self._calendar: list[Event] = []
        self._next_calendar_seq = 0
        self._next_trace_seq = 0
        self._pending_dispatch_barriers: set[float] = set()
        self._trace: list[TraceRecord] = []
        self._processing_intervals: list[ProcessingInterval] = []
        self._lots = {
            lot.lot_id: _LotRuntime(spec=lot)
            for lot in scenario.lots
        }
        self._machines = {
            machine.machine_id: _MachineRuntime(machine_id=machine.machine_id)
            for machine in scenario.machines
        }
        self._run_id = f"{scenario.scenario_id}:seed={seed}"
        self._git_commit = git_commit or discover_git_commit()

    def run(self) -> SimulationResult:
        for lot in sorted(
            self.scenario.lots,
            key=lambda item: (item.release_time, item.lot_id),
        ):
            self._schedule(
                time=lot.release_time,
                event_type=EventType.LOT_RELEASE,
                entity_id=lot.lot_id,
                payload={"lot_id": lot.lot_id},
            )

        if not self.scenario.lots:
            self.current_time = self.scenario.horizon or 0.0
        else:
            self._run_calendar()

        end_time = (
            self.scenario.horizon
            if self.scenario.termination_mode == "fixed_horizon"
            else self.current_time
        )
        assert end_time is not None
        self.current_time = end_time
        metrics = self._build_metrics(end_time)
        provenance = RunProvenance(
            simulation_contract_version=SIMULATION_CONTRACT_VERSION,
            dataset_version=self.scenario.dataset_version,
            git_commit=self._git_commit,
            seed=self.seed,
            simulation_config={
                **asdict(self.scenario),
                "random_stream_scheme": "sha256(seed,stream,entity,occurrence)",
            },
            dispatch_policy=self.policy.name,
            termination_condition=self.scenario.termination_mode,
            horizon=self.scenario.horizon,
        )
        completion_times = {
            lot_id: runtime.completion_time
            for lot_id, runtime in self._lots.items()
            if runtime.completion_time is not None
        }
        return SimulationResult(
            provenance=provenance,
            metrics=metrics,
            trace=tuple(self._trace),
            processing_intervals=tuple(self._processing_intervals),
            completion_times=completion_times,
        )

    def _run_calendar(self) -> None:
        while self._calendar:
            event = heapq.heappop(self._calendar)
            horizon = self.scenario.horizon
            if (
                self.scenario.termination_mode == "fixed_horizon"
                and horizon is not None
                and event.time > horizon
            ):
                self.current_time = horizon
                return
            if event.time < self.current_time:
                raise SimulationError("事件时间倒退")
            self.current_time = event.time
            self._handle(event)
            if (
                self.scenario.termination_mode == "until_all_complete"
                and all(
                    lot.status is LotStatus.COMPLETED
                    for lot in self._lots.values()
                )
            ):
                return

        if (
            self.scenario.termination_mode == "until_all_complete"
            and any(
                lot.status is not LotStatus.COMPLETED
                for lot in self._lots.values()
            )
        ):
            blocked = sorted(
                lot_id
                for lot_id, lot in self._lots.items()
                if lot.status is not LotStatus.COMPLETED
            )
            raise SimulationError(f"事件日历耗尽但仍有未完成 lot：{blocked}")

    def _schedule(
        self,
        *,
        time: float,
        event_type: EventType,
        entity_id: str,
        payload: dict[str, Any],
    ) -> int:
        seq = self._next_calendar_seq
        self._next_calendar_seq += 1
        heapq.heappush(
            self._calendar,
            Event(
                time=time,
                priority=int(EVENT_PRIORITIES[event_type]),
                seq=seq,
                event_type=event_type,
                entity_id=entity_id,
                payload=payload,
            ),
        )
        return seq

    def _handle(self, event: Event) -> None:
        if event.event_type is EventType.LOT_RELEASE:
            self._handle_release(event)
        elif event.event_type is EventType.PROCESS_FINISH:
            self._handle_process_finish(event)
        elif event.event_type is EventType.DISPATCH_BARRIER:
            self._handle_dispatch_barrier(event)
        else:
            raise SimulationError(f"尚未实现事件类型：{event.event_type}")

    def _handle_release(self, event: Event) -> None:
        lot = self._lots[event.entity_id]
        if lot.status is not LotStatus.UNRELEASED:
            raise SimulationError(f"lot 重复释放：{event.entity_id}")
        lot.status = LotStatus.QUEUED
        lot.queue_entered_at = self.current_time
        operation = lot.spec.operations[lot.operation_index]
        self._record(
            event_type="LOT_RELEASE",
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=operation,
            state_before=LotStatus.UNRELEASED.value,
            state_after=LotStatus.QUEUED.value,
        )
        self._ensure_dispatch_barrier()

    def _handle_dispatch_barrier(self, event: Event) -> None:
        self._pending_dispatch_barriers.discard(event.time)
        self._record(
            event_type="DISPATCH_BARRIER",
            priority=event.priority,
            cause_event_seq=event.seq,
            state_before="STATE_UPDATES_COMPLETE",
            state_after="DISPATCH_COMMITTED",
        )
        for machine_id in sorted(self._machines):
            machine = self._machines[machine_id]
            if machine.status is not MachineStatus.IDLE:
                continue
            actions = self._feasible_actions(machine_id)
            action = self.policy.select(
                DispatchState(current_time=self.current_time),
                actions,
            )
            if action is None:
                continue
            if action not in actions:
                raise SimulationError("策略返回了不可行动作")
            self._start_processing(event, action)

    def _feasible_actions(self, machine_id: str) -> list[DispatchAction]:
        actions = []
        for lot_id in sorted(self._lots):
            lot = self._lots[lot_id]
            if lot.status is not LotStatus.QUEUED:
                continue
            operation = lot.spec.operations[lot.operation_index]
            if machine_id not in operation.eligible_machines:
                continue
            if lot.queue_entered_at is None:
                raise SimulationError(f"排队 lot 缺少入队时间：{lot_id}")
            actions.append(
                DispatchAction(
                    lot_id=lot_id,
                    machine_id=machine_id,
                    operation_index=lot.operation_index,
                    step_id=operation.step_id,
                    queue_entered_at=lot.queue_entered_at,
                    release_time=lot.spec.release_time,
                )
            )
        return actions

    def _start_processing(
        self,
        barrier_event: Event,
        action: DispatchAction,
    ) -> None:
        lot = self._lots[action.lot_id]
        machine = self._machines[action.machine_id]
        if lot.status is not LotStatus.QUEUED:
            raise SimulationError("原子提交时 lot 已不可用")
        if machine.status is not MachineStatus.IDLE:
            raise SimulationError("原子提交时 machine 已不可用")
        operation = lot.spec.operations[lot.operation_index]
        lot.status = LotStatus.PROCESSING
        lot.current_machine_id = machine.machine_id
        lot.queue_entered_at = None
        machine.status = MachineStatus.PROCESSING
        machine.lot_id = lot.spec.lot_id
        machine.operation_index = lot.operation_index
        machine.processing_started_at = self.current_time
        self._record(
            event_type="DISPATCH",
            priority=barrier_event.priority,
            cause_event_seq=barrier_event.seq,
            lot=lot,
            operation=operation,
            machine_id=machine.machine_id,
            state_before="LOT:QUEUED|MACHINE:IDLE",
            state_after="LOT:RESERVED|MACHINE:RESERVED",
        )
        self._record(
            event_type="PROCESS_START",
            priority=barrier_event.priority,
            cause_event_seq=barrier_event.seq,
            lot=lot,
            operation=operation,
            machine_id=machine.machine_id,
            state_before="LOT:RESERVED|MACHINE:RESERVED",
            state_after="LOT:PROCESSING|MACHINE:PROCESSING",
        )
        self._schedule(
            time=self.current_time + operation.processing_time,
            event_type=EventType.PROCESS_FINISH,
            entity_id=machine.machine_id,
            payload={
                "machine_id": machine.machine_id,
                "lot_id": lot.spec.lot_id,
                "operation_index": lot.operation_index,
            },
        )

    def _handle_process_finish(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        lot_id = event.payload["lot_id"]
        operation_index = event.payload["operation_index"]
        machine = self._machines[machine_id]
        lot = self._lots[lot_id]
        if (
            machine.status is not MachineStatus.PROCESSING
            or machine.lot_id != lot_id
            or machine.operation_index != operation_index
            or lot.status is not LotStatus.PROCESSING
            or lot.operation_index != operation_index
        ):
            raise SimulationError(f"PROCESS_FINISH 状态不一致：{event.payload}")
        operation = lot.spec.operations[operation_index]
        if machine.processing_started_at is None:
            raise SimulationError("加工结束时缺少开始时间")
        self._processing_intervals.append(
            ProcessingInterval(
                lot_id=lot_id,
                machine_id=machine_id,
                route_id=operation.route_id,
                step_id=operation.step_id,
                start=machine.processing_started_at,
                finish=self.current_time,
            )
        )
        self._record(
            event_type="PROCESS_FINISH",
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=operation,
            machine_id=machine_id,
            state_before="LOT:PROCESSING|MACHINE:PROCESSING",
            state_after="LOT:PROCESSED|MACHINE:IDLE",
        )
        machine.status = MachineStatus.IDLE
        machine.lot_id = None
        machine.operation_index = None
        machine.processing_started_at = None
        lot.current_machine_id = None
        lot.operation_index += 1
        if lot.operation_index == len(lot.spec.operations):
            lot.status = LotStatus.COMPLETED
            lot.completion_time = self.current_time
            self._record(
                event_type="LOT_COMPLETE",
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                operation=operation,
                state_before="PROCESSED",
                state_after=LotStatus.COMPLETED.value,
            )
        else:
            next_operation = lot.spec.operations[lot.operation_index]
            lot.status = LotStatus.QUEUED
            lot.queue_entered_at = self.current_time
            self._record(
                event_type="ROUTE_ADVANCE",
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                operation=next_operation,
                state_before="PROCESSED",
                state_after=LotStatus.QUEUED.value,
            )
        self._ensure_dispatch_barrier()

    def _ensure_dispatch_barrier(self) -> None:
        if self.current_time in self._pending_dispatch_barriers:
            return
        self._pending_dispatch_barriers.add(self.current_time)
        self._schedule(
            time=self.current_time,
            event_type=EventType.DISPATCH_BARRIER,
            entity_id="system",
            payload={},
        )

    def _record(
        self,
        *,
        event_type: str,
        priority: int,
        cause_event_seq: int,
        lot: _LotRuntime | None = None,
        operation: Any | None = None,
        machine_id: str | None = None,
        state_before: str | None = None,
        state_after: str | None = None,
    ) -> None:
        record = TraceRecord(
            run_id=self._run_id,
            event_seq=self._next_trace_seq,
            sim_time=self.current_time,
            priority=priority,
            event_type=event_type,
            lot_id=lot.spec.lot_id if lot is not None else None,
            # MC01/MC02 没有返工，每道工序都是首次 visit。
            # 后续解锁返工时改为按 (lot, step) 独立计数。
            visit_index=0 if lot is not None else None,
            route_id=operation.route_id if operation is not None else None,
            step_id=operation.step_id if operation is not None else None,
            machine_id=machine_id,
            tool_group_id=(
                operation.tool_group_id if operation is not None else None
            ),
            state_before=state_before,
            state_after=state_after,
            cause_event_seq=cause_event_seq,
        )
        self._next_trace_seq += 1
        self._trace.append(record)

    def _build_metrics(self, end_time: float) -> SimulationMetrics:
        released = [
            lot for lot in self._lots.values()
            if lot.status is not LotStatus.UNRELEASED
        ]
        completed = [
            lot for lot in released
            if lot.status is LotStatus.COMPLETED
        ]
        cycle_times = [
            lot.completion_time - lot.spec.release_time
            for lot in completed
            if lot.completion_time is not None
        ]
        return SimulationMetrics(
            completed_lots=len(completed),
            released_lots=len(released),
            completion_ratio=(
                len(completed) / len(released) if released else 0.0
            ),
            mean_cycle_time_completed=(
                fmean(cycle_times) if cycle_times else None
            ),
            throughput_lots_per_minute=(
                len(completed) / end_time if end_time > 0 else 0.0
            ),
            terminal_wip_lots=len(released) - len(completed),
            end_time=end_time,
        )
