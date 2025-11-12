"""Pydantic-based configuration for the OpenSearch store."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from urllib.parse import parse_qs, urlsplit

from typing import ClassVar

from pydantic import Field, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NamespacePath = tuple[str, ...]


class Settings(BaseSettings):
    """Typed configuration, validated when the module imports."""

    template_version: ClassVar[int] = 1
    deployment: Literal["local", "aws"] = "local"
    hosts: list[str] | str = Field(default_factory=lambda: ["http://localhost:9200"])
    username: str | None = None
    password: SecretStr | None = None
    auth_mode: Literal["basic", "sigv4"] = "basic"
    index_prefix: str = "langgraph"
    embedding_dim: PositiveInt = 1536
    vector_engine: Literal["lucene"] = "lucene"
    use_agentic_memory_api: bool = False
    aws_region: str | None = None
    aws_service: Literal["es", "aoss"] = "es"
    aws_role_arn: str | None = None
    aws_session_name: str = "LangGraphMemory"
    aws_web_identity_token_file: str | None = None
    verify_certs: bool = True
    ignore_ssl_certs: bool = False
    timeout: float = 30.0
    extra_headers: dict[str, str] = Field(default_factory=dict)
    search_mode: Literal["auto", "text", "vector", "hybrid"] = "auto"
    search_num_candidates: int = 200
    search_similarity_threshold: float | None = None
    ttl_minutes_default: float | None = None
    ttl_refresh_on_read: bool = False
    log_operations: bool = True
    metrics_enabled: bool = False

    model_config = SettingsConfigDict(
        env_prefix="OPENSEARCH_",
        env_file=".env",
        extra="forbid",
        case_sensitive=False,
    )

    @field_validator("hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: str | Sequence[str]) -> list[str]:  # type: ignore[override]
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
        else:
            parts = list(value)
        if not parts:
            msg = "At least one OpenSearch host is required"
            raise ValueError(msg)
        normalized = []
        for part in parts:
            normalized.append(part if part.startswith("http") else f"https://{part}")
        return normalized

    def namespace_to_index(self, namespace: NamespacePath) -> str:
        """Return the active data index alias (namespaces live in a shared index)."""
        return self.data_index_alias

    @property
    def data_index_alias(self) -> str:
        return f"{self.index_prefix}-data"

    @property
    def data_index_bootstrap(self) -> str:
        return f"{self.index_prefix}-data-v{self.template_version:02d}-000001"

    @property
    def namespace_index_name(self) -> str:
        return f"{self.index_prefix}-namespace"

    def host_urls(self) -> list[str]:
        """Return hosts as a normalized list."""
        value = self.hosts
        return value if isinstance(value, list) else [value]

    @classmethod
    def from_conn_string(cls, conn_str: str, **overrides: Any) -> "Settings":
        builder = SettingsBuilder().from_conn_string(conn_str)
        builder.with_overrides(**overrides)
        return builder.build()

    @model_validator(mode="after")
    def _apply_ssl_flags(self) -> "Settings":
        if self.ignore_ssl_certs:
            object.__setattr__(self, "verify_certs", False)
        return self

    @classmethod
    def from_env_file(cls, path: str) -> "Settings":
        """Helper to instantiate settings from a custom env file (tests/fixtures)."""
        env_path = Path(path)
        if not env_path.exists():
            msg = f"Env file not found: {path}"
            raise FileNotFoundError(msg)
        data: dict[str, Any] = {}
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
        return cls(**data)


def coerce_namespace(value: Iterable[str] | NamespacePath) -> NamespacePath:
    """Ensure namespaces are always stored as tuples."""
    if isinstance(value, tuple):
        return value
    return tuple(value)


class SettingsBuilder:
    def __init__(self, **base: Any) -> None:
        self._data: dict[str, Any] = dict(base)

    def from_env(self, env_path: str | None = None) -> "SettingsBuilder":
        env_settings = Settings.from_env_file(env_path) if env_path else Settings()
        self._data.update(env_settings.model_dump())
        return self

    def from_conn_string(self, conn_str: str) -> "SettingsBuilder":
        parsed = urlsplit(conn_str)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        hosts = [f"{parsed.scheme}://{host}:{port}"]
        self._data["hosts"] = hosts
        if parsed.username:
            self._data["username"] = parsed.username
        if parsed.password:
            self._data["password"] = parsed.password
        query = parse_qs(parsed.query)
        for key, value in query.items():
            val = value[0]
            if key == "auth_mode":
                self._data["auth_mode"] = val
            elif key == "verify_certs":
                self._data["verify_certs"] = val.lower() != "false"
            elif key in {"ignore_ssl", "ignore_ssl_certs"}:
                self._data["ignore_ssl_certs"] = val.lower() == "true"
            elif key == "search_mode":
                self._data["search_mode"] = val
            elif key == "ttl_minutes":
                self._data["ttl_minutes_default"] = float(val)
        return self

    def with_overrides(self, **overrides: Any) -> "SettingsBuilder":
        for key, value in overrides.items():
            if value is not None:
                self._data[key] = value
        return self

    def build(self) -> Settings:
        return Settings(**self._data)


@dataclass
class OpenSearchStoreConfig:
    hosts: list[str]
    auth_mode: Literal["basic", "sigv4"] = "basic"
    username: str | None = None
    password: str | None = None
    verify_certs: bool = True
    ignore_ssl_certs: bool = False
    search_mode: Literal["auto", "text", "vector", "hybrid"] = "auto"
    search_num_candidates: int = 200
    ttl_minutes_default: float | None = None
    ttl_refresh_on_read: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_settings(self) -> Settings:
        data = {
            "hosts": self.hosts,
            "auth_mode": self.auth_mode,
            "username": self.username,
            "password": self.password,
            "verify_certs": self.verify_certs,
            "ignore_ssl_certs": self.ignore_ssl_certs,
            "search_mode": self.search_mode,
            "search_num_candidates": self.search_num_candidates,
            "ttl_minutes_default": self.ttl_minutes_default,
            "ttl_refresh_on_read": self.ttl_refresh_on_read,
        }
        data.update(self.extra)
        return Settings(**{k: v for k, v in data.items() if v is not None})
