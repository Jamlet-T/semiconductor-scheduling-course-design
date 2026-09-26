"""可信轻量 DES 内核的第一阶段实现。

当前覆盖 MC01-MC07：动态投放、等待队列、FIFO 派工、确定性加工、
Setup、Batch、CQT、Dedication 和 preemptive-resume Failure。PM 尚未解锁。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import heapq
from statistics import fmean
from typing import Any
from collections.abc import Mapping

from fab_scheduler.domain.models import LotSpec, ReleaseTemplateSpec, Scenario
from fab_scheduler.policies.base import (
    DISPATCH_POLICY_CONTRACT_VERSION,
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
from fab_scheduler.simulation.batch import (
    BatchCandidate,
    BatchDecision,
    BatchFormation,
)
from fab_scheduler.simulation.cqt import (
    CQT_RUNTIME_SCHEMA_VERSION,
    CQTMetrics,
    CQTClockState,
    CQTRecord,
    CQTRuntime,
    TerminalCQTSnapshot,
)
from fab_scheduler.simulation.dedication import (
    DEDICATION_RUNTIME_SCHEMA_VERSION,
    DedicationBinding,
    DedicationMetrics,
    DedicationRecord,
    DedicationRuntime,
    InitialWipDedicationAudit,
)
from fab_scheduler.simulation.provenance import (
    SIMULATION_CONTRACT_VERSION,
    RunProvenance,
    discover_git_commit,
)
from fab_scheduler.simulation.random_streams import (
    RANDOM_SAMPLE_LEDGER_VERSION,
    EntityRandomStreams,
    RandomSampleRecord,
)
from fab_scheduler.simulation.sampling import (
    SAMPLING_RUNTIME_ID,
    SAMPLING_RUNTIME_SCHEMA_VERSION,
    SAMPLING_STREAM,
    decide_sampling,
)
from fab_scheduler.simulation.setup import SetupDurationResolver
from fab_scheduler.simulation.pm import (
    PM_RUNTIME_SCHEMA_VERSION,
    PMOccurrence,
    PMRuntime,
    WaferPMDue,
    WaferPMStateSnapshot,
)
from fab_scheduler.simulation.failure import (
    FAILURE_RUNTIME_SCHEMA_VERSION,
    FailureOccurrence,
    FailureSchedule,
)
from fab_scheduler.simulation.processing import ProcessingDurationResolver
from fab_scheduler.simulation.transport import (
    TRANSPORT_RUNTIME_SCHEMA_VERSION,
    TRANSPORT_STREAM,
    TransportResolver,
)
from fab_scheduler.simulation.release import (
    RELEASE_RUNTIME_ID,
    RELEASE_RUNTIME_SCHEMA_VERSION,
    materialize_release_lot,
    release_lot_id,
    release_time,
)


class LotStatus(str, Enum):
    UNRELEASED = "UNRELEASED"
    TRANSPORTING = "TRANSPORTING"
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


class MachineStatus(str, Enum):
    IDLE = "IDLE"
    SETTING_UP = "SETTING_UP"
    PROCESSING = "PROCESSING"


class MachineAvailability(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class DowntimeCause(str, Enum):
    FAILURE = "FAILURE"
    CALENDAR_PM = "CALENDAR_PM"
    WAFER_PM = "WAFER_PM"


class ActivityKind(str, Enum):
    PROCESS = "PROCESS"
    SETUP = "SETUP"
    BATCH = "BATCH"


@dataclass(frozen=True, slots=True)
class _InterruptedActivity:
    kind: ActivityKind
    remaining_duration: float
    suspended_at: float


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
    location_id: str | None = None
    current_setup: str = ""
    status: MachineStatus = MachineStatus.IDLE
    lot_id: str | None = None
    operation_index: int | None = None
    setup_started_at: float | None = None
    setup_from: str | None = None
    setup_to: str | None = None
    processing_started_at: float | None = None
    active_batch_id: str | None = None
    availability: MachineAvailability = MachineAvailability.UP
    activity_token: int = 0
    scheduled_activity_finish: float | None = None
    interrupted_activity: _InterruptedActivity | None = None
    downtime_started_at: float | None = None
    repair_ends_at: float | None = None
    failure_count: int = 0
    downtime_cause: DowntimeCause | None = None
    downtime_id: str | None = None
    downtime_ends_at: float | None = None
    pm_count: int = 0


@dataclass(slots=True)
class _ActiveBatch:
    batch_id: str
    machine_id: str
    member_lot_ids: tuple[str, ...]
    member_wafers: tuple[int, ...]
    operation_indices: tuple[int, ...]
    route_id: str
    step_id: int
    total_wafers: int
    start_time: float
    scheduled_finish_time: float | None
    start_reason: str
    accumulated_processing_time: float = 0.0


@dataclass(frozen=True, slots=True)
class ProcessingInterval:
    lot_id: str
    machine_id: str
    route_id: str
    step_id: int
    start: float
    finish: float


@dataclass(frozen=True, slots=True)
class TransportInterval:
    lot_id: str
    route_id: str
    from_step_id: int
    to_step_id: int
    from_location: str
    to_location: str
    start: float
    finish: float
    duration: float
    missing_pair: bool


@dataclass(frozen=True, slots=True)
class ActiveTransportSnapshot:
    lot_id: str
    route_id: str
    from_step_id: int
    to_step_id: int
    from_location: str
    to_location: str
    start: float
    scheduled_finish: float
    remaining_duration: float


@dataclass(slots=True)
class _ActiveTransport:
    lot_id: str
    route_id: str
    from_step_id: int
    to_step_id: int
    from_location: str
    to_location: str
    start: float
    scheduled_finish: float
    duration: float


@dataclass(frozen=True, slots=True)
class TransportMetrics:
    started_count: int
    completed_count: int
    active_count: int
    missing_pair_count: int
    total_minutes: float
    missing_pairs: tuple[tuple[str, str, int], ...] = ()

    @property
    def transition_count(self) -> int:
        return self.started_count

    @property
    def configured_pair_count(self) -> int:
        return self.started_count - self.missing_pair_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_count": self.started_count,
            "completed_count": self.completed_count,
            "active_count": self.active_count,
            "configured_pair_count": self.configured_pair_count,
            "missing_pair_count": self.missing_pair_count,
            "total_minutes": self.total_minutes,
            "missing_pairs": self.missing_pairs,
        }


@dataclass(frozen=True, slots=True)
class SetupInterval:
    lot_id: str
    machine_id: str
    route_id: str
    step_id: int
    from_setup: str
    to_setup: str
    start: float
    finish: float


@dataclass(frozen=True, slots=True)
class BatchInterval:
    batch_id: str
    machine_id: str
    member_lot_ids: tuple[str, ...]
    member_wafers: tuple[int, ...]
    route_id: str
    step_id: int
    total_wafers: int
    start: float
    finish: float
    start_reason: str
    active_processing_time: float | None = None


@dataclass(frozen=True, slots=True)
class ActiveBatchSnapshot:
    batch_id: str
    machine_id: str
    member_lot_ids: tuple[str, ...]
    member_wafers: tuple[int, ...]
    total_wafers: int
    start: float
    scheduled_finish: float | None
    start_reason: str
    remaining_processing: float | None = None


@dataclass(frozen=True, slots=True)
class DowntimeInterval:
    machine_id: str
    occurrence_index: int
    start: float
    finish: float


@dataclass(frozen=True, slots=True)
class PMInterval:
    pm_id: str
    machine_id: str
    occurrence_index: int
    trigger_type: str
    start: float
    finish: float


@dataclass(frozen=True, slots=True)
class MachineFailureSnapshot:
    machine_id: str
    availability: str
    repair_ends_at: float | None
    remaining_repair_time: float | None
    interrupted_activity_kind: str | None
    remaining_activity_time: float | None
    lot_id: str | None
    batch_id: str | None
    downtime_cause: str | None = None
    downtime_id: str | None = None
    remaining_downtime_time: float | None = None
    wafer_pm_pending: bool = False


@dataclass(frozen=True, slots=True)
class MachineStatistics:
    machine_id: str
    processing_time: float
    setup_time: float
    idle_time: float
    final_state: str
    final_setup: str
    active_batch_id: str | None
    downtime: float = 0.0
    availability: str = MachineAvailability.UP.value
    failure_count: int = 0
    remaining_repair_time: float | None = None
    interrupted_activity_kind: str | None = None
    remaining_activity_time: float | None = None
    failure_downtime: float = 0.0
    pm_downtime: float = 0.0
    pm_count: int = 0
    downtime_cause: str | None = None
    wafer_counter: int | None = None
    wafer_pm_pending: bool = False


@dataclass(frozen=True, slots=True)
class SimulationMetrics:
    completed_lots: int
    released_lots: int
    completion_ratio: float
    mean_cycle_time_completed: float | None
    throughput_lots_per_minute: float
    terminal_wip_lots: int
    end_time: float
    cycle_time_coverage: float = 0.0
    remaining_work_minutes: float = 0.0
    lateness_exposure_minutes: float = 0.0
    mean_wip: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """保持 MC01-MC07 已冻结的基础 KPI 序列化契约。"""

        return {
            "completed_lots": self.completed_lots,
            "released_lots": self.released_lots,
            "completion_ratio": self.completion_ratio,
            "mean_cycle_time_completed": self.mean_cycle_time_completed,
            "throughput_lots_per_minute": self.throughput_lots_per_minute,
            "terminal_wip_lots": self.terminal_wip_lots,
            "end_time": self.end_time,
        }

    def terminal_exposure_to_dict(self) -> dict[str, float]:
        """输出 MC08 终态暴露指标，不改变既有基础 KPI 字典。"""

        return {
            "cycle_time_coverage": self.cycle_time_coverage,
            "remaining_work_minutes": self.remaining_work_minutes,
            "lateness_exposure_minutes": self.lateness_exposure_minutes,
            "mean_wip": self.mean_wip,
        }


@dataclass(frozen=True, slots=True)
class SimulationResult:
    provenance: RunProvenance
    metrics: SimulationMetrics
    trace: tuple[TraceRecord, ...]
    processing_intervals: tuple[ProcessingInterval, ...]
    setup_intervals: tuple[SetupInterval, ...]
    batch_intervals: tuple[BatchInterval, ...]
    active_batches: tuple[ActiveBatchSnapshot, ...]
    machine_statistics: dict[str, MachineStatistics]
    completion_times: dict[str, float]
    cqt_records: tuple[CQTRecord, ...]
    open_cqt_clocks: tuple[TerminalCQTSnapshot, ...]
    cqt_metrics: CQTMetrics
    dedication_records: tuple[DedicationRecord, ...]
    active_dedication_bindings: tuple[DedicationBinding, ...]
    initial_wip_dedication_audits: tuple[InitialWipDedicationAudit, ...]
    dedication_metrics: DedicationMetrics
    downtime_intervals: tuple[DowntimeInterval, ...]
    machine_failure_snapshots: tuple[MachineFailureSnapshot, ...]
    failure_count: int
    total_downtime: float
    total_failure_downtime: float
    pm_intervals: tuple[PMInterval, ...]
    wafer_pm_states: tuple[WaferPMStateSnapshot, ...]
    pm_count: int
    total_pm_downtime: float
    random_sample_ledger: tuple[RandomSampleRecord, ...]
    transport_intervals: tuple[TransportInterval, ...]
    active_transports: tuple[ActiveTransportSnapshot, ...]
    transport_metrics: TransportMetrics

    @property
    def policy_id(self) -> str:
        return self.provenance.dispatch_policy_id

    @property
    def policy_parameters(self) -> dict[str, Any]:
        return dict(self.provenance.policy_parameters)

    @property
    def seed(self) -> int:
        return self.provenance.seed

    @property
    def contract_version(self) -> str:
        return self.provenance.simulation_contract_version

    @property
    def policy_contract_version(self) -> str:
        return self.provenance.dispatch_policy_contract_version

    @property
    def scenario_identity(self) -> str:
        return str(self.provenance.simulation_config["scenario_id"])

    @property
    def termination(self) -> str:
        return self.provenance.termination_condition

    def trace_as_dicts(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.trace]

    def key_trace(self) -> list[dict[str, Any]]:
        """返回适合人工逐事件核算的业务 trace。"""

        visible = {
            "LOT_RELEASE",
            "DISPATCH",
            "SETUP_START",
            "SETUP_FINISH",
            "BATCH_TIMEOUT",
            "BATCH_FORMED",
            "BATCH_START",
            "BATCH_FINISH",
            "PROCESS_START",
            "PROCESS_FINISH",
            "ROUTE_ADVANCE",
            "LOT_COMPLETE",
            "CQT_OPEN",
            "CQT_CLOSE",
            "CQT_VIOLATION",
            "DEDICATION_BIND",
            "DEDICATION_RELEASE",
            "DEDICATION_HISTORY_UNKNOWN",
            "FAILURE_START",
            "PROCESS_SUSPEND",
            "SETUP_SUSPEND",
            "BATCH_SUSPEND",
            "REPAIR_COMPLETE",
            "PROCESS_RESUME",
            "SETUP_RESUME",
            "BATCH_RESUME",
            "PM_DUE",
            "PM_START",
            "PM_FINISH",
            "TRANSPORT_START",
            "TRANSPORT_ARRIVE",
            "TRANSPORT_MISSING",
            "SAMPLING_DECISION",
            "OPERATION_SKIPPED",
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
                        "batch": record.batch_id,
                        "member_lots": (
                            list(record.batch_member_lot_ids)
                            if record.batch_member_lot_ids is not None
                            else None
                        ),
                        "member_wafers": (
                            list(record.batch_member_wafers)
                            if record.batch_member_wafers is not None
                            else None
                        ),
                        "total_wafers": record.batch_total_wafers,
                        "start_reason": record.batch_start_reason,
                        "cqt_constraint": record.cqt_constraint_id,
                        "cqt_source_step": record.cqt_source_step_id,
                        "cqt_target_step": record.cqt_target_step_id,
                        "cqt_limit": record.cqt_limit,
                        "cqt_opened_at": record.cqt_opened_at,
                        "cqt_deadline": record.cqt_deadline,
                        "cqt_closed_at": record.cqt_closed_at,
                        "cqt_actual_duration": record.cqt_actual_duration,
                        "cqt_slack": record.cqt_slack,
                        "cqt_violation": record.cqt_violation,
                        "cqt_excess_duration": record.cqt_excess_duration,
                        "dedication_id": record.dedication_id,
                        "dedication_source_step": (
                            record.dedication_source_step_id
                        ),
                        "dedication_target_step": (
                            record.dedication_target_step_id
                        ),
                        "dedication_bound_machine": (
                            record.dedication_bound_machine_id
                        ),
                        "dedication_established_at": (
                            record.dedication_established_at
                        ),
                        "dedication_released_at": (
                            record.dedication_released_at
                        ),
                        "dedication_audit_reason": (
                            record.dedication_audit_reason
                        ),
                        "failure_occurrence": record.failure_occurrence_index,
                        "failure_model": record.failure_model_type,
                        "interrupted_activity": record.interrupted_activity_kind,
                        "remaining_duration": record.remaining_duration,
                        "repair_duration": record.repair_duration,
                        "activity_token": record.activity_token,
                        "downtime_cause": (
                            record.downtime_cause
                            if record.pm_id is not None
                            else None
                        ),
                        "pm_id": record.pm_id,
                        "pm_trigger": record.pm_trigger_type,
                        "pm_occurrence": record.pm_occurrence_index,
                        "pm_duration": record.pm_duration,
                        "wafer_counter_before": record.wafer_counter_before,
                        "wafer_counter_after": record.wafer_counter_after,
                        "wafer_threshold": record.wafer_threshold,
                        "processed_wafers": record.processed_wafers,
                        "transport_from": record.transport_from_location,
                        "transport_to": record.transport_to_location,
                        "transport_duration": record.transport_duration,
                        "transport_missing": record.transport_missing_pair,
                        "sampling_percent": record.sampling_percent,
                        "sampling_draw": record.sampling_draw,
                        "sampling_performed": record.sampling_performed,
                        "sampling_entity": record.sampling_entity_id,
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
        policy_parameters: Mapping[str, Any] | None = None,
        seed: int = 0,
        git_commit: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.policy = policy or FIFOPolicy()
        self.policy_parameters = dict(policy_parameters or {})
        self.seed = seed
        self.random_streams = EntityRandomStreams(seed)
        self.current_time = 0.0
        self._calendar: list[Event] = []
        self._next_calendar_seq = 0
        self._next_trace_seq = 0
        self._pending_dispatch_barriers: set[float] = set()
        self._trace: list[TraceRecord] = []
        self._processing_intervals: list[ProcessingInterval] = []
        self._setup_intervals: list[SetupInterval] = []
        self._batch_intervals: list[BatchInterval] = []
        self._downtime_intervals: list[DowntimeInterval] = []
        self._pm_intervals: list[PMInterval] = []
        self._transport_intervals: list[TransportInterval] = []
        self._active_transports: dict[str, _ActiveTransport] = {}
        self._active_batches: dict[str, _ActiveBatch] = {}
        self._batch_formation = BatchFormation()
        self._next_batch_seq = 1
        self._next_batch_timeout_token = 1
        self._pending_batch_timeouts: dict[
            tuple[str, str, int], tuple[int, float]
        ] = {}
        self._setup_resolver = SetupDurationResolver(
            scenario.setup_transitions
        )
        self._processing_resolver = ProcessingDurationResolver()
        self._cqt_runtime = CQTRuntime(scenario.cqt_constraints)
        self._dedication_runtime = DedicationRuntime(
            scenario.dedication_constraints
        )
        self._failure_schedule = FailureSchedule(
            scenario.failure_specs,
            self.random_streams,
        )
        self._active_failure_occurrence: dict[str, FailureOccurrence] = {}
        self._failure_suppressed_by_pm: dict[str, FailureOccurrence] = {}
        self._pm_runtime = PMRuntime(
            scenario.calendar_pm_specs,
            scenario.wafer_pm_specs,
            self.random_streams,
        )
        self._active_pm_occurrence: dict[str, PMOccurrence] = {}
        self._transport_resolver = TransportResolver(scenario.transport_specs)
        self._lots = {
            lot.lot_id: _LotRuntime(
                spec=lot,
                operation_index=lot.initial_operation_index,
            )
            for lot in scenario.lots
        }
        self._release_templates = {
            template.template_id: template
            for template in scenario.release_templates
        }
        self._machines = {
            machine.machine_id: _MachineRuntime(
                machine_id=machine.machine_id,
                location_id=machine.location_id,
                current_setup=machine.initial_setup,
            )
            for machine in scenario.machines
        }
        self._run_id = f"{scenario.scenario_id}:seed={seed}"
        self._git_commit = git_commit or discover_git_commit()

    def run(self) -> SimulationResult:
        for occurrence in self._failure_schedule.initial_occurrences():
            self._schedule_failure(occurrence)
        for occurrence in self._pm_runtime.initial_calendar_occurrences():
            self._schedule_pm(occurrence)
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
        # 模板只预排第 0 次 occurrence；后续 occurrence 在前一次
        # LOT_RELEASE 被处理后惰性安排，避免按 repeat_limit 预展开。
        for template in sorted(
            self.scenario.release_templates,
            key=lambda item: (item.template_id, item.lot_prefix),
        ):
            self._schedule_release_occurrence(template, repeat_index=0)

        if self._calendar:
            self._run_calendar()
        else:
            self.current_time = self.scenario.horizon or 0.0

        end_time = (
            self.scenario.horizon
            if self.scenario.termination_mode == "fixed_horizon"
            else self.current_time
        )
        assert end_time is not None
        self.current_time = end_time
        metrics = self._build_metrics(end_time)
        machine_statistics = self._build_machine_statistics(end_time)
        open_cqt_clocks = self._cqt_runtime.terminal_snapshots(
            at_time=end_time
        )
        cqt_metrics = self._cqt_runtime.metrics(at_time=end_time)
        dedication_metrics = self._dedication_runtime.metrics
        transport_metrics = self._build_transport_metrics()
        active_transports = self._build_active_transport_snapshots(end_time)
        provenance = RunProvenance(
            simulation_contract_version=SIMULATION_CONTRACT_VERSION,
            dispatch_policy_contract_version=(
                DISPATCH_POLICY_CONTRACT_VERSION
            ),
            dataset_version=self.scenario.dataset_version,
            git_commit=self._git_commit,
            seed=self.seed,
            simulation_config={
                **asdict(self.scenario),
                "random_stream_scheme": "sha256(seed,stream,entity,occurrence)",
                "random_sample_ledger_version": RANDOM_SAMPLE_LEDGER_VERSION,
                "cqt_runtime_schema_version": CQT_RUNTIME_SCHEMA_VERSION,
                "dedication_runtime_schema_version": (
                    DEDICATION_RUNTIME_SCHEMA_VERSION
                ),
                "failure_runtime_schema_version": FAILURE_RUNTIME_SCHEMA_VERSION,
                "failure_random_streams": {
                    "failure_interval": "failure",
                    "repair_duration": "repair",
                    "identity": "machine_id+occurrence_index",
                },
                "pm_runtime_schema_version": PM_RUNTIME_SCHEMA_VERSION,
                "pm_random_streams": {
                    "interval": "pm_interval",
                    "duration": "pm_duration",
                    "identity": "pm_id+occurrence_index",
                },
                "transport_runtime_schema_version": (
                    TRANSPORT_RUNTIME_SCHEMA_VERSION
                ),
                "transport_model": {
                    "resource_capacity": "external_unbounded",
                    "initial_leg": "none",
                    "missing_pair": "zero_duration_audited",
                },
                "transport_random_streams": {
                    "duration": TRANSPORT_STREAM,
                    "identity": (
                        "lot_id+route_id+from_step+to_step+"
                        "from_location+to_location+visit_index"
                    ),
                },
                "sampling_runtime": {
                    "schema_version": SAMPLING_RUNTIME_SCHEMA_VERSION,
                    "id": SAMPLING_RUNTIME_ID,
                    "supported_boundary": {
                        "decision_point": "operation_entry_before_dispatch_and_transport",
                        "scope": "per_lot",
                        "explicit_percent_range": "(0,100]",
                        "unconfigured": "always_perform_without_decision",
                        "percent_100": "perform_without_random_draw",
                        "rework_visits": "unsupported_visit_0_only",
                    },
                },
                "sampling_runtime_schema_version": SAMPLING_RUNTIME_SCHEMA_VERSION,
                "sampling_random_streams": {
                    "decision": SAMPLING_STREAM,
                    "identity": "lot_id+route_id+step_id+visit_index",
                    "draw": "uniform(0,100), draw<=sample_percent",
                },
                "release_runtime": {
                    "schema_version": RELEASE_RUNTIME_SCHEMA_VERSION,
                    "id": RELEASE_RUNTIME_ID,
                    "randomness": "none_for_constant_interval_profile",
                    "supported_boundary": {
                        "termination_mode": "fixed_horizon",
                        "interval_kind": "constant",
                        "interval_unit": "normalized_minutes",
                        "lots_per_repeat": 1,
                        "lazy_occurrence_materialization": True,
                        "horizon_is_closed": True,
                    },
                },
            },
            dispatch_policy=self.policy.name,
            dispatch_policy_id=getattr(
                self.policy,
                "policy_id",
                self.policy.name.lower(),
            ),
            policy_parameters=dict(self.policy_parameters),
            termination_condition=self.scenario.termination_mode,
            horizon=self.scenario.horizon,
        )
        completion_times = {
            lot_id: runtime.completion_time
            for lot_id, runtime in self._lots.items()
            if runtime.completion_time is not None
        }
        active_batches = tuple(
            ActiveBatchSnapshot(
                batch_id=batch.batch_id,
                machine_id=batch.machine_id,
                member_lot_ids=batch.member_lot_ids,
                member_wafers=batch.member_wafers,
                total_wafers=batch.total_wafers,
                start=batch.start_time,
                scheduled_finish=batch.scheduled_finish_time,
                start_reason=batch.start_reason,
                remaining_processing=(
                    self._machines[batch.machine_id].interrupted_activity.remaining_duration
                    if self._machines[batch.machine_id].interrupted_activity is not None
                    else (
                        max(0.0, batch.scheduled_finish_time - end_time)
                        if batch.scheduled_finish_time is not None
                        else None
                    )
                ),
            )
            for batch in sorted(
                self._active_batches.values(),
                key=lambda item: item.batch_id,
            )
        )
        return SimulationResult(
            provenance=provenance,
            metrics=metrics,
            trace=tuple(self._trace),
            processing_intervals=tuple(self._processing_intervals),
            setup_intervals=tuple(self._setup_intervals),
            batch_intervals=tuple(self._batch_intervals),
            active_batches=active_batches,
            machine_statistics=machine_statistics,
            completion_times=completion_times,
            cqt_records=self._cqt_runtime.records,
            open_cqt_clocks=open_cqt_clocks,
            cqt_metrics=cqt_metrics,
            dedication_records=self._dedication_runtime.released_records,
            active_dedication_bindings=(
                self._dedication_runtime.active_bindings
            ),
            initial_wip_dedication_audits=(
                self._dedication_runtime.initial_wip_audits
            ),
            dedication_metrics=dedication_metrics,
            downtime_intervals=tuple(self._downtime_intervals),
            machine_failure_snapshots=self._build_failure_snapshots(end_time),
            failure_count=sum(machine.failure_count for machine in self._machines.values()),
            total_downtime=sum(item.downtime for item in machine_statistics.values()),
            total_failure_downtime=sum(
                item.failure_downtime for item in machine_statistics.values()
            ),
            pm_intervals=tuple(self._pm_intervals),
            wafer_pm_states=self._pm_runtime.snapshots,
            pm_count=sum(machine.pm_count for machine in self._machines.values()),
            total_pm_downtime=sum(item.pm_downtime for item in machine_statistics.values()),
            random_sample_ledger=self.random_streams.ledger,
            transport_intervals=tuple(self._transport_intervals),
            active_transports=active_transports,
            transport_metrics=transport_metrics,
        )

    def query_cqt_state(
        self,
        *,
        lot_id: str,
        constraint_id: str,
        at_time: float | None = None,
        visit_index: int = 0,
    ) -> CQTClockState:
        """为后续策略特征提供只读 slack/risk，不改变 FIFO。"""

        return self._cqt_runtime.query(
            lot_id=lot_id,
            constraint_id=constraint_id,
            visit_index=visit_index,
            at_time=self.current_time if at_time is None else at_time,
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

    def _activate_until(
        self,
        machine: _MachineRuntime,
        finish_time: float,
    ) -> int:
        if finish_time <= self.current_time:
            raise SimulationError("活动完成时刻必须晚于当前时刻")
        machine.activity_token += 1
        machine.scheduled_activity_finish = finish_time
        return machine.activity_token

    def _schedule_failure(self, occurrence: FailureOccurrence) -> None:
        self._schedule(
            time=occurrence.failure_time,
            event_type=EventType.FAILURE_START,
            entity_id=occurrence.machine_id,
            payload={
                "machine_id": occurrence.machine_id,
                "occurrence_index": occurrence.occurrence_index,
                "repair_duration": occurrence.repair_duration,
                "model_type": occurrence.model_type,
            },
        )

    def _schedule_pm(self, occurrence: PMOccurrence) -> None:
        self._schedule(
            time=occurrence.start_time,
            event_type=EventType.PM_START,
            entity_id=occurrence.machine_id,
            payload={
                "pm_id": occurrence.pm_id,
                "machine_id": occurrence.machine_id,
                "occurrence_index": occurrence.occurrence_index,
                "duration": occurrence.duration,
                "trigger_type": occurrence.trigger_type,
                "model_type": occurrence.model_type,
            },
        )

    def _schedule_release_occurrence(
        self,
        template: ReleaseTemplateSpec,
        *,
        repeat_index: int,
    ) -> None:
        """安排一个具体模板 occurrence；调用方负责 cap/horizon 边界。"""

        member_index = 0
        self._schedule(
            time=release_time(template, repeat_index),
            event_type=EventType.LOT_RELEASE,
            entity_id=release_lot_id(
                template.template_id,
                template.lot_prefix,
                repeat_index,
                member_index,
            ),
            payload={
                "lot_id": release_lot_id(
                    template.template_id,
                    template.lot_prefix,
                    repeat_index,
                    member_index,
                ),
                "template_id": template.template_id,
                "repeat_index": repeat_index,
                "member_index": member_index,
            },
        )

    def _handle(self, event: Event) -> None:
        if event.event_type is EventType.LOT_RELEASE:
            self._handle_release(event)
        elif event.event_type is EventType.PROCESS_FINISH:
            self._handle_process_finish(event)
        elif event.event_type is EventType.BATCH_FINISH:
            self._handle_batch_finish(event)
        elif event.event_type is EventType.SETUP_FINISH:
            self._handle_setup_finish(event)
        elif event.event_type is EventType.BATCH_TIMEOUT:
            self._handle_batch_timeout(event)
        elif event.event_type is EventType.DISPATCH_BARRIER:
            self._handle_dispatch_barrier(event)
        elif event.event_type is EventType.FAILURE_START:
            self._handle_failure_start(event)
        elif event.event_type is EventType.REPAIR_FINISH:
            self._handle_repair_finish(event)
        elif event.event_type is EventType.PM_START:
            self._handle_pm_start(event)
        elif event.event_type is EventType.PM_FINISH:
            self._handle_pm_finish(event)
        elif event.event_type is EventType.TRANSPORT_ARRIVE:
            self._handle_transport_arrive(event)
        else:
            raise SimulationError(f"尚未实现事件类型：{event.event_type}")

    def _handle_release(self, event: Event) -> None:
        template_id = event.payload.get("template_id")
        if template_id is not None:
            template = self._release_templates[template_id]
            repeat_index = event.payload["repeat_index"]
            member_index = event.payload["member_index"]
            lot_spec = materialize_release_lot(
                template,
                repeat_index=repeat_index,
                member_index=member_index,
            )
            if lot_spec.lot_id in self._lots:
                raise SimulationError(f"release lot 重复创建：{lot_spec.lot_id}")
            self._lots[lot_spec.lot_id] = _LotRuntime(spec=lot_spec)
            # 只在当前 occurrence 已经成为真实 release 事件后安排下一次。
            next_index = repeat_index + 1
            horizon = self.scenario.horizon
            if (
                next_index < template.repeat_limit
                and horizon is not None
                and release_time(template, next_index) <= horizon
            ):
                self._schedule_release_occurrence(
                    template,
                    repeat_index=next_index,
                )
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
        if lot.spec.is_initial_wip:
            audits = self._dedication_runtime.register_initial_wip(
                lot_id=lot.spec.lot_id,
                route_id=operation.route_id,
                current_step_id=operation.step_id,
                recorded_at=self.current_time,
                visit_index=0,
            )
            for audit in audits:
                self._record(
                    event_type="DEDICATION_HISTORY_UNKNOWN",
                    priority=event.priority,
                    cause_event_seq=event.seq,
                    lot=lot,
                    operation=operation,
                    dedication_id=audit.dedication_id,
                    dedication_source_step_id=audit.source_step_id,
                    dedication_target_step_id=audit.target_step_id,
                    dedication_audit_reason=audit.reason,
                    state_before="HISTORICAL_BINDING:UNKNOWN",
                    state_after="QUALIFICATION_ONLY",
                )
        self._enter_after_release(event=event, lot=lot)
        self._ensure_dispatch_barrier()

    def _record_sampling_decision(
        self,
        *,
        lot: _LotRuntime,
        operation: Any,
        decision: Any,
        priority: int,
        cause_event_seq: int,
    ) -> None:
        self._record(
            event_type="SAMPLING_DECISION",
            priority=priority,
            cause_event_seq=cause_event_seq,
            lot=lot,
            operation=operation,
            sampling_percent=decision.sampling_percent,
            sampling_draw=decision.draw,
            sampling_performed=decision.performed,
            sampling_entity_id=decision.entity_id,
            state_before="OPERATION:ENTRY",
            state_after=(
                "SAMPLING:PERFORM" if decision.performed else "SAMPLING:SKIP"
            ),
        )

    def _enter_after_release(self, *, event: Event, lot: _LotRuntime) -> None:
        """在首工序 release 后判定 sampling，连续跳过仍只停留在当前时刻。"""

        while lot.operation_index < len(lot.spec.operations):
            operation = lot.spec.operations[lot.operation_index]
            decision = decide_sampling(
                operation,
                lot_id=lot.spec.lot_id,
                visit_index=0,
                random_source=self.random_streams,
            )
            if decision is None:
                return
            self._record_sampling_decision(
                lot=lot,
                operation=operation,
                decision=decision,
                priority=event.priority,
                cause_event_seq=event.seq,
            )
            if decision.performed:
                return
            self._record(
                event_type="OPERATION_SKIPPED",
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                operation=operation,
                sampling_percent=decision.sampling_percent,
                sampling_draw=decision.draw,
                sampling_performed=False,
                sampling_entity_id=decision.entity_id,
                state_before="OPERATION:ENTRY",
                state_after="OPERATION:SKIPPED",
            )
            lot.operation_index += 1
        lot.status = LotStatus.COMPLETED
        lot.queue_entered_at = None
        lot.completion_time = self.current_time
        self._record(
            event_type="LOT_COMPLETE",
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=lot.spec.operations[-1],
            state_before="ALL_OPERATIONS:SKIPPED",
            state_after=LotStatus.COMPLETED.value,
        )

    def _handle_transport_arrive(self, event: Event) -> None:
        lot = self._lots[event.entity_id]
        operation_index = event.payload["operation_index"]
        if (
            lot.status is not LotStatus.TRANSPORTING
            or lot.operation_index != operation_index
        ):
            self._record(
                event_type="TRANSPORT_ARRIVE_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                operation=lot.spec.operations[operation_index],
                state_before="STALE_TRANSPORT",
                state_after="NO_EFFECT",
                transport_from_location=event.payload["from_location"],
                transport_to_location=event.payload["to_location"],
                transport_duration=event.payload["duration"],
                transport_missing_pair=False,
            )
            return
        operation = lot.spec.operations[operation_index]
        active = self._active_transports.pop(lot.spec.lot_id, None)
        if active is None:
            raise SimulationError(
                f"TRANSPORT_ARRIVE 缺少 active transport：{lot.spec.lot_id}"
            )
        self._transport_intervals.append(
            TransportInterval(
                active.lot_id,
                active.route_id,
                active.from_step_id,
                active.to_step_id,
                active.from_location,
                active.to_location,
                active.start,
                self.current_time,
                active.duration,
                False,
            )
        )
        lot.status = LotStatus.QUEUED
        lot.queue_entered_at = self.current_time
        self._record(
            event_type="TRANSPORT_ARRIVE",
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=operation,
            state_before=LotStatus.TRANSPORTING.value,
            state_after=LotStatus.QUEUED.value,
            transport_from_location=event.payload["from_location"],
            transport_to_location=event.payload["to_location"],
            transport_duration=event.payload["duration"],
            transport_missing_pair=False,
        )
        self._record(
            event_type="ROUTE_ADVANCE",
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=operation,
            state_before=LotStatus.TRANSPORTING.value,
            state_after=LotStatus.QUEUED.value,
            transport_from_location=event.payload["from_location"],
            transport_to_location=event.payload["to_location"],
            transport_duration=event.payload["duration"],
            transport_missing_pair=False,
        )
        self._ensure_dispatch_barrier()

    def _handle_failure_start(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        machine = self._machines[machine_id]
        occurrence = FailureOccurrence(
            machine_id=machine_id,
            occurrence_index=event.payload["occurrence_index"],
            failure_time=event.time,
            repair_duration=event.payload["repair_duration"],
            model_type=event.payload["model_type"],
        )
        if machine.availability is MachineAvailability.DOWN:
            if (
                machine.downtime_cause
                in {DowntimeCause.CALENDAR_PM, DowntimeCause.WAFER_PM}
                and occurrence.model_type == "stochastic"
            ):
                self._failure_suppressed_by_pm[machine_id] = occurrence
            self._record(
                event_type="FAILURE_START_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                machine_id=machine_id,
                failure_occurrence_index=occurrence.occurrence_index,
                failure_model_type=occurrence.model_type,
                repair_duration=occurrence.repair_duration,
                state_before="MACHINE:DOWN",
                state_after="NO_EFFECT",
            )
            return

        interrupted = self._suspend_activity(
            event=event,
            machine=machine,
            downtime_cause=DowntimeCause.FAILURE,
            failure_occurrence=occurrence,
        )

        machine.availability = MachineAvailability.DOWN
        machine.downtime_started_at = self.current_time
        machine.repair_ends_at = self.current_time + occurrence.repair_duration
        machine.failure_count += 1
        machine.downtime_cause = DowntimeCause.FAILURE
        machine.downtime_id = f"FAILURE:{occurrence.occurrence_index}"
        machine.downtime_ends_at = machine.repair_ends_at
        self._active_failure_occurrence[machine_id] = occurrence
        self._record(
            event_type="FAILURE_START",
            priority=event.priority,
            cause_event_seq=event.seq,
            machine_id=machine_id,
            batch_id=machine.active_batch_id,
            failure_occurrence_index=occurrence.occurrence_index,
            failure_model_type=occurrence.model_type,
            interrupted_activity_kind=(interrupted.kind.value if interrupted else None),
            remaining_duration=(interrupted.remaining_duration if interrupted else None),
            repair_duration=occurrence.repair_duration,
            activity_token=machine.activity_token,
            downtime_cause=DowntimeCause.FAILURE.value,
            state_before="AVAILABILITY:UP",
            state_after="AVAILABILITY:DOWN",
        )
        self._schedule(
            time=machine.repair_ends_at,
            event_type=EventType.REPAIR_FINISH,
            entity_id=machine_id,
            payload={
                "machine_id": machine_id,
                "occurrence_index": occurrence.occurrence_index,
                "model_type": occurrence.model_type,
            },
        )

    def _handle_repair_finish(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        machine = self._machines[machine_id]
        occurrence = self._active_failure_occurrence.get(machine_id)
        if (
            machine.availability is not MachineAvailability.DOWN
            or occurrence is None
            or occurrence.occurrence_index != event.payload["occurrence_index"]
        ):
            self._record(
                event_type="REPAIR_FINISH_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                machine_id=machine_id,
                failure_occurrence_index=event.payload["occurrence_index"],
                state_before="STALE_REPAIR",
                state_after="NO_EFFECT",
            )
            return
        if machine.downtime_started_at is None:
            raise SimulationError("维修完成时缺少 downtime 起点")
        self._downtime_intervals.append(
            DowntimeInterval(
                machine_id=machine_id,
                occurrence_index=occurrence.occurrence_index,
                start=machine.downtime_started_at,
                finish=self.current_time,
            )
        )
        interrupted = machine.interrupted_activity
        machine.availability = MachineAvailability.UP
        machine.downtime_started_at = None
        machine.repair_ends_at = None
        machine.downtime_ends_at = None
        machine.downtime_cause = None
        machine.downtime_id = None
        self._active_failure_occurrence.pop(machine_id)
        self._record(
            event_type="REPAIR_COMPLETE",
            priority=event.priority,
            cause_event_seq=event.seq,
            machine_id=machine_id,
            batch_id=machine.active_batch_id,
            failure_occurrence_index=occurrence.occurrence_index,
            failure_model_type=occurrence.model_type,
            interrupted_activity_kind=(interrupted.kind.value if interrupted else None),
            remaining_duration=(interrupted.remaining_duration if interrupted else None),
            repair_duration=occurrence.repair_duration,
            state_before="AVAILABILITY:DOWN",
            state_after="AVAILABILITY:UP",
        )
        next_occurrence = self._failure_schedule.next_after_repair(
            machine_id=machine_id,
            occurrence_index=occurrence.occurrence_index,
            repaired_at=self.current_time,
        )
        if next_occurrence is not None:
            self._schedule_failure(next_occurrence)
        if interrupted is None:
            if self._schedule_pending_wafer_pm(machine_id):
                return
            self._ensure_dispatch_barrier()
            return
        self._resume_activity(event=event, machine=machine)

    def _handle_pm_start(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        machine = self._machines[machine_id]
        occurrence = PMOccurrence(
            pm_id=event.payload["pm_id"],
            machine_id=machine_id,
            occurrence_index=event.payload["occurrence_index"],
            start_time=event.time,
            duration=event.payload["duration"],
            trigger_type=event.payload["trigger_type"],
            model_type=event.payload["model_type"],
        )
        if occurrence.trigger_type == DowntimeCause.CALENDAR_PM.value:
            next_occurrence = self._pm_runtime.next_calendar_occurrence(occurrence)
            if next_occurrence is not None:
                self._schedule_pm(next_occurrence)
        if machine.availability is MachineAvailability.DOWN:
            event_type = (
                "PM_START_DEFERRED"
                if occurrence.trigger_type == DowntimeCause.WAFER_PM.value
                else "PM_START_STALE"
            )
            self._record(
                event_type=event_type,
                priority=event.priority,
                cause_event_seq=event.seq,
                machine_id=machine_id,
                downtime_cause=machine.downtime_cause.value if machine.downtime_cause else None,
                pm_id=occurrence.pm_id,
                pm_trigger_type=occurrence.trigger_type,
                pm_occurrence_index=occurrence.occurrence_index,
                pm_duration=occurrence.duration,
                state_before="MACHINE:DOWN",
                state_after=("PM:PENDING" if event_type.endswith("DEFERRED") else "NO_EFFECT"),
            )
            return

        wafer_due: WaferPMDue | None = None
        cause = DowntimeCause(occurrence.trigger_type)
        if cause is DowntimeCause.WAFER_PM:
            wafer_due = self._pm_runtime.start_wafer_pm(
                machine_id,
                occurrence.occurrence_index,
            )
        interrupted = self._suspend_activity(
            event=event,
            machine=machine,
            downtime_cause=cause,
            pm_occurrence=occurrence,
        )
        machine.availability = MachineAvailability.DOWN
        machine.downtime_started_at = self.current_time
        machine.downtime_cause = cause
        machine.downtime_id = f"{occurrence.pm_id}:{occurrence.occurrence_index}"
        machine.downtime_ends_at = self.current_time + occurrence.duration
        machine.pm_count += 1
        self._active_pm_occurrence[machine_id] = occurrence
        self._record(
            event_type="PM_START",
            priority=event.priority,
            cause_event_seq=event.seq,
            machine_id=machine_id,
            batch_id=machine.active_batch_id,
            downtime_cause=cause.value,
            pm_id=occurrence.pm_id,
            pm_trigger_type=occurrence.trigger_type,
            pm_occurrence_index=occurrence.occurrence_index,
            pm_duration=occurrence.duration,
            wafer_counter_before=(wafer_due.counter_before if wafer_due else None),
            wafer_counter_after=(wafer_due.counter_after if wafer_due else None),
            wafer_threshold=(wafer_due.threshold_wafers if wafer_due else None),
            processed_wafers=(wafer_due.processed_wafers if wafer_due else None),
            interrupted_activity_kind=(interrupted.kind.value if interrupted else None),
            remaining_duration=(interrupted.remaining_duration if interrupted else None),
            activity_token=machine.activity_token,
            state_before="AVAILABILITY:UP",
            state_after="AVAILABILITY:DOWN",
        )
        self._schedule(
            time=machine.downtime_ends_at,
            event_type=EventType.PM_FINISH,
            entity_id=machine_id,
            payload={
                "pm_id": occurrence.pm_id,
                "machine_id": machine_id,
                "occurrence_index": occurrence.occurrence_index,
                "duration": occurrence.duration,
                "trigger_type": occurrence.trigger_type,
                "model_type": occurrence.model_type,
            },
        )

    def _handle_pm_finish(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        machine = self._machines[machine_id]
        occurrence = self._active_pm_occurrence.get(machine_id)
        if (
            machine.availability is not MachineAvailability.DOWN
            or machine.downtime_cause not in {DowntimeCause.CALENDAR_PM, DowntimeCause.WAFER_PM}
            or occurrence is None
            or occurrence.pm_id != event.payload["pm_id"]
            or occurrence.occurrence_index != event.payload["occurrence_index"]
        ):
            self._record(
                event_type="PM_FINISH_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                machine_id=machine_id,
                pm_id=event.payload["pm_id"],
                pm_occurrence_index=event.payload["occurrence_index"],
                state_before="STALE_PM_FINISH",
                state_after="NO_EFFECT",
            )
            return
        if machine.downtime_started_at is None:
            raise SimulationError("PM 完成时缺少 downtime 起点")
        self._pm_intervals.append(
            PMInterval(
                pm_id=occurrence.pm_id,
                machine_id=machine_id,
                occurrence_index=occurrence.occurrence_index,
                trigger_type=occurrence.trigger_type,
                start=machine.downtime_started_at,
                finish=self.current_time,
            )
        )
        interrupted = machine.interrupted_activity
        wafer_due = None
        if occurrence.trigger_type == DowntimeCause.WAFER_PM.value:
            wafer_due = self._pm_runtime.finish_wafer_pm(
                machine_id,
                occurrence.occurrence_index,
            )
        machine.availability = MachineAvailability.UP
        machine.downtime_started_at = None
        machine.downtime_cause = None
        machine.downtime_id = None
        machine.downtime_ends_at = None
        self._active_pm_occurrence.pop(machine_id)
        suppressed_failure = self._failure_suppressed_by_pm.pop(
            machine_id,
            None,
        )
        self._record(
            event_type="PM_FINISH",
            priority=event.priority,
            cause_event_seq=event.seq,
            machine_id=machine_id,
            batch_id=machine.active_batch_id,
            downtime_cause=occurrence.trigger_type,
            pm_id=occurrence.pm_id,
            pm_trigger_type=occurrence.trigger_type,
            pm_occurrence_index=occurrence.occurrence_index,
            pm_duration=occurrence.duration,
            wafer_counter_before=(wafer_due.counter_after if wafer_due else None),
            wafer_counter_after=(0 if wafer_due else None),
            wafer_threshold=(wafer_due.threshold_wafers if wafer_due else None),
            interrupted_activity_kind=(interrupted.kind.value if interrupted else None),
            remaining_duration=(interrupted.remaining_duration if interrupted else None),
            state_before="AVAILABILITY:DOWN",
            state_after="AVAILABILITY:UP",
        )
        if suppressed_failure is not None:
            next_failure = self._failure_schedule.next_after_repair(
                machine_id=machine_id,
                occurrence_index=suppressed_failure.occurrence_index,
                repaired_at=self.current_time,
            )
            if next_failure is not None:
                self._schedule_failure(next_failure)
        if interrupted is not None:
            self._resume_activity(event=event, machine=machine)
            return
        if self._schedule_pending_wafer_pm(machine_id):
            return
        self._ensure_dispatch_barrier()

    def _schedule_pending_wafer_pm(self, machine_id: str) -> bool:
        due = self._pm_runtime.pending_for_machine(machine_id)
        if due is None:
            return False
        self._schedule_pm(
            PMOccurrence(
                pm_id=due.pm_id,
                machine_id=machine_id,
                occurrence_index=due.occurrence_index,
                start_time=self.current_time,
                duration=due.duration,
                trigger_type=DowntimeCause.WAFER_PM.value,
                model_type="wafer_threshold",
            )
        )
        return True

    def _suspend_activity(
        self,
        *,
        event: Event,
        machine: _MachineRuntime,
        downtime_cause: DowntimeCause,
        failure_occurrence: FailureOccurrence | None = None,
        pm_occurrence: PMOccurrence | None = None,
    ) -> _InterruptedActivity | None:
        if machine.status is MachineStatus.IDLE:
            return None
        finish = machine.scheduled_activity_finish
        if finish is None or finish <= self.current_time:
            raise SimulationError("停机时活动缺少有效计划完成时刻")
        remaining = finish - self.current_time
        if machine.status is MachineStatus.SETTING_UP:
            kind = ActivityKind.SETUP
            self._append_setup_segment(machine, self.current_time)
            suspend_event = "SETUP_SUSPEND"
        elif machine.active_batch_id is not None:
            kind = ActivityKind.BATCH
            batch = self._active_batches[machine.active_batch_id]
            if machine.processing_started_at is None:
                raise SimulationError("Batch 停机时缺少活动段起点")
            batch.accumulated_processing_time += self.current_time - machine.processing_started_at
            batch.scheduled_finish_time = None
            suspend_event = "BATCH_SUSPEND"
        else:
            kind = ActivityKind.PROCESS
            self._append_processing_segment(machine, self.current_time)
            suspend_event = "PROCESS_SUSPEND"
        interrupted = _InterruptedActivity(kind, remaining, self.current_time)
        machine.interrupted_activity = interrupted
        machine.activity_token += 1
        machine.scheduled_activity_finish = None
        machine.processing_started_at = None
        machine.setup_started_at = None
        self._record(
            event_type=suspend_event,
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=(self._lots[machine.lot_id] if machine.lot_id else None),
            operation=self._machine_operation(machine),
            machine_id=machine.machine_id,
            batch_id=machine.active_batch_id,
            failure_occurrence_index=(failure_occurrence.occurrence_index if failure_occurrence else None),
            failure_model_type=(failure_occurrence.model_type if failure_occurrence else None),
            downtime_cause=downtime_cause.value,
            pm_id=(pm_occurrence.pm_id if pm_occurrence else None),
            pm_trigger_type=(pm_occurrence.trigger_type if pm_occurrence else None),
            pm_occurrence_index=(pm_occurrence.occurrence_index if pm_occurrence else None),
            pm_duration=(pm_occurrence.duration if pm_occurrence else None),
            interrupted_activity_kind=kind.value,
            remaining_duration=remaining,
            activity_token=machine.activity_token,
            state_before=f"{kind.value}:ACTIVE",
            state_after=f"{kind.value}:SUSPENDED",
        )
        return interrupted

    def _resume_activity(self, *, event: Event, machine: _MachineRuntime) -> None:
        interrupted = machine.interrupted_activity
        if interrupted is None:
            raise SimulationError("恢复活动时缺少 interrupted activity")
        machine.interrupted_activity = None
        finish_time = self.current_time + interrupted.remaining_duration
        token = self._activate_until(machine, finish_time)
        if interrupted.kind is ActivityKind.SETUP:
            machine.setup_started_at = self.current_time
            lot = self._lots[machine.lot_id]
            self._record_resume(event, machine, lot, "SETUP_RESUME", interrupted, token)
            self._schedule(
                time=finish_time,
                event_type=EventType.SETUP_FINISH,
                entity_id=machine.machine_id,
                payload={"machine_id": machine.machine_id, "lot_id": machine.lot_id, "operation_index": machine.operation_index, "from_setup": machine.setup_from, "to_setup": machine.setup_to, "activity_token": token},
            )
        elif interrupted.kind is ActivityKind.PROCESS:
            machine.processing_started_at = self.current_time
            lot = self._lots[machine.lot_id]
            self._record_resume(event, machine, lot, "PROCESS_RESUME", interrupted, token)
            self._schedule(
                time=finish_time,
                event_type=EventType.PROCESS_FINISH,
                entity_id=machine.machine_id,
                payload={"machine_id": machine.machine_id, "lot_id": machine.lot_id, "operation_index": machine.operation_index, "activity_token": token},
            )
        else:
            batch_id = machine.active_batch_id
            if batch_id is None:
                raise SimulationError("恢复 Batch 时缺少 batch identity")
            machine.processing_started_at = self.current_time
            self._active_batches[batch_id].scheduled_finish_time = finish_time
            self._record_resume(event, machine, None, "BATCH_RESUME", interrupted, token)
            self._schedule(time=finish_time, event_type=EventType.BATCH_FINISH, entity_id=batch_id, payload={"batch_id": batch_id, "activity_token": token})

    def _record_resume(
        self,
        event: Event,
        machine: _MachineRuntime,
        lot: _LotRuntime | None,
        event_type: str,
        interrupted: _InterruptedActivity,
        token: int,
    ) -> None:
        self._record(
            event_type=event_type,
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=self._machine_operation(machine),
            machine_id=machine.machine_id,
            batch_id=machine.active_batch_id,
            failure_occurrence_index=(event.payload["occurrence_index"] if event.event_type is EventType.REPAIR_FINISH else None),
            failure_model_type=(event.payload["model_type"] if event.event_type is EventType.REPAIR_FINISH else None),
            downtime_cause=(DowntimeCause.FAILURE.value if event.event_type is EventType.REPAIR_FINISH else event.payload["trigger_type"]),
            pm_id=(event.payload.get("pm_id") if event.event_type is EventType.PM_FINISH else None),
            pm_trigger_type=(event.payload.get("trigger_type") if event.event_type is EventType.PM_FINISH else None),
            pm_occurrence_index=(event.payload["occurrence_index"] if event.event_type is EventType.PM_FINISH else None),
            pm_duration=(event.payload.get("duration") if event.event_type is EventType.PM_FINISH else None),
            interrupted_activity_kind=interrupted.kind.value,
            remaining_duration=interrupted.remaining_duration,
            activity_token=token,
            state_before=f"{interrupted.kind.value}:SUSPENDED",
            state_after=f"{interrupted.kind.value}:ACTIVE",
        )

    def _machine_operation(self, machine: _MachineRuntime) -> Any | None:
        if machine.active_batch_id is not None:
            batch = self._active_batches[machine.active_batch_id]
            lot = self._lots[batch.member_lot_ids[0]]
            return lot.spec.operations[batch.operation_indices[0]]
        if machine.lot_id is None or machine.operation_index is None:
            return None
        return self._lots[machine.lot_id].spec.operations[machine.operation_index]

    def _append_processing_segment(self, machine: _MachineRuntime, finish: float) -> None:
        if machine.lot_id is None or machine.processing_started_at is None:
            raise SimulationError("加工活动段缺少 lot 或开始时刻")
        lot = self._lots[machine.lot_id]
        operation = lot.spec.operations[lot.operation_index]
        self._processing_intervals.append(
            ProcessingInterval(lot.spec.lot_id, machine.machine_id, operation.route_id, operation.step_id, machine.processing_started_at, finish)
        )

    def _append_setup_segment(self, machine: _MachineRuntime, finish: float) -> None:
        if machine.lot_id is None or machine.setup_started_at is None or machine.setup_from is None or machine.setup_to is None:
            raise SimulationError("Setup 活动段缺少上下文")
        lot = self._lots[machine.lot_id]
        operation = lot.spec.operations[lot.operation_index]
        self._setup_intervals.append(
            SetupInterval(lot.spec.lot_id, machine.machine_id, operation.route_id, operation.step_id, machine.setup_from, machine.setup_to, machine.setup_started_at, finish)
        )

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
            if (
                machine.availability is MachineAvailability.DOWN
                or machine.status is not MachineStatus.IDLE
            ):
                continue
            actions, batch_decisions = self._dispatch_options(machine_id)
            action = self.policy.select(
                DispatchState(current_time=self.current_time),
                actions,
            )
            if action is None:
                continue
            if action not in actions:
                raise SimulationError("策略返回了不可行动作")
            batch_decision = batch_decisions.get(action.action_id)
            if batch_decision is not None:
                self._start_batch(event, machine_id, batch_decision)
            else:
                self._commit_dispatch(event, action)

    def _eligible_actions(self, machine_id: str) -> list[DispatchAction]:
        if self._machines[machine_id].availability is MachineAvailability.DOWN:
            return []
        actions = []
        for lot_id in sorted(self._lots):
            lot = self._lots[lot_id]
            if lot.status is not LotStatus.QUEUED:
                continue
            operation = lot.spec.operations[lot.operation_index]
            if machine_id not in operation.eligible_machines:
                continue
            if not self._dedication_runtime.allows_machine(
                lot_id=lot.spec.lot_id,
                route_id=operation.route_id,
                step_id=operation.step_id,
                machine_id=machine_id,
                visit_index=0,
            ):
                continue
            if lot.queue_entered_at is None:
                raise SimulationError(f"排队 lot 缺少入队时间：{lot_id}")
            actions.append(
                self._build_dispatch_action(
                    machine_id=machine_id,
                    member_lot_ids=(lot_id,),
                    action_type="ordinary",
                )
            )
        return actions

    def _build_dispatch_action(
        self,
        *,
        machine_id: str,
        member_lot_ids: tuple[str, ...],
        action_type: str,
    ) -> DispatchAction:
        """只从 engine runtime 构造 policy 可见快照，不改变任何状态。"""

        if not member_lot_ids:
            raise SimulationError("DispatchAction member 不能为空")
        members = tuple(self._lots[lot_id] for lot_id in member_lot_ids)
        representative = members[0]
        operation = representative.spec.operations[
            representative.operation_index
        ]
        for member in members:
            member_operation = member.spec.operations[member.operation_index]
            if (
                member_operation.route_id,
                member_operation.step_id,
                member_operation.processing_time,
            ) != (
                operation.route_id,
                operation.step_id,
                operation.processing_time,
            ):
                raise SimulationError("同一 DispatchAction 的 route/step/time 不一致")
            if member.queue_entered_at is None:
                raise SimulationError(
                    f"DispatchAction member 缺少 queue time：{member.spec.lot_id}"
                )

        if action_type not in {"ordinary", "batch"}:
            raise SimulationError(f"未知 DispatchAction 类型：{action_type}")
        action_kind = action_type
        member_token = ",".join(member_lot_ids)
        action_id = (
            f"{action_kind.upper()}|{machine_id}|{operation.route_id}|"
            f"{operation.step_id}|{member_token}"
        )
        return DispatchAction(
            action_id=action_id,
            lot_id=representative.spec.lot_id,
            machine_id=machine_id,
            action_type=action_kind,
            member_lot_ids=member_lot_ids,
            operation_index=representative.operation_index,
            route_id=operation.route_id,
            step_id=operation.step_id,
            queue_entered_at=min(
                member.queue_entered_at
                for member in members
                if member.queue_entered_at is not None
            ),
            release_time=min(member.spec.release_time for member in members),
            physical_processing_time=self._processing_resolver.nominal(
                operation,
                quantity_wafers=sum(member.spec.quantity_wafers for member in members),
            ),
            member_due_times=tuple(member.spec.due_time for member in members),
            member_remaining_nominal_processing_times=tuple(
                sum(
                    self._processing_resolver.nominal(
                        item,
                        quantity_wafers=member.spec.quantity_wafers,
                    )
                    for item in member.spec.operations[
                        member.operation_index :
                    ]
                )
                for member in members
            ),
        )

    def _dispatch_options(
        self,
        machine_id: str,
    ) -> tuple[list[DispatchAction], dict[str, BatchDecision]]:
        eligible = self._eligible_actions(machine_id)
        ordinary: list[DispatchAction] = []
        groups: dict[tuple[str, int], list[BatchCandidate]] = {}
        for action in eligible:
            lot = self._lots[action.lot_id]
            operation = lot.spec.operations[action.operation_index]
            if operation.batch_spec is None:
                ordinary.append(action)
                continue
            groups.setdefault(
                (operation.route_id, operation.step_id),
                [],
            ).append(
                BatchCandidate(
                    lot_id=lot.spec.lot_id,
                    quantity_wafers=lot.spec.quantity_wafers,
                    queue_entered_at=action.queue_entered_at,
                    route_id=operation.route_id,
                    step_id=operation.step_id,
                    operation_index=action.operation_index,
                    processing_time=operation.processing_time,
                    batch_spec=operation.batch_spec,
                )
            )

        decisions: dict[str, BatchDecision] = {}
        options = list(ordinary)
        for key in sorted(groups):
            decision = self._batch_formation.evaluate(
                current_time=self.current_time,
                candidates=tuple(groups[key]),
            )
            timeout_key = (machine_id, key[0], key[1])
            if decision.can_start:
                member_ids = tuple(
                    member.lot_id for member in decision.selected_members
                )
                batch_action = self._build_dispatch_action(
                    machine_id=machine_id,
                    member_lot_ids=member_ids,
                    action_type="batch",
                )
                options.append(batch_action)
                decisions[batch_action.action_id] = decision
            elif decision.timeout_at is not None:
                self._ensure_batch_timeout(
                    timeout_key=timeout_key,
                    timeout_at=decision.timeout_at,
                )
            else:
                self._pending_batch_timeouts.pop(timeout_key, None)
        return options, decisions

    def _ensure_batch_timeout(
        self,
        *,
        timeout_key: tuple[str, str, int],
        timeout_at: float,
    ) -> None:
        existing = self._pending_batch_timeouts.get(timeout_key)
        if existing is not None and existing[1] == timeout_at:
            return
        if timeout_at <= self.current_time:
            raise SimulationError("Batch timeout 必须安排在未来")
        token = self._next_batch_timeout_token
        self._next_batch_timeout_token += 1
        self._pending_batch_timeouts[timeout_key] = (token, timeout_at)
        machine_id, route_id, step_id = timeout_key
        self._schedule(
            time=timeout_at,
            event_type=EventType.BATCH_TIMEOUT,
            entity_id=machine_id,
            payload={
                "machine_id": machine_id,
                "route_id": route_id,
                "step_id": step_id,
                "token": token,
            },
        )

    def _handle_batch_timeout(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        route_id = event.payload["route_id"]
        step_id = event.payload["step_id"]
        token = event.payload["token"]
        timeout_key = (machine_id, route_id, step_id)
        pending = self._pending_batch_timeouts.get(timeout_key)
        if pending != (token, event.time):
            self._record(
                event_type="BATCH_TIMEOUT_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                machine_id=machine_id,
                route_id=route_id,
                step_id=step_id,
                state_before="STALE_TOKEN",
                state_after="NO_EFFECT",
            )
            return
        self._pending_batch_timeouts.pop(timeout_key)
        operation = next(
            (
                lot.spec.operations[lot.operation_index]
                for lot in self._lots.values()
                if lot.status is LotStatus.QUEUED
                and (
                    lot.spec.operations[lot.operation_index].route_id,
                    lot.spec.operations[lot.operation_index].step_id,
                )
                == (route_id, step_id)
                and machine_id
                in lot.spec.operations[
                    lot.operation_index
                ].eligible_machines
            ),
            None,
        )
        self._record(
            event_type="BATCH_TIMEOUT",
            priority=event.priority,
            cause_event_seq=event.seq,
            operation=operation,
            machine_id=machine_id,
            route_id=route_id,
            step_id=step_id,
            state_before="WAITING_FOR_TARGET",
            state_after="REEVALUATION_REQUESTED",
        )
        self._ensure_dispatch_barrier()

    def _start_batch(
        self,
        barrier_event: Event,
        machine_id: str,
        decision: BatchDecision,
    ) -> None:
        machine = self._machines[machine_id]
        if (
            machine.availability is MachineAvailability.DOWN
            or machine.status is not MachineStatus.IDLE
        ):
            raise SimulationError("Batch 提交时 machine 已不可用")
        if machine.active_batch_id is not None or machine.lot_id is not None:
            raise SimulationError("Batch 提交时 machine 已被占用")
        if not decision.can_start or not decision.selected_members:
            raise SimulationError("尝试提交不可启动的 batch decision")

        selected = decision.selected_members
        first_lot = self._lots[selected[0].lot_id]
        first_operation = first_lot.spec.operations[
            selected[0].operation_index
        ]
        spec = first_operation.batch_spec
        if spec is None:
            raise SimulationError("Batch decision 对应 operation 非 batch")
        if not (
            spec.minimum_wafers
            <= decision.selected_wafers
            <= spec.maximum_wafers
        ):
            raise SimulationError("Batch selected wafer 容量非法")

        member_lots: list[_LotRuntime] = []
        member_wafers: list[int] = []
        operation_indices: list[int] = []
        for candidate in selected:
            lot = self._lots[candidate.lot_id]
            if (
                lot.status is not LotStatus.QUEUED
                or lot.operation_index != candidate.operation_index
                or lot.queue_entered_at != candidate.queue_entered_at
            ):
                raise SimulationError(
                    f"Batch 原子提交时 lot 已不可用：{candidate.lot_id}"
                )
            operation = lot.spec.operations[lot.operation_index]
            if machine_id not in operation.eligible_machines:
                raise SimulationError("Batch member 不再满足设备资格")
            if not self._dedication_runtime.allows_machine(
                lot_id=lot.spec.lot_id,
                route_id=operation.route_id,
                step_id=operation.step_id,
                machine_id=machine_id,
                visit_index=0,
            ):
                raise SimulationError("Batch member 不再满足 Dedication")
            if (
                operation.route_id,
                operation.step_id,
            ) != decision.compatibility_key:
                raise SimulationError("Batch member compatibility 已失效")
            if operation.batch_spec != spec:
                raise SimulationError("Batch member 配置不一致")
            if (
                operation.required_setup is not None
                and operation.required_setup != machine.current_setup
            ):
                raise SimulationError(
                    "Batch+Setup 联合路径尚未验证；MC04 必须无换型"
                )
            member_lots.append(lot)
            member_wafers.append(lot.spec.quantity_wafers)
            operation_indices.append(lot.operation_index)

        batch_id = f"BATCH-{self._next_batch_seq:06d}"
        self._next_batch_seq += 1
        realization = self._processing_resolver.realize_batch(
            operation=first_operation,
            ordered_member_ids=tuple(lot.spec.lot_id for lot in member_lots),
            total_wafers=decision.selected_wafers,
            random_source=self.random_streams,
        )
        finish_time = self.current_time + realization.realized_minutes
        active = _ActiveBatch(
            batch_id=batch_id,
            machine_id=machine_id,
            member_lot_ids=tuple(lot.spec.lot_id for lot in member_lots),
            member_wafers=tuple(member_wafers),
            operation_indices=tuple(operation_indices),
            route_id=first_operation.route_id,
            step_id=first_operation.step_id,
            total_wafers=decision.selected_wafers,
            start_time=self.current_time,
            scheduled_finish_time=finish_time,
            start_reason=decision.start_reason,
        )
        timeout_key = (
            machine_id,
            first_operation.route_id,
            first_operation.step_id,
        )
        self._pending_batch_timeouts.pop(timeout_key, None)

        for lot in member_lots:
            lot.status = LotStatus.RESERVED
            lot.current_machine_id = machine_id
            lot.queue_entered_at = None
        machine.status = MachineStatus.PROCESSING
        machine.processing_started_at = self.current_time
        machine.active_batch_id = batch_id
        token = self._activate_until(machine, finish_time)
        self._active_batches[batch_id] = active

        batch_trace = {
            "batch_id": batch_id,
            "batch_member_lot_ids": active.member_lot_ids,
            "batch_member_wafers": active.member_wafers,
            "batch_total_wafers": active.total_wafers,
            "batch_start_reason": active.start_reason,
        }
        self._record(
            event_type="BATCH_FORMED",
            priority=barrier_event.priority,
            cause_event_seq=barrier_event.seq,
            operation=first_operation,
            machine_id=machine_id,
            state_before="COMPATIBLE_LOTS:QUEUED|MACHINE:IDLE",
            state_after="BATCH:RESERVED|MACHINE:RESERVED",
            **batch_trace,
        )
        for lot in member_lots:
            operation = lot.spec.operations[lot.operation_index]
            self._establish_dedication_bindings(
                lot=lot,
                operation=operation,
                machine_id=machine_id,
                priority=barrier_event.priority,
                cause_event_seq=barrier_event.seq,
                batch_id=batch_id,
            )
        self._record(
            event_type="BATCH_START",
            priority=barrier_event.priority,
            cause_event_seq=barrier_event.seq,
            operation=first_operation,
            machine_id=machine_id,
            state_before="BATCH:RESERVED|MACHINE:RESERVED",
            state_after="BATCH:PROCESSING|MACHINE:PROCESSING",
            **batch_trace,
        )
        for lot in member_lots:
            operation = lot.spec.operations[lot.operation_index]
            lot.status = LotStatus.PROCESSING
            self._record(
                event_type="PROCESS_START",
                priority=barrier_event.priority,
                cause_event_seq=barrier_event.seq,
                lot=lot,
                operation=operation,
                machine_id=machine_id,
                batch_id=batch_id,
                state_before="LOT:RESERVED|BATCH:RESERVED",
                state_after="LOT:PROCESSING|BATCH:PROCESSING",
            )
            self._close_cqt_clocks(
                lot=lot,
                operation=operation,
                priority=barrier_event.priority,
                cause_event_seq=barrier_event.seq,
                batch_id=batch_id,
            )
        self._schedule(
            time=finish_time,
            event_type=EventType.BATCH_FINISH,
            entity_id=batch_id,
            payload={"batch_id": batch_id, "activity_token": token},
        )

    def _commit_dispatch(
        self,
        barrier_event: Event,
        action: DispatchAction,
    ) -> None:
        lot = self._lots[action.lot_id]
        machine = self._machines[action.machine_id]
        if lot.status is not LotStatus.QUEUED:
            raise SimulationError("原子提交时 lot 已不可用")
        if (
            machine.availability is MachineAvailability.DOWN
            or machine.status is not MachineStatus.IDLE
        ):
            raise SimulationError("原子提交时 machine 已不可用")
        operation = lot.spec.operations[lot.operation_index]
        if operation.batch_spec is not None:
            raise SimulationError("Batch operation 必须通过原子组批路径提交")
        if machine.active_batch_id is not None:
            raise SimulationError("普通派工时 machine 仍绑定 active batch")
        lot.status = LotStatus.RESERVED
        lot.current_machine_id = machine.machine_id
        lot.queue_entered_at = None
        machine.lot_id = lot.spec.lot_id
        machine.operation_index = lot.operation_index
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
        self._establish_dedication_bindings(
            lot=lot,
            operation=operation,
            machine_id=machine.machine_id,
            priority=barrier_event.priority,
            cause_event_seq=barrier_event.seq,
        )
        setup_duration = self._setup_resolver.resolve(
            current_setup=machine.current_setup,
            operation=operation,
        )
        if setup_duration > 0:
            required_setup = operation.required_setup
            if required_setup is None:
                raise SimulationError("正 setup 时长缺少 required_setup")
            machine.status = MachineStatus.SETTING_UP
            machine.setup_started_at = self.current_time
            machine.setup_from = machine.current_setup
            machine.setup_to = required_setup
            setup_finish = self.current_time + setup_duration
            token = self._activate_until(machine, setup_finish)
            self._record(
                event_type="SETUP_START",
                priority=barrier_event.priority,
                cause_event_seq=barrier_event.seq,
                lot=lot,
                operation=operation,
                machine_id=machine.machine_id,
                state_before="LOT:RESERVED|MACHINE:RESERVED",
                state_after="LOT:RESERVED|MACHINE:SETTING_UP",
            )
            self._schedule(
                time=setup_finish,
                event_type=EventType.SETUP_FINISH,
                entity_id=machine.machine_id,
                payload={
                    "machine_id": machine.machine_id,
                    "lot_id": lot.spec.lot_id,
                    "operation_index": lot.operation_index,
                    "from_setup": machine.setup_from,
                    "to_setup": required_setup,
                    "activity_token": token,
                },
            )
            return
        self._begin_processing(
            cause_event_seq=barrier_event.seq,
            priority=barrier_event.priority,
            lot=lot,
            machine=machine,
        )

    def _begin_processing(
        self,
        *,
        cause_event_seq: int,
        priority: int,
        lot: _LotRuntime,
        machine: _MachineRuntime,
    ) -> None:
        if lot.status is not LotStatus.RESERVED:
            raise SimulationError("加工开始时 lot 未被保留")
        if machine.lot_id != lot.spec.lot_id:
            raise SimulationError("加工开始时 machine 未保留该 lot")
        operation = lot.spec.operations[lot.operation_index]
        lot.status = LotStatus.PROCESSING
        machine.status = MachineStatus.PROCESSING
        machine.processing_started_at = self.current_time
        realization = self._processing_resolver.realize_lot(
            operation=operation,
            lot_id=lot.spec.lot_id,
            quantity_wafers=lot.spec.quantity_wafers,
            visit_index=0,
            random_source=self.random_streams,
        )
        finish_time = self.current_time + realization.realized_minutes
        token = self._activate_until(machine, finish_time)
        self._record(
            event_type="PROCESS_START",
            priority=priority,
            cause_event_seq=cause_event_seq,
            lot=lot,
            operation=operation,
            machine_id=machine.machine_id,
            state_before="LOT:RESERVED|MACHINE:RESERVED",
            state_after="LOT:PROCESSING|MACHINE:PROCESSING",
        )
        self._close_cqt_clocks(
            lot=lot,
            operation=operation,
            priority=priority,
            cause_event_seq=cause_event_seq,
        )
        self._schedule(
            time=finish_time,
            event_type=EventType.PROCESS_FINISH,
            entity_id=machine.machine_id,
            payload={
                "machine_id": machine.machine_id,
                "lot_id": lot.spec.lot_id,
                "operation_index": lot.operation_index,
                "activity_token": token,
            },
        )

    def _handle_setup_finish(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        lot_id = event.payload["lot_id"]
        operation_index = event.payload["operation_index"]
        machine = self._machines[machine_id]
        lot = self._lots[lot_id]
        if event.payload["activity_token"] != machine.activity_token:
            self._record(
                event_type="SETUP_FINISH_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                operation=lot.spec.operations[operation_index],
                machine_id=machine_id,
                activity_token=event.payload["activity_token"],
                state_before="STALE_TOKEN",
                state_after="NO_EFFECT",
            )
            return
        if (
            machine.status is not MachineStatus.SETTING_UP
            or machine.lot_id != lot_id
            or machine.operation_index != operation_index
            or lot.status is not LotStatus.RESERVED
            or lot.operation_index != operation_index
            or machine.setup_started_at is None
            or machine.setup_from != event.payload["from_setup"]
            or machine.setup_to != event.payload["to_setup"]
        ):
            raise SimulationError(f"SETUP_FINISH 状态不一致：{event.payload}")
        operation = lot.spec.operations[operation_index]
        self._setup_intervals.append(
            SetupInterval(
                lot_id=lot_id,
                machine_id=machine_id,
                route_id=operation.route_id,
                step_id=operation.step_id,
                from_setup=machine.setup_from,
                to_setup=machine.setup_to,
                start=machine.setup_started_at,
                finish=self.current_time,
            )
        )
        self._record(
            event_type="SETUP_FINISH",
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=operation,
            machine_id=machine_id,
            state_before="LOT:RESERVED|MACHINE:SETTING_UP",
            state_after="LOT:RESERVED|MACHINE:READY",
        )
        machine.current_setup = machine.setup_to
        machine.status = MachineStatus.IDLE
        machine.setup_started_at = None
        machine.setup_from = None
        machine.setup_to = None
        machine.scheduled_activity_finish = None
        self._begin_processing(
            cause_event_seq=event.seq,
            priority=event.priority,
            lot=lot,
            machine=machine,
        )

    def _handle_process_finish(self, event: Event) -> None:
        machine_id = event.payload["machine_id"]
        lot_id = event.payload["lot_id"]
        operation_index = event.payload["operation_index"]
        machine = self._machines[machine_id]
        lot = self._lots[lot_id]
        if event.payload["activity_token"] != machine.activity_token:
            self._record(
                event_type="PROCESS_FINISH_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                operation=lot.spec.operations[operation_index],
                machine_id=machine_id,
                activity_token=event.payload["activity_token"],
                state_before="STALE_TOKEN",
                state_after="NO_EFFECT",
            )
            return
        if (
            machine.status is not MachineStatus.PROCESSING
            or machine.active_batch_id is not None
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
        self._open_cqt_clocks(
            lot=lot,
            operation=operation,
            priority=event.priority,
            cause_event_seq=event.seq,
        )
        self._release_dedication_bindings(
            lot=lot,
            operation=operation,
            priority=event.priority,
            cause_event_seq=event.seq,
        )
        machine.status = MachineStatus.IDLE
        machine.lot_id = None
        machine.operation_index = None
        machine.processing_started_at = None
        machine.scheduled_activity_finish = None
        self._advance_lot_after_processing(
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            completed_operation=operation,
            source_machine_id=machine_id,
        )
        self._account_completed_wafers(
            event=event,
            machine_id=machine_id,
            wafers=lot.spec.quantity_wafers,
            lot=lot,
            operation=operation,
        )
        self._ensure_dispatch_barrier()

    def _handle_batch_finish(self, event: Event) -> None:
        batch_id = event.payload["batch_id"]
        active = self._active_batches.get(batch_id)
        if active is None:
            raise SimulationError(f"未知或重复 BATCH_FINISH：{batch_id}")
        machine = self._machines[active.machine_id]
        if event.payload["activity_token"] != machine.activity_token:
            self._record(
                event_type="BATCH_FINISH_STALE",
                priority=event.priority,
                cause_event_seq=event.seq,
                machine_id=active.machine_id,
                batch_id=batch_id,
                activity_token=event.payload["activity_token"],
                state_before="STALE_TOKEN",
                state_after="NO_EFFECT",
            )
            return
        if (
            machine.status is not MachineStatus.PROCESSING
            or machine.active_batch_id != batch_id
            or machine.processing_started_at is None
        ):
            raise SimulationError(f"BATCH_FINISH machine 状态不一致：{batch_id}")
        member_lots = [self._lots[lot_id] for lot_id in active.member_lot_ids]
        for lot, operation_index in zip(
            member_lots,
            active.operation_indices,
            strict=True,
        ):
            if (
                lot.status is not LotStatus.PROCESSING
                or lot.operation_index != operation_index
                or lot.current_machine_id != active.machine_id
            ):
                raise SimulationError(
                    f"BATCH_FINISH member 状态不一致：{lot.spec.lot_id}"
                )
        first_operation = member_lots[0].spec.operations[
            active.operation_indices[0]
        ]
        self._batch_intervals.append(
            BatchInterval(
                batch_id=batch_id,
                machine_id=active.machine_id,
                member_lot_ids=active.member_lot_ids,
                member_wafers=active.member_wafers,
                route_id=active.route_id,
                step_id=active.step_id,
                total_wafers=active.total_wafers,
                start=active.start_time,
                finish=self.current_time,
                start_reason=active.start_reason,
                active_processing_time=(
                    active.accumulated_processing_time
                    + self.current_time
                    - machine.processing_started_at
                ),
            )
        )
        batch_trace = {
            "batch_id": batch_id,
            "batch_member_lot_ids": active.member_lot_ids,
            "batch_member_wafers": active.member_wafers,
            "batch_total_wafers": active.total_wafers,
            "batch_start_reason": active.start_reason,
        }
        self._record(
            event_type="BATCH_FINISH",
            priority=event.priority,
            cause_event_seq=event.seq,
            operation=first_operation,
            machine_id=active.machine_id,
            state_before="BATCH:PROCESSING|MACHINE:PROCESSING",
            state_after="BATCH:FINISHED|MACHINE:IDLE",
            **batch_trace,
        )
        machine.status = MachineStatus.IDLE
        machine.processing_started_at = None
        machine.active_batch_id = None
        machine.scheduled_activity_finish = None
        self._active_batches.pop(batch_id)
        for lot, operation_index in zip(
            member_lots,
            active.operation_indices,
            strict=True,
        ):
            operation = lot.spec.operations[operation_index]
            self._record(
                event_type="PROCESS_FINISH",
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                operation=operation,
                machine_id=active.machine_id,
                batch_id=batch_id,
                state_before="LOT:PROCESSING|BATCH:PROCESSING",
                state_after="LOT:PROCESSED|BATCH:FINISHED",
            )
            self._open_cqt_clocks(
                lot=lot,
                operation=operation,
                priority=event.priority,
                cause_event_seq=event.seq,
                batch_id=batch_id,
            )
            self._release_dedication_bindings(
                lot=lot,
                operation=operation,
                priority=event.priority,
                cause_event_seq=event.seq,
                batch_id=batch_id,
            )
            self._advance_lot_after_processing(
                priority=event.priority,
                cause_event_seq=event.seq,
                lot=lot,
                completed_operation=operation,
                batch_id=batch_id,
                source_machine_id=active.machine_id,
            )
        self._account_completed_wafers(
            event=event,
            machine_id=active.machine_id,
            wafers=active.total_wafers,
            operation=first_operation,
            batch_id=batch_id,
        )
        self._ensure_dispatch_barrier()

    def _account_completed_wafers(
        self,
        *,
        event: Event,
        machine_id: str,
        wafers: int,
        lot: _LotRuntime | None = None,
        operation: Any | None = None,
        batch_id: str | None = None,
    ) -> None:
        due = self._pm_runtime.account_completed_wafers(machine_id, wafers)
        if due is None:
            return
        self._record(
            event_type="PM_DUE",
            priority=event.priority,
            cause_event_seq=event.seq,
            lot=lot,
            operation=operation,
            machine_id=machine_id,
            batch_id=batch_id,
            downtime_cause=DowntimeCause.WAFER_PM.value,
            pm_id=due.pm_id,
            pm_trigger_type=DowntimeCause.WAFER_PM.value,
            pm_occurrence_index=due.occurrence_index,
            pm_duration=due.duration,
            wafer_counter_before=due.counter_before,
            wafer_counter_after=due.counter_after,
            wafer_threshold=due.threshold_wafers,
            processed_wafers=due.processed_wafers,
            state_before="PM:NOT_DUE",
            state_after="PM:PENDING",
        )
        self._schedule_pending_wafer_pm(machine_id)

    def _advance_lot_after_processing(
        self,
        *,
        priority: int,
        cause_event_seq: int,
        lot: _LotRuntime,
        completed_operation: Any,
        batch_id: str | None = None,
        source_machine_id: str | None = None,
    ) -> None:
        lot.current_machine_id = None
        lot.operation_index += 1
        while lot.operation_index < len(lot.spec.operations):
            next_operation = lot.spec.operations[lot.operation_index]
            decision = decide_sampling(
                next_operation,
                lot_id=lot.spec.lot_id,
                visit_index=0,
                random_source=self.random_streams,
            )
            if decision is None or decision.performed:
                if decision is not None:
                    self._record_sampling_decision(
                        lot=lot,
                        operation=next_operation,
                        decision=decision,
                        priority=priority,
                        cause_event_seq=cause_event_seq,
                    )
                break
            self._record_sampling_decision(
                lot=lot,
                operation=next_operation,
                decision=decision,
                priority=priority,
                cause_event_seq=cause_event_seq,
            )
            self._record(
                event_type="OPERATION_SKIPPED",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=next_operation,
                sampling_percent=decision.sampling_percent,
                sampling_draw=decision.draw,
                sampling_performed=False,
                sampling_entity_id=decision.entity_id,
                state_before="OPERATION:ENTRY",
                state_after="OPERATION:SKIPPED",
            )
            lot.operation_index += 1
        if lot.operation_index == len(lot.spec.operations):
            lot.status = LotStatus.COMPLETED
            lot.completion_time = self.current_time
            self._record(
                event_type="LOT_COMPLETE",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=completed_operation,
                batch_id=batch_id,
                state_before="PROCESSED",
                state_after=LotStatus.COMPLETED.value,
            )
        else:
            next_operation = lot.spec.operations[lot.operation_index]
            self._dedication_runtime.validate_target_qualification(
                lot_id=lot.spec.lot_id,
                route_id=next_operation.route_id,
                step_id=next_operation.step_id,
                eligible_machines=next_operation.eligible_machines,
                visit_index=0,
            )
            if self._transport_resolver.enabled:
                if source_machine_id is None:
                    raise SimulationError("transport route advance 缺少 source machine")
                self._start_transport(
                    priority=priority,
                    cause_event_seq=cause_event_seq,
                    lot=lot,
                    completed_operation=completed_operation,
                    next_operation=next_operation,
                    source_machine_id=source_machine_id,
                    batch_id=batch_id,
                )
            else:
                lot.status = LotStatus.QUEUED
                lot.queue_entered_at = self.current_time
                self._record(
                    event_type="ROUTE_ADVANCE",
                    priority=priority,
                    cause_event_seq=cause_event_seq,
                    lot=lot,
                    operation=next_operation,
                    batch_id=batch_id,
                    state_before="PROCESSED",
                    state_after=LotStatus.QUEUED.value,
                )

    def _start_transport(
        self,
        *,
        priority: int,
        cause_event_seq: int,
        lot: _LotRuntime,
        completed_operation: Any,
        next_operation: Any,
        source_machine_id: str,
        batch_id: str | None,
    ) -> None:
        source_machine = self._machines[source_machine_id]
        from_location = source_machine.location_id
        if not from_location:
            raise SimulationError(
                f"transport source machine 缺少 location_id：{source_machine_id}"
            )
        target_locations = {
            self._machines[machine_id].location_id
            for machine_id in next_operation.eligible_machines
        }
        if len(target_locations) != 1 or None in target_locations:
            raise SimulationError(
                f"目标工序 location 不唯一：{lot.spec.lot_id}/step "
                f"{next_operation.step_id}"
            )
        to_location = next(iter(target_locations))
        resolution = self._transport_resolver.resolve(from_location, to_location)
        if resolution.missing_pair:
            duration = 0.0
            self._transport_intervals.append(
                TransportInterval(
                    lot.spec.lot_id,
                    next_operation.route_id,
                    completed_operation.step_id,
                    next_operation.step_id,
                    from_location,
                    to_location,
                    self.current_time,
                    self.current_time,
                    duration,
                    True,
                )
            )
            self._record(
                event_type="TRANSPORT_MISSING",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=next_operation,
                batch_id=batch_id,
                machine_id=source_machine_id,
                state_before="PROCESSED",
                state_after="PROCESSED",
                transport_from_location=from_location,
                transport_to_location=to_location,
                transport_duration=duration,
                transport_missing_pair=True,
            )
            lot.status = LotStatus.QUEUED
            lot.queue_entered_at = self.current_time
            self._record(
                event_type="ROUTE_ADVANCE",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=next_operation,
                batch_id=batch_id,
                state_before="PROCESSED",
                state_after=LotStatus.QUEUED.value,
                transport_from_location=from_location,
                transport_to_location=to_location,
                transport_duration=duration,
                transport_missing_pair=True,
            )
            return
        duration, entity_id = self._transport_resolver.sample(
            resolution,
            lot_id=lot.spec.lot_id,
            route_id=next_operation.route_id,
            from_step_id=completed_operation.step_id,
            to_step_id=next_operation.step_id,
            visit_index=0,
            random_source=self.random_streams,
        )
        finish = self.current_time + duration
        self._active_transports[lot.spec.lot_id] = _ActiveTransport(
            lot.spec.lot_id,
            next_operation.route_id,
            completed_operation.step_id,
            next_operation.step_id,
            from_location,
            to_location,
            self.current_time,
            finish,
            duration,
        )
        lot.status = LotStatus.TRANSPORTING
        self._record(
            event_type="TRANSPORT_START",
            priority=priority,
            cause_event_seq=cause_event_seq,
            lot=lot,
            operation=next_operation,
            batch_id=batch_id,
            machine_id=source_machine_id,
            state_before="PROCESSED",
            state_after=LotStatus.TRANSPORTING.value,
            transport_from_location=from_location,
            transport_to_location=to_location,
            transport_duration=duration,
            transport_missing_pair=False,
        )
        self._schedule(
            time=finish,
            event_type=EventType.TRANSPORT_ARRIVE,
            entity_id=lot.spec.lot_id,
            payload={
                "lot_id": lot.spec.lot_id,
                "operation_index": lot.operation_index,
                "from_location": from_location,
                "to_location": to_location,
                "duration": duration,
                "entity_id": entity_id,
            },
        )

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

    def _open_cqt_clocks(
        self,
        *,
        lot: _LotRuntime,
        operation: Any,
        priority: int,
        cause_event_seq: int,
        batch_id: str | None = None,
    ) -> None:
        clocks = self._cqt_runtime.open_for_source(
            lot_id=lot.spec.lot_id,
            route_id=operation.route_id,
            step_id=operation.step_id,
            opened_at=self.current_time,
            visit_index=0,
        )
        for clock in clocks:
            self._record(
                event_type="CQT_OPEN",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=operation,
                batch_id=batch_id,
                cqt_constraint_id=clock.constraint_id,
                cqt_source_step_id=clock.source_step_id,
                cqt_target_step_id=clock.target_step_id,
                cqt_limit=clock.max_duration_minutes,
                cqt_opened_at=clock.opened_at,
                cqt_deadline=clock.deadline,
                state_before="CQT:INACTIVE",
                state_after="CQT:ACTIVE",
            )

    def _establish_dedication_bindings(
        self,
        *,
        lot: _LotRuntime,
        operation: Any,
        machine_id: str,
        priority: int,
        cause_event_seq: int,
        batch_id: str | None = None,
    ) -> None:
        bindings = self._dedication_runtime.establish_for_source(
            lot_id=lot.spec.lot_id,
            route_id=operation.route_id,
            step_id=operation.step_id,
            machine_id=machine_id,
            established_at=self.current_time,
            visit_index=0,
        )
        for binding in bindings:
            self._record(
                event_type="DEDICATION_BIND",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=operation,
                machine_id=machine_id,
                batch_id=batch_id,
                dedication_id=binding.dedication_id,
                dedication_source_step_id=binding.source_step_id,
                dedication_target_step_id=binding.target_step_id,
                dedication_bound_machine_id=binding.machine_id,
                dedication_established_at=binding.established_at,
                state_before="DEDICATION:UNBOUND",
                state_after="DEDICATION:BOUND",
            )

    def _release_dedication_bindings(
        self,
        *,
        lot: _LotRuntime,
        operation: Any,
        priority: int,
        cause_event_seq: int,
        batch_id: str | None = None,
    ) -> None:
        records = self._dedication_runtime.release_for_target(
            lot_id=lot.spec.lot_id,
            route_id=operation.route_id,
            step_id=operation.step_id,
            released_at=self.current_time,
            visit_index=0,
        )
        for record in records:
            self._record(
                event_type="DEDICATION_RELEASE",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=operation,
                machine_id=record.machine_id,
                batch_id=batch_id,
                dedication_id=record.dedication_id,
                dedication_source_step_id=record.source_step_id,
                dedication_target_step_id=record.target_step_id,
                dedication_bound_machine_id=record.machine_id,
                dedication_established_at=record.established_at,
                dedication_released_at=record.released_at,
                state_before="DEDICATION:BOUND",
                state_after="DEDICATION:RELEASED",
            )

    def _close_cqt_clocks(
        self,
        *,
        lot: _LotRuntime,
        operation: Any,
        priority: int,
        cause_event_seq: int,
        batch_id: str | None = None,
    ) -> None:
        records = self._cqt_runtime.close_for_target(
            lot_id=lot.spec.lot_id,
            route_id=operation.route_id,
            step_id=operation.step_id,
            closed_at=self.current_time,
            visit_index=0,
        )
        for record in records:
            fields = {
                "cqt_constraint_id": record.constraint_id,
                "cqt_source_step_id": record.source_step_id,
                "cqt_target_step_id": record.target_step_id,
                "cqt_limit": record.limit,
                "cqt_opened_at": record.opened_at,
                "cqt_deadline": record.opened_at + record.limit,
                "cqt_closed_at": record.closed_at,
                "cqt_actual_duration": record.actual_duration,
                "cqt_slack": record.slack,
                "cqt_violation": record.violation,
                "cqt_excess_duration": record.excess_duration,
            }
            self._record(
                event_type="CQT_CLOSE",
                priority=priority,
                cause_event_seq=cause_event_seq,
                lot=lot,
                operation=operation,
                batch_id=batch_id,
                state_before="CQT:ACTIVE",
                state_after=(
                    "CQT:CLOSED_VIOLATED"
                    if record.violation
                    else "CQT:CLOSED_SATISFIED"
                ),
                **fields,
            )
            if record.violation:
                self._record(
                    event_type="CQT_VIOLATION",
                    priority=priority,
                    cause_event_seq=cause_event_seq,
                    lot=lot,
                    operation=operation,
                    batch_id=batch_id,
                    state_before="CQT:CLOSED",
                    state_after="CQT:VIOLATION_RECORDED",
                    **fields,
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
        route_id: str | None = None,
        step_id: int | None = None,
        batch_id: str | None = None,
        batch_member_lot_ids: tuple[str, ...] | None = None,
        batch_member_wafers: tuple[int, ...] | None = None,
        batch_total_wafers: int | None = None,
        batch_start_reason: str | None = None,
        cqt_constraint_id: str | None = None,
        cqt_source_step_id: int | None = None,
        cqt_target_step_id: int | None = None,
        cqt_limit: float | None = None,
        cqt_opened_at: float | None = None,
        cqt_deadline: float | None = None,
        cqt_closed_at: float | None = None,
        cqt_actual_duration: float | None = None,
        cqt_slack: float | None = None,
        cqt_violation: bool | None = None,
        cqt_excess_duration: float | None = None,
        dedication_id: str | None = None,
        dedication_source_step_id: int | None = None,
        dedication_target_step_id: int | None = None,
        dedication_bound_machine_id: str | None = None,
        dedication_established_at: float | None = None,
        dedication_released_at: float | None = None,
        dedication_audit_reason: str | None = None,
        failure_occurrence_index: int | None = None,
        failure_model_type: str | None = None,
        interrupted_activity_kind: str | None = None,
        remaining_duration: float | None = None,
        repair_duration: float | None = None,
        activity_token: int | None = None,
        downtime_cause: str | None = None,
        pm_id: str | None = None,
        pm_trigger_type: str | None = None,
        pm_occurrence_index: int | None = None,
        pm_duration: float | None = None,
        wafer_counter_before: int | None = None,
        wafer_counter_after: int | None = None,
        wafer_threshold: int | None = None,
        processed_wafers: int | None = None,
        transport_from_location: str | None = None,
        transport_to_location: str | None = None,
        transport_duration: float | None = None,
        transport_missing_pair: bool | None = None,
        sampling_percent: float | None = None,
        sampling_draw: float | None = None,
        sampling_performed: bool | None = None,
        sampling_entity_id: str | None = None,
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
            product_id=lot.spec.product_id if lot is not None else None,
            order_id=lot.spec.order_id if lot is not None else None,
            hot_lot=lot.spec.hot_lot if lot is not None else None,
            source_row=lot.spec.source_row if lot is not None else None,
            lot_due_time=lot.spec.due_time if lot is not None else None,
            lot_priority=lot.spec.priority if lot is not None else None,
            lot_quantity_wafers=(
                lot.spec.quantity_wafers if lot is not None else None
            ),
            release_template_id=(
                lot.spec.release_template_id if lot is not None else None
            ),
            release_repeat_index=(
                lot.spec.release_repeat_index if lot is not None else None
            ),
            release_member_index=(
                lot.spec.release_member_index if lot is not None else None
            ),
            # MC01-MC04 没有返工，每道工序都是首次 visit。
            # 后续解锁返工时改为按 (lot, step) 独立计数。
            visit_index=0 if lot is not None else None,
            route_id=(
                operation.route_id if operation is not None else route_id
            ),
            step_id=operation.step_id if operation is not None else step_id,
            machine_id=machine_id,
            tool_group_id=(
                operation.tool_group_id if operation is not None else None
            ),
            batch_id=batch_id,
            batch_member_lot_ids=batch_member_lot_ids,
            batch_member_wafers=batch_member_wafers,
            batch_total_wafers=batch_total_wafers,
            batch_start_reason=batch_start_reason,
            cqt_constraint_id=cqt_constraint_id,
            cqt_source_step_id=cqt_source_step_id,
            cqt_target_step_id=cqt_target_step_id,
            cqt_limit=cqt_limit,
            cqt_opened_at=cqt_opened_at,
            cqt_deadline=cqt_deadline,
            cqt_closed_at=cqt_closed_at,
            cqt_actual_duration=cqt_actual_duration,
            cqt_slack=cqt_slack,
            cqt_violation=cqt_violation,
            cqt_excess_duration=cqt_excess_duration,
            dedication_id=dedication_id,
            dedication_source_step_id=dedication_source_step_id,
            dedication_target_step_id=dedication_target_step_id,
            dedication_bound_machine_id=dedication_bound_machine_id,
            dedication_established_at=dedication_established_at,
            dedication_released_at=dedication_released_at,
            dedication_audit_reason=dedication_audit_reason,
            failure_occurrence_index=failure_occurrence_index,
            failure_model_type=failure_model_type,
            interrupted_activity_kind=interrupted_activity_kind,
            remaining_duration=remaining_duration,
            repair_duration=repair_duration,
            activity_token=activity_token,
            downtime_cause=downtime_cause,
            pm_id=pm_id,
            pm_trigger_type=pm_trigger_type,
            pm_occurrence_index=pm_occurrence_index,
            pm_duration=pm_duration,
            wafer_counter_before=wafer_counter_before,
            wafer_counter_after=wafer_counter_after,
            wafer_threshold=wafer_threshold,
            processed_wafers=processed_wafers,
            sampling_percent=sampling_percent,
            sampling_draw=sampling_draw,
            sampling_performed=sampling_performed,
            sampling_entity_id=sampling_entity_id,
            transport_from_location=transport_from_location,
            transport_to_location=transport_to_location,
            transport_duration=transport_duration,
            transport_missing_pair=transport_missing_pair,
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
            and not lot.spec.is_initial_wip
        ]
        released_cycle_cohort = [
            lot for lot in released if not lot.spec.is_initial_wip
        ]
        completed_cycle_cohort = [
            lot for lot in completed if not lot.spec.is_initial_wip
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
            cycle_time_coverage=(
                len(completed_cycle_cohort) / len(released_cycle_cohort)
                if released_cycle_cohort
                else 0.0
            ),
            remaining_work_minutes=self._remaining_work_minutes(end_time),
            lateness_exposure_minutes=sum(
                max(0.0, end_time - lot.spec.due_time)
                for lot in released
                if lot.status is not LotStatus.COMPLETED
                and lot.spec.due_time is not None
            ),
            mean_wip=self._mean_wip(end_time),
        )

    def _build_transport_metrics(self) -> TransportMetrics:
        missing: dict[tuple[str, str], int] = {}
        for interval in self._transport_intervals:
            if interval.missing_pair:
                key = (interval.from_location, interval.to_location)
                missing[key] = missing.get(key, 0) + 1
        return TransportMetrics(
            started_count=(
                len(self._transport_intervals) + len(self._active_transports)
            ),
            completed_count=len(self._transport_intervals),
            active_count=len(self._active_transports),
            missing_pair_count=sum(missing.values()),
            total_minutes=sum(item.duration for item in self._transport_intervals),
            missing_pairs=tuple(
                (from_location, to_location, count)
                for (from_location, to_location), count in sorted(missing.items())
            ),
        )

    def _build_active_transport_snapshots(
        self,
        end_time: float,
    ) -> tuple[ActiveTransportSnapshot, ...]:
        return tuple(
            ActiveTransportSnapshot(
                item.lot_id,
                item.route_id,
                item.from_step_id,
                item.to_step_id,
                item.from_location,
                item.to_location,
                item.start,
                item.scheduled_finish,
                max(0.0, item.scheduled_finish - end_time),
            )
            for item in sorted(
                self._active_transports.values(),
                key=lambda value: value.lot_id,
            )
        )

    def _mean_wip(self, end_time: float) -> float:
        if end_time <= 0:
            return 0.0
        changes = sorted(
            (
                record.sim_time,
                1 if record.event_type == "LOT_RELEASE" else -1,
                record.event_seq,
            )
            for record in self._trace
            if record.event_type in {"LOT_RELEASE", "LOT_COMPLETE"}
            and record.sim_time <= end_time
        )
        area = 0.0
        level = 0
        previous = 0.0
        for time, delta, _ in changes:
            area += level * (time - previous)
            level += delta
            previous = time
        area += level * (end_time - previous)
        return area / end_time

    def _remaining_work_minutes(self, end_time: float) -> float:
        total = 0.0
        for lot in self._lots.values():
            if lot.status in {LotStatus.UNRELEASED, LotStatus.COMPLETED}:
                continue
            operations = lot.spec.operations
            total += sum(
                operation.processing_time
                for operation in operations[lot.operation_index:]
            )
            if lot.status is not LotStatus.PROCESSING:
                continue
            operation = operations[lot.operation_index]
            machine_id = lot.current_machine_id
            if machine_id is None:
                raise SimulationError("PROCESSING lot 缺少 machine")
            machine = self._machines[machine_id]
            if machine.active_batch_id is not None:
                batch = self._active_batches[machine.active_batch_id]
                executed = batch.accumulated_processing_time
                if machine.processing_started_at is not None:
                    executed += end_time - machine.processing_started_at
            else:
                executed = sum(
                    interval.finish - interval.start
                    for interval in self._processing_intervals
                    if interval.lot_id == lot.spec.lot_id
                    and interval.step_id == operation.step_id
                    and interval.route_id == operation.route_id
                )
                if machine.processing_started_at is not None:
                    executed += end_time - machine.processing_started_at
            total -= min(operation.processing_time, executed)
        return total

    def _build_machine_statistics(
        self,
        end_time: float,
    ) -> dict[str, MachineStatistics]:
        processing_by_machine = {
            machine_id: 0.0 for machine_id in self._machines
        }
        setup_by_machine = {
            machine_id: 0.0 for machine_id in self._machines
        }
        for interval in self._processing_intervals:
            processing_by_machine[interval.machine_id] += (
                interval.finish - interval.start
            )
        for interval in self._batch_intervals:
            processing_by_machine[interval.machine_id] += (
                interval.active_processing_time
                if interval.active_processing_time is not None
                else interval.finish - interval.start
            )
        for interval in self._setup_intervals:
            setup_by_machine[interval.machine_id] += (
                interval.finish - interval.start
            )
        for machine_id, machine in self._machines.items():
            if machine.active_batch_id is not None:
                processing_by_machine[machine_id] += self._active_batches[
                    machine.active_batch_id
                ].accumulated_processing_time
            if (
                machine.status is MachineStatus.PROCESSING
                and machine.processing_started_at is not None
            ):
                processing_by_machine[machine_id] += (
                    end_time - machine.processing_started_at
                )
            elif (
                machine.status is MachineStatus.SETTING_UP
                and machine.setup_started_at is not None
            ):
                setup_by_machine[machine_id] += (
                    end_time - machine.setup_started_at
                )
        downtime_by_machine = {
            machine_id: 0.0 for machine_id in self._machines
        }
        failure_downtime_by_machine = {
            machine_id: 0.0 for machine_id in self._machines
        }
        pm_downtime_by_machine = {
            machine_id: 0.0 for machine_id in self._machines
        }
        for interval in self._downtime_intervals:
            duration = interval.finish - interval.start
            downtime_by_machine[interval.machine_id] += duration
            failure_downtime_by_machine[interval.machine_id] += duration
        for interval in self._pm_intervals:
            duration = interval.finish - interval.start
            downtime_by_machine[interval.machine_id] += duration
            pm_downtime_by_machine[interval.machine_id] += duration
        for machine_id, machine in self._machines.items():
            if machine.downtime_started_at is not None:
                duration = end_time - machine.downtime_started_at
                downtime_by_machine[machine_id] += duration
                if machine.downtime_cause is DowntimeCause.FAILURE:
                    failure_downtime_by_machine[machine_id] += duration
                else:
                    pm_downtime_by_machine[machine_id] += duration
        wafer_states = {
            state.machine_id: state for state in self._pm_runtime.snapshots
        }
        return {
            machine_id: MachineStatistics(
                machine_id=machine_id,
                processing_time=processing_by_machine[machine_id],
                setup_time=setup_by_machine[machine_id],
                idle_time=max(
                    0.0,
                    end_time
                    - processing_by_machine[machine_id]
                    - setup_by_machine[machine_id]
                    - downtime_by_machine[machine_id],
                ),
                final_state=machine.status.value,
                final_setup=machine.current_setup,
                active_batch_id=machine.active_batch_id,
                downtime=downtime_by_machine[machine_id],
                availability=machine.availability.value,
                failure_count=machine.failure_count,
                remaining_repair_time=(
                    max(0.0, machine.repair_ends_at - end_time)
                    if machine.repair_ends_at is not None
                    else None
                ),
                interrupted_activity_kind=(
                    machine.interrupted_activity.kind.value
                    if machine.interrupted_activity is not None
                    else None
                ),
                remaining_activity_time=(
                    machine.interrupted_activity.remaining_duration
                    if machine.interrupted_activity is not None
                    else None
                ),
                failure_downtime=failure_downtime_by_machine[machine_id],
                pm_downtime=pm_downtime_by_machine[machine_id],
                pm_count=machine.pm_count,
                downtime_cause=(
                    machine.downtime_cause.value
                    if machine.downtime_cause is not None
                    else None
                ),
                wafer_counter=(
                    wafer_states[machine_id].counter_wafers
                    if machine_id in wafer_states
                    else None
                ),
                wafer_pm_pending=(
                    wafer_states[machine_id].pending
                    if machine_id in wafer_states
                    else False
                ),
            )
            for machine_id, machine in sorted(self._machines.items())
        }

    def _build_failure_snapshots(
        self,
        end_time: float,
    ) -> tuple[MachineFailureSnapshot, ...]:
        return tuple(
            MachineFailureSnapshot(
                machine_id=machine_id,
                availability=machine.availability.value,
                repair_ends_at=machine.repair_ends_at,
                remaining_repair_time=(
                    max(0.0, machine.repair_ends_at - end_time)
                    if machine.repair_ends_at is not None
                    else None
                ),
                interrupted_activity_kind=(
                    machine.interrupted_activity.kind.value
                    if machine.interrupted_activity is not None
                    else None
                ),
                remaining_activity_time=(
                    machine.interrupted_activity.remaining_duration
                    if machine.interrupted_activity is not None
                    else None
                ),
                lot_id=machine.lot_id,
                batch_id=machine.active_batch_id,
                downtime_cause=(
                    machine.downtime_cause.value
                    if machine.downtime_cause is not None
                    else None
                ),
                downtime_id=machine.downtime_id,
                remaining_downtime_time=(
                    max(0.0, machine.downtime_ends_at - end_time)
                    if machine.downtime_ends_at is not None
                    else None
                ),
                wafer_pm_pending=(
                    self._pm_runtime.pending_for_machine(machine_id) is not None
                ),
            )
            for machine_id, machine in sorted(self._machines.items())
            if machine.availability is MachineAvailability.DOWN
            or machine.failure_count > 0
            or machine.pm_count > 0
            or self._pm_runtime.pending_for_machine(machine_id) is not None
        )
