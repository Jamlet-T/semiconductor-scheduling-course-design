"""与调用顺序无关的实体索引随机流。"""

from __future__ import annotations

import hashlib
import random


class EntityRandomStreams:
    """从根种子、流名、实体和发生次数派生独立随机量。"""

    def __init__(self, seed: int) -> None:
        self.seed = seed

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
        generator = random.Random(
            self.derived_seed(stream, entity_key, occurrence)
        )
        return generator.uniform(low, high)
