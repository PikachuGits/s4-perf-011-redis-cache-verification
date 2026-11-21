from __future__ import annotations

import uuid
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.demo import ORG_ID, SITE_MAIN, user_id


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = "postgresql+psycopg://clawhunt@127.0.0.1:55432/clawhunt_2984"
    redis_url: str = "redis://127.0.0.1:56379/0"
    cache_enabled: bool = True
    cache_key_prefix: str = "ms"
    cache_ttl_scale: float = 1.0
    admin_token: str = "dev-admin-token"

    default_org_id: uuid.UUID = ORG_ID
    default_site_id: uuid.UUID = SITE_MAIN
    default_user_id: uuid.UUID = user_id(1)

    db_pool_size: int = Field(default=20, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_recycle: int = 1800

    redis_socket_timeout: float = 0.5
    singleflight_ttl_ms: int = 5000
    singleflight_wait_ms: int = 1000


@lru_cache
def get_settings() -> Settings:
    return Settings()
