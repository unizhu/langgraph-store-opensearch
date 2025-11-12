# LangGraph OpenSearch Memory Store Plugin — Agent Guide

This repository hosts a LangGraph 1.0-compatible memory store plugin that lets agents keep short-term state via LangGraph checkpointers and persist long-term memories inside OpenSearch 3.x clusters (both self-managed and Amazon OpenSearch Service). This file tells coding agents how the project is laid out, which commands to run, and how to work safely with the OpenSearch dependencies.

---

## 1. Repository Layout & Tooling

- `pyproject.toml` — authoritative Python project configuration (build-system, dependencies, Ruff, Pyright, pytest settings).
- `src/langgraph_opensearch_store/`
  - `__init__.py` — exposes the public API.
  - `config.py` — typed settings backed by `pydantic-settings.BaseSettings` (endpoints, auth mode, index names, embedding dimensions) so env vars are validated at import time.
  - `client.py` — OpenSearch client factory (local/basic auth + AWS SigV4 adapters).
  - `schema.py` — TemplateManager plus index templates, mappings, and vector helpers.
  - `store.py` — `OpenSearchStore` implementation that subclasses `langgraph.store.base.BaseStore` (shared data index, namespace metadata index, batch put/get/search, semantic filtering, TTL hooks).
  - `checkpointer.py` — optional short-term store wrapper (`MemorySaver` orchestration + OpenSearch backlinks).
- `examples/`
  - `local_notebook.ipynb` — demonstrates hooking the store into a LangGraph workflow against a workstation OpenSearch 3.0 node.
  - `aws_notebook.ipynb` — demonstrates the AWS SigV4 path.
- `tests/`
  - `conftest.py` — spins up ephemeral test indices, seeds embeddings, tears them down.
  - `test_store_sync.py` / `test_store_async.py` — unit coverage for BaseStore primitives.
  - `test_semantic_search.py` — ensures embeddings + vector queries round-trip.
  - `test_sigv4.py` — mocks AWS credentials to validate signer wiring.
- `docs/`
  - `index.md` — the project documentation folder.
  - `OPS_GUIDE.md` — operational runbook (migrations, TTL sweeps, metrics, CLI).
  - `CONTRACT_TESTS.md` — instructions for running parity tests against Postgres store.
- `infra/cloudformation/`
  - `memory-store.yaml` — provisions an OpenSearch 3.x domain, IAM role, and access policies so SigV4-authenticated agents (including CI/CD) can talk to the store.
- `.github/workflows/ci.yml` — runs `ruff check .`, `pyright`, `pytest`.
- `uv.lock` — optional lockfile committed once dependency versions are pinned.

Python 3.11+ is required. The project assumes globally available `pyright` and `ruff`, but still exposes `uv run pyright` / `uv run ruff` shims for CI reproducibility.

---

## 2. Dev Environment Setup

