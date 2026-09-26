"""SMT2020 原始文件的稳定、只读 manifest。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


MANIFEST_SCHEMA_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class ManifestFile:
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_family: str
    model_name: str
    parser_schema_version: str
    loader_version: str
    files: tuple[ManifestFile, ...]
    manifest_hash: str
    source_root: str
    loaded_at_utc: str

    @property
    def dataset_version(self) -> str:
        return f"{self.model_name}@sha256:{self.manifest_hash}"


def _identity_payload(
    model_name: str,
    loader_version: str,
    files: tuple[ManifestFile, ...],
) -> dict[str, object]:
    return {
        "dataset_family": "SMT2020",
        "model_name": model_name,
        "parser_schema_version": MANIFEST_SCHEMA_VERSION,
        "loader_version": loader_version,
        "files": [
            {
                "relative_path": item.relative_path,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in files
        ],
    }


def build_dataset_manifest(
    model_root: Path,
    *,
    model_name: str,
    loader_version: str,
) -> DatasetManifest:
    """按逻辑相对路径和原始字节生成跨机器稳定的 identity。"""

    root = model_root.resolve()
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not paths:
        raise ValueError(f"数据集目录为空：{root}")
    files = tuple(
        ManifestFile(
            relative_path=path.relative_to(root).as_posix(),
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in paths
    )
    canonical = json.dumps(
        _identity_payload(model_name, loader_version, files),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DatasetManifest(
        dataset_family="SMT2020",
        model_name=model_name,
        parser_schema_version=MANIFEST_SCHEMA_VERSION,
        loader_version=loader_version,
        files=files,
        manifest_hash=hashlib.sha256(canonical).hexdigest(),
        source_root=str(root),
        loaded_at_utc=datetime.now(timezone.utc).isoformat(),
    )

