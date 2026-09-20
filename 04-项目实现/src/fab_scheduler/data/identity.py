"""只读数据集身份计算。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


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
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"数据集目录为空：{directory}")
    manifest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
        manifest.update(relative)
        manifest.update(b"\0")
        manifest.update(file_digest)
        manifest.update(b"\n")
    return DatasetIdentity(
        name=directory.name,
        sha256=manifest.hexdigest(),
        file_count=len(files),
    )
