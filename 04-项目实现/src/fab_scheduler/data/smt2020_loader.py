"""SMT2020 原始表到静态领域模型及受控验证 Scenario 的正式入口。"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

from fab_scheduler.data.manifest import DatasetManifest, build_dataset_manifest
from fab_scheduler.domain.models import (
    DatasetProvenanceSpec,
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    SourceFileProvenance,
)


SMT2020_LOADER_VERSION = "0.1.0"
SMT2020_LOADER_CONTRACT_VERSION = "0.1.0"
Severity = Literal["ERROR", "BLOCKER", "WARNING", "INFO"]


class LoaderError(RuntimeError):
    def __init__(self, entries: tuple["LoaderAuditEntry", ...]) -> None:
        self.entries = entries
        super().__init__("; ".join(f"{item.code}: {item.message}" for item in entries))


@dataclass(frozen=True, slots=True)
class LoaderAuditEntry:
    severity: Severity
    code: str
    message: str
    context: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    topic: str
    level: str
    source: str
    interpretation: str


@dataclass(frozen=True, slots=True)
class DistributionDefinition:
    kind: str
    parameter_1_minutes: float
    parameter_2_minutes: float | None
    raw_unit: str


@dataclass(frozen=True, slots=True)
class ProductDefinition:
    product_id: str
    product_family: str
    route_file: str
    route_id: str


@dataclass(frozen=True, slots=True)
class MachineTemplateDefinition:
    tool_family_id: str
    station_id: str
    group_id: str
    location_id: str
    quantity: int
    resource_instance_ids: tuple[str, ...]
    batch_criterion: str | None
    batch_unit: str | None
    setup_group: str | None
    load_minutes: float
    unload_minutes: float
    cascading: bool


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    route_id: str
    step_id: int
    description: str
    tool_family_id: str
    eligible_machine_ids: tuple[str, ...]
    processing: DistributionDefinition
    processing_basis: str
    batch_min_wafers: int | None
    batch_max_wafers: int | None
    required_setup: str | None
    setup_override_minutes: float | None
    batch_interval_minutes: float | None
    part_interval_minutes: float | None
    sample_percent: float | None
    rework_step_id: int | None
    rework_percent: float | None
    cqt_target_step_id: int | None
    cqt_limit_minutes: float | None
    dedication_target_step_id: int | None


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    route_id: str
    source_file: str
    operations: tuple[OperationDefinition, ...]


@dataclass(frozen=True, slots=True)
class ReleaseTemplateDefinition:
    lot_prefix: str
    product_id: str
    priority: int
    quantity_wafers: int
    first_release_minutes: float
    interval: DistributionDefinition
    repeat_limit: int
    lots_per_repeat: int
    relative_due_minutes: float | None
    hot_lot: bool


@dataclass(frozen=True, slots=True)
class InitialWipDefinition:
    lot_id: str
    product_id: str
    quantity_wafers: int
    current_step_id: int
    due_minutes: float | None


@dataclass(frozen=True, slots=True)
class TransportDefinition:
    from_location: str
    to_location: str
    duration: DistributionDefinition


@dataclass(frozen=True, slots=True)
class SetupTransitionDefinition:
    from_setup: str
    to_setup: str
    duration_minutes: float
    source_scope: str


@dataclass(frozen=True, slots=True)
class SetupGroupMemberDefinition:
    setup_group: str
    setup_id: str
    minimum_run: int
    source_scope: str


@dataclass(frozen=True, slots=True)
class CalendarDefinition:
    calendar_id: str
    calendar_type: str
    interval: DistributionDefinition | None
    wafer_threshold: int | None
    duration: DistributionDefinition


@dataclass(frozen=True, slots=True)
class CalendarAttachmentDefinition:
    calendar_id: str
    calendar_kind: str
    resource_type: str
    resource_name: str
    first_occurrence: DistributionDefinition | None
    first_occurrence_wafers: int | None


@dataclass(frozen=True, slots=True)
class SMT2020StaticModel:
    products: tuple[ProductDefinition, ...]
    routes: tuple[RouteDefinition, ...]
    machine_templates: tuple[MachineTemplateDefinition, ...]
    release_templates: tuple[ReleaseTemplateDefinition, ...]
    initial_wip: tuple[InitialWipDefinition, ...]
    setup_transitions: tuple[SetupTransitionDefinition, ...]
    setup_group_members: tuple[SetupGroupMemberDefinition, ...]
    transport: tuple[TransportDefinition, ...]
    failure_calendars: tuple[CalendarDefinition, ...]
    pm_calendars: tuple[CalendarDefinition, ...]
    calendar_attachments: tuple[CalendarAttachmentDefinition, ...]


@dataclass(frozen=True, slots=True)
class LoaderConfig:
    mode: Literal["audit", "validation_slice"] = "audit"
    validation_product_id: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"audit", "validation_slice"}:
            raise ValueError(f"不支持的 loader mode：{self.mode}")
        if self.mode == "audit" and self.validation_product_id is not None:
            raise ValueError("audit mode 不接受 validation_product_id")

    def provenance_items(self) -> tuple[tuple[str, str], ...]:
        return (
            ("mode", self.mode),
            ("validation_product_id", self.validation_product_id or ""),
        )


@dataclass(frozen=True, slots=True)
class LoadedScenario:
    scenario: Scenario | None
    static_model: SMT2020StaticModel
    dataset_manifest: DatasetManifest
    loader_audit: tuple[LoaderAuditEntry, ...]
    semantic_evidence: tuple[SemanticEvidence, ...]
    statistics: Mapping[str, Any]
    loader_config: LoaderConfig

    @property
    def blocker_count(self) -> int:
        return sum(item.severity == "BLOCKER" for item in self.loader_audit)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "ERROR" for item in self.loader_audit)

    def audit_to_dict(self) -> dict[str, Any]:
        """返回可直接写入 JSON 的机器可审计摘要。"""

        return {
            "loader_contract_version": SMT2020_LOADER_CONTRACT_VERSION,
            "loader_version": SMT2020_LOADER_VERSION,
            "dataset_manifest": asdict(self.dataset_manifest),
            "loader_config": asdict(self.loader_config),
            "statistics": dict(self.statistics),
            "audit": [asdict(item) for item in self.loader_audit],
            "semantic_evidence": [asdict(item) for item in self.semantic_evidence],
            "scenario_constructed": self.scenario is not None,
            "blocker_count": self.blocker_count,
            "error_count": self.error_count,
        }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = []
        for row in csv.DictReader(handle, delimiter="\t"):
            rows.append({key: (value or "").strip() for key, value in row.items() if key is not None})
        return rows


_UNIT_TO_MINUTES = {"sec": 1 / 60, "min": 1, "hr": 60, "day": 1440}


def _minutes(value: str, unit: str) -> float:
    try:
        factor = _UNIT_TO_MINUTES[unit]
    except KeyError as exc:
        raise ValueError(f"不支持的时间单位：{unit!r}") from exc
    result = float(value) * factor
    if not isfinite(result) or result < 0:
        raise ValueError(f"非法时长：{value} {unit}")
    return result


def parse_distribution(
    kind: str,
    value_1: str,
    value_2: str,
    unit: str,
) -> DistributionDefinition:
    """统一解析 constant/uniform/exponential；不等同于 runtime 已支持。"""

    normalized = kind.lower().strip()
    if normalized not in {"constant", "uniform", "exponential"}:
        raise ValueError(f"不支持的分布：{kind!r}")
    first = _minutes(value_1, unit)
    second = _minutes(value_2, unit) if value_2 else None
    if first <= 0:
        raise ValueError("分布第一参数必须为正")
    if normalized == "uniform":
        if second is None or second < 0 or first - second / 2 <= 0:
            raise ValueError("uniform 必须满足 mean-width/2 > 0")
    elif second not in (None, 0.0):
        raise ValueError(f"{normalized} 不接受第二参数")
    return DistributionDefinition(normalized, first, second, unit)


def _int(value: str) -> int:
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"需要整数，实际为 {value!r}")
    return int(number)


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%m/%d/%y %H:%M:%S")


def _dataset_dir(dataset_root: Path, model_id: str) -> Path:
    candidate = dataset_root.resolve() / model_id
    if candidate.is_dir():
        return candidate
    if dataset_root.resolve().name == model_id and dataset_root.is_dir():
        return dataset_root.resolve()
    raise FileNotFoundError(f"找不到 SMT2020 模型目录：{candidate}")


def load_smt2020(
    dataset_root: Path | str,
    model_id: str,
    *,
    loader_config: LoaderConfig | None = None,
) -> LoadedScenario:
    """只读加载真实 SMT2020 表；BLOCKER 不会被隐式降级为默认值。"""

    config = loader_config or LoaderConfig()
    if not isinstance(config, LoaderConfig):
        raise TypeError("loader_config 必须是 LoaderConfig")
    root = _dataset_dir(Path(dataset_root), model_id)
    manifest = build_dataset_manifest(root, model_name=model_id, loader_version=SMT2020_LOADER_VERSION)
    audit: list[LoaderAuditEntry] = []

    def note(severity: Severity, code: str, message: str, **context: object) -> None:
        audit.append(LoaderAuditEntry(severity, code, message, tuple(sorted((key, str(value)) for key, value in context.items()))))

    required = {"part.txt", "order.txt", "WIP.txt", "tool.txt.1l", "setupgrp.txt", "setup.txt", "pmcal.txt", "downcal.txt", "attach.txt", "fromto.txt"}
    present = {item.relative_path for item in manifest.files}
    missing = sorted(required - present)
    if missing:
        note("ERROR", "DI_MISSING_FILES", "缺少必需原始文件", files=",".join(missing))

    try:
        part_rows = _read_tsv(root / "part.txt")
        tool_rows = _read_tsv(root / "tool.txt.1l")
        order_rows = _read_tsv(root / "order.txt")
        wip_rows = _read_tsv(root / "WIP.txt")
        route_rows_by_file = {
            path.name: _read_tsv(path) for path in sorted(root.glob("route_*.txt"))
        }
        setup_rows = _read_tsv(root / "setup.txt")
        setup_group_rows = _read_tsv(root / "setupgrp.txt")
        pm_rows = _read_tsv(root / "pmcal.txt")
        down_rows = _read_tsv(root / "downcal.txt")
        attach_rows = _read_tsv(root / "attach.txt")
        transport_rows = _read_tsv(root / "fromto.txt")
    except (OSError, ValueError) as exc:
        note("ERROR", "DI_PARSE_ERROR", str(exc))
        raise LoaderError(tuple(audit)) from exc

    products: list[ProductDefinition] = []
    product_ids: set[str] = set()
    for row in part_rows:
        product = ProductDefinition(row["PART"], row["PARTFAM"], row["ROUTEFILE"], row["ROUTE"])
        if product.product_id in product_ids:
            note("ERROR", "DI_DUPLICATE_PRODUCT", "产品 ID 重复", product=product.product_id)
        product_ids.add(product.product_id)
        if product.route_file not in route_rows_by_file:
            note("ERROR", "DI_MISSING_ROUTE_FILE", "产品引用的 route 文件不存在", product=product.product_id, route_file=product.route_file)
        products.append(product)

    machine_templates: list[MachineTemplateDefinition] = []
    family_machines: dict[str, list[str]] = {}
    group_names: set[str] = set()
    for row in tool_rows:
        quantity = _int(row["STNQTY"])
        if quantity <= 0:
            note("ERROR", "DI_INVALID_MACHINE_QUANTITY", "STNQTY 必须为正", station=row["STN"])
            continue
        machine_ids = tuple(f"{row['STN']}#{index:04d}" for index in range(1, quantity + 1))
        family_machines.setdefault(row["STNFAM"], []).extend(machine_ids)
        group_names.add(row["STNGRP"])
        machine_templates.append(MachineTemplateDefinition(
            row["STNFAM"], row["STN"], row["STNGRP"], row["STNFAMLOC"], quantity,
            machine_ids,
            row["BATCHCRITF"] or None, row["BATCHPER"] or None, row["SETUPGRP"] or None,
            _minutes(row["LTIME"], row["LTUNITS"]), _minutes(row["ULTIME"], row["ULTUNITS"]), row["STNCAP"] == "2.0",
        ))

    routes: list[RouteDefinition] = []
    route_lookup: dict[str, RouteDefinition] = {}
    raw_operation_count = 0
    for product in products:
        rows = route_rows_by_file.get(product.route_file, [])
        raw_operation_count += len(rows)
        operations: list[OperationDefinition] = []
        observed_steps: list[int] = []
        for row in rows:
            step = _int(row["STEP"])
            observed_steps.append(step)
            eligible = tuple(family_machines.get(row["STNFAM"], ()))
            if not eligible:
                note("ERROR", "DI_EMPTY_QUALIFICATION", "工序没有可用物理机", route=product.route_id, step=step, tool_family=row["STNFAM"])
            try:
                processing = parse_distribution(row["PDIST"], row["PTIME"], row["PTIME2"], row["PTUNITS"])
            except ValueError as exc:
                note("ERROR", "DI_INVALID_PROCESSING_DISTRIBUTION", str(exc), route=product.route_id, step=step)
                continue
            op = OperationDefinition(
                product.route_id, step, row["DESC"], row["STNFAM"], eligible, processing, row["PTPER"],
                _int(row["BATCHMN"]) if row["BATCHMN"] else None,
                _int(row["BATCHMX"]) if row["BATCHMX"] else None,
                row["SETUP"] or None,
                _minutes(row["STIME"], row["STUNITS"]) if row["STIME"] else None,
                _minutes(row["BatchInterval"], row["BatchIntUnits"]) if row["BatchInterval"] else None,
                _minutes(row["PartInterval"], row["PartIntUnits"]) if row["PartInterval"] else None,
                float(row["StepPercent"]) if row["StepPercent"] else None,
                _int(row["RWKSTEP"]) if row["RWKSTEP"] else None,
                float(row["REWORK"]) if row["REWORK"] else None,
                _int(row["STEP_CQT"]) if row["STEP_CQT"] else None,
                _minutes(row["CQT"], row["CQTUNITS"]) if row["CQT"] else None,
                _int(row["FORSTEP"]) if row["SVESTN"].lower() == "yes" else None,
            )
            if op.batch_min_wafers is not None and (op.batch_max_wafers is None or op.batch_min_wafers > op.batch_max_wafers or op.processing_basis != "per_batch"):
                note("ERROR", "DI_INVALID_BATCH_RANGE", "Batch capacity/PTPER 不一致", route=product.route_id, step=step)
            operations.append(op)
        if observed_steps != list(range(1, len(observed_steps) + 1)):
            note("ERROR", "DI_INVALID_ROUTE_SEQUENCE", "STEP 必须按显式 sequence 为 1..N", route=product.route_id)
        known = set(observed_steps)
        for op in operations:
            for target, code in ((op.cqt_target_step_id, "CQT"), (op.dedication_target_step_id, "DEDICATION")):
                if target is not None and (target not in known or target <= op.step_id):
                    note("ERROR", f"DI_INVALID_{code}_LINK", "跨步引用无效", route=product.route_id, source=op.step_id, target=target)
        route = RouteDefinition(product.route_id, product.route_file, tuple(operations))
        routes.append(route)
        route_lookup[product.route_id] = route

    base_times = [_parse_datetime(row["START"]) for row in order_rows + wip_rows if row["START"]]
    epoch = min(base_times)
    releases: list[ReleaseTemplateDefinition] = []
    for row in order_rows:
        if row["PART"] not in product_ids:
            note("ERROR", "DI_UNKNOWN_RELEASE_PRODUCT", "order 引用未知产品", lot=row["LOT"], product=row["PART"])
        start = _parse_datetime(row["START"])
        due = _parse_datetime(row["DUE"]) if row["DUE"] else None
        releases.append(ReleaseTemplateDefinition(
            row["LOT"], row["PART"], _int(row["PRIOR"]), _int(row["PIECES"]),
            (start - epoch).total_seconds() / 60,
            parse_distribution(row["RDIST"], row["REPEAT"], "", row["RUNITS"]),
            _int(row["RPT#"]), _int(row["LOTSPERRPT"]),
            (due - start).total_seconds() / 60 if due else None,
            row["HOTLOT"].lower() == "yes",
        ))

    product_route = {item.product_id: route_lookup.get(item.route_id) for item in products}
    initial_wip: list[InitialWipDefinition] = []
    unknown_cqt = 0
    unknown_dedication = 0
    for row in wip_rows:
        route = product_route.get(row["PART"])
        current = _int(row["CURSTEP"])
        if route is None or current not in {op.step_id for op in route.operations}:
            note("ERROR", "DI_INVALID_WIP_STEP", "WIP 当前 step 不在产品 route", lot=row["LOT"], product=row["PART"], step=current)
            continue
        due = _parse_datetime(row["DUE"]) if row["DUE"] else None
        initial_wip.append(InitialWipDefinition(row["LOT"], row["PART"], _int(row["PIECES"]), current, (due - epoch).total_seconds() / 60 if due else None))
        unknown_cqt += sum(op.step_id < current <= (op.cqt_target_step_id or -1) for op in route.operations)
        unknown_dedication += sum(op.step_id < current <= (op.dedication_target_step_id or -1) for op in route.operations)

    transport = tuple(TransportDefinition(row["FROMLOC"], row["TOLOC"], parse_distribution(row["DDIST"], row["DTIME"], row["DTIME2"], row["DUNITS"])) for row in transport_rows)
    down_calendars = tuple(CalendarDefinition(row["DOWNCALNAME"], row["DOWNCALTYPE"], parse_distribution(row["MTTFDIST"], row["MTTF"], "", row["MTTFUNITS"]), None, parse_distribution(row["MTTRDIST"], row["MTTR"], "", row["MTTRUNITS"])) for row in down_rows)
    pm_calendars = tuple(
        CalendarDefinition(
            row["PMCALNAME"], row["PMCALTYPE"],
            None if row["MTBPMUNITS"] == "pieces" else parse_distribution("constant", row["MTBPM"], "", row["MTBPMUNITS"]),
            _int(row["MTBPM"]) if row["MTBPMUNITS"] == "pieces" else None,
            parse_distribution(row["MTTRDIST"], row["MTTR"], row["MTTR2"], row["MTTRUNITS"]),
        )
        for row in pm_rows
    )
    calendar_ids = {item.calendar_id for item in down_calendars + pm_calendars}
    attachments: list[CalendarAttachmentDefinition] = []
    for row in attach_rows:
        if row["CALNAME"] not in calendar_ids:
            note("ERROR", "DI_UNKNOWN_CALENDAR", "attach 引用未知 calendar", calendar=row["CALNAME"])
        valid_target = row["RESNAME"] in (set(family_machines) if row["RESTYPE"] == "stnfam" else group_names)
        if not valid_target:
            note("ERROR", "DI_UNKNOWN_ATTACHMENT_TARGET", "attach 引用未知设备资源", calendar=row["CALNAME"], target=row["RESNAME"])
        if row["FOAUNITS"]:
            first = parse_distribution(row["FOADIST"], row["FOA"], "", row["FOAUNITS"])
            first_wafers = None
        else:
            first = None
            first_wafers = _int(row["FOA"])
        attachments.append(CalendarAttachmentDefinition(row["CALNAME"], row["CALTYPE"], row["RESTYPE"], row["RESNAME"], first, first_wafers))

    setup_transitions = tuple(
        SetupTransitionDefinition(
            row["CURSETUP"], row["NEWSETUP"],
            _minutes(row["STIME"], row["STUNITS"]), row["IGNORE"],
        )
        for row in setup_rows
    )
    setup_group_members: list[SetupGroupMemberDefinition] = []
    active_setup_group = ""
    for row in setup_group_rows:
        active_setup_group = row["SETUPGRP"] or active_setup_group
        if not active_setup_group:
            note("ERROR", "DI_SETUP_GROUP_FORWARD_FILL", "setupgrp 首条记录缺少 SETUPGRP")
            continue
        setup_group_members.append(
            SetupGroupMemberDefinition(
                active_setup_group, row["SETUP"], _int(row["MINRUN"]), row["IGNORE"]
            )
        )
    setup_transition_count = len(setup_transitions)
    setup_minrun_count = len(setup_group_members)
    operations = tuple(op for route in routes for op in route.operations)
    batch_ops = tuple(op for op in operations if op.batch_min_wafers is not None)
    cqt_ops = tuple(op for op in operations if op.cqt_target_step_id is not None)
    dedication_ops = tuple(op for op in operations if op.dedication_target_step_id is not None)
    sample_field_ops = tuple(op for op in operations if op.sample_percent is not None)
    sample_ops = tuple(op for op in operations if op.sample_percent not in (None, 100.0))
    rework_ops = tuple(op for op in operations if op.rework_step_id is not None)
    cascading_ops = tuple(op for op in operations if op.batch_interval_minutes is not None or op.part_interval_minutes is not None)

    note("BLOCKER", "DI_UNSUPPORTED_PROCESSING_DISTRIBUTION", "runtime 的 OperationSpec 仍是确定性时长；原始工序全部使用 uniform", affected=len(operations))
    note("BLOCKER", "DI_UNSUPPORTED_PROCESSING_BASIS", "per-piece/per-batch 与级联加工尚未形成完整 runtime 链", affected=sum(op.processing_basis != "per_lot" for op in operations))
    note("BLOCKER", "DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE", "load/unload、STNCAP 与 Part/BatchInterval 尚未执行", affected=len(cascading_ops))
    note("BLOCKER", "DI_UNSUPPORTED_RELEASE_TEMPLATES", "RPT# 重复投放需要按 horizon 惰性生成，当前 Scenario 只接受显式 lot", templates=len(releases))
    note("BLOCKER", "DI_UNSUPPORTED_TRANSPORT_RUNTIME", "transport 表已解析，但 TRANSPORTING/ARRIVE runtime 尚未实现", pairs=len(transport))
    if sample_ops:
        note("BLOCKER", "DI_UNSUPPORTED_SAMPLING", "StepPercent 抽样跳步尚未实现", affected=len(sample_ops))
    if rework_ops:
        note("BLOCKER", "DI_UNSUPPORTED_REWORK", "RWKSTEP/REWORK 路线回跳尚未实现", affected=len(rework_ops))
    if setup_minrun_count:
        note("BLOCKER", "DI_UNSUPPORTED_SETUP_MINRUN", "setupgrp.MINRUN 尚未进入 runtime", affected=setup_minrun_count)
    note("BLOCKER", "DI_MISSING_BATCH_DECISION_CONFIG", "B_target/T_max 不在 raw 数据中；正式场景需版本化 loader_config", batch_operations=len(batch_ops))
    if any(item.interval and item.interval.kind == "exponential" for item in down_calendars):
        note("BLOCKER", "DI_UNSUPPORTED_EXPONENTIAL_FAILURE", "Failure runtime 分布类型尚不支持 exponential", calendars=len(down_calendars))
    note("BLOCKER", "DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT", "同一物理机可能附着多条 Failure/PM calendar，而当前 Scenario 每类每机最多一条", attachments=len(attachments))
    note(
        "WARNING", "DI_INITIAL_SETUP_UNKNOWN",
        "原始数据不含每台生产机初始 setup；保持显式 unknown/fallback",
        machines=sum(
            len(item.resource_instance_ids)
            for item in machine_templates
            if item.location_id != "Delay"
        ),
    )
    note("WARNING", "DI_INITIAL_CQT_UNKNOWN", "初始 WIP 的历史 CQT 起点不可恢复", relationships=unknown_cqt)
    note("WARNING", "DI_INITIAL_DEDICATION_UNKNOWN", "初始 WIP 的历史 dedication machine 不可恢复", relationships=unknown_dedication)
    note("WARNING", "DI_INITIAL_WAFER_PM_COUNTER_UNKNOWN", "原始数据不含初始 wafer-PM counter；0 只能作为本地假设")

    errors = tuple(item for item in audit if item.severity == "ERROR")
    if errors:
        raise LoaderError(errors)

    physical_machine_count = sum(len(item.resource_instance_ids) for item in machine_templates if item.location_id != "Delay")
    virtual_delay_resource_count = sum(len(item.resource_instance_ids) for item in machine_templates if item.location_id == "Delay")
    stats: dict[str, Any] = {
        "products": len(products), "routes": len(routes), "operations": len(operations),
        "tool_table_rows": len(tool_rows), "tool_groups": len(family_machines) - int(virtual_delay_resource_count > 0), "physical_machines": physical_machine_count,
        "virtual_delay_resources": virtual_delay_resource_count,
        "release_templates": len(releases), "configured_future_lot_capacity": sum(item.repeat_limit * item.lots_per_repeat for item in releases),
        "initial_wip_lots": len(initial_wip), "batch_operations": len(batch_ops), "cqt_constraints": len(cqt_ops),
        "cross_step_cqt_constraints": sum((op.cqt_target_step_id or 0) > op.step_id + 1 for op in cqt_ops),
        "dedication_constraints": len(dedication_ops),
        "sampling_field_operations": len(sample_field_ops),
        "stochastic_sampling_operations": len(sample_ops),
        "rework_operations": len(rework_ops),
        "cascading_operations": len(cascading_ops), "setup_transitions": setup_transition_count,
        "failure_calendars": len(down_calendars), "pm_calendars": len(pm_calendars), "calendar_attachments": len(attachments),
        "transport_pairs": len(transport), "unknown_initial_cqt_relationships": unknown_cqt,
        "unknown_initial_dedication_relationships": unknown_dedication,
        "raw_rows": {"part": len(part_rows), "route": raw_operation_count, "tool": len(tool_rows), "order": len(order_rows), "wip": len(wip_rows)},
        "parsed_rows": {"products": len(products), "operations": len(operations), "tool_templates": len(machine_templates), "release_templates": len(releases), "initial_wip": len(initial_wip)},
    }
    static_model = SMT2020StaticModel(
        tuple(products), tuple(routes), tuple(machine_templates), tuple(releases),
        tuple(initial_wip), setup_transitions, tuple(setup_group_members), transport,
        down_calendars, pm_calendars, tuple(attachments),
    )

    scenario = _build_validation_slice(static_model, manifest, config) if config.mode == "validation_slice" else None
    evidence = (
        SemanticEvidence("entity-fields", "A", "raw SMT2020 tables", "字段和值直接读取"),
        SemanticEvidence("qualification", "B", "route.STNFAM + tool.STNFAM/STNQTY", "推导具体物理机资格集合"),
        SemanticEvidence("uniform(m,w)", "D", "PySCFabSim reference implementation", "均值与全宽"),
        SemanticEvidence("initial-state-fallbacks", "E/F", "project contract + absent raw history", "显式假设并保留 unknown audit"),
    )
    return LoadedScenario(scenario, static_model, manifest, tuple(audit), evidence, MappingProxyType(stats), config)


def _build_validation_slice(model: SMT2020StaticModel, manifest: DatasetManifest, config: LoaderConfig) -> Scenario:
    """从真实记录选取单工序闭包；均值投影只用于 API 兼容 smoke。"""

    product_by_route = {item.route_id: item for item in model.products}
    candidates = [
        op for route in model.routes for op in route.operations
        if op.processing_basis == "per_lot" and op.required_setup is None
        and op.batch_min_wafers is None and op.sample_percent is None
        and op.rework_step_id is None and op.cqt_target_step_id is None
        and op.dedication_target_step_id is None and op.batch_interval_minutes is None
        and op.part_interval_minutes is None and op.eligible_machine_ids
        and (config.validation_product_id is None or product_by_route[op.route_id].product_id == config.validation_product_id)
    ]
    if not candidates:
        raise ValueError("找不到满足 validation slice 约束的真实工序")
    op = sorted(candidates, key=lambda item: (item.route_id, item.step_id))[0]
    machine_id = op.eligible_machine_ids[0]
    provenance = DatasetProvenanceSpec(
        manifest.dataset_family, manifest.model_name, manifest.manifest_hash,
        manifest.parser_schema_version, manifest.loader_version, SMT2020_LOADER_CONTRACT_VERSION,
        config.provenance_items(),
        tuple(SourceFileProvenance(item.relative_path, item.size_bytes, item.sha256) for item in manifest.files),
    )
    duration = op.processing.parameter_1_minutes
    return Scenario(
        scenario_id=f"{manifest.model_name}:validation-slice:{op.route_id}:{op.step_id}",
        dataset_version=manifest.dataset_version,
        machines=(MachineSpec(machine_id),),
        lots=(LotSpec(f"VALIDATION-{product_by_route[op.route_id].product_id}", 0, (OperationSpec(1, duration, (machine_id,), route_id=op.route_id, tool_group_id=op.tool_family_id),), quantity_wafers=25),),
        termination_mode="fixed_horizon", horizon=duration + 1,
        dataset_provenance=provenance,
    )
