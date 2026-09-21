"""物理机 Dedication 的独立运行时与硬可行性检查。"""

from __future__ import annotations

from dataclasses import dataclass

from fab_scheduler.domain.models import DedicationSpec


DEDICATION_RUNTIME_SCHEMA_VERSION = "0.1.0"


class DedicationRuntimeError(RuntimeError):
    """Dedication 绑定、资格或生命周期违反契约。"""


@dataclass(frozen=True, slots=True)
class DedicationBinding:
    dedication_id: str
    lot_id: str
    route_id: str
    source_step_id: int
    target_step_id: int
    visit_index: int
    machine_id: str
    established_at: float


@dataclass(frozen=True, slots=True)
class DedicationRecord:
    dedication_id: str
    lot_id: str
    route_id: str
    source_step_id: int
    target_step_id: int
    visit_index: int
    machine_id: str
    established_at: float
    released_at: float


@dataclass(frozen=True, slots=True)
class InitialWipDedicationAudit:
    dedication_id: str
    lot_id: str
    route_id: str
    source_step_id: int
    target_step_id: int
    visit_index: int
    current_step_id: int
    recorded_at: float
    reason: str = "initial_wip_missing_dedication"


@dataclass(frozen=True, slots=True)
class DedicationMetrics:
    dedication_binding_count: int
    released_dedication_count: int
    active_dedication_count: int
    initial_wip_missing_dedication: int


