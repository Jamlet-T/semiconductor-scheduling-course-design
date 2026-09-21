"""只读数据集身份计算。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fab_scheduler.data.manifest import build_dataset_manifest


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    name: str
    sha256: str
    file_count: int

    @property
    def version(self) -> str:
        return f"{self.name}@sha256:{self.sha256}"


def identify_dataset(directory: Path) -> DatasetIdentity:
    """按相对路径和原始字节计算稳定 manifest hash，不修改文件。"""

    directory = directory.resolve()
    manifest = build_dataset_manifest(
        directory,
        model_name=directory.name,
        loader_version="0.1.0",
    )
    return DatasetIdentity(
        name=directory.name,
        sha256=manifest.manifest_hash,
        file_count=len(manifest.files),
    )
