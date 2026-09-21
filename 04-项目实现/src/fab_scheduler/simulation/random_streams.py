"""与调用顺序无关的实体索引随机流。"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass


RANDOM_SAMPLE_LEDGER_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class RandomSampleRecord:
    """一次真正请求的外生随机样本及其稳定 identity。"""

    stream_name: str
    entity_id: str
    occurrence_index: int
    distribution: str
    parameters: tuple[float, ...]
    value: float
    derived_seed: int

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.stream_name, self.entity_id, self.occurrence_index)


class EntityRandomStreams:
    """从根种子、流名、实体和发生次数派生独立随机量。"""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._ledger: dict[tuple[str, str, int], RandomSampleRecord] = {}

    def derived_seed(
        self,
        stream: str,
        entity_key: str,
        occurrence: int,
    ) -> int:
        if not stream or not entity_key:
            raise ValueError("stream 和 entity_key 不能为空")
        if occurrence < 0:
            raise ValueError("occurrence 不能为负")
        material = f"{self.seed}\0{stream}\0{entity_key}\0{occurrence}".encode()
        digest = hashlib.sha256(material).digest()
        return int.from_bytes(digest[:16], byteorder="big", signed=False)

    def uniform(
        self,
        stream: str,
        entity_key: str,
        occurrence: int,
        low: float,
        high: float,
    ) -> float:
        if high < low:
            raise ValueError("uniform 上界不能小于下界")
        identity = (stream, entity_key, occurrence)
        existing = self._ledger.get(identity)
        if existing is not None:
            if existing.distribution != "uniform" or existing.parameters != (
                low,
                high,
            ):
                raise ValueError("相同随机 identity 使用了不同分布参数")
            return existing.value
        derived_seed = self.derived_seed(stream, entity_key, occurrence)
        generator = random.Random(derived_seed)
        value = generator.uniform(low, high)
        self._ledger[identity] = RandomSampleRecord(
            stream_name=stream,
            entity_id=entity_key,
            occurrence_index=occurrence,
            distribution="uniform",
            parameters=(low, high),
            value=value,
            derived_seed=derived_seed,
        )
        return value

    @property
    def ledger(self) -> tuple[RandomSampleRecord, ...]:
        return tuple(self._ledger[key] for key in sorted(self._ledger))
