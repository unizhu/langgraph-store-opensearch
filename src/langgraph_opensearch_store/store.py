"""LangGraph BaseStore implementation backed by OpenSearch."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Sequence

from langchain_core.embeddings import Embeddings
from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    NOT_PROVIDED,
    PutOp,
    SearchItem,
    SearchOp,
)

from .client import create_client
from .config import NamespacePath, Settings
from .schema import TemplateManager

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
logger = logging.getLogger("langgraph.opensearch.store")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_ts(value: datetime) -> str:
    return value.strftime(ISO_FORMAT)


def _parse_ts(raw: str | None) -> datetime:
    if not raw:
        return _now()
    for fmt in (ISO_FORMAT, "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return _now()


def _namespace_key(namespace: NamespacePath) -> str:
    return "::".join(namespace)


def _document_id(namespace: NamespacePath, key: str) -> str:
    return f"{_namespace_key(namespace)}::{key}"


def _extract_condition(conditions, match_type: str) -> NamespacePath | None:
    for condition in conditions:
        if getattr(condition, "match_type", None) == match_type:
            path = tuple(segment for segment in condition.path if segment != "*")
            if path:
                return path
    return None


def _suffix_matches(namespace: NamespacePath, suffix: NamespacePath) -> bool:
    if not suffix:
        return True
    if len(suffix) > len(namespace):
        return False
    return namespace[-len(suffix) :] == tuple(suffix)


def _compute_ttl_expires(ttl_minutes: float | None) -> str | None:
    if ttl_minutes is None:
        return None
    expires_at = _now() + timedelta(minutes=ttl_minutes)
    return _serialize_ts(expires_at)


class OpenSearchStore(BaseStore):
    """Concrete BaseStore that persists documents to OpenSearch."""

    supports_ttl = True

    def __init__(
        self,
        *,
        settings: Settings,
        client: Any | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._embeddings = embeddings
        self._metrics = MetricsEmitter(enabled=settings.metrics_enabled)
        self._ttl_manager = TTLManager(self)

    @classmethod
    def from_settings(
        cls,
        *,
        settings: Settings,
        embeddings: Embeddings | None = None,
    ) -> "OpenSearchStore":
        return cls(settings=settings, client=None, embeddings=embeddings)

    @classmethod
    def from_params(
        cls,
        *,
        embeddings: Embeddings | None = None,
        **settings_kwargs: Any,
    ) -> "OpenSearchStore":
        """Instantiate the store from keyword args instead of relying on `.env`."""

        settings = Settings(**settings_kwargs)
        return cls(settings=settings, client=None, embeddings=embeddings)

    @classmethod
    def from_conn_string(
        cls,
        conn_str: str,
        *,
        embeddings: Embeddings | None = None,
        **overrides: Any,
    ) -> "OpenSearchStore":
        settings = Settings.from_conn_string(conn_str, **overrides)
        return cls(settings=settings, client=None, embeddings=embeddings)

    def setup(self, *, client: Any | None = None) -> None:
        es = client or self.client
        TemplateManager(es, self.settings).apply()

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = create_client(self.settings)
        return self._client

    @property
    def ttl_manager(self) -> "TTLManager":
        return self._ttl_manager

    @property
    def ttl_config(self) -> dict[str, Any] | None:  # type: ignore[override]
        if not self.supports_ttl:
            return None
        default_ttl = self.settings.ttl_minutes_default
        refresh_on_read = self.settings.ttl_refresh_on_read
        last_run = self.ttl_manager.last_run_at
        return {
            "default_ttl": default_ttl,
            "refresh_on_read": refresh_on_read,
            "default_ttl_minutes": default_ttl,
            "last_sweep": last_run.isoformat() if last_run else None,
        }

    @property
    def index_config(self) -> dict[str, Any]:
        return {
            "data_index": self.settings.data_index_alias,
            "namespace_index": self.settings.namespace_index_name,
            "template_version": self.settings.template_version,
        }

    @property
    def embeddings(self) -> Embeddings | None:  # type: ignore[override]
        return self._embeddings

    def migrate(self, *, rollover: bool = False, new_index: str | None = None) -> dict[str, Any]:
        manager = TemplateManager(self.client, self.settings)
        return manager.upgrade(rollover=rollover, new_index=new_index)

    def create_snapshot(
        self,
        *,
        repository: str,
        snapshot: str,
        indices: Sequence[str] | None = None,
        wait: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> Any:
        body: dict[str, Any] = {}
        if indices:
            body["indices"] = ",".join(indices)
        if metadata:
            body["metadata"] = dict(metadata)
        response = self.client.snapshot.create(
            repository=repository,
            snapshot=snapshot,
            body=body or None,
            wait_for_completion=wait,
        )
        return response

    def restore_snapshot(
        self,
        *,
        repository: str,
        snapshot: str,
        indices: Sequence[str] | None = None,
        wait: bool = True,
    ) -> Any:
        body: dict[str, Any] = {}
        if indices:
            body["indices"] = ",".join(indices)
        response = self.client.snapshot.restore(
            repository=repository,
            snapshot=snapshot,
            body=body or None,
            wait_for_completion=wait,
        )
        return response

    def delete_snapshot(self, *, repository: str, snapshot: str) -> Any:
        return self.client.snapshot.delete(repository=repository, snapshot=snapshot)

    # ------------------------------------------------------------------
    # BaseStore API
    def batch(self, ops: Iterable[Op]) -> list[Any]:
        return [self._execute_op(op) for op in ops]

    async def abatch(self, ops: Iterable[Op]) -> list[Any]:
        return await asyncio.gather(*[asyncio.to_thread(self._execute_op, op) for op in ops])

    # ------------------------------------------------------------------
    # Internal helpers
    def _execute_op(self, op: Op) -> Any:
        start = time.perf_counter()
        op_name = type(op).__name__
        success = True
        try:
            if isinstance(op, PutOp):
                return self._handle_put(op)
            if isinstance(op, GetOp):
                return self._handle_get(op)
            if isinstance(op, SearchOp):
                return self._handle_search(op)
            if isinstance(op, ListNamespacesOp):
                return self._handle_list_namespaces(op)
            raise NotImplementedError(f"Unhandled op type: {type(op).__name__}")
        except Exception:
            success = False
            raise
        finally:
            duration = time.perf_counter() - start
            if self.settings.log_operations:
                logger.info(
                    "operation=%s duration_ms=%.3f",
                    op_name,
                    duration * 1000,
                )
            self._metrics.record(
                "operation_duration",
                duration,
                {
                    "operation": op_name,
                    "success": success,
                },
            )

    def _handle_put(self, op: PutOp) -> None:
        namespace = op.namespace
        index = self.settings.data_index_alias
        doc_id = _document_id(namespace, op.key)
        existed = self._doc_exists(doc_id)

        if op.value is None:
            if existed:
                self.client.delete(index=index, id=doc_id, ignore=[404])
                self._update_namespace_stats(namespace, delta=-1)
            return None

        ttl_minutes = self._resolve_ttl_minutes(op.ttl)
        payload = self._document_body(namespace, op.key, op.value, ttl_minutes=ttl_minutes)
        self.client.index(index=index, id=doc_id, document=payload)
        self._update_namespace_stats(namespace, delta=0 if existed else 1)
        return None

    def _handle_get(self, op: GetOp) -> Item | None:
        index = self.settings.data_index_alias
        doc_id = _document_id(op.namespace, op.key)
        try:
            resp = self.client.get(index=index, id=doc_id)
        except Exception:
            return None
        source = resp.get("_source", {})
        if self._is_expired(source):
            self.client.delete(index=index, id=doc_id, ignore=[404])
            self._update_namespace_stats(op.namespace, delta=-1)
            return None
        if self._should_refresh_ttl(op.refresh_ttl, source):
            self._refresh_ttl(doc_id, source)
        return self._item_from_source(op.namespace, op.key, source)

    def _handle_search(self, op: SearchOp) -> list[SearchItem]:
        mode = self._determine_search_mode(op.query)
        filters = self._build_filters(op.namespace_prefix, op.filter)
        hits: list[dict[str, Any]]
        if mode == "vector":
            hits = self._vector_search(op.query, filters, op.limit, op.offset)
        elif mode == "hybrid":
            hits = self._hybrid_search(op.query, filters, op.limit, op.offset)
        else:
            hits = self._text_search(op.query, filters, op.limit, op.offset)
        return self._hits_to_items(hits, op.refresh_ttl)

    def _document_body(
        self,
        namespace: NamespacePath,
        key: str,
        value: Mapping[str, Any],
        *,
        ttl_minutes: float | None = None,
    ) -> dict[str, Any]:
        now = _now()
        body = {
            "namespace": list(namespace),
            "namespace_key": _namespace_key(namespace),
            "depth": len(namespace),
            "key": key,
            "doc": dict(value),
            "created_at": _serialize_ts(now),
            "updated_at": _serialize_ts(now),
        }
        expires_at = _compute_ttl_expires(ttl_minutes)
        if expires_at is not None:
            body["ttl_expires_at"] = expires_at
            body["ttl_minutes"] = ttl_minutes
        if self._embeddings is not None:
            text = self._extract_text(value)
            if text:
                try:
                    embedding_vector = self._embeddings.embed_query(text)
                except Exception:  # pragma: no cover - provider failures
                    logger.warning("embedding_failure", exc_info=True)
                    embedding_vector = None
                if embedding_vector:
                    body["embedding"] = embedding_vector
                else:
                    logger.debug(
                        "embedding_skip", extra={"namespace": namespace, "key": key}
                    )
        return body

    def _extract_text(self, value: Mapping[str, Any]) -> str | None:
        for candidate in ("text", "body", "content"):
            maybe = value.get(candidate)
            if isinstance(maybe, str):
                return maybe
        return None

    def _determine_search_mode(self, query: str | None) -> Literal["text", "vector", "hybrid"]:
        configured = self.settings.search_mode
        if configured != "auto":
            return "hybrid" if configured == "hybrid" else configured  # type: ignore[return-value]
        if query and self._embeddings is not None:
            return "hybrid"
        return "text"

    def _build_filters(self, namespace: NamespacePath, metadata_filter: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [
            {"term": {"namespace_key": _namespace_key(namespace)}}
        ]
        ttl_filter = self._ttl_filter_clause()
        if ttl_filter is not None:
            filters.append(ttl_filter)
        if metadata_filter:
            for key, value in metadata_filter.items():
                filters.append({"term": {f"doc.{key}": value}})
        return filters

    def _ttl_filter_clause(self) -> dict[str, Any] | None:
        if self.settings.ttl_minutes_default is None and not self.settings.ttl_refresh_on_read:
            # TTL disabled globally; filters still needed when doc has ttl but easiest is to always allow both
            pass
        return {
            "bool": {
                "should": [
                    {"bool": {"must_not": {"exists": {"field": "ttl_expires_at"}}}},
                    {"range": {"ttl_expires_at": {"gt": _serialize_ts(_now())}}},
                ],
                "minimum_should_match": 1,
            }
        }

    def _text_search(
        self,
        query: str | None,
        filters: list[dict[str, Any]],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        must_clause: dict[str, Any]
        if query:
            must_clause = {"match": {"doc": query}}
        else:
            must_clause = {"match_all": {}}
        body = {
            "from": offset,
            "size": limit,
            "query": {"bool": {"must": must_clause, "filter": filters}},
        }
        resp = self.client.search(index=self.settings.data_index_alias, body=body)
        return resp.get("hits", {}).get("hits", [])

    def _vector_search(
        self,
        query: str | None,
        filters: list[dict[str, Any]],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        if self._embeddings is None or not query:
            return self._text_search(query, filters, limit, offset)
        vector = self._embeddings.embed_query(query)
        size = limit + offset
        knn_payload = {
            "vector": vector,
            "k": size,
            "num_candidates": max(size * 2, self.settings.search_num_candidates),
        }
        if self.settings.search_similarity_threshold is not None:
            knn_payload["similarity_cutoff"] = self.settings.search_similarity_threshold
        body = {"size": size}
        self._apply_knn_query(body, knn_payload, filters)
        resp = self.client.search(index=self.settings.data_index_alias, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        return hits[offset:offset + limit]

    def _hybrid_search(
        self,
        query: str | None,
        filters: list[dict[str, Any]],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        text_hits = self._text_search(query, filters, limit + offset, 0)
        vector_hits = self._vector_search(query, filters, limit + offset, 0)
        scores: dict[str, float] = {}
        hits: dict[str, dict[str, Any]] = {}

        def rank_items(items: list[dict[str, Any]], weight: float) -> None:
            for rank, hit in enumerate(items, start=1):
                doc_id = hit.get("_id") or hit.get("_source", {}).get("key")
                if not doc_id:
                    continue
                hits[doc_id] = hit
                scores[doc_id] = scores.get(doc_id, 0.0) + weight / (rank + 1)

        rank_items(text_hits, weight=1.0)
        rank_items(vector_hits, weight=1.0)
        ranked_ids = sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)
        sliced = ranked_ids[offset: offset + limit]
        return [hits[doc_id] for doc_id in sliced]

    def _hits_to_items(self, hits: list[dict[str, Any]], refresh_ttl: bool | None) -> list[SearchItem]:
        items: list[SearchItem] = []
        for hit in hits:
            source = hit.get("_source", {})
            doc_id = hit.get("_id")
            namespace = tuple(source.get("namespace", []))
            key = source.get("key", doc_id)
            if self._is_expired(source):
                if doc_id:
                    self.client.delete(index=self.settings.data_index_alias, id=doc_id, ignore=[404])
                continue
            if doc_id and self._should_refresh_ttl(refresh_ttl, source):
                self._refresh_ttl(doc_id, source)
            item = self._item_from_source(namespace, key, source)
            items.append(
                SearchItem(
                    namespace=item.namespace,
                    key=item.key,
                    value=item.value,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    score=hit.get("_score"),
                )
            )
        return items

    def _item_from_source(self, namespace: NamespacePath, key: str, source: dict[str, Any]) -> Item:
        doc = source.get("doc") or {}
        created = _parse_ts(source.get("created_at"))
        updated = _parse_ts(source.get("updated_at"))
        return Item(
            namespace=namespace,
            key=key,
            value=dict(doc),
            created_at=created,
            updated_at=updated,
        )

    def _search_body(self, namespace: NamespacePath, query: str, limit: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "size": limit,
            "query": {
                "bool": {
                    "must": {"match": {"doc": query}},
                    "filter": [{"term": {"namespace_key": _namespace_key(namespace)}}],
                }
            },
        }
        if self._embeddings is not None:
            vector = self._embeddings.embed_query(query)
            self._apply_knn_query(
                body,
                {
                    "vector": vector,
                    "k": limit,
                    "num_candidates": max(limit * 4, 20),
                },
                [],
            )
        return body

    def _apply_knn_query(
        self,
        body: dict[str, Any],
        payload: dict[str, Any],
        filters: list[dict[str, Any]],
    ) -> None:
        clause = self._format_knn_clause(payload)
        if filters:
            self._merge_knn_filters(clause[self._embedding_field], filters)
        body["query"] = {"knn": clause}

    def _format_knn_clause(self, payload: dict[str, Any]) -> dict[str, Any]:
        field_name = self._embedding_field
        modern = dict(payload)
        num_candidates = modern.pop("num_candidates", None)
        if num_candidates is not None:
            method_params = modern.setdefault("method_parameters", {})
            if "ef_search" not in method_params:
                ef_search = self._calculate_ef_search(modern, num_candidates)
                if ef_search is not None:
                    method_params["ef_search"] = ef_search
        return {field_name: modern}

    def _calculate_ef_search(self, payload: dict[str, Any], num_candidates: Any) -> int | None:
        try:
            ef_search = int(num_candidates)
            if ef_search <= 0:
                return None
            k_value = payload.get("k")
            if k_value is not None:
                k_int = max(int(k_value), 1)
                ef_search = max(ef_search, k_int)
            return ef_search
        except Exception:
            return None

    def _merge_knn_filters(self, clause: dict[str, Any], filters: list[dict[str, Any]]) -> None:
        existing = clause.get("filter")
        additional = {"bool": {"filter": filters}}
        if not existing:
            clause["filter"] = additional
            return
        if isinstance(existing, dict) and "bool" in existing:
            bool_section = existing.setdefault("bool", {})
            current_filters = bool_section.setdefault("filter", [])
            if isinstance(current_filters, list):
                current_filters.extend(filters)
                return
        clause["filter"] = {
            "bool": {
                "filter": [existing, additional],
            }
        }

    @property
    def _embedding_field(self) -> str:
        return "embedding"

    def _handle_list_namespaces(self, op: ListNamespacesOp) -> list[tuple[str, ...]]:
        prefix_path = _extract_condition(op.match_conditions, "prefix")
        suffix_path = _extract_condition(op.match_conditions, "suffix")
        size = min(max(op.limit + op.offset, 50), 1000)
        filters: list[dict[str, Any]] = []
        if prefix_path:
            filters.append({"prefix": {"namespace_key": _namespace_key(prefix_path)}})
        query: dict[str, Any]
        if filters:
            query = {"bool": {"filter": filters}}
        else:
            query = {"match_all": {}}
        body = {
            "size": size,
            "query": query,
            "sort": [{"namespace_key": "asc"}],
        }
        resp = self.client.search(index=self.settings.namespace_index_name, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        namespaces: list[tuple[str, ...]] = []
        for hit in hits:
            raw_ns = hit.get("_source", {}).get("namespace", [])
            if not isinstance(raw_ns, list):
                continue
            ns_tuple: tuple[str, ...] = tuple(raw_ns)
            if suffix_path and not _suffix_matches(ns_tuple, suffix_path):
                continue
            if op.max_depth is not None and len(ns_tuple) > op.max_depth:
                ns_tuple = ns_tuple[: op.max_depth]
            namespaces.append(ns_tuple)
        namespaces = sorted(dict.fromkeys(namespaces))
        start = min(op.offset, len(namespaces))
        end = min(start + op.limit, len(namespaces))
        return namespaces[start:end]

    def get_stats(self) -> dict[str, Any]:
        total = self.client.count(index=self.settings.data_index_alias).get("count", 0)
        namespaces = self.client.count(index=self.settings.namespace_index_name).get("count", 0)
        oldest = self._fetch_single_doc(order="asc")
        newest = self._fetch_single_doc(order="desc")
        top_namespaces = self._top_namespaces()
        return {
            "total_items": total,
            "namespace_count": namespaces,
            "oldest_item": oldest,
            "newest_item": newest,
            "top_namespaces": top_namespaces,
        }

    def get_health(self) -> dict[str, Any]:
        cluster_health = self.client.cluster.health() if hasattr(self.client, "cluster") else {}
        info = self.client.info() if hasattr(self.client, "info") else {}
        sweeper = self.ttl_manager.last_result or {}
        return {
            "template_version": self.settings.template_version,
            "cluster": cluster_health,
            "cluster_info": info,
            "ttl": {
                "enabled": self.settings.ttl_minutes_default is not None,
                "last_run_at": self.ttl_manager.last_run_at.isoformat() if self.ttl_manager.last_run_at else None,
                "last_result": sweeper,
            },
            "indices": {
                "data_alias": self.settings.data_index_alias,
                "namespace_index": self.settings.namespace_index_name,
            },
        }

    def _fetch_single_doc(self, order: str) -> dict[str, Any] | None:
        body = {
            "size": 1,
            "sort": [{"created_at": {"order": order}}],
        }
        resp = self.client.search(index=self.settings.data_index_alias, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            return None
        source = hits[0].get("_source", {})
        return {
            "namespace": tuple(source.get("namespace", [])),
            "key": source.get("key"),
            "created_at": source.get("created_at"),
        }

    def _doc_exists(self, doc_id: str) -> bool:
        try:
            return bool(
                self.client.exists(index=self.settings.data_index_alias, id=doc_id)
            )
        except Exception:
            return False

    def _update_namespace_stats(self, namespace: NamespacePath, *, delta: int) -> None:
        namespace_key = _namespace_key(namespace)
        params = {
            "delta": delta,
            "namespace": list(namespace),
            "namespace_key": namespace_key,
            "depth": len(namespace),
            "updated_at": _serialize_ts(_now()),
        }
        script = (
            "if (ctx._source.doc_count == null) { ctx._source.doc_count = 0; } "
            "ctx._source.doc_count = Math.max(0, ctx._source.doc_count + params.delta); "
            "ctx._source.updated_at = params.updated_at; "
            "ctx._source.namespace = params.namespace; "
            "ctx._source.namespace_key = params.namespace_key; "
            "ctx._source.depth = params.depth;"
        )
        upsert_doc = {
            "namespace": params["namespace"],
            "namespace_key": namespace_key,
            "depth": params["depth"],
            "doc_count": max(delta, 0),
            "updated_at": params["updated_at"],
        }
        body = {
            "scripted_upsert": True,
            "script": {"source": script, "lang": "painless", "params": params},
            "upsert": upsert_doc,
        }
        self.client.update(
            index=self.settings.namespace_index_name,
            id=namespace_key,
            body=body,
        )

    def _top_namespaces(self, limit: int = 5) -> list[dict[str, Any]]:
        body = {
            "size": limit,
            "sort": [{"doc_count": {"order": "desc"}}],
            "query": {"match_all": {}},
        }
        resp = self.client.search(index=self.settings.namespace_index_name, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        top: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source", {})
            top.append(
                {
                    "namespace": tuple(source.get("namespace", [])),
                    "doc_count": source.get("doc_count", 0),
                    "updated_at": source.get("updated_at"),
                }
            )
        return top

    def _log_event(self, event: str, duration: float, **fields: Any) -> None:
        if not self.settings.log_operations:
            return
        logger.info(
            "event=%s duration_ms=%.3f extra=%s",
            event,
            duration * 1000,
            fields,
        )

    def _is_expired(self, source: dict[str, Any]) -> bool:
        expires = source.get("ttl_expires_at")
        if not expires:
            return False
        return _parse_ts(expires) <= _now()

    def _should_refresh_ttl(self, refresh_flag: bool | None, source: dict[str, Any]) -> bool:
        if not source.get("ttl_expires_at"):
            return False
        return bool(refresh_flag or self.settings.ttl_refresh_on_read)

    def _refresh_ttl(self, doc_id: str, source: dict[str, Any]) -> None:
        ttl_minutes = source.get("ttl_minutes") or self.settings.ttl_minutes_default
        if ttl_minutes is None:
            return
        expires_at = _compute_ttl_expires(ttl_minutes)
        if expires_at is None:
            return
        body = {
            "doc": {
                "ttl_expires_at": expires_at,
                "updated_at": _serialize_ts(_now()),
            }
        }
        try:
            self.client.update(
                index=self.settings.data_index_alias,
                id=doc_id,
                body=body,
            )
        except Exception:
            return

    def _resolve_ttl_minutes(self, ttl_value: Any) -> float | None:
        if ttl_value is NOT_PROVIDED:
            ttl_value = None
        if ttl_value is None:
            return self.settings.ttl_minutes_default
        return ttl_value


class TTLManager:
    def __init__(self, store: OpenSearchStore) -> None:
        self.store = store
        self.last_result: dict[str, Any] | None = None
        self.last_run_at: datetime | None = None

    def run_once(self, *, batch_size: int = 1000) -> dict[str, Any]:
        now = _serialize_ts(_now())
        body = {
            "query": {"range": {"ttl_expires_at": {"lte": now}}},
            "max_docs": batch_size,
        }
        start = time.perf_counter()
        result = self.store.client.delete_by_query(
            index=self.store.settings.data_index_alias,
            body=body,
            conflicts="proceed",
            slices="auto",
        )
        duration = time.perf_counter() - start
        self.last_result = result
        self.last_run_at = _now()
        self.store._log_event(
            "ttl_sweep",
            duration,
            deleted=result.get("deleted"),
        )
        return result


class MetricsEmitter:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._logger = logging.getLogger("langgraph.opensearch.store.metrics")

    def record(self, event: str, value: float, attributes: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        payload = {"event": event, "value": value, "attributes": attributes or {}}
        self._logger.info(payload)
