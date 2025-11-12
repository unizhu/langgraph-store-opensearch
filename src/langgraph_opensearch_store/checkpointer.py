"""Helpers to bridge MemorySaver checkpoints with the OpenSearchStore."""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Protocol

from .store import OpenSearchStore


class MemorySaverProtocol(Protocol):
    def save(self, payload: dict[str, Any]) -> None: ...


class OpenSearchCheckpointer:
    """Pairs a `MemorySaver` with the durable OpenSearch store."""

    def __init__(self, saver: MemorySaverProtocol, store: OpenSearchStore) -> None:
        self.saver = saver
        self.store = store

    def save_checkpoint(self, namespace: Iterable[str], payload: dict[str, Any]) -> None:
        """Persist short-term state as usual, but mirror key facts to OpenSearch."""
        tuple_namespace = tuple(namespace)
        self.saver.save(dict(payload))
        self.store.put(tuple_namespace, key=str(uuid.uuid4()), value=payload)

    def promote_fact(self, namespace: Iterable[str], message: str) -> None:
        """Allow agents to copy ad-hoc strings into long-term storage."""
        tuple_namespace = tuple(namespace)
        self.store.put(tuple_namespace, key=str(uuid.uuid4()), value={"text": message})
