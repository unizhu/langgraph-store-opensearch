"""Composable index templates, mappings, and setup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings

def data_index_template(settings: Settings) -> dict[str, Any]:
    return {
        "index_patterns": [f"{settings.index_prefix}-data-*"],
        "template": {
            "settings": {
                "index": {
                    "knn": True,
                    "query": {"default_field": "doc.text"},
                }
            },
            "mappings": {
                "properties": {
                    "namespace": {"type": "keyword"},
                    "namespace_key": {"type": "keyword"},
                    "key": {"type": "keyword"},
                    "depth": {"type": "integer"},
                    "metadata": {"type": "object", "enabled": True},
                    "doc": {"type": "object", "enabled": True},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": settings.embedding_dim,
                        "method": {
                            "name": "hnsw",
                            "engine": settings.vector_engine,
                            "space_type": "cosinesimil",
                        },
                    },
                    "ttl_expires_at": {"type": "date", "null_value": None},
                }
            },
        },
    }


def namespace_index_body() -> dict[str, Any]:
    return {
        "settings": {
            "index": {
                "refresh_interval": "1s",
            }
        },
        "mappings": {
            "properties": {
                "namespace": {"type": "keyword"},
                "namespace_key": {"type": "keyword"},
                "depth": {"type": "integer"},
                "doc_count": {"type": "long"},
                "updated_at": {"type": "date"},
            }
        },
    }


@dataclass
class TemplateManager:
    client: Any
    settings: Settings

    def apply(self) -> None:
        self._ensure_data_template()
        self._ensure_namespace_index()
        self._ensure_bootstrap_index()

    # -------------------------------------------------
    def _ensure_data_template(self) -> None:
        template_name = f"{self.settings.index_prefix}-data-template-v{self.settings.template_version}"
        body = data_index_template(self.settings)
        self.client.indices.put_index_template(name=template_name, body=body, create=False)

    def _ensure_bootstrap_index(self) -> None:
        index_name = self.settings.data_index_bootstrap
        exists = self.client.indices.exists(index=index_name)
        if not exists:
            body = data_index_template(self.settings)["template"]
            self.client.indices.create(index=index_name, body=body, ignore=[400])
        self.client.indices.put_alias(index=index_name, name=self.settings.data_index_alias, ignore=[404])

    def _ensure_namespace_index(self) -> None:
        index_name = self.settings.namespace_index_name
        exists = self.client.indices.exists(index=index_name)
        if not exists:
            self.client.indices.create(index=index_name, body=namespace_index_body(), ignore=[400])
