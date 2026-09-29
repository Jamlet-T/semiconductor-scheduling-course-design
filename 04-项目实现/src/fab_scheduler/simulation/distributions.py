"""统一、可审计的分钟制随机分布采样。"""

from __future__ import annotations

from math import isfinite

from fab_scheduler.domain.models import TimeDistributionSpec
from fab_scheduler.simulation.random_streams import EntityRandomStreams


DISTRIBUTION_RUNTIME_SCHEMA_VERSION = "0.1.0"


def sample_distribution(
    spec: TimeDistributionSpec,
    *,
    random_source: EntityRandomStreams,
    stream_name: str,
    entity_id: str,
    occurrence_index: int,
) -> float:
    """按稳定 identity 抽一次样本；所有时间在进入这里前已转换为分钟。"""

    if spec.kind == "constant":
        value = spec.mean_minutes
    elif spec.kind == "uniform":
        half_width = spec.width_minutes / 2
        value = random_source.uniform(
            stream_name,
            entity_id,
            occurrence_index,
            spec.mean_minutes - half_width,
            spec.mean_minutes + half_width,
        )
    elif spec.kind == "exponential":
        # SMT2020 downcal 的 MTTF/MTTR 参数按均值解释；转换为 rate=1/mean。
        value = random_source.exponential(
            stream_name,
            entity_id,
            occurrence_index,
            mean=spec.mean_minutes,
        )
    else:  # pragma: no cover - domain validation already rejects this.
        raise ValueError(f"不支持的 distribution kind：{spec.kind}")
    if not isfinite(value) or value <= 0:
        raise ValueError("distribution sample 必须为有限正分钟数")
    return value
