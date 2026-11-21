"""Redis 位图 Bloom 过滤器，用于字典项查询的穿透前置判断。

不依赖 RedisBloom 模块，只用 SETBIT/GETBIT，便于私有化环境直接运行。
"""
from __future__ import annotations

import hashlib

from redis import Redis


class RedisBloom:
    def __init__(
        self,
        redis_client: Redis,
        key: str,
        bits: int = 65536,
        hashes: int = 5,
        ttl_seconds: int = 3600,
    ) -> None:
        self.redis = redis_client
        self.key = key
        self.bits = bits
        self.hashes = hashes
        self.ttl_seconds = ttl_seconds

    def _indexes(self, value: str) -> list[int]:
        indexes: list[int] = []
        for i in range(self.hashes):
            digest = hashlib.blake2b(
                value.encode("utf-8"), digest_size=16, person=f"msp{i}".encode()
            ).digest()
            indexes.append(int.from_bytes(digest[:8], "big") % self.bits)
        return indexes

    def build(self, values: list[str], ttl_seconds: int = 3600) -> int:
        pipe = self.redis.pipeline(transaction=False)
        pipe.delete(self.key)
        for value in values:
            for index in self._indexes(value):
                pipe.setbit(self.key, index, 1)
        pipe.expire(self.key, ttl_seconds or self.ttl_seconds)
        pipe.execute()
        return len(values)

    def add(self, value: str, ttl_seconds: int = 3600) -> None:
        pipe = self.redis.pipeline(transaction=False)
        for index in self._indexes(value):
            pipe.setbit(self.key, index, 1)
        pipe.expire(self.key, ttl_seconds or self.ttl_seconds)
        pipe.execute()

    def exists(self) -> bool:
        return bool(self.redis.exists(self.key))

    def might_contain(self, value: str) -> bool:
        pipe = self.redis.pipeline(transaction=False)
        for index in self._indexes(value):
            pipe.getbit(self.key, index)
        return all(int(bit) for bit in pipe.execute())

    def delete(self) -> None:
        self.redis.delete(self.key)
