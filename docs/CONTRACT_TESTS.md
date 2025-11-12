# Contract Test Harness

The `tests/contract` suite compares `OpenSearchStore` versus LangGraph's `PostgresStore`. It requires
external services and is skipped unless the following environment variables are set:

- `POSTGRES_DSN` — e.g. `postgresql://postgres:postgres@localhost:5432/langgraph`
- `OPENSEARCH_CONN` — e.g. `http://admin:admin@localhost:9200/?search_mode=hybrid&ttl_minutes=60`

## Running Locally

```bash
export POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/langgraph
export OPENSEARCH_CONN=http://admin:admin@localhost:9200
uv run pytest tests/contract -m contract
```

## CI Integration

The GitHub Actions workflow defines a `contract-tests` job that spins up Postgres + OpenSearch
containers and executes the suite. It only runs on `workflow_dispatch` events to keep PRs fast.
Trigger it manually from the Actions tab when you need full parity verification.

## Dataset

The harness loads a deterministic dataset (`DATASET` constant in `tests/contract/test_parity.py`) and
compares the outputs of `put/get/search/list_namespaces/get_stats` across both stores. Extend the
fixture when new surface area needs coverage.
