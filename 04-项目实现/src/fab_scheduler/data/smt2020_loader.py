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
    TimeDistributionSpec,
    TransportSpec,
)


SMT2020_LOADER_VERSION = "0.1.3"
SMT2020_LOADER_CONTRACT_VERSION = "0.1.3"
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
    rework_scope: str | None
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
    source_row: int
    order_id: str

    def __post_init__(self) -> None:
        if not self.lot_prefix:
            raise ValueError("release lot_prefix 不能为空")
        if not self.product_id:
            raise ValueError("release product_id 不能为空")
        if not self.order_id:
            raise ValueError("release order_id 不能为空")
        if self.source_row <= 0:
            raise ValueError("release source_row 必须为正")
        if self.repeat_limit <= 0:
            raise ValueError("release repeat_limit 必须为正")
        if self.lots_per_repeat <= 0:
            raise ValueError("release lots_per_repeat 必须为正")
        if not isinstance(self.hot_lot, bool):
            raise ValueError("release hot_lot 必须为 bool")
        if self.quantity_wafers <= 0:
            raise ValueError("release quantity_wafers 必须为正")
        if not isfinite(self.first_release_minutes) or self.first_release_minutes < 0:
            raise ValueError("release first_release_minutes 必须为有限非负数")
        if self.relative_due_minutes is not None and self.relative_due_minutes < 0:
            raise ValueError("release relative_due_minutes 不能为负")