1. Install [uv](https://github.com/astral-sh/uv) once per machine (Mac/Linux):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. Create a project-local environment and sync deps (this repo is already `uv init`-ed, so just wire the venv):
   ```bash
   uv venv
   source .venv/bin/activate
   uv sync --dev
   ```
3. Copy `.env.example` ➜ `.env` and fill in whichever endpoint you’re targeting (see §4).
4. Export `PYTHONPATH=src` (or rely on editor settings) before running tests. All project commands can be wrapped with `uv run ...` to guarantee they execute inside the managed virtualenv.
5. For a step-by-step walkthrough (including CI/publish expectations) see `README.md`.
6. Use the CLI (`langgraph-opensearch health|stats|ttl-sweep`) during ops debugging; it reads the same env vars as the library.

---

## 3. Memory Architecture Primer

- **Short-term memory (thread scope)** — continue to use LangGraph’s `MemorySaver` (or whichever checkpoint backend the application already picked). Keep thread checkpoints lightweight; do _not_ push per-turn payloads into OpenSearch.
- **Long-term memory (cross-thread)** — the new `OpenSearchStore` satisfies LangGraph’s `store` parameter so any node/tool can `store.put()` / `store.search()` durable documents. The store supports:
  - Hierarchical namespaces (tuples of strings) to mirror LangGraph’s `NamespacePath`.
  - Document-level metadata + optional TTL fields.
  - Semantic search via OpenSearch’s Lucene k-NN engine (no legacy `nmslib`/`HNSW` engine usage) with embedding functions provided via LangChain `Embeddings`.
  - Optional “agentic memory container” compatibility so we can plug into OpenSearch’s native memory APIs when helpful.

When adding features, keep the BaseStore contract in mind: every method should support batch operations (`mget`, `mset`, `mdelete`) and async equivalents (`amget`, …). Use typed `Item` wrappers to pass metadata downstream.

---

## 4. OpenSearch Deployment Targets

### 4.1 Local / Self-Managed OpenSearch 3.0

- Install the official tarball or Homebrew package (`brew install opensearch`) that ships with OpenSearch 3.0.0 and Dashboards 3.0.0.
- Launch via `./opensearch-tar-install.sh` (tarball) or `opensearch-dashboards` (Dashboards) so we can inspect indices locally.
- Default dev credentials: `admin:admin`. Override via `.env`.
- Ensure `plugins.security.disabled: true` is only toggled during laptop development; production-style testing should keep Security enabled.
- Lucene vector search is enabled by default in 3.x; verify `plugins.query.lucene.knn.enabled: true` and avoid deprecated NMSLIB/HNSW engine settings entirely.

### 4.2 Amazon OpenSearch Service (Managed 3.0/3.1)

- Use SigV4 signing via `opensearch-py`’s `AWSV4SignerAuth`.
- Environment variables consumed by `client.py`:
  - `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, optional `AWS_SESSION_TOKEN`.
  - `OPENSEARCH_HOST=https://search-<domain>.<region>.es.amazonaws.com`.
- The AWS path enforces TLS + IAM policies; avoid storing static credentials in the repo. Prefer role-based auth when running in CI.
- When targeting Serverless collections set `service="aoss"` in the signer.
- Apply index templates through the `_component_template` API because managed domains restrict low-level settings.
- Turn on fine-grained access control and scope the plugin’s role to only the memory indices.
- **CloudFormation + IAM support**
  - The `infra/cloudformation/memory-store.yaml` template provisions the OpenSearch domain plus a dedicated IAM role (default `LangGraphMemoryRole`) with least-privilege policies.
  - CloudFormation outputs the role ARN and domain endpoint; feed these into the `.env` (`OPENSEARCH_HOSTS`, `AWS_REGION`, `AWS_ROLE_ARN`) so the store signs requests automatically.
  - Deploy via `aws cloudformation deploy --template-file infra/cloudformation/memory-store.yaml --stack-name langgraph-memory --capabilities CAPABILITY_NAMED_IAM`.
  - After the stack finishes, map the role ARN to the `ml_full_access` (or a custom) OpenSearch backend role through Dashboards → Security → Roles → `ml_full_access` → _Mapped users_. This step authorizes the SigV4 principal created by CloudFormation.
  - Add the same role ARN to the domain access policy (either inline in the template’s `AccessPolicies` block or via a follow-up `aws opensearch update-domain-config`) so Lambda/agents created by CloudFormation can reach the cluster without manual tweaks.
  - CI runners assuming the exported role (via `aws sts assume-role --role-arn ...`) inherit the same permissions, so integration tests may run headless.

---

## 5. Configuration & Environment Variables

All runtime configuration funnels through the `Settings` model in `config.py`. The dataclass was upgraded to a `pydantic-settings.BaseSettings` subclass so every field is validated/coerced when the module loads and `.model_dump()` can feed the OpenSearch client directly. Keep these conventions when adding fields:

- Prefer explicit types (`SecretStr`, `HttpUrl`, `PositiveInt`) so invalid inputs fail fast.
- Use `model_config = SettingsConfigDict(env_prefix="OPENSEARCH_", extra="forbid")` to ensure no unchecked env vars leak in.
- Expose helpers such as `Settings.from_env_file(path)` for tests that need fixture-specific overrides.

The settings model reads from `.env` or process env and includes:

| Variable | Purpose |
| --- | --- |
| `OPENSEARCH_DEPLOYMENT` | `local` or `aws`. Selects auth strategy and default ports. |
| `OPENSEARCH_HOSTS` | Comma-delimited endpoints (with scheme). |
| `OPENSEARCH_USERNAME` / `OPENSEARCH_PASSWORD` | Basic auth for local clusters. |
| `OPENSEARCH_AUTH_MODE` | `basic` or `sigv4`. Set to `sigv4` whenever you rely on IAM/CloudFormation roles. |
| `OPENSEARCH_INDEX_PREFIX` | Prepended to every namespace to avoid collisions across environments. |
| `OPENSEARCH_EMBEDDING_DIM` | Required to build vector fields. Must match the embedding model used by LangGraph. |
| `OPENSEARCH_VECTOR_ENGINE` | Only `lucene` is supported (legacy `nmslib`/`HNSW` engine paths are deprecated in 3.x). Keep this consistent across all indices. |
| `OPENSEARCH_USE_AGENTIC_MEMORY_API` | `true/false`. When true, the store uses the native memory container endpoints for bulk ingestion / summarization. |
| `OPENSEARCH_VERIFY_CERTS` | `true/false`. Defaults to `true`; leave enabled in prod. |
| `OPENSEARCH_IGNORE_SSL_CERTS` | `true/false`. Convenience flag for local dev/self-signed clusters; when `true`, TLS verification is disabled regardless of `OPENSEARCH_VERIFY_CERTS`. |
| `OPENSEARCH_SEARCH_MODE` | `auto` (default), `text`, `vector`, or `hybrid`. Governs the search pipeline. |
| `OPENSEARCH_SEARCH_NUM_CANDIDATES` | Controls Lucene kNN `num_candidates`. |
| `OPENSEARCH_SEARCH_SIMILARITY_THRESHOLD` | Optional cutoff for vector search scores. |
| `OPENSEARCH_TTL_MINUTES_DEFAULT` | Default TTL applied when `store.put` omits `ttl`. |
| `OPENSEARCH_TTL_REFRESH_ON_READ` | `true/false`. Refresh TTL automatically during `get/search`. |
| `PYDANTIC_CONFIG_OVERRIDE` | Optional dotted path to a `BaseSettings` mixin if you need per-environment validation knobs without forking `config.py`. |
| `AWS_ROLE_ARN` | Optional — when set, the dev tooling runs `aws sts assume-role` before talking to OpenSearch. Useful for CI or CloudFormation-provisioned roles. |
| `AWS_WEB_IDENTITY_TOKEN_FILE` | Optional — enables IRSA-style auth (pods authenticate via projected tokens) without storing static credentials. |
| `LANGCHAIN_TRACING_V2`, `LANGSMITH_API_KEY` | Optional telemetry for debugging agent flows. |

Agents consuming the PyPI package without a `.env` can pass these fields directly via
`OpenSearchStore.from_params(...)` or by instantiating `Settings(**kwargs)` before calling
`OpenSearchStore.from_settings`.

Never hardcode secrets in code or tests; rely on fixtures + env files.

---

## 6. Implementation Guidelines

1. **BaseStore Surface**
   - Sync + async variants must be feature-parity.
   - `mset` should upsert documents using `_bulk` for throughput.
   - `search` must support text, vector, and hybrid modes. Use reciprocal-rank fusion to blend BM25 + Lucene kNN so the behavior mirrors Postgres' hybrid search helper.
   - Respect namespaces by storing them as tuple metadata inside the shared data index; composite IDs should combine namespace + key to avoid collisions.
2. **Schema Management**
   - Store JSON payloads under `doc` field, metadata under `metadata`, vector embeddings under `embedding`.
   - Use `TemplateManager` to install versioned templates, aliases, and namespace indices before ingesting data.
   - Keep the namespace metadata index (`<prefix>-namespace`) in sync via scripted upserts when documents change.
   - When `OPENSEARCH_USE_AGENTIC_MEMORY_API=true`, mirror the schema OpenSearch expects for memory containers (messages, embeddings, context).
3. **TTL**
   - Set `supports_ttl = True`, store `ttl_minutes` + `ttl_expires_at`, and honor `refresh_ttl` in `get/search`.
   - Provide a `TTLManager` helper around delete-by-query sweeps so operators can remove expired docs just like the Postgres TTL sweeper.
4. **Ops & Observability**
   - Instrument operations with the `langgraph.opensearch.store` logger; respect `OPENSEARCH_LOG_OPERATIONS`.
   - When `OPENSEARCH_METRICS_ENABLED=true`, emit JSON metrics via `langgraph.opensearch.store.metrics`.
   - Keep `get_health()` and CLI commands (`langgraph-opensearch health|stats|ttl-sweep`) working before shipping any release.
3. **Short-term/Long-term Bridge**
   - `checkpointer.py` should show how to keep quick-turn state in `MemorySaver` while logging durable facts into `OpenSearchStore`.
   - Provide helper functions to promote items from short-term to long-term memory when the agent decides a fact is worth keeping.
4. **Pydantic-powered Validation Hooks**
   - Use `field_validator`/`model_validator` to normalize host lists, merge default ports, and enforce allowed `OPENSEARCH_VECTOR_ENGINE` values.
   - Derive lightweight DTOs (e.g., `IndexTemplateConfig(BaseModel)`) so schema helpers and tests can share the same validation logic.
   - Keep serialization in one place: prefer `.model_dump(mode="json")` when passing settings into OpenSearch API stubs.
5. **AWS Support**
   - Reuse an `AwsSigV4SignerAuth` instance across requests; don’t sign each call from scratch.
   - Support both domain endpoints (`*.es.amazonaws.com`) and collections (`*.aoss.amazonaws.com`).
   - Allow custom CA bundles via `REQUESTS_CA_BUNDLE` for private VPC endpoints.
6. **Observability**
   - Wrap OpenSearch calls with structured logging (latency, namespace, op type) so LangSmith traces can correlate memory latency with agent steps.
   - Surface circuit-breaker stats in `/metrics` once we add a FastAPI shim (future work).

---

## 7. Testing & QA

All changes must keep the following green from the repo root (wrap them with `uv run` if the virtualenv is not activated):

```bash
uv run pytest
uv run pyright
uv run ruff check .
```

Recommended extra checks:

- `pytest -m integration` — runs tests that require a live OpenSearch node (skips by default if `OPENSEARCH_HOSTS` is unset).
- `pytest tests/test_sigv4.py -k live` — hits a dev AWS domain; requires IAM credentials with scoped permissions.
- `ruff format .` — adhere to the formatter settings in `pyproject`.

When adding features, include fixtures or factories so tests don’t rely on pre-existing indices.

---

## 8. Deployment & Release

- Version the package with SemVer (`0.x` until the store API stabilizes).
- Ensure `pyproject.toml` declares `requires-python = ">=3.11"`, lists `pydantic>=2.7`, `pydantic-settings`, and marks `uv`'s `backend = "uv"` under `[build-system]` so `uv build` can emit reproducible wheels.
- Run `uv lock --upgrade` after dependency bumps so downstream agents inherit the pinned resolver graph.
- Use `uv build` (or `uv run python -m build`) to produce wheels/sdist, then `UV_PUBLISH_TOKEN=<pypi-token> uv publish --index https://upload.pypi.org/legacy/` for first-party PyPI pushes. Mirror builds to the internal feed when required.
- Attach release artifacts plus the `uv.lock` digest to the GitHub Release so CI runners can verify supply-chain integrity.
- Each release must document:
  - Minimal LangGraph version (target: ≥1.0.0).
  - Minimal OpenSearch compatibility matrix (currently 3.0–3.3 and AWS 3.0+/Serverless collections).
  - Any schema migrations (e.g., vector dimension changes).
  - Whether new settings or Pydantic validators are breaking (include upgrade snippet).

---

## 9. Security Notes

- Lock down `.env` files in `.gitignore`.
- Treat AWS credentials as ephemeral; rely on `aws sts assume-role` during local testing.
- For customer deployments, encourage VPC-only OpenSearch domains plus SigV4 auth rather than basic auth.
- When running local clusters with security disabled, bind the processes to loopback interfaces only and never expose them over shared networks.

---

## 10. Contribution Workflow

1. Create a feature branch: `git checkout -b feat/<short-description>`.
2. Update docs/examples when touching public APIs.
3. Run the required test trio (pytest, pyright, ruff) and mention them in the PR description.
4. Submit PRs targeting `main`. Title format: `[store] <short description>`.
5. Request review from the LangGraph Storage maintainers group.
6. When preparing a release or large change, trigger the optional `contract-tests` workflow (via workflow_dispatch) to compare Postgres vs OpenSearch behavior (see `docs/CONTRACT_TESTS.md`).

---

Use this AGENTS.md whenever you need a quick refresher on how to interact with the codebase or automate changes. For deeper architectural background, see the LangGraph long-term memory docs and the OpenSearch 3.x agentic memory guides referenced in the project README.
