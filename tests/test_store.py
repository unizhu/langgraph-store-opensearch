from unittest.mock import MagicMock

import pytest

from langchain_core.embeddings import Embeddings

from langgraph_opensearch_store.config import Settings
from langgraph_opensearch_store.schema import TemplateManager
from langgraph_opensearch_store.store import OpenSearchStore


class DummyEmbeddings(Embeddings):
    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed_documents(self, texts):  # pragma: no cover - tests only
        return [[float(len(text))] * self.dim for text in texts]

    def embed_query(self, text: str):  # pragma: no cover - trivial math
        return [float(len(text))] * self.dim


@pytest.fixture()
def store() -> OpenSearchStore:
    client = MagicMock()
    client.snapshot = MagicMock()
    settings = Settings(hosts="http://localhost:9200")
    embeddings = DummyEmbeddings(dim=settings.embedding_dim)
    client.exists.return_value = False
    return OpenSearchStore(settings=settings, client=client, embeddings=embeddings)


def test_from_params_constructs_settings():
    store = OpenSearchStore.from_params(hosts="http://localhost:9200")
    assert isinstance(store.settings, Settings)
    assert store.settings.hosts[0] == "http://localhost:9200"


def test_put_index_called(store: OpenSearchStore):
    store.put(("prefs", "user"), "k1", {"text": "hello"})
    store.client.index.assert_called_once()
    kwargs = store.client.index.call_args.kwargs
    assert kwargs["index"] == store.settings.data_index_alias
    assert "::" in kwargs["id"]
    store.client.update.assert_called_once()


def test_search_body_respects_namespace(store: OpenSearchStore):
    store.settings.search_mode = "text"
    store.client.search.return_value = {"hits": {"hits": []}}
    store.search(("prefs", "user"), query="hello", limit=2)
    args, kwargs = store.client.search.call_args
    assert kwargs["index"] == store.settings.data_index_alias
    body = kwargs["body"]
    filters = body["query"]["bool"]["filter"]
    assert any(f.get("term", {}).get("namespace_key") == "prefs::user" for f in filters)


def test_list_namespaces_filters_results(store: OpenSearchStore):
    store.client.search.return_value = {
        "hits": {
            "hits": [
                {"_source": {"namespace": ["prefs", "user"]}},
                {"_source": {"namespace": ["prefs", "other"]}},
            ]
        }
    }
    namespaces = store.list_namespaces(prefix=("prefs",), limit=1)
    assert namespaces == [("prefs", "other")]


def test_get_stats_calls_counts(store: OpenSearchStore):
    store.client.count.side_effect = [
        {"count": 5},
        {"count": 2},
    ]
    store.client.search.return_value = {"hits": {"hits": []}}
    stats = store.get_stats()
    assert stats["total_items"] == 5
    assert stats["namespace_count"] == 2


def test_put_applies_ttl(store: OpenSearchStore):
    store._handle_put = OpenSearchStore._handle_put.__get__(store)  # ensure bound? not necessary
    store.put(("prefs",), "k1", {"text": "hello"}, ttl=5)
    _, kwargs = store.client.index.call_args
    doc = kwargs["document"]
    assert doc["ttl_minutes"] == 5
    assert "ttl_expires_at" in doc