class DedicationRuntime:
    """保存绑定状态；所有可行性查询均为无副作用纯读取。"""

    def __init__(self, specs: tuple[DedicationSpec, ...]) -> None:
        self._specs = tuple(
            sorted(specs, key=lambda item: item.dedication_id)
        )
        self._by_source: dict[
            tuple[str, int], tuple[DedicationSpec, ...]
        ] = {}
        self._by_target: dict[
            tuple[str, int], tuple[DedicationSpec, ...]
        ] = {}
        for spec in self._specs:
            source_key = (spec.route_id, spec.source_step_id)
            target_key = (spec.route_id, spec.target_step_id)
            self._by_source[source_key] = (
                *self._by_source.get(source_key, ()),
                spec,
            )
            self._by_target[target_key] = (
                *self._by_target.get(target_key, ()),
                spec,
            )
        self._active: dict[
            tuple[str, str, int], DedicationBinding
        ] = {}
        self._released: list[DedicationRecord] = []
        self._initial_unknown_keys: set[tuple[str, str, int]] = set()
        self._initial_audits: list[InitialWipDedicationAudit] = []
        self._binding_count = 0

    @property
    def active_bindings(self) -> tuple[DedicationBinding, ...]:
        return tuple(self._active[key] for key in sorted(self._active))

    @property
    def released_records(self) -> tuple[DedicationRecord, ...]:
        return tuple(self._released)

    @property
    def initial_wip_audits(self) -> tuple[InitialWipDedicationAudit, ...]:
        return tuple(self._initial_audits)

    @property
    def metrics(self) -> DedicationMetrics:
        return DedicationMetrics(
            dedication_binding_count=self._binding_count,
            released_dedication_count=len(self._released),
            active_dedication_count=len(self._active),
            initial_wip_missing_dedication=len(self._initial_audits),
        )

    def register_initial_wip(
        self,
        *,
        lot_id: str,
        route_id: str,
        current_step_id: int,
        recorded_at: float,
        visit_index: int = 0,
    ) -> tuple[InitialWipDedicationAudit, ...]:
        audits: list[InitialWipDedicationAudit] = []
        for spec in self._specs:
            if spec.route_id != route_id:
                continue
            if not (
                spec.source_step_id < current_step_id
                <= spec.target_step_id
            ):
                continue
            key = (lot_id, spec.dedication_id, visit_index)
            if key in self._initial_unknown_keys:
                raise DedicationRuntimeError(
                    f"重复登记初始 WIP dedication 缺口：{key}"
                )
            audit = InitialWipDedicationAudit(
                dedication_id=spec.dedication_id,
                lot_id=lot_id,
                route_id=route_id,
                source_step_id=spec.source_step_id,
                target_step_id=spec.target_step_id,
                visit_index=visit_index,
                current_step_id=current_step_id,
                recorded_at=recorded_at,
            )
            self._initial_unknown_keys.add(key)
            self._initial_audits.append(audit)
            audits.append(audit)
        return tuple(audits)

    def establish_for_source(
        self,
        *,
        lot_id: str,
        route_id: str,
        step_id: int,
        machine_id: str,
        established_at: float,
        visit_index: int = 0,
    ) -> tuple[DedicationBinding, ...]:
        bindings: list[DedicationBinding] = []
        for spec in self._by_source.get((route_id, step_id), ()):
            key = (lot_id, spec.dedication_id, visit_index)
            if key in self._active or key in self._initial_unknown_keys:
                raise DedicationRuntimeError(
                    f"重复或冲突建立 Dedication binding：{key}"
                )
            binding = DedicationBinding(
                dedication_id=spec.dedication_id,
                lot_id=lot_id,
                route_id=route_id,
                source_step_id=spec.source_step_id,
                target_step_id=spec.target_step_id,
                visit_index=visit_index,
                machine_id=machine_id,
                established_at=established_at,
            )
            self._active[key] = binding
            self._binding_count += 1
            bindings.append(binding)
        return tuple(bindings)

    def allows_machine(
        self,
        *,
        lot_id: str,
        route_id: str,
        step_id: int,
        machine_id: str,
        visit_index: int = 0,
    ) -> bool:
        for spec in self._by_target.get((route_id, step_id), ()):
            key = (lot_id, spec.dedication_id, visit_index)
            if key in self._initial_unknown_keys:
                continue
            binding = self._active.get(key)
            if binding is None:
                raise DedicationRuntimeError(
                    f"Dedication target 缺少 binding：{key}"
                )
            if binding.machine_id != machine_id:
                return False
        return True

    def validate_target_qualification(
        self,
        *,
        lot_id: str,
        route_id: str,
        step_id: int,
        eligible_machines: tuple[str, ...],
        visit_index: int = 0,
    ) -> None:
        for spec in self._by_target.get((route_id, step_id), ()):
            key = (lot_id, spec.dedication_id, visit_index)
            if key in self._initial_unknown_keys:
                continue
            binding = self._active.get(key)
            if binding is None:
                raise DedicationRuntimeError(
                    f"Dedication target 缺少 binding：{key}"
                )
            if binding.machine_id not in eligible_machines:
                raise DedicationRuntimeError(
                    "Dedication 与 qualification 冲突："
                    f"{key} 绑定 {binding.machine_id}，"
                    f"qualified={sorted(eligible_machines)}"
                )

    def release_for_target(
        self,
        *,
        lot_id: str,
        route_id: str,
        step_id: int,
        released_at: float,
        visit_index: int = 0,
    ) -> tuple[DedicationRecord, ...]:
        records: list[DedicationRecord] = []
        for spec in self._by_target.get((route_id, step_id), ()):
            key = (lot_id, spec.dedication_id, visit_index)
            if key in self._initial_unknown_keys:
                self._initial_unknown_keys.remove(key)
                continue
            binding = self._active.pop(key, None)
            if binding is None:
                raise DedicationRuntimeError(
                    f"释放不存在的 Dedication binding：{key}"
                )
            record = DedicationRecord(
                dedication_id=binding.dedication_id,
                lot_id=binding.lot_id,
                route_id=binding.route_id,
                source_step_id=binding.source_step_id,
                target_step_id=binding.target_step_id,
                visit_index=binding.visit_index,
                machine_id=binding.machine_id,
                established_at=binding.established_at,
                released_at=released_at,
            )
            self._released.append(record)
            records.append(record)
        return tuple(records)
