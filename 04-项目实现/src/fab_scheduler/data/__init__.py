"""数据身份与后续 SMT2020 读取接口。"""

from fab_scheduler.data.identity import DatasetIdentity, identify_dataset
from fab_scheduler.data.manifest import DatasetManifest, ManifestFile, build_dataset_manifest
from fab_scheduler.data.smt2020_loader import (
    LoadedScenario,
    LoaderAuditEntry,
    LoaderConfig,
    LoaderError,
    SMT2020_LOADER_CONTRACT_VERSION,
    SMT2020_LOADER_VERSION,
    load_smt2020,
    parse_distribution,
)

__all__ = [
    "DatasetIdentity", "DatasetManifest", "LoadedScenario", "LoaderAuditEntry",
    "LoaderConfig", "LoaderError", "ManifestFile", "SMT2020_LOADER_CONTRACT_VERSION",
    "SMT2020_LOADER_VERSION", "build_dataset_manifest", "identify_dataset",
    "load_smt2020", "parse_distribution",
]