def test_get_respects_expired_docs(store: OpenSearchStore):
    expired_doc = {
        "namespace": ["prefs"],
        "key": "k1",
        "doc": {"text": "old"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "ttl_expires_at": "2000-01-01T00:00:00Z",
    }
    store.client.get.return_value = {"_source": expired_doc}
    item = store.get(("prefs",), "k1")
    assert item is None


def test_ttl_manager_delete_query(store: OpenSearchStore):
    store.client.delete_by_query.return_value = {"deleted": 1}
    result = store.ttl_manager.run_once(batch_size=50)
    assert result == {"deleted": 1}
    store.client.delete_by_query.assert_called()


def test_get_health_returns_expected_shape(store: OpenSearchStore):
    store.client.cluster = MagicMock()
    store.client.cluster.health.return_value = {"status": "green"}
    store.client.info.return_value = {"version": {"number": "3.0.0"}}
    store.client.count.side_effect = [{"count": 0}, {"count": 0}]
    store.client.search.return_value = {"hits": {"hits": []}}
    health = store.get_health()
    assert health["cluster"]["status"] == "green"
    assert "ttl" in health


def test_index_and_embedding_properties(store: OpenSearchStore):
    cfg = store.index_config
    assert cfg["data_index"] == store.settings.data_index_alias
    assert store.embeddings is store._embeddings


def test_migrate_invokes_template_manager(monkeypatch, store: OpenSearchStore):
    called = {}

    def fake_upgrade(self, rollover: bool, new_index: str | None = None):
        called["rollover"] = rollover
        called["new_index"] = new_index
        return {"rolled_over": rollover, "new_index": new_index}

    monkeypatch.setattr(TemplateManager, "upgrade", fake_upgrade, raising=False)

    result = store.migrate(rollover=True, new_index="custom")

    assert called == {"rollover": True, "new_index": "custom"}
    assert result == {"rolled_over": True, "new_index": "custom"}


def test_create_snapshot_calls_client(store: OpenSearchStore):
    store.client.snapshot.create.return_value = {"accepted": True}
    result = store.create_snapshot(
        repository="repo",
        snapshot="snap",
        indices=["a", "b"],
        wait=False,
        metadata={"source": "test"},
    )
    store.client.snapshot.create.assert_called_with(
        repository="repo",
        snapshot="snap",
        body={"indices": "a,b", "metadata": {"source": "test"}},
        wait_for_completion=False,
    )
    assert result == {"accepted": True}


def test_restore_snapshot_calls_client(store: OpenSearchStore):
    store.client.snapshot.restore.return_value = {"accepted": True}
    store.restore_snapshot(repository="repo", snapshot="snap", indices=["a"], wait=True)
    store.client.snapshot.restore.assert_called_with(
        repository="repo",
        snapshot="snap",
        body={"indices": "a"},
        wait_for_completion=True,
    )


def test_delete_snapshot_calls_client(store: OpenSearchStore):
    store.client.snapshot.delete.return_value = {"acknowledged": True}
    store.delete_snapshot(repository="repo", snapshot="snap")
    store.client.snapshot.delete.assert_called_with(repository="repo", snapshot="snap")


def _extract_knn_clause(store: OpenSearchStore, body: dict[str, object]) -> dict[str, object]:
    query = body.get("query")
    assert isinstance(query, dict)
    knn = query.get("knn")
    assert isinstance(knn, dict)
    clause = knn.get(store._embedding_field)
    assert isinstance(clause, dict)
    return clause


def test_apply_knn_query_builds_expected_structure(store: OpenSearchStore):
    body: dict[str, object] = {}
    vector = [0.5] * store.settings.embedding_dim
    store._apply_knn_query(body, {"vector": vector, "k": 3, "num_candidates": 9}, [])
    clause = _extract_knn_clause(store, body)
    assert clause["vector"] == vector
    assert clause["k"] == 3
    method_params = clause.get("method_parameters")
    assert isinstance(method_params, dict)
    assert method_params.get("ef_search") == 9


def test_apply_knn_query_embeds_filters_inline(store: OpenSearchStore):
    body: dict[str, object] = {}
    filters = [{"term": {"namespace_key": "prefs::user"}}]
    vector = [0.1] * store.settings.embedding_dim
    store._apply_knn_query(body, {"vector": vector, "k": 1}, list(filters))
    clause = _extract_knn_clause(store, body)
    filter_clause = clause.get("filter")
    assert isinstance(filter_clause, dict)
    assert "bool" in filter_clause
    bool_block = filter_clause["bool"]
    assert isinstance(bool_block, dict)
    assigned = bool_block.get("filter")
    assert isinstance(assigned, list)
    assert filters[0] in assigned


def test_knn_clause_converts_num_candidates_to_method_params(store: OpenSearchStore):
    body: dict[str, object] = {}
    vector = [0.2] * store.settings.embedding_dim
    store._apply_knn_query(body, {"vector": vector, "k": 2, "num_candidates": 1}, [])
    clause = _extract_knn_clause(store, body)
    assert "num_candidates" not in clause
    method_params = clause.get("method_parameters")
    assert isinstance(method_params, dict)
    assert method_params.get("ef_search") == 2  # max(k, num_candidates)
