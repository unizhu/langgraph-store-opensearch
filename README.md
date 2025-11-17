# LangGraph OpenSearch Store

LangGraph-compatible `BaseStore` that persists agent long-term memory inside OpenSearch 3.x.
Configuration uses `pydantic-settings`, so environment variables are validated as soon as the
package imports. See [docs/CODE_EXAMPLES.md](docs/CODE_EXAMPLES.md) for the high-level
architecture, [docs/OPS_GUIDE.md](docs/OPS_GUIDE.md) for migrations/operations, and [docs/CONTRACT_TESTS.md](docs/CONTRACT_TESTS.md) for parity
testing instructions.

## TL;DR

```bash
uv pip install langgraph-opensearch-store
```

or just use pip: `pip install langgraph-opensearch-store`

## Development Prerequisites

- [uv](https://github.com/astral-sh/uv) ≥ 0.6
- Python 3.11+ (uv can install/manage it automatically)
- OpenSearch 3.x cluster (local tarball/Homebrew or Amazon OpenSearch Service)

## Quick Start

```bash
# 1) Install uv once per machine (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) Initialize the project & tooling (pyproject already present)
uv venv
source .venv/bin/activate
uv sync --dev

# 3) Install dependencies
# uv sync --all-extras

# 4) Provide connection settings
cp .env.example .env
# → edit .env to match your OpenSearch deployment
#   - set `OPENSEARCH_VERIFY_CERTS=true` for prod clusters
#   - set `OPENSEARCH_IGNORE_SSL_CERTS=true` only for dev/self-signed endpoints

# 5) Run quality gates
uv run ruff check .
uv run pyright
uv run pytest

# 6) Exercise sample code (venv must stay active)
uv run python examples/basic_usage.py

# 7) Build
uv build

```

> **Tip:** Always `source .venv/bin/activate` (or prefix commands with `uv run`) before running Python
> scripts so the managed interpreter and dependencies load correctly.

## Programmatic Configuration (no `.env` required)

If you install the package via pip and prefer not to maintain a `.env` file, instantiate settings
directly:

```python
from langchain_openai import OpenAIEmbeddings
from langgraph_opensearch_store import OpenSearchStore

store = OpenSearchStore.from_params(
    hosts="https://search-example.us-east-1.es.amazonaws.com",
    auth_mode="sigv4",
    aws_region="us-east-1",
    index_prefix="agent_mem",
    ignore_ssl_certs=False,
    embeddings=OpenAIEmbeddings(model="text-embedding-3-small"),
)
store.setup()
```

Pass any field from `Settings` as a keyword argument; defaults mirror the `.env` example.

## Project Folder Structure

- `src/langgraph_opensearch_store/` — `Settings`, OpenSearch client factory, schema helpers,
  `OpenSearchStore`, TemplateManager, and the MemorySaver bridge.
- `examples/` — runnable scripts mirroring `docs/CODE_EXAMPLES.md`.
- `tests/` — unit tests for the settings model + store wiring.
- `docs/` — research notes and reference snippets for agents.

## Namespace Metadata & Stats

`OpenSearchStore.setup()` now installs versioned templates plus two indices:

- **Data index** (`<prefix>-data` alias) storing all documents across namespaces.
- **Namespace index** (`<prefix>-namespace`) tracking doc counts and timestamps for
  `store.list_namespaces()` + `store.get_stats()`.

This means you can introspect namespaces the same way you would with the Postgres store:

```python
store.list_namespaces(prefix=("prefs",))
store.get_stats()  # => {"total_items": ..., "namespace_count": ...}
```

## Search Modes & TTL

- `search_mode`: `auto` (default), `text`, `vector`, or `hybrid`. Auto uses hybrid when embeddings + query
  are available. Configure via `.env` (`OPENSEARCH_SEARCH_MODE`) or `OpenSearchStore.from_params(...)`.
- `search_num_candidates` influences Lucene kNN recall; the store now maps this value to
  `method_parameters.ef_search` so OpenSearch 3.x queries stay valid while still letting you widen the
  candidate pool. `search_similarity_threshold` remains available for score cutoffs.
- Namespace + metadata filters are injected directly into the kNN clause, so Lucene/Faiss can short-circuit
  on filtered subsets without a post-filter penalty.
- TTL support is enabled by default when you pass `ttl` to `store.put(...)` or set
  `OPENSEARCH_TTL_MINUTES_DEFAULT`. Expired docs are filtered automatically during `get/search`, and
  the helper `store.ttl_manager.run_once()` deletes all expired docs via delete-by-query.

```python
store = OpenSearchStore.from_params(
    hosts="http://localhost:9200",
    search_mode="hybrid",
    search_num_candidates=400,   # mapped to ef_search under the hood
    ttl_minutes_default=1440,
)
store.setup()
store.put(("prefs",), "favorite_color", {"text": "blue"}, ttl=60)
store.search(("prefs",), query="blue", limit=3, metadata_filter={"source": "profile"})
store.ttl_manager.run_once()
```

## Operations CLI

The package exposes a CLI (installed as `langgraph-opensearch`) for common maintenance tasks. Commands
accept the same flags/env vars as `OpenSearchStore.from_params` (e.g., `--auth-mode sigv4`).

```bash
# Health & stats
langgraph-opensearch --conn "https://user:pass@localhost:9200" health
langgraph-opensearch --conn $OPENSEARCH_CONN stats
langgraph-opensearch --conn $OPENSEARCH_CONN ttl-sweep --batch-size 500

# Template migrations / alias rollovers
langgraph-opensearch --conn $OPENSEARCH_CONN migrate --rollover
langgraph-opensearch --conn $OPENSEARCH_CONN migrate --no-rollover --new-index my-data-v02

# Snapshot management (fs repo must exist on the cluster)
langgraph-opensearch --conn $OPENSEARCH_CONN snapshots create --repository langgraph --snapshot nightly --no-wait
langgraph-opensearch --conn $OPENSEARCH_CONN snapshots delete --repository langgraph --snapshot nightly
```

> The GitHub Actions `contract-tests` job runs `migrate` and snapshot smoke tests against the
> containerized OpenSearch service so regressions in the CLI are caught automatically.

## Observability & Metrics

- Set `OPENSEARCH_LOG_OPERATIONS=false` to silence operation logs.
- Set `OPENSEARCH_METRICS_ENABLED=true` to emit simple JSON metrics via the
  `langgraph.opensearch.store.metrics` logger (wire it into Prometheus/Otel via your logging pipeline).
- `store.get_health()` returns cluster info, template version, and TTL sweeper state for dashboards.
