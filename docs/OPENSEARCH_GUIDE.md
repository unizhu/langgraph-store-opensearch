# LangGraph OpenSearch Store — Ops Guide

## Template & Index Lifecycle
- Run `uv run python -m langgraph_opensearch_store.cli health` after provisioning to verify the data alias (`<prefix>-data`) and namespace index exist.
- `TemplateManager` version bumps require a rolling `setup()` + reindex:
  1. Bump `Settings.template_version` and release.
  2. Run `store.setup()` (or `langgraph-opensearch health`) to create the new `data-vXX` index.
  3. Reindex old data into the new alias, then delete obsolete indices.

## ILM & TTL
- Data index stores documents plus `ttl_expires_at`. Set `OPENSEARCH_TTL_MINUTES_DEFAULT` for default expiration.
- Enable refresh-on-read via `OPENSEARCH_TTL_REFRESH_ON_READ=true` if you need session-style behavior.
- Use `langgraph-opensearch ttl-sweep --batch-size 1000` to delete expired docs on demand or wire it into cron.

## CLI Cheatsheet

`$OPENSEARCH_CONN` looks like `https://user:pass@host:9200/?search_mode=hybrid&ttl_minutes=1440`.

```
langgraph-opensearch --conn $OPENSEARCH_CONN health
langgraph-opensearch --conn $OPENSEARCH_CONN stats
langgraph-opensearch --conn $OPENSEARCH_CONN ttl-sweep --batch-size 500
```

## Metrics & Logging
- `OPENSEARCH_LOG_OPERATIONS=false` silences request-level logs.
- `OPENSEARCH_METRICS_ENABLED=true` emits JSON metrics under `langgraph.opensearch.store.metrics` (hook your log forwarder into Prometheus/Otel).
- TTL sweeps log `event=ttl_sweep` with deleted counts + duration.

## AWS SigV4 Notes
- Set `OPENSEARCH_AUTH_MODE=sigv4`, `AWS_REGION`, and optionally `AWS_ROLE_ARN`.
- For web identity (IRSA), set `AWS_WEB_IDENTITY_TOKEN_FILE` — the client will call `AssumeRoleWithWebIdentity` before signing requests.
- Retries/backoff are enabled for 429/5xx by default; override via `OPENSEARCH_MAX_RETRIES` in future if needed.

## Troubleshooting
| Symptom | Check | Fix |
| --- | --- | --- |
| `security_exception` on startup | IAM role lacks `es:ESHttp*` | Update domain access policy + role mapping |
| `ttl_sweep` deletes zero docs | Ensure docs have `ttl_expires_at` and `OPENSEARCH_TTL_MINUTES_DEFAULT` is set | Re-ingest or set default TTL |
| High query latency | Review `search_mode` (`hybrid` costs more) and tune `search_num_candidates` | Lower `num_candidates` or use text-only |
| SigV4 auth fails | Confirm `AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE` | Recreate credentials / update env |

## Contract Tests
- Run `uv run pytest tests/contract -m contract` locally (requires Postgres + OpenSearch endpoints).
- Trigger the `contract-tests` GitHub Action (manual dispatch) to spin up dockerized services and execute the suite in CI.