@dataclass(frozen=True, slots=True)
class InitialWipDefinition:
    lot_id: str
    product_id: str
    quantity_wafers: int
    current_step_id: int
    due_minutes: float | None
    priority: int = 0
    order_id: str = ""
    hot_lot: bool | None = None
    source_start_minutes: float | None = None
    source_trace: str | None = None

    @property
    def trace(self) -> str | None:
        """兼容 Data Contract 中的 TRACE/source_metadata 称呼。"""

        return self.source_trace


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
    mode: Literal["audit", "validation_slice", "transport_validation_slice", "release_validation_slice"] = "audit"
    validation_product_id: str | None = None
    validation_transport_pair: tuple[str, str] | None = None
    validation_release_lot_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"audit", "validation_slice", "transport_validation_slice", "release_validation_slice"}:
            raise ValueError(f"不支持的 loader mode：{self.mode}")
        if self.mode == "audit" and (
            self.validation_product_id is not None
            or self.validation_transport_pair is not None
            or self.validation_release_lot_prefix is not None
        ):
            raise ValueError("audit mode 不接受 validation selector")
        if (
            self.validation_transport_pair is not None
            and self.mode != "transport_validation_slice"
        ):
            raise ValueError(
                "validation_transport_pair 仅适用于 transport_validation_slice"
            )
        if self.validation_transport_pair is not None:
            if len(self.validation_transport_pair) != 2:
                raise ValueError("validation_transport_pair 必须包含两个 location")
            if any(not location for location in self.validation_transport_pair):
                raise ValueError("validation_transport_pair 的 location 不能为空")
        if self.validation_product_id is not None and self.mode not in {
            "validation_slice", "transport_validation_slice", "release_validation_slice"
        }:
            raise ValueError("validation_product_id 仅适用于 validation slice mode")
        if self.validation_release_lot_prefix is not None and self.mode != "release_validation_slice":
            raise ValueError("validation_release_lot_prefix 仅适用于 release_validation_slice")
        if self.validation_release_lot_prefix == "":
            raise ValueError("validation_release_lot_prefix 不能为空")

    def provenance_items(self) -> tuple[tuple[str, str], ...]:
        return (
            ("mode", self.mode),
            ("validation_product_id", self.validation_product_id or ""),
            (
                "validation_transport_pair",
                "->".join(self.validation_transport_pair)
                if self.validation_transport_pair is not None
                else "",
            ),
            ("validation_release_lot_prefix", self.validation_release_lot_prefix or ""),
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


def to_runtime_distribution(definition: DistributionDefinition) -> TimeDistributionSpec:
    """把已统一为分钟的原始分布映射为唯一 runtime 定义。"""

    return TimeDistributionSpec(
        kind=definition.kind,  # type: ignore[arg-type]
        mean_minutes=definition.parameter_1_minutes,
        width_minutes=definition.parameter_2_minutes or 0.0,
    )


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
            sample_raw = row["StepPercent"].strip()
            sample_percent: float | None = None
            if sample_raw:
                try:
                    sample_percent = float(sample_raw)
                except ValueError:
                    note(
                        "ERROR", "DI_INVALID_SAMPLING_PERCENT",
                        "StepPercent 必须是数字", route=product.route_id,
                        step=step, value=sample_raw,
                    )
                else:
                    if not 0 < sample_percent <= 100:
                        note(
                            "ERROR", "DI_INVALID_SAMPLING_PERCENT",
                            "StepPercent 必须位于 (0,100]", route=product.route_id,
                            step=step, value=sample_raw,
                        )

            rework_values = tuple(row[field].strip() for field in ("RWKSTEP", "REWORK", "RWKTYPE"))
            rework_present = sum(bool(value) for value in rework_values)
            if rework_present not in (0, 3):
                note(
                    "ERROR", "DI_INCOMPLETE_REWORK_FIELDS",
                    "RWKSTEP/REWORK/RWKTYPE 必须成组出现", route=product.route_id,
                    step=step,
                )
            rework_step_id: int | None = None
            rework_percent: float | None = None
            rework_scope: str | None = None
            if rework_present == 3:
                try:
                    rework_step_id = _int(rework_values[0])
                except ValueError:
                    note(
                        "ERROR", "DI_INVALID_REWORK_STEP",
                        "RWKSTEP 必须是整数", route=product.route_id,
                        step=step, value=rework_values[0],
                    )
                try:
                    rework_percent = float(rework_values[1])
                except ValueError:
                    note(
                        "ERROR", "DI_INVALID_REWORK_PERCENT",
                        "REWORK 必须是数字", route=product.route_id,
                        step=step, value=rework_values[1],
                    )
                else:
                    if not 0 < rework_percent <= 100:
                        note(
                            "ERROR", "DI_INVALID_REWORK_PERCENT",
                            "REWORK 必须位于 (0,100]", route=product.route_id,
                            step=step, value=rework_values[1],
                        )
                rework_scope = rework_values[2].lower()
                if rework_scope != "lot":
                    note(
                        "BLOCKER", "DI_UNSUPPORTED_REWORK_SCOPE",
                        "当前仅能静态识别 RWKTYPE=lot", route=product.route_id,
                        step=step, scope=rework_scope,
                    )

            op = OperationDefinition(
                product.route_id, step, row["DESC"], row["STNFAM"], eligible, processing, row["PTPER"],
                _int(row["BATCHMN"]) if row["BATCHMN"] else None,
                _int(row["BATCHMX"]) if row["BATCHMX"] else None,
                row["SETUP"] or None,
                _minutes(row["STIME"], row["STUNITS"]) if row["STIME"] else None,
                _minutes(row["BatchInterval"], row["BatchIntUnits"]) if row["BatchInterval"] else None,
                _minutes(row["PartInterval"], row["PartIntUnits"]) if row["PartInterval"] else None,
                sample_percent,
                rework_step_id,
                rework_percent,
                rework_scope,
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
            if op.rework_step_id is not None and (
                op.rework_step_id not in known or op.rework_step_id >= op.step_id
            ):
                note(
                    "ERROR", "DI_INVALID_REWORK_LINK",
                    "RWKSTEP 必须引用同一路线中严格早于 source 的工序",
                    route=product.route_id, source=op.step_id,
                    target=op.rework_step_id,
                )
        route = RouteDefinition(product.route_id, product.route_file, tuple(operations))
        routes.append(route)
        route_lookup[product.route_id] = route

    base_times = [_parse_datetime(row["START"]) for row in order_rows + wip_rows if row["START"]]
    epoch = min(base_times)
    releases: list[ReleaseTemplateDefinition] = []
    release_lots: set[str] = set()
    release_orders: set[str] = set()
    for source_row, row in enumerate(order_rows, start=2):
        lot_prefix = row["LOT"]
        order_id = row["ORDER"]
        if lot_prefix in release_lots:
            note("ERROR", "DI_DUPLICATE_RELEASE_LOT", "order LOT 必须唯一", lot=lot_prefix, source_row=source_row)
        release_lots.add(lot_prefix)
        if order_id in release_orders:
            note("ERROR", "DI_DUPLICATE_RELEASE_ORDER", "order ORDER 必须唯一", order=order_id, source_row=source_row)
        release_orders.add(order_id)
        if row["PART"] not in product_ids:
            note("ERROR", "DI_UNKNOWN_RELEASE_PRODUCT", "order 引用未知产品", lot=lot_prefix, product=row["PART"])
        if row["HOTLOT"].lower() not in {"yes", "no"}:
            note("ERROR", "DI_INVALID_RELEASE_HOTLOT", "order HOTLOT 仅支持 yes/no", lot=lot_prefix, value=row["HOTLOT"])
            continue
        try:
            repeat_limit = _int(row["RPT#"])
            lots_per_repeat = _int(row["LOTSPERRPT"])
        except ValueError as exc:
            note("ERROR", "DI_INVALID_RELEASE_REPEAT", str(exc), lot=lot_prefix, source_row=source_row)
            continue
        if repeat_limit <= 0:
            note("ERROR", "DI_INVALID_RELEASE_REPEAT", "RPT# 必须为正", lot=lot_prefix, value=repeat_limit)
        if lots_per_repeat <= 0:
            note("ERROR", "DI_INVALID_RELEASE_LOTS_PER_REPEAT", "LOTSPERRPT 必须为正", lot=lot_prefix, value=lots_per_repeat)
        try:
            start = _parse_datetime(row["START"])
            due = _parse_datetime(row["DUE"]) if row["DUE"] else None
            interval = parse_distribution(row["RDIST"], row["REPEAT"], "", row["RUNITS"])
        except (KeyError, ValueError) as exc:
            note("ERROR", "DI_INVALID_RELEASE_FIELDS", str(exc), lot=lot_prefix, source_row=source_row)
            continue
        relative_due = (due - start).total_seconds() / 60 if due else None
        if relative_due is not None and relative_due < 0:
            note("ERROR", "DI_INVALID_RELEASE_DUE", "DUE 必须不早于 START", lot=lot_prefix, source_row=source_row)
        try:
            releases.append(ReleaseTemplateDefinition(
                lot_prefix=lot_prefix,
                product_id=row["PART"],
                priority=_int(row["PRIOR"]),
                quantity_wafers=_int(row["PIECES"]),
                first_release_minutes=(start - epoch).total_seconds() / 60,
                interval=interval,
                repeat_limit=repeat_limit,
                lots_per_repeat=lots_per_repeat,
                relative_due_minutes=relative_due,
                hot_lot=row["HOTLOT"].lower() == "yes",
                source_row=source_row,
                order_id=order_id,
            ))
        except ValueError as exc:
            note("ERROR", "DI_INVALID_RELEASE_FIELDS", str(exc), lot=lot_prefix, source_row=source_row)

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
        start = _parse_datetime(row["START"])
        due = _parse_datetime(row["DUE"]) if row["DUE"] else None
        hot_lot_raw = row["HOTLOT"].lower()
        if hot_lot_raw not in {"", "yes", "no"}:
            note("ERROR", "DI_INVALID_WIP_HOTLOT", "WIP HOTLOT 仅支持 yes/no 或空值", lot=row["LOT"], value=row["HOTLOT"])
            hot_lot: bool | None = None
        else:
            hot_lot = None if not hot_lot_raw else hot_lot_raw == "yes"
        initial_wip.append(InitialWipDefinition(
            lot_id=row["LOT"], product_id=row["PART"], quantity_wafers=_int(row["PIECES"]),
            current_step_id=current, due_minutes=(due - epoch).total_seconds() / 60 if due else None,
            priority=_int(row["PRIOR"]), order_id=row["ORDER"], hot_lot=hot_lot,
            source_start_minutes=(start - epoch).total_seconds() / 60,
            source_trace=row["TRACE"] or None,
        ))
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
    rework_scope_counts = {
        scope: sum(op.rework_scope == scope for op in rework_ops)
        for scope in sorted({op.rework_scope for op in rework_ops if op.rework_scope is not None})
    }
    cascading_ops = tuple(op for op in operations if op.batch_interval_minutes is not None or op.part_interval_minutes is not None)
    location_by_machine = {
        machine_id: template.location_id
        for template in machine_templates
        for machine_id in template.resource_instance_ids
    }

    def operation_location(operation: OperationDefinition) -> str | None:
        locations = {
            location_by_machine[machine_id]
            for machine_id in operation.eligible_machine_ids
            if machine_id in location_by_machine
        }
        return next(iter(locations)) if len(locations) == 1 else None

    route_location_transition_counts: dict[tuple[str, str], int] = {}
    ambiguous_location_transitions = 0
    for route in routes:
        for first, second in zip(route.operations, route.operations[1:]):
            from_location = operation_location(first)
            to_location = operation_location(second)
            if from_location is None or to_location is None:
                ambiguous_location_transitions += 1
                continue
            key = (from_location, to_location)
            route_location_transition_counts[key] = (
                route_location_transition_counts.get(key, 0) + 1
            )
    configured_transport_pairs = {
        (item.from_location, item.to_location) for item in transport
    }
    unconfigured_transport_transition_counts = {
        key: count
        for key, count in route_location_transition_counts.items()
        if key not in configured_transport_pairs
    }

    note("INFO", "DI_PROCESSING_DISTRIBUTION_RUNTIME_SUPPORTED", "constant/uniform 已进入统一分钟制 sampler；不再使用均值投影执行 validation slice", affected=len(operations))
    note("INFO", "DI_PROCESSING_BASIS_RUNTIME_SUPPORTED", "per_lot/per_piece/per_batch 已进入 processing duration resolver；Part/BatchInterval 仍单列 blocker", affected=sum(op.processing_basis != "per_lot" for op in operations))
    note("BLOCKER", "DI_UNSUPPORTED_LOAD_UNLOAD_CASCADE", "load/unload、STNCAP 与 Part/BatchInterval 尚未执行", affected=len(cascading_ops))
    unsupported_release_templates = tuple(
        item for item in releases
        if (
            item.interval.kind != "constant"
            or item.interval.raw_unit != "min"
            or item.lots_per_repeat != 1
        )
    )
    if not unsupported_release_templates and releases:
        note(
            "INFO", "DI_RELEASE_TEMPLATE_RUNTIME_SUPPORTED",
            "release template 当前均为 constant/min 且 LOTSPERRPT=1；runtime 按 horizon 惰性生成",
            templates=len(releases),
        )
    else:
        note(
            "BLOCKER", "DI_UNSUPPORTED_RELEASE_TEMPLATES",
            "当前 release runtime 仅支持 RDIST=constant、RUNITS=min 且 LOTSPERRPT=1；其他值不得隐式降级",
            templates=len(unsupported_release_templates),
        )
    note("INFO", "DI_TRANSPORT_RUNTIME_SUPPORTED", "transport 表已解析并由 runtime 支持；缺失 location pair 仍显式审计", pairs=len(transport))
    if unconfigured_transport_transition_counts:
        note(
            "WARNING",
            "DI_TRANSPORT_ROUTE_PAIRS_UNCONFIGURED",
            "route 中存在 fromto 表未配置的 location pair；runtime 按契约使用零时长并逐次记录",
            transitions=sum(unconfigured_transport_transition_counts.values()),
            pairs={
                f"{from_location}->{to_location}": count
                for (from_location, to_location), count
                in sorted(unconfigured_transport_transition_counts.items())
            },
        )
    if ambiguous_location_transitions:
        note(
            "BLOCKER",
            "DI_AMBIGUOUS_OPERATION_LOCATION",
            "相邻工序的设备资格不能唯一解析为 location，无法确定 transport pair",
            transitions=ambiguous_location_transitions,
        )
    if sample_ops:
        note("BLOCKER", "DI_UNSUPPORTED_SAMPLING", "StepPercent 抽样跳步尚未实现", affected=len(sample_ops))
    if rework_ops:
        note("BLOCKER", "DI_UNSUPPORTED_REWORK", "RWKSTEP/REWORK 路线回跳尚未实现", affected=len(rework_ops))
    if setup_minrun_count:
        note("BLOCKER", "DI_UNSUPPORTED_SETUP_MINRUN", "setupgrp.MINRUN 尚未进入 runtime", affected=setup_minrun_count)
    note("BLOCKER", "DI_MISSING_BATCH_DECISION_CONFIG", "B_target/T_max 不在 raw 数据中；正式场景需版本化 loader_config", batch_operations=len(batch_ops))
    if any(item.interval and item.interval.kind == "exponential" for item in down_calendars):
        note("INFO", "DI_EXPONENTIAL_FAILURE_RUNTIME_SUPPORTED", "downcal exponential 已按均值参数进入共享 sampler", calendars=len(down_calendars))
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
        "release_distribution_kinds": {kind: sum(item.interval.kind == kind for item in releases) for kind in sorted({item.interval.kind for item in releases})},
        "unsupported_release_templates": len(unsupported_release_templates),
        "unsupported_release_lotsp_per_repeat_count": sum(item.lots_per_repeat != 1 for item in releases),
        "unsupported_release_unit_count": sum(item.interval.raw_unit != "min" for item in releases),
        "initial_wip_lots": len(initial_wip), "batch_operations": len(batch_ops), "cqt_constraints": len(cqt_ops),
        "cross_step_cqt_constraints": sum((op.cqt_target_step_id or 0) > op.step_id + 1 for op in cqt_ops),
        "dedication_constraints": len(dedication_ops),
        "sampling_field_operations": len(sample_field_ops),
        "stochastic_sampling_operations": len(sample_ops),
        "rework_operations": len(rework_ops),
        "rework_scope_counts": rework_scope_counts,
        "cascading_operations": len(cascading_ops), "setup_transitions": setup_transition_count,
        "route_location_transition_counts": {
            f"{from_location}->{to_location}": count
            for (from_location, to_location), count
            in sorted(route_location_transition_counts.items())
        },
        "unconfigured_transport_transition_counts": {
            f"{from_location}->{to_location}": count
            for (from_location, to_location), count
            in sorted(unconfigured_transport_transition_counts.items())
        },
        "ambiguous_location_transitions": ambiguous_location_transitions,
        "processing_distribution_kinds": {kind: sum(op.processing.kind == kind for op in operations) for kind in sorted({op.processing.kind for op in operations})},
        "processing_basis_counts": {basis: sum(op.processing_basis == basis for op in operations) for basis in sorted({op.processing_basis for op in operations})},
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

    if config.mode == "validation_slice":
        scenario = _build_validation_slice(static_model, manifest, config)
    elif config.mode == "transport_validation_slice":
        scenario = _build_transport_validation_slice(static_model, manifest, config)
    elif config.mode == "release_validation_slice":
        scenario = _build_release_validation_slice(static_model, manifest, config)
    else:
        scenario = None
    evidence = (
        SemanticEvidence("entity-fields", "A", "raw SMT2020 tables", "字段和值直接读取"),
        SemanticEvidence("qualification", "B", "route.STNFAM + tool.STNFAM/STNQTY", "推导具体物理机资格集合"),
        SemanticEvidence("uniform(m,w)", "D", "PySCFabSim reference implementation", "均值与全宽"),
        SemanticEvidence(
            "transport-runtime",
            "A/B/D/E",
            "fromto + route/tool locations + Data/Simulation Contract",
            "外生无容量；未配置 pair 零时长并显式审计",
        ),
        SemanticEvidence(
            "release-runtime",
            "A/B/D/E",
            "order + part/route + release runtime reference + local frozen contract",
            "保留真实模板与 RPT#，仅在 fixed-horizon 下按 constant interval 惰性生成；slice 的 ID 与 horizon 可复现",
        ),
        SemanticEvidence("initial-state-fallbacks", "E/F", "project contract + absent raw history", "显式假设并保留 unknown audit"),
    )
    return LoadedScenario(scenario, static_model, manifest, tuple(audit), evidence, MappingProxyType(stats), config)


def _build_validation_slice(model: SMT2020StaticModel, manifest: DatasetManifest, config: LoaderConfig) -> Scenario:
    """从真实记录选取单工序闭包；真实分布仅在 commit 后由 runtime 抽样。"""

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
    distribution = to_runtime_distribution(op.processing)
    duration = distribution.mean_minutes
    return Scenario(
        scenario_id=f"{manifest.model_name}:validation-slice:{op.route_id}:{op.step_id}",
        dataset_version=manifest.dataset_version,
        machines=(MachineSpec(machine_id),),
        lots=(LotSpec(f"VALIDATION-{product_by_route[op.route_id].product_id}", 0, (OperationSpec(1, duration, (machine_id,), route_id=op.route_id, tool_group_id=op.tool_family_id, processing_distribution=distribution, processing_basis=op.processing_basis),), quantity_wafers=25),),
        termination_mode="fixed_horizon", horizon=duration + distribution.width_minutes / 2 + 1,
        dataset_provenance=provenance,
    )


def _build_release_validation_slice(
    model: SMT2020StaticModel,
    manifest: DatasetManifest,
    config: LoaderConfig,
) -> Scenario:
    """构造一个真实 release template 的惰性投放 slice。

    该 slice 故意不预展开 lots；真实的 RPT#（通常为 200000）保留在模板中，
    由 runtime 按 fixed horizon 生成首个、第二个和第三个 release。
    """

    # 局部导入使旧版领域模型仍可加载静态 audit；只有真正请求该 mode 时才
    # 要求 ReleaseTemplateSpec 已由领域层提供。
    from fab_scheduler.domain.models import ReleaseTemplateSpec

    product_by_id = {item.product_id: item for item in model.products}
    templates = [
        item for item in model.release_templates
        if config.validation_product_id is None
        or item.product_id == config.validation_product_id
        if config.validation_release_lot_prefix is None
        or item.lot_prefix == config.validation_release_lot_prefix
    ]
    supported_templates = [
        item for item in templates
        if (
            item.interval.kind == "constant"
            and item.interval.raw_unit == "min"
            and item.lots_per_repeat == 1
        )
    ]
    if not supported_templates:
        raise ValueError(
            "找不到满足 release validation slice 的 constant/min/LPR=1 真实订单模板"
        )
    template = sorted(supported_templates, key=lambda item: (item.priority, item.source_row))[0]
    product = product_by_id.get(template.product_id)
    if product is None:
        raise ValueError(f"release template 引用未知产品：{template.product_id}")
    route = next((item for item in model.routes if item.route_id == product.route_id), None)
    if route is None:
        raise ValueError(f"找不到产品 route：{product.route_id}")
    candidates = [
        op for op in route.operations
        if op.processing_basis == "per_lot"
        and op.required_setup is None
        and op.batch_min_wafers is None
        and op.sample_percent is None
        and op.rework_step_id is None
        and op.cqt_target_step_id is None
        and op.dedication_target_step_id is None
        and op.batch_interval_minutes is None
        and op.part_interval_minutes is None
        and op.eligible_machine_ids
    ]
    if not candidates:
        raise ValueError("找不到满足 release validation slice 约束的真实工序")
    op = sorted(candidates, key=lambda item: item.step_id)[0]
    machine_id = op.eligible_machine_ids[0]
    processing = to_runtime_distribution(op.processing)
    provenance = DatasetProvenanceSpec(
        manifest.dataset_family,
        manifest.model_name,
        manifest.manifest_hash,
        manifest.parser_schema_version,
        manifest.loader_version,
        SMT2020_LOADER_CONTRACT_VERSION,
        config.provenance_items(),
        tuple(SourceFileProvenance(item.relative_path, item.size_bytes, item.sha256) for item in manifest.files),
    )
    operation = OperationSpec(
        op.step_id,
        processing.mean_minutes,
        (machine_id,),
        route_id=route.route_id,
        tool_group_id=op.tool_family_id,
        processing_distribution=processing,
        processing_basis=op.processing_basis,
    )
    release_template = ReleaseTemplateSpec(
        template_id=(
            f"{manifest.model_name}:release-template:{template.source_row:04d}"
        ),
        source_row=template.source_row,
        lot_prefix=template.lot_prefix,
        product_id=template.product_id,
        order_id=template.order_id,
        operations=(operation,),
        first_release_time=template.first_release_minutes,
        interval=to_runtime_distribution(template.interval),
        repeat_limit=template.repeat_limit,
        lots_per_repeat=template.lots_per_repeat,
        relative_due_minutes=template.relative_due_minutes,
        priority=template.priority,
        hot_lot=template.hot_lot,
        quantity_wafers=template.quantity_wafers,
    )
    return Scenario(
        scenario_id=(
            f"{manifest.model_name}:release-validation-slice:"
            f"{template.source_row}:{route.route_id}:{op.step_id}"
        ),
        dataset_version=manifest.dataset_version,
        machines=(MachineSpec(machine_id),),
        lots=(),
        termination_mode="fixed_horizon",
        horizon=template.first_release_minutes + 2 * template.interval.parameter_1_minutes,
        release_templates=(release_template,),
        dataset_provenance=provenance,
    )


def _build_transport_validation_slice(
    model: SMT2020StaticModel,
    manifest: DatasetManifest,
    config: LoaderConfig,
) -> Scenario:
    """从真实 route 选取连续且无 route-level blocker 字段的 transport slice。"""

    product_by_route = {item.route_id: item for item in model.products}
    location_by_machine = {
        machine_id: template.location_id
        for template in model.machine_templates
        for machine_id in template.resource_instance_ids
    }

    def operation_location(operation: OperationDefinition) -> str | None:
        locations = {
            location_by_machine[machine_id]
            for machine_id in operation.eligible_machine_ids
            if machine_id in location_by_machine
        }
        return next(iter(locations)) if len(locations) == 1 else None

    candidates: list[tuple[RouteDefinition, OperationDefinition, OperationDefinition]] = []
    requested_pair = config.validation_transport_pair or ("Fab", "Fab")
    for route in model.routes:
        product = product_by_route[route.route_id]
        if config.validation_product_id is not None and product.product_id != config.validation_product_id:
            continue
        for first, second in zip(route.operations, route.operations[1:]):
            if (
                first.processing_basis == "per_lot"
                and second.processing_basis == "per_lot"
                and first.required_setup is None
                and second.required_setup is None
                and first.batch_min_wafers is None
                and second.batch_min_wafers is None
                and first.sample_percent is None
                and second.sample_percent is None
                and first.rework_step_id is None
                and second.rework_step_id is None
                and first.cqt_target_step_id is None
                and second.cqt_target_step_id is None
                and first.dedication_target_step_id is None
                and second.dedication_target_step_id is None
                and first.batch_interval_minutes is None
                and second.batch_interval_minutes is None
                and first.part_interval_minutes is None
                and second.part_interval_minutes is None
                and operation_location(first) == requested_pair[0]
                and operation_location(second) == requested_pair[1]
            ):
                candidates.append((route, first, second))
    if not candidates:
        raise ValueError(
            "找不到连续 "
            f"{requested_pair[0]}→{requested_pair[1]} transport validation slice"
        )
    route, first, second = sorted(
        candidates,
        key=lambda item: (item[0].route_id, item[1].step_id),
    )[0]
    product = product_by_route[route.route_id]
    source_machine = first.eligible_machine_ids[0]
    target_machine = second.eligible_machine_ids[0]
    machines_by_id = {
        machine_id: MachineSpec(
            machine_id,
            location_id=location_by_machine[machine_id],
        )
        for machine_id in (source_machine, target_machine)
    }
    first_distribution = to_runtime_distribution(first.processing)
    second_distribution = to_runtime_distribution(second.processing)
    transport_specs = tuple(
        TransportSpec(
            item.from_location,
            item.to_location,
            to_runtime_distribution(item.duration),
        )
        for item in model.transport
    )
    transport_upper = max(
        (
            item.duration.mean_minutes + item.duration.width_minutes / 2
            for item in transport_specs
        ),
        default=0.0,
    )
    horizon = (
        first_distribution.mean_minutes + first_distribution.width_minutes / 2
        + second_distribution.mean_minutes + second_distribution.width_minutes / 2
        + transport_upper + 1
    )
    provenance = DatasetProvenanceSpec(
        manifest.dataset_family,
        manifest.model_name,
        manifest.manifest_hash,
        manifest.parser_schema_version,
        manifest.loader_version,
        SMT2020_LOADER_CONTRACT_VERSION,
        config.provenance_items(),
        tuple(
            SourceFileProvenance(item.relative_path, item.size_bytes, item.sha256)
            for item in manifest.files
        ),
    )
    lot = LotSpec(
        f"TRANSPORT-VALIDATION-{product.product_id}",
        0,
        (
            OperationSpec(
                first.step_id,
                first_distribution.mean_minutes,
                (source_machine,),
                route_id=route.route_id,
                tool_group_id=first.tool_family_id,
                processing_distribution=first_distribution,
            ),
            OperationSpec(
                second.step_id,
                second_distribution.mean_minutes,
                (target_machine,),
                route_id=route.route_id,
                tool_group_id=second.tool_family_id,
                processing_distribution=second_distribution,
            ),
        ),
        quantity_wafers=25,
    )
    return Scenario(
        scenario_id=(
            f"{manifest.model_name}:transport-validation-slice:"
            f"{route.route_id}:{first.step_id}-{second.step_id}"
        ),
        dataset_version=manifest.dataset_version,
        machines=tuple(machines_by_id.values()),
        lots=(lot,),
        termination_mode="fixed_horizon",
        horizon=horizon,
        transport_specs=transport_specs,
        dataset_provenance=provenance,
    )
