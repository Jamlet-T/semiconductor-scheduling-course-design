"""SMT2020 原始表到静态领域模型及受控验证 Scenario 的正式入口。"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from math import ceil, isfinite
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping

from fab_scheduler.data.manifest import DatasetManifest, build_dataset_manifest
from fab_scheduler.domain.models import (
    BatchSpec,
    CalendarPMSpec,
    DatasetProvenanceSpec,
    LotSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    SourceFileProvenance,
    TimeDistributionSpec,
    TransportSpec,
)


SMT2020_LOADER_VERSION = "0.1.7"
SMT2020_LOADER_CONTRACT_VERSION = "0.1.7"
Severity = Literal["ERROR", "BLOCKER", "WARNING", "INFO"]


@dataclass(frozen=True, slots=True)
class BatchDecisionConfig:
    """版本化的 batch 决策参数；raw SMT2020 不含这些值，不能隐式补齐。"""

    config_id: str
    version: str
    model_id: str
    manifest_hash: str
    target_rule: Literal["raw_batch_max"]
    max_wait_minutes: float
    source_path: Path | None = None
    source_sha256: str | None = None
    source_schema_version: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("config_id", "version", "model_id", "manifest_hash"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"batch decision {field_name} 必须为非空字符串")
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.version) is None:
            raise ValueError("batch decision version 必须为 x.y.z")
        if self.target_rule != "raw_batch_max":
            raise ValueError(
                "batch decision target_rule 仅支持 raw_batch_max；禁止隐式选择 target"
            )
        if (
            len(self.manifest_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.manifest_hash.lower())
        ):
            raise ValueError("batch decision manifest_hash 必须为 64 位十六进制 SHA-256")
        if (
            isinstance(self.max_wait_minutes, bool)
            or not isinstance(self.max_wait_minutes, (int, float))
            or not isfinite(float(self.max_wait_minutes))
            or self.max_wait_minutes < 0
        ):
            raise ValueError("batch decision max_wait_minutes 必须为有限非负数")
        if self.source_path is not None and not isinstance(self.source_path, Path):
            raise TypeError("batch decision source_path 必须是 Path")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "config_id": self.config_id,
            "version": self.version,
            "model_id": self.model_id,
            "manifest_hash": self.manifest_hash,
            "target_rule": self.target_rule,
            "max_wait_minutes": float(self.max_wait_minutes),
        }

    @property
    def canonical_hash(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def provenance_items(self) -> tuple[tuple[str, str], ...]:
        items = (
            ("batch_decision_config_id", self.config_id),
            ("batch_decision_config_version", self.version),
            ("batch_decision_config_model_id", self.model_id),
            ("batch_decision_config_manifest_hash", self.manifest_hash),
            ("batch_decision_config_target_rule", self.target_rule),
            ("batch_decision_config_max_wait_minutes", str(float(self.max_wait_minutes))),
            ("batch_decision_config_hash", self.canonical_hash),
            ("batch_decision_config_source_sha256", self.source_sha256 or ""),
            ("batch_decision_config_source_schema_version", self.source_schema_version or ""),
        )
        return items


def load_batch_decision_config(path: Path, model_id: str) -> BatchDecisionConfig:
    """从显式版本化 artifact 读取一套模型参数；不在 loader 内自动选择。"""

    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "purpose", "evidence_level", "target_rule",
        "max_wait_minutes", "profiles",
    }:
        raise ValueError("batch decision artifact 顶层 schema 不匹配")
    if payload["schema_version"] != "1.0.0" or payload["purpose"] != "data_integration_runtime_profile_not_performance_recommendation":
        raise ValueError("batch decision artifact schema/purpose 不匹配")
    if payload["evidence_level"] != "E_local_modeling_choice_not_raw_SMT2020":
        raise ValueError("batch decision artifact 必须明确标注 E 级本地假设")
    profiles = payload["profiles"]
    if not isinstance(profiles, list) or not profiles or any(
        not isinstance(profile, dict)
        or set(profile) != {"config_id", "version", "model_id", "manifest_hash"}
        for profile in profiles
    ):
        raise ValueError("batch decision artifact profiles schema 不匹配")
    matches = [profile for profile in profiles if profile["model_id"] == model_id]
    if len(matches) != 1:
        raise ValueError("batch decision artifact 必须恰有一个对应 model profile")
    profile = matches[0]
    return BatchDecisionConfig(
        config_id=profile["config_id"],
        version=profile["version"],
        model_id=profile["model_id"],
        manifest_hash=profile["manifest_hash"],
        target_rule=payload["target_rule"],
        max_wait_minutes=payload["max_wait_minutes"],
        source_path=path.resolve(),
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        source_schema_version=payload["schema_version"],
    )


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
    source_row: int | None = None


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
    source_row: int | None = None


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
    source_row: int | None = None

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
    source_row: int | None = None


@dataclass(frozen=True, slots=True)
class CalendarAttachmentDefinition:
    calendar_id: str
    calendar_kind: str
    resource_type: str
    resource_name: str
    first_occurrence: DistributionDefinition | None
    first_occurrence_wafers: int | None
    source_row: int | None = None


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
    mode: Literal[
        "audit", "validation_slice", "transport_validation_slice",
        "release_validation_slice", "sampling_validation_slice",
        "batch_validation_slice", "load_unload_validation_slice",
        "multi_calendar_validation_slice",
    ] = "audit"
    validation_product_id: str | None = None
    validation_transport_pair: tuple[str, str] | None = None
    validation_release_lot_prefix: str | None = None
    validation_sampling_operation: tuple[str, int] | None = None
    validation_batch_operation: tuple[str, int] | None = None
    batch_decision_config: BatchDecisionConfig | None = None

    def __post_init__(self) -> None:
        if self.mode not in {
            "audit", "validation_slice", "transport_validation_slice",
            "release_validation_slice", "sampling_validation_slice",
            "batch_validation_slice", "load_unload_validation_slice",
            "multi_calendar_validation_slice",
        }:
            raise ValueError(f"不支持的 loader mode：{self.mode}")
        if self.mode == "audit" and (
            self.validation_product_id is not None
            or self.validation_transport_pair is not None
            or self.validation_release_lot_prefix is not None
            or self.validation_batch_operation is not None
            or self.validation_sampling_operation is not None
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
        if self.validation_sampling_operation is not None:
            if self.mode != "sampling_validation_slice":
                raise ValueError(
                    "validation_sampling_operation 仅适用于 sampling_validation_slice"
                )
            route_id, step_id = self.validation_sampling_operation
            if not route_id:
                raise ValueError("validation_sampling_operation 的 route 不能为空")
            if isinstance(step_id, bool) or not isinstance(step_id, int) or step_id < 1:
                raise ValueError(
                    "validation_sampling_operation 的 step 必须为正整数"
                )
        if self.validation_batch_operation is not None:
            if self.mode != "batch_validation_slice":
                raise ValueError(
                    "validation_batch_operation 仅适用于 batch_validation_slice"
                )
            route_id, step_id = self.validation_batch_operation
            if not route_id:
                raise ValueError("validation_batch_operation 的 route 不能为空")
            if isinstance(step_id, bool) or not isinstance(step_id, int) or step_id < 1:
                raise ValueError("validation_batch_operation 的 step 必须为正整数")
        if self.batch_decision_config is not None and not isinstance(
            self.batch_decision_config, BatchDecisionConfig
        ):
            raise TypeError("batch_decision_config 必须是 BatchDecisionConfig")
        if self.batch_decision_config is not None and self.mode not in {
            "audit", "batch_validation_slice"
        }:
            raise ValueError(
                "batch_decision_config 仅适用于 audit 或 batch_validation_slice"
            )

    def provenance_items(self) -> tuple[tuple[str, str], ...]:
        items = (
            ("mode", self.mode),
            ("validation_product_id", self.validation_product_id or ""),
            (
                "validation_transport_pair",
                "->".join(self.validation_transport_pair)
                if self.validation_transport_pair is not None
                else "",
            ),
            ("validation_release_lot_prefix", self.validation_release_lot_prefix or ""),
            (
                "validation_sampling_operation",
                (
                    f"{self.validation_sampling_operation[0]}:{self.validation_sampling_operation[1]}"
                    if self.validation_sampling_operation is not None else ""
                ),
            ),
            (
                "validation_batch_operation",
                (
                    f"{self.validation_batch_operation[0]}:{self.validation_batch_operation[1]}"
                    if self.validation_batch_operation is not None else ""
                ),
            ),
        )
        if self.batch_decision_config is None:
            return items + (("batch_decision_config_hash", ""),)
        return items + self.batch_decision_config.provenance_items()


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

        config_dict = asdict(self.loader_config)
        if config_dict["batch_decision_config"] is not None:
            source_path = config_dict["batch_decision_config"]["source_path"]
            config_dict["batch_decision_config"]["source_path"] = (
                str(source_path) if source_path is not None else None
            )
        return {
            "loader_contract_version": SMT2020_LOADER_CONTRACT_VERSION,
            "loader_version": SMT2020_LOADER_VERSION,
            "dataset_manifest": asdict(self.dataset_manifest),
            "loader_config": config_dict,
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
    group_machines: dict[str, list[str]] = {}
    for source_row, row in enumerate(tool_rows, start=2):
        quantity = _int(row["STNQTY"])
        if quantity <= 0:
            note("ERROR", "DI_INVALID_MACHINE_QUANTITY", "STNQTY 必须为正", station=row["STN"])
            continue
        machine_ids = tuple(f"{row['STN']}#{index:04d}" for index in range(1, quantity + 1))
        family_machines.setdefault(row["STNFAM"], []).extend(machine_ids)
        group_machines.setdefault(row["STNGRP"], []).extend(machine_ids)
        machine_templates.append(MachineTemplateDefinition(
            row["STNFAM"], row["STN"], row["STNGRP"], row["STNFAMLOC"], quantity,
            machine_ids,
            row["BATCHCRITF"] or None, row["BATCHPER"] or None, row["SETUPGRP"] or None,
            _minutes(row["LTIME"], row["LTUNITS"]), _minutes(row["ULTIME"], row["ULTUNITS"]), row["STNCAP"] == "2.0",
            source_row=source_row,
        ))

    routes: list[RouteDefinition] = []
    route_lookup: dict[str, RouteDefinition] = {}
    raw_operation_count = 0
    for product in products:
        rows = route_rows_by_file.get(product.route_file, [])
        raw_operation_count += len(rows)
        operations: list[OperationDefinition] = []
        observed_steps: list[int] = []
        for source_row, row in enumerate(rows, start=2):
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
                source_row=source_row,
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
    for source_row, row in enumerate(wip_rows, start=2):
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
            source_row=source_row,
        ))
        unknown_cqt += sum(op.step_id < current <= (op.cqt_target_step_id or -1) for op in route.operations)
        unknown_dedication += sum(op.step_id < current <= (op.dedication_target_step_id or -1) for op in route.operations)

    transport = tuple(TransportDefinition(row["FROMLOC"], row["TOLOC"], parse_distribution(row["DDIST"], row["DTIME"], row["DTIME2"], row["DUNITS"])) for row in transport_rows)
    down_calendars = tuple(
        CalendarDefinition(
            row["DOWNCALNAME"], row["DOWNCALTYPE"],
            parse_distribution(row["MTTFDIST"], row["MTTF"], "", row["MTTFUNITS"]),
            None,
            parse_distribution(row["MTTRDIST"], row["MTTR"], "", row["MTTRUNITS"]),
            source_row=source_row,
        )
        for source_row, row in enumerate(down_rows, start=2)
    )
    pm_calendars = tuple(
        CalendarDefinition(
            row["PMCALNAME"], row["PMCALTYPE"],
            None if row["MTBPMUNITS"] == "pieces" else parse_distribution("constant", row["MTBPM"], "", row["MTBPMUNITS"]),
            _int(row["MTBPM"]) if row["MTBPMUNITS"] == "pieces" else None,
            parse_distribution(row["MTTRDIST"], row["MTTR"], row["MTTR2"], row["MTTRUNITS"]),
            source_row=source_row,
        )
        for source_row, row in enumerate(pm_rows, start=2)
    )
    calendar_ids = {item.calendar_id for item in down_calendars + pm_calendars}
    down_calendar_ids = {item.calendar_id for item in down_calendars}
    pm_calendar_by_id = {item.calendar_id: item for item in pm_calendars}
    attachments: list[CalendarAttachmentDefinition] = []
    for source_row, row in enumerate(attach_rows, start=2):
        if row["CALNAME"] not in calendar_ids:
            note("ERROR", "DI_UNKNOWN_CALENDAR", "attach 引用未知 calendar", calendar=row["CALNAME"])
        if row["CALTYPE"] not in {"down", "pm"}:
            note("ERROR", "DI_UNSUPPORTED_ATTACHMENT_TYPE", "attach.CALTYPE 必须为 down 或 pm", calendar=row["CALNAME"], caltype=row["CALTYPE"])
        if row["RESTYPE"] == "stnfam":
            target_machines = family_machines.get(row["RESNAME"], [])
        elif row["RESTYPE"] == "stngrp":
            target_machines = group_machines.get(row["RESNAME"], [])
        else:
            target_machines = []
            note("ERROR", "DI_UNSUPPORTED_ATTACHMENT_TARGET_TYPE", "attach.RESTYPE 必须为 stnfam 或 stngrp", calendar=row["CALNAME"], restype=row["RESTYPE"])
        valid_target = bool(target_machines)
        if not valid_target:
            note("ERROR", "DI_UNKNOWN_ATTACHMENT_TARGET", "attach 引用未知设备资源", calendar=row["CALNAME"], target=row["RESNAME"])
        if row["CALTYPE"] == "down" and row["CALNAME"] not in down_calendar_ids:
            note("ERROR", "DI_ATTACHMENT_CALENDAR_TYPE_MISMATCH", "down attachment 必须引用 downcal", calendar=row["CALNAME"])
        if row["CALTYPE"] == "pm" and row["CALNAME"] not in pm_calendar_by_id:
            note("ERROR", "DI_ATTACHMENT_CALENDAR_TYPE_MISMATCH", "pm attachment 必须引用 pmcal", calendar=row["CALNAME"])
        if row["FOAUNITS"]:
            first = parse_distribution(row["FOADIST"], row["FOA"], "", row["FOAUNITS"])
            first_wafers = None
        else:
            first = None
            first_wafers = _int(row["FOA"])
        attachments.append(CalendarAttachmentDefinition(
            row["CALNAME"], row["CALTYPE"], row["RESTYPE"], row["RESNAME"],
            first, first_wafers, source_row=source_row,
        ))

    # Static attachment expansion only: this does not construct a runtime Scenario.
    # Keep one record per production physical machine so the audit distinguishes
    # raw attachment rows from the machines affected by those rows.
    location_by_machine = {
        machine_id: template.location_id
        for template in machine_templates
        for machine_id in template.resource_instance_ids
    }
    production_machine_ids = tuple(sorted(
        machine_id
        for machine_id, location in location_by_machine.items()
        if location != "Delay"
    ))
    attachment_machine_counts: dict[str, dict[str, int]] = {
        machine_id: {"failure": 0, "calendar_pm": 0, "wafer_pm": 0}
        for machine_id in production_machine_ids
    }
    expanded_attachment_edges = 0
    for attachment in attachments:
        if attachment.resource_type == "stnfam":
            target_machines = family_machines.get(attachment.resource_name, ())
        elif attachment.resource_type == "stngrp":
            target_machines = group_machines.get(attachment.resource_name, ())
        else:
            target_machines = ()
        if attachment.calendar_kind == "down":
            bucket = "failure"
        elif attachment.calendar_kind == "pm":
            calendar = pm_calendar_by_id.get(attachment.calendar_id)
            if calendar is None:
                continue
            bucket = "calendar_pm" if calendar.interval is not None else "wafer_pm"
        else:
            continue
        for machine_id in target_machines:
            if machine_id in attachment_machine_counts:
                attachment_machine_counts[machine_id][bucket] += 1
                expanded_attachment_edges += 1

    attachment_count_distributions = {
        bucket: {
            count: sum(values[bucket] == count for values in attachment_machine_counts.values())
            for count in sorted({values[bucket] for values in attachment_machine_counts.values()})
        }
        for bucket in ("failure", "calendar_pm", "wafer_pm")
    }
    machines_with_attachments = sum(
        any(value > 0 for value in counts.values())
        for counts in attachment_machine_counts.values()
    )
    machines_with_multi_calendar_pm = sum(
        counts["calendar_pm"] > 1 for counts in attachment_machine_counts.values()
    )
    max_calendar_pm_per_machine = max(
        (counts["calendar_pm"] for counts in attachment_machine_counts.values()),
        default=0,
    )

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
    rework_segment_steps: dict[str, dict[str, set[int]]] = {}
    for op in rework_ops:
        if op.rework_step_id is None:
            continue
        positions = rework_segment_steps.setdefault(
            op.route_id, {"return": set(), "middle": set(), "source": set()}
        )
        positions["return"].add(op.rework_step_id)
        positions["middle"].update(range(op.rework_step_id + 1, op.step_id))
        positions["source"].add(op.step_id)
    product_route_ids = {product.product_id: product.route_id for product in products}
    initial_wip_rework_position_counts = {
        position: sum(
            wip.current_step_id
            in rework_segment_steps.get(
                product_route_ids[wip.product_id], {}
            ).get(position, set())
            for wip in initial_wip
        )
        for position in ("return", "middle", "source")
    }
    cascading_ops = tuple(op for op in operations if op.batch_interval_minutes is not None or op.part_interval_minutes is not None)
    cqt_endpoint_keys = {
        (op.route_id, step_id)
        for op in cqt_ops
        for step_id in (op.step_id, op.cqt_target_step_id)
        if step_id is not None
    }
    dedication_endpoint_keys = {
        (op.route_id, step_id)
        for op in dedication_ops
        for step_id in (op.step_id, op.dedication_target_step_id)
        if step_id is not None
    }
    cascading_tool_families = {
        template.tool_family_id
        for template in machine_templates
        if template.cascading
    }
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

    def sampling_runtime_profile(operation: OperationDefinition) -> bool:
        """检查 raw StepPercent 是否落在当前 sampling runtime 的受限闭包。"""

        locations = {
            location_by_machine[machine_id]
            for machine_id in operation.eligible_machine_ids
            if machine_id in location_by_machine
        }
        return (
            operation.processing_basis == "per_lot"
            and locations == {"Fab"}
            and operation.batch_min_wafers is None
            and operation.batch_max_wafers is None
            and operation.required_setup is None
            and operation.setup_override_minutes is None
            and operation.dedication_target_step_id is None
            and (
                operation.sample_percent == 100.0
                or (operation.route_id, operation.step_id) not in cqt_endpoint_keys
            )
            and (
                operation.route_id,
                operation.step_id,
            ) not in dedication_endpoint_keys
            and operation.tool_family_id not in cascading_tool_families
            and operation.batch_interval_minutes is None
            and operation.part_interval_minutes is None
        )

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
    unsupported_sampling_ops = tuple(
        op for op in sample_field_ops if not sampling_runtime_profile(op)
    )
    supported_sampling_ops = tuple(
        op for op in sample_field_ops if sampling_runtime_profile(op)
    )
    sampling_cqt_endpoint_ops = tuple(
        op
        for op in sample_field_ops
        if (op.route_id, op.step_id) in cqt_endpoint_keys
    )
    stochastic_sampling_cqt_endpoint_ops = tuple(
        op
        for op in sampling_cqt_endpoint_ops
        if op.sample_percent != 100.0
    )
    sampling_dedication_endpoint_ops = tuple(
        op
        for op in sample_field_ops
        if (op.route_id, op.step_id) in dedication_endpoint_keys
    )
    sampling_cascading_tool_ops = tuple(
        op for op in sample_field_ops if op.tool_family_id in cascading_tool_families
    )
    tool_templates_by_family = {
        template.tool_family_id: template for template in machine_templates
    }
    sampling_load_unload_ops = tuple(
        op
        for op in sample_field_ops
        if (
            tool_templates_by_family[op.tool_family_id].load_minutes > 0
            or tool_templates_by_family[op.tool_family_id].unload_minutes > 0
        )
    )
    sampling_rework_overlap_ops = tuple(
        op for op in sample_field_ops if op.rework_step_id is not None
    )
    if unsupported_sampling_ops:
        note(
            "BLOCKER", "DI_UNSUPPORTED_SAMPLING",
            "部分 StepPercent 抽样超出当前受限 runtime profile；随机 sampling 作为 CQT/Dedication endpoint 时的 skip 语义尚未冻结",
            affected=len(unsupported_sampling_ops),
            cqt_endpoints=len(sampling_cqt_endpoint_ops),
            stochastic_cqt_endpoints=len(stochastic_sampling_cqt_endpoint_ops),
            dedication_endpoints=len(sampling_dedication_endpoint_ops),
            cascading_tools=len(sampling_cascading_tool_ops),
        )
        if supported_sampling_ops:
            note(
                "INFO", "DI_SAMPLING_RUNTIME_SUPPORTED_LIMITED",
                "per_lot/Fab StepPercent 子集已由 runtime 支持；p=100 CQT endpoint 确定执行",
                affected=len(supported_sampling_ops),
                stochastic=sum(
                    op.sample_percent not in (None, 100.0)
                    for op in supported_sampling_ops
                ),
            )
    elif sample_field_ops:
        note(
            "INFO", "DI_SAMPLING_RUNTIME_SUPPORTED",
            "显式 StepPercent 均符合 per_lot/Fab 且无随机 CQT endpoint、dedication/cascade profile；由 runtime 执行抽样",
            affected=len(sample_field_ops), stochastic=len(sample_ops),
        )
    if sampling_rework_overlap_ops:
        note(
            "INFO", "DI_SAMPLING_REWORK_OVERLAP",
            "部分 sampling operation 同时声明 rework；抽样 runtime 不隐式忽略该组合，仍由 rework blocker 阻塞正式组合",
            operations=len(sampling_rework_overlap_ops),
        )
    if config.mode == "sampling_validation_slice":
        note(
            "WARNING", "DI_SAMPLING_SLICE_OMITS_LOAD_UNLOAD",
            "sampling validation slice 仅验证判定/映射；原设备 LOAD/UNLOAD 尚未进入物理时长",
            raw_operations_with_load_unload=len(sampling_load_unload_ops),
        )
    if rework_ops:
        note("BLOCKER", "DI_UNSUPPORTED_REWORK", "RWKSTEP/REWORK 路线回跳尚未实现", affected=len(rework_ops))
        if any(initial_wip_rework_position_counts.values()):
            note(
                "WARNING", "DI_REWORK_INITIAL_HISTORY_UNKNOWN",
                "初始 WIP 位于 rework 回跳段；raw 无 visit、既往 rework 判定或原机台历史",
                **initial_wip_rework_position_counts,
            )
    if setup_minrun_count:
        note("BLOCKER", "DI_UNSUPPORTED_SETUP_MINRUN", "setupgrp.MINRUN 已有本地合成 runtime，但真实组合映射未闭环", affected=setup_minrun_count)
    batch_decision_config = config.batch_decision_config
    batch_config_matches = batch_decision_config is not None
    if batch_decision_config is None:
        note(
            "BLOCKER",
            "DI_MISSING_BATCH_DECISION_CONFIG",
            "B_target/T_max 不在 raw 数据中；正式场景需显式传入版本化 BatchDecisionConfig",
            batch_operations=len(batch_ops),
        )
    else:
        if batch_decision_config.source_path is None:
            batch_config_matches = False
            note(
                "BLOCKER",
                "DI_MISSING_BATCH_DECISION_CONFIG",
                "BatchDecisionConfig 缺少可复核的版本化 source artifact；手工构造对象不能关闭 Gate blocker",
                config_id=batch_decision_config.config_id,
            )
        else:
            try:
                source_config = load_batch_decision_config(
                    batch_decision_config.source_path,
                    batch_decision_config.model_id,
                )
            except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
                batch_config_matches = False
                note(
                    "ERROR",
                    "DI_BATCH_DECISION_CONFIG_SOURCE_INVALID",
                    "BatchDecisionConfig source artifact 无法重新解析；禁止回退",
                    detail=str(exc),
                )
            else:
                if (
                    source_config.canonical_payload() != batch_decision_config.canonical_payload()
                    or source_config.source_sha256 != batch_decision_config.source_sha256
                    or source_config.source_schema_version != batch_decision_config.source_schema_version
                ):
                    batch_config_matches = False
                    note(
                        "ERROR",
                        "DI_BATCH_DECISION_CONFIG_SOURCE_MISMATCH",
                        "BatchDecisionConfig 与 source artifact 不一致；禁止回退",
                        config_id=batch_decision_config.config_id,
                    )
        if batch_decision_config.model_id != model_id:
            batch_config_matches = False
            note(
                "ERROR",
                "DI_BATCH_DECISION_CONFIG_MODEL_MISMATCH",
                "BatchDecisionConfig.model_id 与当前 SMT2020 模型不一致；禁止回退",
                config_model_id=batch_decision_config.model_id,
                model_id=model_id,
            )
        if batch_decision_config.manifest_hash != manifest.manifest_hash:
            batch_config_matches = False
            note(
                "ERROR",
                "DI_BATCH_DECISION_CONFIG_MANIFEST_MISMATCH",
                "BatchDecisionConfig.manifest_hash 与真实数据 manifest 不一致；禁止回退",
                config_manifest_hash=batch_decision_config.manifest_hash,
                manifest_hash=manifest.manifest_hash,
            )
        if batch_config_matches:
            invalid_batch_bounds = tuple(
                op for op in batch_ops
                if op.batch_max_wafers is None
                or op.batch_min_wafers is None
                or op.batch_min_wafers <= 0
                or op.batch_max_wafers < op.batch_min_wafers
            )
            if invalid_batch_bounds:
                batch_config_matches = False
                note(
                    "ERROR",
                    "DI_BATCH_DECISION_CONFIG_RAW_BOUNDS_INVALID",
                    "raw batch 容量不满足显式 raw_batch_max 配置规则；禁止回退",
                    affected=len(invalid_batch_bounds),
                )
        if batch_config_matches:
            batch_template_by_machine = {
                machine_id: template
                for template in machine_templates
                for machine_id in template.resource_instance_ids
            }
            unsupported_batch_compatibility = tuple(
                op for op in batch_ops
                if not op.eligible_machine_ids or any(
                    machine_id not in batch_template_by_machine
                    or batch_template_by_machine[machine_id].batch_criterion != "crit_sameroutestep"
                    or batch_template_by_machine[machine_id].batch_unit != "piece"
                    for machine_id in op.eligible_machine_ids
                )
            )
            if unsupported_batch_compatibility:
                batch_config_matches = False
                note(
                    "ERROR",
                    "DI_BATCH_COMPATIBILITY_UNSUPPORTED",
                    "raw BATCHCRITF/BATCHPER 与当前 BatchSpec 不兼容；禁止依赖默认规则",
                    affected=len(unsupported_batch_compatibility),
                )
        if batch_config_matches:
            note(
                "INFO",
                "DI_BATCH_DECISION_CONFIG_SUPPLIED",
                "已提供并校验版本化 BatchDecisionConfig；raw_batch_max 与 T_max 仅为本地 E 级验证假设",
                config_id=batch_decision_config.config_id,
                config_hash=batch_decision_config.canonical_hash,
                batch_operations=len(batch_ops),
            )
    if config.mode == "batch_validation_slice" and batch_config_matches:
        note(
            "WARNING",
            "DI_BATCH_SLICE_OMITS_LOAD_UNLOAD",
            "batch validation slice 仅验证组批判定/真实 processing distribution；原设备 LOAD/UNLOAD 尚未进入物理时长",
            raw_batch_operations=len(batch_ops),
        )
    if any(item.interval and item.interval.kind == "exponential" for item in down_calendars):
        note("INFO", "DI_EXPONENTIAL_FAILURE_RUNTIME_SUPPORTED", "downcal exponential 已按均值参数进入共享 sampler", calendars=len(down_calendars))
    note(
        "BLOCKER",
        "DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT",
        "raw attachment 显示同一物理机的多条 Failure/PM 组合；多 Wafer PM、Failure 与多 Calendar PM 的完整联动仍未闭环，受限 slice 不关闭该 blocker",
        attachments=len(attachments),
        raw_attachment_rows=len(attachments),
        physical_machines_with_attachments=machines_with_attachments,
        physical_machines_with_multi_calendar_pm=machines_with_multi_calendar_pm,
        calendar_pm_attachment_rows=sum(
            item.calendar_kind == "pm"
            and pm_calendar_by_id.get(item.calendar_id) is not None
            and pm_calendar_by_id[item.calendar_id].interval is not None
            for item in attachments
        ),
        wafer_pm_attachment_rows=sum(
            item.calendar_kind == "pm"
            and pm_calendar_by_id.get(item.calendar_id) is not None
            and pm_calendar_by_id[item.calendar_id].wafer_threshold is not None
            for item in attachments
        ),
        failure_attachment_rows=sum(item.calendar_kind == "down" for item in attachments),
    )
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
    route_id_by_product = {item.product_id: item.route_id for item in products}
    sample_by_route_step = {
        (op.route_id, op.step_id): op for op in sample_field_ops
    }
    initial_wip_sampling = tuple(
        (wip, sample_by_route_step[(route_id_by_product[wip.product_id], wip.current_step_id)])
        for wip in initial_wip
        if (route_id_by_product.get(wip.product_id), wip.current_step_id)
        in sample_by_route_step
    )

    def percent_key(percent: float) -> str:
        return str(int(percent)) if float(percent).is_integer() else str(percent)

    sampling_percent_distribution = {
        percent_key(percent): sum(
            op.sample_percent == percent for op in sample_field_ops
        )
        for percent in sorted({op.sample_percent for op in sample_field_ops if op.sample_percent is not None})
    }
    initial_wip_sampling_percent_distribution = {
        percent_key(percent): sum(
            op.sample_percent == percent for _, op in initial_wip_sampling
        )
        for percent in sorted({op.sample_percent for _, op in initial_wip_sampling if op.sample_percent is not None})
    }
    initial_wip_sampling_100 = sum(op.sample_percent == 100.0 for _, op in initial_wip_sampling)
    initial_wip_sampling_stochastic = sum(op.sample_percent not in (None, 100.0) for _, op in initial_wip_sampling)
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
        "sampling_percent_distribution": sampling_percent_distribution,
        "sampling_percent_counts": sampling_percent_distribution,
        "sampling_100_operations": sum(op.sample_percent == 100.0 for op in sample_field_ops),
        "sampling_stochastic_operations": len(sample_ops),
        "sampling_profile_unsupported_operations": len(unsupported_sampling_ops),
        "sampling_profile_supported_operations": len(supported_sampling_ops),
        "sampling_cqt_endpoint_operations": len(sampling_cqt_endpoint_ops),
        "sampling_stochastic_cqt_endpoint_operations": len(
            stochastic_sampling_cqt_endpoint_ops
        ),
        "sampling_dedication_endpoint_operations": len(sampling_dedication_endpoint_ops),
        "sampling_cascading_tool_operations": len(sampling_cascading_tool_ops),
        "sampling_load_unload_operations": len(sampling_load_unload_ops),
        "initial_wip_at_sampling_lots": len(initial_wip_sampling),
        "initial_wip_at_sampling_count": len(initial_wip_sampling),
        "initial_wip_at_sampling_percent_distribution": initial_wip_sampling_percent_distribution,
        "initial_wip_at_sampling_counts": {
            "100": initial_wip_sampling_100,
            "stochastic": initial_wip_sampling_stochastic,
        },
        "initial_wip_at_sampling_100": initial_wip_sampling_100,
        "initial_wip_at_sampling_stochastic": initial_wip_sampling_stochastic,
        "sampling_rework_overlap_operations": len(sampling_rework_overlap_ops),
        "sampling_rework_overlap_count": len(sampling_rework_overlap_ops),
        "sampling_rework_overlap_initial_wip_lots": sum(
            op.rework_step_id is not None for _, op in initial_wip_sampling
        ),
        "rework_operations": len(rework_ops),
        "rework_scope_counts": rework_scope_counts,
        "initial_wip_rework_position_counts": initial_wip_rework_position_counts,
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
        "attachment_expanded_machine_edges": expanded_attachment_edges,
        "attachment_physical_machine_count": machines_with_attachments,
        "attachment_physical_machine_count_total": len(production_machine_ids),
        "attachment_physical_machines_with_multi_calendar_pm": machines_with_multi_calendar_pm,
        "attachment_max_calendar_pm_per_machine": max_calendar_pm_per_machine,
        "attachment_machine_count_distributions": attachment_count_distributions,
        "attachment_machine_counts_by_physical_machine": {
            machine_id: dict(attachment_machine_counts[machine_id])
            for machine_id in production_machine_ids
        },
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
    elif config.mode == "sampling_validation_slice":
        scenario = _build_sampling_validation_slice(static_model, manifest, config)
    elif config.mode == "batch_validation_slice" and batch_config_matches:
        scenario = _build_batch_validation_slice(static_model, manifest, config)
    elif config.mode == "load_unload_validation_slice":
        scenario = _build_load_unload_validation_slice(static_model, manifest, config)
    elif config.mode == "multi_calendar_validation_slice":
        scenario = _build_multi_calendar_validation_slice(static_model, manifest, config)
    else:
        scenario = None
    if config.mode == "batch_validation_slice" and scenario is not None:
        selected_machine_id = scenario.machines[0].machine_id
        eligible_machine_count = int(dict(scenario.dataset_provenance.loader_config)["batch_slice_raw_eligible_machine_count"])
        if eligible_machine_count > 1:
            note(
                "WARNING",
                "DI_BATCH_SLICE_SINGLE_MACHINE",
                "batch validation slice 只选取一台真实合格机；其余资格机器及资源竞争不进入诊断",
                selected_machine_id=selected_machine_id,
                raw_eligible_machines=eligible_machine_count,
            )
        omitted_calendars = stats["attachment_machine_counts_by_physical_machine"].get(
            selected_machine_id, {}
        )
        if any(omitted_calendars.values()):
            note(
                "WARNING",
                "DI_BATCH_SLICE_OMITS_CALENDAR_ATTACHMENTS",
                "batch validation slice 不装配该真实物理机的 Failure/PM calendar；仅验证 batch 决策与加工抽样",
                machine_id=selected_machine_id,
                **omitted_calendars,
            )
    if config.mode == "load_unload_validation_slice" and scenario is not None:
        slice_items = dict(scenario.dataset_provenance.loader_config)
        selected_ids = tuple(
            item
            for item in slice_items.get(
                "load_unload_slice_selected_machine_ids", ""
            ).split(",")
            if item
        )
        omitted_ids = tuple(
            item
            for item in slice_items.get(
                "load_unload_slice_omitted_machine_ids", ""
            ).split(",")
            if item
        )
        note(
            "WARNING",
            "DI_LOAD_UNLOAD_SLICE_SINGLE_MACHINE",
            "load/unload validation slice 每道工序只选一台具体真实合格机；其他资格机不进入 Scenario",
            selected_machine_ids=selected_ids,
            omitted_machine_count=len(omitted_ids),
        )
        note(
            "WARNING",
            "DI_LOAD_UNLOAD_SLICE_OMITS_CALENDAR_ATTACHMENTS",
            "load/unload validation slice 不装配所选物理机的 Failure/calendar PM/wafer PM；原始 attachment 与 ID 已进入 provenance",
            failure_calendars=slice_items.get(
                "load_unload_slice_omitted_failure_calendar_ids", ""
            ),
            calendar_pm=slice_items.get(
                "load_unload_slice_omitted_calendar_pm_ids", ""
            ),
            wafer_pm=slice_items.get(
                "load_unload_slice_omitted_wafer_pm_ids", ""
            ),
        )
        note(
            "WARNING",
            "DI_LOAD_UNLOAD_SLICE_INITIAL_HISTORY_UNKNOWN",
            "slice 不伪造初始 setup、wafer-PM counter 或历史 CQT/dedication 状态；当前 WIP 原始元数据已保留",
            initial_setup=slice_items.get(
                "load_unload_slice_omitted_initial_setup", "unknown"
            ),
            wafer_pm_counter=slice_items.get(
                "load_unload_slice_omitted_wafer_pm_initial_counter", ""
            ),
            unknown_cqt=slice_items.get(
                "load_unload_slice_initial_unknown_cqt_relationships", "0"
            ),
            unknown_dedication=slice_items.get(
                "load_unload_slice_initial_unknown_dedication_relationships", "0"
            ),
        )
        note(
            "INFO",
            "DI_LOAD_UNLOAD_SLICE_PROFILE_RESTRICTED",
            "slice 仅接受 per_lot、无 setup/batch/sampling/rework/CQT/dedication 的 non-cascade 两工序 profile",
            profile=slice_items.get("load_unload_slice_profile_constraints", ""),
        )
    if config.mode == "multi_calendar_validation_slice" and scenario is not None:
        slice_items = dict(scenario.dataset_provenance.loader_config)
        note(
            "WARNING",
            "DI_MULTI_CALENDAR_SLICE_OMITS_FAILURE",
            "multi-calendar validation slice 不装配所选物理机的 Failure calendar；仅验证多 Calendar PM 调度",
            machine_id=slice_items.get("multi_calendar_slice_machine_id", ""),
            omitted_failure_calendars=slice_items.get(
                "multi_calendar_slice_omitted_failure_calendar_ids", ""
            ),
            omitted_calendar_pm=slice_items.get(
                "multi_calendar_slice_omitted_calendar_pm_ids", ""
            ),
            omitted_wafer_pm=slice_items.get(
                "multi_calendar_slice_omitted_wafer_pm_ids", ""
            ),
        )
        note(
            "WARNING",
            "DI_MULTI_CALENDAR_SLICE_OMITS_OTHER_MACHINES",
            "multi-calendar validation slice 只选取一台真实合格物理机；其他资格机和资源竞争不进入 Scenario",
            machine_id=slice_items.get("multi_calendar_slice_machine_id", ""),
            omitted_machine_ids=slice_items.get(
                "multi_calendar_slice_omitted_machine_ids", ""
            ),
        )
        note(
            "WARNING",
            "DI_MULTI_CALENDAR_SLICE_OMITS_ROUTE_HISTORY",
            "multi-calendar validation slice 只保留当前一道工序；后续 route/rework 不进入 Scenario",
            omitted_route_steps=slice_items.get(
                "multi_calendar_slice_omitted_route_steps", ""
            ),
            omitted_future_route_steps=slice_items.get(
                "multi_calendar_slice_omitted_future_route_steps", ""
            ),
            omitted_rework_links=slice_items.get(
                "multi_calendar_slice_omitted_rework_links", ""
            ),
        )
        note(
            "WARNING",
            "DI_MULTI_CALENDAR_SLICE_INITIAL_HISTORY_UNKNOWN",
            "multi-calendar validation slice 不伪造初始 setup、CQT/dedication 历史或 wafer-PM counter",
            initial_setup=slice_items.get(
                "multi_calendar_slice_initial_setup", "unknown"
            ),
            initial_cqt=slice_items.get(
                "multi_calendar_slice_initial_cqt_history", "unknown"
            ),
            initial_dedication=slice_items.get(
                "multi_calendar_slice_initial_dedication_history", "unknown"
            ),
            initial_wafer_pm_counter=slice_items.get(
                "multi_calendar_slice_initial_wafer_pm_counter", "unknown"
            ),
        )
        note(
            "WARNING",
            "DI_MULTI_CALENDAR_SLICE_NOT_FULL_FAB",
            "multi-calendar validation slice 是受限诊断，不代表 full-fab，也不关闭 Data Integration Gate blocker",
            calendar_ids=slice_items.get("multi_calendar_slice_calendar_ids", ""),
        )
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
        SemanticEvidence(
            "sampling-runtime",
            "A/B/C/D/E",
            "route.StepPercent + initial WIP + SMT2020 paper + fixed PySCFabSim reference + local contract",
            "受限 per-lot operation-entry 判定可复现；真实 CQT target 均为 p=100，LOAD/UNLOAD 组合继续阻塞完整物理兼容",
        ),
        SemanticEvidence(
            "batch-decision-runtime",
            "A/B/E",
            "raw BATCHMN/BATCHMX + real initial WIP + explicit versioned BatchDecisionConfig",
            "B_target=raw BATCHMX 与 T_max 为显式本地假设；受限 slice 不包含 LOAD/UNLOAD 和 calendar",
        ),
        SemanticEvidence(
            "load-unload-validation-runtime",
            "A/B/D/E",
            "real initial WIP + route_3 + tool.txt.1l + fromto.txt + local contract",
            "仅保留 r_3:18→19、指定真实 WIP 和各自首台合格机；保留真实 1 min LOAD/UNLOAD 与 Fab→Fab 外生 transport，其余资格机、Failure/PM 和历史状态显式省略并进入 provenance",
        ),
        SemanticEvidence(
            "multi-calendar-validation-runtime",
            "A/B/D/E",
            "pmcal.txt + attach.txt + real initial WIP + tool.txt.1l + local contract",
            "逐条保留同一真实物理机的 WK/MN/QT Calendar PM interval、duration 与 FOA；runtime pm_id 由 raw calendar ID 和 physical machine ID 组成，其他设备、Failure、后续 route/rework 与初始历史显式省略",
        ),
        SemanticEvidence("initial-state-fallbacks", "E/F", "project contract + absent raw history", "显式假设并保留 unknown audit"),
    )
    return LoadedScenario(scenario, static_model, manifest, tuple(audit), evidence, MappingProxyType(stats), config)


def _build_batch_validation_slice(
    model: SMT2020StaticModel,
    manifest: DatasetManifest,
    config: LoaderConfig,
) -> Scenario:
    """从真实 initial WIP 构造一个受限 per-batch 验证 slice。

    该 slice 只保留 raw 中已经具备 ``BATCHMN/BATCHMX`` 的 per-batch 工序，
    且要求真实 WIP 在同一 route/step 上达到 BATCHMN。每个 lot 保留原始
    25-wafer WIP 与元数据；batch target 来自显式配置的 ``raw_batch_max``。
    机器 LOAD/UNLOAD 不进入 ``MachineSpec`` 的物理时长，相关 raw 数值写入
    provenance，并由主 loader 以 WARNING 标记，不能据此宣称 physical-duration closure。
    """

    decision_config = config.batch_decision_config
    if decision_config is None:
        raise ValueError("batch_validation_slice 必须显式提供 BatchDecisionConfig")

    product_by_id = {item.product_id: item for item in model.products}
    all_operations = tuple(
        operation for route in model.routes for operation in route.operations
    )
    cqt_endpoint_keys = {
        (operation.route_id, step_id)
        for operation in all_operations
        if operation.cqt_target_step_id is not None
        for step_id in (operation.step_id, operation.cqt_target_step_id)
    }
    dedication_endpoint_keys = {
        (operation.route_id, step_id)
        for operation in all_operations
        if operation.dedication_target_step_id is not None
        for step_id in (operation.step_id, operation.dedication_target_step_id)
    }
    operations_by_key = {
        (route.route_id, operation.step_id): operation
        for route in model.routes
        for operation in route.operations
        if (
            operation.processing_basis == "per_batch"
            and operation.batch_min_wafers is not None
            and operation.batch_max_wafers is not None
            and operation.required_setup is None
            and operation.setup_override_minutes is None
            and operation.batch_interval_minutes is None
            and operation.part_interval_minutes is None
            and operation.sample_percent is None
            and operation.rework_step_id is None
            and operation.cqt_target_step_id is None
            and operation.dedication_target_step_id is None
            and (route.route_id, operation.step_id) not in cqt_endpoint_keys
            and (route.route_id, operation.step_id) not in dedication_endpoint_keys
            and operation.eligible_machine_ids
        )
    }
    wip_by_key: dict[tuple[str, int], list[InitialWipDefinition]] = {}
    for wip in model.initial_wip:
        product = product_by_id.get(wip.product_id)
        if product is None or wip.quantity_wafers != 25:
            continue
        wip_by_key.setdefault((product.route_id, wip.current_step_id), []).append(wip)

    candidates: list[tuple[tuple[str, int], OperationDefinition, tuple[InitialWipDefinition, ...]]] = []
    for key, operation in operations_by_key.items():
        wips = tuple(sorted(wip_by_key.get(key, ()), key=lambda item: (item.source_row or 0, item.lot_id)))
        if wips and sum(item.quantity_wafers for item in wips) >= operation.batch_min_wafers:
            candidates.append((key, operation, wips))
    requested = config.validation_batch_operation
    if requested is not None:
        candidates = [item for item in candidates if item[0] == requested]
        if not candidates:
            raise ValueError(
                "找不到满足 batch validation slice 的精确真实 selector："
                f"{requested[0]}:{requested[1]}"
            )
    if not candidates:
        raise ValueError(
            "找不到具备真实 initial WIP 且达到 BATCHMN 的受限 per-batch 工序"
        )

    # 优先选择真实 WIP 达到 BATCHMN 但未达到 target 的组，能够验证 T_max；
    # 若数据没有此形态，则按 route/step 稳定选择首个可行组。
    has_timeout_candidate = any(
        sum(wip.quantity_wafers for wip in item[2])
        < (item[1].batch_max_wafers or 0)
        for item in candidates
    )
    if has_timeout_candidate:
        selection_key = lambda item: (
            sum(wip.quantity_wafers for wip in item[2])
            >= (item[1].batch_max_wafers or 0),
            item[1].batch_min_wafers or 0,
            item[0][0],
            item[0][1],
        )
    else:
        selection_key = lambda item: (item[0][0], item[0][1])
    key, operation, wips = sorted(candidates, key=selection_key)[0]
    # The target is deliberately the raw BATCHMX under the only supported rule.
    target_wafers = operation.batch_max_wafers
    assert target_wafers is not None
    if decision_config.target_rule != "raw_batch_max":
        raise ValueError("不支持的 batch target_rule；禁止隐式回退")
    if target_wafers < operation.batch_min_wafers:
        raise ValueError("raw BATCHMX 小于 BATCHMN，无法构造 BatchSpec")

    product_ids = tuple(sorted({wip.product_id for wip in wips}))
    machine_id = operation.eligible_machine_ids[0]
    tool_template = next(
        template
        for template in model.machine_templates
        if machine_id in template.resource_instance_ids
    )
    if tool_template.batch_criterion != "crit_sameroutestep" or tool_template.batch_unit != "piece":
        raise ValueError("真实 batch tool 的 BATCHCRITF/BATCHPER 当前不受支持")
    omitted_calendar_ids = tuple(sorted(
        attachment.calendar_id
        for attachment in model.calendar_attachments
        if (
            attachment.resource_type == "stnfam"
            and attachment.resource_name == tool_template.tool_family_id
        ) or (
            attachment.resource_type == "stngrp"
            and attachment.resource_name == tool_template.group_id
        )
    ))
    processing = to_runtime_distribution(operation.processing)
    batch_spec = BatchSpec(
        minimum_wafers=operation.batch_min_wafers,
        maximum_wafers=operation.batch_max_wafers,
        target_wafers=target_wafers,
        max_wait_minutes=decision_config.max_wait_minutes,
        compatibility_rule=tool_template.batch_criterion,
    )
    runtime_operation = OperationSpec(
        operation.step_id,
        processing.mean_minutes,
        (machine_id,),
        route_id=operation.route_id,
        tool_group_id=operation.tool_family_id,
        batch_spec=batch_spec,
        processing_distribution=processing,
        processing_basis=operation.processing_basis,
    )
    lots = tuple(
        LotSpec(
            wip.lot_id,
            0.0,
            (runtime_operation,),
            quantity_wafers=25,
            due_time=wip.due_minutes,
            priority=wip.priority,
            is_initial_wip=True,
            product_id=wip.product_id,
            order_id=wip.order_id,
            hot_lot=wip.hot_lot,
            source_row=wip.source_row,
        )
        for wip in wips
    )
    batch_count = ceil(sum(lot.quantity_wafers for lot in lots) / target_wafers)
    processing_upper = processing.mean_minutes + processing.width_minutes / 2
    provenance = DatasetProvenanceSpec(
        manifest.dataset_family,
        manifest.model_name,
        manifest.manifest_hash,
        manifest.parser_schema_version,
        manifest.loader_version,
        SMT2020_LOADER_CONTRACT_VERSION,
        config.provenance_items() + (
            ("batch_slice_operation", f"{operation.route_id}:{operation.step_id}"),
            ("batch_slice_product_ids", ",".join(product_ids)),
            ("batch_slice_wip_lots", str(len(lots))),
            ("batch_slice_selected_machine_id", machine_id),
            ("batch_slice_raw_eligible_machine_count", str(len(operation.eligible_machine_ids))),
            ("batch_slice_raw_batch_criterion", tool_template.batch_criterion),
            ("batch_slice_raw_batch_unit", tool_template.batch_unit),
            ("batch_slice_omitted_load_minutes", str(tool_template.load_minutes)),
            ("batch_slice_omitted_unload_minutes", str(tool_template.unload_minutes)),
            ("batch_slice_omitted_calendar_ids", ",".join(omitted_calendar_ids)),
        ),
        tuple(
            SourceFileProvenance(item.relative_path, item.size_bytes, item.sha256)
            for item in manifest.files
        ),
    )
    return Scenario(
        scenario_id=(
            f"{manifest.model_name}:batch-validation-slice:"
            f"{operation.route_id}:{operation.step_id}"
        ),
        dataset_version=manifest.dataset_version,
        machines=(MachineSpec(machine_id, location_id=tool_template.location_id),),
        lots=lots,
        termination_mode="fixed_horizon",
        horizon=batch_count * processing_upper + decision_config.max_wait_minutes + 1,
        dataset_provenance=provenance,
    )


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


def _build_load_unload_validation_slice(
    model: SMT2020StaticModel,
    manifest: DatasetManifest,
    config: LoaderConfig,
) -> Scenario:
    """构造真实 LOAD/UNLOAD 的两工序最小闭包验证 slice。

    这是一个有意受限的诊断场景，不是 HVLM/LVHM 全量实验：只选 ``r_3`` 的
    18→19 两道连续工序和指定的真实 initial WIP，并为每道工序选取一台具体
    合格机。与其它 validation slice 不同，所选 MachineSpec 保留 raw 的
    LOAD=UNLOAD=1 min；其余合格机、calendar、历史状态和组合 profile 都不
    进入 Scenario，但会写入 provenance，避免把省略误读成数据不存在。
    """

    target_lot_by_model = {
        "SMT2020_HVLM": "Init_Lot_3_1361",
        "SMT2020_LVHM": "Init_Lot_3_290",
    }
    target_lot_id = target_lot_by_model.get(manifest.model_name)
    if target_lot_id is None:
        raise ValueError(
            "load_unload_validation_slice 仅支持 SMT2020_HVLM/SMT2020_LVHM"
        )

    product = next(
        (item for item in model.products if item.route_id == "r_3"),
        None,
    )
    route = next((item for item in model.routes if item.route_id == "r_3"), None)
    if product is None or route is None:
        raise ValueError("找不到 load/unload slice 所需的真实 r_3 产品/路线")
    if product.product_id != "part_3":
        raise ValueError("r_3 的真实产品不是 part_3，拒绝隐式替换 slice")

    operation_by_step = {operation.step_id: operation for operation in route.operations}
    try:
        operations = (operation_by_step[18], operation_by_step[19])
    except KeyError as exc:
        raise ValueError("真实 r_3 缺少连续的 18→19 工序") from exc
    if operations[1].step_id != operations[0].step_id + 1:
        raise ValueError("load/unload slice 要求 r_3:18→r_3:19 连续")
    expected_families = ("DE_FE_1", "DE_FE_86")
    if tuple(operation.tool_family_id for operation in operations) != expected_families:
        raise ValueError(
            "r_3:18→r_3:19 的真实 tool family 不是 DE_FE_1→DE_FE_86"
        )

    location_by_machine = {
        machine_id: template.location_id
        for template in model.machine_templates
        for machine_id in template.resource_instance_ids
    }
    template_by_machine = {
        machine_id: template
        for template in model.machine_templates
        for machine_id in template.resource_instance_ids
    }
    selected_machine_ids: list[str] = []
    selected_templates: list[MachineTemplateDefinition] = []
    for operation in operations:
        if (
            operation.processing_basis != "per_lot"
            or operation.required_setup is not None
            or operation.setup_override_minutes is not None
            or operation.batch_min_wafers is not None
            or operation.batch_max_wafers is not None
            or operation.sample_percent is not None
            or operation.rework_step_id is not None
            or operation.cqt_target_step_id is not None
            or operation.dedication_target_step_id is not None
            or operation.batch_interval_minutes is not None
            or operation.part_interval_minutes is not None
        ):
            raise ValueError(
                f"真实 {operation.route_id}:{operation.step_id} 超出 non-cascade per_lot profile"
            )
        eligible = tuple(
            machine_id
            for machine_id in operation.eligible_machine_ids
            if location_by_machine.get(machine_id) == "Fab"
            and not template_by_machine[machine_id].cascading
        )
        if not eligible:
            raise ValueError(
                f"真实 {operation.route_id}:{operation.step_id} 没有 Fab non-cascade 合格机"
            )
        machine_id = eligible[0]
        template = template_by_machine[machine_id]
        if (template.load_minutes, template.unload_minutes) != (1.0, 1.0):
            raise ValueError(
                f"真实 {machine_id} 的 LOAD/UNLOAD 不是 1/1 min："
                f"{template.load_minutes}/{template.unload_minutes}"
            )
        selected_machine_ids.append(machine_id)
        selected_templates.append(template)

    matching_wip = tuple(
        item
        for item in model.initial_wip
        if item.lot_id == target_lot_id
        and item.product_id == product.product_id
        and item.current_step_id == 18
    )
    if len(matching_wip) != 1:
        raise ValueError(
            "找不到唯一的指定真实 initial WIP："
            f"{target_lot_id}（要求 part_3/current_step=18）"
        )
    wip = matching_wip[0]

    selected_set = set(selected_machine_ids)
    omitted_machine_ids = tuple(
        sorted(
            machine_id
            for operation in operations
            for machine_id in operation.eligible_machine_ids
            if machine_id not in selected_set
        )
    )
    if len(omitted_machine_ids) != len(set(omitted_machine_ids)):
        omitted_machine_ids = tuple(sorted(set(omitted_machine_ids)))

    # Attachments are expanded exactly as the audit path does: one raw attach
    # row may select a station family or a station group. They are deliberately
    # not mounted on the selected MachineSpecs in this validation slice.
    pm_by_id = {item.calendar_id: item for item in model.pm_calendars}
    omitted_failure_ids: set[str] = set()
    omitted_calendar_pm_ids: set[str] = set()
    omitted_wafer_pm_ids: set[str] = set()
    for machine_id, template in zip(selected_machine_ids, selected_templates, strict=True):
        for attachment in model.calendar_attachments:
            matches_machine = (
                attachment.resource_type == "stnfam"
                and attachment.resource_name == template.tool_family_id
            ) or (
                attachment.resource_type == "stngrp"
                and attachment.resource_name == template.group_id
            )
            if not matches_machine:
                continue
            if attachment.calendar_kind == "down":
                omitted_failure_ids.add(attachment.calendar_id)
            elif attachment.calendar_kind == "pm":
                calendar = pm_by_id.get(attachment.calendar_id)
                if calendar is not None and calendar.wafer_threshold is not None:
                    omitted_wafer_pm_ids.add(attachment.calendar_id)
                else:
                    omitted_calendar_pm_ids.add(attachment.calendar_id)

    relevant_transport = tuple(
        item for item in model.transport
        if item.from_location == "Fab" and item.to_location == "Fab"
    )
    if not relevant_transport:
        raise ValueError("真实 fromto.txt 缺少 Fab→Fab transport")
    transport_specs = tuple(
        TransportSpec(
            item.from_location,
            item.to_location,
            to_runtime_distribution(item.duration),
        )
        for item in relevant_transport
    )
    if len({(item.from_location, item.to_location) for item in transport_specs}) != len(transport_specs):
        raise ValueError("Fab→Fab transport pair 重复，无法构造唯一 Scenario")

    all_operations = tuple(item for item in route.operations)
    unknown_cqt = sum(
        operation.step_id < wip.current_step_id <= (operation.cqt_target_step_id or -1)
        for operation in all_operations
    )
    unknown_dedication = sum(
        operation.step_id < wip.current_step_id <= (operation.dedication_target_step_id or -1)
        for operation in all_operations
    )
    processing_distributions = tuple(
        to_runtime_distribution(operation.processing) for operation in operations
    )
    runtime_operations = tuple(
        OperationSpec(
            operation.step_id,
            distribution.mean_minutes,
            (machine_id,),
            route_id=operation.route_id,
            tool_group_id=operation.tool_family_id,
            processing_distribution=distribution,
            processing_basis=operation.processing_basis,
        )
        for operation, machine_id, distribution in zip(
            operations, selected_machine_ids, processing_distributions, strict=True
        )
    )
    lot = LotSpec(
        wip.lot_id,
        0.0,
        runtime_operations,
        quantity_wafers=wip.quantity_wafers,
        due_time=wip.due_minutes,
        priority=wip.priority,
        is_initial_wip=True,
        initial_operation_index=0,
        product_id=wip.product_id,
        order_id=wip.order_id,
        hot_lot=wip.hot_lot,
        source_row=wip.source_row,
    )
    machines = tuple(
        MachineSpec(
            machine_id,
            load_minutes=template.load_minutes,
            unload_minutes=template.unload_minutes,
            cascading=template.cascading,
            location_id=template.location_id,
            setup_group=template.setup_group,
        )
        for machine_id, template in zip(selected_machine_ids, selected_templates, strict=True)
    )
    transport_upper = max(
        item.duration.mean_minutes + item.duration.width_minutes / 2
        for item in transport_specs
    )
    horizon = (
        sum(
            distribution.mean_minutes + distribution.width_minutes / 2
            for distribution in processing_distributions
        )
        + sum(template.load_minutes + template.unload_minutes for template in selected_templates)
        + transport_upper
        + 1
    )
    provenance_items = config.provenance_items() + (
        ("load_unload_slice_model", manifest.model_name),
        ("load_unload_slice_route", "r_3"),
        ("load_unload_slice_steps", "18->19"),
        ("load_unload_slice_lot_id", wip.lot_id),
        ("load_unload_slice_wip_product_id", wip.product_id),
        ("load_unload_slice_wip_current_step", str(wip.current_step_id)),
        ("load_unload_slice_wip_quantity_wafers", str(wip.quantity_wafers)),
        ("load_unload_slice_wip_due_minutes", str(wip.due_minutes)),
        ("load_unload_slice_wip_priority", str(wip.priority)),
        ("load_unload_slice_wip_order_id", wip.order_id),
        ("load_unload_slice_wip_hot_lot", str(wip.hot_lot)),
        ("load_unload_slice_wip_source_start_minutes", str(wip.source_start_minutes)),
        ("load_unload_slice_wip_source_trace", wip.source_trace or ""),
        ("load_unload_slice_wip_source_row", str(wip.source_row)),
        ("load_unload_slice_selected_machine_ids", ",".join(selected_machine_ids)),
        ("load_unload_slice_selected_tool_families", "DE_FE_1,DE_FE_86"),
        ("load_unload_slice_raw_eligible_machine_counts", ";".join(
            f"{operation.tool_family_id}:{len(operation.eligible_machine_ids)}"
            for operation in operations
        )),
        ("load_unload_slice_omitted_machine_ids", ",".join(omitted_machine_ids)),
        ("load_unload_slice_raw_load_minutes", ";".join(
            f"{operation.tool_family_id}:1.0" for operation in operations
        )),
        ("load_unload_slice_raw_unload_minutes", ";".join(
            f"{operation.tool_family_id}:1.0" for operation in operations
        )),
        ("load_unload_slice_transport_pairs", "Fab->Fab"),
        ("load_unload_slice_omitted_failure_calendar_ids", ",".join(sorted(omitted_failure_ids))),
        ("load_unload_slice_omitted_calendar_pm_ids", ",".join(sorted(omitted_calendar_pm_ids))),
        ("load_unload_slice_omitted_wafer_pm_ids", ",".join(sorted(omitted_wafer_pm_ids))),
        ("load_unload_slice_omitted_wafer_pm_initial_counter", "unknown; runtime fallback=0"),
        ("load_unload_slice_omitted_initial_setup", "unknown"),
        ("load_unload_slice_initial_unknown_cqt_relationships", str(unknown_cqt)),
        ("load_unload_slice_initial_unknown_dedication_relationships", str(unknown_dedication)),
        (
            "load_unload_slice_profile_constraints",
            "processing_basis=per_lot;setup=None;batch=None;sampling=None;"
            "rework=None;cqt=None;dedication=None;cascading=False",
        ),
    )
    provenance = DatasetProvenanceSpec(
        manifest.dataset_family,
        manifest.model_name,
        manifest.manifest_hash,
        manifest.parser_schema_version,
        manifest.loader_version,
        SMT2020_LOADER_CONTRACT_VERSION,
        provenance_items,
        tuple(
            SourceFileProvenance(item.relative_path, item.size_bytes, item.sha256)
            for item in manifest.files
        ),
    )
    return Scenario(
        scenario_id=(
            f"{manifest.model_name}:load-unload-validation-slice:"
            f"r_3:18-19:{wip.lot_id}"
        ),
        dataset_version=manifest.dataset_version,
        machines=machines,
        lots=(lot,),
        termination_mode="fixed_horizon",
        horizon=horizon,
        transport_specs=transport_specs,
        dataset_provenance=provenance,
    )


def _build_multi_calendar_validation_slice(
    model: SMT2020StaticModel,
    manifest: DatasetManifest,
    config: LoaderConfig,
) -> Scenario:
    """构造同一真实物理机上多条 Calendar PM 的最小诊断 slice。

    该 slice 只为验证 raw ``pmcal.txt``/``attach.txt`` 到 runtime
    ``CalendarPMSpec`` 的一对一映射：它保留指定 initial WIP 的当前一道
    ``per_piece`` 工序、``Litho_BE_110#0001`` 及其 raw 三条 WK/MN/QT
    calendar。Failure、其它资格机、剩余 route/rework 和初始历史都明确不
    进入 Scenario；因此它不是 full-fab 场景，也不改变 Data Integration
    Gate 的判断。
    """

    target_by_model = {
        "SMT2020_HVLM": {
            "lot_id": "Init_Lot_3_134",
            "product_id": "part_3",
            "route_id": "r_3",
            "step_id": 491,
            "overlap_pair": ("QT", "MN"),
        },
        "SMT2020_LVHM": {
            "lot_id": "Init_Lot_2_22",
            "product_id": "part_2",
            "route_id": "r_2",
            "step_id": 459,
            "overlap_pair": ("WK", "QT"),
        },
    }
    target = target_by_model.get(manifest.model_name)
    if target is None:
        raise ValueError(
            "multi_calendar_validation_slice 仅支持 SMT2020_HVLM/SMT2020_LVHM"
        )

    family_id = "Litho_BE_110"
    expected_calendar_ids = tuple(
        f"{family_id}_{suffix}" for suffix in ("WK", "MN", "QT")
    )
    family_templates = tuple(
        item
        for item in model.machine_templates
        if item.tool_family_id == family_id
    )
    if len(family_templates) != 1:
        raise ValueError(
            "真实静态模型中的 Litho_BE_110 tool template 数量不是 1："
            f"{len(family_templates)}"
        )
    template = family_templates[0]
    if template.location_id != "Fab" or template.cascading:
        raise ValueError(
            "真实 Litho_BE_110 不是 Fab non-cascade："
            f"location={template.location_id}, cascading={template.cascading}"
        )
    if (template.load_minutes, template.unload_minutes) != (1.0, 1.0):
        raise ValueError(
            "真实 Litho_BE_110 LOAD/UNLOAD 不是 1/1 min："
            f"{template.load_minutes}/{template.unload_minutes}"
        )
    selected_machine_id = f"{family_id}#0001"
    if selected_machine_id not in template.resource_instance_ids:
        raise ValueError(
            "真实 Litho_BE_110 缺少优先选择的 physical machine："
            f"{selected_machine_id}"
        )

    product = next(
        (
            item
            for item in model.products
            if item.product_id == target["product_id"]
            and item.route_id == target["route_id"]
        ),
        None,
    )
    if product is None:
        raise ValueError(
            "multi-calendar slice 找不到指定真实 product/route："
            f"{target['product_id']}/{target['route_id']}"
        )
    route = next(
        (item for item in model.routes if item.route_id == target["route_id"]),
        None,
    )
    if route is None:
        raise ValueError(f"找不到真实 route：{target['route_id']}")
    operation = next(
        (item for item in route.operations if item.step_id == target["step_id"]),
        None,
    )
    if operation is None:
        raise ValueError(
            "真实 route 缺少指定 current operation："
            f"{target['route_id']}:{target['step_id']}"
        )
    if operation.tool_family_id != family_id:
        raise ValueError(
            "指定 current operation 的 raw tool family 不匹配："
            f"{operation.tool_family_id}"
        )
    if selected_machine_id not in operation.eligible_machine_ids:
        raise ValueError(
            "指定 physical machine 不在 current operation 的 raw qualification 中"
        )
    unsupported_profile_fields = {
        "processing_basis": operation.processing_basis,
        "sample_percent": operation.sample_percent,
        "required_setup": operation.required_setup,
        "setup_override_minutes": operation.setup_override_minutes,
        "batch_min_wafers": operation.batch_min_wafers,
        "batch_max_wafers": operation.batch_max_wafers,
        "batch_interval_minutes": operation.batch_interval_minutes,
        "part_interval_minutes": operation.part_interval_minutes,
        "rework_step_id": operation.rework_step_id,
        "cqt_target_step_id": operation.cqt_target_step_id,
        "dedication_target_step_id": operation.dedication_target_step_id,
    }
    if (
        operation.processing_basis != "per_piece"
        or any(
            value is not None
            for key, value in unsupported_profile_fields.items()
            if key != "processing_basis"
        )
    ):
        raise ValueError(
            "指定 current operation 不满足无 sampling/setup/batch/interval 的"
            f" per_piece profile：{unsupported_profile_fields}"
        )

    matching_wip = tuple(
        item
        for item in model.initial_wip
        if item.lot_id == target["lot_id"]
        and item.product_id == target["product_id"]
        and item.current_step_id == target["step_id"]
    )
    if len(matching_wip) != 1:
        raise ValueError(
            "找不到唯一的指定真实 initial WIP："
            f"{target['lot_id']}（要求 {target['product_id']}/"
            f"{target['step_id']}）"
        )
    wip = matching_wip[0]

    pm_by_id = {item.calendar_id: item for item in model.pm_calendars}
    if len(pm_by_id) != len(model.pm_calendars):
        raise ValueError("raw pmcal.txt 的 PMCALNAME 不唯一，拒绝猜测映射")
    attach_by_calendar: dict[str, tuple[CalendarAttachmentDefinition, ...]] = {}
    for item in model.calendar_attachments:
        attach_by_calendar.setdefault(item.calendar_id, ())
        attach_by_calendar[item.calendar_id] += (item,)

    calendar_specs: list[CalendarPMSpec] = []
    selected_attachments: list[CalendarAttachmentDefinition] = []
    for calendar_id in expected_calendar_ids:
        calendar = pm_by_id.get(calendar_id)
        if calendar is None:
            raise ValueError(f"raw pmcal.txt 缺少指定 calendar：{calendar_id}")
        if calendar.calendar_type.lower() != "mtbpm_by_cal":
            raise ValueError(
                f"{calendar_id} 的 raw PMCALTYPE 不是 mtbpm_by_cal："
                f"{calendar.calendar_type}"
            )
        if calendar.interval is None or calendar.wafer_threshold is not None:
            raise ValueError(
                f"{calendar_id} 不是 calendar-interval PM，拒绝降级为其它语义"
            )
        if calendar.duration.kind not in {"constant", "uniform"}:
            raise ValueError(
                f"{calendar_id} 的 raw duration 无固定上下界，"
                "不能验证受限 slice 的 overlap/horizon"
            )
        candidates = tuple(
            item
            for item in attach_by_calendar.get(calendar_id, ())
            if item.calendar_kind == "pm"
            and (
                (item.resource_type == "stnfam" and item.resource_name == family_id)
                or (
                    item.resource_type == "stngrp"
                    and item.resource_name == template.group_id
                )
            )
        )
        if len(candidates) != 1:
            raise ValueError(
                f"{calendar_id} 对 {family_id} 的 raw pm attachment 数量不是 1："
                f"{len(candidates)}"
            )
        attachment = candidates[0]
        if attachment.first_occurrence is None or attachment.first_occurrence_wafers is not None:
            raise ValueError(
                f"{calendar_id} 缺少可映射的 FOA first occurrence 分布"
            )
        if attachment.first_occurrence.kind != "constant":
            raise ValueError(
                f"{calendar_id} 的 FOA 不是 constant，不能无损映射 periodic first_start_time："
                f"{attachment.first_occurrence.kind}"
            )
        first_start = to_runtime_distribution(attachment.first_occurrence)
        if first_start.width_minutes != 0:
            raise ValueError(f"{calendar_id} 的 FOA constant 宽度异常")
        calendar_specs.append(
            CalendarPMSpec(
                pm_id=f"{calendar_id}@{selected_machine_id}",
                machine_id=selected_machine_id,
                model_type="periodic",
                first_start_time=first_start.mean_minutes,
                interval=to_runtime_distribution(calendar.interval),
                duration=to_runtime_distribution(calendar.duration),
            )
        )
        selected_attachments.append(attachment)

    # Confirm the requested raw overlap using only constant raw interval/FOA
    # values and the raw duration lower bounds.  LVHM is an exact same-start
    # overlap; HVLM is a duration overlap (89.2/89.4 day).  If the data no
    # longer supports the diagnostic clock, stop rather than silently changing
    # the horizon or inventing a recurrence rule.
    specs_by_suffix = {
        calendar_id.rsplit("_", 1)[-1]: (calendar, attachment)
        for calendar_id, calendar, attachment in zip(
            expected_calendar_ids,
            (pm_by_id[item] for item in expected_calendar_ids),
            selected_attachments,
            strict=True,
        )
    }

    def calendar_clock_values(
        calendar: CalendarDefinition,
        attachment: CalendarAttachmentDefinition,
    ) -> tuple[float, float, float]:
        if calendar.interval is None or calendar.interval.kind != "constant":
            raise ValueError(
                f"{calendar.calendar_id} 的 raw interval 不是 constant，"
                "不能验证固定 overlap clock"
            )
        if attachment.first_occurrence is None or attachment.first_occurrence.kind != "constant":
            raise ValueError(
                f"{calendar.calendar_id} 的 raw FOA 不是 constant，"
                "不能验证固定 overlap clock"
            )
        if calendar.duration.kind not in {"constant", "uniform"}:
            raise ValueError(
                f"{calendar.calendar_id} 的 raw duration 不是可计算固定上下界的分布："
                f"{calendar.duration.kind}"
            )
        interval = calendar.interval.parameter_1_minutes
        if interval <= 0:
            raise ValueError(f"{calendar.calendar_id} 的 raw interval 非正")
        duration_lower = calendar.duration.parameter_1_minutes - (
            calendar.duration.parameter_2_minutes or 0.0
        ) / 2
        if duration_lower <= 0:
            raise ValueError(f"{calendar.calendar_id} 的 raw duration 下界非正")
        return (
            attachment.first_occurrence.parameter_1_minutes,
            interval,
            duration_lower,
        )

    pair_left, pair_right = target["overlap_pair"]
    left_calendar, left_attachment = specs_by_suffix[pair_left]
    right_calendar, right_attachment = specs_by_suffix[pair_right]
    left_first, left_interval, left_duration_lower = calendar_clock_values(
        left_calendar, left_attachment
    )
    right_first, right_interval, right_duration_lower = calendar_clock_values(
        right_calendar, right_attachment
    )
    overlap_start: float | None = None
    left_index = 0
    right_index = 0
    # Monotone two-pointer search avoids a quadratic nested scan.  The bound is
    # an explicit diagnostic guard: raw conflicts must fail loudly rather than
    # making loader time unbounded.  Both selected real models overlap within
    # the first 13 occurrences, far below this guard.
    for _ in range(100_001):
        left_time = left_first + left_index * left_interval
        right_time = right_first + right_index * right_interval
        intervals_overlap = (
            left_time <= right_time + right_duration_lower + 1e-9
            and right_time <= left_time + left_duration_lower + 1e-9
        )
        if intervals_overlap:
            overlap_start = max(left_time, right_time)
            break
        if left_time + left_duration_lower < right_time - 1e-9:
            left_index += 1
        else:
            right_index += 1
    if overlap_start is None:
        raise ValueError(
            "raw Calendar PM FOA/interval 不支持要求的同刻 overlap："
            f"{pair_left}/{pair_right}"
        )

    duration_upper = max(
        item.duration.mean_minutes + item.duration.width_minutes / 2
        for item in calendar_specs
    )
    horizon = overlap_start + duration_upper + 1

    def machine_attachment_matches(item: CalendarAttachmentDefinition) -> bool:
        return (
            item.resource_type == "stnfam"
            and item.resource_name == template.tool_family_id
        ) or (
            item.resource_type == "stngrp"
            and item.resource_name == template.group_id
        )

    selected_ids = set(expected_calendar_ids)
    omitted_failure_ids: set[str] = set()
    omitted_calendar_pm_ids: set[str] = set()
    omitted_wafer_pm_ids: set[str] = set()
    for attachment in model.calendar_attachments:
        if not machine_attachment_matches(attachment):
            continue
        if attachment.calendar_kind == "down":
            omitted_failure_ids.add(attachment.calendar_id)
            continue
        if attachment.calendar_kind != "pm" or attachment.calendar_id in selected_ids:
            continue
        calendar = pm_by_id.get(attachment.calendar_id)
        if calendar is None:
            continue
        if calendar.interval is not None:
            omitted_calendar_pm_ids.add(attachment.calendar_id)
        elif calendar.wafer_threshold is not None:
            omitted_wafer_pm_ids.add(attachment.calendar_id)

    omitted_machine_ids = tuple(
        sorted(
            machine_id
            for machine_id in operation.eligible_machine_ids
            if machine_id != selected_machine_id
        )
    )
    omitted_route_steps = tuple(
        str(item.step_id)
        for item in route.operations
        if item.step_id != operation.step_id
    )
    omitted_prior_route_steps = tuple(
        str(item.step_id)
        for item in route.operations
        if item.step_id < operation.step_id
    )
    omitted_future_route_steps = tuple(
        str(item.step_id)
        for item in route.operations
        if item.step_id > operation.step_id
    )
    omitted_rework_links = tuple(
        f"{item.route_id}:{item.step_id}->{item.rework_step_id}"
        for item in route.operations
        if item.rework_step_id is not None
    )
    processing = to_runtime_distribution(operation.processing)
    runtime_operation = OperationSpec(
        operation.step_id,
        processing.mean_minutes,
        (selected_machine_id,),
        route_id=operation.route_id,
        tool_group_id=operation.tool_family_id,
        processing_distribution=processing,
        processing_basis=operation.processing_basis,
    )
    lot = LotSpec(
        wip.lot_id,
        0.0,
        (runtime_operation,),
        quantity_wafers=wip.quantity_wafers,
        due_time=wip.due_minutes,
        priority=wip.priority,
        is_initial_wip=True,
        product_id=wip.product_id,
        order_id=wip.order_id,
        hot_lot=wip.hot_lot,
        source_row=wip.source_row,
    )
    machine = MachineSpec(
        selected_machine_id,
        load_minutes=template.load_minutes,
        unload_minutes=template.unload_minutes,
        cascading=template.cascading,
        location_id=template.location_id,
        setup_group=template.setup_group,
    )

    def raw_distribution_text(definition: DistributionDefinition) -> str:
        second = "" if definition.parameter_2_minutes is None else str(definition.parameter_2_minutes)
        return f"{definition.kind}:{definition.parameter_1_minutes}:{second}:{definition.raw_unit}"

    calendar_mapping = ";".join(
        f"{calendar.calendar_id}->pm_id={spec.pm_id},"
        f"interval={raw_distribution_text(calendar.interval)},"
        f"duration={raw_distribution_text(calendar.duration)},"
        f"foa={raw_distribution_text(attachment.first_occurrence)}"
        for calendar, attachment, spec in zip(
            (pm_by_id[item] for item in expected_calendar_ids),
            selected_attachments,
            calendar_specs,
            strict=True,
        )
    )
    pmcal_source_rows = ";".join(
        f"{calendar.calendar_id}:{calendar.source_row}"
        for calendar in (pm_by_id[item] for item in expected_calendar_ids)
    )
    attach_source_rows = ";".join(
        f"{attachment.calendar_id}:{attachment.source_row}"
        for attachment in selected_attachments
    )
    attach_identity = ";".join(
        f"{attachment.calendar_id}:{attachment.resource_type}:{attachment.resource_name}"
        for attachment in selected_attachments
    )
    provenance_items = config.provenance_items() + (
        ("multi_calendar_slice_model", manifest.model_name),
        ("multi_calendar_slice_family", family_id),
        ("multi_calendar_slice_machine_id", selected_machine_id),
        ("multi_calendar_slice_machine_source_row", str(template.source_row)),
        ("multi_calendar_slice_machine_location", template.location_id),
        ("multi_calendar_slice_machine_cascading", str(template.cascading)),
        ("multi_calendar_slice_raw_load_minutes", str(template.load_minutes)),
        ("multi_calendar_slice_raw_unload_minutes", str(template.unload_minutes)),
        ("multi_calendar_slice_machine_setup_group", template.setup_group or ""),
        ("multi_calendar_slice_lot_id", wip.lot_id),
        ("multi_calendar_slice_wip_product_id", wip.product_id),
        ("multi_calendar_slice_route_id", operation.route_id),
        ("multi_calendar_slice_current_step", str(operation.step_id)),
        ("multi_calendar_slice_operation_source_file", route.source_file),
        ("multi_calendar_slice_operation_source_row", str(operation.source_row)),
        ("multi_calendar_slice_wip_source_row", str(wip.source_row)),
        ("multi_calendar_slice_wip_source_start_minutes", str(wip.source_start_minutes)),
        ("multi_calendar_slice_wip_source_trace", wip.source_trace or ""),
        ("multi_calendar_slice_calendar_ids", ",".join(expected_calendar_ids)),
        ("multi_calendar_slice_runtime_pm_ids", ",".join(item.pm_id for item in calendar_specs)),
        ("multi_calendar_slice_pmcal_source_rows", pmcal_source_rows),
        ("multi_calendar_slice_attach_source_rows", attach_source_rows),
        ("multi_calendar_slice_attach_identity", attach_identity),
        ("multi_calendar_slice_calendar_mapping", calendar_mapping),
        ("multi_calendar_slice_overlap_pair", f"{pair_left}/{pair_right}"),
        ("multi_calendar_slice_overlap_start_minutes", str(overlap_start)),
        ("multi_calendar_slice_horizon_minutes", str(horizon)),
        ("multi_calendar_slice_raw_eligible_machine_count", str(len(operation.eligible_machine_ids))),
        ("multi_calendar_slice_omitted_machine_ids", ",".join(omitted_machine_ids)),
        ("multi_calendar_slice_omitted_failure_calendar_ids", ",".join(sorted(omitted_failure_ids))),
        ("multi_calendar_slice_omitted_calendar_pm_ids", ",".join(sorted(omitted_calendar_pm_ids))),
        ("multi_calendar_slice_omitted_wafer_pm_ids", ",".join(sorted(omitted_wafer_pm_ids))),
        ("multi_calendar_slice_omitted_route_steps", ",".join(omitted_route_steps)),
        (
            "multi_calendar_slice_omitted_prior_route_steps",
            ",".join(omitted_prior_route_steps),
        ),
        (
            "multi_calendar_slice_omitted_future_route_steps",
            ",".join(omitted_future_route_steps),
        ),
        ("multi_calendar_slice_omitted_rework_links", ";".join(omitted_rework_links)),
        ("multi_calendar_slice_initial_setup", "unknown"),
        ("multi_calendar_slice_initial_cqt_history", "unknown"),
        ("multi_calendar_slice_initial_dedication_history", "unknown"),
        ("multi_calendar_slice_initial_wafer_pm_counter", "unknown"),
        (
            "multi_calendar_slice_profile_constraints",
            "one_initial_wip;one_current_operation;processing_basis=per_piece;"
            "sampling=None;setup=None;batch=None;interval=None;noncascade=True",
        ),
        (
            "multi_calendar_slice_scope_boundary",
            "diagnostic_only;not_full_fab;does_not_close_DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT",
        ),
    )
    provenance = DatasetProvenanceSpec(
        manifest.dataset_family,
        manifest.model_name,
        manifest.manifest_hash,
        manifest.parser_schema_version,
        manifest.loader_version,
        SMT2020_LOADER_CONTRACT_VERSION,
        provenance_items,
        tuple(
            SourceFileProvenance(item.relative_path, item.size_bytes, item.sha256)
            for item in manifest.files
        ),
    )
    return Scenario(
        scenario_id=(
            f"{manifest.model_name}:multi-calendar-validation-slice:"
            f"{operation.route_id}:{operation.step_id}:{wip.lot_id}"
        ),
        dataset_version=manifest.dataset_version,
        machines=(machine,),
        lots=(lot,),
        termination_mode="fixed_horizon",
        horizon=horizon,
        calendar_pm_specs=tuple(calendar_specs),
        dataset_provenance=provenance,
    )


def _build_sampling_validation_slice(
    model: SMT2020StaticModel,
    manifest: DatasetManifest,
    config: LoaderConfig,
) -> Scenario:
    """从真实 initial WIP 选取一个显式 StepPercent 的单工序 slice。

    选择以 ``(route_id, step_id)`` 精确定位工序，再以 lot_id/source row
    稳定定位真实 WIP。默认优先随机抽样（p<100）的无组合约束工序；显式
    selector 可选择 p=100 工序，用于验证无 sampling ledger 的边界。
    """

    product_by_route = {item.route_id: item for item in model.products}
    location_by_machine = {
        machine_id: template.location_id
        for template in model.machine_templates
        for machine_id in template.resource_instance_ids
    }
    all_operations = tuple(
        operation for route in model.routes for operation in route.operations
    )
    cqt_endpoint_keys = {
        (operation.route_id, step_id)
        for operation in all_operations
        if operation.cqt_target_step_id is not None
        for step_id in (operation.step_id, operation.cqt_target_step_id)
    }
    dedication_endpoint_keys = {
        (operation.route_id, step_id)
        for operation in all_operations
        if operation.dedication_target_step_id is not None
        for step_id in (operation.step_id, operation.dedication_target_step_id)
    }
    cascading_tool_families = {
        template.tool_family_id
        for template in model.machine_templates
        if template.cascading
    }

    def profile_supported(operation: OperationDefinition) -> bool:
        locations = {
            location_by_machine[machine_id]
            for machine_id in operation.eligible_machine_ids
            if machine_id in location_by_machine
        }
        return (
            operation.sample_percent is not None
            and operation.processing_basis == "per_lot"
            and locations == {"Fab"}
            and operation.batch_min_wafers is None
            and operation.batch_max_wafers is None
            and operation.required_setup is None
            and operation.setup_override_minutes is None
            and operation.dedication_target_step_id is None
            and (
                operation.sample_percent == 100.0
                or (operation.route_id, operation.step_id) not in cqt_endpoint_keys
            )
            and (
                operation.route_id,
                operation.step_id,
            ) not in dedication_endpoint_keys
            and operation.tool_family_id not in cascading_tool_families
            and operation.batch_interval_minutes is None
            and operation.part_interval_minutes is None
        )

    operations_by_key = {
        (route.route_id, operation.step_id): operation
        for route in model.routes
        for operation in route.operations
        if profile_supported(operation)
    }
    wip_by_key: dict[tuple[str, int], list[InitialWipDefinition]] = {}
    for wip in model.initial_wip:
        product = next(
            (item for item in model.products if item.product_id == wip.product_id),
            None,
        )
        if product is None:
            continue
        wip_by_key.setdefault((product.route_id, wip.current_step_id), []).append(wip)

    candidates = [
        (key, operation, wip)
        for key, operation in operations_by_key.items()
        for wip in wip_by_key.get(key, ())
        if operation.rework_step_id is None
    ]
    requested = config.validation_sampling_operation
    if requested is not None:
        candidates = [item for item in candidates if item[0] == requested]
        if not candidates:
            raise ValueError(
                "找不到满足 sampling validation slice 的精确真实 selector："
                f"{requested[0]}:{requested[1]}"
            )
    if not candidates:
        raise ValueError("找不到当前 raw sampling profile 中且位于 initial WIP 的真实工序")

    key, operation, wip = sorted(
        candidates,
        key=lambda item: (
            item[1].sample_percent == 100.0,
            item[0][0],
            item[0][1],
            item[2].lot_id,
            item[2].source_row or 0,
        ),
    )[0]
    product = product_by_route[key[0]]
    machine_id = operation.eligible_machine_ids[0]
    tool_template = next(
        template
        for template in model.machine_templates
        if machine_id in template.resource_instance_ids
    )
    processing = to_runtime_distribution(operation.processing)
    runtime_operation = OperationSpec(
        operation.step_id,
        processing.mean_minutes,
        (machine_id,),
        route_id=operation.route_id,
        tool_group_id=operation.tool_family_id,
        processing_distribution=processing,
        processing_basis=operation.processing_basis,
        sample_percent=operation.sample_percent,
    )
    provenance = DatasetProvenanceSpec(
        manifest.dataset_family,
        manifest.model_name,
        manifest.manifest_hash,
        manifest.parser_schema_version,
        manifest.loader_version,
        SMT2020_LOADER_CONTRACT_VERSION,
        config.provenance_items() + (
            ("sampling_slice_omitted_load_minutes", str(tool_template.load_minutes)),
            ("sampling_slice_omitted_unload_minutes", str(tool_template.unload_minutes)),
        ),
        tuple(
            SourceFileProvenance(item.relative_path, item.size_bytes, item.sha256)
            for item in manifest.files
        ),
    )
    lot = LotSpec(
        wip.lot_id,
        0.0,
        (runtime_operation,),
        quantity_wafers=wip.quantity_wafers,
        due_time=wip.due_minutes,
        priority=wip.priority,
        is_initial_wip=True,
        product_id=wip.product_id,
        order_id=wip.order_id,
        hot_lot=wip.hot_lot,
        source_row=wip.source_row,
    )
    return Scenario(
        scenario_id=(
            f"{manifest.model_name}:sampling-validation-slice:"
            f"{operation.route_id}:{operation.step_id}:{wip.lot_id}"
        ),
        dataset_version=manifest.dataset_version,
        machines=(MachineSpec(machine_id, location_id="Fab"),),
        lots=(lot,),
        termination_mode="fixed_horizon",
        horizon=processing.mean_minutes + processing.width_minutes / 2 + 1,
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
