import importlib
import os
from collections.abc import Iterator
from typing import Iterable, Protocol, cast

import pytest

from langgraph_opensearch_store.store import OpenSearchStore


class _PostgresStoreProto(Protocol):
    @classmethod
    def from_conn_string(cls, dsn: str) -> "_PostgresStoreProto":
        ...

    def setup(self) -> None:
        ...

    def put(self, namespace, key, value) -> None:
        ...

    def delete(self, namespace, key) -> None:
        ...

    def get(self, namespace, key):
        ...

    def search(self, namespace_prefix, *, query: str | None, limit: int):
        ...

    def list_namespaces(self, prefix):
        ...

    def get_stats(self):
        ...

def _load_postgres_store() -> type[_PostgresStoreProto] | None:
    try:
        module = importlib.import_module("langgraph.store.postgres")
    except ModuleNotFoundError:
        return None
    return cast(type[_PostgresStoreProto], getattr(module, "PostgresStore", None))


PostgresStore = _load_postgres_store()

DATASET = [
    (("prefs", "user_a"), "color", {"text": "I like blue"}),
    (("prefs", "user_a"), "food", {"text": "I like pizza"}),
    (("prefs", "user_b"), "color", {"text": "I like red"}),
]

pytestmark = pytest.mark.contract


def _require_env() -> tuple[str, str]:
    if PostgresStore is None:
        pytest.skip("Postgres store not available")
    pg_dsn = os.getenv("POSTGRES_DSN")
    os_conn = os.getenv("OPENSEARCH_CONN")
    if not pg_dsn or not os_conn:
        pytest.skip("Set POSTGRES_DSN and OPENSEARCH_CONN to run contract tests")
    return pg_dsn, os_conn


@pytest.fixture()
def reference_store() -> Iterator[_PostgresStoreProto]:
    pg_dsn, _ = _require_env()
    if PostgresStore is None:
        pytest.skip("langgraph[postgres] extra not installed")
    pg_cls = cast(type[_PostgresStoreProto], PostgresStore)
    store: _PostgresStoreProto = pg_cls.from_conn_string(pg_dsn)
    store.setup()
    _load_dataset(store)
    yield store
    _truncate_store(store)


@pytest.fixture()
def opensearch_store():
    _, os_conn = _require_env()
    store = OpenSearchStore.from_conn_string(os_conn)
    store.setup()
    _load_dataset(store)
    yield store


def _load_dataset(store: _PostgresStoreProto | OpenSearchStore) -> None:
    for namespace, key, doc in DATASET:
        store.put(namespace, key, doc)


def _truncate_store(store: _PostgresStoreProto) -> None:
    for namespace, key, _ in DATASET:
        store.delete(namespace, key)


def _sorted(items: Iterable):
    return sorted(items, key=lambda item: (tuple(item.namespace), item.key))


def test_parity_get(reference_store, opensearch_store):
    for namespace, key, _ in DATASET:
        ref = reference_store.get(namespace, key)
        ours = opensearch_store.get(namespace, key)
        assert ref.value == ours.value


def test_parity_search(reference_store, opensearch_store):
    ref_results = reference_store.search(("prefs",), query="like", limit=10)
    os_results = opensearch_store.search(("prefs",), query="like", limit=10)
    assert len(ref_results) == len(os_results)


def test_parity_list_namespaces(reference_store, opensearch_store):
    ref_list = reference_store.list_namespaces(prefix=("prefs",))
    os_list = opensearch_store.list_namespaces(prefix=("prefs",))
    assert set(ref_list) == set(os_list)


def test_parity_stats(reference_store, opensearch_store):
    ref_stats = reference_store.get_stats()
    os_stats = opensearch_store.get_stats()
    assert ref_stats["total_items"] == os_stats["total_items"]
